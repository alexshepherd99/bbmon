"""Throughput measurement via the official Ookla Speedtest CLI.

The tool is a standalone binary rather than a Python library, so it is invoked
as a subprocess exactly as :mod:`bbmon.collectors.ping` invokes ``ping`` — no
shell, always an argv list. It contributes nothing to ``pyproject.toml``;
``bootstrap.sh`` installs it on the Pi.

``speedtest-cli``, the Python library the requirements originally named, was
archived in January 2026 and no longer works against Ookla's backend. See
``docs/phase-1/log.md`` for that decision.

Nothing user-editable reaches this command line: every argument is a constant
defined here. Ookla's JSON, by contrast, is external input and is treated as
untrusted — a shape we do not recognise becomes a recorded failure, never an
exception that stops the service.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from bbmon import db
from bbmon.collectors.base import Collector, CollectorError
from bbmon.models import SpeedtestResult

logger = logging.getLogger(__name__)

#: The binary installed by ``bootstrap.sh``, found on PATH.
BINARY = "speedtest"

#: A speed test normally takes 30–40s. This is the point at which we stop
#: waiting and record a failure, generous enough that a slow line is measured
#: rather than reported as broken.
TIMEOUT_SECONDS = 180

_SECONDS_PER_HOUR = 3600
_BITS_PER_BYTE = 8
_BITS_PER_MEGABIT = 1_000_000

Runner = Callable[..., subprocess.CompletedProcess]


def to_megabits_per_second(bytes_per_second: float) -> float:
    """Convert Ookla's ``bandwidth`` field to the unit an ISP advertises in.

    Ookla reports bytes per second. Reporting that number as megabits would
    understate the line by a factor of eight, so the conversion is done once,
    here, and never repeated downstream.
    """
    return bytes_per_second * _BITS_PER_BYTE / _BITS_PER_MEGABIT


class SpeedtestCollector(Collector):
    """Runs one speed test per cycle."""

    def __init__(
        self,
        interval_hours: int,
        runner: Runner | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        reboot_imminent: Callable[[], bool] = lambda: False,
    ) -> None:
        """
        :param interval_hours: How often the speed test runs, from configuration.
        :param runner: Injection point for the subprocess call, so tests need
            neither the network nor the Ookla binary. Defaults to
            ``subprocess.run``, looked up when the collector is built rather
            than when this module is imported, so the boundary stays reachable
            for a service assembled by :func:`bbmon.speedtest.main`.
        :param clock: Injection point for the current time.
        :param reboot_imminent: Whether the machine is about to reboot, for
            requirement 5's last clause. A predicate rather than a value
            because a cycle can be hours after the one before it. The default
            says no, which is right for any collector built without a reboot
            schedule to consult.
        """
        self._interval_hours = interval_hours
        self._runner = runner if runner is not None else subprocess.run
        self._clock = clock
        self._reboot_imminent = reboot_imminent

    @property
    def name(self) -> str:
        return "speedtest"

    @property
    def interval_seconds(self) -> int:
        """The configured interval in hours, as the seconds the loop sleeps for."""
        return self._interval_hours * _SECONDS_PER_HOUR

    def collect(self) -> list[SpeedtestResult]:
        """Run one speed test.

        Returns a single-element list because the collector interface is shaped
        around the ping case, where one cycle measures several targets — or an
        empty one when the run is skipped, which needed no interface change.

        :raises CollectorError: if the Ookla binary is not installed.
        """
        if self._reboot_imminent():
            # Requirement 5. Not recorded as a failure: nothing failed, and a
            # killed test would put an outage on the chart where there was
            # none. The service tests on startup, so a real measurement
            # follows a minute or two after the machine comes back.
            logger.info("Skipping the speed test: a reboot is due imminently")
            return []

        # Every element is a constant: no configuration value reaches this
        # command line, so there is nothing here to inject into.
        argv = [BINARY, "--format=json", "--accept-license", "--accept-gdpr"]

        try:
            completed = self._runner(
                argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS
            )
        except FileNotFoundError as error:
            logger.error("The speedtest binary is not available: %s", error)
            raise CollectorError(f"The speedtest binary is not available: {error}")
        except subprocess.TimeoutExpired:
            logger.warning(
                "The speed test did not finish within %ds", TIMEOUT_SECONDS
            )
            return [self._failure()]
        except OSError as error:
            logger.warning("Could not run the speed test: %s", error)
            return [self._failure()]

        if completed.returncode != 0:
            logger.warning(
                "The speed test failed (exit %s): %s",
                completed.returncode,
                completed.stderr.strip(),
            )
            return [self._failure()]

        payload = _find_result_object(completed.stdout)
        if payload is None:
            logger.warning(
                "The speed test exited cleanly but produced no result object: %s",
                completed.stdout.strip()[:500],
            )
            return [self._failure()]

        return [self._to_result(payload)]

    def store(
        self, conn: sqlite3.Connection, results: Sequence[SpeedtestResult]
    ) -> None:
        db.insert_speedtest_results(conn, results)

    def _to_result(self, payload: dict[str, Any]) -> SpeedtestResult:
        """Build a result from Ookla's JSON, failing closed on an unexpected shape."""
        try:
            download = to_megabits_per_second(payload["download"]["bandwidth"])
            upload = to_megabits_per_second(payload["upload"]["bandwidth"])
            latency = float(payload["ping"]["latency"])
        except (KeyError, TypeError, ValueError) as error:
            logger.warning("The speed test result was missing fields: %s", error)
            return self._failure()

        server = payload.get("server") or {}
        return SpeedtestResult(
            timestamp=self._clock(),
            download_mbps=download,
            upload_mbps=upload,
            ping_ms=latency,
            isp=payload.get("isp"),
            server=_describe_server(server),
            success=True,
        )

    def _failure(self) -> SpeedtestResult:
        return SpeedtestResult(
            timestamp=self._clock(),
            download_mbps=None,
            upload_mbps=None,
            ping_ms=None,
            isp=None,
            server=None,
            success=False,
        )


def _find_result_object(stdout: str) -> dict[str, Any] | None:
    """Locate Ookla's final result object in the command's output.

    Confirmed against Ookla 1.2.0.84: ``--format=json`` emits exactly one line
    and sends its own log messages to stderr, so the common case is the whole
    of stdout. Two fallbacks cover the rest — the object spread over several
    lines, and a stream of progress objects with the result last — because
    either would otherwise be recorded as a failed speed test rather than
    diagnosed, and only on the Pi.
    """
    whole = stdout.strip()
    if whole:
        try:
            parsed = json.loads(whole)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, dict) and parsed.get("type") == "result":
                return parsed

    candidates = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            candidates.append(parsed)

    for candidate in reversed(candidates):
        if candidate.get("type") == "result":
            return candidate
    return None


def _describe_server(server: dict[str, Any]) -> str | None:
    """Render Ookla's server object as one human-readable string."""
    name = server.get("name")
    location = server.get("location")
    if name and location:
        return f"{name} ({location})"
    return name or location or None
