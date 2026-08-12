"""Tests for the one-shot database initialisation entrypoint."""

from pathlib import Path

import pytest

from bbmon import db, init

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
