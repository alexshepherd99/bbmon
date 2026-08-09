"""The bbmon pinger service.

Runs standalone as ``python -m bbmon.pinger``; the systemd unit added at M2 is
a thin wrapper around this entrypoint, so nothing here depends on systemd.

Results are buffered in memory and flushed periodically rather than written on
every ping — on a Pi booting from an SD card, one write per ping per target is
the difference between a card lasting years and lasting months.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import FrameType

from bbmon import db
from bbmon.collectors.base import Collector, CollectorError
from bbmon.collectors.ping import PingCollector
from bbmon.config import ConfigError, load
from bbmon.db import DatabaseError
from bbmon.models import PingResult

logger = logging.getLogger(__name__)

#: Requirement 4 puts the flush somewhere in the 30–60s range.
FLUSH_INTERVAL_SECONDS = 60

#: Ceiling on the in-memory buffer, so an outage that blocks writes for hours
#: cannot grow without bound on a machine with very little memory.
MAX_BUFFERED_RESULTS = 5000


class PingerService:
    """Runs a collector on its interval, buffering results between writes."""

    def __init__(
        self,
        collector: Collector,
        database_path: str | Path,
        flush_interval_seconds: int = FLUSH_INTERVAL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        :param collector: The measurement to run each cycle.
        :param database_path: Where results are written.
        :param flush_interval_seconds: How often the buffer is written out.
        :param sleep: Injection point for waiting between cycles.
        :param monotonic: Injection point for elapsed-time measurement. Monotonic
            rather than wall-clock, so an NTP step cannot skip or stall a flush.
        """
        self._collector = collector
        self._database_path = Path(database_path)
        self._flush_interval_seconds = flush_interval_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self.max_buffered_results = MAX_BUFFERED_RESULTS
        self.buffer: list[PingResult] = []

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
        last_flush = self._monotonic()

        try:
            while should_continue():
                self._collect_once()
                if self._monotonic() - last_flush >= self._flush_interval_seconds:
                    self._flush()
                    last_flush = self._monotonic()
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

        pending = list(self.buffer)
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


def main() -> int:
    """Entrypoint for ``python -m bbmon.pinger``."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    try:
        config = load()
        db.initialise(config.database_path)
    except (ConfigError, DatabaseError):
        logger.exception("The pinger could not start")
        return 1

    collector = PingCollector(
        targets=config.ping_targets,
        interval_seconds=config.ping_interval_seconds,
    )
    # systemd stops a service with SIGTERM, which by default kills the process
    # outright — losing everything buffered since the last flush, on every
    # restart and every deploy. Stopping via this event instead lets the loop
    # exit normally and write what it is holding.
    stopping = threading.Event()

    def request_stop(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received %s, finishing the current cycle", signal.Signals(signum).name)
        stopping.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    service = PingerService(
        collector,
        config.database_path,
        # Event.wait returns as soon as the event is set, so a stop request is
        # not left waiting out the rest of the ping interval.
        sleep=stopping.wait,
    )

    logger.info(
        "Pinging %s every %ds, writing to %s",
        ", ".join(config.ping_targets),
        config.ping_interval_seconds,
        config.database_path,
    )

    try:
        service.run(should_continue=lambda: not stopping.is_set())
    except CollectorError:
        logger.exception("The ping collector cannot run")
        return 1

    logger.info("Stopped cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
