"""The interface every periodic collector implements.

Phase 1 requirement 9: a new periodic test should be addable as a new module
plus a new systemd service, without modifying existing services. A collector
therefore owns three things — when it runs, how it measures, and how its
results are written — and nothing outside it needs to know its table.

This shape is provisional until M3, when the speed test becomes the first
very differently-shaped collector to use it.
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any


class CollectorError(Exception):
    """Raised when a collector cannot run at all, as opposed to measuring a failure.

    A measurement that fails — an unreachable host, a timed-out request — is a
    recorded result, not an exception. This is for conditions that will not
    recover on the next cycle, such as a missing measurement binary.
    """


class Collector(ABC):
    """A periodic measurement and its storage."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs."""

    @property
    @abstractmethod
    def interval_seconds(self) -> int:
        """How often :meth:`collect` should be run."""

    @abstractmethod
    def collect(self) -> Sequence[Any]:
        """Take one round of measurements.

        Failed measurements are returned as results marked unsuccessful, so a
        gap in the data always means the collector did not run.

        :raises CollectorError: if the collector cannot run at all.
        """

    @abstractmethod
    def store(self, conn: sqlite3.Connection, results: Sequence[Any]) -> None:
        """Write a batch of results from :meth:`collect`."""
