"""Tests for the dashboard's latest-speed-test panel and its API."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from bbmon import db
from bbmon.config import Config
from bbmon.models import SpeedtestResult
from bbmon.web.app import create_app


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


def store(database: Path, *results: SpeedtestResult) -> None:
    with db.connect(database) as conn:
        db.insert_speedtest_results(conn, list(results))


def a_result(minutes_ago: float, download: float = 48.5) -> SpeedtestResult:
    return SpeedtestResult(
        timestamp=at(minutes_ago),
        download_mbps=download,
        upload_mbps=9.4,
        ping_ms=14.2,
        isp="Example Broadband",
        server="Example Telecom (London)",
        success=True,
    )


def test_the_api_returns_the_latest_result(
    client: FlaskClient, database: Path
) -> None:
    store(database, a_result(90, download=20.0), a_result(5, download=48.5))

    payload = client.get("/api/speedtest/latest").get_json()

    assert payload["download_mbps"] == 48.5
    assert payload["upload_mbps"] == 9.4
    assert payload["ping_ms"] == 14.2
    assert payload["isp"] == "Example Broadband"
    assert payload["server"] == "Example Telecom (London)"
    assert payload["success"] is True


def test_the_api_reports_when_no_speed_test_has_run(client: FlaskClient) -> None:
    """The dashboard is up before the first test completes, and says so."""
    response = client.get("/api/speedtest/latest")

    assert response.status_code == 200
    assert response.get_json()["result"] is None


def test_a_failed_run_is_reported_as_a_failure_not_as_missing_data(
    client: FlaskClient, database: Path
) -> None:
    """Requirement 5's whole point: a gap and a failure must look different."""
    store(database, SpeedtestResult(at(2), None, None, None, None, None, False))

    payload = client.get("/api/speedtest/latest").get_json()

    assert payload["result"] is not None
    assert payload["success"] is False
    assert payload["download_mbps"] is None


def test_a_failure_does_not_leave_an_older_success_showing_as_current(
    client: FlaskClient, database: Path
) -> None:
    store(database, a_result(120), SpeedtestResult(at(2), None, None, None, None, None, False))

    payload = client.get("/api/speedtest/latest").get_json()

    assert payload["success"] is False
    assert payload["download_mbps"] is None


def test_the_timestamp_is_returned_so_the_page_can_show_its_age(
    client: FlaskClient, database: Path
) -> None:
    store(database, a_result(5))

    payload = client.get("/api/speedtest/latest").get_json()

    assert datetime.fromisoformat(payload["timestamp"]).tzinfo is not None


def test_the_dashboard_page_has_the_speed_test_panel(client: FlaskClient) -> None:
    body = client.get("/").get_data(as_text=True)

    assert 'id="speedtest-panel"' in body


def test_the_speed_test_panel_precedes_the_latency_chart(
    client: FlaskClient,
) -> None:
    """Requirement 7 asks for it near the top, not buried below the chart."""
    body = client.get("/").get_data(as_text=True)

    assert body.index("speedtest-panel") < body.index("latency-chart")
