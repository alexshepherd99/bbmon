"""Data types shared between the collectors, the database layer, and the web app."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PingResult:
    """One ping attempt against one target.

    A failed attempt is recorded rather than dropped, so a gap in the dashboard
    always means "nothing ran", never "the network was down".
    """

    timestamp: datetime
    target: str
    latency_ms: float | None
    success: bool


@dataclass(frozen=True)
class HourlyPingSummary:
    """One target's latency distribution over one clock hour.

    The five statistics are exactly what a box plot draws, computed in SQL so
    the whole day's raw pings never travel into Python — a day at the default
    five-second interval is tens of thousands of rows per target, and this
    reduces each hour of them to one box.

    Quartiles are nearest-rank: the sample sitting at the quarter, half and
    three-quarter positions of the sorted latencies, with no interpolation
    between neighbours. For a distribution summary drawn a few pixels wide,
    the difference from an interpolated quartile is not visible.

    ``count`` is how many samples the box was built from, so a box drawn from
    a handful of pings after an outage can be told from a full hour's worth.
    """

    hour: datetime
    target: str
    count: int
    low: float
    q1: float
    median: float
    q3: float
    high: float


@dataclass(frozen=True)
class Restart:
    """One restart of the machine.

    ``expected`` distinguishes a reboot bbmon asked for from one it merely
    noticed afterwards — a power cut, a crash, or someone typing ``reboot``.
    An expected restart is recorded before the reboot happens; an unexpected
    one can only be recorded on the way back up, so its timestamp is when the
    machine came back, not when it went down.
    """

    timestamp: datetime
    expected: bool
    reason: str | None


@dataclass(frozen=True)
class SpeedtestResult:
    """One completed speed test.

    Like :class:`PingResult`, a failed run is recorded rather than dropped. All
    six measurement fields are optional because a failure has none of them —
    ``success`` is what says whether to trust the rest.

    Throughput is stored in megabits per second, the unit an ISP advertises in.
    The measurement tool reports bytes per second, so the conversion happens
    once, in the collector, and never again downstream.
    """

    timestamp: datetime
    download_mbps: float | None
    upload_mbps: float | None
    ping_ms: float | None
    isp: str | None
    server: str | None
    success: bool
