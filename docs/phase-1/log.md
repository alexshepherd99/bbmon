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
