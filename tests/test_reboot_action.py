"""Tests for asking the machine to reboot, and for deciding when it is due."""

import subprocess
from pathlib import Path

import pytest

from bbmon import reboot
from bbmon.reboot import (
    NoOpReboot,
    RebootError,
    RebootScheduler,
    SystemctlReboot,
    action_from_environment,
)


class FakeAction(reboot.RebootAction):
    """Records that a reboot was asked for, optionally refusing it."""

    def __init__(self, error: str | None = None) -> None:
        self.calls = 0
        self.error = error

    def reboot(self) -> None:
        self.calls += 1
        if self.error:
            raise RebootError(self.error)


class FakeRun:
    """Stands in for subprocess.run at the one place bbmon shells out to sudo."""

    def __init__(self, returncode: int = 0, error: Exception | None = None) -> None:
        self.returncode = returncode
        self.error = error
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        self.commands.append(command)
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(command, self.returncode, "", "")


@pytest.fixture
def request_file(tmp_path: Path) -> Path:
    return tmp_path / "reboot-requested"


def test_the_request_file_sits_beside_the_database() -> None:
    """The state directory is the only path the units leave writable."""
    assert reboot.request_file_path("/var/lib/bbmon/bbmon.db") == Path(
        "/var/lib/bbmon/reboot-requested"
    )


def test_requesting_a_reboot_leaves_the_reason_behind_first(
    request_file: Path,
) -> None:
    """The reason has to outlive the process that decided on it."""
    action = FakeAction()

    reboot.request_reboot(request_file, action, reason="scheduled after 3 days")

    assert request_file.read_text() == "scheduled after 3 days"
    assert action.calls == 1


def test_a_refused_reboot_takes_the_request_back(request_file: Path) -> None:
    """Left behind, it would make the next power cut look planned — for ever."""
    action = FakeAction(error="sudo said no")

    with pytest.raises(RebootError):
        reboot.request_reboot(request_file, action, reason="scheduled")

    assert not request_file.exists()


def test_the_no_op_action_does_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """Requirement 10: the development machine must not be rebootable by bbmon."""
    NoOpReboot().reboot()

    assert "no-op" in caplog.text


def test_the_real_action_runs_one_fixed_command() -> None:
    """plan.md's security posture: no shell, no wildcard, no argument we supply."""
    run = FakeRun()

    SystemctlReboot(run=run).reboot()

    assert run.commands == [list(SystemctlReboot.COMMAND)]
    assert all(isinstance(word, str) for word in run.commands[0])


def test_a_failing_systemctl_is_an_error_rather_than_a_silent_no_reboot() -> None:
    """The quiet failure this project keeps meeting: success reported, nothing done."""
    run = FakeRun(returncode=1)

    with pytest.raises(RebootError):
        SystemctlReboot(run=run).reboot()


def test_a_missing_sudo_is_an_error_too() -> None:
    run = FakeRun(error=FileNotFoundError("sudo"))

    with pytest.raises(RebootError):
        SystemctlReboot(run=run).reboot()


def test_the_development_default_is_the_no_op() -> None:
    assert isinstance(action_from_environment({}), NoOpReboot)


def test_the_pi_units_ask_for_the_real_thing() -> None:
    assert isinstance(
        action_from_environment({reboot.REBOOT_ACTION_ENV_VAR: "systemctl"}),
        SystemctlReboot,
    )


def test_an_unrecognised_setting_is_refused_rather_than_assumed() -> None:
    """A typo in a unit file must not silently disable reboots."""
    with pytest.raises(RebootError):
        action_from_environment({reboot.REBOOT_ACTION_ENV_VAR: "sytemctl"})


def scheduler(
    request_file: Path, action: FakeAction, uptime: float, interval_days: int = 3
) -> RebootScheduler:
    return RebootScheduler(
        interval_days=interval_days,
        action=action,
        request_path=request_file,
        uptime=lambda: uptime,
    )


def test_no_reboot_before_the_interval_has_elapsed(request_file: Path) -> None:
    action = FakeAction()

    scheduler(request_file, action, uptime=3 * 86400 - 60).check()

    assert action.calls == 0
    assert not request_file.exists()


def test_a_reboot_once_the_interval_has_elapsed(request_file: Path) -> None:
    """Requirement 6: every reboot.interval_days."""
    action = FakeAction()

    scheduler(request_file, action, uptime=3 * 86400).check()

    assert action.calls == 1
    assert "3 days" in request_file.read_text()


def test_the_interval_is_measured_from_boot(request_file: Path) -> None:
    """Uptime, not the last recorded restart.

    A power cut restarts the clock as surely as a planned reboot does, and a Pi
    that came up ten minutes ago does not need rebooting whatever the table says.
    """
    action = FakeAction()

    scheduler(request_file, action, uptime=600, interval_days=1).check()

    assert action.calls == 0


def test_a_reboot_is_asked_for_only_once(request_file: Path) -> None:
    """The machine takes a minute to go down; the loop keeps running meanwhile."""
    action = FakeAction()
    subject = scheduler(request_file, action, uptime=99 * 86400)

    subject.check()
    subject.check()
    subject.check()

    assert action.calls == 1


def test_an_unreadable_uptime_does_not_take_the_pinger_down(
    request_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """This runs inside the ping loop, which must survive it."""

    def broken_uptime() -> float:
        raise RebootError("no /proc/uptime")

    RebootScheduler(
        interval_days=3,
        action=FakeAction(),
        request_path=request_file,
        uptime=broken_uptime,
    ).check()

    assert "reboot" in caplog.text.lower()


def test_a_refused_reboot_is_retried_on_a_later_cycle(request_file: Path) -> None:
    """A one-off sudo failure should not disable rebooting until the next boot."""
    action = FakeAction(error="sudo said no")
    subject = scheduler(request_file, action, uptime=99 * 86400)

    subject.check()
    subject.check()

    assert action.calls == 2


def test_time_left_before_the_reboot_is_due(request_file: Path) -> None:
    """What the speed test consults before starting a run it cannot finish."""
    subject = scheduler(request_file, FakeAction(), uptime=3 * 86400 - 90)

    assert subject.seconds_until_due() == pytest.approx(90)


def test_time_left_is_never_negative(request_file: Path) -> None:
    """Overdue is zero, so callers can treat it as "any moment now"."""
    subject = scheduler(request_file, FakeAction(), uptime=10 * 86400)

    assert subject.seconds_until_due() == 0
