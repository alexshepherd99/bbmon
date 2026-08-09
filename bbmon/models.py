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
