"""Restart records and the reboot mechanism.

Requirement 6 wants two things that look related and are not. One is history:
every restart of the machine appears in the ``restarts`` table, marked
``expected`` when bbmon asked for it and unexpected when it did not — a power
cut, a crash, someone typing ``reboot``. The other is the reboot itself, which
only exists on the Pi and is kept behind :class:`RebootAction` so that
requirement 10's "no Pi-specific call in the development path" holds.

**How a restart is judged.** Nothing survives a reboot except the filesystem,
so a planned reboot leaves a request file behind before it goes down and the
next startup reads it. Exactly one row is written per boot, at startup, and
the request file is deleted as it is read — otherwise one planned reboot would
excuse every power cut that followed it.

The row is therefore written when the machine comes *back*, not when it went
away, which is the only timestamp a restart record can honestly carry: an
unexpected restart is by definition not observed while it happens.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from bbmon import db
from bbmon.models import Restart

logger = logging.getLogger(__name__)

#: Linux exposes seconds-since-boot here, as "<uptime> <idle>".
UPTIME_PATH = Path("/proc/uptime")

#: Written next to the database, which is the one directory the services can
#: write to under ``ProtectSystem=strict`` — see the ``StateDirectory=bbmon``
#: line in every unit.
REBOOT_REQUEST_FILENAME = "reboot-requested"

_UNEXPECTED_REASON = "no reboot was requested before the machine went down"
_UNEXPLAINED_REASON = "requested by bbmon"


class RebootError(Exception):
    """Raised when the reboot mechanism cannot do what it was asked to."""


def request_file_path(database_path: str | Path) -> Path:
    """Where the pending-reboot request lives, given the configured database.

    Derived from the database path rather than configured separately: the two
    have to sit in the same writable state directory, and a second setting
    would only be a way for them to disagree.
    """
    return Path(database_path).parent / REBOOT_REQUEST_FILENAME


def uptime_seconds(path: str | Path | None = None) -> float:
    """How long the machine has been up.

    :param path: Where to read it from, defaulting to :data:`UPTIME_PATH`.
        Resolved on each call rather than bound as a default argument, so the
        one file this module touches outside the state directory stays a
        boundary a test can move.
    :raises RebootError: if the file cannot be read or does not look like
        ``/proc/uptime``. Guessing here would put a wrong timestamp on a
        restart record and a wrong due time on the next reboot.
    """
    path = Path(path) if path is not None else UPTIME_PATH
    try:
        contents = path.read_text()
    except OSError as error:
        logger.error("Could not read the system uptime from %s: %s", path, error)
        raise RebootError(f"Could not read the system uptime from {path}: {error}")

    try:
        return float(contents.split()[0])
    except (IndexError, ValueError):
        logger.error("Unexpected content in %s: %r", path, contents)
        raise RebootError(f"Unexpected content in {path}: {contents!r}")


def boot_time(now: datetime, uptime: float) -> datetime:
    """When the machine last booted."""
    return now - timedelta(seconds=uptime)


def record_startup(
    conn: sqlite3.Connection,
    request_path: str | Path,
    boot_time: datetime,
    now: datetime,
) -> Restart | None:
    """Record this boot as a restart, expected or not, exactly once.

    :param request_path: The file a planned reboot leaves behind. Its presence
        makes the restart expected and its contents become the reason; it is
        deleted here so the next restart is judged on its own.
    :param boot_time: When the machine came up, from :func:`boot_time`.
    :param now: When the restart was noticed.
    :return: The row written, or ``None`` if this boot is already recorded —
        which is the normal case when a single service is restarted by hand.
    """
    request_path = Path(request_path)

    latest = db.latest_restart(conn)
    if latest is not None and latest.timestamp >= boot_time:
        logger.debug("This boot is already recorded; nothing to do")
        _clear_request(request_path)
        return None

    expected, reason = _consume_request(request_path)
    restart = Restart(timestamp=now, expected=expected, reason=reason)
    db.insert_restart(conn, restart)

    if expected:
        logger.info("Recorded the expected restart: %s", reason)
    else:
        logger.warning("Recorded an unexpected restart: %s", reason)
    return restart


def _consume_request(request_path: Path) -> tuple[bool, str]:
    """Read and delete the pending-reboot request, if there is one."""
    try:
        reason = request_path.read_text().strip()
    except FileNotFoundError:
        return False, _UNEXPECTED_REASON
    except OSError as error:
        # Unreadable is not the same as absent, and calling it absent would
        # report a planned reboot as a power cut. Say so rather than guess.
        logger.error("Could not read the reboot request %s: %s", request_path, error)
        return False, f"a reboot request existed but could not be read: {error}"

    _clear_request(request_path)
    return True, reason or _UNEXPLAINED_REASON


def _clear_request(request_path: Path) -> None:
    try:
        request_path.unlink(missing_ok=True)
    except OSError as error:
        # Left in place, it would make the next unexpected restart look planned.
        logger.error(
            "Could not delete the reboot request %s; the next unexpected "
            "restart may be recorded as expected: %s",
            request_path,
            error,
        )
