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
- **Authentication on the admin page** — deliberately excluded from phase 1 by requirements 7 and 8 ("no authentication (trusted LAN)"). Worth reopening if the dashboard is ever exposed beyond the LAN, or if untrusted devices (guest WiFi, IoT) share the network. Note this changes the CSRF picture: tokens would then protect a session rather than just an action.

## Infrastructure

- **DB schema migration strategy** — if future (Phase 2+) collectors add tables/columns, how does the refresh/update script handle schema changes? Even a simple "migrations run automatically on service start" approach needs to be defined. Phase 1 only commits to a versioned schema-init step so a mechanism can be added later without rework — see `docs/phase-1/requirements.md`.
