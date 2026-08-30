"""Requirement 8's CSV download of ping and speed test data.

This is the only route that can be asked for more rows than the Pi has memory
for. A full retention window is roughly a million and a half pings, so nothing
here builds the file before sending it: rows arrive from SQLite a page at a
time and leave as chunks of text, and no step holds more than one chunk.

The date range is whole UTC days, which is what a date picker offers and what
"date-range selectable" in requirement 8 means. Internally it is half-open —
see :class:`DateRange` — so the last day is included in full.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import BadRequest

from bbmon import db

#: How much CSV text accumulates before a chunk is handed to the server. A
#: compromise between a syscall per row and holding the file in memory.
CHUNK_BYTES = 64 * 1024

PING_COLUMNS = ("timestamp", "target", "latency_ms", "success")

SPEEDTEST_COLUMNS = (
    "timestamp",
    "download_mbps",
    "upload_mbps",
    "ping_ms",
    "isp",
    "server",
    "success",
)


@dataclass(frozen=True)
class DateRange:
    """A whole number of UTC days, inclusive of both ends as asked for."""

    first_day: date
    last_day: date

    @property
    def start(self) -> datetime:
        """The first instant included."""
        return datetime.combine(self.first_day, time.min, tzinfo=timezone.utc)

    @property
    def end(self) -> datetime:
        """The first instant *not* included: midnight after ``last_day``.

        Exclusive rather than inclusive because someone asking for data "to
        the 11th" means the whole of the 11th. A range ending at the 11th's
        own midnight would quietly return nothing for the last day chosen,
        which is the kind of wrong that looks right.
        """
        return datetime.combine(
            self.last_day + timedelta(days=1), time.min, tzinfo=timezone.utc
        )

    @property
    def label(self) -> str:
        """How the range names itself in the downloaded file's name."""
        return f"{self.first_day.isoformat()}-to-{self.last_day.isoformat()}"


def requested_range(args: MultiDict) -> DateRange:
    """Read the ``start`` and ``end`` query parameters.

    Both are required. Defaulting either one would mean choosing between an
    export of everything — an unbounded read on the machine least able to
    afford one — and a window the caller did not ask for and would have no
    reason to check.

    :raises BadRequest: if either is missing or is not a ``YYYY-MM-DD`` date,
        or if the range runs backwards.
    """
    first_day = _requested_day(args, "start")
    last_day = _requested_day(args, "end")

    if last_day < first_day:
        raise BadRequest(f"end ({last_day}) is before start ({first_day})")

    return DateRange(first_day=first_day, last_day=last_day)


def _requested_day(args: MultiDict, name: str) -> date:
    raw = args.get(name)
    if raw is None:
        raise BadRequest(f"{name} is required, as a YYYY-MM-DD date")

    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise BadRequest(f"{name} must be a YYYY-MM-DD date, got {raw!r}")


def ping_rows(conn: sqlite3.Connection, span: DateRange) -> Iterator[tuple]:
    """Yield the pings in ``span``, shaped for :data:`PING_COLUMNS`."""
    for timestamp, target, latency_ms, success in db.stream_ping_results(
        conn, start=span.start, end=span.end
    ):
        yield timestamp, target, latency_ms, _boolean(success)


def speedtest_rows(conn: sqlite3.Connection, span: DateRange) -> Iterator[tuple]:
    """Yield the speed tests in ``span``, shaped for :data:`SPEEDTEST_COLUMNS`."""
    for row in db.stream_speedtest_results(conn, start=span.start, end=span.end):
        timestamp, download, upload, ping_ms, isp, server, success = row
        yield timestamp, download, upload, ping_ms, isp, server, _boolean(success)


def _boolean(stored: int) -> str:
    """Render SQLite's 1 and 0 the way the JSON API renders the same column.

    Two ways of getting the same data out of bbmon should not disagree about
    what a failed measurement looks like.
    """
    return "true" if stored else "false"


def csv_body(columns: Sequence[str], rows: Iterable[Sequence]) -> Iterator[str]:
    """Render ``rows`` as CSV, as an iterator of chunks of text.

    The first chunk is produced before this returns, which is what makes the
    query run here rather than during the response. A database error can then
    still become an error response; once the first byte is out, the only thing
    a failure can produce is a download that stops early and looks complete.
    """
    chunks = _csv_chunks(columns, rows)
    return _prefixed(next(chunks), chunks)


def _csv_chunks(columns: Sequence[str], rows: Iterable[Sequence]) -> Iterator[str]:
    """Yield the CSV text in chunks of at least :data:`CHUNK_BYTES`.

    The first row is pulled before the header is yielded, so that nothing has
    been emitted by the time the query has either worked or failed.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    remaining = iter(rows)

    first = next(remaining, None)
    writer.writerow(columns)
    if first is not None:
        writer.writerow(first)
    yield _drain(buffer)

    for row in remaining:
        writer.writerow(row)
        if buffer.tell() >= CHUNK_BYTES:
            yield _drain(buffer)

    tail = _drain(buffer)
    if tail:
        yield tail


def _drain(buffer: io.StringIO) -> str:
    """Take everything written so far and leave the buffer empty."""
    text = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return text


def _prefixed(head: str, rest: Iterator[str]) -> Iterator[str]:
    """Put an already-produced chunk back in front of the ones still to come.

    A generator rather than :func:`itertools.chain`, because closing a chain
    does not close what it wraps: an abandoned download would then leave the
    database connection open until the garbage collector noticed.
    """
    yield head
    yield from rest
