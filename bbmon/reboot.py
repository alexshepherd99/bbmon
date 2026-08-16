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
import os
import sqlite3
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path

from bbmon import db
from bbmon.models import Restart

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400

#: Linux exposes seconds-since-boot here, as "<uptime> <idle>".
UPTIME_PATH = Path("/proc/uptime")

#: Written next to the database, which is the one directory the services can
#: write to under ``ProtectSystem=strict`` — see the ``StateDirectory=bbmon``
#: line in every unit.
REBOOT_REQUEST_FILENAME = "reboot-requested"

#: Chooses the reboot implementation. Unset means the no-op, so a development
#: machine cannot reboot itself by accident; the Pi's units opt in explicitly.
REBOOT_ACTION_ENV_VAR = "BBMON_REBOOT"

_ACTION_NOOP = "noop"
_ACTION_SYSTEMCTL = "systemctl"

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


class RebootAction(ABC):
    """How this machine is rebooted, if it can be.

    Requirement 10 keeps the one genuinely Pi-specific call in phase 1 behind
    an interface, so every service can be developed and run on a machine where
    rebooting would be unwelcome.
    """

    @abstractmethod
    def reboot(self) -> None:
        """Ask the machine to reboot now.

        :raises RebootError: if the reboot could not be requested. Returning
            quietly would leave a system that believes it reboots weekly and
            has not rebooted in a year.
        """


class NoOpReboot(RebootAction):
    """The development default: says what would have happened, does nothing."""

    def reboot(self) -> None:
        logger.warning(
            "A reboot was requested, but the no-op reboot action is in use, so "
            "nothing will happen. Set %s=%s to reboot for real.",
            REBOOT_ACTION_ENV_VAR,
            _ACTION_SYSTEMCTL,
        )


class SystemctlReboot(RebootAction):
    """The Pi implementation: one fixed command through a narrow sudoers rule.

    ``plan.md``'s security posture allows exactly this — an argv list, no
    shell, no wildcard, and not one character of it derived from configuration
    or from anything a web request could reach. ``deploy/sudoers.d/bbmon``
    grants the ``bbmon`` user this command and nothing else, so the sudoers
    entry and this tuple have to stay character-for-character identical.
    """

    COMMAND = (
        "/usr/bin/sudo",
        "-n",
        "/usr/bin/systemctl",
        "start",
        "bbmon-reboot.service",
    )

    def __init__(self, run: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        """:param run: Injection point for the one subprocess call."""
        self._run = run

    def reboot(self) -> None:
        try:
            completed = self._run(
                list(self.COMMAND),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            logger.error("Could not run %s: %s", " ".join(self.COMMAND), error)
            raise RebootError(f"Could not run {' '.join(self.COMMAND)}: {error}")

        if completed.returncode != 0:
            logger.error(
                "%s exited %d: %s",
                " ".join(self.COMMAND),
                completed.returncode,
                completed.stderr.strip(),
            )
            raise RebootError(
                f"{' '.join(self.COMMAND)} exited {completed.returncode}: "
                f"{completed.stderr.strip()}"
            )

        logger.info("The machine has been asked to reboot")


def action_from_environment(
    environ: Mapping[str, str] | None = None,
) -> RebootAction:
    """Pick the reboot implementation named by ``BBMON_REBOOT``.

    :raises RebootError: for an unrecognised value. A typo in a unit file
        would otherwise disable rebooting for good, and look like nothing.
    """
    if environ is None:
        environ = os.environ

    setting = environ.get(REBOOT_ACTION_ENV_VAR, _ACTION_NOOP).strip().lower()
    if setting == _ACTION_NOOP:
        return NoOpReboot()
    if setting == _ACTION_SYSTEMCTL:
        return SystemctlReboot()

    raise RebootError(
        f"{REBOOT_ACTION_ENV_VAR}={setting!r} is not a reboot action; "
        f"expected {_ACTION_NOOP!r} or {_ACTION_SYSTEMCTL!r}"
    )


def request_reboot(
    request_path: str | Path, action: RebootAction, reason: str
) -> None:
    """Record why the machine is going down, then ask it to go.

    The reason is written first because nothing in this process survives the
    reboot, and it is taken back if the reboot is refused — a request left
    lying around would make the next unexpected restart, whenever it came,
    look like this planned one.

    :raises RebootError: if the reboot could not be requested.
    """
    request_path = Path(request_path)

    try:
        request_path.write_text(reason)
    except OSError as error:
        logger.error("Could not write the reboot request %s: %s", request_path, error)
        raise RebootError(f"Could not write the reboot request {request_path}: {error}")

    logger.info("Rebooting: %s", reason)
    try:
        action.reboot()
    except RebootError:
        _clear_request(request_path)
        raise


class RebootScheduler:
    """Decides when the periodic reboot in requirement 6 is due.

    Due-ness is measured from the machine's uptime rather than from the last
    row in ``restarts``: a power cut restarts that clock as surely as a planned
    reboot does, and a Pi that came up ten minutes ago does not need rebooting
    whatever the table says. It also means the decision needs no database read,
    which matters because it is taken from inside the ping loop.
    """

    def __init__(
        self,
        interval_days: int,
        action: RebootAction,
        request_path: str | Path,
        uptime: Callable[[], float] = uptime_seconds,
    ) -> None:
        """
        :param interval_days: ``reboot.interval_days`` from the configuration.
        :param action: How to reboot, from :func:`action_from_environment`.
        :param request_path: Where to leave the reason, from
            :func:`request_file_path`.
        :param uptime: Injection point for reading the machine's uptime.
        """
        self._interval_seconds = interval_days * SECONDS_PER_DAY
        self._interval_days = interval_days
        self._action = action
        self._request_path = Path(request_path)
        self._uptime = uptime
        self._requested = False

    def seconds_until_due(self) -> float:
        """How long until the periodic reboot is due; ``0`` once it is overdue."""
        return max(0.0, self._interval_seconds - self._uptime())

    def check(self) -> None:
        """Reboot if it is due. Safe to call as often as the caller likes.

        Never raises: this runs inside the ping loop, and a machine that stops
        measuring because it could not reboot has failed at the more important
        job of the two.
        """
        if self._requested:
            return

        try:
            if self.seconds_until_due() > 0:
                return
            request_reboot(
                self._request_path,
                self._action,
                reason=f"scheduled reboot after {self._interval_days} days of uptime",
            )
        except RebootError:
            # Retried on the next cycle rather than latched off: a one-off
            # sudo failure should not disable rebooting until the next boot.
            logger.exception("The scheduled reboot could not be started")
            return

        self._requested = True


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
