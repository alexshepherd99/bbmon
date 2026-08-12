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

## 2026-08-09 — M1 built: the walking skeleton runs

The vertical slice is complete and on `main` in seven commits: config → SQLite → pinger → a live latency chart in a browser. 92 tests, green. Verified by running the whole stack for 70s — 13 points per target served over HTTP while the pinger wrote, 42 rows on disk after the exit flush.

Three decisions were taken before any code was written.

**Packaging.** A `pyproject.toml` with an editable install, rather than a bare `requirements.txt`. Imports then resolve identically in development and from a non-editable install on the Pi, so no service depends on being started from the right directory.

**Charting library: Apache ECharts, not Chart.js.** The first recommendation was uPlot, argued from bundle size on low-spec hardware. That argument was wrong and was withdrawn: the chart renders in the browser on a phone or laptop, not on the Pi, so bundle size is close to irrelevant and the Pi-side cost is the SQL aggregation that M5 already addresses. Re-decided on the real discriminator, requirement 7's hourly box plot. Chart.js is unambiguously the standard core (12.6M weekly downloads, 67.6k stars) but has no box plot; that would have rested on a 136-star single-maintainer plugin with a 2-star statistics dependency inlined into it, untouched for ten months. ECharts has `boxplot` as a native series type from one Apache-governed project with no plugin at all, and is committed to weekly. Vendoring also reshapes the security question: with no `npm install` on the Pi, no post-install scripts and no CDN, the risk is a one-time "is this copy clean?" review rather than an ongoing trust relationship, so the bundle's provenance and SHA-256 are recorded next to it.

**Development paths.** One environment variable, `BBMON_CONFIG`, selects the settings file; everything else including the database location is an ordinary field inside it. Noted at the time: M6's admin form must not render `database.path`, since pointing a running service at a different database from a web page is a good way to appear to have lost all the data.

Three findings came out of the work itself, each from checking rather than assuming.

**A test that passed for the wrong reason.** The `yaml.safe_load` test asserted only that `ConfigError` is raised. Mutating the loader to `yaml.unsafe_load` left it passing — while the payload actually executed and created its marker file. The exploit ran, the resulting value then failed validation, and the assertion was satisfied on the way past. It now asserts the side effect never happened. Worth remembering as a shape: a test that asserts "an error was raised" proves nothing about *which* error, or about what happened before it.

**Buffered data was lost on every stop.** Running the pinger for real, then stopping it, wrote nothing. systemd stops services with SIGTERM, which by default kills the process outright, so the `finally` flush never ran and up to a minute of pings went with it — on every stop, and M2's `deploy.sh` restarts services on each deploy. Now handled into an event that ends the loop normally; the inter-cycle sleep is that event's wait, so a stop is not left waiting out the ping interval. This was invisible to the unit tests and only surfaced by running the thing.

**Two guards that mutation showed were not doing what they appeared to.** `PRAGMA busy_timeout` was the same value `sqlite3.connect(timeout=)` already sets, written twice — removed. Removing `ORDER BY` changed no test result either, because the range scan uses the timestamp index and happens to return sorted rows; that makes the ordering incidental to the query plan rather than guaranteed, so it stays, and the test was instead shown to have teeth by flipping it to `DESC`.

Not verified: no browser has loaded the dashboard page. The route, the JSON contract and the vendored asset are all covered by tests, but the chart's actual rendering is unconfirmed.

Observed and left as a question rather than decided: with a 60s flush interval, a freshly started dashboard shows nothing for the first minute, including after every deploy.

Next: M2 — Pi bootstrap and the deploy loop, starting with the Crostini-cannot-reach-the-Pi diagnosis deferred from the replan.

## 2026-08-11 — Requirement 5's speed test tool replaced

`speedtest-cli`, named as the tool in requirement 5 since the original requirements were written, is no longer usable. Its repository was archived read-only on 2026-01-21; its last release is 2.1.3 from April 2021, documented as supporting "Python 2.4–3.7" against a project that requires 3.11+; and it is reported to hang against Ookla's current backend. The requirement was written before any of that was true and was carried forward unexamined — it was re-checked only because M3 was about to depend on it.

Replaced with the **official Ookla Speedtest CLI**, chosen over the open-source `librespeed-cli` because its figures come from the speedtest.net server network and are therefore the ones an ISP will recognise, which is the point of measuring at all. The cost is accepted knowingly: a proprietary closed-source binary on the Pi, an EULA and GDPR acceptance, and results transiting Ookla.

The replacement improves the dependency position rather than worsening it. Both candidates are command-line binaries rather than Python libraries, so the collector shells out with an argv list and parses `--format=json` — the same shape `PingCollector` already uses for `ping`, and already sanctioned by this plan's decision to prefer the system binary. `bbmon`'s Python dependencies stay PyYAML and Flask; nothing is added to `pyproject.toml`. The new dependency is an external binary installed by `bootstrap.sh`, which is a different kind of risk, not a larger one — no dependency tree, no package manager, and a version we pin ourselves.

Hardware confirmed as a **Pi 3 Model B** (ARMv8 Cortex-A53). Ookla publishes `armel`, `armhf` and `aarch64` builds, so every plausible Raspberry Pi OS bitness is covered; which one this Pi needs is read from `uname -m` at bootstrap rather than assumed, and is confirmed at G1. Ookla's apt repository is 64-bit only, so the tarball is the install route regardless.

Deploying to x86 in future was raised and deliberately not designed for now. Recorded in `BACKLOG.md` instead, with the finding that it is verification rather than a port: the tarball architecture string is the only architecture-coupled thing in the system.

Next: M3 — the speed test collector, pulled ahead of M2. Its stated dependency is M1, not M2, and `speedtest_results` already exists in the schema at version 1, so nothing is out of order. G2 still needs a deployable Pi and therefore trails M2; because gates are cleared in batches at home, running M3 first means G1 and G2 clear on one visit rather than two.

## 2026-08-12 — M3 built: the speed test collector

Five commits on `main`: model and storage, the collector, the shared service loop, the service entrypoint, the dashboard panel. 129 tests, green. No schema change was needed — M1 created all three tables up front, and that decision paid for itself here.

**The collector interface survived unchanged.** This was M3's actual purpose, and the answer is that the abstraction held: `SpeedtestCollector` implements `name`, `interval_seconds`, `collect` and `store` with no additions. Three strains showed up and none of them justified changing it yet.

- `collect()` returns a `Sequence` because a ping cycle measures several targets. A speed test returns a single-element list, which is slightly silly but costs nothing and keeps one shape for the service loop.
- `interval_seconds` is an awkward name for something configured in hours. The conversion sits in the collector, so the loop stays in one unit.
- Requirement 5's "skip if a reboot is imminently due" has nowhere to live on the interface. **This is not built** — it needs M4's reboot mechanism to exist before there is anything to ask. Carried to M4 rather than backlogged, since it is a phase-1 requirement.

**What did need changing was the code around the interface, not the interface.** `PingerService` was already collector-agnostic and only its *name* was ping-specific; it moved to `bbmon.service` as `CollectorService`. The SIGTERM wiring moved with it into `run_until_stopped`, so the fix for M1's flush-on-shutdown data loss is inherited by every future service instead of being re-typed and possibly forgotten. Those two code paths had no tests before and now do.

The speed test deliberately does **not** buffer. Buffering exists to spare the SD card thousands of small ping writes; one row every few hours held in memory for hours is just a row a crash loses.

Two test defects were found by mutation rather than by review, both the same shape as M1's `ORDER BY` finding — a test passing through a path other than the one it names.

- `latest_speedtest_result`'s ordering test inserted the newest row first, so natural row order already gave the right answer and dropping `ORDER BY timestamp DESC` left it green. Rewritten to insert the oldest last.
- The non-zero-exit test passed empty output, so it was satisfied by the "no result object" branch and stayed green with the exit-code check replaced by `if False`. Rewritten to return a complete, parseable result alongside a non-zero exit, so only the exit code can catch it.

**The headless render check earned its keep.** Adding the panel broke it immediately: the harness builds its own DOM, which lacked the new elements, so `dashboard.js` threw on a null. Fixing that exposed a second, subtler problem — the harness's placeholder text was the same string the page writes when no speed test has ever run, which made "the fetch has not returned" indistinguishable from "there is no data", and the panel was being read before it had loaded. It now uses a distinct sentinel and waits for both polls. The panel's success and failure renderings were then both confirmed against a running app.

Not verified, and it is the main risk M3 carries into G2: **no real Ookla binary has ever been run.** Every test injects canned JSON, so the parsing is only as right as the assumed output shape. `--format=json`'s field names, the `bandwidth` unit, and whether progress objects share the stream are all assumptions until the binary runs. The parser deliberately locates the result object rather than assuming it stands alone, and treats an unrecognised shape as a recorded failure, so a wrong guess degrades to "failures recorded" instead of a crash loop — but it is still a guess.

Next: M2 — Pi bootstrap and the deploy loop. Its open question is whether the Ookla CLI is installed from a pinned tarball or from Ookla's apt repository, now that 64-bit hardware makes the latter possible.

## 2026-08-12 — The Ookla CLI checked against the real binary

M3's parsing was written against an assumed output shape, which was the largest risk it carried into G2 — a wrong guess would have cost a home visit to find. The binary was installed on the Crostini container instead (x86_64 tarball, into `~/.bbmon-tools/` alongside jsdom, deliberately not into the repo and not system-wide) and run for real.

**Every field assumption held.** `type: "result"`, `ping.latency`, `download.bandwidth`, `upload.bandwidth`, `isp`, `server.name` and `server.location` are all present with the expected types, and `bandwidth` is indeed bytes per second. A real run through `SpeedtestCollector` produced plausible download, upload and latency figures, with the ISP and server-name fields populated as expected.

Facts worth having recorded for M2 and G2:

- **Version tested: 1.2.0.84**, a statically linked musl binary with no dependencies. The tarball's SHA-256 is `5690596c54ff9bed63fa3732f818a05dbc2db19ad36ed68f21ca5f64d5cfeeb7` — that is the x86_64 build, so the Pi's `aarch64` tarball needs its own checksum recorded at M2.
- **`--format=json` emits exactly one line**, and the licence and GDPR banners go to **stderr**, so they cannot corrupt the JSON on stdout. The `--accept-license --accept-gdpr` flags work non-interactively on a first run, as intended.
- **The binary writes `~/.config/ookla/speedtest-cli.json`.** With an unwritable home it logs `Failed to save settings` to stderr, exits 0 and still prints a complete result on stdout. This matters because M2's security posture commits to `ProtectHome=yes` on every unit: the speed test tolerates it, so that directive does not need relaxing. Worth re-confirming at G1 rather than assuming the Pi behaves identically.

One real defect was found by the exercise, and not the one expected. The parser scanned stdout line by line, which cannot parse a pretty-printed object spread over several lines — that would have been recorded as a failed speed test rather than surfaced as a parsing problem, and only ever on the Pi. The whole of stdout is now tried first, with the line scan kept as a fallback. The assumption that was actually wrong was about *robustness*, not about the field names.
