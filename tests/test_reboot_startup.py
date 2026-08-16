"""Tests for detecting, on the way up, whether the last restart was ours."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bbmon import db, reboot
from bbmon.models import Restart
from bbmon.reboot import RebootError


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    return path


@pytest.fixture
def request_file(tmp_path: Path) -> Path:
    return tmp_path / "reboot-requested"


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
BOOTED = NOW - timedelta(minutes=2)


def record(database: Path, request_file: Path, now: datetime = NOW) -> Restart | None:
    with db.connect(database) as conn:
        return reboot.record_startup(conn, request_file, boot_time=BOOTED, now=now)


def stored(database: Path) -> Restart | None:
    with db.connect(database) as conn:
        return db.latest_restart(conn)


def test_uptime_is_read_from_the_first_field(tmp_path: Path) -> None:
    """/proc/uptime is "<uptime> <idle>"; the second field is not ours."""
    path = tmp_path / "uptime"
    path.write_text("330837.56 293286.69\n")

    assert reboot.uptime_seconds(path) == pytest.approx(330837.56)


def test_an_unreadable_uptime_is_an_error_rather_than_a_guess(tmp_path: Path) -> None:
    with pytest.raises(RebootError):
        reboot.uptime_seconds(tmp_path / "absent")


def test_an_unparseable_uptime_is_an_error_rather_than_a_guess(tmp_path: Path) -> None:
    path = tmp_path / "uptime"
    path.write_text("not a number\n")

    with pytest.raises(RebootError):
        reboot.uptime_seconds(path)


def test_boot_time_is_now_less_the_uptime() -> None:
    assert reboot.boot_time(now=NOW, uptime=120.0) == NOW - timedelta(seconds=120)


def test_a_restart_nobody_asked_for_is_recorded_as_unexpected(
    database: Path, request_file: Path
) -> None:
    """Requirement 6: power loss, a crash, or a manual reboot all land here."""
    recorded = record(database, request_file)

    assert recorded is not None
    assert recorded.expected is False
    assert stored(database) == recorded


def test_a_restart_bbmon_asked_for_is_recorded_as_expected(
    database: Path, request_file: Path
) -> None:
    request_file.write_text("scheduled reboot after 3 days")

    recorded = record(database, request_file)

    assert recorded is not None
    assert recorded.expected is True
    assert recorded.reason == "scheduled reboot after 3 days"


def test_the_request_is_consumed_so_the_next_restart_is_judged_afresh(
    database: Path, request_file: Path
) -> None:
    """Left in place, one planned reboot would excuse every later power cut."""
    request_file.write_text("scheduled reboot after 3 days")

    record(database, request_file)

    assert not request_file.exists()


def test_an_empty_request_still_counts_as_expected(
    database: Path, request_file: Path
) -> None:
    """The file's existence is the signal; its contents are only the reason."""
    request_file.write_text("")

    recorded = record(database, request_file)

    assert recorded is not None
    assert recorded.expected is True
    assert recorded.reason


def test_a_service_restart_within_the_same_boot_records_nothing(
    database: Path, request_file: Path
) -> None:
    """systemd re-runs the init unit whenever a service is restarted by hand.

    The machine has not rebooted, so a second row would invent a restart that
    never happened.
    """
    record(database, request_file)

    assert record(database, request_file, now=NOW + timedelta(minutes=5)) is None

    with db.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM restarts").fetchone()[0] == 1


def test_a_later_boot_is_recorded_even_though_earlier_ones_were(
    database: Path, request_file: Path
) -> None:
    """The guard above is "this boot", not "any boot"."""
    with db.connect(database) as conn:
        db.insert_restart(
            conn, Restart(BOOTED - timedelta(days=3), expected=True, reason="earlier")
        )

    recorded = record(database, request_file)

    assert recorded is not None
    assert recorded.expected is False


def test_an_earlier_expected_reboot_does_not_excuse_a_later_power_cut(
    database: Path, request_file: Path
) -> None:
    """The failure the consumed request file exists to prevent.

    A planned reboot three days ago left an ``expected`` row as the newest in
    the table. Reading only that row, this power cut looks planned too.
    """
    with db.connect(database) as conn:
        db.insert_restart(
            conn,
            Restart(BOOTED - timedelta(days=3), expected=True, reason="scheduled"),
        )

    recorded = record(database, request_file)

    assert recorded is not None
    assert recorded.expected is False


def test_the_restart_is_timestamped_when_it_was_noticed(
    database: Path, request_file: Path
) -> None:
    """Not when the machine went down, which nothing on the way up can know."""
    recorded = record(database, request_file)

    assert recorded is not None
    assert recorded.timestamp == NOW
