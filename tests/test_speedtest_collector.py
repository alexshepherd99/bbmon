"""Tests for the Ookla Speedtest CLI collector.

The real binary is never invoked here: every test injects a runner returning
canned output, so the suite needs no network and no Ookla licence.
"""

import json
import subprocess
from datetime import datetime, timezone

import pytest

from bbmon.collectors.base import CollectorError
from bbmon.collectors.speedtest import SpeedtestCollector

NOW = datetime(2026, 8, 12, 9, 0, 0, tzinfo=timezone.utc)

# Ookla reports throughput in BYTES per second. 6_062_500 B/s is 48.5 Mbps and
# 1_175_000 B/s is 9.4 Mbps; the numbers differ by a factor of eight so a
# missing conversion cannot pass unnoticed.
OOKLA_OUTPUT = json.dumps(
    {
        "type": "result",
        "timestamp": "2026-08-12T09:00:00Z",
        "ping": {"jitter": 0.9, "latency": 14.2},
        "download": {"bandwidth": 6_062_500, "bytes": 74_000_000, "elapsed": 15000},
        "upload": {"bandwidth": 1_175_000, "bytes": 14_000_000, "elapsed": 15000},
        "packetLoss": 0,
        "isp": "Example Broadband",
        "server": {"id": 1234, "name": "Example Telecom", "location": "London"},
        "result": {"id": "abc", "url": "https://www.speedtest.net/result/c/abc"},
    }
)


def runner_returning(stdout: str, returncode: int = 0, stderr: str = ""):
    def run(argv, **kwargs):
        run.argv = argv
        run.kwargs = kwargs
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    return run


def runner_raising(error: Exception):
    def run(argv, **kwargs):
        raise error

    return run


def collector(runner, interval_hours: int = 3) -> SpeedtestCollector:
    return SpeedtestCollector(
        interval_hours=interval_hours, runner=runner, clock=lambda: NOW
    )


def test_a_successful_run_is_parsed_into_a_result() -> None:
    results = collector(runner_returning(OOKLA_OUTPUT)).collect()

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].ping_ms == 14.2
    assert results[0].isp == "Example Broadband"
    assert results[0].timestamp == NOW


def test_bandwidth_is_converted_from_bytes_per_second_to_megabits() -> None:
    """Ookla reports bytes/s; an ISP advertises megabits/s."""
    results = collector(runner_returning(OOKLA_OUTPUT)).collect()

    assert results[0].download_mbps == pytest.approx(48.5)
    assert results[0].upload_mbps == pytest.approx(9.4)


def test_the_server_name_and_location_are_both_recorded() -> None:
    results = collector(runner_returning(OOKLA_OUTPUT)).collect()

    assert results[0].server == "Example Telecom (London)"


def test_the_licence_prompts_are_accepted_so_nothing_blocks_on_stdin() -> None:
    """A first run on a fresh Pi otherwise waits forever for a keypress."""
    runner = runner_returning(OOKLA_OUTPUT)

    collector(runner).collect()

    assert "--accept-license" in runner.argv
    assert "--accept-gdpr" in runner.argv


def test_the_command_is_an_argv_list_with_no_shell() -> None:
    runner = runner_returning(OOKLA_OUTPUT)

    collector(runner).collect()

    assert isinstance(runner.argv, list)
    assert runner.argv[0] == "speedtest"
    assert runner.kwargs.get("shell") is not True


def test_a_non_zero_exit_is_recorded_as_a_failed_result() -> None:
    """Requirement 5: a failure is a row, so a dashboard gap is unambiguous.

    The output is deliberately a complete, parseable result: a partial run that
    exits non-zero must not be believed just because it left usable-looking
    JSON behind. With empty output this would pass without the exit code ever
    being checked, since there would be nothing to parse either way.
    """
    runner = runner_returning(
        OOKLA_OUTPUT, returncode=2, stderr="Cannot reach test server"
    )

    results = collector(runner).collect()

    assert len(results) == 1
    assert results[0].success is False
    assert results[0].download_mbps is None
    assert results[0].upload_mbps is None
    assert results[0].timestamp == NOW


def test_no_output_at_all_is_recorded_as_a_failed_result() -> None:
    """The other failure path: a clean exit that produced nothing."""
    results = collector(runner_returning("")).collect()

    assert results[0].success is False


def test_unparseable_output_is_recorded_as_a_failed_result() -> None:
    results = collector(runner_returning("not json at all")).collect()

    assert results[0].success is False


def test_output_missing_the_expected_fields_is_recorded_as_a_failure() -> None:
    """A clean exit with a shape we do not recognise is still not a measurement."""
    results = collector(runner_returning(json.dumps({"type": "log"}))).collect()

    assert results[0].success is False


def test_a_timeout_is_recorded_as_a_failed_result() -> None:
    runner = runner_raising(subprocess.TimeoutExpired(cmd="speedtest", timeout=180))

    results = collector(runner).collect()

    assert results[0].success is False


def test_the_result_is_found_even_when_progress_lines_precede_it() -> None:
    """Defensive: the JSON object is located rather than assumed to be alone."""
    noisy = '{"type":"download","progress":0.5}\n' + OOKLA_OUTPUT + "\n"

    results = collector(runner_returning(noisy)).collect()

    assert results[0].success is True
    assert results[0].download_mbps == pytest.approx(48.5)


def test_a_missing_binary_raises_rather_than_recording_a_failure() -> None:
    """Not a measurement failure: it will not recover on the next cycle."""
    runner = runner_raising(FileNotFoundError("speedtest"))

    with pytest.raises(CollectorError):
        collector(runner).collect()


def test_the_interval_is_reported_in_seconds() -> None:
    """Configured in hours; the service loop sleeps in seconds."""
    assert collector(runner_returning(OOKLA_OUTPUT), interval_hours=3).interval_seconds == 10800


def test_the_collector_is_named_for_its_logs() -> None:
    assert collector(runner_returning(OOKLA_OUTPUT)).name == "speedtest"
