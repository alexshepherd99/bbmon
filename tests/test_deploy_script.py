"""Tests for the changed-file to service mapping in scripts/deploy.sh.

Almost all of deploy.sh needs a Pi to exercise, and that is what gate G1 is
for. This one part does not: deciding which services a changed file affects is
ordinary logic, and getting it wrong produces the least obvious failure mode
the deploy loop has — a deploy that reports success while leaving a service
running the code it had before.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "deploy.sh"

ALL_SERVICES = {"bbmon-pinger", "bbmon-speedtest", "bbmon-web"}

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is needed to source deploy.sh"
)


def services_for(path: str) -> set[str]:
    """Ask the real script, rather than reimplementing its case statement here."""
    result = subprocess.run(
        ["bash", "-c", f'source "{DEPLOY_SCRIPT}"; services_for_path "{path}"'],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(result.stdout.split())


def build_stamp_text(root: Path) -> str:
    """Ask the real script for the line it would record on the Pi."""
    result = subprocess.run(
        ["bash", "-c", f'source "{DEPLOY_SCRIPT}"; build_stamp_text "{root}"'],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def make_repo(path: Path) -> None:
    def run(*args: str) -> None:
        subprocess.run(args, cwd=path, check=True, capture_output=True)

    path.mkdir(parents=True, exist_ok=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "test@example.invalid")
    run("git", "config", "user.name", "Test")
    (path / "tracked.txt").write_text("original\n")
    run("git", "add", "tracked.txt")
    run("git", "commit", "-qm", "first")


def test_the_build_stamp_names_the_script_that_wrote_it(tmp_path: Path) -> None:
    """Two scripts write this file, and which one ran is worth knowing."""
    make_repo(tmp_path)

    assert "by deploy.sh" in build_stamp_text(tmp_path)


def test_a_clean_tree_is_stamped_with_its_commit(tmp_path: Path) -> None:
    make_repo(tmp_path)
    revision = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    stamp = build_stamp_text(tmp_path)

    assert f"from {revision} " in stamp
    assert "+local" not in stamp


def test_a_dirty_tree_is_marked_as_local(tmp_path: Path) -> None:
    """deploy.sh pushes uncommitted work, so the commit alone would be a lie.

    Without the marker the footer would name a commit that is not what was
    copied to the Pi, which is worse than saying nothing at all.
    """
    make_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("edited but not committed\n")

    assert "+local" in build_stamp_text(tmp_path)


def test_an_untracked_file_also_marks_the_tree_as_local(tmp_path: Path) -> None:
    """rsync copies untracked files too, so they are part of what is deployed."""
    make_repo(tmp_path)
    (tmp_path / "extra.py").write_text("print('new module')\n")

    assert "+local" in build_stamp_text(tmp_path)


def test_a_checkout_without_git_is_stamped_unknown(tmp_path: Path) -> None:
    assert "from unknown" in build_stamp_text(tmp_path)


def test_sourcing_the_script_does_not_deploy() -> None:
    """The guard around main() is what makes every other test here safe."""
    result = subprocess.run(
        ["bash", "-c", f'source "{DEPLOY_SCRIPT}"; echo SOURCED'],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "SOURCED" in result.stdout
    assert "Deploying" not in result.stdout


def test_a_web_change_restarts_only_the_web_service() -> None:
    assert services_for("bbmon/web/app.py") == {"bbmon-web"}
    assert services_for("bbmon/web/static/dashboard.js") == {"bbmon-web"}


def test_a_ping_change_restarts_only_the_pinger() -> None:
    assert services_for("bbmon/pinger.py") == {"bbmon-pinger"}
    assert services_for("bbmon/collectors/ping.py") == {"bbmon-pinger"}


def test_a_speedtest_change_restarts_only_the_speedtest_service() -> None:
    assert services_for("bbmon/speedtest.py") == {"bbmon-speedtest"}
    assert services_for("bbmon/collectors/speedtest.py") == {"bbmon-speedtest"}


@pytest.mark.parametrize(
    "path",
    [
        "bbmon/config.py",
        "bbmon/db.py",
        "bbmon/models.py",
        "bbmon/service.py",
        "bbmon/collectors/base.py",
        "bbmon/init.py",
    ],
)
def test_a_shared_module_restarts_everything(path: str) -> None:
    """Every service imports these, so restarting only one leaves the rest stale."""
    assert services_for(path) == ALL_SERVICES


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/phase-1/plan.md",
        "tests/test_db.py",
        "deploy/systemd/bbmon-web.service",
        "scripts/deploy.sh",
    ],
)
def test_files_that_do_not_affect_a_running_service_restart_nothing(path: str) -> None:
    assert services_for(path) == set()


def itemize(lines: str) -> list[str]:
    """Run real rsync --itemize-changes output through the script's filter."""
    result = subprocess.run(
        ["bash", "-c", f'source "{DEPLOY_SCRIPT}"; changed_paths_from_itemize'],
        input=lines,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()


def test_attribute_only_lines_are_not_content_changes() -> None:
    """The G1 bug: a fresh clone's mtimes made every identical file look changed.

    A leading "." means rsync performed no update and only reconciled
    attributes. Counting those restarted all three services on every deploy.
    """
    assert itemize(".f..t...... bbmon/db.py\n") == []
    assert itemize(".f...p..... scripts/deploy.sh\n") == []
    assert itemize(".d..t...... bbmon/web/\n") == []


def test_transferred_files_are_content_changes() -> None:
    assert itemize(">f.st...... bbmon/db.py\n") == ["bbmon/db.py"]
    assert itemize(">f+++++++++ bbmon/init.py\n") == ["bbmon/init.py"]
    assert itemize("cd+++++++++ bbmon/new/\n") == ["bbmon/new/"]


def test_deletions_count_as_changes() -> None:
    """--delete removing a module is exactly when a restart is needed."""
    assert itemize("*deleting   bbmon/old.py\n") == ["bbmon/old.py"]


def test_a_realistic_mixed_run_picks_out_only_the_real_changes() -> None:
    output = (
        ".d..t...... ./\n"
        ".f..t...... README.md\n"
        ">f.st...... bbmon/web/app.py\n"
        ".f..t...... bbmon/db.py\n"
        "*deleting   bbmon/web/static/old.js\n"
        ".f...p..... scripts/deploy.sh\n"
    )
    assert itemize(output) == ["bbmon/web/app.py", "bbmon/web/static/old.js"]
