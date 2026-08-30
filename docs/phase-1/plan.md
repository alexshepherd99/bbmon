# Phase 1 — Plan

Replaces the milestone outline drafted 2026-08-03 (recoverable at commit `4c5f866`), which assumed no Raspberry Pi until the final milestone. A Pi is available from the outset, so the plan now runs the hardware in parallel with development rather than at the end.

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

**M4 — Restarts and the reboot mechanism.** *(dev + gate G3)* **Done (dev) 2026-08-16.** `restarts` rows, expected/unexpected detection on startup, NTP-sync wait before the first write. The reboot action sits behind an abstraction with a no-op implementation for dev; the real implementation is the path unit described below. Also picked up requirement 5's "skip a speed test if a reboot is imminently due", carried from M3. *Depends on: M1.*

**M5 — Full dashboard.** *(dev)* **Done (dev) 2026-08-17.** The remaining two charts (hourly box plot over 1 day; speedtest history with selectable range), the pre-aggregation and short-lived server-side cache the charts read through, the restart list with its expected-restart toggle, the version/build footer, and the mobile layout. Also closed the two display defects deferred from 2026-08-09 and 2026-08-12. *Depends on: M3, M4.*

**M6 — Admin page.** *(dev + gate G5)* Config form with server-side validation and atomic write-back, SIGHUP reload, force-reboot button, CSV export of ping and speedtest data over a selectable date range, and the daily ping-retention purge. *Depends on: M5.*

**M7 — Operability and release.** *(dev + gate G4)* Log rotation, the security checklist verified end to end, low-spec behaviour measured on the Pi for the first time, and `update.sh` proven from a clean pull. *Depends on: everything above.*

## Pi gates

**G1 — first deploy** *(after M2)*. **Cleared 2026-08-12**, bar the phone check, which moves to G4 — see `log.md`. **Re-run end to end on 2026-08-19** after the Pi was rebuilt, and passed again on a 64-bit image against the M4/M5 code the original run predates. `bootstrap.sh` completes on a clean Pi; services come up under systemd as `bbmon`; dashboard reachable from a phone on the LAN; `deploy.sh` round-trip works; key-only SSH confirmed working, and password authentication confirmed refused from a fresh session. Unprivileged ICMP is now a specific check rather than a general one: `NoNewPrivileges=yes` makes the kernel ignore `ping`'s `cap_net_raw` file capability, so what must be confirmed is that `bootstrap.sh`'s `net.ipv4.ping_group_range` drop-in took effect and that the pinger records latency rather than "Operation not permitted". Also confirm the Ookla CLI's `aarch64` (or `armhf`, if the OS turns out to be 32-bit) checksum matched, and that it tolerates `ProtectHome=yes`.

**G2 — real measurements** *(after M3)*. **Cleared 2026-08-19.** Speed tests produce plausible, repeatable figures with the ISP and server fields populated; ping latency during a concurrent speed test rises on all three targets together for about ten seconds and returns to baseline, with no failed pings; SD-card write volume from the buffered flush measured per-process at 48 KiB net per 60s flush, around 69 MiB a day. The periodic-spike open item was answered here too — see `log.md`.

**G3 — reboot** *(after M4)*. **Cleared 2026-08-19**, and it found the defect it existed to find: an ordering cycle between `bbmon-reboot.path` and `bbmon-init.service` that made systemd delete the init job at boot, leaving the whole of bbmon dead on a machine that had just rebooted itself on schedule. Fixed with `DefaultDependencies=no` on the watcher; see `log.md` and the unit file's own comment. Every item below then passed against the fixed units.

- `bbmon-reboot.path` is active after `bootstrap.sh`, and `bbmon-reboot.service` is installed but **not** enabled — systemd reports it `static`, so it cannot be enabled even by accident.
- Writing `/var/lib/bbmon/reboot-now` as the `bbmon` user reboots the Pi, and the next boot records `expected = true` with the scheduled reason.
- The Pi comes back **once**. A leftover trigger does not start a second reboot — the loop guard, and the reason this gate is worth clearing before the Pi is left running unattended.
- A pulled power cable is recorded as `expected = false` on the next boot. `PRAGMA integrity_check` returned `ok` afterwards, which is the WAL settings from M1 doing their job.
- Restarting a single service (`systemctl restart bbmon-pinger`) adds no restart row.
- The NTP wait behaves on a Pi with no RTC: `/run/systemd/timesync/synchronized` appears and the wait ends, rather than timing out at 120s. Measured at 37s, 39s and 55s across three boots.
- Confirm the pinger keeps `NoNewPrivileges=yes` and still reboots — the whole point of the path unit.

**G5 — the config helper and the admin page** *(after M6)*. Added 2026-08-30. M6 was planned as dev-only, on the assumption it installed no units; the privileged config helper installs two, so it has Pi work that would otherwise have gone unrecorded. **Unit files changed, so this deploy needs `bootstrap.sh`, not just `deploy.sh`** — G3's lesson was that a unit-file change delivered by `deploy.sh` alone is never installed.

**A security review runs before this deploy**, in its own session — asked for on 2026-08-30. M6 is the milestone that gave an unauthenticated LAN page the ability to change the configuration and reboot the machine, so the review belongs before that reaches hardware rather than after.

- **The Pi still boots with every unit running.** The first thing to check and the reason this gate exists: a second `.path` unit is the exact shape that produced G3's ordering cycle, where systemd resolved the deadlock by deleting the init job and nothing bbmon started at all. It is written with `DefaultDependencies=no` from the outset, but that is a reason to expect a pass, not evidence of one — and G3 was intermittent, so **reboot more than once**. `systemd-analyze verify` does not detect ordering cycles; only a real boot does.
- `bbmon-config.path` is active after `bootstrap.sh`, and `bbmon-config.service` is installed but **not** enabled — `systemctl` should report it `static`.
- Writing a valid proposal to `/var/lib/bbmon/config-staged.yaml` **as the `bbmon` user** installs it: `/etc/bbmon/config.yaml` changes, keeps `root:bbmon 0640`, and the proposal is gone afterwards. This is the whole mechanism, and it has never run as real root against a real `/etc`.
- A proposal that is a symlink, one owned by another user, and one that is not a valid configuration are each refused, with `/etc/bbmon/config.yaml` unchanged. The symlink case is the escalation the helper exists to refuse, so point it at a root-only file and confirm nothing was copied.
- The sandboxing does not stop it doing its job: `ProtectSystem=strict` with `ReadWritePaths=` naming only `/etc/bbmon` and `/var/lib/bbmon` is untested against a real filesystem, and getting it wrong fails closed — the install silently does nothing.
- **`/var/lib/bbmon` is still owned by `bbmon` after this unit has run.** The `StateDirectory=` trap: if that directive is ever added to this unit, systemd chowns the directory to root and every collector loses its database. Worth confirming once by inspection rather than trusting the test that forbids it.
- The dashboard is still reachable from a phone by address, with the Host allowlist now in force. If the Pi is reached by a **name** rather than an address, that name needs adding to `web.allowed_hosts` — expected, and the point at which the setting earns itself.
- **A save from the admin page reaches the file.** The round trip from the form, through the staged proposal, to an installed `/etc/bbmon/config.yaml` has never happened anywhere: development has no root helper, so a proposal sits in the state directory unread. Change one harmless setting — `web.restart_limit` — and confirm the file changed, the proposal is gone, and reloading the page shows the new value. That last part is the whole design working: the page reads the file, so it can only show what root actually installed.
- **A save the helper refuses leaves the page honest.** Stage a proposal by hand that the form would not produce (a `database.path` outside `/var/lib/bbmon`) and confirm `/etc/bbmon/config.yaml` is unchanged and the journal says why. The page will still have said "proposed", which is why it does not say "saved".
- The admin page is usable on a phone: the number boxes do not zoom the page when focused, and the target list is typeable. The dashboard's link to it and its link back are both fingertip-sized. This is the M5 mobile check repeated for the one page M5 did not have.

**G4 — soak** *(after M7)*. Log rotation observed; full security checklist. The retention purge is **deployed as of 2026-08-28** and runs daily, but with retention left at 30 days and the oldest ping dated 2026-08-19, the first run that deletes anything falls due around **2026-09-18** — it stays silent until then, so an absence of log lines before that date is the purge working, not the purge missing. `update.sh` from a clean git pull was **done 2026-08-19** — its first ever run, which delivered the G3 ordering-cycle fix. **The mobile layout was confirmed on a real phone on 2026-08-28**, closing the item deferred from G1.

CPU under normal load is **measured**: about 0.7% of one core for the pinger, averaged over three days. **Memory is read from `/proc/<pid>/status`, not from systemd** — decided 2026-08-30, when it was needed for the CSV export and worked on a live service with no unit edit and no restart. `MemoryAccounting=yes` is therefore not being added; `MemoryCurrent` stays `[not set]`. `bbmon-web` idles at about 38 MB and does not grow while streaming an export. The pinger and speed test service still need the same reading.

M5 added two items that cannot be settled off the Pi, both now closed:

- **The hourly box plot query timed on the Pi 3.** **Done 2026-08-28: 2.0 s** for the 24-hour window, 50,115 rows, against ~400ms for 56k rows on the x86 development container — 5× slower. The index bounds the scan to the window, so the cost is linear in the window (~40 µs a row) and flat in table size; a growing `ping_results` does not make it worse, and M6's retention purge will not make it better. Accepted for phase 1 without change: the 300s cache means one viewer in five minutes waits two seconds for that panel and the rest get 8 ms. See `log.md` for the query plan and the scaling figures.
- **The build stamp written by both scripts.** **Done 2026-08-19.** `deploy.sh` wrote it during the rebuild, and `update.sh` wrote it on its first run; the footer rendered each in turn, naming the right short SHA and no `+local` on a clean tree.

And two items this gate gained on 2026-08-19:

- **Re-run G3's reboot checklist end to end, cleanly.** The original run was muddled: a script was run again by accident partway through, which for a while looked like the defect and was not it. Every item has since passed against the fixed units, so this is confirmation rather than an open question — but it is the one gate whose failure mode needs a keyboard at the machine, so it deserves an unambiguous pass. **Run one command at a time and confirm each before starting the next**, rather than queueing them; the reboots are fast enough that an accidental repeat lands inside the previous one and the evidence then cannot be untangled — the journal does not survive a reboot to help.

  **Two of these items no longer need staging: the machine did them by itself.** Left running from 2026-08-19 to 2026-08-28, it took its scheduled reboot twice — 2026-08-22 and 2026-08-25 — each recorded `expected = true` with the scheduled reason, each followed by the machine coming back and resuming collection. That is the scheduled-reboot path and the restart row proven unsupervised, which is a stronger claim than a staged run. What still needs deliberate action is everything that cannot happen on its own: the loop guard, the pulled-power `expected = false` case, the single-service restart adding no row, and the reboot from inside the sandbox under `NoNewPrivileges=yes`.
- **Decide what to do about the volatile journal.** Raspberry Pi OS ships `/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf` with `Storage=volatile`, so **no log survives any reboot**. That is a sensible default for an SD card and it makes "log rotation observed" a much smaller item than it looked — but it also means that after an unexpected restart, the `restarts` row is the only evidence that anything happened, with nothing to say why. Found at G3, where it is what stopped the first failure being diagnosable at all.

## Decisions taken with this plan

The four items under "Open decisions" in `requirements.md`, plus the ones this plan's shape forces. All are closed here unless marked otherwise.

- **Reboot trigger** — an unprivileged service writes `/var/lib/bbmon/reboot-now`; `bbmon-reboot.path` notices the write and systemd starts `bbmon-reboot.service`, a root unit that reboots and runs none of bbmon's code. *(Revised at M4. This was recorded as a sudoers rule scoped to `systemctl start bbmon-reboot.service`, which cannot work: `NoNewPrivileges=yes` on every unit makes the kernel ignore sudo's setuid bit, so sudo refuses to run. Keeping sudo would have meant dropping that directive from the pinger. See `log.md`.)*

  The watcher must keep `DefaultDependencies=no`. Without it a path unit is implicitly ordered `Before=paths.target`, which closes an ordering cycle back through `bbmon-init.service`, and systemd resolves that by deleting a job — at G3 it chose the init job, and nothing bbmon started at all. This is not a tidying detail: it is load-bearing, and `tests/test_systemd_units.py` holds it in place.
- **Web port and route structure** — one Flask app on port 8080; dashboard at `/`, admin at `/admin`. A second app would double the surface for no benefit.
- **Purge job placement** — inside the pinger service's existing loop as a daily check, not a separate systemd timer. One fewer unit to install, secure and reason about.
- **Default ping targets** — ship `8.8.8.8`, `1.1.1.1`, `google.com` as documented. It is a config default, editable from the admin page, so it is not worth further deliberation. (Adding the router as a target is `BACKLOG.md` item 1, not phase 1.)
- **Ping implementation** — shell out to the system `ping` binary with an argv list (never `shell=True`), rather than raw sockets. The system binary is already setuid/setcap, so it works unprivileged on both Crostini and the Pi, and the service needs no `CAP_NET_RAW` grant.
- **Pre-aggregation applies to the hourly box plot only, and never to the live latency chart.** Agreed 2026-08-17. Requirement 7 asks chart queries to use pre-aggregated data rather than raw rows; that is read as "where the volume warrants it", not as a rule for all three charts.

  **The governing constraint is that latency spikes must stay visible.** They are a primary signal — a spike is the thing worth seeing — and averaging is exactly what would hide one: at a five-second interval, per-minute buckets fold twelve samples into one number and a single bad ping disappears into eleven good ones. So the live 2-hour chart reads raw rows, and no averaging is to be introduced into it later as a performance measure. If that chart ever needs to get cheaper, the levers are the window, the cache and client-side downsampling that preserves extremes — not the mean.

  The box plot is where aggregation belongs, and it keeps the spikes too: min and max are two of the five values it draws, so the whiskers show the worst ping in the hour rather than smoothing it away. The speed test history reads raw rows simply because 30 days is a few hundred of them.

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
- **The admin account's passwordless sudo is an enumerated list, not a blanket grant.** `deploy.sh` runs over a non-interactive SSH session, so it needs privilege with no terminal to prompt on. `bootstrap.sh` installs `/etc/sudoers.d/bbmon-deploy` permitting exactly the three service restarts that script can ask for and the one build-stamp write. `bbmon-init` and `bbmon-reboot` are outside it deliberately: the latter is the reboot trigger, and M6 puts a reboot button on an unauthenticated web app. The rule is validated with `visudo -cqf` before installation, because a malformed drop-in disables sudo machine-wide and a headless Pi reached only over SSH cannot be repaired from there. *(M2)* — this replaced an unexamined assumption rather than adding a control: the pre-2026-08-19 image gave the admin account blanket passwordless sudo, `deploy.sh` silently depended on it, and the rebuild is what surfaced that. *(2026-08-19)*
- **No long-running bbmon process is privileged, and none can gain a privilege.** The reboot is performed by a root unit that bbmon can start but cannot influence: the only input is that a watched file was written, so there is no argument, path or option for a web request to reach. Done at M4, and it is what let `NoNewPrivileges=yes` stay on every unit. *(M4)*

  **M6 added a second root unit, and it is the weaker of the two.** `bbmon-config.service` installs a configuration the web app proposed, and unlike the reboot unit it runs bbmon's own Python as root — unavoidably, because refusing a bad proposal means parsing it. Both are started the same way and take no argument, so neither can be *instructed*; what differs is how much code runs on the privileged side. That is why this one is sandboxed where the reboot unit is not: `NoNewPrivileges=yes`, `ProtectSystem=strict`, an empty `CapabilityBoundingSet`, and `ReadWritePaths=` naming only `/etc/bbmon` and `/var/lib/bbmon`. It deliberately does **not** take `StateDirectory=bbmon` like every other unit: with no `User=`, systemd would create `/var/lib/bbmon` as `root:root` and every actual service would lose write access to its own database. *(M6)*
- **The trigger the code writes and the trigger the unit watches are checked against each other at startup.** The units name the path literally; the code derives it from `database.path`. Move that setting and the pinger refuses to start rather than asking for reboots into thin air. **M6's config form must validate `database.path` against the same rule**, since that form can set it. *(M4, M6)*
- **The reboot trigger cannot loop the Pi.** Three guards, because the failure is unrecoverable without a keyboard at the machine: `PathModified=` fires on a write rather than on the file existing, `bbmon-reboot.service` deletes the trigger before rebooting, and `bbmon-init` deletes any leftover before the watcher is allowed to start. *(M4)*
- **CSRF tokens on every state-changing request** (config save, force reboot). Without authentication there is no session to protect, but there is still an action to protect: any page in a LAN browser can otherwise POST a reboot. *(M6)*
- **Host-header allowlist**, rejecting requests whose `Host` is not an expected name or IP. This is what stops DNS rebinding turning a random website into a client of the admin page. *(M6)* — **and it must land in the same commit as the first POST route, not merely the same milestone.** Confirmed live on 2026-08-13 that the app serves any `Host`; today that only exposes read-only telemetry, because every route is a `GET`. The first state-changing route turns the same weakness into a remote reboot trigger, so the two cannot be separated even by a day.
- **Config written atomically** (temp file plus rename) with restrictive permissions, and only to the one fixed configured path — no user-supplied path reaches the filesystem. *(M6)*
- **M6 must not gain that write access by loosening file permissions.** `bootstrap.sh` installs `/etc/bbmon/config.yaml` as `root:bbmon 0640`, and the `bbmon` user cannot write it — confirmed on the Pi at G1. The obvious fix, chowning it to `bbmon` or going `0660`, would hand an unauthenticated LAN-reachable web process write access to a root-owned file in `/etc`, on the same service that M6 also gives a reboot button. The intended route is a narrow privileged helper the web app can ask but cannot instruct, the same shape M4's reboot ended up with. Decided now because it is far cheaper to design in than to retrofit. *(M6)*

  **Built 2026-08-30 as `bbmon-config.path` plus `bbmon-config.service`.** The web app writes a proposal to `/var/lib/bbmon/config-staged.yaml`; systemd starts the root unit, which refuses it unless it is a regular file (never a symlink — `O_NOFOLLOW`, or naming `/etc/shadow` would copy it into a file the service group can read), owned by `bbmon`, valid as a configuration, and not moving `database.path` out from under `bbmon-reboot.path`. The proposal is consumed whether accepted or refused. Root revalidates rather than trusting the form, which is what keeps a bug in the web app from leaving every service unable to start.

Deferred to `BACKLOG.md` as hardening rather than hole-closing: a host firewall restricting port 8080 to the LAN subnet, request rate limiting, and admin-page authentication.

**Standing check, every milestone:** if a change adds a way for input to reach a subprocess, the filesystem, or SQL, it gets called out at review time rather than found at G4.

## Open items

- **The figures were already published.** The repository was public when those entries were pushed, so exposure has to be assumed rather than argued away, and rewriting history does not undo it. Realistically the audience was nobody, but that is not a claim that can be made with evidence. No action beyond not repeating it; recorded so it is not later mistaken for a clean record.
- **Collector interface shape** is settled. M3's speed test used it unchanged; what needed adjusting was the code around it, not the interface itself — see the M3 entry in `log.md`. Three strains are recorded there and none justified a change yet.
- **A real browser has now loaded the page**, on 2026-08-12 from the Chromebook, with all three services running and real data — the latency chart and the speed test panel both drew correctly. That closes the canvas renderer and the poll timer, which the jsdom harness explicitly cannot cover, and it found the redraw-animation defect since fixed at M5. The CSS layout on a **phone** was confirmed on 2026-08-28 and is no longer open; requirement 7's mobile layout is met.
- **The Pi is on WiFi, not Ethernet**, and that is the shape of its latency data rather than a fault in it: a median around 17ms with a p99 in the hundreds, spikes on roughly a third of cycles, and occasional seconds where the host is unreachable altogether. Measured at G2. It raises the value of two `BACKLOG.md` items considerably — pinging the router separates "the WiFi hiccuped" from "the ISP is down", and WiFi signal logging stops being conditional on the Pi not being wired. Neither is phase 1.
- **The dashboard layout wants reworking for a desktop screen.** Feedback on M5's page, 2026-08-17, **deferred to a later session by agreement**. Three things asked for:
  - **Fit on one screen at 1920×1080 with no scrolling.**
  - **The two ping charts side by side** — the live 2-hour line chart and the hourly box plot.
  - **The restart panel moved up beside the speed test readings.**

  The current wide layout is one full-width row each for the readings, the live chart and the restarts, with the box plot and speed test history paired. So this is a rearrangement into roughly: readings | restarts, then live chart | box plot.

  Two things need deciding when it is picked up, because the request does not settle them. **Where the speed test history chart goes** — it is the panel with no place left in the arrangement above, and putting it on a third row is what the no-scrolling constraint will fight. And **how the chart heights are derived**: they are `42vh` with a `min-height: 240px` floor today, and two rows of charts at that height plus a header, a readings panel, a restart table and a footer do not fit in 1080px. The floor in particular will override any vh figure small enough to fit.

  Note this constrains **wide viewports only**. Requirement 7's mobile layout still has to stack and scroll, so "no scrolling" is a `min-width` rule, not a property of the page.

- **The build stamp can lie if a deploy script fails between copying files and writing it.** Accepted knowingly at M5 when the mechanism was chosen. `set -e` and the write ordering keep the window narrow, and the alternative — a digest derived from the deployed bytes — was rejected as harder to read for a gain only visible in a case the scripts already abort on. No action; recorded so it is not later mistaken for an oversight.
