"""Flask application serving the dashboard and its data.

M1 is deliberately thin: one chart, reading raw rows. The pre-aggregation and
short-lived cache that requirement 10 asks for arrive at M5, once there is more
than one chart to share them.

The app has no authentication by requirement — it is LAN-only — so the debugger
is hard off and the bind address is explicit rather than implicit.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import BadRequest

from bbmon import __version__, db
from bbmon.config import Config, ConfigError, load

logger = logging.getLogger(__name__)

#: The short-term chart's window, per requirement 7.
DEFAULT_WINDOW_MINUTES = 120

#: Ceiling on the requested window, so one request cannot scan the whole table
#: on a machine this small.
MAX_WINDOW_MINUTES = 24 * 60


def create_app(config: Config) -> Flask:
    """Build the application for a given configuration."""
    app = Flask(__name__)
    # Never on, in any environment: the Werkzeug debugger is remote code
    # execution to anyone who can reach the port, and this port is open to the
    # whole LAN with no authentication in front of it.
    app.config["DEBUG"] = False
    app.debug = False

    @app.get("/")
    def dashboard() -> str:
        return render_template("dashboard.html", version=__version__)

    @app.get("/api/ping")
    def ping_data():
        minutes = _requested_window_minutes()
        since = datetime.now(timezone.utc) - timedelta(minutes=minutes)

        with db.connect(config.database_path) as conn:
            results = db.recent_ping_results(conn, since=since)

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

    return app


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
