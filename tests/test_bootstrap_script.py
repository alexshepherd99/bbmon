"""Tests for the ping_group_range arithmetic in scripts/bootstrap.sh.

Most of bootstrap.sh needs a Pi and root, and G1 is where that is checked. This
part needs neither, and it earns a test because the first version of it was
wrong in a way that only real hardware revealed: it wrote a bare "$gid $gid",
which on a Raspberry Pi — where the range ships wide open at "0 2147483647" —
would have revoked unprivileged ICMP from every other account on the machine.

The rule under test is that the range only ever widens.
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
