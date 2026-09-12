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

import os
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


# The checkout itself, against real git repositories. Found at G4 on
# 2026-09-12: deploy.sh copies files that are new in the development tree, so
# the Pi's checkout gains untracked copies of them, and once they are committed
# the pull that brings them refuses to overwrite them. By then the tracked
# modifications had already been discarded, leaving the checkout on its old
# commit with newer files beside it — code no commit ever contained, waiting
# for the next restart.

GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, **GIT_ENV},
        check=True,
    )
    return result.stdout.strip()


def commit(repo: Path, files: dict[str, str], message: str) -> None:
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(repo, "add", "--all")
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def deployed(tmp_path: Path) -> tuple[Path, Path]:
    """An origin one commit ahead of a checkout that deploy.sh has written over.

    The checkout has the newer commit's changes as a tracked modification and
    its new file as an untracked copy, which is what an rsync of the
    development tree leaves behind.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    commit(origin, {"bbmon/pinger.py": "old\n"}, "first")

    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "-q", str(origin), str(checkout))

    commit(origin, {"bbmon/pinger.py": "new\n", "bbmon/configstore.py": "new\n"}, "second")
    (checkout / "bbmon/pinger.py").write_text("new\n")
    (checkout / "bbmon/configstore.py").write_text("new\n")
    return origin, checkout


def run_update_checkout(checkout: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run update.sh's checkout step as the current user.

    update.sh runs every git command as the checkout's owner through sudo. A
    stand-in sudo that drops its ``-u`` and runs the rest lets the real
    function run here unprivileged.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake_sudo = fake_bin / "sudo"
    fake_sudo.write_text('#!/bin/sh\n[ "$1" = "-u" ] && shift 2\nexec "$@"\n')
    fake_sudo.chmod(0o755)

    env = {**os.environ, **GIT_ENV, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", "-c", f'source "{UPDATE}"; update_checkout "$(id -un)" main'],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_update_replaces_untracked_copies_of_files_it_brings(
    deployed: tuple[Path, Path], tmp_path: Path
) -> None:
    origin, checkout = deployed

    result = run_update_checkout(checkout, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert git(checkout, "rev-parse", "HEAD") == git(origin, "rev-parse", "HEAD")
    assert git(checkout, "status", "--porcelain") == ""
    assert "bbmon/configstore.py" in result.stdout


def test_update_leaves_untracked_files_it_does_not_bring(
    deployed: tuple[Path, Path], tmp_path: Path
) -> None:
    """They are not in the way of the pull, and not ours to delete."""
    _, checkout = deployed
    (checkout / "notes.txt").write_text("mine\n")

    result = run_update_checkout(checkout, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "notes.txt").read_text() == "mine\n"


def test_a_failed_fetch_discards_nothing(
    deployed: tuple[Path, Path], tmp_path: Path
) -> None:
    """With no network, the checkout must stay exactly as deploy.sh left it:
    discarding first and failing second is the mixed state this guards."""
    _, checkout = deployed
    git(checkout, "remote", "set-url", "origin", str(tmp_path / "nowhere"))

    result = run_update_checkout(checkout, tmp_path)

    assert result.returncode != 0
    # git empties FETCH_HEAD when a fetch fails, so the fast-forward check
    # would stop the update too — but by blaming a rewritten history, which
    # sends whoever reads it looking for the wrong problem.
    assert "could not fetch" in result.stderr
    assert (checkout / "bbmon/pinger.py").read_text() == "new\n"
    assert (checkout / "bbmon/configstore.py").exists()


def test_a_diverged_history_discards_nothing(
    deployed: tuple[Path, Path], tmp_path: Path
) -> None:
    """A rewritten origin cannot be fast-forwarded to; that is found before
    anything is thrown away, not after."""
    origin, checkout = deployed
    # Rewrite the commit the checkout is on, not just the one after it: a new
    # second commit on the old first would still be a fast-forward from here.
    git(origin, "reset", "-q", "--hard", "HEAD~1")
    (origin / "bbmon/pinger.py").write_text("rewritten\n")
    git(origin, "commit", "-q", "--all", "--amend", "-m", "rewritten first")

    result = run_update_checkout(checkout, tmp_path)

    assert result.returncode != 0
    assert (checkout / "bbmon/pinger.py").read_text() == "new\n"
    assert (checkout / "bbmon/configstore.py").exists()
