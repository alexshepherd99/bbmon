"""Tests for the shared collector service loop and its buffered write path."""

import os
import signal
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Sequence

import pytest

from bbmon import db
from bbmon.collectors.base import CollectorError
from bbmon.db import DatabaseError
from bbmon.models import PingResult
from bbmon.service import CollectorService, run_until_stopped

START = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    """A monotonic clock that only advances when the service sleeps."""

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def sleep(self, seconds: float) -> None:
        self.seconds += seconds


class FakeCollector:
    """Returns one canned result per cycle and records how it was used."""

    def __init__(self, interval_seconds: int = 5, store_error: bool = False) -> None:
        self._interval_seconds = interval_seconds
        self.store_error = store_error
        self.cycles = 0
        self.stored: list[PingResult] = []

    name = "ping"

    @property
    def interval_seconds(self) -> int:
        return self._interval_seconds

    def collect(self) -> list[PingResult]:
        self.cycles += 1
        return [
            PingResult(
                timestamp=START + timedelta(seconds=self.cycles),
                target="8.8.8.8",
                latency_ms=float(self.cycles),
                success=True,
            )
        ]

    def store(self, conn: sqlite3.Connection, results: Sequence[PingResult]) -> None:
        if self.store_error:
            raise DatabaseError("disk is having a bad day")
        self.stored.extend(results)
        db.insert_ping_results(conn, results)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    return path


def stop_after(cycles: int):
    remaining = {"n": cycles}

    def should_continue() -> bool:
        if remaining["n"] <= 0:
            return False
        remaining["n"] -= 1
        return True

    return should_continue


def service(
    database: Path, collector: FakeCollector, clock: FakeClock, flush: int = 60
) -> CollectorService:
    return CollectorService(
        collector=collector,
        database_path=database,
        flush_interval_seconds=flush,
        sleep=clock.sleep,
        monotonic=clock,
    )


def row_count(database: Path) -> int:
    with db.connect(database) as conn:
        return conn.execute("SELECT count(*) FROM ping_results").fetchone()[0]


def test_results_are_buffered_rather_than_written_every_cycle(database: Path) -> None:
    """Writing on every ping is what wears the SD card out."""
    clock = FakeClock()
    collector = FakeCollector(interval_seconds=5)

    service(database, collector, clock, flush=60).run(stop_after(3), flush_on_exit=False)

    assert collector.cycles == 3
    # One write, not three. The first cycle is flushed immediately so the
    # dashboard is not blank on a fresh start; the two after it wait for the
    # interval, which is the wear the buffering exists to avoid.
    assert row_count(database) == 1


def test_the_first_cycle_is_written_without_waiting_for_the_interval(
    database: Path,
) -> None:
    """A freshly started service otherwise leaves the dashboard blank.

    Nothing was on disk until the first flush, so for a minute after every
    start — and every deploy, which restarts the services — the page showed no
    data at all and looked broken rather than new.
    """
    clock = FakeClock()
    collector = FakeCollector(interval_seconds=5)

    service(database, collector, clock, flush=60).run(stop_after(1), flush_on_exit=False)

    assert row_count(database) == 1


def test_the_buffer_is_written_once_the_flush_interval_elapses(database: Path) -> None:
    clock = FakeClock()
    collector = FakeCollector(interval_seconds=5)

    service(database, collector, clock, flush=30).run(stop_after(8), flush_on_exit=False)

    # Six sleeps of 5s put the clock on 30, so the flush check first passes
    # after the seventh collect. The eighth is still buffered.
    assert row_count(database) == 7


def test_a_flush_clears_the_buffer_so_rows_are_not_written_twice(
    database: Path,
) -> None:
    clock = FakeClock()
    collector = FakeCollector(interval_seconds=5)

    service(database, collector, clock, flush=30).run(stop_after(12))

    assert row_count(database) == 12


def test_buffered_results_are_written_when_the_loop_stops(database: Path) -> None:
    """A clean shutdown must not throw away what has not been flushed yet."""
    clock = FakeClock()
    collector = FakeCollector(interval_seconds=5)

    service(database, collector, clock, flush=3600).run(stop_after(4))

    assert row_count(database) == 4


def test_a_failed_write_keeps_the_results_for_the_next_attempt(
    database: Path,
) -> None:
    clock = FakeClock()
    collector = FakeCollector(interval_seconds=5, store_error=True)
    subject = service(database, collector, clock, flush=10)

    subject.run(stop_after(6))

    assert row_count(database) == 0
    assert len(subject.buffer) == 6


def test_a_failed_write_does_not_stop_the_loop(database: Path) -> None:
    clock = FakeClock()
    collector = FakeCollector(interval_seconds=5, store_error=True)

    service(database, collector, clock, flush=10).run(stop_after(6))

    assert collector.cycles == 6


def test_the_buffer_is_capped_so_a_long_outage_cannot_exhaust_memory(
    database: Path,
) -> None:
    clock = FakeClock()
    collector = FakeCollector(interval_seconds=5, store_error=True)
    subject = service(database, collector, clock, flush=10)
    subject.max_buffered_results = 4

    subject.run(stop_after(10))

    assert len(subject.buffer) == 4
    # The oldest are dropped, so what survives is the most recent.
    assert [r.latency_ms for r in subject.buffer] == [7.0, 8.0, 9.0, 10.0]


def test_a_stop_requested_mid_sleep_ends_the_loop_and_still_flushes(
    database: Path,
) -> None:
    """How the service is actually wired: systemd's SIGTERM sets an event, and
    the sleep is that event's wait, so a stop is not left waiting out the
    interval and nothing buffered is lost."""
    stopping = threading.Event()
    collector = FakeCollector(interval_seconds=5)

    subject = CollectorService(
        collector=collector,
        database_path=database,
        flush_interval_seconds=3600,
        sleep=lambda _seconds: stopping.set(),
        monotonic=FakeClock(),
    )
    subject.run(should_continue=lambda: not stopping.is_set())

    assert collector.cycles == 1
    assert row_count(database) == 1


def test_work_sharing_the_loop_runs_once_a_cycle(database: Path) -> None:
    """M4 hangs the reboot due-check here rather than on a systemd timer of its own."""
    clock = FakeClock()
    calls = []

    CollectorService(
        collector=FakeCollector(interval_seconds=5),
        database_path=database,
        flush_interval_seconds=60,
        sleep=clock.sleep,
        monotonic=clock,
        between_cycles=lambda: calls.append(clock.seconds),
    ).run(stop_after(4))

    assert len(calls) == 4


def test_work_sharing_the_loop_runs_after_the_flush(database: Path) -> None:
    """The reboot check can take the machine down, so nothing may still be buffered."""
    clock = FakeClock()
    rows_seen = []

    CollectorService(
        collector=FakeCollector(interval_seconds=5),
        database_path=database,
        flush_interval_seconds=0,
        sleep=clock.sleep,
        monotonic=clock,
        between_cycles=lambda: rows_seen.append(row_count(database)),
    ).run(stop_after(2))

    assert rows_seen == [1, 2]


def test_the_loop_sleeps_for_the_collector_interval(database: Path) -> None:
    clock = FakeClock()
    collector = FakeCollector(interval_seconds=7)

    service(database, collector, clock).run(stop_after(3))

    assert clock.seconds == 21


@pytest.fixture
def restore_signal_handlers() -> Iterator[None]:
    """run_until_stopped installs process-wide handlers; put them back after."""
    previous = {
        number: signal.getsignal(number)
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    }
    yield
    for number, handler in previous.items():
        signal.signal(number, handler)


class SelfStoppingCollector(FakeCollector):
    """Signals the process mid-cycle, the way systemd stops a real service."""

    def __init__(self, signal_number: int) -> None:
        super().__init__(interval_seconds=5)
        self._signal_number = signal_number

    def collect(self) -> list[PingResult]:
        results = super().collect()
        os.kill(os.getpid(), self._signal_number)
        return results


class UnrunnableCollector(FakeCollector):
    """A collector whose tool is missing, so it will not recover next cycle."""

    def collect(self) -> list[PingResult]:
        raise CollectorError("the measurement binary is not installed")


@pytest.mark.parametrize("signal_number", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_stops_the_loop_and_still_writes_what_was_buffered(
    database: Path, restore_signal_handlers: None, signal_number: int
) -> None:
    """The M1 data-loss bug: SIGTERM killed the process before the flush ran.

    Buffering is set to an hour so nothing would be written by the ordinary
    flush path — the row can only be on disk because the shutdown flushed it.
    """
    collector = SelfStoppingCollector(signal_number)

    exit_code = run_until_stopped(
        collector, database, flush_interval_seconds=3600
    )

    assert exit_code == 0
    assert collector.cycles == 1
    assert row_count(database) == 1


def test_sighup_comes_back_to_reload_and_still_writes_what_was_buffered(
    database: Path, restore_signal_handlers: None
) -> None:
    """Requirement 2's reload, which the caller answers by rebuilding.

    It has to leave the loop as tidily as a stop does: the caller is about to
    build a new collector, and anything this one was still holding would
    otherwise go with it. Buffering is set to an hour, so the row on disk can
    only have been written on the way out.
    """
    reloading = threading.Event()
    collector = SelfStoppingCollector(signal.SIGHUP)

    exit_code = run_until_stopped(
        collector, database, flush_interval_seconds=3600, reloading=reloading
    )

    assert exit_code == 0
    assert reloading.is_set()
    assert collector.cycles == 1
    assert row_count(database) == 1


def test_sighup_is_left_alone_for_a_service_that_cannot_reload(
    database: Path, restore_signal_handlers: None
) -> None:
    """Its default terminates the process, which is the honest answer.

    A handler installed here regardless would swallow the signal and leave a
    service that says it reloaded and did not.
    """
    before = signal.getsignal(signal.SIGHUP)

    run_until_stopped(SelfStoppingCollector(signal.SIGTERM), database)

    assert signal.getsignal(signal.SIGHUP) is before


def test_a_collector_that_cannot_run_at_all_exits_non_zero(
    database: Path, restore_signal_handlers: None
) -> None:
    """systemd's Restart=on-failure needs a failed start to look like one."""
    exit_code = run_until_stopped(UnrunnableCollector(), database)

    assert exit_code == 1


def test_flushing_every_cycle_writes_without_waiting_for_an_interval(
    database: Path,
) -> None:
    """What the speed test uses: one row every few hours, held in memory for none of it."""
    clock = FakeClock()
    collector = FakeCollector(interval_seconds=5)

    service(database, collector, clock, flush=0).run(
        stop_after(3), flush_on_exit=False
    )

    assert row_count(database) == 3
