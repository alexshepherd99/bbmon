"""The loop every collector service runs, and the signal wiring around it.

Extracted from ``bbmon.pinger`` at M3, when the speed test became the second
service to need it. The loop was already collector-agnostic; only its name was
not. Nothing here imports a specific collector.

The SIGTERM handling in :func:`run_until_stopped` is not incidental. systemd
stops a service by sending SIGTERM, which by default kills the process outright
— which at M1 silently discarded everything buffered since the last flush, on
every restart and every deploy. Every collector service goes through this
function so that fix cannot be forgotten by the next one.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import FrameType
from typing import Any

from bbmon import db
from bbmon.collectors.base import Collector, CollectorError
from bbmon.db import DatabaseError

logger = logging.getLogger(__name__)

#: Requirement 4 puts the ping flush somewhere in the 30–60s range.
FLUSH_INTERVAL_SECONDS = 60

#: Ceiling on the in-memory buffer, so an outage that blocks writes for hours
#: cannot grow without bound on a machine with very little memory.
MAX_BUFFERED_RESULTS = 5000

#: Flush every cycle. Buffering exists to spare the SD card thousands of small
#: ping writes; a collector producing one row every few hours has nothing to
#: gain from it and would instead risk holding that row in memory for hours.
FLUSH_EVERY_CYCLE = 0


class CollectorService:
    """Runs a collector on its interval, buffering results between writes."""

    def __init__(
        self,
        collector: Collector,
        database_path: str | Path,
        flush_interval_seconds: int = FLUSH_INTERVAL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        between_cycles: Callable[[], None] = lambda: None,
    ) -> None:
        """
        :param collector: The measurement to run each cycle.
        :param database_path: Where results are written.
        :param flush_interval_seconds: How often the buffer is written out.
            ``FLUSH_EVERY_CYCLE`` writes each cycle's results immediately.
        :param sleep: Injection point for waiting between cycles.
        :param monotonic: Injection point for elapsed-time measurement. Monotonic
            rather than wall-clock, so an NTP step cannot skip or stall a flush.
        :param between_cycles: Periodic work that shares this loop rather than
            owning a systemd timer of its own — M4's reboot due-check, and the
            retention purge at M6. Called once a cycle, after the flush, so it
            may take the machine down without losing buffered results. It must
            decide for itself whether it is due, and must not raise.
        """
        self._collector = collector
        self._database_path = Path(database_path)
        self._flush_interval_seconds = flush_interval_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._between_cycles = between_cycles
        self.max_buffered_results = MAX_BUFFERED_RESULTS
        self.buffer: list[Any] = []

    def run(
        self,
        should_continue: Callable[[], bool] = lambda: True,
        flush_on_exit: bool = True,
    ) -> None:
        """Collect on the configured interval until ``should_continue`` is false.

        :param should_continue: Checked before each cycle; the injection point
            that lets this loop be driven by a test rather than by a signal.
        :param flush_on_exit: Write whatever is still buffered on the way out,
            so a clean shutdown loses nothing.
        """
        # Backdated by a full interval so the first cycle's results are written
        # straight away. Requirement 4's buffering exists to spare the SD card
        # thousands of small writes; one extra write per service start is not
        # that, and without it a freshly started service left the dashboard
        # blank for a minute — after every restart and every deploy, which
        # looks broken rather than new.
        last_flush = self._monotonic() - self._flush_interval_seconds

        try:
            while should_continue():
                self._collect_once()
                if self._monotonic() - last_flush >= self._flush_interval_seconds:
                    self._flush()
                    last_flush = self._monotonic()
                self._between_cycles()
                self._sleep(self._collector.interval_seconds)
        finally:
            if flush_on_exit:
                self._flush()

    def _collect_once(self) -> None:
        results = self._collector.collect()
        self.buffer.extend(results)

        overflow = len(self.buffer) - self.max_buffered_results
        if overflow > 0:
            logger.warning(
                "Discarding %d buffered %s results; the buffer is full at %d, "
                "which means writes have been failing",
                overflow,
                self._collector.name,
                self.max_buffered_results,
            )
            del self.buffer[:overflow]

    def _flush(self) -> None:
        """Write the buffer, keeping it intact if the write fails."""
        if not self.buffer:
            return

        pending: Sequence[Any] = list(self.buffer)
        try:
            with db.connect(self._database_path) as conn:
                self._collector.store(conn, pending)
        except DatabaseError:
            # Retained rather than dropped: the next flush retries them, and
            # the cap in _collect_once bounds how far behind this can get.
            logger.exception(
                "Could not write %d buffered %s results; keeping them for the "
                "next flush",
                len(pending),
                self._collector.name,
            )
            return

        del self.buffer[: len(pending)]
        logger.debug("Wrote %d %s results", len(pending), self._collector.name)


def run_until_stopped(
    collector: Collector,
    database_path: str | Path,
    flush_interval_seconds: int = FLUSH_INTERVAL_SECONDS,
    between_cycles: Callable[[], None] = lambda: None,
) -> int:
    """Run ``collector`` until the service is asked to stop, and report an exit code.

    Installs the SIGTERM and SIGINT handlers that let the loop finish its cycle
    and write what it is holding, rather than being killed mid-buffer.

    :return: ``0`` after a clean stop, ``1`` if the collector cannot run at all.
    """
    stopping = threading.Event()

    def request_stop(signum: int, _frame: FrameType | None) -> None:
        logger.info(
            "Received %s, finishing the current cycle", signal.Signals(signum).name
        )
        stopping.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    service = CollectorService(
        collector,
        database_path,
        flush_interval_seconds=flush_interval_seconds,
        # Event.wait returns as soon as the event is set, so a stop request is
        # not left waiting out the rest of the collector's interval — which for
        # the speed test is measured in hours.
        sleep=stopping.wait,
        between_cycles=between_cycles,
    )

    try:
        service.run(should_continue=lambda: not stopping.is_set())
    except CollectorError:
        logger.exception("The %s collector cannot run", collector.name)
        return 1

    logger.info("Stopped cleanly")
    return 0
