# bbmon

Home broadband monitoring using a Raspberry Pi.

A headless Python application that runs on a Raspberry Pi and starts on boot, continuously monitoring home broadband performance — latency, throughput, and connection reliability — and serving the results on a local web dashboard. Self-reboots on a schedule and logs both expected and unexpected restarts. Built to be extended with further periodic tests over time.

LAN-only, no authentication, designed to run on low-end Pi hardware.

## Status

Pre-implementation. Requirements are settled; no code yet.

## Documentation

- [`docs/phase-1/`](docs/phase-1/) — the current effort: requirements, plan, and log.
- [`BACKLOG.md`](BACKLOG.md) — work not yet started.
