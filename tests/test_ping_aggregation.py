"""Tests for the hourly ping aggregation behind the long-term box plot.

Timestamps here are absolute rather than relative to now. The whole point of
this query is which hour a sample falls in, so a fixture built from
``now - 5 minutes`` would straddle an hour boundary whenever the suite happened
to run near the top of one.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bbmon import db
from bbmon.models import PingResult

#: A fixed hour, so bucket boundaries are a property of the data and not of the
#: clock the suite runs on.
HOUR = datetime(2026, 3, 4, 14, 0, tzinfo=timezone.utc)
EARLIER = HOUR - timedelta(hours=1)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    return path


def sample(when: datetime, target: str, latency_ms: float | None) -> PingResult:
    return PingResult(
        timestamp=when,
        target=target,
        latency_ms=latency_ms,
        success=latency_ms is not None,
    )


def write(database: Path, results: list[PingResult]) -> None:
    with db.connect(database) as conn:
        db.insert_ping_results(conn, results)


def summarise(database: Path, since: datetime = EARLIER):
    with db.connect(database) as conn:
        return db.hourly_ping_summary(conn, since=since)


def test_quartiles_come_from_the_sorted_latencies(database: Path) -> None:
    """Eight evenly spaced samples put every box value on a different number.

    Nearest-rank quartiles of 10..80 are min 10, q1 20, median 40, q3 60,
    max 80 — so a query returning the wrong statistic cannot coincide with the
    right one.
    """
    write(
        database,
        [
            sample(HOUR + timedelta(minutes=i), "8.8.8.8", latency)
            # Deliberately not in sorted order, so the query has to sort rather
            # than inherit the insertion order.
            for i, latency in enumerate([50.0, 10.0, 80.0, 30.0, 60.0, 20.0, 70.0, 40.0])
        ],
    )

    (bucket,) = summarise(database)

    assert bucket.target == "8.8.8.8"
    assert bucket.hour == HOUR
    assert bucket.count == 8
    assert (bucket.low, bucket.q1, bucket.median, bucket.q3, bucket.high) == (
        10.0,
        20.0,
        40.0,
        60.0,
        80.0,
    )


def test_a_single_sample_gives_a_flat_box(database: Path) -> None:
    write(database, [sample(HOUR, "8.8.8.8", 42.0)])

    (bucket,) = summarise(database)

    assert bucket.count == 1
    assert (bucket.low, bucket.q1, bucket.median, bucket.q3, bucket.high) == (
        42.0,
        42.0,
        42.0,
        42.0,
        42.0,
    )


def test_samples_are_bucketed_by_hour(database: Path) -> None:
    write(
        database,
        [
            sample(HOUR + timedelta(minutes=1), "8.8.8.8", 10.0),
            sample(HOUR + timedelta(minutes=59), "8.8.8.8", 20.0),
            sample(HOUR + timedelta(minutes=61), "8.8.8.8", 90.0),
        ],
    )

    first, second = summarise(database)

    assert (first.hour, first.count, first.median) == (HOUR, 2, 10.0)
    assert (second.hour, second.count, second.median) == (
        HOUR + timedelta(hours=1),
        1,
        90.0,
    )


def test_each_target_gets_its_own_box_within_an_hour(database: Path) -> None:
    write(
        database,
        [
            sample(HOUR, "1.1.1.1", 30.0),
            sample(HOUR, "8.8.8.8", 10.0),
        ],
    )

    buckets = summarise(database)

    assert [(b.target, b.median) for b in buckets] == [("1.1.1.1", 30.0), ("8.8.8.8", 10.0)]


def test_failed_pings_contribute_no_sample(database: Path) -> None:
    """A failure has no latency, so it must not become a zero in the box."""
    write(
        database,
        [
            sample(HOUR, "8.8.8.8", 10.0),
            sample(HOUR + timedelta(minutes=1), "8.8.8.8", None),
            sample(HOUR + timedelta(minutes=2), "8.8.8.8", 20.0),
        ],
    )

    (bucket,) = summarise(database)

    assert bucket.count == 2
    assert bucket.low == 10.0
    assert bucket.high == 20.0


def test_an_hour_of_nothing_but_failures_produces_no_box(database: Path) -> None:
    write(database, [sample(HOUR, "8.8.8.8", None)])

    assert summarise(database) == []


def test_samples_before_the_window_are_excluded(database: Path) -> None:
    write(
        database,
        [
            sample(EARLIER, "8.8.8.8", 99.0),
            sample(HOUR, "8.8.8.8", 10.0),
        ],
    )

    (bucket,) = summarise(database, since=HOUR)

    assert bucket.hour == HOUR
    assert bucket.high == 10.0


def test_buckets_are_returned_oldest_first(database: Path) -> None:
    """The chart plots these straight onto a category axis, in order."""
    write(
        database,
        [
            sample(HOUR + timedelta(hours=2), "8.8.8.8", 30.0),
            sample(HOUR, "8.8.8.8", 10.0),
            sample(HOUR + timedelta(hours=1), "8.8.8.8", 20.0),
        ],
    )

    assert [bucket.median for bucket in summarise(database)] == [10.0, 20.0, 30.0]


def test_hours_are_grouped_together_before_targets(database: Path) -> None:
    """Hour is the primary sort, target the secondary.

    Two targets over two hours is the smallest fixture that can tell the two
    orderings apart: sorting by target first would return both of one target's
    hours before the other target appeared at all, which on a category axis
    reads as a chart of the wrong shape.
    """
    later = HOUR + timedelta(hours=1)
    write(
        database,
        [
            sample(later, "8.8.8.8", 40.0),
            sample(HOUR, "8.8.8.8", 20.0),
            sample(later, "1.1.1.1", 30.0),
            sample(HOUR, "1.1.1.1", 10.0),
        ],
    )

    assert [(b.hour, b.target) for b in summarise(database)] == [
        (HOUR, "1.1.1.1"),
        (HOUR, "8.8.8.8"),
        (later, "1.1.1.1"),
        (later, "8.8.8.8"),
    ]


def test_no_pings_at_all_gives_no_buckets(database: Path) -> None:
    assert summarise(database) == []
