# Phase 1 — Plan

Replaces the milestone outline drafted 2026-08-03 (recoverable at commit `344cc08`), which assumed no Raspberry Pi until the final milestone. A Pi is available from the outset, so the plan now runs the hardware in parallel with development rather than at the end.

**Goal: a working prototype as fast as possible.** The shape is a vertical slice — the first milestone puts a live ping chart in a browser, and every later milestone thickens a system that already runs, rather than adding a component that isn't wired to anything yet.

## Scope discipline

The requirements in `requirements.md` are the whole of phase 1. Nothing else is built now.

- **Anything discovered along the way goes to `BACKLOG.md`, not into this plan.** No exceptions for "it's only small" — that is exactly how the plan grows.
- **The one admitted exception is security**, and only where something would open a hole in the home network. Those items are listed explicitly under "Security posture" below and are already costed into the milestones; a *new* security idea still goes to the backlog unless it closes a real hole.
- **Explicit non-goals for phase 1**: authentication on any page, HTTPS, alerting/notifications, multi-Pi support, schema migrations, historical data import, packaging/distribution, and every item already sitting in `BACKLOG.md`.

## Working with the Pi in the loop

Development happens on the Chromebook (Crostini); the Pi is a deployment and verification target reachable over SSH when at home. Development is often away from it, so **no milestone blocks on Pi access**.

- Every milestone is built and unit-tested entirely on Crostini and reaches **done (dev)** there — a green pytest suite, no hardware involved.
- Pi-only concerns do not interleave into that work. They are collected into the numbered **gates** below, each a short checklist that reaches **done (verified)**.
- Gates are cleared in batches when at the Pi. Work carries on past an uncleared gate; a gate only blocks the specific claim that its milestone is verified on hardware.
- Deploying is one command (`scripts/deploy.sh`), so clearing a gate is minutes of hardware time, not a re-setup.

## Milestones

**M1 — Walking skeleton.** *(dev)* A thin end-to-end path: package layout and pytest; config loader (YAML via `safe_load`, defaults, validation of intervals and ping targets); SQLite layer with WAL, busy timeout and the three tables behind a versioned one-shot init; the ping collector with its buffered-flush write path; a Flask app serving one live-polling latency chart. Deliberately thin — one chart, raw rows, no aggregation or caching, no admin page, no speedtest. Each service runs standalone as `python -m bbmon.<service>`. *Depends on: nothing.* **This milestone is the prototype.**

**M2 — Pi bootstrap and the deploy loop.** *(dev + gate G1)* `scripts/bootstrap.sh` (first-time: dedicated non-root `bbmon` user, directories and permissions, dependencies, config, systemd units enabled), `scripts/deploy.sh` (rsync the working tree, restart affected services — no commit required), and `scripts/update.sh` (git pull + restart, the requirement-10 path for proper deploys). Systemd units with `Restart=on-failure` and the sandboxing directives listed below. SSH key setup, password auth disabled, and `docs/pi-access.md` covering how to reach the Pi from other hosts. *Depends on: M1.*

**M3 — Speed test collector.** *(dev + gate G2)* Second collector against the M1 interface — which is the real test of whether that interface generalises; expect to adjust it here rather than guessing it right at M1. Failure rows recorded, never silently skipped. Latest-result panel added to the dashboard. *Depends on: M1.*

**M4 — Restarts and the reboot mechanism.** *(dev + gate G3)* `restarts` rows, expected/unexpected detection on startup, NTP-sync wait before the first write. The reboot action sits behind an abstraction with a no-op implementation for dev; the real implementation is the narrow sudoers rule described below. *Depends on: M1.*

**M5 — Full dashboard.** *(dev)* The remaining two charts (hourly box plot over 1 day; speedtest history with selectable range), the pre-aggregation and short-lived server-side cache both charts read through, the restart list with its expected-restart toggle, the version/build footer, and the mobile layout. *Depends on: M3, M4.*

**M6 — Admin page.** *(dev)* Config form with server-side validation and atomic write-back, SIGHUP reload, force-reboot button, CSV export of ping and speedtest data over a selectable date range, and the daily ping-retention purge. *Depends on: M5.*

**M7 — Operability and release.** *(dev + gate G4)* Log rotation, the security checklist verified end to end, low-spec behaviour measured on the Pi for the first time, and `update.sh` proven from a clean pull. *Depends on: everything above.*

## Pi gates

**G1 — first deploy** *(after M2)*. `bootstrap.sh` completes on a clean Pi; services come up under systemd as `bbmon`; dashboard reachable from a phone on the LAN; `deploy.sh` round-trip works; unprivileged ICMP confirmed on the Pi; key-only SSH confirmed working.

**G2 — real measurements** *(after M3)*. Speed test produces meaningful numbers (Crostini figures validate the code path only); ping latency during a concurrent speed test looks sane; SD-card write volume from the buffered flush measured and acceptable.

**G3 — reboot** *(after M4)*. Sudoers rule grants exactly the one command and nothing more; force-reboot actually reboots and logs `expected = true`; a pulled power cable is detected as `expected = false` on next boot; NTP wait behaves on a Pi with no RTC.

**G4 — soak** *(after M7)*. CPU and memory measured under normal load; log rotation observed; retention purge observed over several days; `update.sh` from a clean git pull; mobile layout on a real phone; full security checklist.

## Decisions taken with this plan

The four items under "Open decisions" in `requirements.md`, plus the ones this plan's shape forces. All are closed here unless marked otherwise.

- **Reboot trigger** — the web app runs one fixed command via a sudoers rule scoped to exactly `systemctl start bbmon-reboot.service`. No shell, no wildcards, no user-supplied arguments.
- **Web port and route structure** — one Flask app on port 8080; dashboard at `/`, admin at `/admin`. A second app would double the surface for no benefit.
- **Purge job placement** — inside the pinger service's existing loop as a daily check, not a separate systemd timer. One fewer unit to install, secure and reason about.
- **Default ping targets** — ship `8.8.8.8`, `1.1.1.1`, `google.com` as documented. It is a config default, editable from the admin page, so it is not worth further deliberation. (Adding the router as a target is `BACKLOG.md` item 1, not phase 1.)
- **Ping implementation** — shell out to the system `ping` binary with an argv list (never `shell=True`), rather than raw sockets. The system binary is already setuid/setcap, so it works unprivileged on both Crostini and the Pi, and the service needs no `CAP_NET_RAW` grant.
- **Front-end assets are vendored, never loaded from a CDN.** Keeps the dashboard working without internet access and keeps third-party script execution off a page that can reboot the Pi. The specific charting library is chosen at M5, not now.

## Security posture

The services sit on a trusted LAN with no authentication, by requirement. That makes the threat model "anything already on the LAN, plus anything a browser on the LAN can be tricked into doing" — which is not nothing: a compromised IoT device or a malicious web page is inside that boundary.

Committed for phase 1, costed into the milestones above:

- **No `shell=True` anywhere**, and every subprocess call takes an argv list. Ping targets come from user-editable config, so this is the primary injection path. *(M1)*
- **`yaml.safe_load` only**, never `yaml.load`. *(M1)*
- **Parameterised SQL throughout** — no string interpolation into queries, including the CSV export's date range. *(M1, M6)*
- **Flask debug mode hard-off in all environments.** The Werkzeug debugger is remote code execution to anyone who can reach the port. *(M1)*
- **Explicit bind address**, set from config rather than defaulted implicitly. *(M1)*
- **Dedicated non-root `bbmon` service user**; no service runs as root or as `pi`. *(M2)*
- **Systemd sandboxing** on every unit: `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, and `ReadWritePaths=` naming only `/var/lib/bbmon`. *(M2)*
- **SSH key-only auth to the Pi, password auth disabled.** A guessable password on the well-known `pi` account is the largest single hole on a stock Pi, and it is unrelated to bbmon's own code. *(M2)*
- **Sudoers grant is exactly one fixed command** — no wildcard, no argument the web app controls. *(M4)*
- **CSRF tokens on every state-changing request** (config save, force reboot). Without authentication there is no session to protect, but there is still an action to protect: any page in a LAN browser can otherwise POST a reboot. *(M6)*
- **Host-header allowlist**, rejecting requests whose `Host` is not an expected name or IP. This is what stops DNS rebinding turning a random website into a client of the admin page. *(M6)*
- **Config written atomically** (temp file plus rename) with restrictive permissions, and only to the one fixed configured path — no user-supplied path reaches the filesystem. *(M6)*

Deferred to `BACKLOG.md` as hardening rather than hole-closing: a host firewall restricting port 8080 to the LAN subnet, request rate limiting, and admin-page authentication.

**Standing check, every milestone:** if a change adds a way for input to reach a subprocess, the filesystem, or SQL, it gets called out at review time rather than found at G4.

## Open items

- **Crostini cannot currently resolve or reach the Pi** — the Chromebook itself sees `raspberrypi`, so the container is most likely not on the home network. To be diagnosed at the start of M2, not before; addressing is being handled outside this plan.
- **`rsync` is not installed on the Crostini container** — one `apt install`, needed before `deploy.sh` is useful.
- **Collector interface shape** stays provisionally settled at M1 and is only confirmed at M3, when a second, very differently-shaped collector uses it.
