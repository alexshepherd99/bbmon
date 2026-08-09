"""Latency measurement by shelling out to the system ``ping`` binary.

The system binary is used rather than raw sockets because it is already
setuid/setcap on both Raspberry Pi OS and the development container, so the
service needs no ``CAP_NET_RAW`` grant of its own.

Ping targets are user-editable configuration, which makes this the program's
main injection path. The command is always built as an argv list and passed
straight to ``subprocess.run`` — there is no shell anywhere in this module.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from bbmon import db
from bbmon.collectors.base import Collector, CollectorError
from bbmon.models import PingResult

logger = logging.getLogger(__name__)

#: How long ping waits for a reply, and — with a small margin — how long we
#: wait for ping itself before giving up on the subprocess.
REPLY_TIMEOUT_SECONDS = 5
_SUBPROCESS_TIMEOUT_MARGIN_SECONDS = 5

_LATENCY = re.compile(r"time[=<]\s*([0-9]+(?:\.[0-9]+)?)\s*ms")

Runner = Callable[..., subprocess.CompletedProcess]


def parse_latency_ms(output: str) -> float | None:
    """Return the round-trip time from ping's output, or ``None`` if absent."""
    match = _LATENCY.search(output)
    return float(match.group(1)) if match else None


class PingCollector(Collector):
    """Pings each configured target once per cycle."""

    def __init__(
        self,
        targets: Sequence[str],
        interval_seconds: int,
        runner: Runner = subprocess.run,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        """
        :param targets: Hostnames or IP addresses, already validated by the
            config loader.
        :param interval_seconds: How often the pinger service runs a cycle.
        :param runner: Injection point for the subprocess call, so tests do not
            need real network access.
        :param clock: Injection point for the current time.
        """
        self._targets = tuple(targets)
        self._interval_seconds = interval_seconds
        self._runner = runner
        self._clock = clock

    @property
    def name(self) -> str:
        return "ping"

    @property
    def interval_seconds(self) -> int:
        return self._interval_seconds

    def collect(self) -> list[PingResult]:
        """Ping every target once, in configured order."""
        return [self._ping(target) for target in self._targets]

    def store(self, conn: sqlite3.Connection, results: Sequence[PingResult]) -> None:
        db.insert_ping_results(conn, results)

    def _ping(self, target: str) -> PingResult:
        # The target is always the final, separate element: nothing it contains
        # can become another argument, let alone another command.
        argv = ["ping", "-c", "1", "-n", "-W", str(REPLY_TIMEOUT_SECONDS), target]

        try:
            completed = self._runner(
                argv,
                capture_output=True,
                text=True,
                timeout=REPLY_TIMEOUT_SECONDS + _SUBPROCESS_TIMEOUT_MARGIN_SECONDS,
            )
        except FileNotFoundError as error:
            logger.error("The ping binary is not available: %s", error)
            raise CollectorError(f"The ping binary is not available: {error}")
        except subprocess.TimeoutExpired:
            logger.warning("ping to %s did not return within its timeout", target)
            return self._failure(target)
        except OSError as error:
            logger.warning("Could not run ping for %s: %s", target, error)
            return self._failure(target)

        if completed.returncode != 0:
            logger.debug(
                "ping to %s failed (exit %s): %s",
                target,
                completed.returncode,
                completed.stderr.strip(),
            )
            return self._failure(target)

        latency_ms = parse_latency_ms(completed.stdout)
        if latency_ms is None:
            logger.warning(
                "ping to %s exited cleanly but reported no round-trip time", target
            )
            return self._failure(target)

        return PingResult(
            timestamp=self._clock(),
            target=target,
            latency_ms=latency_ms,
            success=True,
        )

    def _failure(self, target: str) -> PingResult:
        return PingResult(
            timestamp=self._clock(), target=target, latency_ms=None, success=False
        )
