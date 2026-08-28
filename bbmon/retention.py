"""Requirement 3's ping retention, as a daily job on the pinger's existing loop.

``ping_results`` is the one table that grows without bound — three targets on a
five-second interval is roughly fifty thousand rows a day — so it is the one
table with a retention rule. Speed tests and restarts are kept indefinitely and
are never touched here.

There is no systemd timer, by the decision recorded in ``plan.md``: the pinger
is already awake every few seconds, and a timer would be a fifth unit to
install, secure and reason about, with its schedule baked into a unit file
rather than read from ``retention.ping_days``. This is the same reasoning that
put the periodic reboot on the same loop at M4.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bbmon import db
from bbmon.db import DatabaseError

logger = logging.getLogger(__name__)

#: How long between purges. Requirement 3 asks for a daily job, and there is
#: nothing to gain from running it more often: a day's pings are a day old.
PURGE_INTERVAL_SECONDS = 24 * 60 * 60


class RetentionPurge:
    """Deletes pings past their retention window, at most once a day.

    Due-ness is measured with a monotonic clock rather than the wall clock, for
    the same reason the flush interval is: an NTP step on a Pi with no RTC —
    which happens on every boot — must not skip a purge or trigger a burst of
    them. The retention *cutoff* is wall-clock, because that is what the stored
    timestamps are.
    """

    def __init__(
        self,
        database_path: str | Path,
        ping_days: int,
        interval_seconds: float = PURGE_INTERVAL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        """
        :param database_path: The database to purge.
        :param ping_days: ``retention.ping_days`` from the configuration.
        :param interval_seconds: How long between purges.
        :param monotonic: Injection point for elapsed-time measurement.
        :param now: Injection point for the wall clock the cutoff is taken from.
        """
        self._database_path = Path(database_path)
        self._ping_days = ping_days
        self._interval_seconds = interval_seconds
        self._monotonic = monotonic
        self._now = now
        self._last_purge: float | None = None

    def check(self) -> None:
        """Purge if a purge is due. Safe to call as often as the caller likes.

        The first call always purges rather than waiting out a full interval, so
        a freshly deployed or restarted service honours retention immediately
        instead of a day later.

        Never raises: this runs inside the ping loop, and a machine that stops
        measuring because it could not delete old rows has failed at the more
        important job of the two.
        """
        if not self._is_due():
            return

        cutoff = self._now() - timedelta(days=self._ping_days)
        try:
            with db.connect(self._database_path) as conn:
                deleted = db.purge_ping_results(conn, before=cutoff)
        except DatabaseError:
            # Left undue so the next cycle tries again, rather than latched off
            # until the service restarts: retention that quietly stopped is how
            # an SD card fills up.
            logger.exception("The ping retention purge failed")
            return

        self._last_purge = self._monotonic()

        if deleted:
            logger.info(
                "Purged %d ping results recorded before %s, keeping %d days",
                deleted,
                cutoff.isoformat(),
                self._ping_days,
            )

    def _is_due(self) -> bool:
        if self._last_purge is None:
            return True
        return self._monotonic() - self._last_purge >= self._interval_seconds
