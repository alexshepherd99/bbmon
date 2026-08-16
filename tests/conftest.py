"""Fixtures applied to the whole suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from bbmon import reboot, timesync


@pytest.fixture(autouse=True)
def isolated_machine_state(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the suite off this machine's uptime and clock-sync state.

    Both are read at module scope by paths in /proc and /run, and both now
    change behaviour: a reboot becomes due once the machine has been up longer
    than ``reboot.interval_days``. Without this, a test asserting that a speed
    test runs would pass on a laptop rebooted this morning and fail on one
    that has been up a week — which is exactly what happened.

    The default here is a machine freshly booted two minutes ago, with nothing
    managing its clock. Tests that care override it.
    """
    state: Path = tmp_path_factory.mktemp("machine")

    uptime = state / "uptime"
    uptime.write_text("120.0 60.0\n")
    monkeypatch.setattr(reboot, "UPTIME_PATH", uptime)
    monkeypatch.setattr(timesync, "TIMESYNC_RUNTIME_DIR", state / "no-timesyncd")
