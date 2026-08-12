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

from bbmon import db
from bbmon.config import ConfigError, load
from bbmon.db import DatabaseError

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
