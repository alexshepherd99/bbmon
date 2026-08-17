"""Tests for the ping service entrypoint."""

from pathlib import Path
from typing import Any

import pytest

from bbmon import pinger, reboot
from bbmon.service import FLUSH_INTERVAL_SECONDS

CONFIG = """
ping:
  interval_seconds: 9
  targets:
    - 9.9.9.9
reboot:
  interval_days: 1
database:
  path: {path}
"""


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.format(path=tmp_path / "bbmon.db"))
    monkeypatch.setenv("BBMON_CONFIG", str(path))
    return path


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run the entrypoint's wiring without running its loop."""
    captured: dict[str, Any] = {}

    def fake_run(collector, database_path, flush_interval_seconds, between_cycles):
        captured["collector"] = collector
        captured["database_path"] = database_path
        captured["flush"] = flush_interval_seconds
        captured["between_cycles"] = between_cycles
        return 0

    monkeypatch.setattr(pinger, "run_until_stopped", fake_run)
    return captured


def up_for(days: float, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "uptime"
    path.write_text(f"{days * 86400} 0.0\n")
    monkeypatch.setattr(reboot, "UPTIME_PATH", path)


def test_the_service_configures_the_collector_from_the_config_file(
    config_file: Path, captured: dict[str, Any]
) -> None:
    assert pinger.main() == 0

    assert captured["collector"].interval_seconds == 9
    assert captured["flush"] == FLUSH_INTERVAL_SECONDS


def test_the_reboot_check_shares_the_ping_loop(
    config_file: Path, captured: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 6's periodic reboot, with no systemd timer of its own.

    Two days up against a one-day interval, so the check should ask for a
    reboot — which under the development no-op action means leaving the
    request file behind and nothing else.
    """
    up_for(2, tmp_path, monkeypatch)

    assert pinger.main() == 0
    captured["between_cycles"]()

    assert reboot.request_file_path(tmp_path / "bbmon.db").exists()


def test_the_reboot_check_leaves_a_machine_alone_before_its_interval(
    config_file: Path, captured: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    up_for(0.5, tmp_path, monkeypatch)

    assert pinger.main() == 0
    captured["between_cycles"]()

    assert not reboot.request_file_path(tmp_path / "bbmon.db").exists()


def test_a_misconfigured_reboot_action_stops_the_service_starting(
    config_file: Path, captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo in the unit file must not read as "reboots are off"."""
    monkeypatch.setenv(reboot.REBOOT_ACTION_ENV_VAR, "sytemctl")

    assert pinger.main() == 1


def test_a_database_moved_out_from_under_the_watcher_stops_the_service(
    config_file: Path, captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: this config's database is in tmp, so the trigger would be too.

    On the Pi that combination means every reboot request is written where
    bbmon-reboot.path is not looking. The service refuses to start instead,
    which systemd and deploy.sh both report; a Pi that has quietly stopped
    rebooting reports nothing.
    """
    monkeypatch.setenv(reboot.REBOOT_ACTION_ENV_VAR, "systemd")

    assert pinger.main() == 1
    assert "between_cycles" not in captured
