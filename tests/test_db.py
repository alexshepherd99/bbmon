"""Tests for the SQLite access layer."""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bbmon import db
from bbmon.models import PingResult, Restart, SpeedtestResult


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    return path


def table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as raw:
        rows = raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {name for (name,) in rows}


def at(minutes_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def days_ago(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def test_initialise_creates_all_three_tables(database: Path) -> None:
    assert {"ping_results", "speedtest_results", "restarts"} <= table_names(database)


def test_initialise_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "bbmon.db"

    db.initialise(path)

    assert path.exists()


def test_initialise_records_the_schema_version(database: Path) -> None:
    with sqlite3.connect(database) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_initialise_is_idempotent(database: Path) -> None:
    """Every service may run init on start, so a second run must be harmless."""
    with db.connect(database) as conn:
        db.insert_ping_results(conn, [PingResult(at(1), "8.8.8.8", 12.3, True)])

    db.initialise(database)

    with db.connect(database) as conn:
        assert len(db.recent_ping_results(conn, since=at(60))) == 1


def test_initialise_rejects_a_database_written_by_another_schema_version(
    database: Path,
) -> None:
    with sqlite3.connect(database) as raw:
        raw.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")

    with pytest.raises(db.DatabaseError, match="schema version"):
        db.initialise(database)


def test_connect_enables_write_ahead_logging(database: Path) -> None:
    """Several services read and write this file concurrently."""
    with db.connect(database) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_connect_sets_a_busy_timeout(database: Path) -> None:
    with db.connect(database) as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == db.BUSY_TIMEOUT_MS


def test_ping_results_round_trip(database: Path) -> None:
    written = PingResult(at(5), "8.8.8.8", 12.5, True)

    with db.connect(database) as conn:
        db.insert_ping_results(conn, [written])
        read_back = db.recent_ping_results(conn, since=at(60))

    assert len(read_back) == 1
    assert read_back[0].target == written.target
    assert read_back[0].latency_ms == written.latency_ms
    assert read_back[0].success is True
    assert read_back[0].timestamp == written.timestamp


def test_failed_pings_are_stored_with_no_latency(database: Path) -> None:
    with db.connect(database) as conn:
        db.insert_ping_results(conn, [PingResult(at(1), "1.1.1.1", None, False)])
        (result,) = db.recent_ping_results(conn, since=at(60))

    assert result.latency_ms is None
    assert result.success is False


def test_recent_ping_results_excludes_rows_before_the_window(database: Path) -> None:
    with db.connect(database) as conn:
        db.insert_ping_results(
            conn,
            [
                PingResult(at(180), "8.8.8.8", 10.0, True),
                PingResult(at(5), "8.8.8.8", 20.0, True),
            ],
        )
        results = db.recent_ping_results(conn, since=at(120))

    assert [r.latency_ms for r in results] == [20.0]


def test_recent_ping_results_are_ordered_oldest_first(database: Path) -> None:
    """The dashboard plots them in order, so the query must not rely on rowid."""
    with db.connect(database) as conn:
        db.insert_ping_results(
            conn,
            [
                PingResult(at(1), "8.8.8.8", 30.0, True),
                PingResult(at(30), "8.8.8.8", 10.0, True),
                PingResult(at(15), "8.8.8.8", 20.0, True),
            ],
        )
        results = db.recent_ping_results(conn, since=at(60))

    assert [r.latency_ms for r in results] == [10.0, 20.0, 30.0]


def test_insert_writes_every_row_in_one_batch(database: Path) -> None:
    """The pinger buffers and flushes, so multi-row inserts are the normal path."""
    rows = [PingResult(at(i), f"host{i}.example.com", float(i), True) for i in range(50)]

    with db.connect(database) as conn:
        db.insert_ping_results(conn, rows)
        assert len(db.recent_ping_results(conn, since=at(120))) == 50


def test_insert_of_no_rows_is_harmless(database: Path) -> None:
    with db.connect(database) as conn:
        db.insert_ping_results(conn, [])
        assert db.recent_ping_results(conn, since=at(60)) == []


def test_sql_metacharacters_in_a_target_are_stored_literally(database: Path) -> None:
    """Proves the insert is parameterised rather than interpolated."""
    hostile = "'); DROP TABLE ping_results; --"

    with db.connect(database) as conn:
        db.insert_ping_results(conn, [PingResult(at(1), hostile, 1.0, True)])
        (result,) = db.recent_ping_results(conn, since=at(60))

    assert result.target == hostile
    assert "ping_results" in table_names(database)


def test_purge_deletes_pings_older_than_the_cutoff(database: Path) -> None:
    with db.connect(database) as conn:
        db.insert_ping_results(
            conn,
            [
                PingResult(days_ago(40), "8.8.8.8", 10.0, True),
                PingResult(days_ago(31), "8.8.8.8", 11.0, True),
                PingResult(days_ago(1), "8.8.8.8", 12.0, True),
            ],
        )

        db.purge_ping_results(conn, before=days_ago(30))

        remaining = db.recent_ping_results(conn, since=days_ago(365))

    assert [result.latency_ms for result in remaining] == [12.0]


def test_purge_keeps_a_ping_recorded_exactly_at_the_cutoff(database: Path) -> None:
    """The boundary is inclusive, so a full retention window is really kept."""
    cutoff = days_ago(30)

    with db.connect(database) as conn:
        db.insert_ping_results(conn, [PingResult(cutoff, "8.8.8.8", 10.0, True)])

        db.purge_ping_results(conn, before=cutoff)

        assert len(db.recent_ping_results(conn, since=days_ago(365))) == 1


def test_purge_reports_how_many_rows_it_deleted(database: Path) -> None:
    """The count is what the log line reports, and the only sign it ran."""
    with db.connect(database) as conn:
        db.insert_ping_results(
            conn,
            [PingResult(days_ago(40), "8.8.8.8", float(n), True) for n in range(7)],
        )

        assert db.purge_ping_results(conn, before=days_ago(30)) == 7


def test_purge_leaves_speed_tests_and_restarts_alone(database: Path) -> None:
    """Requirement 3 retains speed tests indefinitely; only pings are purged."""
    with db.connect(database) as conn:
        db.insert_ping_results(conn, [PingResult(days_ago(40), "8.8.8.8", 1.0, True)])
        db.insert_speedtest_results(
            conn,
            [SpeedtestResult(days_ago(40), 50.0, 10.0, 12.0, None, None, True)],
        )
        db.insert_restart(conn, Restart(days_ago(40), True, "an old reboot"))

        db.purge_ping_results(conn, before=days_ago(30))

        assert db.recent_ping_results(conn, since=days_ago(365)) == []
        assert len(db.speedtest_history(conn, since=days_ago(365))) == 1
        assert len(db.recent_restarts(conn, limit=10)) == 1


def test_purge_of_nothing_is_harmless(database: Path) -> None:
    """The normal case for most of a retention window's length."""
    with db.connect(database) as conn:
        db.insert_ping_results(conn, [PingResult(days_ago(1), "8.8.8.8", 1.0, True)])

        assert db.purge_ping_results(conn, before=days_ago(30)) == 0
        assert len(db.recent_ping_results(conn, since=days_ago(365))) == 1


def test_purge_raises_when_the_table_is_gone(database: Path) -> None:
    with db.connect(database) as conn:
        conn.execute("DROP TABLE ping_results")

        with pytest.raises(db.DatabaseError, match="purge"):
            db.purge_ping_results(conn, before=days_ago(30))
