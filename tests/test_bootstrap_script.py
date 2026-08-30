"""Tests for the parts of scripts/bootstrap.sh that need neither a Pi nor root.

Most of bootstrap.sh has to be run on hardware, and the gates are where that is
checked. Three pieces are ordinary logic and are tested here, each because
getting it wrong does damage that is invisible at the time:

- the ping_group_range arithmetic, whose first version wrote a bare "$gid $gid"
  and would have revoked unprivileged ICMP from every other account on a
  Raspberry Pi, where the range ships wide open at "0 2147483647";
- the split between units that are enabled and the reboot service that must
  never be, which is all that stands between the Pi and a boot loop;
- the sudoers grant deploy.sh runs on, where the thing worth asserting is what
  it does *not* allow.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

BOOTSTRAP = Path(__file__).resolve().parent.parent / "scripts" / "bootstrap.sh"

#: The kernel's "disabled" encoding: low above high, so no group qualifies.
DISABLED = (1, 0)

#: What Raspberry Pi OS actually ships, confirmed on the Pi on 2026-08-12.
WIDE_OPEN = (0, 2147483647)

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is needed to source bootstrap.sh"
)


def range_for(gid: int, low: int, high: int) -> str:
    """Ask the real script rather than reimplementing its arithmetic here."""
    result = subprocess.run(
        ["bash", "-c", f'source "{BOOTSTRAP}"; ping_group_range_for {gid} {low} {high}'],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_sourcing_the_script_installs_nothing() -> None:
    """The guard around main() is what makes every other test here safe."""
    result = subprocess.run(
        ["bash", "-c", f'source "{BOOTSTRAP}"; echo SOURCED'],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "SOURCED" in result.stdout
    assert "Installing" not in result.stdout


def unit_list(name: str) -> list[str]:
    """Read one of the script's unit arrays without running anything."""
    result = subprocess.run(
        ["bash", "-c", f'source "{BOOTSTRAP}"; printf "%s\\n" "${{{name}[@]}}"'],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()


def test_the_reboot_service_is_installed_but_never_enabled() -> None:
    """Enabling it would reboot the Pi on every boot, for ever.

    The two arrays are what keeps that from happening: everything in UNITS is
    enabled and started, and only what is in ON_DEMAND_UNITS escapes that.
    """
    assert "bbmon-reboot.service" in unit_list("ON_DEMAND_UNITS")
    assert "bbmon-reboot.service" not in unit_list("UNITS")


def test_the_reboot_watcher_is_enabled() -> None:
    """It is the half of the pair that has to be running to notice a request."""
    assert "bbmon-reboot.path" in unit_list("UNITS")


def test_the_config_installer_is_installed_but_never_enabled() -> None:
    """Enabled, it would install whatever proposal was lying about at boot."""
    assert "bbmon-config.service" in unit_list("ON_DEMAND_UNITS")
    assert "bbmon-config.service" not in unit_list("UNITS")


def test_the_config_watcher_is_enabled() -> None:
    """Without it running, the admin page's save would go quietly nowhere."""
    assert "bbmon-config.path" in unit_list("UNITS")


def test_a_wide_open_range_is_left_alone() -> None:
    """The bug G1 found: this used to narrow it to the bbmon group alone."""
    assert range_for(996, *WIDE_OPEN) == ""


def test_a_disabled_range_is_opened_for_that_group_only() -> None:
    assert range_for(996, *DISABLED) == "996 996"


def test_a_group_already_inside_the_range_needs_no_change() -> None:
    assert range_for(996, 990, 1000) == ""


def test_the_boundaries_count_as_inside() -> None:
    assert range_for(990, 990, 1000) == ""
    assert range_for(1000, 990, 1000) == ""


def test_a_group_below_the_range_extends_the_low_bound_only() -> None:
    assert range_for(500, 990, 1000) == "500 1000"


def test_a_group_above_the_range_extends_the_high_bound_only() -> None:
    assert range_for(2000, 990, 1000) == "990 2000"


@pytest.mark.parametrize(
    ("gid", "low", "high"),
    [
        (996, *WIDE_OPEN),
        (996, *DISABLED),
        (500, 990, 1000),
        (2000, 990, 1000),
        (996, 990, 1000),
        (0, 0, 0),
    ],
)
def test_the_range_never_narrows(gid: int, low: int, high: int) -> None:
    """The property that matters, asserted directly rather than case by case."""
    result = range_for(gid, low, high)
    if not result:
        # No change written, so whatever was in force stays in force.
        assert low <= gid <= high
        return

    new_low, new_high = (int(part) for part in result.split())
    assert new_low <= gid <= new_high, "the point of the change is to include gid"
    if low <= high:
        assert new_low <= low, f"low bound narrowed from {low} to {new_low}"
        assert new_high >= high, f"high bound narrowed from {high} to {new_high}"


DEPLOY_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "deploy.sh"

#: An account name that could not be confused with anything already in the
#: rendered file, so a test asserting it appears is asserting something.
ADMIN = "someadmin"

#: One path per branch of deploy.sh's services_for_path, so the set below is
#: everything that script can ask the Pi to restart.
DEPLOY_PATHS = (
    "bbmon/web/app.py",
    "bbmon/pinger.py",
    "bbmon/collectors/ping.py",
    "bbmon/speedtest.py",
    "bbmon/collectors/speedtest.py",
    "bbmon/config.py",
)


def sudoers_content(user: str = ADMIN) -> str:
    """Ask the real script for the file it would install."""
    result = subprocess.run(
        ["bash", "-c", f'source "{BOOTSTRAP}"; sudoers_content_for "{user}"'],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def granted_commands(user: str = ADMIN) -> list[str]:
    """The commands the drop-in permits, one per entry, comments discarded."""
    body = sudoers_content(user).split("NOPASSWD:", 1)[1]
    body = body.replace("\\\n", " ")
    return [command.strip() for command in body.split(",") if command.strip()]


def services_deploy_can_restart() -> set[str]:
    """Ask deploy.sh, rather than restating its case statement here."""
    services: set[str] = set()
    for path in DEPLOY_PATHS:
        result = subprocess.run(
            ["bash", "-c", f'source "{DEPLOY_SCRIPT}"; services_for_path "{path}"'],
            capture_output=True,
            text=True,
            check=True,
        )
        services.update(result.stdout.split())
    return services


def test_every_service_deploy_restarts_is_granted() -> None:
    """The join the two scripts cannot check for themselves.

    deploy.sh decides what to restart; bootstrap.sh decides what may be
    restarted without a password. Add a service to one and not the other and
    the deploy fails at the last step, after the code has already been copied.
    """
    granted = granted_commands()
    for service in services_deploy_can_restart():
        assert f"/usr/bin/systemctl restart {service}" in granted, (
            f"deploy.sh can restart {service}, but the sudoers grant omits it"
        )


def test_both_spellings_of_each_unit_are_granted() -> None:
    """sudo matches the command line as typed, not what systemd resolves it to."""
    granted = granted_commands()
    for service in services_deploy_can_restart():
        assert f"/usr/bin/systemctl restart {service}.service" in granted


def test_nothing_outside_the_deploy_loop_is_granted() -> None:
    """The point of the file. Anything else here is a hole, not a convenience."""
    permitted_prefixes = ("/usr/bin/systemctl restart bbmon-", "/usr/bin/tee ")
    for command in granted_commands():
        assert command.startswith(permitted_prefixes), f"unexpected grant: {command}"


def test_the_grant_is_not_a_blanket_one() -> None:
    assert "NOPASSWD: ALL" not in sudoers_content()
    assert "ALL" not in " ".join(granted_commands())


def test_the_build_stamp_is_the_only_file_writable_through_it() -> None:
    """tee with a free argument would be write-anything-as-root."""
    tee_grants = [c for c in granted_commands() if c.startswith("/usr/bin/tee")]
    assert tee_grants == ["/usr/bin/tee /var/lib/bbmon/build-stamp"]


@pytest.mark.parametrize("unit", ["bbmon-init", "bbmon-reboot"])
def test_boot_and_reboot_units_are_not_restartable_without_a_password(unit: str) -> None:
    """Neither is ever the answer to "code changed", so neither is in the grant.

    bbmon-reboot in particular: the path unit is the trigger for rebooting the
    machine, and M6 puts a reboot button on an unauthenticated web app.
    """
    for command in granted_commands():
        assert unit not in command


def test_the_grant_names_the_account_it_was_asked_for() -> None:
    """It is the account deploy.sh connects as, not a hardcoded "pi"."""
    lines = sudoers_content("someoneelse").splitlines()
    assert any(line.startswith("someoneelse ALL=(root)") for line in lines)


def find_visudo() -> str | None:
    """visudo lives in /usr/sbin, which is not on a non-root PATH on Debian.

    Looking there as well is the difference between this test running and
    silently skipping on every development machine.
    """
    return shutil.which("visudo") or shutil.which("visudo", path="/usr/sbin:/sbin")


@pytest.mark.skipif(find_visudo() is None, reason="visudo is not installed")
def test_the_generated_file_is_valid_sudoers(tmp_path: Path) -> None:
    """A malformed drop-in disables sudo for the whole machine.

    bootstrap.sh runs this same check before installing and refuses on a
    failure; this asserts the file it generates passes in the first place, so
    the refusal is never the thing that finds out.
    """
    visudo = find_visudo()
    assert visudo is not None
    candidate = tmp_path / "bbmon-deploy"
    candidate.write_text(sudoers_content())
    result = subprocess.run(
        [visudo, "-cqf", str(candidate)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
