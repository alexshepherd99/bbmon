"""Tests for the one-shot database initialisation entrypoint."""

from pathlib import Path

import pytest

from bbmon import db, init, reboot, timesync

CONFIG = """
database:
  path: {path}
"""


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.format(path=tmp_path / "nested" / "bbmon.db"))
    monkeypatch.setenv("BBMON_CONFIG", str(path))
    return path


@pytest.fixture(autouse=True)
def no_timesyncd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the clock wait out of the way, and off whatever the host runs."""
    monkeypatch.setattr(timesync, "TIMESYNC_RUNTIME_DIR", tmp_path / "no-timesyncd")


@pytest.fixture
def uptime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand in for /proc/uptime: a machine up for two minutes."""
    path = tmp_path / "uptime"
    path.write_text("120.0 60.0\n")
    monkeypatch.setattr(reboot, "UPTIME_PATH", path)
    return path


def test_it_creates_the_schema_at_the_configured_path(
    config_file: Path, tmp_path: Path
) -> None:
    assert init.main() == 0

    with db.connect(tmp_path / "nested" / "bbmon.db") as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {"ping_results", "speedtest_results", "restarts"} <= tables


def test_it_stamps_the_schema_version(config_file: Path, tmp_path: Path) -> None:
    init.main()

    with db.connect(tmp_path / "nested" / "bbmon.db") as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_running_it_twice_is_harmless(config_file: Path, tmp_path: Path) -> None:
    """systemd re-runs a oneshot unit on every boot, so this is the normal path."""
    assert init.main() == 0
    assert init.main() == 0


def test_it_records_the_restart(config_file: Path, uptime: Path, tmp_path: Path) -> None:
    """Requirement 6's check runs here, before any service is allowed to start."""
    assert init.main() == 0

    with db.connect(tmp_path / "nested" / "bbmon.db") as conn:
        recorded = db.latest_restart(conn)

    assert recorded is not None
    assert recorded.expected is False


def test_it_records_a_requested_reboot_as_expected(
    config_file: Path, uptime: Path, tmp_path: Path
) -> None:
    database = tmp_path / "nested" / "bbmon.db"
    database.parent.mkdir()
    reboot.request_file_path(database).write_text("scheduled reboot after 3 days")

    assert init.main() == 0

    with db.connect(database) as conn:
        recorded = db.latest_restart(conn)

    assert recorded is not None
    assert recorded.expected is True
    assert recorded.reason == "scheduled reboot after 3 days"


def test_it_clears_a_reboot_trigger_left_over_from_the_last_boot(
    config_file: Path, uptime: Path, tmp_path: Path
) -> None:
    """Otherwise the Pi could come up, notice its own trigger, and go down again."""
    database = tmp_path / "nested" / "bbmon.db"
    database.parent.mkdir()
    trigger = reboot.trigger_file_path(database)
    trigger.write_text("")

    assert init.main() == 0

    assert not trigger.exists()


def test_the_schema_still_gets_created_when_the_restart_cannot_be_recorded(
    config_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every service Requires= this unit, so a diagnostic nicety must not fail it.

    No uptime fixture here: /proc/uptime is pointed at a file that is not there.
    """
    monkeypatch.setattr(reboot, "UPTIME_PATH", tmp_path / "absent")

    assert init.main() == 0

    with db.connect(tmp_path / "nested" / "bbmon.db") as conn:
        assert db.latest_restart(conn) is None


def test_it_waits_for_the_clock_before_recording_anything(
    config_file: Path,
    uptime: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Requirement 6: the boot's first write waits on NTP.

    timesyncd is made to look present but unsynchronised, so the wait runs and
    times out. The warning is the evidence that it ran at all — every unit is
    ordered after this one, so this is where the whole system waits.
    """
    runtime_dir = tmp_path / "timesync"
    runtime_dir.mkdir()
    monkeypatch.setattr(timesync, "TIMESYNC_RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(timesync, "DEFAULT_TIMEOUT_SECONDS", 0)

    assert init.main() == 0

    assert "not synchronised" in caplog.text


def test_a_bad_config_exits_non_zero_rather_than_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("database:\n  path: ''\n")
    monkeypatch.setenv("BBMON_CONFIG", str(path))

    assert init.main() == 1


def test_an_unwritable_location_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The units order every service after this one, so a failure must be visible."""
    blocked = tmp_path / "blocked"
    blocked.mkdir(mode=0o500)
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.format(path=blocked / "sub" / "bbmon.db"))
    monkeypatch.setenv("BBMON_CONFIG", str(path))

    assert init.main() == 1
