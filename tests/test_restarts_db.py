"""Tests for storing and reading restart records."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bbmon import db
from bbmon.models import Restart


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    return path


def at(minutes_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def test_a_restart_round_trips(database: Path) -> None:
    with db.connect(database) as conn:
        db.insert_restart(conn, Restart(at(5), expected=True, reason="scheduled"))

    with db.connect(database) as conn:
        stored = db.latest_restart(conn)

    assert stored is not None
    assert stored.expected is True
    assert stored.reason == "scheduled"


def test_an_unexpected_restart_round_trips(database: Path) -> None:
    """Requirement 6: power loss and crashes are recorded, not inferred later."""
    with db.connect(database) as conn:
        db.insert_restart(conn, Restart(at(5), expected=False, reason="no marker"))

    with db.connect(database) as conn:
        stored = db.latest_restart(conn)

    assert stored is not None
    assert stored.expected is False


def test_a_restart_needs_no_reason(database: Path) -> None:
    with db.connect(database) as conn:
        db.insert_restart(conn, Restart(at(5), expected=True, reason=None))

    with db.connect(database) as conn:
        stored = db.latest_restart(conn)

    assert stored is not None
    assert stored.reason is None


def test_latest_restart_is_the_most_recent_row(database: Path) -> None:
    """By timestamp, not by insertion order.

    The older row is deliberately written last: the startup check asks whether
    anything was recorded since this boot, so answering with whichever row
    SQLite reaches first would read a pre-reboot marker as belonging to now.
    """
    with db.connect(database) as conn:
        db.insert_restart(conn, Restart(at(5), expected=True, reason="newer"))
        db.insert_restart(conn, Restart(at(600), expected=False, reason="older"))

    with db.connect(database) as conn:
        stored = db.latest_restart(conn)

    assert stored is not None
    assert stored.reason == "newer"


def test_latest_restart_is_none_before_anything_is_recorded(database: Path) -> None:
    with db.connect(database) as conn:
        assert db.latest_restart(conn) is None


def test_the_stored_timestamp_survives_the_round_trip(database: Path) -> None:
    """The startup check compares this against boot time, so it must be exact."""
    when = at(90)

    with db.connect(database) as conn:
        db.insert_restart(conn, Restart(when, expected=True, reason=None))

    with db.connect(database) as conn:
        stored = db.latest_restart(conn)

    assert stored is not None
    assert stored.timestamp == when
