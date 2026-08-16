"""Waiting for the clock before the first write of the boot.

Requirement 6: a Pi has no real-time clock. Until NTP has been round, its idea
of the time is whatever the filesystem last recorded, so a restart row written
in the first seconds of a boot can carry a timestamp minutes or years wrong —
and unlike a bad measurement, a bad timestamp is not obviously bad on a chart.

``systemd-timesyncd`` publishes exactly the signal needed: it creates
``/run/systemd/timesync`` while it runs, and touches ``synchronized`` inside it
the first time the clock is set from the network. That file's absence is what
``systemd-time-wait-sync`` waits on, so this is the documented mechanism rather
than an inference.

Waiting here rather than in each service is deliberate: only ``bbmon-init``
calls this, and every other unit is ordered after it, so the wait covers the
whole system's first write without four copies of it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

#: Created by systemd-timesyncd while it is running.
TIMESYNC_RUNTIME_DIR = Path("/run/systemd/timesync")

#: Touched inside that directory once the clock has been set from the network.
SYNCHRONISED_FILENAME = "synchronized"

#: How long to wait before carrying on with a clock that may be wrong. Bounded
#: because monitoring that never starts is worse than monitoring with a
#: questionable first timestamp, and the Pi may genuinely have no network.
DEFAULT_TIMEOUT_SECONDS = 120

POLL_INTERVAL_SECONDS = 1.0


def wait_for_synchronised(
    timeout_seconds: float | None = None,
    runtime_dir: str | Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    """Block until the clock has been synchronised, or until it is not worth waiting.

    :param timeout_seconds: How long to wait, defaulting to
        :data:`DEFAULT_TIMEOUT_SECONDS`.
    :param runtime_dir: systemd-timesyncd's runtime directory, defaulting to
        :data:`TIMESYNC_RUNTIME_DIR`.
    :param sleep: Injection point for waiting between polls.
    :param monotonic: Injection point for elapsed-time measurement. Monotonic
        because the thing being waited for is a wall-clock correction, which
        would otherwise move the deadline as it arrived.
    :return: ``True`` only when synchronisation was confirmed. ``False`` means
        the timestamps that follow are not known to be right — either the wait
        timed out, or nothing on this machine publishes the signal.
    """
    if timeout_seconds is None:
        timeout_seconds = DEFAULT_TIMEOUT_SECONDS
    runtime_dir = Path(runtime_dir) if runtime_dir is not None else TIMESYNC_RUNTIME_DIR
    synchronised = runtime_dir / SYNCHRONISED_FILENAME

    if synchronised.exists():
        return True

    if not runtime_dir.is_dir():
        logger.info(
            "%s does not exist, so systemd-timesyncd is not managing this clock; "
            "not waiting for a time sync",
            runtime_dir,
        )
        return False

    logger.info("Waiting up to %gs for the clock to be synchronised", timeout_seconds)
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        sleep(POLL_INTERVAL_SECONDS)
        if synchronised.exists():
            logger.info("The clock is synchronised")
            return True

    logger.warning(
        "The clock is still not synchronised after %gs; carrying on, and "
        "timestamps written from now on may be wrong",
        timeout_seconds,
    )
    return False
