# Backlog

Not-yet-started work. Items move out of here into `docs/<effort-name>/` when picked up.

## Phase 2+ monitoring features

Ordered by suggested priority (highest value / most natural extension first). Carried over from `REQUIREMENTS.md` (removed 2026-08-03; original at commit 05ab2b5).

1. **Gateway/router ping alongside WAN targets** — ping the router's LAN IP as one of the regular ping targets, to distinguish "LAN/WiFi problem" from "ISP problem." Directly extends the existing ping architecture — just another target.
2. **Jitter** — derive latency variance between consecutive pings from data already being collected. Standard VoIP/gaming quality metric, effectively free to add.
3. **DNS resolution time** — measure and record hostname resolution time separately from the ping itself, per target.
4. **HTTP(S) response time** — full request time (not just ICMP) to a few configured real sites, via `requests` — closer to "does the internet actually feel fast."
5. **WiFi signal strength/link quality** — if the Pi itself is on WiFi, log signal strength over time via `iwconfig`/`iw` output (subprocess call, no extra hardware). Lower priority — only relevant if the Pi isn't wired.
6. **GPIO-based sensor collectors** — the original motivating "future test type" example; plugs into the Phase 1 collector interface once specific sensors are chosen.
7. **Concurrent-speedtest chart marker** (minor UI polish) — mark on the ping latency chart when a speed test was running concurrently, since it causes a visible (expected) latency bump that would otherwise look unexplained.

## Security hardening

Deferred from phase 1 on 2026-08-05 as hardening rather than hole-closing — each raises the cost of an attack that the phase-1 measures already block outright. The measures that *do* close holes (CSRF tokens, Host-header allowlist, key-only SSH, non-root service user, systemd sandboxing, injection-safe coding) are in phase 1 — see the "Security posture" section of `docs/phase-1/requirements.md`'s sibling `plan.md`.

- **Host firewall restricting the web port to the LAN subnet** — an nftables/ufw rule so port 8080 is unreachable from outside the local subnet even if the router is ever misconfigured or a port-forward is added by accident. Defence in depth against a mistake elsewhere, not against a current exposure.
- **Request rate limiting on the web app** — caps brute-force and accidental-loop traffic against the dashboard and admin endpoints. Low value while the LAN is trusted and there are no credentials to guess; worth revisiting if authentication is ever added.
- **Set `UMask=0077` on the bbmon units** — `systemd-analyze security` scored the pinger 5.8 (MEDIUM) at G1, and the one finding worth recording was that files the services create are world-readable by default. The database directory itself is `0755`, so on a single-user Pi this exposes ping and speed test history to any local account. Hardening rather than hole-closing: it needs a local login to exploit, and the same data is already served unauthenticated over the LAN by requirement 7. The rest of that score is directives phase 1 deliberately did not take (`SystemCallFilter`, `RestrictAddressFamilies`, `ProtectKernelTunables` and similar) — worth a pass together rather than one at a time.
- **Authentication on the admin page** — deliberately excluded from phase 1 by requirements 7 and 8 ("no authentication (trusted LAN)"). Worth reopening if the dashboard is ever exposed beyond the LAN, or if untrusted devices (guest WiFi, IoT) share the network. Note this changes the CSRF picture: tokens would then protect a session rather than just an action.

## Infrastructure

- **DB schema migration strategy** — if future (Phase 2+) collectors add tables/columns, how does the refresh/update script handle schema changes? Even a simple "migrations run automatically on service start" approach needs to be defined. Phase 1 only commits to a versioned schema-init step so a mechanism can be added later without rework — see `docs/phase-1/requirements.md`.
- **Run bbmon on x86** — phase 1 targets a Pi 3 Model B and is verified only on ARM, but deploying to x86 hardware is a live possibility. The porting work is expected to be near-zero rather than a port: Python, Flask, PyYAML, SQLite, the systemd units and the `ping` subprocess are all architecture-neutral, and the one architecture-specific step is choosing which Ookla Speedtest CLI tarball to download — Ookla publishes `x86_64` and `i386` builds alongside the ARM ones under the same URL pattern. `bootstrap.sh` therefore selects that tarball from `uname -m` rather than hardcoding an ARM string, so this item is *verification*, not implementation: run bootstrap on an x86 Debian-family host and confirm the services come up. Two things genuinely need checking rather than assuming — that the `uname -m` values on x86 (`x86_64`, `i686`) map to the right tarball names, and that nothing else in `bootstrap.sh` has quietly assumed Raspberry Pi OS specifically (package names, default user, hostname). Non-Debian x86 distributions are a larger job and out of scope for this item.
