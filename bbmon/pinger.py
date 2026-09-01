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
import threading

from bbmon import db, reboot
from bbmon.collectors.ping import PingCollector
from bbmon.config import Config, ConfigError, load, reloaded
from bbmon.db import DatabaseError
from bbmon.reboot import RebootAction, RebootError, RebootScheduler
from bbmon.retention import RetentionPurge
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
        reboot_action = reboot.action_from_environment(
            reboot.trigger_file_path(config.database_path)
        )
    except (ConfigError, DatabaseError, RebootError):
        logger.exception("The pinger could not start")
        return 1

    # Requirement 2's SIGHUP reload, as a rebuild rather than as a set of
    # settings pushed into running objects. Everything below is constructed
    # from one configuration, so a reload cannot leave the service part way
    # between two of them — and the reload path is the startup path, which is
    # the only one that has to be right anyway. What it costs is the state
    # those objects hold: the purge runs again on the first cycle after a
    # reload, and a reboot already asked for is asked for again.
    reloading = threading.Event()
    while True:
        code = _run(config, reboot_action, reloading)
        if not reloading.is_set():
            return code

        reloading.clear()
        config = reloaded(config)


def _run(
    config: Config, reboot_action: RebootAction, reloading: threading.Event
) -> int:
    """Run the pinger on one configuration, until it is stopped or reloaded.

    Nothing here can fail the way :func:`main`'s first build can: ``reloaded``
    refuses a configuration that moves ``database.path``, so the database, the
    reboot trigger and the request file are the ones this process started with.
    """
    scheduler = RebootScheduler(
        interval_days=config.reboot_interval_days,
        action=reboot_action,
        request_path=reboot.request_file_path(config.database_path),
    )
    purge = RetentionPurge(
        database_path=config.database_path,
        ping_days=config.retention_ping_days,
    )

    def between_cycles() -> None:
        """The two jobs that ride on this loop rather than owning a timer.

        The order between them carries no meaning and is not tested: asking
        for a reboot writes a trigger file and returns, so the machine goes
        down some seconds later, in the middle of whatever is running.
        """
        purge.check()
        scheduler.check()

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
    logger.info("Keeping %d days of ping results", config.retention_ping_days)

    return run_until_stopped(
        collector,
        config.database_path,
        flush_interval_seconds=FLUSH_INTERVAL_SECONDS,
        between_cycles=between_cycles,
        reloading=reloading,
    )


if __name__ == "__main__":
    sys.exit(main())
