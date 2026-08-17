"""Tests for the short-lived cache in front of the dashboard's queries."""

from bbmon.web.cache import TimedCache


class FakeClock:
    """A monotonic clock the test advances by hand.

    Injected rather than patched: the cache takes its clock as a parameter, so
    these tests exercise the real code path instead of a stubbed-out one.
    """

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Counter:
    """A producer that records how often it actually ran."""

    def __init__(self, value: object = "value") -> None:
        self.calls = 0
        self.value = value

    def __call__(self) -> object:
        self.calls += 1
        return self.value


def test_the_first_call_produces_a_value() -> None:
    cache = TimedCache(ttl_seconds=10, clock=FakeClock())
    produce = Counter("fresh")

    assert cache.get_or_call("k", produce) == "fresh"
    assert produce.calls == 1


def test_a_second_call_within_the_ttl_does_not_query_again() -> None:
    """The whole point: repeated polls must not re-run the query."""
    clock = FakeClock()
    cache = TimedCache(ttl_seconds=10, clock=clock)
    produce = Counter()

    cache.get_or_call("k", produce)
    clock.advance(9)
    cache.get_or_call("k", produce)

    assert produce.calls == 1


def test_the_entry_expires_once_the_ttl_has_passed() -> None:
    clock = FakeClock()
    cache = TimedCache(ttl_seconds=10, clock=clock)
    produce = Counter()

    cache.get_or_call("k", produce)
    clock.advance(11)
    cache.get_or_call("k", produce)

    assert produce.calls == 2


def test_an_expired_entry_returns_the_new_value() -> None:
    """A stale entry must be replaced, not merely re-produced and discarded."""
    clock = FakeClock()
    cache = TimedCache(ttl_seconds=10, clock=clock)

    cache.get_or_call("k", Counter("first"))
    clock.advance(11)

    assert cache.get_or_call("k", Counter("second")) == "second"


def test_keys_are_cached_independently() -> None:
    """The chart windows share one cache, so one must not answer for another."""
    cache = TimedCache(ttl_seconds=10, clock=FakeClock())

    assert cache.get_or_call("a", Counter("first")) == "first"
    assert cache.get_or_call("b", Counter("second")) == "second"


def test_a_cached_key_is_not_reproduced_for_a_different_key() -> None:
    clock = FakeClock()
    cache = TimedCache(ttl_seconds=10, clock=clock)
    produce = Counter()

    cache.get_or_call("a", produce)
    cache.get_or_call("b", Counter())
    clock.advance(1)
    cache.get_or_call("a", produce)

    assert produce.calls == 1


def test_a_longer_ttl_can_be_given_for_one_key() -> None:
    """Slow-moving data should not be re-queried at the default rate.

    The hourly summary is by far the most expensive query the dashboard makes,
    and its buckets change once an hour, so it is given a TTL matching how
    often the page asks for it.
    """
    clock = FakeClock()
    cache = TimedCache(ttl_seconds=10, clock=clock)
    produce = Counter()

    cache.get_or_call("k", produce, ttl_seconds=300)
    clock.advance(299)
    cache.get_or_call("k", produce, ttl_seconds=300)

    assert produce.calls == 1


def test_a_longer_ttl_still_expires() -> None:
    clock = FakeClock()
    cache = TimedCache(ttl_seconds=10, clock=clock)
    produce = Counter()

    cache.get_or_call("k", produce, ttl_seconds=300)
    clock.advance(301)
    cache.get_or_call("k", produce, ttl_seconds=300)

    assert produce.calls == 2


def test_the_default_ttl_applies_when_none_is_given() -> None:
    clock = FakeClock()
    cache = TimedCache(ttl_seconds=10, clock=clock)
    produce = Counter()

    cache.get_or_call("k", produce)
    clock.advance(11)
    cache.get_or_call("k", produce)

    assert produce.calls == 2


def test_a_failing_producer_caches_nothing() -> None:
    """A failed query must not be remembered as an answer.

    The database read raises DatabaseError when the file is briefly locked or
    unreadable. Caching that would turn a transient failure into a fixed one
    for the length of the TTL, on every viewer at once.
    """
    cache = TimedCache(ttl_seconds=10, clock=FakeClock())

    def fail() -> object:
        raise RuntimeError("the database is locked")

    for _ in range(2):
        try:
            cache.get_or_call("k", fail)
        except RuntimeError:
            pass

    produce = Counter("recovered")
    assert cache.get_or_call("k", produce) == "recovered"
    assert produce.calls == 1
