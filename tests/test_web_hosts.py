"""Tests for the Host-header allowlist.

The allowlist is what stops DNS rebinding: a page on any website can point a
name it controls at the Pi's address and, once the browser has cached that
name as same-origin, read the dashboard and post to the admin page. The
defence is to check the name the browser asked for, not the address it
reached.
"""

from pathlib import Path

import pytest
from flask.testing import FlaskClient

from bbmon import db
from bbmon.config import Config, ConfigError
from bbmon.web.app import create_app


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    return path


def client_for(database: Path, *allowed: str) -> FlaskClient:
    app = create_app(Config(database_path=database, web_allowed_hosts=allowed))
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def client(database: Path) -> FlaskClient:
    return client_for(database)


def test_an_unknown_host_name_is_refused(client: FlaskClient) -> None:
    """The rebinding case: the browser asks for a name bbmon does not answer to."""
    response = client.get("/", headers={"Host": "bbmon.example.com"})

    assert response.status_code == 400


def test_an_unknown_host_name_is_refused_on_the_api_too(client: FlaskClient) -> None:
    """Rebinding reads the JSON, not the page — the check cannot be per-route."""
    response = client.get("/api/ping", headers={"Host": "bbmon.example.com"})

    assert response.status_code == 400


def test_an_unknown_host_name_is_refused_for_static_files(client: FlaskClient) -> None:
    response = client.get(
        "/static/vendor/echarts.min.js", headers={"Host": "bbmon.example.com"}
    )

    assert response.status_code == 400


def test_an_address_is_served(client: FlaskClient) -> None:
    """How the dashboard is actually reached, and not a name DNS can move."""
    response = client.get("/", headers={"Host": "192.0.2.10:8080"})

    assert response.status_code == 200


def test_an_ipv6_address_is_served(client: FlaskClient) -> None:
    response = client.get("/", headers={"Host": "[2001:db8::1]:8080"})

    assert response.status_code == 200


def test_localhost_is_served(client: FlaskClient) -> None:
    response = client.get("/", headers={"Host": "localhost:8080"})

    assert response.status_code == 200


def test_a_configured_name_is_served(database: Path) -> None:
    response = client_for(database, "bbmon.lan").get("/", headers={"Host": "bbmon.lan"})

    assert response.status_code == 200


def test_a_configured_name_is_matched_without_regard_to_case(database: Path) -> None:
    """Host names are case-insensitive, and browsers do not normalise them."""
    response = client_for(database, "bbmon.lan").get("/", headers={"Host": "BBmon.LAN"})

    assert response.status_code == 200


def test_a_configured_name_is_matched_whatever_port_it_carries(database: Path) -> None:
    """The setting names a host; the port is bbmon's own and not a second name."""
    response = client_for(database, "bbmon.lan").get(
        "/", headers={"Host": "bbmon.lan:8080"}
    )

    assert response.status_code == 200


def test_a_configured_name_may_itself_be_written_in_any_case(database: Path) -> None:
    """The setting is hand-edited, so the file's spelling cannot be relied on."""
    response = client_for(database, "BBmon.LAN").get("/", headers={"Host": "bbmon.lan"})

    assert response.status_code == 200


def test_a_fully_qualified_name_matches_the_same_name(database: Path) -> None:
    """A trailing dot is the root label, not a different host."""
    response = client_for(database, "bbmon.lan").get(
        "/", headers={"Host": "bbmon.lan."}
    )

    assert response.status_code == 200


def test_a_configured_name_may_itself_be_fully_qualified(database: Path) -> None:
    response = client_for(database, "bbmon.lan.").get(
        "/", headers={"Host": "bbmon.lan"}
    )

    assert response.status_code == 200


def test_a_name_that_only_prefixes_a_configured_one_is_refused(database: Path) -> None:
    """`bbmon.lan.example.com` is a name an attacker can register."""
    response = client_for(database, "bbmon.lan").get(
        "/", headers={"Host": "bbmon.lan.example.com"}
    )

    assert response.status_code == 400


def test_an_empty_host_is_refused(client: FlaskClient) -> None:
    response = client.get("/", headers={"Host": ""})

    assert response.status_code == 400


def test_allowed_hosts_must_be_a_list() -> None:
    with pytest.raises(ConfigError, match="web.allowed_hosts"):
        Config(web_allowed_hosts="bbmon.lan")  # type: ignore[arg-type]


def test_an_allowed_host_must_be_a_hostname_or_address() -> None:
    with pytest.raises(ConfigError, match="not a valid hostname"):
        Config(web_allowed_hosts=("bbmon lan",))
