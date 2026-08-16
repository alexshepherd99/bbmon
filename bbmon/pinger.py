"""The bbmon pinger service.

Runs standalone as ``python -m bbmon.pinger``; the systemd unit added at M2 is
a thin wrapper around this entrypoint, so nothing here depends on systemd.

Results are buffered in memory and flushed periodically rather than written on
every ping — on a Pi booting from an SD card, one write per ping per target is
the difference between a card lasting years and lasting months. The loop that
does this lives in :mod:`bbmon.service`, shared with the speed test.

Requirement 6's periodic reboot rides on that loop too. It has no timer of its
own for the same reason the retention purge will not have one at M6: this
service is already awake every few seconds, and a systemd timer would be a
fifth unit to install, secure and reason about — one whose schedule would have
to be baked into a unit file rather than read from ``reboot.interval_days``.
"""

from __future__ import annotations

import logging
import sys

from bbmon import db, reboot
from bbmon.collectors.ping import PingCollector
from bbmon.config import ConfigError, load
from bbmon.db import DatabaseError
from bbmon.reboot import RebootError, RebootScheduler
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
        reboot_action = reboot.action_from_environment()
        scheduler = RebootScheduler(
            interval_days=config.reboot_interval_days,
            action=reboot_action,
            request_path=reboot.request_file_path(config.database_path),
        )
    except (ConfigError, DatabaseError, RebootError):
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
    logger.info(
        "Rebooting every %d days, using the %s reboot action",
        config.reboot_interval_days,
        type(reboot_action).__name__,
    )

    return run_until_stopped(
        collector,
        config.database_path,
        flush_interval_seconds=FLUSH_INTERVAL_SECONDS,
        between_cycles=scheduler.check,
    )


if __name__ == "__main__":
    sys.exit(main())
