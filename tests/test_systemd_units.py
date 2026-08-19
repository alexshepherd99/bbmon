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

from bbmon.reboot import WATCHED_TRIGGER_PATH, trigger_file_path

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

#: The four units that run bbmon's own code as the bbmon user. The reboot unit
#: below is deliberately not one of them — it runs as root and runs no Python,
#: so the directives above are either meaningless or wrong for it.
ALL_UNITS = ["bbmon-init.service"] + LONG_RUNNING_UNITS

REBOOT_UNIT = "bbmon-reboot.service"
REBOOT_PATH_UNIT = "bbmon-reboot.path"

#: The file the unprivileged services write to ask for a reboot. Both units
#: name it literally, and bbmon.reboot derives it from the configured database.
TRIGGER_FILE = "/var/lib/bbmon/reboot-now"


def read_unit(name: str) -> configparser.ConfigParser:
    """Parse a unit file. systemd allows duplicate keys; ConfigParser does not."""
    parser = configparser.ConfigParser(strict=False)
    # Directive names are case-sensitive in systemd.
    parser.optionxform = str  # type: ignore[method-assign]
    parser.read(UNIT_DIR / name)
    return parser


def test_every_unit_file_is_present() -> None:
    for name in ALL_UNITS + [REBOOT_UNIT, REBOOT_PATH_UNIT]:
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


def test_the_init_unit_starts_after_the_clock_is_managed() -> None:
    """The NTP wait in bbmon.timesync reads a directory timesyncd creates.

    Start before timesyncd and that directory is absent, which the wait treats
    as "no time sync on this machine" — so it skips, silently, exactly when it
    was most needed.
    """
    after = read_unit("bbmon-init.service")["Unit"].get("After", "")
    assert "systemd-timesyncd.service" in after
    assert "time-sync.target" in after


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
    assert "BBMON_CONFIG=/etc/bbmon/config.yaml" in service.get("Environment", "")


def test_the_pinger_is_the_service_allowed_to_reboot() -> None:
    """Requirement 6's schedule runs in the ping loop, so it needs the real action.

    Left to its default the pinger would use the no-op, and a Pi that reports
    itself healthy would simply never reboot.
    """
    service = read_unit("bbmon-pinger.service")["Service"]
    assert "BBMON_REBOOT=systemd" in service.get("Environment", "")


@pytest.mark.parametrize("name", ["bbmon-speedtest.service", "bbmon-web.service"])
def test_no_other_service_is_given_the_real_reboot_action(name: str) -> None:
    """The web app gets it at M6, with the button; nothing needs it before then."""
    assert "BBMON_REBOOT" not in read_unit(name)["Service"].get("Environment", "")


def test_the_reboot_unit_is_never_enabled() -> None:
    """It reboots the machine when started, so a WantedBy= would be a boot loop."""
    parser = read_unit(REBOOT_UNIT)
    assert "Install" not in parser, (
        "an [Install] section would let systemctl enable bbmon-reboot.service, "
        "which would reboot the Pi on every boot, for ever"
    )


def test_the_reboot_unit_runs_one_command_and_nothing_of_ours() -> None:
    """The only privileged thing bbmon installs. It stays this small on purpose."""
    service = read_unit(REBOOT_UNIT)["Service"]

    assert service.get("Type") == "oneshot"
    assert service.get("ExecStart") == "/usr/bin/systemctl --no-block reboot"
    assert "ExecStartPost" not in service
    assert ".venv" not in service.get("ExecStart", "")


def test_the_reboot_unit_clears_its_trigger_before_going_down() -> None:
    """A trigger that survives the reboot is the one way this could loop.

    Cleared here as well as in bbmon-init, because these are two different
    failures: this covers the reboot working, the other covers it not.
    """
    service = read_unit(REBOOT_UNIT)["Service"]
    assert service.get("ExecStartPre") == f"/bin/rm -f {TRIGGER_FILE}"


def test_the_watcher_fires_on_a_write_not_on_the_file_being_there() -> None:
    """PathExists= would fire at boot on a leftover trigger — a reboot loop."""
    path_unit = read_unit(REBOOT_PATH_UNIT)["Path"]

    assert path_unit.get("PathModified") == TRIGGER_FILE
    assert "PathExists" not in path_unit
    assert "PathExistsGlob" not in path_unit
    assert path_unit.get("Unit") == REBOOT_UNIT


def test_the_watcher_is_the_unit_that_gets_enabled() -> None:
    """The pair only works if the watcher starts at boot and the service does not."""
    assert read_unit(REBOOT_PATH_UNIT)["Install"].get("WantedBy") == "multi-user.target"


def test_the_watcher_will_not_run_without_a_working_init() -> None:
    """A Pi whose configuration is broken must not be able to reboot itself.

    The ordering matters as much as the dependency: bbmon-init deletes a
    leftover trigger, and that has to have happened before anything watches.
    """
    unit = read_unit(REBOOT_PATH_UNIT)["Unit"]

    assert "bbmon-init.service" in unit.get("Requires", "")
    assert "bbmon-init.service" in unit.get("After", "")


def test_the_trigger_is_the_path_the_code_writes() -> None:
    """The units and bbmon.reboot have to name the same file to work at all.

    The units say it literally, the code derives it from database.path, and
    WATCHED_TRIGGER_PATH is the constant that keeps the two honest at runtime.
    """
    assert TRIGGER_FILE == str(trigger_file_path("/var/lib/bbmon/bbmon.db"))
    assert TRIGGER_FILE == str(WATCHED_TRIGGER_PATH)


def test_the_reboot_unit_does_not_pretend_to_be_sandboxed() -> None:
    """It has to run as root, and the restart row is written before it is called.

    Asserted rather than left implicit so nobody 'fixes' the missing User= by
    adding one, which would leave the unit unable to do the one thing it does.
    """
    service = read_unit(REBOOT_UNIT)["Service"]
    assert "User" not in service
    assert "StateDirectory" not in service


def test_the_watcher_opts_out_of_the_default_path_dependencies() -> None:
    """Otherwise it deadlocks the boot and systemd resolves it by not starting bbmon.

    A path unit with the default dependencies is implicitly ordered
    ``Before=paths.target``; ``basic.target`` is ``After=paths.target``; and a
    service with the default dependencies is ``After=basic.target``. The
    ``After=bbmon-init.service`` the test above requires therefore closes a
    cycle::

        bbmon-reboot.path -> bbmon-init.service -> basic.target
                          -> paths.target -> bbmon-reboot.path

    systemd breaks an ordering cycle by deleting one job from it, and on the Pi
    it chose ``bbmon-init.service/start`` — the unit every other unit
    ``Requires=``, so nothing bbmon started at all. Found on hardware at G3 on
    2026-08-19, and intermittent: which job gets deleted is not fixed, so one
    boot came up clean and the next came up with no monitoring running.

    Opting out drops the ``Before=paths.target`` that closes the cycle. The
    dependencies worth keeping then have to be restated by hand.
    """
    unit = read_unit(REBOOT_PATH_UNIT)["Unit"]

    assert unit.get("DefaultDependencies") == "no"
    assert "sysinit.target" in unit.get("After", "")
    assert "shutdown.target" in unit.get("Conflicts", "")
    assert "shutdown.target" in unit.get("Before", "")
