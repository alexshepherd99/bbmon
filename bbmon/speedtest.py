"""The bbmon speed test service.

Runs standalone as ``python -m bbmon.speedtest``; the systemd unit added at M2
is a thin wrapper around this entrypoint.

Requirement 5 asks for a test on startup and then every
``speedtest.interval_hours``. The shared loop in :mod:`bbmon.service` collects
before it sleeps, so the startup run is the first cycle rather than a special
case.

Unlike the pinger, this service does not buffer. Buffering exists to spare the
SD card thousands of small ping writes; one row every few hours is not worth
holding in memory for hours, where a crash would lose it.
"""

from __future__ import annotations

import logging
import sys

from bbmon import db, reboot
from bbmon.collectors.speedtest import SpeedtestCollector
from bbmon.config import ConfigError, load
from bbmon.db import DatabaseError
from bbmon.service import FLUSH_EVERY_CYCLE, run_until_stopped

logger = logging.getLogger(__name__)

#: How close to a reboot a speed test is abandoned rather than started.
#: A test takes 30–40s and is killed at 180s (see the collector's
#: ``TIMEOUT_SECONDS``); five minutes covers the slowest run plus the time the
#: machine takes to go down, without skipping tests that would have finished.
SKIP_BEFORE_REBOOT_SECONDS = 300


def main() -> int:
    """Entrypoint for ``python -m bbmon.speedtest``."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    try:
        config = load()
        db.initialise(config.database_path)
    except (ConfigError, DatabaseError):
        logger.exception("The speed test service could not start")
        return 1

    collector = SpeedtestCollector(
        interval_hours=config.speedtest_interval_hours,
        # Requirement 5. Asked freshly each cycle, and answered from the
        # configured interval and the machine's uptime — the same two things
        # the pinger's schedule reads, so the two services agree on when the
        # reboot is coming without sharing any state.
        reboot_imminent=lambda: reboot.reboot_is_imminent(
            config.reboot_interval_days, SKIP_BEFORE_REBOOT_SECONDS
        ),
    )

    logger.info(
        "Running a speed test now and every %dh, writing to %s",
        config.speedtest_interval_hours,
        config.database_path,
    )

    return run_until_stopped(
        collector,
        config.database_path,
        flush_interval_seconds=FLUSH_EVERY_CYCLE,
    )


if __name__ == "__main__":
    sys.exit(main())
