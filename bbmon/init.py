"""The bbmon database initialisation step.

Runs standalone as ``python -m bbmon.init``; the ``bbmon-init.service`` unit
added at M2 is a ``Type=oneshot`` wrapper around this entrypoint, and every
other unit is ordered ``After=`` it.

Requirement 3 asks for the schema to exist before any service starts. Each
service does also call :func:`bbmon.db.initialise` on its own way up, which is
idempotent and stays as a safety net for standalone runs — but relying on it
alone would mean three services racing to create the same tables on boot, and
would leave a schema-version mismatch surfacing three times over as a crash
loop rather than once, up front, as a failed unit.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from bbmon import db, reboot, timesync
from bbmon.config import Config, ConfigError, load
from bbmon.db import DatabaseError
from bbmon.reboot import RebootError

logger = logging.getLogger(__name__)


def main() -> int:
    """Entrypoint for ``python -m bbmon.init``.

    :return: ``0`` once the schema is present, ``1`` if the configuration is
        invalid or the database cannot be created.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    try:
        config = load()
        db.initialise(config.database_path)
    except (ConfigError, DatabaseError):
        logger.exception("The database could not be initialised")
        return 1

    logger.info(
        "Database ready at %s, schema version %d",
        config.database_path,
        db.SCHEMA_VERSION,
    )

    # Requirement 6, and the reason this wait is here rather than in each
    # service: the restart row below is the boot's first timestamped write, and
    # every other unit is ordered after this one, so waiting once covers them
    # all. On a Pi with no RTC the clock before this point is fiction.
    timesync.wait_for_synchronised()

    _record_restart(config)
    return 0


def _record_restart(config: Config) -> None:
    """Record this boot, per requirement 6.

    This unit is where the check belongs: every other unit is ordered after it,
    so the restart is recorded exactly once per boot rather than once per
    service, and it is recorded before any measurement is written.

    A failure here is logged and swallowed. The unit's job is the schema, and
    everything else ``Requires=`` it — taking the whole system down because
    the uptime could not be read would cost more than the missing row.
    """
    now = datetime.now(timezone.utc)
    try:
        with db.connect(config.database_path) as conn:
            reboot.record_startup(
                conn,
                reboot.request_file_path(config.database_path),
                boot_time=reboot.boot_time(now=now, uptime=reboot.uptime_seconds()),
                now=now,
            )
    except (RebootError, DatabaseError):
        logger.exception("This restart could not be recorded")


if __name__ == "__main__":
    sys.exit(main())
