"""Checks on the shipped systemd units.

These are not a substitute for running them — only the Pi can do that, at gate
G1. What they do catch is drift: the security posture in ``docs/phase-1/plan.md``
commits every unit to a specific set of sandboxing directives, and the natural
way to lose one is to add a fifth unit later (M4's reboot service) by copying a
fourth and trimming it.
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

UNIT_DIR = Path(__file__).resolve().parent.parent / "deploy" / "systemd"

#: The sandboxing plan.md commits to for every collector/web unit, and the
#: empty bounding set that holds its "no CAP_NET_RAW" ping decision in place.
REQUIRED_SANDBOXING = {
    "NoNewPrivileges": "yes",
    "ProtectSystem": "strict",
    "ProtectHome": "yes",
    "PrivateTmp": "yes",
    "CapabilityBoundingSet": "",
}

LONG_RUNNING_UNITS = [
    "bbmon-pinger.service",
    "bbmon-speedtest.service",
    "bbmon-web.service",
]
ALL_UNITS = ["bbmon-init.service"] + LONG_RUNNING_UNITS


def read_unit(name: str) -> configparser.ConfigParser:
    """Parse a unit file. systemd allows duplicate keys; ConfigParser does not."""
    parser = configparser.ConfigParser(strict=False)
    # Directive names are case-sensitive in systemd.
    parser.optionxform = str  # type: ignore[method-assign]
    parser.read(UNIT_DIR / name)
    return parser


def test_every_unit_file_is_present() -> None:
    for name in ALL_UNITS:
        assert (UNIT_DIR / name).is_file(), f"{name} is missing"


@pytest.mark.parametrize("name", ALL_UNITS)
def test_units_carry_the_committed_sandboxing(name: str) -> None:
    service = read_unit(name)["Service"]
    for directive, expected in REQUIRED_SANDBOXING.items():
        assert directive in service, f"{name} is missing {directive}"
        assert service[directive] == expected, (
            f"{name} sets {directive}={service[directive]!r}, expected {expected!r}"
        )


@pytest.mark.parametrize("name", ALL_UNITS)
def test_no_unit_runs_as_root(name: str) -> None:
    service = read_unit(name)["Service"]
    assert service.get("User") == "bbmon"
    assert service.get("Group") == "bbmon"


@pytest.mark.parametrize("name", ALL_UNITS)
def test_the_database_directory_is_the_only_writable_path(name: str) -> None:
    """StateDirectory=bbmon is what grants /var/lib/bbmon under ProtectSystem=strict."""
    service = read_unit(name)["Service"]
    assert service.get("StateDirectory") == "bbmon"
    assert "ReadWritePaths" not in service, (
        "StateDirectory already grants /var/lib/bbmon; a ReadWritePaths= line "
        "is either the same grant twice or a wider one"
    )


@pytest.mark.parametrize("name", LONG_RUNNING_UNITS)
def test_long_running_units_restart_on_failure(name: str) -> None:
    """Requirement 10: a crashed collector recovers on its own."""
    assert read_unit(name)["Service"].get("Restart") == "on-failure"


@pytest.mark.parametrize("name", LONG_RUNNING_UNITS)
def test_every_service_depends_on_the_init_step(name: str) -> None:
    """Requirement 3: the schema exists before anything else starts."""
    unit = read_unit(name)["Unit"]
    assert "bbmon-init.service" in unit.get("Requires", "")
    assert "bbmon-init.service" in unit.get("After", "")


def test_the_init_unit_stays_active_once_it_has_run() -> None:
    """A oneshot without RemainAfterExit reads as inactive, so Requires= restarts it."""
    service = read_unit("bbmon-init.service")["Service"]
    assert service.get("Type") == "oneshot"
    assert service.get("RemainAfterExit") == "yes"


@pytest.mark.parametrize("name", ["bbmon-pinger.service", "bbmon-speedtest.service"])
def test_collectors_are_given_time_to_flush_on_stop(name: str) -> None:
    """The final flush runs during shutdown; SIGKILL arriving first would lose it."""
    service = read_unit(name)["Service"]
    assert int(service["TimeoutStopSec"]) >= 30


@pytest.mark.parametrize("name", ALL_UNITS)
def test_units_state_their_config_path_explicitly(name: str) -> None:
    service = read_unit(name)["Service"]
    assert service.get("Environment") == "BBMON_CONFIG=/etc/bbmon/config.yaml"
