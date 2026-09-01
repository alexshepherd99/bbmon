"""Tests for the web app's half of requirement 2's SIGHUP reload.

The collectors answer a reload by rebuilding themselves around a new
configuration. The web app cannot: it is inside ``app.run`` with a socket
bound, so it replaces the settings its routes read and reports the two it
cannot honour — the host and port it is already listening on.

The reload is driven here through the app's own handle rather than by sending
a signal, which is what :func:`bbmon.web.app.main` wires it to.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from bbmon import configstore, db
from bbmon.config import Config
from bbmon.web.app import EXTENSION_KEY, create_app


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    return path


@pytest.fixture
def config_file(tmp_path: Path, database: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        configstore.to_yaml(
            Config(
                database_path=database,
                web_allowed_hosts=("bbmon.example",),
                web_restart_limit=20,
            )
        )
    )
    return path


@pytest.fixture
def app(database: Path, config_file: Path):
    application = create_app(
        Config(
            database_path=database,
            web_allowed_hosts=("bbmon.example",),
            web_restart_limit=20,
        ),
        config_path=config_file,
    )
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app) -> FlaskClient:
    return app.test_client()


def rewrite(config_file: Path, **changes) -> None:
    """Put a different configuration in the file, as an installed save does."""
    config_file.write_text(configstore.to_yaml(Config(**changes)))


def test_a_reload_answers_to_a_name_added_since_the_service_started(
    app, client: FlaskClient, config_file: Path, database: Path
) -> None:
    """The setting that earns itself at G5: the Pi is reached by a new name."""
    assert client.get("/", headers={"Host": "pi.example"}).status_code == 400

    rewrite(config_file, database_path=database, web_allowed_hosts=("pi.example",))
    app.extensions[EXTENSION_KEY].reload()

    assert client.get("/", headers={"Host": "pi.example"}).status_code == 200


def test_a_reload_changes_how_many_restarts_the_dashboard_asks_for(
    app, client: FlaskClient, config_file: Path, database: Path
) -> None:
    rewrite(config_file, database_path=database, web_restart_limit=5)
    app.extensions[EXTENSION_KEY].reload()

    assert client.get("/api/restarts").get_json()["limit"] == 5


def test_a_reload_of_an_unusable_file_keeps_the_service_serving(
    app, client: FlaskClient, config_file: Path
) -> None:
    """A bad file must not cost the dashboard the settings it already had."""
    config_file.write_text("web:\n  port: not-a-port\n")
    app.extensions[EXTENSION_KEY].reload()

    assert client.get("/api/restarts").get_json()["limit"] == 20
    assert client.get("/", headers={"Host": "bbmon.example"}).status_code == 200


def test_a_reload_says_the_bind_address_needs_a_restart(
    app, config_file: Path, database: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No signal can move a listening socket, so the page would lie silently."""
    rewrite(config_file, database_path=database, web_port=9090)

    with caplog.at_level(logging.WARNING):
        app.extensions[EXTENSION_KEY].reload()

    assert "restarted" in caplog.text
    assert "web.port" in caplog.text


def test_a_reload_that_moves_the_database_is_refused(
    app, client: FlaskClient, config_file: Path, tmp_path: Path
) -> None:
    """Refused for the whole service, by the rule every service shares."""
    rewrite(
        config_file, database_path=tmp_path / "elsewhere.db", web_restart_limit=5
    )
    app.extensions[EXTENSION_KEY].reload()

    assert client.get("/api/restarts").get_json()["limit"] == 20
