# Phase 1 — Requirements

Migrated 2026-08-03 from the repo-root `REQUIREMENTS.md` (removed in the same change; original at commit 7da4a66). Phase 2+ items moved to `BACKLOG.md`.

## Overview

A headless Python application that runs on a Raspberry Pi, starting automatically on boot, to continuously monitor home broadband and network performance (latency, jitter, DNS, and throughput), log periodic self-reboots, and expose results via a local web dashboard. Designed to be extensible for future periodic tests (e.g. GPIO-based sensors).

## Platform assumptions (provisional)

### Target (deployment)

- Raspberry Pi OS Lite **64-bit**, based on **Debian 13 (trixie)**, with **Python 3.13.5** — read off the machine at gate G1 on 2026-08-12. This corrects an earlier assumption of Bookworm and "Python 3.11+", which was never checked against the hardware. `requires-python = ">=3.11"` is satisfied either way, so nothing depended on the wrong value, but the PEP 668 externally-managed-environment behaviour that `bootstrap.sh`'s virtualenv works around belongs to this line of Debian too
- The target is a **Raspberry Pi 3 Model B** (ARMv8 Cortex-A53, 1 GB RAM — 905 MB usable, measured at G1), confirmed 2026-08-11 — low-end hardware by design, not a Pi 4/5. The low-spec constraints below are written against this machine; `uname -m` reports `aarch64`, confirmed at G1, which is what selects the Ookla Speedtest CLI build
- Network connectivity via either wired LAN or WiFi (not assumed to be wired-only)
- Single Pi, LAN-only access (no internet-facing exposure), fully headless — no display/monitor attached
- systemd available for service management

### Development (Chromebook / Crostini)

Phase 1 is developed on a Chromebook in a Crostini Linux container. A Raspberry Pi is available over SSH, but only when at home — hardware access is intermittent, not continuous. This sharpens requirement 10's "dev/test away from the Pi" from a nice-to-have into the default working mode: everything is built and unit-tested in the container so no milestone blocks on hardware, and Pi-only verification is batched into gates cleared on a home visit (G1–G4 in `plan.md`).

What the container cannot verify:

- **systemd** — the Crostini container runs systemd, but unit files, `After=`/`Requires=` ordering, and `Restart=on-failure` behaviour are only meaningfully verified on the Pi. Every service therefore needs a plain module entrypoint that runs standalone, with the systemd unit as a thin wrapper around it.
- **ICMP** — unprivileged ping depends on `net.ipv4.ping_group_range` inside the container; raw sockets need `CAP_NET_RAW`. Resolved by shelling out to the system `ping` binary with an argv list: it is already setuid/setcap, so it works unprivileged on both platforms and the service needs no `CAP_NET_RAW` grant. Confirmed on the Pi at gate G1.
- **Reboot** — never actually invoked in dev, so the abstraction requirement 10 calls for has a no-op implementation as the dev default. The real implementation (a narrow sudoers rule) is exercised on hardware at gate G3.
- **Speed test figures are not comparable** — Crostini sits behind ChromeOS's virtual network, so dev throughput numbers validate the code path, not the measurement. Only Pi runs produce meaningful data.
- **LAN reachability** — Crostini does not expose ports to the LAN by default, so "usable from a phone on the LAN" (requirement 7) is verified on the Pi at gate G1; dev testing is localhost-only. A related unknown is open: the container cannot currently resolve or reach the Pi at all, though ChromeOS itself can — diagnosis deferred to M2.
- **CPU architecture** — the Chromebook is x86_64 (or arm64 on some models); the Pi is ARM. Prefer pure-Python dependencies; anything shipping native wheels must be confirmed installable on the Pi before it is locked in.
- **Low-spec behaviour is unverifiable in dev** — a Chromebook will not surface the CPU/memory pressure of a Pi Zero 2 W. Requirement 10's low-spec constraint is a design discipline during development and a measurement only once on the Pi.

*Note: although only these requirements need to be built now, the design (architecture, data model, and collector/config interfaces) should be laid out so that the Phase 2+ items in `BACKLOG.md` can be added later without reworking Phase 1 code — e.g. new collectors slot into the existing interface, new ping targets (like a router) need no schema change, and new chart types reuse the existing aggregation/caching layer.*

## 1. Architecture

Split into independent systemd services, communicating only via the shared SQLite database (no direct IPC):

1. **`bbmon-pinger`** — runs the ping loop
2. **`bbmon-speedtest`** — runs the speed test loop
3. **`bbmon-web`** — Flask app serving dashboard + config/reboot pages
4. **`bbmon-reboot`** — a root one-shot unit that reboots the machine, started by a `bbmon-reboot.path` watcher when an unprivileged service asks. Nothing of bbmon's own runs in it; the schedule and the restart record live in the services above

A shared Python package (e.g. `bbmon/`) holds common code: config loader, DB access layer, models. Each service is a thin entrypoint importing this shared package. Collectors (ping, speedtest, and future test types) conform to a common interface — see Extensibility (item 9) — so new periodic tests can be added as a new module + systemd service without changing existing services.

## 2. Configuration

- YAML file, e.g. `/etc/bbmon/config.yaml`
- Editable via the web config page (writes back to the file; services re-read on next cycle or on SIGHUP)
- Fields include:
  - `ping.interval_seconds` (default 5)
  - `ping.targets` (default: `8.8.8.8`, `1.1.1.1`, `google.com`)
  - `speedtest.interval_hours` (default 3)
  - `reboot.interval_days` (default 3)
  - `retention.ping_days` (default 30)
  - `web.host` (default `127.0.0.1`) — the address the web app binds to, stated
    explicitly rather than left to a framework default; the Pi's deployed config
    sets `0.0.0.0` so the dashboard is reachable from the LAN
  - `web.port` (default 8080)
  - `web.restart_limit` (default 20) — how many restarts the dashboard's
    restart list shows, which item 7 asks to be configurable
  - `database.path` (default `/var/lib/bbmon/bbmon.db`)
- The file's own location comes from the `BBMON_CONFIG` environment variable,
  falling back to `/etc/bbmon/config.yaml`. That single override is what lets a
  development machine run every service without writing to `/etc` or `/var/lib`
- Config page validates values on save (positive intervals, valid hostnames/IPs for ping targets) and rejects bad input with a clear error rather than letting a bad config crash a service on reload

## 3. Data Storage

- SQLite database file, e.g. `/var/lib/bbmon/bbmon.db`, with WAL journal mode and a busy timeout set in the DB layer (multiple services write/read this file concurrently)
- Tables (indicative):
  - `ping_results (id, timestamp, target, latency_ms, success)`
  - `speedtest_results (id, timestamp, download_mbps, upload_mbps, ping_ms, isp, server, success)`
  - `restarts (id, timestamp, expected BOOLEAN, reason TEXT)`
- Retention: `speedtest_results` kept indefinitely; `ping_results` purged after `retention.ping_days` (daily purge job)
- A one-shot "init" step creates the DB schema before any other service starts, with proper systemd `After=`/`Requires=` dependencies
- The schema carries a version marker from the outset, so a migration mechanism can be added later without reworking Phase 1 — the mechanism itself is backlog, not Phase 1 (see `BACKLOG.md`)

## 4. Ping Monitor

- Every `ping.interval_seconds`, pings each configured target, records latency (or failure) per target
- Runs as its own service so it's unaffected by speedtest load
- Buffers inserts and flushes every 30–60s rather than writing on every single ping, to reduce SD card wear

## 5. Speed Test

- Runs on startup, then every `speedtest.interval_hours`
- Tool: the **official Ookla Speedtest CLI** — a single static binary invoked as a subprocess with an argv list and `--format=json`, not a Python library, so it adds no dependency to the `bbmon` package itself. Installed by `bootstrap.sh` from Ookla's architecture-specific tarball; its licence and GDPR prompts are accepted non-interactively so nothing blocks on a prompt
- Records download/upload/ping plus server/ISP metadata
- If the test fails (e.g. network down), records a failed/error row rather than silently skipping, so dashboard gaps are unambiguous
- Skips/delays if a reboot is imminently due, to avoid the two overlapping

## 6. Reboot Management

- Reboots the Pi every `reboot.interval_days` (default 3), as a scheduled check inside the ping service's existing loop
- Before rebooting, records that the reboot was deliberate, so the restart can be told apart from a power cut afterwards
- On startup, the app records the restart: `expected = true` if the reboot was deliberate, `expected = false` otherwise (covering power loss, crashes, manual reboots). One row per boot, timestamped when the restart was noticed — the only time a restart record can honestly carry, since an unexpected restart is not observed while it happens
- Waits for NTP time sync before performing its first write on boot, so timestamps are reliable even if the Pi has no RTC
- Rebooting is a Pi-only capability behind an abstraction (requirement 10), with a no-op implementation as the development default

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

- **Dev/test away from the Pi**: the app must run and be testable on a development machine without Pi-specific hardware. Pi-only dependencies (GPIO, actual reboot calls) must be isolated behind interfaces/abstractions with no-op or mock implementations for non-Pi environments, so the pinger, speedtest, DB layer, and web app can all be developed and tested locally. For this effort the development machine is a Chromebook running Crostini — see Platform assumptions above for what that specifically rules out.
- **Basic test suite**: pytest-based unit tests for the DB layer and collectors, runnable without any Pi hardware
- **Low-spec hardware**: must run comfortably on entry-level Pi hardware (e.g. Pi Zero 2 W / Pi 3-class or lower) — lightweight dependencies, avoid heavy frameworks, modest memory/CPU footprint across all services
- **Web performance**: dashboard should stay fast under repeated polling — minimise redundant database calls via server-side caching of query results (e.g. short-lived cache on aggregated chart data), rather than re-querying/re-aggregating on every poll
- **Service resilience**: systemd units specify `Restart=on-failure` so a crashed pinger/speedtest service auto-recovers
- **App log rotation**: services' own log files need rotation (e.g. via `logrotate` or Python's rotating file handler) or they will grow unbounded
- **Simple first-time setup and updates**: deliverables include
  - A first-time setup script: installs dependencies, creates the database, installs and enables the systemd services, deploys the default config
  - A refresh/update script: pulls latest code and restarts affected services with minimal manual steps
  - Both scripts and accompanying instructions should be usable by someone without deep Linux/systemd knowledge

## Open decisions

None. All four decisions that previously blocked milestones were closed on 2026-08-05 — ping target defaults, web port and route structure, purge job placement, and the reboot trigger mechanism. They are recorded with their reasoning in `plan.md` under "Decisions taken with this plan".
