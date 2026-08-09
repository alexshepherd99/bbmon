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
from datetime import datetime
from pathlib import Path

from bbmon.models import PingResult

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
