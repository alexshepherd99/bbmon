"""Tests for the speed test service entrypoint."""

import json
from pathlib import Path

import pytest

from bbmon import db, speedtest
from bbmon.service import FLUSH_EVERY_CYCLE

CONFIG = """
speedtest:
  interval_hours: 6
database:
  path: {path}
"""


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.format(path=tmp_path / "bbmon.db"))
    monkeypatch.setenv("BBMON_CONFIG", str(path))
    return path


def test_the_service_configures_the_collector_from_the_config_file(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def fake_run(collector, database_path, flush_interval_seconds):
        captured["interval_seconds"] = collector.interval_seconds
        captured["flush"] = flush_interval_seconds
        captured["database_path"] = database_path
        return 0

    monkeypatch.setattr(speedtest, "run_until_stopped", fake_run)

    assert speedtest.main() == 0
    # Six hours, converted to the seconds the loop sleeps for.
    assert captured["interval_seconds"] == 6 * 3600


def test_the_service_does_not_buffer(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row every few hours held in memory is a row a crash loses."""
    captured = {}

    def fake_run(collector, database_path, flush_interval_seconds):
        captured["flush"] = flush_interval_seconds
        return 0

    monkeypatch.setattr(speedtest, "run_until_stopped", fake_run)
    speedtest.main()

    assert captured["flush"] == FLUSH_EVERY_CYCLE


def test_the_service_creates_the_database_before_collecting(
    config_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(speedtest, "run_until_stopped", lambda *a, **k: 0)

    speedtest.main()

    with db.connect(tmp_path / "bbmon.db") as conn:
        assert db.latest_speedtest_result(conn) is None


def test_a_bad_config_exits_non_zero_rather_than_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("speedtest:\n  interval_hours: -1\n")
    monkeypatch.setenv("BBMON_CONFIG", str(path))

    assert speedtest.main() == 1


def test_a_real_collector_run_reaches_the_database(
    config_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end bar the binary: canned Ookla JSON in, a stored row out."""
    import subprocess

    from bbmon.collectors import speedtest as collector_module

    payload = json.dumps(
        {
            "type": "result",
            "ping": {"latency": 11.0},
            "download": {"bandwidth": 6_062_500},
            "upload": {"bandwidth": 1_175_000},
            "isp": "Example Broadband",
            "server": {"name": "Example Telecom", "location": "London"},
        }
    )

    def fake_subprocess_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, payload, "")

    monkeypatch.setattr(collector_module.subprocess, "run", fake_subprocess_run)

    def run_one_cycle(collector, database_path, flush_interval_seconds):
        with db.connect(database_path) as conn:
            collector.store(conn, collector.collect())
        return 0

    monkeypatch.setattr(speedtest, "run_until_stopped", run_one_cycle)

    assert speedtest.main() == 0

    with db.connect(tmp_path / "bbmon.db") as conn:
        stored = db.latest_speedtest_result(conn)

    assert stored is not None
    assert stored.success is True
    assert stored.download_mbps == pytest.approx(48.5)
    assert stored.isp == "Example Broadband"
