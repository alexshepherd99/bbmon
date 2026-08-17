"""Tests for the JSON contracts behind M5's charts and the restart list.

The charts themselves are browser-side; these cover what the routes return and
the caching in front of them.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from bbmon import db
from bbmon.config import Config
from bbmon.models import PingResult, Restart, SpeedtestResult
from bbmon.web.app import MAX_HISTORY_DAYS, SLOW_CACHE_TTL_SECONDS, create_app
from bbmon.web.cache import TimedCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    return path


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def client(database: Path, clock: FakeClock) -> FlaskClient:
    """A client whose cache expires only when the test says so.

    The cache is injected rather than patched, so these exercise the same code
    path the real app runs.
    """
    app = create_app(
        Config(database_path=database),
        cache=TimedCache(ttl_seconds=10, clock=clock),
    )
    app.config["TESTING"] = True
    return app.test_client()


def ago(**kwargs: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kwargs)


def store_pings(database: Path, *results: PingResult) -> None:
    with db.connect(database) as conn:
        db.insert_ping_results(conn, list(results))


def ping(minutes_ago: float, target: str = "8.8.8.8", latency: float | None = 10.0):
    return PingResult(ago(minutes=minutes_ago), target, latency, latency is not None)


def store_speedtests(database: Path, *results: SpeedtestResult) -> None:
    with db.connect(database) as conn:
        db.insert_speedtest_results(conn, list(results))


def speedtest(hours_ago: float, download: float = 48.5) -> SpeedtestResult:
    return SpeedtestResult(
        timestamp=ago(hours=hours_ago),
        download_mbps=download,
        upload_mbps=9.4,
        ping_ms=14.2,
        isp="Example Broadband",
        server="Example (example.net)",
        success=True,
    )


def store_restarts(database: Path, *restarts: Restart) -> None:
    with db.connect(database) as conn:
        for restart in restarts:
            db.insert_restart(conn, restart)


# --- hourly ping summary ---------------------------------------------------


def test_hourly_pings_return_one_bucket_per_target_per_hour(
    database: Path, client: FlaskClient
) -> None:
    store_pings(database, ping(5, "8.8.8.8", 10.0), ping(6, "1.1.1.1", 20.0))

    body = client.get("/api/ping/hourly").get_json()

    assert {bucket["target"] for bucket in body["buckets"]} == {"8.8.8.8", "1.1.1.1"}


def test_an_hourly_bucket_carries_the_five_box_values_and_a_count(
    database: Path, client: FlaskClient
) -> None:
    store_pings(database, ping(5, latency=10.0), ping(6, latency=20.0))

    (bucket,) = client.get("/api/ping/hourly").get_json()["buckets"]

    assert bucket["count"] == 2
    assert bucket["low"] == 10.0
    assert bucket["high"] == 20.0
    assert set(bucket) == {"hour", "target", "count", "low", "q1", "median", "q3", "high"}


def test_hourly_pings_report_the_window_they_cover(client: FlaskClient) -> None:
    """Requirement 7 fixes this chart at one day, so the page need not ask."""
    assert client.get("/api/ping/hourly").get_json()["hours"] == 24


def test_hourly_pings_exclude_samples_older_than_a_day(
    database: Path, client: FlaskClient
) -> None:
    store_pings(database, ping(minutes_ago=60 * 25, latency=99.0))

    assert client.get("/api/ping/hourly").get_json()["buckets"] == []


def test_the_window_covers_exactly_twenty_four_hour_buckets(
    database: Path, client: FlaskClient
) -> None:
    """A window measured from "now" straddles hour boundaries.

    Counting back 24 hours from part-way through an hour clips a sliver off
    the oldest hour and adds the current partial one, so the chart drew 25
    columns for a window labelled 24 hours — the first of them a box built
    from a fraction of an hour's pings, sitting beside full ones.
    """
    this_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    oldest = this_hour - timedelta(hours=23)
    store_pings(
        database,
        ping(minutes_ago=0.0),
        # One second either side of the oldest hour this window should reach.
        PingResult(oldest, "8.8.8.8", 20.0, True),
        PingResult(oldest - timedelta(seconds=1), "8.8.8.8", 30.0, True),
    )

    buckets = client.get("/api/ping/hourly").get_json()["buckets"]
    hours = {bucket["hour"] for bucket in buckets}

    assert len(hours) == 2
    assert oldest.isoformat() in hours
    assert (oldest - timedelta(hours=1)).isoformat() not in hours


def test_hourly_pings_with_no_data_return_an_empty_list(client: FlaskClient) -> None:
    response = client.get("/api/ping/hourly")

    assert response.status_code == 200
    assert response.get_json()["buckets"] == []


# --- speed test history ----------------------------------------------------


def test_speedtest_history_returns_results_oldest_first(
    database: Path, client: FlaskClient
) -> None:
    store_speedtests(database, speedtest(1, download=10.0), speedtest(20, download=20.0))

    body = client.get("/api/speedtest/history").get_json()

    assert [row["download_mbps"] for row in body["results"]] == [20.0, 10.0]


def test_speedtest_history_defaults_to_a_week(client: FlaskClient) -> None:
    assert client.get("/api/speedtest/history").get_json()["days"] == 7


def test_speedtest_history_honours_the_requested_range(
    database: Path, client: FlaskClient
) -> None:
    store_speedtests(database, speedtest(hours_ago=48, download=20.0), speedtest(1, download=10.0))

    body = client.get("/api/speedtest/history?days=1").get_json()

    assert body["days"] == 1
    assert [row["download_mbps"] for row in body["results"]] == [10.0]


def test_a_failed_speed_test_appears_with_no_readings(
    database: Path, client: FlaskClient
) -> None:
    """The gap is the point — an outage must not be smoothed over."""
    store_speedtests(
        database,
        SpeedtestResult(ago(hours=1), None, None, None, None, None, success=False),
    )

    (row,) = client.get("/api/speedtest/history").get_json()["results"]

    assert row["success"] is False
    assert row["download_mbps"] is None


@pytest.mark.parametrize("days", ["0", "-1", str(MAX_HISTORY_DAYS + 1)])
def test_an_out_of_range_history_length_is_rejected(
    client: FlaskClient, days: str
) -> None:
    response = client.get(f"/api/speedtest/history?days={days}")

    assert response.status_code == 400


def test_a_non_numeric_history_length_is_rejected(client: FlaskClient) -> None:
    assert client.get("/api/speedtest/history?days=lots").status_code == 400


# --- restarts --------------------------------------------------------------


def test_restarts_are_listed_newest_first(database: Path, client: FlaskClient) -> None:
    store_restarts(
        database,
        Restart(ago(hours=1), expected=True, reason="newest"),
        Restart(ago(hours=10), expected=True, reason="oldest"),
    )

    body = client.get("/api/restarts").get_json()

    assert [row["reason"] for row in body["restarts"]] == ["newest", "oldest"]


def test_a_restart_carries_its_timestamp_expectedness_and_reason(
    database: Path, client: FlaskClient
) -> None:
    store_restarts(database, Restart(ago(hours=1), expected=False, reason="power cut"))

    (row,) = client.get("/api/restarts").get_json()["restarts"]

    assert row["expected"] is False
    assert row["reason"] == "power cut"
    assert row["timestamp"].startswith(str(datetime.now(timezone.utc).year))


def test_expected_restarts_can_be_excluded(database: Path, client: FlaskClient) -> None:
    store_restarts(
        database,
        Restart(ago(hours=1), expected=True, reason="scheduled"),
        Restart(ago(hours=2), expected=False, reason="power cut"),
    )

    body = client.get("/api/restarts?include_expected=false").get_json()

    assert [row["reason"] for row in body["restarts"]] == ["power cut"]


def test_expected_restarts_are_included_by_default(
    database: Path, client: FlaskClient
) -> None:
    store_restarts(database, Restart(ago(hours=1), expected=True, reason="scheduled"))

    body = client.get("/api/restarts").get_json()

    assert [row["reason"] for row in body["restarts"]] == ["scheduled"]


def test_the_restart_list_length_comes_from_configuration(database: Path) -> None:
    app = create_app(Config(database_path=database, web_restart_limit=1))
    app.config["TESTING"] = True
    store_restarts(
        database,
        Restart(ago(hours=1), expected=True, reason="newest"),
        Restart(ago(hours=2), expected=True, reason="older"),
    )

    body = app.test_client().get("/api/restarts").get_json()

    assert body["limit"] == 1
    assert [row["reason"] for row in body["restarts"]] == ["newest"]


def test_an_unrecognised_expected_toggle_is_rejected(client: FlaskClient) -> None:
    """Anything but true or false is a mistake, and guessing would hide it."""
    assert client.get("/api/restarts?include_expected=maybe").status_code == 400


# --- caching ---------------------------------------------------------------


def test_a_repeated_request_is_served_from_the_cache(
    database: Path, client: FlaskClient
) -> None:
    """Requirement 10: repeated polling must not re-run the query.

    Observed through the route rather than by counting calls — data written
    between the two requests cannot appear in the second one if the first was
    cached.
    """
    store_pings(database, ping(5, latency=10.0))
    client.get("/api/ping/hourly")

    store_pings(database, ping(5, "1.1.1.1", 20.0))
    body = client.get("/api/ping/hourly").get_json()

    assert {bucket["target"] for bucket in body["buckets"]} == {"8.8.8.8"}


def test_the_cache_lets_new_data_through_once_it_expires(
    database: Path, client: FlaskClient, clock: FakeClock
) -> None:
    store_pings(database, ping(5, latency=10.0))
    client.get("/api/ping/hourly")

    store_pings(database, ping(5, "1.1.1.1", 20.0))
    clock.advance(SLOW_CACHE_TTL_SECONDS + 1)
    body = client.get("/api/ping/hourly").get_json()

    assert {bucket["target"] for bucket in body["buckets"]} == {"8.8.8.8", "1.1.1.1"}


def test_the_slow_panels_outlive_the_default_ttl(
    database: Path, client: FlaskClient, clock: FakeClock
) -> None:
    """The expensive queries are held for a poll interval, not ten seconds.

    Advancing past the cache's own TTL but not past the slow one: if the
    hourly summary fell back to the default, this new target would appear.
    """
    store_pings(database, ping(5, latency=10.0))
    client.get("/api/ping/hourly")

    store_pings(database, ping(5, "1.1.1.1", 20.0))
    clock.advance(11)
    body = client.get("/api/ping/hourly").get_json()

    assert {bucket["target"] for bucket in body["buckets"]} == {"8.8.8.8"}


def test_the_live_latency_chart_keeps_the_short_ttl(
    database: Path, client: FlaskClient, clock: FakeClock
) -> None:
    """The chart that updates every five seconds must not be held for minutes."""
    store_pings(database, ping(5, latency=10.0))
    client.get("/api/ping")

    store_pings(database, ping(5, "1.1.1.1", 20.0))
    clock.advance(11)
    body = client.get("/api/ping").get_json()

    assert set(body["targets"]) == {"8.8.8.8", "1.1.1.1"}


def test_different_history_ranges_do_not_share_a_cache_entry(
    database: Path, client: FlaskClient
) -> None:
    """A one-day request must not be answered with the 30-day result."""
    store_speedtests(database, speedtest(hours_ago=48, download=20.0), speedtest(1, download=10.0))

    week = client.get("/api/speedtest/history?days=7").get_json()
    day = client.get("/api/speedtest/history?days=1").get_json()

    assert len(week["results"]) == 2
    assert len(day["results"]) == 1


def test_the_restart_toggle_does_not_share_a_cache_entry(
    database: Path, client: FlaskClient
) -> None:
    store_restarts(
        database,
        Restart(ago(hours=1), expected=True, reason="scheduled"),
        Restart(ago(hours=2), expected=False, reason="power cut"),
    )

    everything = client.get("/api/restarts").get_json()
    unexpected = client.get("/api/restarts?include_expected=false").get_json()

    assert len(everything["restarts"]) == 2
    assert len(unexpected["restarts"]) == 1
