"""A short-lived cache in front of the dashboard's database queries.

Requirement 10 asks the dashboard to stay fast under repeated polling by not
re-querying on every poll. Several browsers on the LAN can be watching the same
page, and each of them polls on its own timer, so the same query would
otherwise run once per viewer per interval on a Pi 3.

Nothing here needs to be clever. The pinger buffers and flushes once a minute,
so what is on disk changes far more slowly than the page polls — a cache
measured in seconds cannot show anything a fresh query would not also have
returned.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

#: Long enough to collapse several viewers' polls into one query, short enough
#: to be invisible against requirement 4's 60-second flush interval.
DEFAULT_TTL_SECONDS = 10


class TimedCache:
    """Remembers each key's value for a fixed number of seconds.

    Entries are produced on demand and never evicted other than by expiry, so
    the number of keys must stay small and bounded by the application rather
    than by request input — the routes key on their own validated parameters,
    not on arbitrary strings from a query string.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        :param ttl_seconds: How long a produced value stays usable.
        :param clock: Source of monotonic time, injectable for tests. Monotonic
            rather than wall-clock, so a clock correction — which this machine
            expects on every boot, per requirement 6's NTP wait — cannot leave
            an entry cached far into the future.
        """
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_call(
        self,
        key: Any,
        produce: Callable[[], Any],
        ttl_seconds: float | None = None,
    ) -> Any:
        """Return the cached value for ``key``, producing it if it is stale.

        The lock is held across ``produce`` rather than only around the
        dictionary. That serialises the queries themselves, which is the point
        on a single-core-bound Pi: several viewers arriving together share one
        query instead of starting an identical one each.

        A raised exception is not cached. A database read can fail transiently
        while the file is locked, and remembering that would turn one failed
        request into a guaranteed failure for every viewer until the entry
        expired.

        :param ttl_seconds: Overrides the cache's default for this key. Data
            that changes slowly can be held far longer than the live chart's,
            so an expensive query is not re-run once per viewer.
        """
        ttl = self._ttl_seconds if ttl_seconds is None else ttl_seconds

        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and self._clock() - entry[0] < ttl:
                return entry[1]

            value = produce()
            self._entries[key] = (self._clock(), value)
            return value
