"""Tests for requirement 8's admin page: the config form and its write-back.

The page cannot install a configuration — it stages a proposal that root rules
on, which :mod:`bbmon.configstore` owns and ``tests/test_config_store.py``
covers. What is checked here is everything on the asking side: that the form
shows what the file actually says, that a bad edit is refused before anything
is written, that a save leaves a proposal root would accept, and that a POST
without this app's own token is refused outright.
"""

from __future__ import annotations

import re
from dataclasses import fields as dataclass_fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from bbmon import configstore, db
from bbmon.config import Config, load
from bbmon.web import adminform
from bbmon.web.app import DEFAULT_EXPORT_DAYS, create_app

#: The settings a fresh install has, as the file on disk holds them.
STORED = Config(
    ping_interval_seconds=5,
    ping_targets=("8.8.8.8", "1.1.1.1"),
    speedtest_interval_hours=3,
    reboot_interval_days=3,
    retention_ping_days=30,
    web_host="0.0.0.0",
    web_port=8080,
    web_allowed_hosts=(),
    web_restart_limit=20,
)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    return path


@pytest.fixture
def config_file(tmp_path: Path, database: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(configstore.to_yaml(_stored_with(database_path=database)))
    return path


@pytest.fixture
def client(database: Path, config_file: Path) -> FlaskClient:
    app = create_app(
        Config(database_path=database, web_allowed_hosts=("bbmon.example",)),
        config_path=config_file,
    )
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def staged(database: Path) -> Path:
    return configstore.staged_path(database)


def _stored_with(**changes) -> Config:
    return Config(**{**STORED.__dict__, **changes})


def token_from(client: FlaskClient) -> str:
    """The token this app's own admin page carries."""
    page = client.get("/admin").data.decode()
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match, "the admin form carries no token"
    return match.group(1)


def form_for(client: FlaskClient, **changes) -> dict[str, str]:
    """The stored settings as the form submits them, with edits applied."""
    submitted = adminform.values_from_config(STORED)
    submitted["csrf_token"] = token_from(client)
    submitted.update(changes)
    return submitted


def test_the_admin_page_loads(client: FlaskClient) -> None:
    response = client.get("/admin")

    assert response.status_code == 200
    assert b"Settings" in response.data


def test_the_form_shows_what_the_file_says(
    client: FlaskClient, config_file: Path, database: Path
) -> None:
    """Not what this process started with: those disagree after every save."""
    config_file.write_text(
        configstore.to_yaml(
            _stored_with(database_path=database, ping_interval_seconds=17)
        )
    )

    page = client.get("/admin").data.decode()

    assert 'name="ping.interval_seconds"' in page
    assert 'value="17"' in page


def test_a_list_setting_is_shown_one_entry_per_line(client: FlaskClient) -> None:
    page = client.get("/admin").data.decode()

    assert "8.8.8.8\n1.1.1.1" in page


def test_every_editable_setting_is_on_the_form() -> None:
    """A setting added to ``Config`` and not here would silently become
    uneditable, which requirement 8's "edit all config.yaml values" forbids."""
    covered = {field.attribute for field in adminform.FIELDS}

    assert covered == set(adminform.editable_config_fields())


def test_the_database_path_is_not_on_the_form() -> None:
    """Repointing a running service at another database from a web page looks
    exactly like losing all the data."""
    assert "database.path" not in {field.name for field in adminform.FIELDS}


def test_saving_stages_a_proposal(client: FlaskClient, staged: Path) -> None:
    response = client.post("/admin", data=form_for(client, **{"web.port": "9090"}))

    assert response.status_code == 302
    assert load(staged).web_port == 9090


def test_a_saved_proposal_keeps_the_settings_that_were_not_edited(
    client: FlaskClient, staged: Path
) -> None:
    client.post("/admin", data=form_for(client, **{"web.port": "9090"}))

    proposed = load(staged)
    assert proposed.ping_targets == ("8.8.8.8", "1.1.1.1")
    assert proposed.retention_ping_days == 30


def test_a_saved_proposal_keeps_the_database_the_services_are_using(
    client: FlaskClient, staged: Path, database: Path
) -> None:
    """Even when the form is made to submit one, which a browser will not do
    and anything else can."""
    submitted = form_for(client)
    submitted["database.path"] = "/tmp/elsewhere.db"

    client.post("/admin", data=submitted)

    assert load(staged).database_path == database


def test_a_list_setting_survives_the_round_trip(
    client: FlaskClient, staged: Path
) -> None:
    client.post(
        "/admin",
        data=form_for(client, **{"ping.targets": "8.8.8.8\n  \n1.1.1.1, example.com"}),
    )

    assert load(staged).ping_targets == ("8.8.8.8", "1.1.1.1", "example.com")


def test_the_page_says_a_save_was_proposed_rather_than_installed(
    client: FlaskClient,
) -> None:
    """Root installs it, asynchronously; this response is written before that
    has happened, so the page must not claim it succeeded."""
    response = client.post("/admin", data=form_for(client), follow_redirects=True)

    assert b"proposed" in response.data.lower()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ping.interval_seconds", "0"),
        ("ping.interval_seconds", "five"),
        ("ping.interval_seconds", ""),
        ("ping.targets", ""),
        ("ping.targets", "not a hostname"),
        ("web.host", "example.com"),
        ("web.port", "70000"),
        ("web.allowed_hosts", "not a hostname"),
        ("retention.ping_days", "-1"),
    ],
)
def test_a_bad_value_is_refused_and_nothing_is_staged(
    client: FlaskClient, staged: Path, field: str, value: str
) -> None:
    response = client.post("/admin", data=form_for(client, **{field: value}))

    assert response.status_code == 400
    assert field.encode() in response.data
    assert not staged.exists()


@pytest.mark.parametrize(
    ("value", "explanation"),
    [("", "must not be empty"), ("five", "must be a whole number")],
)
def test_a_box_that_should_hold_a_number_says_what_is_wrong_with_it(
    client: FlaskClient, value: str, explanation: str
) -> None:
    """The reason the form reads these itself rather than handing the string
    to ``Config``: an empty box is a field to go and fill in, and saying "must
    be a whole number, got ''" reads as a bug instead."""
    response = client.post(
        "/admin", data=form_for(client, **{"ping.interval_seconds": value})
    )

    assert explanation in response.data.decode()


def test_a_refused_save_gives_the_form_back_with_the_edit_still_in_it(
    client: FlaskClient,
) -> None:
    response = client.post(
        "/admin", data=form_for(client, **{"web.port": "70000", "ping.targets": "1.1.1.1"})
    )

    page = response.data.decode()
    assert 'value="70000"' in page
    assert ">1.1.1.1<" in page


def test_a_missing_field_is_refused(client: FlaskClient, staged: Path) -> None:
    """A form posted by something other than the page, one field short."""
    submitted = form_for(client)
    del submitted["speedtest.interval_hours"]

    response = client.post("/admin", data=submitted)

    assert response.status_code == 400
    assert not staged.exists()


def test_a_post_without_a_token_is_refused(
    client: FlaskClient, staged: Path
) -> None:
    """The forgery this stops: any page in a LAN browser posting here."""
    submitted = adminform.values_from_config(STORED)

    response = client.post("/admin", data=submitted)

    assert response.status_code == 403
    assert not staged.exists()


def test_a_post_with_the_wrong_token_is_refused(
    client: FlaskClient, staged: Path
) -> None:
    response = client.post("/admin", data=form_for(client, csrf_token="not-the-token"))

    assert response.status_code == 403
    assert not staged.exists()


def test_a_token_from_another_instance_is_refused(
    database: Path, config_file: Path, client: FlaskClient, staged: Path
) -> None:
    """Tokens are per process, so a form left open across a restart is refused
    rather than accepted by a service that has forgotten issuing it."""
    other = create_app(Config(database_path=database), config_path=config_file)
    other.config["TESTING"] = True

    response = client.post(
        "/admin", data=form_for(client, csrf_token=token_from(other.test_client()))
    )

    assert response.status_code == 403
    assert not staged.exists()


def test_an_unknown_host_is_refused_before_the_token_is_looked_at(
    client: FlaskClient, staged: Path
) -> None:
    """Rebinding is what would let an attacker's page read a valid token."""
    response = client.post(
        "/admin", data=form_for(client), headers={"Host": "attacker.example.com"}
    )

    assert response.status_code == 400
    assert not staged.exists()


def test_an_unreadable_config_file_still_gives_a_usable_form(
    client: FlaskClient, config_file: Path
) -> None:
    """The moment being able to write a good file back matters most."""
    config_file.write_text("ping: [this is not a mapping]\n")

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'name="ping.interval_seconds"' in response.data.decode()
    assert b"started with" in response.data


def test_a_proposal_that_cannot_be_written_is_reported_not_raised(
    client: FlaskClient, staged: Path
) -> None:
    """The state directory is not writable by the web service on a Pi where
    something has gone wrong with permissions; that is a message, not a 500."""
    staged.parent.chmod(0o500)
    try:
        response = client.post("/admin", data=form_for(client))
    finally:
        staged.parent.chmod(0o700)

    assert response.status_code == 400
    assert not staged.exists()


def test_the_export_form_offers_a_date_range_for_both_files(
    client: FlaskClient,
) -> None:
    page = client.get("/admin").data.decode()

    assert 'type="date" name="start"' in page
    assert 'type="date" name="end"' in page
    assert 'formaction="/export/ping.csv"' in page
    assert 'formaction="/export/speedtest.csv"' in page


def test_the_export_dates_start_on_a_window_ending_today(client: FlaskClient) -> None:
    today = datetime.now(timezone.utc).date()
    page = client.get("/admin").data.decode()

    assert f'value="{today.isoformat()}"' in page
    assert (
        f'value="{(today - timedelta(days=DEFAULT_EXPORT_DAYS - 1)).isoformat()}"'
        in page
    )


def test_the_dashboard_links_to_the_admin_page(client: FlaskClient) -> None:
    """Requirement 8 wants a separate page; nothing else would find it."""
    assert b'href="/admin"' in client.get("/").data


def test_the_admin_page_links_back_to_the_dashboard(client: FlaskClient) -> None:
    assert b'href="/"' in client.get("/admin").data


def test_config_fields_are_all_accounted_for() -> None:
    """Guards the drift guard: ``editable_config_fields`` is derived from
    ``Config`` itself, so this fails if the exclusion stops matching a field."""
    names = {field.name for field in dataclass_fields(Config)}

    assert adminform.UNEDITABLE_FIELD in names
    assert set(adminform.editable_config_fields()) == names - {
        adminform.UNEDITABLE_FIELD
    }
