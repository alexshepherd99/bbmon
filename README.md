# bbmon

Home broadband monitoring using a Raspberry Pi.

A headless Python application that runs on a Raspberry Pi and starts on boot, continuously monitoring home broadband performance — latency, throughput, and connection reliability — and serving the results on a local web dashboard. Self-reboots on a schedule and logs both expected and unexpected restarts. Built to be extended with further periodic tests over time.

LAN-only, no authentication, designed to run on low-end Pi hardware.

## Status

In development — phase 1. The pinger and speed test record to SQLite and the
dashboard charts them live (M1, M3), the whole system runs on the Pi under
systemd via the bootstrap and deploy scripts (M2, gate G1 cleared 2026-08-12),
and it now logs its own restarts and reboots itself on a schedule (M4). Next
are gates G2 and G3 on the hardware, then M5, the full dashboard.

## Deploying to a Pi

Set the Pi up once, then use one of the two deploy paths. See
[`docs/pi-access.md`](docs/pi-access.md) for SSH key setup first.

```sh
# On the Pi, once. Raspberry Pi OS Lite ships without git, so it comes first;
# bootstrap.sh installs it again anyway, because update.sh needs a checkout.
sudo apt install -y git
sudo git clone https://github.com/alexshepherd99/bbmon /opt/bbmon
sudo /opt/bbmon/scripts/bootstrap.sh

# From here, during development — pushes the working tree, no commit needed:
scripts/deploy.sh

# On the Pi, to update from committed code:
sudo /opt/bbmon/scripts/update.sh
```

Run it locally with two terminals:

```sh
BBMON_CONFIG=dev-config.yaml .venv/bin/python -m bbmon.pinger
BBMON_CONFIG=dev-config.yaml .venv/bin/python -m bbmon.web
```

A third service runs the speed test. It needs the Ookla Speedtest CLI on
`PATH`, which `bootstrap.sh` installs on the Pi but which is not part of the
development setup — without it the service logs that the binary is missing and
exits, and the dashboard's speed test panel simply stays empty:

```sh
BBMON_CONFIG=dev-config.yaml .venv/bin/python -m bbmon.speedtest
```

then open <http://127.0.0.1:8080>. Results are buffered, so the first points
appear about a minute after the pinger starts.

To view the dashboard from the ChromeOS browser under Crostini, set
`web.host` to `0.0.0.0` in `dev-config.yaml`, add `penguin.linux.test` to
`web.allowed_hosts` there, and open <http://penguin.linux.test:8080>. The
container is a separate VM, so its loopback is not the browser's; binding all
interfaces exposes the dashboard to the Chromebook only, as the Crostini
subnet is host-only. The dashboard answers to any address and to `localhost`
but refuses host names it has not been told about, so reaching it by name
takes that one setting — see the comment in `deploy/config.yaml`.

To check a chart change without a browser, `tools/render-dashboard.js` renders
the real dashboard headlessly and asserts it drew. It needs jsdom, installed
outside this repo on purpose — see the file's header.

## Development

Requires Python 3.11+. All development and unit testing happens on an ordinary
Linux machine; no Raspberry Pi is needed.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

Services read their settings from `/etc/bbmon/config.yaml`. In development,
point `BBMON_CONFIG` at a local file instead:

```sh
BBMON_CONFIG=dev-config.yaml .venv/bin/python -m bbmon.pinger
```

## Documentation

- [`docs/phase-1/`](docs/phase-1/) — the current effort: requirements, plan, and log.
- [`BACKLOG.md`](BACKLOG.md) — work not yet started.
