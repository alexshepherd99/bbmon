"""The bbmon pinger service.

Runs standalone as ``python -m bbmon.pinger``; the systemd unit added at M2 is
a thin wrapper around this entrypoint, so nothing here depends on systemd.

Results are buffered in memory and flushed periodically rather than written on
every ping — on a Pi booting from an SD card, one write per ping per target is
the difference between a card lasting years and lasting months. The loop that
does this lives in :mod:`bbmon.service`, shared with the speed test.
"""

from __future__ import annotations

import logging
import sys

from bbmon import db
from bbmon.collectors.ping import PingCollector
from bbmon.config import ConfigError, load
from bbmon.db import DatabaseError
from bbmon.service import FLUSH_INTERVAL_SECONDS, run_until_stopped

logger = logging.getLogger(__name__)


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

    logger.info(
        "Pinging %s every %ds, writing to %s",
        ", ".join(config.ping_targets),
        config.ping_interval_seconds,
        config.database_path,
    )

    return run_until_stopped(
        collector,
        config.database_path,
        flush_interval_seconds=FLUSH_INTERVAL_SECONDS,
    )


if __name__ == "__main__":
    sys.exit(main())
