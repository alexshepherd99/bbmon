"""SQLite access layer shared by every bbmon service.

The services never talk to each other directly — this database file is the only
thing they share. That makes concurrent access the normal case rather than the
exception, so connections use write-ahead logging and a busy timeout.

All SQL here is parameterised; no value is ever interpolated into a statement.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from bbmon.models import HourlyPingSummary, PingResult, Restart, SpeedtestResult

logger = logging.getLogger(__name__)

#: Bumped whenever the schema below changes. Recorded in ``PRAGMA user_version``
#: so a future migration mechanism has something to work from; phase 1 only
#: refuses to run against a version it does not recognise.
SCHEMA_VERSION = 1

#: How long a writer waits for a lock held by another service before failing.
BUSY_TIMEOUT_MS = 5000

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS ping_results (
        id         INTEGER PRIMARY KEY,
        timestamp  TEXT    NOT NULL,
        target     TEXT    NOT NULL,
        latency_ms REAL,
        success    INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_ping_results_timestamp
        ON ping_results (timestamp)
    """,
    """
    CREATE TABLE IF NOT EXISTS speedtest_results (
        id            INTEGER PRIMARY KEY,
        timestamp     TEXT    NOT NULL,
        download_mbps REAL,
        upload_mbps   REAL,
        ping_ms       REAL,
        isp           TEXT,
        server        TEXT,
        success       INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_speedtest_results_timestamp
        ON speedtest_results (timestamp)
    """,
    """
    CREATE TABLE IF NOT EXISTS restarts (
        id        INTEGER PRIMARY KEY,
        timestamp TEXT    NOT NULL,
        expected  INTEGER NOT NULL,
        reason    TEXT
    )
    """,
)


class DatabaseError(Exception):
    """Raised when the database cannot be opened or is not the expected schema."""


def initialise(path: str | Path) -> None:
    """Create the database and its schema if they do not already exist.

    Safe to run repeatedly: a service may call this on every start. All three
    tables are created together, even though phase 1's M1 only writes pings, so
    later milestones need no schema change.

    :raises DatabaseError: if the file exists but was written by a different
        schema version.
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        logger.error("Could not create database directory %s: %s", path.parent, error)
        raise DatabaseError(f"Could not create database directory {path.parent}: {error}")

    with connect(path) as conn:
        existing = conn.execute("PRAGMA user_version").fetchone()[0]
        if existing not in (0, SCHEMA_VERSION):
            logger.error(
                "Database %s has schema version %s, expected %s",
                path,
                existing,
                SCHEMA_VERSION,
            )
            raise DatabaseError(
                f"Database {path} has schema version {existing}, "
                f"but this build expects schema version {SCHEMA_VERSION}"
            )

        for statement in _SCHEMA_STATEMENTS:
            conn.execute(statement)
        # A pragma value cannot be bound as a parameter; SCHEMA_VERSION is an
        # int constant defined above, never external input.
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


@contextmanager
def connect(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a connection configured for concurrent multi-service access."""
    try:
        conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    except sqlite3.Error as error:
        logger.error("Could not open database %s: %s", path, error)
        raise DatabaseError(f"Could not open database {path}: {error}")

    try:
        # sqlite3.connect's timeout= above is the busy timeout; setting the
        # pragma as well would be the same value written twice.
        conn.execute("PRAGMA journal_mode = WAL")
        yield conn
    finally:
        conn.close()


def insert_ping_results(
    conn: sqlite3.Connection, results: Sequence[PingResult]
) -> None:
    """Write a batch of ping results.

    The pinger buffers and flushes rather than writing on every ping, to limit
    SD card wear, so batches are the normal case.
    """
    if not results:
        return

    try:
        conn.executemany(
            "INSERT INTO ping_results (timestamp, target, latency_ms, success) "
            "VALUES (?, ?, ?, ?)",
            [
                (
                    result.timestamp.isoformat(),
                    result.target,
                    result.latency_ms,
                    int(result.success),
                )
                for result in results
            ],
        )
        conn.commit()
    except sqlite3.Error as error:
        logger.error("Could not write %d ping results: %s", len(results), error)
        raise DatabaseError(f"Could not write ping results: {error}")


def insert_speedtest_results(
    conn: sqlite3.Connection, results: Sequence[SpeedtestResult]
) -> None:
    """Write a batch of speed test results.

    A batch, for symmetry with the ping path and the collector interface, though
    a speed test produces one row every few hours rather than many per minute.
    """
    if not results:
        return

    try:
        conn.executemany(
            "INSERT INTO speedtest_results "
            "(timestamp, download_mbps, upload_mbps, ping_ms, isp, server, success) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    result.timestamp.isoformat(),
                    result.download_mbps,
                    result.upload_mbps,
                    result.ping_ms,
                    result.isp,
                    result.server,
                    int(result.success),
                )
                for result in results
            ],
        )
        conn.commit()
    except sqlite3.Error as error:
        logger.error("Could not write %d speed test results: %s", len(results), error)
        raise DatabaseError(f"Could not write speed test results: {error}")


def latest_speedtest_result(conn: sqlite3.Connection) -> SpeedtestResult | None:
    """Return the most recent speed test, or ``None`` if none has ever run.

    "Most recent" is by timestamp rather than by insertion order, so a row
    written out of order cannot leave an older result showing as current. A
    failed run counts: the newest result is reported whether or not it worked,
    otherwise a stale success would sit on the dashboard through an outage.
    """
    try:
        row = conn.execute(
            "SELECT timestamp, download_mbps, upload_mbps, ping_ms, isp, server, "
            "success FROM speedtest_results ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error as error:
        logger.error("Could not read the latest speed test result: %s", error)
        raise DatabaseError(f"Could not read the latest speed test result: {error}")

    if row is None:
        return None

    timestamp, download_mbps, upload_mbps, ping_ms, isp, server, success = row
    return SpeedtestResult(
        timestamp=datetime.fromisoformat(timestamp),
        download_mbps=download_mbps,
        upload_mbps=upload_mbps,
        ping_ms=ping_ms,
        isp=isp,
        server=server,
        success=bool(success),
    )


def insert_restart(conn: sqlite3.Connection, restart: Restart) -> None:
    """Record one restart.

    Singular where the result inserts are batched: restarts arrive one at a
    time, minutes or days apart, and the expected ones are written immediately
    before a reboot — the one write in this application that has no next
    opportunity to retry.
    """
    try:
        conn.execute(
            "INSERT INTO restarts (timestamp, expected, reason) VALUES (?, ?, ?)",
            (restart.timestamp.isoformat(), int(restart.expected), restart.reason),
        )
        conn.commit()
    except sqlite3.Error as error:
        logger.error("Could not record a restart: %s", error)
        raise DatabaseError(f"Could not record a restart: {error}")


def latest_restart(conn: sqlite3.Connection) -> Restart | None:
    """Return the most recently recorded restart, or ``None`` if there is none.

    By timestamp rather than insertion order, because the startup check asks
    whether anything was recorded since the machine booted and an out-of-order
    row would otherwise answer for a different boot.
    """
    try:
        row = conn.execute(
            "SELECT timestamp, expected, reason FROM restarts "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error as error:
        logger.error("Could not read the latest restart: %s", error)
        raise DatabaseError(f"Could not read the latest restart: {error}")

    if row is None:
        return None

    timestamp, expected, reason = row
    return Restart(
        timestamp=datetime.fromisoformat(timestamp),
        expected=bool(expected),
        reason=reason,
    )


def recent_ping_results(
    conn: sqlite3.Connection, since: datetime
) -> list[PingResult]:
    """Return every ping result recorded at or after ``since``, oldest first."""
    try:
        rows = conn.execute(
            "SELECT timestamp, target, latency_ms, success FROM ping_results "
            "WHERE timestamp >= ? ORDER BY timestamp",
            (since.isoformat(),),
        ).fetchall()
    except sqlite3.Error as error:
        logger.error("Could not read ping results: %s", error)
        raise DatabaseError(f"Could not read ping results: {error}")

    return [
        PingResult(
            timestamp=datetime.fromisoformat(timestamp),
            target=target,
            latency_ms=latency_ms,
            success=bool(success),
        )
        for timestamp, target, latency_ms, success in rows
    ]


#: Buckets a stored timestamp into its clock hour. Every timestamp is written
#: by ``datetime.isoformat()`` in UTC, so the first 13 characters are always
#: ``YYYY-MM-DDTHH`` and string truncation is a correct hour key.
_HOUR_KEY = "substr(timestamp, 1, 13)"

_HOUR_KEY_FORMAT = "%Y-%m-%dT%H"

#: Nearest-rank quartiles over each bucket's latencies.
#:
#: ``ROW_NUMBER`` sorts each (hour, target) group by latency and ``COUNT`` gives
#: the group's size, so the sample at any quantile is the one whose position
#: equals that fraction of the total, rounded up. Integer division makes that
#: ceiling directly: ``ceil(a/b)`` is ``(a + b - 1) / b``.
#:
#: Only rows carrying a latency take part. A failed ping has none, so including
#: it would either drag the box down to zero or need a null filtered out later;
#: ``count`` therefore means "samples in the box", not "pings attempted".
_HOURLY_PING_SUMMARY_SQL = f"""
    WITH ranked AS (
        SELECT
            {_HOUR_KEY} AS hour,
            target,
            latency_ms,
            ROW_NUMBER() OVER (
                PARTITION BY {_HOUR_KEY}, target ORDER BY latency_ms
            ) AS position,
            COUNT(*) OVER (PARTITION BY {_HOUR_KEY}, target) AS total
        FROM ping_results
        WHERE timestamp >= ? AND latency_ms IS NOT NULL
    )
    SELECT
        hour,
        target,
        total,
        MIN(latency_ms) AS low,
        MAX(CASE WHEN position = (total + 3) / 4 THEN latency_ms END) AS q1,
        MAX(CASE WHEN position = (total + 1) / 2 THEN latency_ms END) AS median,
        MAX(CASE WHEN position = (total * 3 + 3) / 4 THEN latency_ms END) AS q3,
        MAX(latency_ms) AS high
    FROM ranked
    GROUP BY hour, target, total
    -- Removing this leaves the tests green, because SQLite's GROUP BY happens
    -- to emit groups in key order. That makes the ordering an accident of the
    -- query plan rather than a guarantee, which is exactly why it stays.
    ORDER BY hour, target
"""


def hourly_ping_summary(
    conn: sqlite3.Connection, since: datetime
) -> list[HourlyPingSummary]:
    """Summarise latency per target per clock hour, for the long-term chart.

    The aggregation happens in SQLite rather than in Python because requirement
    10 asks the chart queries to stay responsive on a Pi 3: a day of pings is
    tens of thousands of rows, and only the resulting handful of boxes needs to
    cross into the application.

    Buckets come back oldest first, and alphabetically by target within an
    hour, which is the order the chart plots them in.

    :param since: Ignore samples recorded before this moment.
    """
    try:
        rows = conn.execute(
            _HOURLY_PING_SUMMARY_SQL, (since.isoformat(),)
        ).fetchall()
    except sqlite3.Error as error:
        logger.error("Could not summarise ping results: %s", error)
        raise DatabaseError(f"Could not summarise ping results: {error}")

    return [
        HourlyPingSummary(
            hour=datetime.strptime(hour, _HOUR_KEY_FORMAT).replace(tzinfo=timezone.utc),
            target=target,
            count=total,
            low=low,
            q1=q1,
            median=median,
            q3=q3,
            high=high,
        )
        for hour, target, total, low, q1, median, q3, high in rows
    ]
