"""Tests for storing and reading speed test results."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bbmon import db
from bbmon.models import SpeedtestResult


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    return path


def at(minutes_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def result(minutes_ago: int, download: float = 48.5) -> SpeedtestResult:
    return SpeedtestResult(
        timestamp=at(minutes_ago),
        download_mbps=download,
        upload_mbps=9.4,
        ping_ms=14.2,
        isp="Example Broadband",
        server="London (example.net)",
        success=True,
    )


def failure(minutes_ago: int) -> SpeedtestResult:
    return SpeedtestResult(
        timestamp=at(minutes_ago),
        download_mbps=None,
        upload_mbps=None,
        ping_ms=None,
        isp=None,
        server=None,
        success=False,
    )


def test_history_returns_results_oldest_first(database: Path) -> None:
    """The chart plots these along a time axis, so order is the contract.

    The newest row is inserted first, so returning rows in natural order gives
    the wrong answer rather than coincidentally the right one.
    """
    with db.connect(database) as conn:
        db.insert_speedtest_results(
            conn,
            [result(5, download=10.0), result(600, download=20.0), result(300, download=30.0)],
        )

    with db.connect(database) as conn:
        history = db.speedtest_history(conn, since=at(1440))

    assert [row.download_mbps for row in history] == [20.0, 30.0, 10.0]


def test_history_excludes_results_before_the_window(database: Path) -> None:
    with db.connect(database) as conn:
        db.insert_speedtest_results(
            conn, [result(600, download=20.0), result(5, download=10.0)]
        )

    with db.connect(database) as conn:
        history = db.speedtest_history(conn, since=at(60))

    assert [row.download_mbps for row in history] == [10.0]


def test_history_includes_failed_runs(database: Path) -> None:
    """A failure is a gap in the line, which is data — requirement 5.

    Dropping these would draw the chart straight through an outage, which is
    the opposite of what recording failures was for.
    """
    with db.connect(database) as conn:
        db.insert_speedtest_results(conn, [failure(30), result(5, download=10.0)])

    with db.connect(database) as conn:
        history = db.speedtest_history(conn, since=at(60))

    assert [(row.success, row.download_mbps) for row in history] == [
        (False, None),
        (True, 10.0),
    ]


def test_history_of_an_empty_database_is_empty(database: Path) -> None:
    with db.connect(database) as conn:
        assert db.speedtest_history(conn, since=at(60)) == []


def test_speedtest_results_round_trip(database: Path) -> None:
    with db.connect(database) as conn:
        db.insert_speedtest_results(conn, [result(5)])

    with db.connect(database) as conn:
        stored = db.latest_speedtest_result(conn)

    assert stored is not None
    assert stored.download_mbps == 48.5
    assert stored.upload_mbps == 9.4
    assert stored.ping_ms == 14.2
    assert stored.isp == "Example Broadband"
    assert stored.server == "London (example.net)"
    assert stored.success is True


def test_latest_speedtest_result_is_the_most_recent_row(database: Path) -> None:
    """By timestamp, not by insertion order.

    The oldest row is deliberately inserted last, so returning whichever row
    SQLite happens to reach first gives the wrong answer. Without that, natural
    row order already matches timestamp order and the ordering is untested.
    """
    with db.connect(database) as conn:
        db.insert_speedtest_results(
            conn, [result(600, download=20.0), result(5, download=10.0)]
        )

    with db.connect(database) as conn:
        stored = db.latest_speedtest_result(conn)

    assert stored is not None
    assert stored.download_mbps == 10.0


def test_latest_speedtest_result_is_none_when_nothing_has_run(database: Path) -> None:
    """The dashboard renders before the first speed test has ever completed."""
    with db.connect(database) as conn:
        assert db.latest_speedtest_result(conn) is None


def test_a_failed_speedtest_is_stored_with_no_measurements(database: Path) -> None:
    """Requirement 5: a failure is a recorded row, never a silent gap."""
    failure = SpeedtestResult(
        timestamp=at(1),
        download_mbps=None,
        upload_mbps=None,
        ping_ms=None,
        isp=None,
        server=None,
        success=False,
    )

    with db.connect(database) as conn:
        db.insert_speedtest_results(conn, [failure])

    with db.connect(database) as conn:
        stored = db.latest_speedtest_result(conn)

    assert stored is not None
    assert stored.success is False
    assert stored.download_mbps is None
    assert stored.isp is None


def test_a_failed_speedtest_still_counts_as_the_latest_result(database: Path) -> None:
    """A failure must not leave a stale success showing as current."""
    failure = SpeedtestResult(at(1), None, None, None, None, None, False)

    with db.connect(database) as conn:
        db.insert_speedtest_results(conn, [result(30), failure])

    with db.connect(database) as conn:
        stored = db.latest_speedtest_result(conn)

    assert stored is not None
    assert stored.success is False


def test_insert_of_no_speedtest_rows_is_harmless(database: Path) -> None:
    with db.connect(database) as conn:
        db.insert_speedtest_results(conn, [])

    with db.connect(database) as conn:
        assert db.latest_speedtest_result(conn) is None


def test_sql_metacharacters_in_isp_metadata_are_stored_literally(
    database: Path,
) -> None:
    """ISP and server names come from Ookla's JSON, not from us."""
    hostile = SpeedtestResult(
        timestamp=at(1),
        download_mbps=1.0,
        upload_mbps=1.0,
        ping_ms=1.0,
        isp="'); DROP TABLE speedtest_results; --",
        server="O'Brien & Co",
        success=True,
    )

    with db.connect(database) as conn:
        db.insert_speedtest_results(conn, [hostile])

    with db.connect(database) as conn:
        stored = db.latest_speedtest_result(conn)

    assert stored is not None
    assert stored.isp == "'); DROP TABLE speedtest_results; --"
    assert stored.server == "O'Brien & Co"
