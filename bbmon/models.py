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
