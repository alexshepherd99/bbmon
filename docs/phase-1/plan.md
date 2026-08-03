# Phase 1 — Plan

**Status: draft milestone outline.** Sequence and dependencies only. The technical approach for each milestone is agreed in conversation when that milestone starts (propose-before-writing), not pre-committed here.

All milestones through M8 are developed and tested entirely on Crostini. M9 is the first time real Pi hardware is involved.

## Milestones

**M1 — Project skeleton and test harness.** Package layout for the shared `bbmon/` package, pytest wired up, a green (trivial) suite to start from. No behaviour yet. *Depends on: nothing.*

**M2 — Config loader.** YAML load, defaults, validation rules (positive intervals, valid hostnames/IPs). Validation is the behaviour worth testing first. *Depends on: M1.*

**M3 — DB layer and schema init.** SQLite access layer with WAL and busy timeout, the three tables, the one-shot versioned init step. *Depends on: M1.* Open decision — purge job placement — affects M4/M8 wiring, not the schema.

**M4 — Collector interface and pinger.** Define the common collector interface (item 9) with the pinger as its first implementation, so the interface is shaped by a real case rather than guessed. Includes the buffered-flush write path. *Depends on: M2, M3.* Risk: unprivileged ICMP under Crostini — resolve early, it may constrain the implementation.

**M5 — Speed test collector.** Second collector, which is the real test of whether M4's interface generalises. Failure rows recorded, not skipped. *Depends on: M4.* Dev runs validate the code path only; the numbers mean nothing until M9.

**M6 — Restart logging and reboot abstraction.** `restarts` rows, expected/unexpected detection on startup, NTP-sync wait. The reboot action itself is behind an abstraction whose only implementation before M9 is a no-op. *Depends on: M3.* Open decision — reboot trigger mechanism — is deferred to M9 behind this abstraction.

**M7 — Web dashboard.** Flask app, aggregation + caching layer, the three charts, latest-speedtest panel, restart list, polling refresh, version indicator. *Depends on: M3, M6.* Blocked on the port/route-structure decision. Mobile layout is built here but only verified on a real phone at M9.

**M8 — Config/admin page, CSV export, retention purge.** The remaining web surface plus the daily purge job. *Depends on: M2, M7.* Blocked on the purge-placement decision.

**M9 — Pi deployment.** First real hardware. Setup script, update script, systemd units, `Restart=on-failure`, log rotation, LAN access from a phone, low-spec behaviour measured for the first time, and the reboot trigger mechanism decided and implemented. *Depends on: everything above.*

## Notes

- M4 and M5 are the pair that proves the collector interface; resist finalising it at M4 alone.
- M9 is deliberately a single milestone rather than a step tacked onto each earlier one — batching the Pi-only work keeps the dev loop fast, at the cost of discovering deployment problems late. If that trade turns out badly, pull a minimal "does it run on the Pi at all" check forward to just after M4.
