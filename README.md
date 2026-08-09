# bbmon

Home broadband monitoring using a Raspberry Pi.

A headless Python application that runs on a Raspberry Pi and starts on boot, continuously monitoring home broadband performance — latency, throughput, and connection reliability — and serving the results on a local web dashboard. Self-reboots on a schedule and logs both expected and unexpected restarts. Built to be extended with further periodic tests over time.

LAN-only, no authentication, designed to run on low-end Pi hardware.

## Status

In development — phase 1. M1 (walking skeleton) is done: the pinger records
latency to SQLite and the dashboard charts it live. Next is M2, the Pi
bootstrap and deploy loop.

Run it locally with two terminals:

```sh
BBMON_CONFIG=dev-config.yaml .venv/bin/python -m bbmon.pinger
BBMON_CONFIG=dev-config.yaml .venv/bin/python -m bbmon.web
```

then open <http://127.0.0.1:8080>. Results are buffered, so the first points
appear about a minute after the pinger starts.

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
