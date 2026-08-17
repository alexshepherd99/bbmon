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


def write_restarts(database: Path, restarts: list[Restart]) -> None:
    with db.connect(database) as conn:
        for restart in restarts:
            db.insert_restart(conn, restart)


def test_recent_restarts_are_newest_first(database: Path) -> None:
    """The list reads top-down as "what happened most recently".

    The oldest is written last, so natural row order would give the wrong
    answer rather than accidentally the right one.
    """
    write_restarts(
        database,
        [
            Restart(at(5), expected=True, reason="newest"),
            Restart(at(600), expected=True, reason="oldest"),
            Restart(at(60), expected=True, reason="middle"),
        ],
    )

    with db.connect(database) as conn:
        listed = db.recent_restarts(conn, limit=10)

    assert [restart.reason for restart in listed] == ["newest", "middle", "oldest"]


def test_the_limit_keeps_the_newest_restarts(database: Path) -> None:
    write_restarts(
        database,
        [Restart(at(minutes), expected=True, reason=str(minutes)) for minutes in (5, 60, 600)],
    )

    with db.connect(database) as conn:
        listed = db.recent_restarts(conn, limit=2)

    assert [restart.reason for restart in listed] == ["5", "60"]


def test_expected_restarts_can_be_excluded(database: Path) -> None:
    """Requirement 7's toggle: hide the reboots bbmon asked for itself."""
    write_restarts(
        database,
        [
            Restart(at(5), expected=True, reason="scheduled"),
            Restart(at(60), expected=False, reason="power cut"),
        ],
    )

    with db.connect(database) as conn:
        listed = db.recent_restarts(conn, limit=10, include_expected=False)

    assert [restart.reason for restart in listed] == ["power cut"]


def test_the_limit_applies_after_expected_restarts_are_excluded(database: Path) -> None:
    """Otherwise the toggle would show an empty list on a healthy machine.

    Every recent restart being a scheduled one is the normal state, so a limit
    applied first would select 20 expected rows and then filter them all away
    — hiding the unexpected restarts the toggle exists to find.
    """
    write_restarts(
        database,
        [Restart(at(minutes), expected=True, reason="scheduled") for minutes in (5, 10, 15)]
        + [Restart(at(600), expected=False, reason="power cut")],
    )

    with db.connect(database) as conn:
        listed = db.recent_restarts(conn, limit=3, include_expected=False)

    assert [restart.reason for restart in listed] == ["power cut"]


def test_recent_restarts_of_an_empty_database_is_empty(database: Path) -> None:
    with db.connect(database) as conn:
        assert db.recent_restarts(conn, limit=10) == []


def test_the_stored_timestamp_survives_the_round_trip(database: Path) -> None:
    """The startup check compares this against boot time, so it must be exact."""
    when = at(90)

    with db.connect(database) as conn:
        db.insert_restart(conn, Restart(when, expected=True, reason=None))

    with db.connect(database) as conn:
        stored = db.latest_restart(conn)

    assert stored is not None
    assert stored.timestamp == when
