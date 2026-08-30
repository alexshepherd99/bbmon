"""Tests for staging a configuration and installing it as root.

The two halves belong to different privilege levels and are tested as such:
:func:`stage` is what the unauthenticated web app may do, and :func:`install`
is what root will do on its behalf. Everything :func:`install` refuses is a
case where the web app has asked for something it should not get.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml

from bbmon import configstore
from bbmon.config import Config, load
from bbmon.configstore import ConfigInstallError, install, stage, staged_path, to_yaml


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    """A config file in the shape bootstrap.sh installs: present, and owned."""
    path = tmp_path / "etc" / "config.yaml"
    path.parent.mkdir()
    path.write_text("ping:\n  interval_seconds: 5\n")
    path.chmod(0o640)
    return path


@pytest.fixture
def staged(tmp_path: Path) -> Path:
    """The state directory exists on the Pi; bootstrap.sh creates it."""
    state = tmp_path / "state"
    state.mkdir()
    return state / "config-staged.yaml"


def valid_config() -> Config:
    """A config that passes the installer's checks, trigger guard included."""
    return Config(database_path=Path("/var/lib/bbmon/bbmon.db"))


def stage_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def install_here(staged: Path, destination: Path) -> None:
    """Install as the user running the tests, standing in for root."""
    install(staged, destination, expected_owner_uid=os.getuid())


def test_the_staged_path_sits_beside_the_database() -> None:
    """The same derivation the reboot trigger uses: one writable directory."""
    assert staged_path("/var/lib/bbmon/bbmon.db") == Path(
        "/var/lib/bbmon/config-staged.yaml"
    )


def test_a_config_survives_being_written_and_read_back(tmp_path: Path) -> None:
    """The round trip the admin page depends on: what is saved is what loads."""
    config = Config(
        ping_interval_seconds=11,
        ping_targets=("9.9.9.9", "example.com"),
        speedtest_interval_hours=6,
        reboot_interval_days=7,
        retention_ping_days=14,
        web_host="0.0.0.0",
        web_port=8081,
        web_allowed_hosts=("bbmon.lan",),
        web_restart_limit=5,
        database_path=Path("/var/lib/bbmon/bbmon.db"),
    )
    written = tmp_path / "config.yaml"
    written.write_text(to_yaml(config))

    assert load(written) == config


def test_the_written_file_is_the_documented_nested_shape(tmp_path: Path) -> None:
    """Written back in sections, not as flattened field names."""
    document = yaml.safe_load(to_yaml(valid_config()))

    assert document["ping"]["interval_seconds"] == 5
    assert document["web"]["port"] == 8080
    assert document["database"]["path"] == "/var/lib/bbmon/bbmon.db"


def test_staging_writes_a_file_the_service_group_cannot_read(staged: Path) -> None:
    """Same 0640 the deployed config carries; staging must not widen it."""
    stage(valid_config(), staged)

    assert stat.S_IMODE(staged.stat().st_mode) == 0o640


def test_staging_leaves_no_temporary_file_behind(staged: Path) -> None:
    stage(valid_config(), staged)

    assert [entry.name for entry in staged.parent.iterdir()] == [staged.name]


def test_installing_replaces_the_destination(staged: Path, destination: Path) -> None:
    stage(valid_config(), staged)

    install_here(staged, destination)

    assert load(destination) == valid_config()


def test_installing_consumes_the_staged_file(staged: Path, destination: Path) -> None:
    """Like the reboot trigger: the request is spent once it is acted on."""
    stage(valid_config(), staged)

    install_here(staged, destination)

    assert not staged.exists()


def test_installing_keeps_the_destination_permissions(
    staged: Path, destination: Path
) -> None:
    """The file stays unreadable to everyone but root and the service group."""
    stage(valid_config(), staged)

    install_here(staged, destination)

    assert stat.S_IMODE(destination.stat().st_mode) == 0o640


def test_nothing_to_install_is_not_an_error(staged: Path, destination: Path) -> None:
    """systemd may start the unit with no request pending; that is not a fault."""
    before = destination.read_text()

    install_here(staged, destination)

    assert destination.read_text() == before


def test_a_symlink_is_refused(tmp_path: Path, staged: Path, destination: Path) -> None:
    """The escalation this helper exists to refuse.

    The staged file lives in the one directory the web app can write. If root
    followed a symlink there, the web app could name any file on the machine
    and have its contents copied into a file the service group can read.
    """
    secret = tmp_path / "secret"
    secret.write_text("root-only material\n")
    staged.symlink_to(secret)

    with pytest.raises(ConfigInstallError, match="symbolic link"):
        install_here(staged, destination)

    assert "root-only material" not in destination.read_text()


def test_a_refused_symlink_does_not_delete_what_it_pointed_at(
    tmp_path: Path, staged: Path, destination: Path
) -> None:
    secret = tmp_path / "secret"
    secret.write_text("root-only material\n")
    staged.symlink_to(secret)

    with pytest.raises(ConfigInstallError):
        install_here(staged, destination)

    assert secret.exists()


def test_a_file_owned_by_someone_else_is_refused(
    staged: Path, destination: Path
) -> None:
    """Only the service user may ask. Anything else did not come from bbmon."""
    stage(valid_config(), staged)

    with pytest.raises(ConfigInstallError, match="owned"):
        install(staged, destination, expected_owner_uid=os.getuid() + 1)


def test_a_config_that_is_not_valid_yaml_is_refused(
    staged: Path, destination: Path
) -> None:
    stage_text(staged, "ping: [unclosed\n")
    before = destination.read_text()

    with pytest.raises(ConfigInstallError):
        install_here(staged, destination)

    assert destination.read_text() == before


def test_a_config_with_an_invalid_setting_is_refused(
    staged: Path, destination: Path
) -> None:
    """Root revalidates rather than trusting the web app to have done it."""
    stage_text(staged, "ping:\n  interval_seconds: 0\n")
    before = destination.read_text()

    with pytest.raises(ConfigInstallError, match="greater than 0"):
        install_here(staged, destination)

    assert destination.read_text() == before


def test_a_database_path_that_moves_the_reboot_trigger_is_refused(
    staged: Path, destination: Path
) -> None:
    """plan.md's M4/M6 guard: the units name the trigger literally.

    Moving ``database.path`` moves the trigger out from under
    ``bbmon-reboot.path``, and the Pi then stops rebooting silently.
    """
    stage(Config(database_path=Path("/var/lib/bbmon/elsewhere/bbmon.db")), staged)
    before = destination.read_text()

    with pytest.raises(ConfigInstallError, match="bbmon-reboot.path"):
        install_here(staged, destination)

    assert destination.read_text() == before


def test_a_refused_request_is_still_consumed(staged: Path, destination: Path) -> None:
    """A rejected request is not left in the state directory to be retried."""
    stage_text(staged, "ping:\n  interval_seconds: 0\n")

    with pytest.raises(ConfigInstallError):
        install_here(staged, destination)

    assert not staged.exists()


def test_the_watched_path_is_where_the_default_database_stages_to() -> None:
    """The unit names this literally; the code derives it. They must agree."""
    assert staged_path(Config().database_path) == configstore.WATCHED_STAGE_PATH
