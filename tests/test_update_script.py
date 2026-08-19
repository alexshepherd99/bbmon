"""Tests for the unit list in scripts/update.sh.

update.sh runs on the Pi and needs root, so almost all of it is a gate item.
What can be checked here is the join no single script can check for itself:
bootstrap.sh decides which units exist, and update.sh decides which of them a
pull reinstalls. Nothing connected the two, and they had drifted — update.sh
had never heard of either half of M4's reboot mechanism, so a fix to
bbmon-reboot.path would be pulled into /opt/bbmon and never installed. The
symptom is the worst kind: the update reports success and the machine keeps
running the old unit.

Found at G3 on 2026-08-19 while shipping the ordering-cycle fix, which is
exactly a change to bbmon-reboot.path.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
BOOTSTRAP = SCRIPTS / "bootstrap.sh"
UPDATE = SCRIPTS / "update.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is needed to source the scripts"
)


def array_from(script: Path, name: str) -> set[str]:
    """Read one array out of a script by sourcing it, rather than restating it.

    The point of these tests is that the two scripts agree; a copy of either
    list here would be a third thing to keep in step.
    """
    result = subprocess.run(
        ["bash", "-c", f'source "{script}"; printf "%s\\n" "${{{name}[@]}}"'],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return {line for line in result.stdout.split() if line}


def test_sourcing_the_script_does_not_update() -> None:
    """The guard around main() is what makes the other tests here safe."""
    result = subprocess.run(
        ["bash", "-c", f'source "{UPDATE}"; echo SOURCED'],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "SOURCED" in result.stdout
    assert "Pulling" not in result.stdout


def test_update_reinstalls_every_unit_bootstrap_installs() -> None:
    """A unit bootstrap.sh installs but update.sh does not is a unit that
    silently stops being updated after the first deploy."""
    installed_by_bootstrap = array_from(BOOTSTRAP, "UNITS") | array_from(
        BOOTSTRAP, "ON_DEMAND_UNITS"
    )
    reinstalled_by_update = array_from(UPDATE, "UNITS") | array_from(
        UPDATE, "ON_DEMAND_UNITS"
    )

    assert installed_by_bootstrap == reinstalled_by_update


def test_update_never_starts_the_reboot_service() -> None:
    """It may install it. Starting it reboots the machine, and enabling it
    would reboot the machine on every boot for ever."""
    text = UPDATE.read_text()

    assert "bbmon-reboot.service" in array_from(UPDATE, "ON_DEMAND_UNITS")
    assert "bbmon-reboot.service" not in array_from(UPDATE, "SERVICES")
    assert "systemctl enable" not in text

    # Named per line rather than as a substring of the whole file: the watcher
    # is legitimately restarted, and "restart bbmon-reboot.path" contains
    # "start bbmon-reboot".
    systemctl_lines = [
        line for line in text.splitlines() if "systemctl" in line and "#" not in line
    ]
    assert systemctl_lines, "expected update.sh to drive systemd at all"
    assert not [line for line in systemctl_lines if "bbmon-reboot.service" in line]


def test_update_restarts_the_watcher_when_unit_files_change() -> None:
    """Reinstalling a path unit without restarting it leaves the old
    configuration running — which for the ordering-cycle fix would mean the
    update reporting success and changing nothing."""
    assert "restart bbmon-reboot.path" in UPDATE.read_text()
