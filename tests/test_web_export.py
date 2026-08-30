"""Tests for requirement 8's CSV download of ping and speed test data.

The export is the one route that can return more rows than fit in memory on a
Pi — a full retention window of pings is over a million of them — so these
cover the streaming contract as well as the content.
"""

import csv
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from bbmon import db
from bbmon.config import Config
from bbmon.models import PingResult, SpeedtestResult
from bbmon.web import export
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


def at(*, day: int, hour: int = 12) -> datetime:
    """A moment on a fixed day in 2026, so ranges can be written literally."""
    return datetime(2026, 8, day, hour, tzinfo=timezone.utc)


def store_pings(database: Path, *results: PingResult) -> None:
    with db.connect(database) as conn:
        db.insert_ping_results(conn, list(results))


def store_speedtests(database: Path, *results: SpeedtestResult) -> None:
    with db.connect(database) as conn:
        db.insert_speedtest_results(conn, list(results))


def ping(timestamp: datetime, target: str = "8.8.8.8", latency_ms=12.5) -> PingResult:
    return PingResult(
        timestamp=timestamp,
        target=target,
        latency_ms=latency_ms,
        success=latency_ms is not None,
    )


def speedtest(timestamp: datetime, download_mbps=95.0) -> SpeedtestResult:
    return SpeedtestResult(
        timestamp=timestamp,
        download_mbps=download_mbps,
        upload_mbps=18.0,
        ping_ms=14.0,
        isp="An ISP",
        server="A server",
        success=download_mbps is not None,
    )


def rows_of(response) -> list[list[str]]:
    """Parse a CSV response into its rows, header included."""
    return list(csv.reader(StringIO(response.get_data(as_text=True))))


def test_the_ping_export_has_a_header_and_one_row_per_ping(
    client: FlaskClient, database: Path
) -> None:
    store_pings(database, ping(at(day=10)), ping(at(day=11), target="1.1.1.1"))

    response = client.get("/export/ping.csv?start=2026-08-10&end=2026-08-11")

    assert response.status_code == 200
    assert rows_of(response) == [
        ["timestamp", "target", "latency_ms", "success"],
        [at(day=10).isoformat(), "8.8.8.8", "12.5", "true"],
        [at(day=11).isoformat(), "1.1.1.1", "12.5", "true"],
    ]


def test_the_ping_export_is_served_as_a_csv_download(
    client: FlaskClient, database: Path
) -> None:
    store_pings(database, ping(at(day=10)))

    response = client.get("/export/ping.csv?start=2026-08-10&end=2026-08-11")

    assert response.mimetype == "text/csv"
    disposition = response.headers["Content-Disposition"]
    assert disposition.startswith("attachment;")
    assert "bbmon-ping-2026-08-10-to-2026-08-11.csv" in disposition


def test_the_ping_export_excludes_rows_outside_the_range(
    client: FlaskClient, database: Path
) -> None:
    store_pings(
        database,
        ping(at(day=9, hour=23), target="before"),
        ping(at(day=10, hour=0), target="inside"),
        ping(at(day=12, hour=0), target="after"),
    )

    response = client.get("/export/ping.csv?start=2026-08-10&end=2026-08-11")

    assert [row[1] for row in rows_of(response)[1:]] == ["inside"]


def test_the_end_date_includes_that_whole_day(
    client: FlaskClient, database: Path
) -> None:
    """A range ending on the 11th means "up to the end of the 11th"."""
    store_pings(database, ping(at(day=11, hour=23), target="late on the last day"))

    response = client.get("/export/ping.csv?start=2026-08-10&end=2026-08-11")

    assert [row[1] for row in rows_of(response)[1:]] == ["late on the last day"]


def test_a_failed_ping_exports_an_empty_latency(
    client: FlaskClient, database: Path
) -> None:
    store_pings(database, ping(at(day=10), latency_ms=None))

    response = client.get("/export/ping.csv?start=2026-08-10&end=2026-08-10")

    assert rows_of(response)[1] == [at(day=10).isoformat(), "8.8.8.8", "", "false"]


def test_the_speedtest_export_carries_every_recorded_field(
    client: FlaskClient, database: Path
) -> None:
    store_speedtests(database, speedtest(at(day=10)))

    response = client.get("/export/speedtest.csv?start=2026-08-10&end=2026-08-10")

    assert response.status_code == 200
    assert rows_of(response) == [
        [
            "timestamp",
            "download_mbps",
            "upload_mbps",
            "ping_ms",
            "isp",
            "server",
            "success",
        ],
        [at(day=10).isoformat(), "95.0", "18.0", "14.0", "An ISP", "A server", "true"],
    ]


def test_the_speedtest_export_respects_the_range(
    client: FlaskClient, database: Path
) -> None:
    store_speedtests(database, speedtest(at(day=9)), speedtest(at(day=10)))

    response = client.get("/export/speedtest.csv?start=2026-08-10&end=2026-08-10")

    assert len(rows_of(response)) == 2


def test_an_export_of_an_empty_range_is_a_header_and_nothing_else(
    client: FlaskClient, database: Path
) -> None:
    """An empty CSV, not a 404: the range is valid, there is simply no data."""
    response = client.get("/export/ping.csv?start=2026-08-10&end=2026-08-11")

    assert response.status_code == 200
    assert len(rows_of(response)) == 1


@pytest.mark.parametrize("path", ["/export/ping.csv", "/export/speedtest.csv"])
@pytest.mark.parametrize(
    "query",
    [
        "",
        "start=2026-08-10",
        "end=2026-08-11",
        "start=2026-08-10&end=not-a-date",
        "start=10/08/2026&end=2026-08-11",
        "start=2026-08-32&end=2026-08-11",
        "start=2026-08-11&end=2026-08-10",
    ],
)
def test_a_bad_date_range_is_rejected(
    client: FlaskClient, path: str, query: str
) -> None:
    assert client.get(f"{path}?{query}").status_code == 400


def test_an_export_is_never_served_from_the_cache(
    client: FlaskClient, database: Path
) -> None:
    """Exports bypass the dashboard's cache: they are large and never polled.

    Caching them would also key the cache on a caller-chosen date range, which
    is exactly the unbounded growth :class:`TimedCache` warns against.
    """
    store_pings(database, ping(at(day=10), target="first"))
    client.get("/export/ping.csv?start=2026-08-10&end=2026-08-11")

    store_pings(database, ping(at(day=10, hour=13), target="second"))
    response = client.get("/export/ping.csv?start=2026-08-10&end=2026-08-11")

    assert [row[1] for row in rows_of(response)[1:]] == ["first", "second"]


def test_a_failed_read_is_an_error_rather_than_a_short_file(tmp_path: Path) -> None:
    """A download that stops early looks exactly like a complete one.

    So the query has to run before the first byte is sent. The database here
    is real but has no schema, which is the honest way to make the read fail
    without patching bbmon's own code.
    """
    app = create_app(Config(database_path=tmp_path / "never-initialised.db"))

    # TESTING is deliberately left off: it re-raises rather than handling, and
    # what is being checked is the response a browser would get.
    response = app.test_client().get("/export/ping.csv?start=2026-08-10&end=2026-08-11")

    assert response.status_code == 500


def test_rendering_does_not_pull_every_row_before_yielding_anything() -> None:
    """The reason this route exists in this shape.

    A full retention window is over a million pings; materialising one is what
    would take the web service down on a Pi 3.
    """
    pulled = []

    def rows():
        for index in range(10_000):
            pulled.append(index)
            yield (index,)

    body = export.csv_body(("n",), rows())
    next(body)

    assert pulled == [0]


def test_a_large_export_arrives_in_several_intact_chunks() -> None:
    """The chunk boundary is only crossed by exports too big to test through.

    A real download is hundreds of thousands of rows and any dev database is
    a fraction of one chunk, so without this the split — and the buffer reset
    that goes with it — would first be exercised on the Pi.
    """
    padding = "x" * 100
    rows = ((index, padding) for index in range(2_000))

    chunks = list(export.csv_body(("n", "pad"), rows))
    parsed = list(csv.reader(StringIO("".join(chunks))))

    assert len(chunks) > 1
    assert len(parsed) == 2_001
    assert parsed[-1] == ["1999", padding]


def test_an_export_response_does_not_declare_a_length(
    client: FlaskClient, database: Path
) -> None:
    """The route-level shadow of the same property.

    A response whose body has been built can be measured, and Werkzeug then
    sends a ``Content-Length``. Its absence is what says the rows are still
    being read as the browser receives them.
    """
    store_pings(database, ping(at(day=10)))

    response = client.get("/export/ping.csv?start=2026-08-10&end=2026-08-11")

    assert response.status_code == 200
    assert "Content-Length" not in response.headers
