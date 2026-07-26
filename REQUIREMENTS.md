# Raspberry Pi Broadband Monitor — Requirements

## Overview
A headless Python application that runs on a Raspberry Pi, starting automatically on boot, to continuously monitor home broadband and network performance (latency, jitter, DNS, and throughput), log periodic self-reboots, and expose results via a local web dashboard. Designed to be extensible for future periodic tests (e.g. GPIO-based sensors).

## Platform Assumptions (provisional)
- Raspberry Pi OS Lite (Bookworm), Python 3.11+
- Old or low-end Pi hardware (e.g. Pi 3-class or a Pi Zero/Zero 2 W) — not a Pi 4/5
- Network connectivity via either wired LAN or WiFi (not assumed to be wired-only)
- Single Pi, LAN-only access (no internet-facing exposure), fully headless — no display/monitor attached
- systemd available for service management

---

# Phase 1 — Minimum Requirements

*Note: although only these requirements need to be built now, the design (architecture, data model, and collector/config interfaces) should be laid out so that the Phase 2+ items below can be added later without reworking Phase 1 code — e.g. new collectors slot into the existing interface, new ping targets (like a router) need no schema change, and new chart types reuse the existing aggregation/caching layer.*

## 1. Architecture
Split into independent systemd services, communicating only via the shared SQLite database (no direct IPC):

1. **`bbmon-pinger`** — runs the ping loop
2. **`bbmon-speedtest`** — runs the speed test loop
3. **`bbmon-web`** — Flask app serving dashboard + config/reboot pages
4. **`bbmon-reboot`** — timer/service that triggers the periodic reboot and logs it

A shared Python package (e.g. `bbmon/`) holds common code: config loader, DB access layer, models. Each service is a thin entrypoint importing this shared package. Collectors (ping, speedtest, and future test types) conform to a common interface — see Extensibility (Phase 1, item 9) — so new periodic tests can be added as a new module + systemd service without changing existing services.

## 2. Configuration
- YAML file, e.g. `/etc/bbmon/config.yaml`
- Editable via the web config page (writes back to the file; services re-read on next cycle or on SIGHUP)
- Fields include:
  - `ping.interval_seconds` (default 5)
  - `ping.targets` (default: `8.8.8.8`, `1.1.1.1`, `google.com`)
  - `speedtest.interval_hours` (default 3)
  - `reboot.interval_days` (default 3)
  - `retention.ping_days` (default 30)
  - `web.port` (default 8080)
- Config page validates values on save (positive intervals, valid hostnames/IPs for ping targets) and rejects bad input with a clear error rather than letting a bad config crash a service on reload

## 3. Data Storage
- SQLite database file, e.g. `/var/lib/bbmon/bbmon.db`, with WAL journal mode and a busy timeout set in the DB layer (multiple services write/read this file concurrently)
- Tables (indicative):
  - `ping_results (id, timestamp, target, latency_ms, success)`
  - `speedtest_results (id, timestamp, download_mbps, upload_mbps, ping_ms, isp, server, success)`
  - `restarts (id, timestamp, expected BOOLEAN, reason TEXT)`
- Retention: `speedtest_results` kept indefinitely; `ping_results` purged after `retention.ping_days` (daily purge job)
- A one-shot "init" step creates the DB schema before any other service starts, with proper systemd `After=`/`Requires=` dependencies

## 4. Ping Monitor
- Every `ping.interval_seconds`, pings each configured target, records latency (or failure) per target
- Runs as its own service so it's unaffected by speedtest load
- Buffers inserts and flushes every 30–60s rather than writing on every single ping, to reduce SD card wear

## 5. Speed Test
- Runs on startup, then every `speedtest.interval_hours`
- Tool: **speedtest-cli** (open-source Python library) — no manual confirmation prompts, scriptable
- Records download/upload/ping plus server/ISP metadata
- If the test fails (e.g. network down), records a failed/error row rather than silently skipping, so dashboard gaps are unambiguous
- Skips/delays if a reboot is imminently due, to avoid the two overlapping

## 6. Reboot Management
- Reboots the Pi every `reboot.interval_days` (default 3), via a systemd timer or scheduled check
- Before rebooting, writes a `restarts` row with `expected = true`
- On every service startup, the app checks whether the prior shutdown was logged as expected; if not, logs `expected = false` (covers power loss, crashes, manual reboots)
- Waits for NTP time sync before performing its first write on boot, so timestamps are reliable even if the Pi has no RTC

## 7. Web Dashboard (main page)
- Flask app, built-in dev server (LAN-only, acceptable per requirements)
- Auto-updates without full page reload (polling/AJAX)
- Mobile-friendly layout (usable from a phone on the LAN)
- Version/build indicator in the footer, to confirm the update script deployed the latest code
- Most recent speed test result (download and upload) shown prominently near the top of the page, not just buried in the history chart
- Charts:
  - **Ping latency — short term**: simple line chart, last 2 hours, per target
  - **Ping latency — long term**: box plot, last 1 day of data, boxed hourly
  - **Speed test history**: download/upload/ping over time, with a selectable time range (e.g. last 24h / 7d / 30d), since speed test data is retained indefinitely and could grow large
  - Chart queries use pre-aggregated data (e.g. hourly averages) rather than raw rows, to stay responsive on low-spec hardware
- Reboot list: last 20 (configurable) restarts, with a toggle to exclude expected (self-triggered) restarts
- No authentication (trusted LAN)

## 8. Config / Admin Page
- Separate page from the dashboard
- Edit all config.yaml values via form
- "Force reboot" button (triggers immediate reboot, logs as expected)
- CSV download of ping and speed test data (date-range selectable)
- No authentication (trusted LAN)

## 9. Extensibility
- Common "collector" interface (e.g. `collect() -> dict`, `interval`, `table/schema`) so future periodic tests can be added as a new module + systemd service without modifying existing services

## 10. Non-Functional Requirements
- **Dev/test away from the Pi**: the app must run and be testable on a development machine (e.g. laptop) without Pi-specific hardware. Pi-only dependencies (GPIO, actual reboot calls) must be isolated behind interfaces/abstractions with no-op or mock implementations for non-Pi environments, so the pinger, speedtest, DB layer, and web app can all be developed and tested locally
- **Basic test suite**: pytest-based unit tests for the DB layer and collectors, runnable without any Pi hardware
- **Low-spec hardware**: must run comfortably on entry-level Pi hardware (e.g. Pi Zero 2 W / Pi 3-class or lower) — lightweight dependencies, avoid heavy frameworks, modest memory/CPU footprint across all services
- **Web performance**: dashboard should stay fast under repeated polling — minimise redundant database calls via server-side caching of query results (e.g. short-lived cache on aggregated chart data), rather than re-querying/re-aggregating on every poll
- **Service resilience**: systemd units specify `Restart=on-failure` so a crashed pinger/speedtest service auto-recovers
- **App log rotation**: services' own log files need rotation (e.g. via `logrotate` or Python's rotating file handler) or they will grow unbounded
- **Simple first-time setup and updates**: deliverables include
  - A first-time setup script: installs dependencies, creates the database, installs and enables the systemd services, deploys the default config
  - A refresh/update script: pulls latest code and restarts affected services with minimal manual steps
  - Both scripts and accompanying instructions should be usable by someone without deep Linux/systemd knowledge

---

# Phase 2+ — Future Requirements
Ordered by suggested priority (highest value / most natural extension first):

1. **Gateway/router ping alongside WAN targets**: ping the router's LAN IP as one of the regular ping targets, to distinguish "LAN/WiFi problem" from "ISP problem." Directly extends the existing ping architecture — just another target.
2. **Jitter**: derive latency variance between consecutive pings from data already being collected. Standard VoIP/gaming quality metric, effectively free to add.
3. **DNS resolution time**: measure and record hostname resolution time separately from the ping itself, per target.
4. **HTTP(S) response time**: full request time (not just ICMP) to a few configured real sites, via `requests` — closer to "does the internet actually feel fast."
5. **WiFi signal strength/link quality**: if the Pi itself is on WiFi, log signal strength over time via `iwconfig`/`iw` output (subprocess call, no extra hardware). Lower priority — only relevant if the Pi isn't wired.
6. **GPIO-based sensor collectors**: the original motivating "future test type" example — plugs into the Phase 1 collector interface once specific sensors are chosen.
7. **Concurrent-speedtest chart marker** (minor UI polish): mark on the ping latency chart when a speed test was running concurrently, since it causes a visible (expected) latency bump that would otherwise look unexplained.

---

# Items to Discuss Before Proceeding
- **Default ping target list**: is `8.8.8.8` / `1.1.1.1` / `google.com` the right default set?
- **Web port and route structure**: default port 8080; is the config/admin page a separate route on the same Flask app, or a distinct app?
- **Purge job placement**: does the daily ping-retention purge run inside the pinger service, or as its own systemd timer?

- **Reboot trigger mechanism**: how does the unprivileged web app actually invoke `reboot`? Options include a narrowly-scoped passwordless sudo rule, or a small dedicated systemd unit the web app starts. Needs a decision before deploy.
- **DB schema migration strategy**: if future (Phase 2+) collectors add tables/columns, how does the refresh/update script handle schema changes? Even a simple "migrations run automatically on service start" approach needs to be defined.
