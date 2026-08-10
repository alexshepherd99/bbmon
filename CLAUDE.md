# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`bbmon` — home broadband monitoring running headless on a Raspberry Pi.

Development happens on a Chromebook under Crostini; a Pi is not involved until
late in the cycle. Assume no Pi hardware is available unless told otherwise.

Persistent docs follow `agentic`'s `shared/persistent-docs.md`:
- `BACKLOG.md` — not-yet-started work.
- `docs/phase-1/` — the current effort's `requirements.md`, `plan.md`, `log.md`.
  Its "Open decisions" section lists what is genuinely still undecided.

## Shared agents/skills/conventions

This repo pulls in reusable agents, skills, and conventions from the
`agentic` repo, mounted read-only at `../agentic`.
See its `learning/CONVENTIONS.md` for settled decisions and
`skills/`/`agents/` for what's available.

Start sessions with `./claude.sh` — it mounts `agentic` so the skills
below actually load. If `how-we-work` is not in your available skills,
this session was started without it: say so and ask to relaunch.

Apply these shared skills as a matter of course:
- **`how-we-work`** — the working discipline for any task; consult it at
  the very start of a piece of work, before planning or code.
- **`coding-standards`** — how code here should be written; consult it
  before writing, modifying, or reviewing code.

Propose changes to `agentic` with the **`propose-shared-change`**
skill — it defines how a draft crosses from this session to one
that can write to `agentic`.
