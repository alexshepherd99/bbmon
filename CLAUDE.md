# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`bbmon` — home broadband monitoring running headless on a Raspberry Pi. See
`REQUIREMENTS.md` for the full spec: phased requirements, platform assumptions,
and the open questions still to be decided.

## Shared agents/skills/conventions

This repo pulls in reusable agents, skills, and conventions from the
`agentic` repo, mounted read-only at `../agentic`.
See its `learning/CONVENTIONS.md` for settled decisions and
`skills/`/`agents/` for what's available.

Apply these shared skills as a matter of course:
- **`how-we-work`** — the working discipline for any task; consult it at
  the very start of a piece of work, before planning or code.
- **`coding-standards`** — how code here should be written; consult it
  before writing, modifying, or reviewing code.

Propose changes to `agentic` with the **`propose-shared-change`**
skill — it defines how a draft crosses from this session to one
that can write to `agentic`.
