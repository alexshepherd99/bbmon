# Phase 1 — Log

Append-only. Corrections go in a new entry, never by editing an old one.

## 2026-08-03 — Effort opened

Ran `init-project-docs`. This was a migration, not a clean scaffold: `REQUIREMENTS.md` already existed at the repo root, written before the repo adopted `agentic`'s persistent-document structure.

Mapping applied, with sign-off on each of the three decisions:

- Phase 1 sections, Overview, and Platform Assumptions → `requirements.md` here.
- Phase 2+ list (7 items) → `BACKLOG.md`, priority order preserved.
- "DB schema migration strategy" open question → `BACKLOG.md` (its stated driver is Phase 2+ collectors). Phase 1 keeps only a versioned-schema-init constraint so the mechanism can land later without rework.
- Remaining four open questions → "Open decisions" in `requirements.md`. Three block specific milestones; the reboot trigger mechanism is marked deploy-stage, since it cannot be tested on Crostini.
- Root `REQUIREMENTS.md` deleted rather than stubbed — recoverable at commit 7da4a66, and one source of truth beats two. `CLAUDE.md`'s pointer updated.
- Effort named `phase-1` to match the vocabulary already in use. Not collapsed to a single `log.md` — ten requirement sections across four services is past trivial.

Recorded the development environment as a first-class platform assumption: development happens on a Chromebook under Crostini, with a Raspberry Pi entering only at M9. Consequences captured in `requirements.md` (systemd, ICMP privileges, reboot no-op, non-comparable speed test figures, no LAN reachability, CPU architecture, unverifiable low-spec behaviour).

`plan.md` drafted as a milestone outline only — sequence and dependencies, with per-milestone approach left to be proposed when each starts.

No code written yet. Next: M1, once the open decisions that block early milestones are ready to be taken.

## 2026-08-05 — Replanned for a fast prototype, with the Pi in the loop

Two premises of the 2026-08-03 plan turned out to be wrong, and the replan follows from correcting them.

**A Pi is available now.** The previous plan deferred all hardware to a final M9. Corrected: hardware access is *intermittent* — Alex is often developing away from it — which is a different problem from hardware being absent, and neither "wait for the Pi" nor "ignore the Pi until the end" answers it. Resolution: every milestone is built and unit-tested to completion on Crostini so nothing blocks on access, and Pi-only verification is batched into four gates (G1 first deploy, G2 real measurements, G3 reboot, G4 soak) cleared in a single visit. The deploy loop is pulled early to M2 so clearing a gate costs minutes, not a re-setup.

**The goal is a working prototype quickly.** The previous plan built each component fully before the next, so nothing was visible end-to-end until M7. Replaced with a vertical slice: M1 alone carries config → SQLite → pinger → a live chart in a browser, deliberately thin (one chart, raw rows, no aggregation, no admin page, no speedtest), and later milestones thicken a running system. `plan.md` rewritten rather than amended; the original is at commit `4c5f866`.

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

**Every field assumption held.** `type: "result"`, `ping.latency`, `download.bandwidth`, `upload.bandwidth`, `isp`, `server.name` and `server.location` are all present with the expected types, and `bandwidth` is indeed bytes per second. A real run through `SpeedtestCollector` produced plausible download, upload and latency figures, with the ISP and server-name fields populated as expected. (Redacted 2026-08-13 — see that day's entry.)

Facts worth having recorded for M2 and G2:

- **Version tested: 1.2.0.84**, a statically linked musl binary with no dependencies. The tarball's SHA-256 is `5690596c54ff9bed63fa3732f818a05dbc2db19ad36ed68f21ca5f64d5cfeeb7` — that is the x86_64 build, so the Pi's `aarch64` tarball needs its own checksum recorded at M2.
- **`--format=json` emits exactly one line**, and the licence and GDPR banners go to **stderr**, so they cannot corrupt the JSON on stdout. The `--accept-license --accept-gdpr` flags work non-interactively on a first run, as intended.
- **The binary writes `~/.config/ookla/speedtest-cli.json`.** With an unwritable home it logs `Failed to save settings` to stderr, exits 0 and still prints a complete result on stdout. This matters because M2's security posture commits to `ProtectHome=yes` on every unit: the speed test tolerates it, so that directive does not need relaxing. Worth re-confirming at G1 rather than assuming the Pi behaves identically.

One real defect was found by the exercise, and not the one expected. The parser scanned stdout line by line, which cannot parse a pretty-printed object spread over several lines — that would have been recorded as a failed speed test rather than surfaced as a parsing problem, and only ever on the Pi. The whole of stdout is now tried first, with the line scan kept as a fallback. The assumption that was actually wrong was about *robustness*, not about the field names.

## 2026-08-12 — The dashboard seen in a real browser for the first time

All three services were run together against real data and the page was loaded from the Chromebook. Both panels drew correctly: a genuine speed test result with all three figures and the ISP name rendered, and the latency chart with three live series. This is the first time the canvas renderer and the poll timers have been exercised at all — jsdom covers neither, and its header has always said so.

Two pieces of feedback, both deferred by agreement rather than acted on.

- **A speed test history chart** is wanted. No new item was raised: requirement 7 already asks for it and `plan.md` already schedules it at M5, alongside the hourly box plot, the pre-aggregation and the short-lived cache both charts read through. Recorded here only so the request is not mistaken for something missing from the plan.
- **The latency chart re-animates on every poll**, redrawing left to right over roughly a second, every five seconds. Added to "Open items" with the probable cause: `replaceMerge: ["series"]` makes ECharts treat each poll as new series and replay the entry animation. A display defect, not a data one.

The three fake speed test rows seeded during the earlier headless verification were deleted before this run, so nothing shown was fabricated.

## 2026-08-12 — M2 built: bootstrap, the deploy loop, and Pi access

Five commits on `main`: the one-shot init entrypoint, the four systemd units, the three scripts, and `docs/pi-access.md`. 176 tests, green. Everything below reached **done (dev)**; **G1 is not cleared** and nothing here has run on the Pi.

**The open item M2 was supposed to start by diagnosing was simply wrong.** The plan had recorded that the Crostini container could not resolve or reach the Pi and was "most likely not on the home network". It resolves the Pi by name and pings it fine. The container sits on a host-only subnet and routes out through ChromeOS, so the Pi is two hops away rather than on the same link — which is exactly why it works and was never the problem. The belief appears to have been formed once, early, and carried forward unexamined; the same failure mode as requirement 5's dead `speedtest-cli`, found the same way, by checking it when something finally depended on it.

Two things were discovered while checking it. The Pi's SSH host key had changed since January (ECDSA to ED25519) because it had been reimaged — confirmed rather than assumed before the stale entry was cleared. And the server still advertises `password` as an authentication method, so requirement-level key-only SSH is written up in `docs/pi-access.md` but is **not yet done on the Pi**; that is a G1 item.

**Machine and network specifics are kept out of git entirely**, by decision taken during this milestone. Addresses, host key fingerprints and hostnames go in `docs/pi-access.local.md`, which `.gitignore` excludes via `*.local.md`; the committed `pi-access.md` carries the procedure and refers to it. Checking in anything identifying one machine or LAN is outside this project's risk appetite, and losing the local file is accepted as the cost. The first draft of `pi-access.md` did record them, and was rewritten before anything was pushed.

**The security posture and requirement 4 turned out to be in direct conflict, and it would have cost a home visit to find out.** `NoNewPrivileges=yes` is committed for every unit. `/usr/bin/ping` carries a `cap_net_raw` file capability, and `NoNewPrivileges` makes the kernel ignore file capabilities — so the pinger under its own sandbox falls back to a raw socket and dies with "Operation not permitted". Reproduced directly under `systemd-run`, with the control run without the directive succeeding.

The resolution keeps both commitments rather than trading one away. `bootstrap.sh` installs a sysctl drop-in putting the `bbmon` group inside `net.ipv4.ping_group_range`, which lets `ping` use an unprivileged ICMP datagram socket needing no capability at all. Verified end to end on Crostini by widening the range, watching a previously-denied `SOCK_DGRAM`/`IPPROTO_ICMP` socket become allowed, watching `ping` succeed under `NoNewPrivileges=yes`, and restoring the original value. So the sandbox stays fully on *and* the service holds no `CAP_NET_RAW`, which is what the M1 ping decision wanted. An empty `CapabilityBoundingSet=` is what now holds that decision in place mechanically rather than by intention.

This is the second time a phase-1 assumption has been caught by running the thing rather than reading it, and both times the cost of not checking would have been a wasted trip home.

**A one-shot init step now exists as its own entrypoint.** Requirement 3 asked for it and M1 had approximated it by having every service call `db.initialise` on its own way up. That works, but it means three services race to create the same tables on boot, and a schema-version mismatch surfaces as three crash loops instead of one failed unit. `bbmon.init` is what the other units are ordered `After=` and `Requires=`. The per-service calls stay, as a safety net for standalone runs.

**Decisions taken while writing this, with their reasoning.**

- **Ookla CLI from a pinned tarball, not Ookla's apt repository** — the question M3 left open. The repository was rejected because adding it grants Ookla's signing key authority to install *any* package on the Pi through routine updates, which is a standing trust relationship; a checksummed tarball is a one-time, verifiable grant. Checksums were recorded for all five published architectures rather than only `aarch64`, because a Pi 3 can perfectly well be running 32-bit Raspberry Pi OS and discovering that at G1 would cost the visit. The `x86_64` value matches the one logged on 2026-08-12, which is what confirms these are all the same release.
- **Editable install at `/opt/bbmon`** — so `deploy.sh` and `update.sh` take effect by replacing files and restarting, with no reinstall step in the day-to-day loop. The checkout is owned by the admin account rather than root, which is what lets `deploy.sh` rsync into it over SSH with sudo needed only for `systemctl restart` rather than for file writes.
- **`StateDirectory=bbmon` in place of an explicit `ReadWritePaths=`** — a departure from the letter of the security posture, which names `ReadWritePaths=`. It is the same grant plus correct ownership on every start, and writing both would be the same thing twice: the identical finding to M1's duplicated `busy_timeout`.
- **The `bbmon` user's home is `/var/lib/bbmon`, not under `/home`** — so `ProtectHome=yes` does not hide it from the Ookla CLI's settings file. The binary tolerates an unwritable home anyway, so this keeps the logs quiet rather than making the speed test work.
- **`bootstrap.sh` never overwrites an existing `/etc/bbmon/config.yaml`** — that file is also what M6's admin page writes back to, so clobbering it on a re-run would silently discard settings changed from the dashboard.

**What is tested, and what deliberately is not.** Almost all of these scripts need a Pi, and G1 is the check. Three things did not need one and are covered: the init entrypoint, the units' security directives (parsed and asserted, so M4 adding a fifth unit by copying and trimming a fourth cannot quietly drop one), and `deploy.sh`'s changed-file-to-service mapping. That last one earns its test because its failure mode is the quietest the deploy loop has — a deploy reporting success while a service carries on running the code it had before. `deploy.sh` is sourceable so the mapping can be called directly.

None of these tests could be observed red for the right reason first: the units and scripts are data and shell rather than Python behaviour, and `bbmon.init`'s only pre-implementation red was the `ImportError` the standards rule out. All were confirmed by mutation instead — dropping `db.initialise`, returning 0 from its error path, removing `NoNewPrivileges` from a unit, adding a `ReadWritePaths` line, dropping `Requires=bbmon-init`, blanking the shared-module catch-all, and removing the source guard. Each failed the tests it should have.

`shellcheck` and `rsync` were installed on the container to do this work; `rsync` was already noted in the plan as needed before `deploy.sh` was useful.

Next: G1 — the first real deploy, which is the first time any of M2 will have executed. After that, M4.

## 2026-08-12 — G1 cleared, and it found four defects

The Pi was bootstrapped from a clean state and everything M2 built ran on hardware for the first time. **G1 is cleared** apart from the phone check, which needs a phone and moves to G4. What follows is mostly a list of things that were wrong, because that is what the gate was for.

**The system works.** All four units come up and run as `bbmon`; 180 ping rows across the three targets with **zero failures**; three real speed tests with plausible figures and populated ISP and server fields; schema at version 1; the dashboard and both API endpoints serve real data to another host on the LAN. `systemctl show` confirms `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes` and an empty `CapabilityBoundingSet` are genuinely in force, not merely written down.

Two things that were reasoned about in the abstract at M2 turned out right on hardware. **Unprivileged ICMP works under the sandbox** — zero ping failures is the proof, and it is the concern that drove the whole `ping_group_range` investigation. And the **Ookla CLI tolerates `ProtectHome=yes`**: it wrote `/var/lib/bbmon/.config/ookla/speedtest-cli.json` without complaint, because the service user's home was deliberately put outside `/home`.

**Four defects, none of which the unit tests could have found.**

*`bootstrap.sh` would have narrowed `net.ipv4.ping_group_range` system-wide.* Caught before running it, by reading the setting off the Pi rather than trusting the comment sitting next to the code. That comment asserted the range is empty on a stock Raspberry Pi OS; it actually ships wide open at `0 2147483647`. Writing a bare `$gid $gid` would have revoked unprivileged ICMP from every other account on the machine as a silent side effect of installing a monitoring tool. `ping_group_range_for` now only ever widens, and on this Pi correctly wrote nothing at all. The lesson is narrow and repeatable: the assumption was in a *comment*, and comments are not checked by anything.

*`bootstrap.sh` exited non-zero after succeeding.* `trap 'rm -rf "$tmp"' RETURN` on a function-local — bash installs RETURN traps globally rather than scoping them to the function, so it fired again when `main` returned, by which point `$tmp` was gone and `set -u` made cleanup an error. The script printed its entire success summary and then failed. It only did this on a *first* run, since a re-run skips the download branch and never sets the trap — so the broken case was exactly the first-time setup the script exists for, and the case a re-run cannot reveal.

*`deploy.sh` restarted everything on every deploy.* Its itemize filter treated `.` as a change. In `rsync --itemize-changes` the leading character is the update type and `.` means *no* update occurred — only attributes such as mtime were reconciled. Since `rsync -a` syncs mtimes and a fresh clone has clone-time mtimes throughout, every identical file itemised as changed. The whole "restart only what is affected" mechanism was therefore dead on arrival, and it announced its own success while being dead. After the fix, an unchanged tree restarts nothing and a change confined to `bbmon/web/app.py` restarts `bbmon-web` alone — confirmed by watching the other two services' `ActiveEnterTimestamp` stay put.

*Disabling password authentication did nothing.* The drop-in was installed as `60-bbmon-no-passwords.conf` and `sshd -T` still reported `passwordauthentication yes`. `sshd` takes the **first** value obtained for a keyword rather than the last, and Raspberry Pi OS ships `50-cloud-init.conf` setting it to `yes`, which sorts earlier and wins. Renumbering to `10-` fixed it. This is the most dangerous of the four, because the file sat there looking exactly right: the only thing separating "hardened" from "believed to be hardened" was running the verification step. `KbdInteractiveAuthentication` *had* applied, which made it look partially effective.

The change was made behind a `systemd-run --on-active=300` dead-man revert, disarmed only after key login was proven from a fresh connection. Recommended in `docs/pi-access.md` now — locking oneself out of a headless Pi costs a keyboard, a monitor and a disassembly.

**Two smaller findings.** Raspberry Pi OS Lite has no `git`, so the documented first step failed and, worse, a Pi bootstrapped without it would have run fine with `update.sh` permanently broken; `git` joins the apt list. And the Pi is running **Debian 13 (trixie) with Python 3.13.5**, not the Bookworm and "Python 3.11+" that `requirements.md` had asserted since before any hardware was examined — nothing depended on the wrong value, but it is now read off the machine rather than assumed.

**On the shape of all this.** Every one of the four defects is quiet: in each case the thing reports success while not working. That is the same shape as M3's unrun Ookla binary and M1's flush-on-shutdown loss, and it is now three milestones in a row where the defects were found by execution and were invisible to review and to unit tests. The tests added here are the ones that did not need a Pi — `ping_group_range_for` and `changed_paths_from_itemize` are both pure functions once extracted, and both are now covered, including a property test asserting the ping range never narrows. 193 tests, green.

**Not verified.** `update.sh` has still never run; the mobile layout has never been seen on a phone. Both carried to G4. Throughput figures from the Pi differ markedly from Crostini's, which is expected — G2 is where speed test numbers are actually assessed.

Next: M4 — restarts and the reboot mechanism.

## 2026-08-13 — What the end-of-session audit caught

Three things, none of which the work itself had surfaced. Recorded together because the pattern is the point: each had already passed a check earlier in the session.

- **A private address quoted inside the entry arguing against quoting addresses** — see the entry below.
- **The security review's findings existed only in conversation.** Two of them were decisions M6 has to honour, and would have been lost entirely. They are now in the security posture in `plan.md`, where M6 will actually meet them.
- **A regression in `update.sh`, introduced an hour earlier.** The dirty-tree fix filtered untracked files with `grep -v '^??'`; under `set -o pipefail` a grep matching nothing returns 1 and killed the script at the assignment. So it failed on a *clean* tree — the normal case — while working on the dirty tree it had been written for. It exited 1 having printed nothing, which is why nothing looked wrong.

That last one has the sharpest lesson: **the verification only exercised the case the change was about.** The fix was tested on a dirty tree, which passed, and the untouched path was never re-run. Both are checked now, and both cases pass on the Pi.

The general form, which is why the end-of-session review is worth the time rather than being a formality: **checking each change as it is made answers a different question from checking what the repository now is.** All three of these sat inside work that had been reviewed, tested and pushed.

## 2026-08-13 — The rule was broken by the entry that wrote the rule

Found in the end-of-session audit, which is the only reason it was found at all. The 2026-08-13 redaction entry below argued that a private LAN address is less identifying than an ISP and a city — and quoted the actual address to make the comparison. So the entry establishing that identifying data must not enter the repository put an identifying value into the repository, in its own supporting argument.

It survived four separate readings: writing it, committing it, a history rewrite performed specifically to remove identifying data from this file, and a review of that rewrite. Every one of those was looking for exactly this.

Two things generalise.

**Prose about a rule is not exempt from the rule.** The failure mode is specific and worth naming: quoting a value in order to explain why it is sensitive. It reads as legitimate while writing, because the sentence is *about* protecting the value — and the scan cannot tell the difference, because the value is identical either way.

**An audit that only re-runs the checks already run finds nothing new.** The scan that caught this is the same one that passed earlier in the session; what changed was running it over the whole repository again at the end, rather than over the diff in front of me. Checking the thing just written is not the same as checking what the repository now contains.

The working tree was corrected immediately. **The history was deliberately left alone**, decided on being shown the cost and the alternative: the value is an RFC 1918 private address from the commonest home range, it identifies nothing about anyone, and a second rewrite would have meant disabling the branch protection enabled hours earlier, re-pointing the quoted SHAs a second time, and re-syncing the Pi — for no gain in what anyone could learn. Recorded so this is not later mistaken for something nobody noticed.

Note the shape of that judgement, because it sits in tension with the rule being categorical. The rule stays categorical *for writing*: the point of not weighing individual values is that the weighing is what erodes it. Whether an already-published value justifies a second history rewrite is a different question, with real costs on the other side, and is decided case by case.

Both lessons went to the `agentic` proposals rather than staying here — the write-time trap into the prevention rule, and the whole-repository re-scan into the review skill.

## 2026-08-13 — Full security review of the repository

Whole repository and whole history, not a branch diff — the built-in `security-review` is diff-scoped and returns nothing against a clean tree, which is what it did. Recorded here because the findings were otherwise only ever stated in conversation.

**No high or medium severity vulnerabilities.** Everything the "Security posture" section above commits to was checked as *implemented* rather than taken on trust: parameterised SQL throughout, argv lists with no `shell=True`, `yaml.safe_load` only, Flask debug off (and `/console` returning 404 on the running Pi), and the systemd sandboxing confirmed in force via `systemctl show` rather than by reading the unit files.

Two controls were tested rather than reasoned about. The one reflected-input path — `minutes` echoed into a `BadRequest` — was probed live with `<script>` and `<img onerror>` payloads and is escaped by Werkzeug. The vendored ECharts bundle's SHA-256 was recomputed and matches the value recorded beside it.

Three low findings, all now closed or scheduled:

- **DNS rebinding can read the telemetry.** The app accepts any `Host`. Low only because every route is a `GET` — confirmed by POSTing each one and getting 405. Scheduled at M6, with the sequencing requirement now written into the security posture above.
- **A real routable address used as a documentation example** in `deploy.sh`. Fixed.
- **M6 cannot write the config file it is required to write.** Not currently exploitable; recorded above because the tempting fix is the insecure one.

**One accepted deviation, stated so it is not mistaken for an oversight.** The development container authenticates every repository through a single classic token with full `repo` scope, which can push to `main` and can also switch off the branch protection meant to contain it. A fine-grained token scoped to named repositories, without administration rights and with an expiry, is the tighter arrangement and is the standard now drafted for `agentic`. Not changed here: the container is a personal machine with passwordless `sudo`, so anyone holding it has other routes. Worth doing, not urgent.

**What the review missed on its first pass is the more useful finding**, and is recorded in the entry below: it scanned for the shapes of secrets and found none, while ordinary personal data sat in this log in plain prose.

## 2026-08-13 — ISP, location and throughput figures redacted from this log

A security review of the whole repository and its history found no credentials, no keys and no addresses — the scan was clean on every pattern it looked for. What it missed on the first pass, and found only when the public/private decision forced the question, was **semantic** rather than pattern-shaped: three entries in this log named the ISP, the speed test server's city, and the measured line speeds.

That is more identifying than the LAN address deliberately kept out of the repo on 2026-08-12. A private address means nothing to anyone outside this house; "this ISP, this city, this line speed", attached to a named GitHub account, is ordinary personal data. The rule adopted the previous day was written as "no machine specifics", and the honest reading of its intent covers this too.

Redacted here rather than left with a marker, but **not** silently: the technical claim each entry was making is preserved in full, because none of it depended on the figures. "A real run produced plausible download, upload and latency figures with the ISP field populated" carries exactly the evidence the original sentence carried — that the parser works against real output — and carries none of the personal detail. Nothing about what was verified has changed.

This is the one admitted exception to this log being append-only. The convention exists so that what was believed at the time cannot be quietly rewritten; redacting personal data is a different act from revising a claim, and it is recorded here rather than done invisibly.

**Standing rule, generalised from this:** a repository must contain no data identifying a machine, a network, a *person*, or a location. Addresses, key fingerprints, hostnames and MAC addresses were the original list; ISP, geographic location and measured line characteristics belong on it. Real measurements go in the database, which is not tracked — never into documentation.

**Two things this does not fix.** The figures remain in the git history blobs, so redacting the working tree is cosmetic while that history exists; with the repository going public, that history goes public too. And they were already published, since the repository was public when those entries were pushed — realistically to nobody, but that cannot be asserted. Both are open questions recorded in `plan.md` rather than decided here.

## 2026-08-13 — History rewritten to remove the redacted figures

The first of the two open questions above is closed: the history was rewritten with `git-filter-repo --replace-text`, replacing the four passages with the same wording the working tree already carried. Every blob in the repository was re-scanned afterwards — 8 occurrences before, 0 after — and all 49 commits survived, none dropped.

Done before the repository is made public, which was the whole point of the timing. A rewrite is cheap here (one branch, no tags, no forks, one collaborator, two clones) and gets steadily more expensive once a public repository starts accumulating clones nobody can reach.

**A prediction made while explaining the change was wrong, and the error is worth keeping.** Two commit SHAs are quoted in the docs as recovery pointers, and the claim was that both would survive because they predate the earliest affected commit. Both broke. `git-filter-repo` rewrites the *entire* history it is given rather than only the commits from the first change onward, so every SHA in the repository changed, not just the twelve downstream ones. The references were repointed and both now resolve.

The generalisable form: **a history rewrite invalidates every hash in the repository, not just the ones after the edit.** The mental model of "downstream commits change because their parents changed" is right about the mechanism and wrong about the blast radius. This has been corrected in the guidance drafted for `agentic`, where it had been written down in the wrong form.

**What this does not do**, restated because it stays true: the figures were public when originally pushed and rewriting history does not reach anyone who cloned or cached them then.

The pre-rewrite state was bundled to a local scratch file before starting, and is not in the repository.

## 2026-08-13 — Public again, protected, and `update.sh` finally run

**Access was verified before the switch, not after.** One collaborator with push rights, no outside collaborators, no pending invitations, no deploy keys, no forks, and a personal owner so no team grants. One thing could *not* be checked: installed GitHub Apps, whose endpoints refuse an ordinary user token. Recorded as unverified rather than passed.

The repository was then made public and **branch protection enabled immediately** — force-push blocked, deletion blocked, and applied to administrators, which is the setting that makes it mean anything on a repository whose only collaborator is an admin. There is no CI to require, so those two blocks are the whole of it. Making it public also enabled secret scanning and push protection automatically, both unavailable while private; zero alerts.

An **anonymous clone** was taken as the final check on the rewrite, since that is literally the public view: no ISP, no location, no throughput figures anywhere in the history.

**`update.sh` ran for the first time and failed twice, both real defects.** It was the only M2 script never exercised, and it produced roughly the same yield per run as `bootstrap.sh` did at G1.

*A bare `git pull` needs upstream tracking, and there wasn't any.* The tracking had been lost as a side effect of the history rewrite — `git-filter-repo` removes the origin remote by design, and re-adding the remote does not restore `branch.main.merge`. The Pi inherited that when its `.git` was resynced. The remote and branch are now named explicitly, so the script no longer depends on local config that can vanish without anyone editing it. The failure also *read* like a git problem, several lines of advice about setting an upstream, which is the wrong signal in the middle of a deploy.

*Then it aborted on a working tree `deploy.sh` had dirtied.* "Your local changes would be overwritten by merge — please commit or stash." But this is the designed sequence, not an edge case: `deploy.sh` exists to push uncommitted work for testing and `update.sh` exists to return to committed code. Git's advice is also wrong on a deploy target, where nothing is authored and a modified tracked file is always a leftover. `update.sh` now discards tracked modifications before pulling and lists every file it throws away; untracked files are left alone, since they do not block a fast-forward and are not ours to delete.

Both were verified by reproducing them on hardware — the second by dirtying the tree exactly as `deploy.sh` would, watching the old behaviour abort, and watching the new behaviour list the artifact and carry on.

That closes the last "never run" item from M2. Of the four M2 deliverables, every one has now failed at least once on contact with real hardware, and none of those failures was visible to review or to the test suite.

Next: M4 — restarts and the reboot mechanism.

## 2026-08-16 — M4 built: restarts, and a reboot mechanism that had to be redesigned

Requirement 6 in full, plus requirement 5's last clause carried from M3. 266 tests, green. **Nothing here has run on hardware**; G3 is the check, and its list in `plan.md` has been rewritten to say what to look at.

**The recorded reboot mechanism could not work, and one line showed it.**

```
$ setpriv --no-new-privs /usr/bin/sudo -n /bin/true
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
```

`plan.md` committed to two things that contradict each other: `NoNewPrivileges=yes` on every unit, and a reboot triggered by `sudo systemctl start bbmon-reboot.service`. `NoNewPrivileges` makes the kernel ignore sudo's setuid bit, so the sudoers rule could never have fired. Both decisions were taken in the same document on the same day, and neither review nor the test suite had anything to say about the pair. G3 would have found it, at the cost of a home visit.

The choice it forced was between the two. Keeping sudo meant dropping `NoNewPrivileges` from the pinger — the one service that feeds user-editable ping targets into a subprocess, and the worst place in the system to hand back access to every setuid binary on the machine. Keeping the sandbox meant giving up the "one fixed sudo command" shape.

**What replaced it.** The unprivileged service writes `/var/lib/bbmon/reboot-now`; `bbmon-reboot.path` notices the write; systemd starts `bbmon-reboot.service`, which is root, runs two lines and none of bbmon's code. No bbmon process is privileged, none gains a privilege, and there is no sudoers file at all. The grant is narrower than the one it replaces rather than wider: sudo would have taken an argv, whereas the only thing bbmon can communicate here is *that* a file was written.

**The failure mode that shaped the design is a Pi that reboots on sight of its own trigger.** It is unrecoverable without a keyboard at the machine, so it has three independent guards: `PathModified=` fires on a write rather than on the file being present, `bbmon-reboot.service` deletes the trigger before rebooting rather than after, and `bbmon-init` deletes any leftover on the way up before the watcher is ordered to start. The watcher also `Requires=bbmon-init.service`, so a Pi with a broken config cannot reboot itself. Both new units pass `systemd-analyze verify`, which is syntax, not behaviour.

**Two files, not one, and the reason is that consumption has to be provable.** `reboot-requested` holds the reason and is read at the next startup; `reboot-now` is the trigger and is deleted immediately. The first draft used a single row in `restarts` as a pre-reboot marker, and it was wrong in a way worth recording: after one planned reboot, that row stays the newest `expected = true` row for ever, so the *next* power cut reads as planned too. Any scheme where the marker is not consumed has this bug. Writing exactly one row per boot, at boot, from a request file that is deleted as it is read, is what makes "was this restart ours?" answerable more than once.

**A restart is timestamped when it was noticed**, not when the machine went down — the only honest option, since an unexpected restart is not observed while it happens.

**Reboot due-ness is measured from uptime, not from the last restart row.** A power cut resets that clock as surely as a planned reboot does, and a Pi that came up ten minutes ago does not need rebooting whatever the table says. It also means the speed test service can answer requirement 5's "is a reboot imminent?" for itself, from the same configured interval and the same `/proc/uptime`, with no shared state and no message between the two processes.

**The reboot check rides the ping loop** through a new `between_cycles` hook, rather than owning a systemd timer. Same reasoning `plan.md` already applied to the retention purge: one fewer unit, and a timer's schedule would have to be baked into a unit file instead of read from `reboot.interval_days`. The hook runs after the flush, so taking the machine down cannot lose buffered pings. M6's purge slots into the same hook.

**A request that produces no reboot is now noticed.** Writing a file that nobody is watching succeeds exactly like one that works, so if `bbmon-reboot.path` is missing or inactive the only symptom would be a Pi that quietly never reboots. The scheduler therefore records when it asked, and if the machine is still up ten minutes later it warns and asks again. That is the same shape as the defects G1 found: the thing reports success while not working.

**Requirement 5's skip needed no interface change**, which the M3 entry above doubted. The collector takes a `reboot_imminent` predicate and returns an empty sequence — already a legal return from `collect`. Nothing is recorded for a skipped test: it is not a failure, and a test killed by a reboot would put an outage on the chart where there was none.

**The test suite was reading the development machine's uptime.** Adding the schedule made an existing end-to-end speed test fail, because the Chromebook had been up 3.8 days against a 3-day reboot interval — the test would have passed on a laptop rebooted that morning. `tests/conftest.py` now points `/proc/uptime` and the timesyncd runtime directory at fixtures for the whole suite. Worth recording because the test was not wrong when it was written; the code moved underneath it, and the failure was a real one rather than a hypothetical.

**NTP.** The wait uses systemd-timesyncd's own marker, `/run/systemd/timesync/synchronized` — what `systemd-time-wait-sync` waits on — rather than inferring sync from anything. Only `bbmon-init` waits, because every other unit is ordered after it. `bbmon-init` is now also ordered after `systemd-timesyncd.service`: starting before timesyncd has created its runtime directory would make the wait read "nothing manages this clock" and skip, silently, exactly when it was most needed. The wait is bounded at 120s and says so when it gives up, because monitoring that never starts is worse than one questionable timestamp.

**The join between the config and the unit files is checked, because nothing else could check it.** The units name `/var/lib/bbmon/reboot-now` literally; the code derives it from `database.path`. Move that setting and the trigger moves out from under the watcher — and the symptom is a Pi that quietly never reboots, the exact failure the path unit's design is otherwise weakest against. The pinger now refuses to start when the real reboot action is asked for and the two disagree, saying which path it would have written and which one is watched. Deliberately a refusal rather than a warning: a service that will not start is reported by systemd and by `deploy.sh`, while a warning in the journal is read by nobody. It lands on M6 as well — the admin page can set `database.path`, so its validation has to enforce the same rule.

**The `PathModified=` assumption was tested rather than trusted.** The whole boot-loop argument rests on a claim about systemd, so a throwaway user-level pair of units — same directives, a harmless `ExecStart` — was run on Crostini's systemd 252:

- trigger file already present when the watcher starts: **did not fire** (this is the boot-loop case);
- a write to that same existing file: **fired**;
- `ExecStartPre` deleted the trigger and the watcher re-armed: a second write fired again.

That is the mechanism, and it behaves as designed. What it is not is a test of bbmon's actual units, which name root-owned paths and were only checked with `systemd-analyze verify`; and Raspberry Pi OS trixie ships a later systemd than 252.

**Not verified.** No reboot has been performed by this code, on any machine. The boot-loop guards are three belts and an argument, with only the systemd behaviour underneath them measured. The 120s NTP timeout has never met a Pi with no RTC. The rest was run for real on Crostini: `bbmon-init` recorded an unexpected restart on a fresh database, added nothing on a second run in the same boot, and recorded `expected = true` with the right reason when a request file was left behind; the pinger — on a Chromebook 3.8 days up against a 3-day interval — asked for a reboot once, wrote the reason, and wrote the trigger only when `BBMON_REBOOT=systemd` was set.

Next: G3 — the reboot gate, which can be cleared on the same visit as G2. After that, M5.
