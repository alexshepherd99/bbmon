"""Tests for the ping collector.

The fixtures below are real output captured from iputils ping on the
development container, not invented text.
"""

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from bbmon import db
from bbmon.collectors.ping import CollectorError, PingCollector

SUCCESS_OUTPUT = """PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=114 time=14.0 ms

--- 8.8.8.8 ping statistics ---
1 packets transmitted, 1 received, 0% packet loss, time 0ms
rtt min/avg/max/mdev = 13.993/13.993/13.993/0.000 ms
"""

UNREACHABLE_OUTPUT = """PING 192.0.2.1 (192.0.2.1) 56(84) bytes of data.

--- 192.0.2.1 ping statistics ---
1 packets transmitted, 0 received, 100% packet loss, time 0ms
"""

DNS_FAILURE_STDERR = "ping: no-such-host.invalid: Name or service not known\n"

FIXED_TIME = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)


class FakeRunner:
    """Stands in for subprocess.run, recording how it was called."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = SUCCESS_OUTPUT,
        stderr: str = "",
        raises: Exception | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raises = raises
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def __call__(self, argv: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            argv, self.returncode, self.stdout, self.stderr
        )


def collector(runner: FakeRunner, targets: tuple[str, ...] = ("8.8.8.8",)) -> PingCollector:
    return PingCollector(
        targets=targets,
        interval_seconds=5,
        runner=runner,
        clock=lambda: FIXED_TIME,
    )


def test_successful_ping_records_the_latency() -> None:
    (result,) = collector(FakeRunner()).collect()

    assert result.success is True
    assert result.latency_ms == 14.0
    assert result.target == "8.8.8.8"
    assert result.timestamp == FIXED_TIME


def test_unreachable_host_is_recorded_as_a_failure() -> None:
    runner = FakeRunner(returncode=1, stdout=UNREACHABLE_OUTPUT)

    (result,) = collector(runner).collect()

    assert result.success is False
    assert result.latency_ms is None


def test_dns_failure_is_recorded_as_a_failure() -> None:
    runner = FakeRunner(returncode=2, stdout="", stderr=DNS_FAILURE_STDERR)

    (result,) = collector(runner).collect()

    assert result.success is False
    assert result.latency_ms is None


def test_unparseable_output_is_a_failure_even_when_ping_exits_zero() -> None:
    """A zero exit with no timing is not a measurement, so it is not recorded as one."""
    runner = FakeRunner(returncode=0, stdout="something entirely unexpected\n")

    (result,) = collector(runner).collect()

    assert result.success is False
    assert result.latency_ms is None


def test_timeout_is_recorded_as_a_failure() -> None:
    runner = FakeRunner(raises=subprocess.TimeoutExpired(cmd="ping", timeout=5))

    (result,) = collector(runner).collect()

    assert result.success is False
    assert result.latency_ms is None


def test_a_missing_ping_binary_is_fatal_rather_than_a_silent_failure() -> None:
    """This can never recover, so it must not look like the network being down."""
    runner = FakeRunner(raises=FileNotFoundError("ping"))

    with pytest.raises(CollectorError, match="ping"):
        collector(runner).collect()


def test_one_result_per_target_in_configured_order() -> None:
    runner = FakeRunner()

    results = collector(runner, targets=("8.8.8.8", "1.1.1.1", "google.com")).collect()

    assert [r.target for r in results] == ["8.8.8.8", "1.1.1.1", "google.com"]


def test_the_command_is_an_argv_list_and_never_a_shell_string() -> None:
    runner = FakeRunner()

    collector(runner).collect()

    (argv, kwargs) = runner.calls[0]
    assert isinstance(argv, list)
    assert argv[0] == "ping"
    assert kwargs.get("shell", False) is False


def test_a_hostile_target_stays_one_argv_element() -> None:
    """Targets come from user-editable config, so this is the injection path."""
    hostile = "8.8.8.8; rm -rf /"
    runner = FakeRunner()

    collector(runner, targets=(hostile,)).collect()

    (argv, _) = runner.calls[0]
    assert argv[-1] == hostile
    assert argv.count(hostile) == 1


def test_a_single_ping_is_requested_with_a_bounded_timeout() -> None:
    runner = FakeRunner()

    collector(runner).collect()

    (argv, kwargs) = runner.calls[0]
    assert "-c" in argv and argv[argv.index("-c") + 1] == "1"
    assert kwargs.get("timeout") is not None


def test_store_writes_results_to_the_database(tmp_path: Path) -> None:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    subject = collector(FakeRunner())
    results = subject.collect()

    with db.connect(path) as conn:
        subject.store(conn, results)
        read_back = db.recent_ping_results(conn, since=FIXED_TIME)

    assert [r.latency_ms for r in read_back] == [14.0]


def test_the_collector_reports_its_name_and_interval() -> None:
    subject = collector(FakeRunner())

    assert subject.name == "ping"
    assert subject.interval_seconds == 5
