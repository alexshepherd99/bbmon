"""Tests for configuration loading and validation."""

from pathlib import Path

import pytest

from bbmon.config import CONFIG_PATH_ENV_VAR, Config, ConfigError, load


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_absent_values_fall_back_to_defaults(tmp_path: Path) -> None:
    path = write_config(tmp_path, "ping:\n  interval_seconds: 10\n")

    config = load(path)

    assert config.ping_interval_seconds == 10
    assert config.ping_targets == ("8.8.8.8", "1.1.1.1", "google.com")
    assert config.speedtest_interval_hours == 3
    assert config.reboot_interval_days == 3
    assert config.retention_ping_days == 30
    assert config.web_port == 8080
    assert config.database_path == Path("/var/lib/bbmon/bbmon.db")


def test_every_field_can_be_overridden(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
        ping:
          interval_seconds: 2
          targets: [9.9.9.9, example.com]
        speedtest:
          interval_hours: 6
        reboot:
          interval_days: 7
        retention:
          ping_days: 14
        web:
          host: 0.0.0.0
          port: 9090
        database:
          path: ./var/bbmon.db
        """,
    )

    config = load(path)

    assert config.ping_interval_seconds == 2
    assert config.ping_targets == ("9.9.9.9", "example.com")
    assert config.speedtest_interval_hours == 6
    assert config.reboot_interval_days == 7
    assert config.retention_ping_days == 14
    assert config.web_host == "0.0.0.0"
    assert config.web_port == 9090
    assert config.database_path == Path("./var/bbmon.db")


def test_empty_file_yields_all_defaults(tmp_path: Path) -> None:
    path = write_config(tmp_path, "")

    assert load(path) == Config()


def test_path_comes_from_the_environment_when_not_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path, "web:\n  port: 1234\n")
    monkeypatch.setenv(CONFIG_PATH_ENV_VAR, str(path))

    assert load().web_port == 1234


def test_missing_file_names_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"

    with pytest.raises(ConfigError, match=str(missing)):
        load(missing)


def test_malformed_yaml_is_reported_as_a_config_error(tmp_path: Path) -> None:
    path = write_config(tmp_path, "ping: [unclosed\n")

    with pytest.raises(ConfigError, match="not valid YAML"):
        load(path)


def test_top_level_must_be_a_mapping(tmp_path: Path) -> None:
    path = write_config(tmp_path, "- just\n- a list\n")

    with pytest.raises(ConfigError, match="mapping"):
        load(path)


def test_python_object_tags_are_not_constructed(tmp_path: Path) -> None:
    """yaml.safe_load, never yaml.load: a config file must not execute code.

    Asserting only that ConfigError is raised would pass even under an unsafe
    loader, because the constructed value then fails validation anyway — after
    the payload has already run. The marker file is what actually detects it.
    """
    marker = tmp_path / "payload-ran"
    path = write_config(
        tmp_path, f"ping: !!python/object/apply:os.system ['touch {marker}']\n"
    )

    with pytest.raises(ConfigError):
        load(path)

    assert not marker.exists(), "the configuration file executed code"


@pytest.mark.parametrize(
    "section, key",
    [
        ("ping", "interval_seconds"),
        ("speedtest", "interval_hours"),
        ("reboot", "interval_days"),
        ("retention", "ping_days"),
    ],
)
@pytest.mark.parametrize("bad_value", [0, -1])
def test_intervals_must_be_positive(
    tmp_path: Path, section: str, key: str, bad_value: int
) -> None:
    path = write_config(tmp_path, f"{section}:\n  {key}: {bad_value}\n")

    with pytest.raises(ConfigError, match="greater than 0"):
        load(path)


def test_intervals_must_be_whole_numbers(tmp_path: Path) -> None:
    path = write_config(tmp_path, "ping:\n  interval_seconds: not-a-number\n")

    with pytest.raises(ConfigError, match="whole number"):
        load(path)


def test_booleans_are_not_accepted_as_intervals(tmp_path: Path) -> None:
    """bool is a subclass of int, so this needs rejecting explicitly."""
    path = write_config(tmp_path, "ping:\n  interval_seconds: true\n")

    with pytest.raises(ConfigError, match="whole number"):
        load(path)


def test_ping_targets_cannot_be_empty(tmp_path: Path) -> None:
    path = write_config(tmp_path, "ping:\n  targets: []\n")

    with pytest.raises(ConfigError, match="at least one"):
        load(path)


@pytest.mark.parametrize(
    "target",
    [
        "-leading-hyphen.com",
        "trailing-hyphen-.com",
        "has space.com",
        "semicolon;rm -rf /",
        "$(whoami).com",
        "",
        "..",
    ],
)
def test_malformed_ping_targets_are_rejected(tmp_path: Path, target: str) -> None:
    path = write_config(tmp_path, f"ping:\n  targets: ['{target}']\n")

    with pytest.raises(ConfigError, match="not a valid hostname or IP address"):
        load(path)


@pytest.mark.parametrize(
    "target", ["8.8.8.8", "2001:4860:4860::8888", "google.com", "a.b.c.example.co.uk"]
)
def test_valid_ping_targets_are_accepted(tmp_path: Path, target: str) -> None:
    path = write_config(tmp_path, f"ping:\n  targets: ['{target}']\n")

    assert load(path).ping_targets == (target,)


def test_ping_targets_must_be_a_list(tmp_path: Path) -> None:
    path = write_config(tmp_path, "ping:\n  targets: google.com\n")

    with pytest.raises(ConfigError, match="list"):
        load(path)


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_web_port_must_be_in_range(tmp_path: Path, port: int) -> None:
    path = write_config(tmp_path, f"web:\n  port: {port}\n")

    with pytest.raises(ConfigError, match="between 1 and 65535"):
        load(path)


def test_web_host_must_be_an_ip_address(tmp_path: Path) -> None:
    """The bind address is explicit per the security posture, so it is checked."""
    path = write_config(tmp_path, "web:\n  host: not-an-ip\n")

    with pytest.raises(ConfigError, match="IP address"):
        load(path)


def test_database_path_cannot_be_empty(tmp_path: Path) -> None:
    path = write_config(tmp_path, "database:\n  path: ''\n")

    with pytest.raises(ConfigError, match="must not be empty"):
        load(path)


def test_unknown_key_is_rejected_rather_than_silently_ignored(tmp_path: Path) -> None:
    """A typo must not leave a service quietly running on the default."""
    path = write_config(tmp_path, "ping:\n  intervall_seconds: 10\n")

    with pytest.raises(ConfigError, match="unknown"):
        load(path)


def test_unknown_section_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, "pingg:\n  interval_seconds: 10\n")

    with pytest.raises(ConfigError, match="unknown"):
        load(path)


def test_section_must_be_a_mapping(tmp_path: Path) -> None:
    path = write_config(tmp_path, "ping: 5\n")

    with pytest.raises(ConfigError, match="mapping"):
        load(path)
