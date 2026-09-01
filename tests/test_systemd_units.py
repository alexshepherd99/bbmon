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

from bbmon.configstore import WATCHED_STAGE_PATH, staged_path
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

#: The four units that run bbmon's own code as the bbmon user. The two root
#: units below are deliberately not among them, for different reasons:
#: bbmon-reboot.service runs no Python at all, so these directives are
#: meaningless or wrong for it, while bbmon-config.service does run Python and
#: is sandboxed — but as root, so the User= and StateDirectory= assertions here
#: would be wrong for it. Each has its own tests further down.
ALL_UNITS = ["bbmon-init.service"] + LONG_RUNNING_UNITS

REBOOT_UNIT = "bbmon-reboot.service"
REBOOT_PATH_UNIT = "bbmon-reboot.path"

CONFIG_UNIT = "bbmon-config.service"
CONFIG_PATH_UNIT = "bbmon-config.path"

#: The file the web app writes to propose a configuration. Both units name it
#: literally, and bbmon.configstore derives it from the configured database.
STAGED_FILE = "/var/lib/bbmon/config-staged.yaml"

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
    for name in ALL_UNITS + [
        REBOOT_UNIT,
        REBOOT_PATH_UNIT,
        CONFIG_UNIT,
        CONFIG_PATH_UNIT,
    ]:
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


@pytest.mark.parametrize("name", ["bbmon-pinger.service", "bbmon-web.service"])
def test_the_services_that_can_reboot_are_given_the_real_action(name: str) -> None:
    """The two ways a reboot is asked for, and the only two units that need it.

    Requirement 6's schedule runs in the ping loop; requirement 8's button runs
    in the web app. Left to its default either one would use the no-op, and a
    Pi that reports itself healthy would simply never reboot — the pinger
    silently, the button while answering that it had asked.
    """
    service = read_unit(name)["Service"]
    assert "BBMON_REBOOT=systemd" in service.get("Environment", "")


@pytest.mark.parametrize("name", LONG_RUNNING_UNITS)
def test_a_running_service_can_be_told_to_re_read_its_configuration(
    name: str,
) -> None:
    """Requirement 2's SIGHUP reload, from systemd's side of it.

    Without ExecReload= there is no `systemctl reload`, and the code that
    answers the signal would only ever be reached by someone sending it by
    hand — which is not how the admin page's saves arrive.
    """
    service = read_unit(name)["Service"]
    assert service.get("ExecReload") == "/bin/kill -HUP $MAINPID"


def test_installing_a_configuration_tells_the_services_to_re_read_it() -> None:
    """Otherwise a save reaches /etc and stops there, which is a silent failure.

    try-reload-or-restart, so a service that is not running is left alone
    rather than started by a configuration change, and the leading `-` so that
    a failure to reload does not report the install — which did happen — as
    the thing that failed.
    """
    service = read_unit(CONFIG_UNIT)["Service"]

    assert service.get("ExecStartPost") == (
        "-/usr/bin/systemctl try-reload-or-restart "
        "bbmon-pinger.service bbmon-speedtest.service bbmon-web.service"
    )


def test_the_speedtest_service_is_not_given_the_real_reboot_action() -> None:
    """It asks for no reboot, so it is given no way to cause one."""
    service = read_unit("bbmon-speedtest.service")["Service"]
    assert "BBMON_REBOOT" not in service.get("Environment", "")


def test_the_reboot_unit_is_never_enabled() -> None:
    """It reboots the machine when started, so a WantedBy= would be a boot loop."""
    parser = read_unit(REBOOT_UNIT)
    assert "Install" not in parser, (
        "an [Install] section would let systemctl enable bbmon-reboot.service, "
        "which would reboot the Pi on every boot, for ever"
    )


def test_the_reboot_unit_runs_one_command_and_nothing_of_ours() -> None:
    """The stricter of the two root units. It stays this small on purpose.

    bbmon-config.service also runs as root but has to parse a file to refuse a
    bad one; this one has no such excuse and runs none of bbmon's code.
    """
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


def test_the_config_installer_is_never_enabled() -> None:
    """Started by bbmon-config.path on a write, never at boot."""
    assert "Install" not in read_unit(CONFIG_UNIT), (
        "an [Install] section would let bbmon-config.service be enabled, "
        "installing whatever proposal happened to be lying about at boot"
    )


def test_the_config_watcher_fires_on_a_write_not_on_the_file_being_there() -> None:
    """PathExists= would reinstall the same proposal at every boot."""
    path_unit = read_unit(CONFIG_PATH_UNIT)["Path"]

    assert path_unit.get("PathModified") == STAGED_FILE
    assert "PathExists" not in path_unit
    assert "PathExistsGlob" not in path_unit
    assert path_unit.get("Unit") == CONFIG_UNIT


def test_the_config_watcher_is_the_unit_that_gets_enabled() -> None:
    assert read_unit(CONFIG_PATH_UNIT)["Install"].get("WantedBy") == "multi-user.target"


def test_the_config_watcher_will_not_run_without_a_working_init() -> None:
    """A Pi whose configuration is already broken must not install another."""
    unit = read_unit(CONFIG_PATH_UNIT)["Unit"]

    assert "bbmon-init.service" in unit.get("Requires", "")
    assert "bbmon-init.service" in unit.get("After", "")


def test_the_config_watcher_opts_out_of_the_default_path_dependencies() -> None:
    """G3's ordering cycle, avoided by construction rather than rediscovered.

    The full reasoning is on the reboot watcher's equivalent test. The short
    version: a path unit with the default dependencies is ordered
    ``Before=paths.target``, which closes a cycle back through
    ``bbmon-init.service``, and systemd resolves a cycle by deleting a job.
    """
    unit = read_unit(CONFIG_PATH_UNIT)["Unit"]

    assert unit.get("DefaultDependencies") == "no"
    assert "sysinit.target" in unit.get("After", "")
    assert "shutdown.target" in unit.get("Conflicts", "")
    assert "shutdown.target" in unit.get("Before", "")


def test_the_staged_path_is_the_file_the_code_writes() -> None:
    """The unit says it literally; bbmon.configstore derives it from config."""
    assert STAGED_FILE == str(staged_path("/var/lib/bbmon/bbmon.db"))
    assert STAGED_FILE == str(WATCHED_STAGE_PATH)


def test_the_config_installer_is_sandboxed_despite_running_as_root() -> None:
    """It runs bbmon's own code as root, which bbmon-reboot.service does not.

    That is the whole reason for the difference in treatment: the reboot unit
    can be one systemctl call and no Python, and this one cannot, because
    refusing a bad proposal means parsing it.
    """
    service = read_unit(CONFIG_UNIT)["Service"]

    assert service.get("Type") == "oneshot"
    assert service.get("NoNewPrivileges") == "yes"
    assert service.get("ProtectSystem") == "strict"
    assert service.get("ProtectHome") == "yes"
    assert service.get("CapabilityBoundingSet") == ""


def test_the_config_installer_writes_only_the_two_directories_it_must() -> None:
    writable = read_unit(CONFIG_UNIT)["Service"].get("ReadWritePaths", "").split()

    assert sorted(writable) == ["/etc/bbmon", "/var/lib/bbmon"]


def test_the_config_installer_does_not_take_the_state_directory() -> None:
    """StateDirectory= here would chown /var/lib/bbmon to root.

    This unit has no User=, so systemd would create the state directory as
    root:root — and every actual bbmon service would then be unable to write
    its own database. The grant it needs is a plain ReadWritePaths=.
    """
    assert "StateDirectory" not in read_unit(CONFIG_UNIT)["Service"]


def test_the_config_installer_takes_no_direction_from_its_caller() -> None:
    """No argument and no BBMON_CONFIG: root names both paths in the code.

    A path passed in here would be a path the unauthenticated web app chose,
    which is the whole thing this mechanism exists to avoid.
    """
    service = read_unit(CONFIG_UNIT)["Service"]

    assert service.get("ExecStart") == (
        "/opt/bbmon/.venv/bin/python -m bbmon.configstore"
    )
    assert "BBMON_CONFIG" not in service.get("Environment", "")
