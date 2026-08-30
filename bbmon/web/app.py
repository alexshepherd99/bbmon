"""Flask application serving the dashboard and its data.

Every read goes through a short-lived cache (requirement 10): several browsers
on the LAN poll the same endpoints on their own timers, and without it each
viewer would cost the Pi its own copy of every query.

The app has no authentication by requirement — it is LAN-only — so the debugger
is hard off and the bind address is explicit rather than implicit.
"""

from __future__ import annotations

import ipaddress
import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import BadRequest

from bbmon import __version__, db
from bbmon.config import Config, ConfigError, load
from bbmon.web import export
from bbmon.web.cache import TimedCache

logger = logging.getLogger(__name__)

#: The short-term chart's window, per requirement 7.
DEFAULT_WINDOW_MINUTES = 120

#: Ceiling on the requested window, so one request cannot scan the whole table
#: on a machine this small.
MAX_WINDOW_MINUTES = 24 * 60

#: The long-term chart's window. Requirement 7 fixes it at one day boxed
#: hourly, so it takes no parameter — there is nothing for a caller to choose.
BOX_PLOT_HOURS = 24

#: Requirement 7's "selectable time range (e.g. last 24h / 7d / 30d)".
DEFAULT_HISTORY_DAYS = 7
MAX_HISTORY_DAYS = 30

#: How long the slow-moving panels' results are held, matching how often the
#: page asks for them.
#:
#: The hourly summary is much the most expensive query the dashboard makes —
#: it scans a day of pings, tens of thousands of rows, and measured around
#: 400ms against 56k rows on the x86 development container, so the Pi 3 will
#: be slower still by an unmeasured factor. At the default TTL every viewer's
#: poll would miss and pay for its own run of it. Its buckets change once an
#: hour, a speed test runs every few hours and a restart is rarer than that,
#: so holding these for the length of a poll interval costs no visible
#: freshness and makes the cost independent of how many people are watching.
SLOW_CACHE_TTL_SECONDS = 300

#: Written by ``deploy.sh`` and ``update.sh`` beside the database, and shown in
#: the footer. Requirement 7 wants the footer to confirm the update script
#: deployed the latest code, which the package version cannot do — it does not
#: change between deploys.
#:
#: The scripts name this path literally while the app derives it from
#: ``database.path``, the same join M4 had to guard for the reboot trigger.
#: Guarded far more weakly here on purpose: moving the database makes the
#: footer say "unknown", where moving the reboot trigger made the Pi silently
#: stop rebooting.
BUILD_STAMP_NAME = "build-stamp"

#: Shown when no stamp is there to read — a checkout that was never deployed,
#: or a Pi bootstrapped before the scripts wrote one.
UNKNOWN_BUILD = "build unknown"

#: Names answered to whatever ``web.allowed_hosts`` says. ``localhost`` cannot
#: be rebound — it is the browser's own name for the machine the browser is on
#: — so nothing is given away by allowing it, and a Pi-local curl works.
ALWAYS_ALLOWED_HOSTS = frozenset({"localhost"})

#: The stamp is one short line. Anything longer is a corrupt or wrong file, and
#: a footer is not the place to discover that by wrapping across the page.
MAX_STAMP_LENGTH = 120


def create_app(config: Config, cache: TimedCache | None = None) -> Flask:
    """Build the application for a given configuration.

    :param cache: The query cache. Injectable so tests can control expiry
        rather than wait for it; the default is the ordinary timed one.
    """
    cache = cache if cache is not None else TimedCache()
    app = Flask(__name__)
    # Never on, in any environment: the Werkzeug debugger is remote code
    # execution to anyone who can reach the port, and this port is open to the
    # whole LAN with no authentication in front of it.
    app.config["DEBUG"] = False
    app.debug = False

    @app.before_request
    def reject_unexpected_hosts() -> None:
        """Refuse a request that asked for a name this dashboard does not own.

        Registered on the app rather than per route, so it covers the static
        files and every route added later — including the admin page's POSTs,
        where a rebound origin would be able to reboot the Pi.
        """
        if not host_is_allowed(request.host, config.web_allowed_hosts):
            logger.warning(
                "Refused a request for host %r; add it to web.allowed_hosts if "
                "it is a name this dashboard should answer to",
                request.host,
            )
            # The host is not echoed back: whoever sent it already knows it,
            # and it is the one part of the request under their control.
            raise BadRequest("This dashboard does not answer to that host name.")

    @app.get("/")
    def dashboard() -> str:
        return render_template(
            "dashboard.html",
            version=__version__,
            build=_read_build_stamp(config.database_path.parent / BUILD_STAMP_NAME),
        )

    def read(key, query, ttl_seconds=None):
        """Run a database read through the cache.

        ``key`` must be built from validated parameters only, never from raw
        query-string text: the cache has no eviction beyond expiry, so an
        attacker-chosen key would grow it without bound.
        """

        def produce():
            with db.connect(config.database_path) as conn:
                return query(conn)

        return cache.get_or_call(key, produce, ttl_seconds=ttl_seconds)

    @app.get("/api/ping")
    def ping_data():
        minutes = _requested_window_minutes()
        since = datetime.now(timezone.utc) - timedelta(minutes=minutes)

        results = read(
            ("ping", minutes), lambda conn: db.recent_ping_results(conn, since=since)
        )

        series: dict[str, list[list[float | None]]] = defaultdict(list)
        for result in results:
            series[result.target].append(
                [result.timestamp.timestamp() * 1000, result.latency_ms]
            )

        return jsonify(
            window_minutes=minutes,
            generated_at=datetime.now(timezone.utc).isoformat(),
            targets=series,
        )

    @app.get("/api/ping/hourly")
    def hourly_ping_data():
        # From the top of an hour, not from this instant. Counting back 24
        # hours from part-way through one clips a sliver off the oldest hour
        # and adds the current partial hour, which drew 25 columns for a
        # window labelled 24 — the first of them a box built from a few
        # minutes' pings, sitting beside boxes built from full hours.
        this_hour = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        since = this_hour - timedelta(hours=BOX_PLOT_HOURS - 1)
        buckets = read(
            "ping-hourly",
            lambda conn: db.hourly_ping_summary(conn, since=since),
            ttl_seconds=SLOW_CACHE_TTL_SECONDS,
        )

        return jsonify(
            hours=BOX_PLOT_HOURS,
            generated_at=datetime.now(timezone.utc).isoformat(),
            buckets=[
                {
                    "hour": bucket.hour.isoformat(),
                    "target": bucket.target,
                    "count": bucket.count,
                    "low": bucket.low,
                    "q1": bucket.q1,
                    "median": bucket.median,
                    "q3": bucket.q3,
                    "high": bucket.high,
                }
                for bucket in buckets
            ],
        )

    @app.get("/api/speedtest/history")
    def speedtest_history():
        days = _requested_history_days()
        since = datetime.now(timezone.utc) - timedelta(days=days)
        results = read(
            ("speedtest-history", days),
            lambda conn: db.speedtest_history(conn, since=since),
            ttl_seconds=SLOW_CACHE_TTL_SECONDS,
        )

        return jsonify(
            days=days,
            generated_at=datetime.now(timezone.utc).isoformat(),
            results=[
                {
                    "timestamp": result.timestamp.isoformat(),
                    "download_mbps": result.download_mbps,
                    "upload_mbps": result.upload_mbps,
                    "ping_ms": result.ping_ms,
                    "success": result.success,
                }
                for result in results
            ],
        )

    @app.get("/api/restarts")
    def restarts():
        include_expected = _requested_include_expected()
        limit = config.web_restart_limit
        recorded = read(
            ("restarts", limit, include_expected),
            lambda conn: db.recent_restarts(
                conn, limit=limit, include_expected=include_expected
            ),
            ttl_seconds=SLOW_CACHE_TTL_SECONDS,
        )

        return jsonify(
            limit=limit,
            include_expected=include_expected,
            generated_at=datetime.now(timezone.utc).isoformat(),
            restarts=[
                {
                    "timestamp": restart.timestamp.isoformat(),
                    "expected": restart.expected,
                    "reason": restart.reason,
                }
                for restart in recorded
            ],
        )

    @app.get("/api/speedtest/latest")
    def latest_speedtest():
        result = read("speedtest-latest", db.latest_speedtest_result)

        if result is None:
            # Distinct from a failed run, which returns a result whose success
            # is false. "Nothing has run yet" and "the last run failed" are
            # different things and the panel says so differently.
            return jsonify(result=None)

        return jsonify(
            result="latest",
            timestamp=result.timestamp.isoformat(),
            download_mbps=result.download_mbps,
            upload_mbps=result.upload_mbps,
            ping_ms=result.ping_ms,
            isp=result.isp,
            server=result.server,
            success=result.success,
        )

    def csv_download(name, columns, rows_in) -> Response:
        """Stream requirement 8's CSV export of one table over a date range.

        Deliberately outside ``read`` above: an export is large, is downloaded
        rather than polled, and would key the cache on a caller-chosen date
        range — the unbounded growth :class:`TimedCache` exists to avoid.

        :param rows_in: Yields the table's rows for a :class:`export.DateRange`.
        """
        span = export.requested_range(request.args)

        def rows():
            # The connection is opened inside the generator so that it lives
            # exactly as long as the response body does, and is closed even
            # when a browser abandons the download part way through.
            with db.connect(config.database_path) as conn:
                yield from rows_in(conn, span)

        return Response(
            export.csv_body(columns, rows()),
            mimetype="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="bbmon-{name}-{span.label}.csv"'
                )
            },
        )

    @app.get("/export/ping.csv")
    def export_ping_results() -> Response:
        return csv_download("ping", export.PING_COLUMNS, export.ping_rows)

    @app.get("/export/speedtest.csv")
    def export_speedtest_results() -> Response:
        return csv_download(
            "speedtest", export.SPEEDTEST_COLUMNS, export.speedtest_rows
        )

    return app


def host_is_allowed(host: str, allowed: Iterable[str]) -> bool:
    """Whether a request's ``Host`` names something this dashboard answers to.

    This is the defence against DNS rebinding, which is the one attack that
    survives having no authentication and no session to steal. A page on any
    website can point a name it owns at the Pi's address; once the browser has
    cached that name as resolving there, the page is same-origin with the
    dashboard and can read it and post to it. Refusing the name breaks that,
    because the browser sends the name it was told to fetch.

    **Any address is answered**, and that is deliberate rather than lax: an
    address is not something DNS can move, so a page served from elsewhere
    cannot become same-origin with one. It is also how the dashboard is
    reached in practice, from a phone on the LAN, and the address of a home Pi
    is not knowable here.

    :param allowed: Extra names from ``web.allowed_hosts``, matched whole and
        without regard to case. A suffix match is not offered: it would let
        ``bbmon.lan.example.com``, a name anyone can register, through a rule
        meant to name one host.
    """
    name = _host_without_port(host)
    if not name:
        return False
    if name in ALWAYS_ALLOWED_HOSTS or _is_address(name):
        return True
    return name in {_normalise_host(entry) for entry in allowed}


def _host_without_port(host: str) -> str:
    """Return the name or address part of a ``Host`` header, normalised.

    An empty string is returned for anything unparseable, which the caller
    refuses — including a bare IPv6 address, which a ``Host`` header is
    required to bracket.
    """
    host = host.strip().lower()
    if host.startswith("["):
        closed = host.find("]")
        return host[1:closed] if closed != -1 else ""
    return _normalise_host(host.split(":", 1)[0])


def _normalise_host(host: str) -> str:
    """Lower-case a host name and drop the root label a fully-qualified one
    may carry, so ``bbmon.lan.`` and ``bbmon.lan`` are the same name."""
    return host.strip().lower().rstrip(".")


def _is_address(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def _requested_window_minutes() -> int:
    """Read and bound the ``minutes`` query parameter."""
    raw = request.args.get("minutes")
    if raw is None:
        return DEFAULT_WINDOW_MINUTES

    try:
        minutes = int(raw)
    except ValueError:
        raise BadRequest(f"minutes must be a whole number, got {raw!r}")

    if not 1 <= minutes <= MAX_WINDOW_MINUTES:
        raise BadRequest(f"minutes must be between 1 and {MAX_WINDOW_MINUTES}")

    return minutes


def _read_build_stamp(path: Path) -> str:
    """Return what the deploy scripts recorded about this deployment.

    Read on every page request rather than once at startup: ``deploy.sh``
    restarts only the services whose files changed, so a deploy that misses the
    web app would otherwise leave the footer reporting the previous build —
    the exact question this indicator exists to answer.

    A missing or unreadable stamp is not an error. A developer's checkout has
    never been deployed, and neither has a Pi bootstrapped before the scripts
    started writing one; both should say so rather than fail to serve a page.
    """
    try:
        text = path.read_text()
    except OSError:
        return UNKNOWN_BUILD

    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    return line[:MAX_STAMP_LENGTH] if line else UNKNOWN_BUILD


def _requested_history_days() -> int:
    """Read and bound the speed test history's ``days`` query parameter."""
    raw = request.args.get("days")
    if raw is None:
        return DEFAULT_HISTORY_DAYS

    try:
        days = int(raw)
    except ValueError:
        raise BadRequest(f"days must be a whole number, got {raw!r}")

    if not 1 <= days <= MAX_HISTORY_DAYS:
        raise BadRequest(f"days must be between 1 and {MAX_HISTORY_DAYS}")

    return days


def _requested_include_expected() -> bool:
    """Read requirement 7's restart-list toggle.

    Only the two spellings are accepted. Treating anything else as false would
    silently hide the scheduled reboots on a typo, and the list would look
    empty rather than wrong.
    """
    raw = request.args.get("include_expected")
    if raw is None:
        return True
    if raw in ("true", "false"):
        return raw == "true"

    raise BadRequest(f"include_expected must be true or false, got {raw!r}")


def main() -> int:
    """Entrypoint for ``python -m bbmon.web``."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    try:
        config = load()
        db.initialise(config.database_path)
    except (ConfigError, db.DatabaseError):
        logger.exception("The web app could not start")
        return 1

    app = create_app(config)
    logger.info(
        "Serving the dashboard on http://%s:%d", config.web_host, config.web_port
    )
    # host and port come from configuration rather than Flask's implicit
    # defaults, so what the service binds to is always a stated decision.
    app.run(host=config.web_host, port=config.web_port, debug=False)
    return 0
