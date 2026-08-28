"""Tests for the daily ping-retention purge that rides on the pinger's loop."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bbmon import db, retention
from bbmon.models import PingResult


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    return path


def days_ago(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def write_pings(database: Path, ages_in_days: list[float]) -> None:
    with db.connect(database) as conn:
        db.insert_ping_results(
            conn,
            [PingResult(days_ago(age), "8.8.8.8", 1.0, True) for age in ages_in_days],
        )


def surviving_pings(database: Path) -> int:
    with db.connect(database) as conn:
        return len(db.recent_ping_results(conn, since=days_ago(3650)))


class FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


def test_the_first_check_purges(database: Path) -> None:
    """A freshly deployed service should not wait a day to honour retention."""
    write_pings(database, [40, 1])

    retention.RetentionPurge(database, ping_days=30, monotonic=FakeClock()).check()

    assert surviving_pings(database) == 1


def test_a_second_check_the_same_day_does_not_purge_again(database: Path) -> None:
    """This runs every few seconds; it must be a daily job, not a per-cycle one."""
    clock = FakeClock()
    purge = retention.RetentionPurge(database, ping_days=30, monotonic=clock)
    purge.check()

    write_pings(database, [40])
    clock.advance(retention.PURGE_INTERVAL_SECONDS - 1)
    purge.check()

    assert surviving_pings(database) == 1


def test_a_check_a_day_later_purges_again(database: Path) -> None:
    clock = FakeClock()
    purge = retention.RetentionPurge(database, ping_days=30, monotonic=clock)
    purge.check()

    write_pings(database, [40])
    clock.advance(retention.PURGE_INTERVAL_SECONDS)
    purge.check()

    assert surviving_pings(database) == 0


def test_the_cutoff_comes_from_the_configured_retention(database: Path) -> None:
    """A shorter window keeps less; the setting is not decorative."""
    write_pings(database, [10, 3])

    retention.RetentionPurge(database, ping_days=7, monotonic=FakeClock()).check()

    assert surviving_pings(database) == 1


def test_a_failing_purge_does_not_raise(tmp_path: Path) -> None:
    """It runs inside the ping loop; a database problem must not stop measuring."""
    unreachable = tmp_path / "no-such-directory" / "bbmon.db"

    retention.RetentionPurge(unreachable, ping_days=30, monotonic=FakeClock()).check()


def test_a_failing_purge_is_retried_on_the_next_cycle(tmp_path: Path) -> None:
    """A failure must not count as a purge, or retention waits another day.

    The retry comes seconds later, not a day later: the loop runs every few
    seconds, and a purge that failed has not happened.
    """
    database = tmp_path / "bbmon.db"
    clock = FakeClock()
    purge = retention.RetentionPurge(database, ping_days=30, monotonic=clock)

    purge.check()

    db.initialise(database)
    write_pings(database, [40])
    clock.advance(5)
    purge.check()

    assert surviving_pings(database) == 0


def test_a_purge_that_deleted_rows_says_so(
    database: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The count is the only evidence the purge ran, and the journal is volatile."""
    write_pings(database, [40, 40, 40])

    with caplog.at_level(logging.INFO, logger="bbmon.retention"):
        retention.RetentionPurge(database, ping_days=30, monotonic=FakeClock()).check()

    assert "3" in caplog.text


def test_a_purge_that_deleted_nothing_stays_quiet(
    database: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The normal case for most of a window's length; it should not fill the log."""
    write_pings(database, [1])

    with caplog.at_level(logging.INFO, logger="bbmon.retention"):
        retention.RetentionPurge(database, ping_days=30, monotonic=FakeClock()).check()

    assert caplog.text == ""
