"""Tests for the speed test service entrypoint."""

import json
from pathlib import Path

import pytest

from bbmon import db, reboot, speedtest
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


def test_a_reload_rebuilds_the_collector_from_the_changed_file(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 2's SIGHUP reload, on the service with the longest interval.

    Twelve hours is a long time to wait for a setting to take effect, and a
    restart is what it would otherwise cost.
    """
    intervals: list[int] = []

    def fake_run(collector, database_path, flush_interval_seconds, requests, **_):
        intervals.append(collector.interval_seconds)
        if len(intervals) == 1:
            config_file.write_text(
                config_file.read_text().replace("interval_hours: 6", "interval_hours: 12")
            )
            requests.reload.set()
        return 0

    monkeypatch.setattr(speedtest, "run_until_stopped", fake_run)

    assert speedtest.main() == 0
    assert intervals == [6 * 3600, 12 * 3600]


def test_a_reload_does_not_start_a_speed_test_straight_away(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every save from the admin page reloads this service.

    A rebuilt loop tests before it sleeps, so without this, changing any
    setting at all — the restart list's length, say — ran a speed test and put
    its latency bump on the dashboard. Startup still tests at once.
    """
    waits: list[float] = []

    def fake_run(
        collector,
        database_path,
        flush_interval_seconds,
        requests,
        between_cycles=lambda: None,
        first_wait_seconds=0.0,
    ):
        waits.append(first_wait_seconds)
        if len(waits) == 1:
            between_cycles()  # the startup test has run
            requests.reload.set()
        return 0

    monkeypatch.setattr(speedtest, "run_until_stopped", fake_run)

    assert speedtest.main() == 0
    assert waits[0] == 0
    assert waits[1] == pytest.approx(6 * 3600, abs=5)


def test_the_first_test_after_a_reload_keeps_to_the_schedule() -> None:
    """A save part way through an interval moves nothing: the next test is due
    when it would have been, measured with the interval as it now reads.
    """
    now = {"seconds": 1000.0}
    schedule = speedtest.SpeedtestSchedule(monotonic=lambda: now["seconds"])

    # Nothing has run yet, which is startup: test at once.
    assert schedule.seconds_until_due(6 * 3600) == 0

    schedule.cycle_ended()
    now["seconds"] += 3600
    assert schedule.seconds_until_due(6 * 3600) == 5 * 3600
    assert schedule.seconds_until_due(12 * 3600) == 11 * 3600

    # Overdue under a shortened interval: test now, not a negative wait.
    now["seconds"] += 6 * 3600
    assert schedule.seconds_until_due(6 * 3600) == 0


def test_the_service_configures_the_collector_from_the_config_file(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def fake_run(collector, database_path, flush_interval_seconds, requests, **_):
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

    def fake_run(collector, database_path, flush_interval_seconds, requests, **_):
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


def test_the_collector_is_told_when_a_reboot_is_near(
    config_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 5, wired to requirement 6's schedule with no shared state.

    Both services read the same configured interval and the same uptime, so
    the speed test reaches the same answer as the pinger without either
    knowing about the other. Here the machine is one minute short of its
    three-day reboot, so the run is skipped.
    """
    uptime = tmp_path / "uptime"
    uptime.write_text(f"{3 * 86400 - 60} 0.0\n")
    monkeypatch.setattr(reboot, "UPTIME_PATH", uptime)

    captured = {}

    def fake_run(collector, database_path, flush_interval_seconds, requests, **_):
        captured["results"] = collector.collect()
        return 0

    monkeypatch.setattr(speedtest, "run_until_stopped", fake_run)

    assert speedtest.main() == 0
    assert captured["results"] == []


def test_the_collector_runs_when_the_reboot_is_a_long_way_off(
    config_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other branch: the skip must not be permanently on."""
    import subprocess

    from bbmon.collectors import speedtest as collector_module

    uptime = tmp_path / "uptime"
    uptime.write_text("60.0 0.0\n")
    monkeypatch.setattr(reboot, "UPTIME_PATH", uptime)
    monkeypatch.setattr(
        collector_module.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, json.dumps({"type": "result", "ping": {"latency": 11.0}}), ""
        ),
    )

    captured = {}

    def fake_run(collector, database_path, flush_interval_seconds, requests, **_):
        captured["results"] = collector.collect()
        return 0

    monkeypatch.setattr(speedtest, "run_until_stopped", fake_run)

    assert speedtest.main() == 0
    assert len(captured["results"]) == 1


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

    def run_one_cycle(collector, database_path, flush_interval_seconds, requests, **_):
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
