# Phase 1 — Log

Append-only. Corrections go in a new entry, never by editing an old one.

## 2026-08-03 — Effort opened

Ran `init-project-docs`. This was a migration, not a clean scaffold: `REQUIREMENTS.md` already existed at the repo root, written before the repo adopted `agentic`'s persistent-document structure.

Mapping applied, with sign-off on each of the three decisions:

- Phase 1 sections, Overview, and Platform Assumptions → `requirements.md` here.
- Phase 2+ list (7 items) → `BACKLOG.md`, priority order preserved.
- "DB schema migration strategy" open question → `BACKLOG.md` (its stated driver is Phase 2+ collectors). Phase 1 keeps only a versioned-schema-init constraint so the mechanism can land later without rework.
- Remaining four open questions → "Open decisions" in `requirements.md`. Three block specific milestones; the reboot trigger mechanism is marked deploy-stage, since it cannot be tested on Crostini.
- Root `REQUIREMENTS.md` deleted rather than stubbed — recoverable at commit 05ab2b5, and one source of truth beats two. `CLAUDE.md`'s pointer updated.
- Effort named `phase-1` to match the vocabulary already in use. Not collapsed to a single `log.md` — ten requirement sections across four services is past trivial.

Recorded the development environment as a first-class platform assumption: development happens on a Chromebook under Crostini, with a Raspberry Pi entering only at M9. Consequences captured in `requirements.md` (systemd, ICMP privileges, reboot no-op, non-comparable speed test figures, no LAN reachability, CPU architecture, unverifiable low-spec behaviour).

`plan.md` drafted as a milestone outline only — sequence and dependencies, with per-milestone approach left to be proposed when each starts.

No code written yet. Next: M1, once the open decisions that block early milestones are ready to be taken.

## 2026-08-05 — Replanned for a fast prototype, with the Pi in the loop

Two premises of the 2026-08-03 plan turned out to be wrong, and the replan follows from correcting them.

**A Pi is available now.** The previous plan deferred all hardware to a final M9. Corrected: hardware access is *intermittent* — Alex is often developing away from it — which is a different problem from hardware being absent, and neither "wait for the Pi" nor "ignore the Pi until the end" answers it. Resolution: every milestone is built and unit-tested to completion on Crostini so nothing blocks on access, and Pi-only verification is batched into four gates (G1 first deploy, G2 real measurements, G3 reboot, G4 soak) cleared in a single visit. The deploy loop is pulled early to M2 so clearing a gate costs minutes, not a re-setup.

**The goal is a working prototype quickly.** The previous plan built each component fully before the next, so nothing was visible end-to-end until M7. Replaced with a vertical slice: M1 alone carries config → SQLite → pinger → a live chart in a browser, deliberately thin (one chart, raw rows, no aggregation, no admin page, no speedtest), and later milestones thicken a running system. `plan.md` rewritten rather than amended; the original is at commit `344cc08`.

Scope discipline is stated explicitly in the plan, with non-goals named and a standing rule that discoveries go to `BACKLOG.md`. Security is the sole admitted exception, and only where something opens a hole in the home network.

All four open decisions closed, so no milestone now starts on a guess: reboot via a sudoers rule scoped to exactly one fixed command; one Flask app on 8080 with admin at `/admin`; purge inside the pinger loop rather than its own timer; ship the documented ping defaults. Two more decisions were forced by the plan's shape — ping via the system `ping` binary with an argv list (works unprivileged on both platforms, so the service needs no `CAP_NET_RAW`), and front-end assets vendored rather than loaded from a CDN.

On security, the judgement made was between measures that close a hole and measures that raise a cost. Four were treated as holes: Flask debug mode (remote code execution to anyone who can reach the port), CSRF plus a Host-header allowlist (with no authentication there is no session to protect, but there is still an action — any LAN browser can otherwise be made to POST a reboot, and DNS rebinding turns an arbitrary website into a client of the admin page), SSH password auth on the well-known `pi` account (the largest hole on the box, and nothing to do with bbmon's code), and user-editable ping targets reaching a subprocess. A firewall rule, rate limiting and admin authentication were deferred to `BACKLOG.md` as hardening — admin auth is also excluded by requirements 7 and 8 on purpose.

Deploy transport chosen deliberately as two paths, not one: `deploy.sh` rsyncs the working tree for the dev loop (no commit per hardware test, so main does not fill with WIP), and `update.sh` does git pull plus restart for proper deploys. The repo being public means the Pi needs no stored credentials for the git path.

Discovered and not yet resolved: the Crostini container cannot resolve or reach `raspberrypi` although ChromeOS itself can, so the container is likely off the home network. Deferred to the start of M2 by agreement rather than chased now. `rsync` is also not yet installed in the container.

Next: M1.

## 2026-08-05 — Superseded text is deleted here, not annotated

`requirements.md` was first updated using `agentic`'s persistent-docs convention, which keeps superseded wording in place under a dated marker. On review that was rejected for this repo: the annotated version left a reader holding two contradictory statements and working out which one is current, and git already provides the recovery the convention is protecting.

Standing decision for bbmon: **state the current truth directly and delete what it replaces.** The reasoning behind a change belongs in this log, which is append-only and is the right place to look for what was believed when.

This diverges from the shared convention rather than reinterpreting it. Not yet proposed back to `agentic`.
