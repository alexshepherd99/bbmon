"""Tests for the dashboard's HTTP layer.

The chart itself is browser-side and is verified by running the app; these
cover the routes and the JSON contract the chart reads.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from bbmon import db
from bbmon.config import Config
from bbmon.models import PingResult
from bbmon.web.app import MAX_WINDOW_MINUTES, create_app


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    return path


@pytest.fixture
def client(database: Path) -> FlaskClient:
    app = create_app(Config(database_path=database))
    app.config["TESTING"] = True
    return app.test_client()


def at(minutes_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def store(database: Path, *results: PingResult) -> None:
    with db.connect(database) as conn:
        db.insert_ping_results(conn, list(results))


def test_the_dashboard_page_loads(client: FlaskClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert b"bbmon" in response.data


def test_the_dashboard_uses_the_vendored_chart_library(client: FlaskClient) -> None:
    """A CDN reference here would break the dashboard without internet access."""
    page = client.get("/").data.decode()

    assert "/static/vendor/echarts.min.js" in page
    assert "http://" not in page and "https://" not in page


def test_the_vendored_library_is_actually_served(client: FlaskClient) -> None:
    response = client.get("/static/vendor/echarts.min.js")

    assert response.status_code == 200


def test_the_api_returns_a_series_per_target(client: FlaskClient, database: Path) -> None:
    store(
        database,
        PingResult(at(5), "8.8.8.8", 12.0, True),
        PingResult(at(4), "1.1.1.1", 20.0, True),
        PingResult(at(3), "8.8.8.8", 14.0, True),
    )

    body = client.get("/api/ping").get_json()

    assert set(body["targets"]) == {"8.8.8.8", "1.1.1.1"}
    assert [point[1] for point in body["targets"]["8.8.8.8"]] == [12.0, 14.0]


def test_points_are_timestamp_and_latency_pairs(
    client: FlaskClient, database: Path
) -> None:
    when = at(5)
    store(database, PingResult(when, "8.8.8.8", 12.0, True))

    (point,) = client.get("/api/ping").get_json()["targets"]["8.8.8.8"]

    assert point[0] == pytest.approx(when.timestamp() * 1000, abs=1)
    assert point[1] == 12.0


def test_a_failed_ping_is_a_gap_rather_than_a_missing_point(
    client: FlaskClient, database: Path
) -> None:
    """A dropped point would join the line across the outage and hide it."""
    store(
        database,
        PingResult(at(5), "8.8.8.8", 12.0, True),
        PingResult(at(4), "8.8.8.8", None, False),
    )

    points = client.get("/api/ping").get_json()["targets"]["8.8.8.8"]

    assert [point[1] for point in points] == [12.0, None]


def test_the_window_defaults_to_two_hours(client: FlaskClient, database: Path) -> None:
    store(
        database,
        PingResult(at(60), "8.8.8.8", 10.0, True),
        PingResult(at(180), "8.8.8.8", 99.0, True),
    )

    body = client.get("/api/ping").get_json()

    assert body["window_minutes"] == 120
    assert [point[1] for point in body["targets"]["8.8.8.8"]] == [10.0]


def test_the_window_can_be_narrowed(client: FlaskClient, database: Path) -> None:
    store(
        database,
        PingResult(at(5), "8.8.8.8", 10.0, True),
        PingResult(at(45), "8.8.8.8", 99.0, True),
    )

    body = client.get("/api/ping?minutes=10").get_json()

    assert [point[1] for point in body["targets"]["8.8.8.8"]] == [10.0]


def test_no_data_yields_empty_series_rather_than_an_error(client: FlaskClient) -> None:
    response = client.get("/api/ping")

    assert response.status_code == 200
    assert response.get_json()["targets"] == {}


@pytest.mark.parametrize("value", ["0", "-5", "abc", "", "1e9", "9999999"])
def test_a_bad_window_is_rejected(client: FlaskClient, value: str) -> None:
    response = client.get(f"/api/ping?minutes={value}")

    assert response.status_code == 400


def test_the_window_is_capped(client: FlaskClient) -> None:
    """An unbounded window would let one request scan the whole table."""
    assert client.get(f"/api/ping?minutes={MAX_WINDOW_MINUTES}").status_code == 200
    assert client.get(f"/api/ping?minutes={MAX_WINDOW_MINUTES + 1}").status_code == 400


def test_debug_mode_is_off(database: Path) -> None:
    """The Werkzeug debugger is remote code execution to anyone on the LAN."""
    app = create_app(Config(database_path=database))

    assert app.debug is False
    assert app.config["DEBUG"] is False
