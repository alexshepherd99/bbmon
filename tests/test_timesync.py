"""Tests for waiting on the clock before the first write of the boot."""

from pathlib import Path

import pytest

from bbmon import timesync


class FakeClock:
    """A monotonic clock that only advances when something sleeps on it."""

    def __init__(self) -> None:
        self.elapsed = 0.0
        self.sleeps = 0

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds
        self.sleeps += 1


def synchronised_dir(tmp_path: Path) -> Path:
    path = tmp_path / "timesync"
    path.mkdir()
    (path / timesync.SYNCHRONISED_FILENAME).touch()
    return path


def waiting_dir(tmp_path: Path) -> Path:
    """timesyncd is running but has not synchronised yet."""
    path = tmp_path / "timesync"
    path.mkdir()
    return path


def test_an_already_synchronised_clock_is_not_waited_for(tmp_path: Path) -> None:
    clock = FakeClock()

    assert timesync.wait_for_synchronised(
        runtime_dir=synchronised_dir(tmp_path), sleep=clock.sleep, monotonic=clock.monotonic
    )
    assert clock.sleeps == 0


def test_nothing_is_waited_for_when_timesyncd_is_not_running(tmp_path: Path) -> None:
    """On the Chromebook there is no timesyncd, and a dev run must not stall.

    Reported as unconfirmed rather than synchronised: the clock may well be
    right, but nothing here has established that.
    """
    clock = FakeClock()

    assert not timesync.wait_for_synchronised(
        runtime_dir=tmp_path / "absent", sleep=clock.sleep, monotonic=clock.monotonic
    )
    assert clock.sleeps == 0


def test_it_returns_as_soon_as_the_clock_synchronises(tmp_path: Path) -> None:
    runtime_dir = waiting_dir(tmp_path)
    clock = FakeClock()

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if clock.sleeps == 3:
            (runtime_dir / timesync.SYNCHRONISED_FILENAME).touch()

    assert timesync.wait_for_synchronised(
        timeout_seconds=120, runtime_dir=runtime_dir, sleep=sleep, monotonic=clock.monotonic
    )
    assert clock.sleeps == 3


def test_it_gives_up_rather_than_blocking_the_boot(tmp_path: Path) -> None:
    """A Pi with no RTC and no network would otherwise never start monitoring."""
    clock = FakeClock()

    assert not timesync.wait_for_synchronised(
        timeout_seconds=10,
        runtime_dir=waiting_dir(tmp_path),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert clock.elapsed == pytest.approx(10, abs=timesync.POLL_INTERVAL_SECONDS)


def test_giving_up_says_so(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Timestamps written after this are suspect, so it cannot pass silently."""
    clock = FakeClock()

    timesync.wait_for_synchronised(
        timeout_seconds=10,
        runtime_dir=waiting_dir(tmp_path),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert "not synchronised" in caplog.text
