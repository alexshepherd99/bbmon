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

**How the reboot happens.** Two files in the state directory, each with one
job. ``reboot-requested`` holds the reason and is read by the next startup.
``reboot-now`` is the trigger: ``bbmon-reboot.path`` watches it, and a write
makes systemd start ``bbmon-reboot.service``, which reboots. No bbmon process
is privileged and none gains a privilege — see :class:`SystemdPathReboot` for
why the sudo route recorded in ``plan.md`` turned out to be impossible.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path

from bbmon import db
from bbmon.models import Restart

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400

#: How long to wait for a requested reboot to actually happen before asking
#: again and saying so. Generous — a Pi 3 takes well under a minute to go down
#: — because the point is to notice a reboot that is never coming, not to
#: hurry one along.
REBOOT_RETRY_SECONDS = 600

#: Linux exposes seconds-since-boot here, as "<uptime> <idle>".
UPTIME_PATH = Path("/proc/uptime")

#: Written next to the database, which is the one directory the services can
#: write to under ``ProtectSystem=strict`` — see the ``StateDirectory=bbmon``
#: line in every unit.
REBOOT_REQUEST_FILENAME = "reboot-requested"

#: The file ``bbmon-reboot.path`` watches. Writing it is how an unprivileged
#: service asks systemd to reboot the machine. Kept separate from the request
#: file above, which is a record rather than a trigger and outlives the reboot.
REBOOT_TRIGGER_FILENAME = "reboot-now"

#: The trigger path spelled out literally in ``bbmon-reboot.path`` and
#: ``bbmon-reboot.service``. The code derives its own from ``database.path``,
#: so the two agree only while that setting points into the state directory —
#: :func:`action_from_environment` refuses the real action when they diverge.
WATCHED_TRIGGER_PATH = Path("/var/lib/bbmon/reboot-now")

#: Chooses the reboot implementation. Unset means the no-op, so a development
#: machine cannot reboot itself by accident; the Pi's units opt in explicitly.
REBOOT_ACTION_ENV_VAR = "BBMON_REBOOT"

_ACTION_NOOP = "noop"
_ACTION_SYSTEMD = "systemd"

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


def trigger_file_path(database_path: str | Path) -> Path:
    """Where the reboot trigger lives, given the configured database.

    In the same state directory and for the same reason as
    :func:`request_file_path`: it is the only path the units leave writable.
    """
    return Path(database_path).parent / REBOOT_TRIGGER_FILENAME


def clear_trigger(trigger_path: str | Path) -> None:
    """Remove a reboot trigger left over from a reboot that already happened.

    Belt and braces against the one catastrophic failure this mechanism could
    have: a Pi that reboots, comes up, sees its own trigger and reboots again.
    ``bbmon-reboot.path`` watches for a *write*, so a file that is merely
    present should not fire it, and ``bbmon-reboot.service`` deletes the
    trigger before rebooting anyway — but "should not" is doing a lot of work
    in that sentence for a machine that is not in the room, and this costs one
    unlink per boot.

    Called from ``bbmon-init``, which every other unit is ordered after.
    """
    trigger_path = Path(trigger_path)
    try:
        existed = trigger_path.exists()
        trigger_path.unlink(missing_ok=True)
    except OSError as error:
        logger.error(
            "Could not remove a stale reboot trigger %s: %s", trigger_path, error
        )
        return

    if existed:
        logger.warning("Removed a reboot trigger left over from before this boot")


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


def seconds_until_due(interval_days: int, uptime: float) -> float:
    """How long until the periodic reboot is due; ``0`` once it is overdue."""
    return max(0.0, interval_days * SECONDS_PER_DAY - uptime)


def reboot_is_imminent(interval_days: int, within_seconds: float) -> bool:
    """Whether a reboot is due within ``within_seconds``.

    Requirement 5's "skip the speed test if a reboot is imminently due", asked
    from a different process than the one that will do the rebooting. It needs
    no shared state: both read the same configured interval and the same
    uptime, so both reach the same answer.

    An unreadable uptime answers "no". A speed test that gets killed by a
    reboot costs one row; a speed test service that stops running costs the
    measurement entirely.
    """
    try:
        return seconds_until_due(interval_days, uptime_seconds()) <= within_seconds
    except RebootError:
        logger.exception("Could not tell whether a reboot is imminent; assuming not")
        return False


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
            _ACTION_SYSTEMD,
        )


class SystemdPathReboot(RebootAction):
    """The Pi implementation: touch a file systemd is watching.

    Nothing in bbmon runs as root and nothing in bbmon gains a privilege. The
    only thing that can reboot this machine is ``bbmon-reboot.service``, a root
    unit owning two lines, and the only thing that can start it is
    ``bbmon-reboot.path``, which watches for a write to this one file inside
    the state directory the services can already write to.

    The rejected alternative was ``sudo systemctl start bbmon-reboot.service``,
    which ``plan.md`` recorded as the mechanism. It cannot work: every unit
    sets ``NoNewPrivileges=yes``, which makes the kernel ignore sudo's setuid
    bit, so sudo refuses to run at all. Keeping the sudoers rule would have
    meant dropping that directive from the one service that feeds
    user-editable ping targets into a subprocess.
    """

    def __init__(self, trigger_path: str | Path) -> None:
        """:param trigger_path: The file ``bbmon-reboot.path`` watches."""
        self._trigger_path = Path(trigger_path)

    def reboot(self) -> None:
        try:
            self._trigger_path.write_text("")
        except OSError as error:
            logger.error(
                "Could not write the reboot trigger %s: %s", self._trigger_path, error
            )
            raise RebootError(
                f"Could not write the reboot trigger {self._trigger_path}: {error}"
            )

        logger.info(
            "Wrote %s; systemd should now start bbmon-reboot.service",
            self._trigger_path,
        )


def action_from_environment(
    trigger_path: str | Path,
    environ: Mapping[str, str] | None = None,
) -> RebootAction:
    """Pick the reboot implementation named by ``BBMON_REBOOT``.

    :param trigger_path: Where the systemd implementation writes, from
        :func:`trigger_file_path`. Ignored by the no-op, which reboots nothing
        and so does not care where a development database lives.
    :raises RebootError: for an unrecognised value, or for a trigger path no
        unit is watching. Either one would otherwise disable rebooting for
        good and look like nothing at all.
    """
    if environ is None:
        environ = os.environ

    setting = environ.get(REBOOT_ACTION_ENV_VAR, _ACTION_NOOP).strip().lower()
    if setting == _ACTION_NOOP:
        return NoOpReboot()
    if setting == _ACTION_SYSTEMD:
        _require_watched(Path(trigger_path))
        return SystemdPathReboot(trigger_path)

    raise RebootError(
        f"{REBOOT_ACTION_ENV_VAR}={setting!r} is not a reboot action; "
        f"expected {_ACTION_NOOP!r} or {_ACTION_SYSTEMD!r}"
    )


def _require_watched(trigger_path: Path) -> None:
    """Refuse a trigger that no unit is watching.

    The unit files name the trigger literally while the code derives it from
    ``database.path``, so moving that setting moves the trigger out from under
    ``bbmon-reboot.path``. Writing to it would keep succeeding and the Pi would
    simply never reboot, which is the one failure this mechanism cannot
    otherwise report. At M6 the admin page can set ``database.path``, so this
    is also the rule that form has to validate against.
    """
    if trigger_path == WATCHED_TRIGGER_PATH:
        return

    logger.error(
        "The reboot trigger would be %s, but bbmon-reboot.path watches %s",
        trigger_path,
        WATCHED_TRIGGER_PATH,
    )
    raise RebootError(
        f"the reboot trigger would be written to {trigger_path}, but "
        f"bbmon-reboot.path watches {WATCHED_TRIGGER_PATH} — nothing would "
        f"notice it. Set database.path back to a file in "
        f"{WATCHED_TRIGGER_PATH.parent}, or update the unit files to match."
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
        self._interval_days = interval_days
        self._action = action
        self._request_path = Path(request_path)
        self._uptime = uptime
        self._requested_at: float | None = None

    def seconds_until_due(self) -> float:
        """How long until the periodic reboot is due; ``0`` once it is overdue."""
        return seconds_until_due(self._interval_days, self._uptime())

    def check(self) -> None:
        """Reboot if it is due. Safe to call as often as the caller likes.

        Never raises: this runs inside the ping loop, and a machine that stops
        measuring because it could not reboot has failed at the more important
        job of the two.
        """
        try:
            if self._waiting_to_go_down():
                return
            if self.seconds_until_due() > 0:
                return
            request_reboot(
                self._request_path,
                self._action,
                reason=f"scheduled reboot after {self._interval_days} days of uptime",
            )
        except RebootError:
            # Retried on the next cycle rather than latched off: a one-off
            # write failure should not disable rebooting until the next boot.
            logger.exception("The scheduled reboot could not be started")
            return

        self._requested_at = self._uptime()

    def _waiting_to_go_down(self) -> bool:
        """Whether a reboot has been asked for and is still plausibly coming.

        A reboot takes a minute; this loop runs every few seconds, so without
        this the request would be repeated a dozen times on the way down.

        The wait is bounded because the request is a file write, and a write
        that nobody is watching succeeds exactly like one that works — if the
        path unit is not installed or not running, the only symptom is a Pi
        that never reboots. After :data:`REBOOT_RETRY_SECONDS` still up, say
        so and ask again.
        """
        if self._requested_at is None:
            return False

        if self._uptime() - self._requested_at < REBOOT_RETRY_SECONDS:
            return True

        logger.warning(
            "A reboot was requested %gs ago and this machine is still up; "
            "asking again. Check that bbmon-reboot.path is enabled and active.",
            self._uptime() - self._requested_at,
        )
        self._requested_at = None
        return False


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
