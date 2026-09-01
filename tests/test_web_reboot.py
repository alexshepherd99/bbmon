"""Tests for requirement 8's force-reboot button.

The button is the second way into requirement 6's reboot path, and the reason
it is more than one call to :meth:`bbmon.reboot.RebootAction.reboot`: a reboot
asked for here has to leave the same record behind as the scheduled one, or
the startup that follows files it as a power cut.

What is checked here is the asking side — that pressing the button takes that
path, that a press this app did not serve the page for is refused, that a
failure is reported rather than raised, and that an app built without a real
action cannot take a development machine down.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from bbmon import configstore, db, reboot
from bbmon.config import Config
from bbmon.web.app import create_app


class RecordingReboot(reboot.RebootAction):
    """A reboot action that counts being asked, on a machine that stays up.

    Injected where the Pi injects :class:`bbmon.reboot.SystemdPathReboot`, so
    these tests exercise the real route and the real request file and stop at
    the one call that would end the test session.
    """

    def __init__(self, error: str | None = None) -> None:
        """:param error: What to fail with, for the refusing-machine case."""
        self.calls = 0
        self._error = error

    def reboot(self) -> None:
        self.calls += 1
        if self._error is not None:
            raise reboot.RebootError(self._error)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "bbmon.db"
    db.initialise(path)
    return path


@pytest.fixture
def config_file(tmp_path: Path, database: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(configstore.to_yaml(Config(database_path=database)))
    return path


@pytest.fixture
def action() -> RecordingReboot:
    return RecordingReboot()


def client_for(
    database: Path, config_file: Path, action: reboot.RebootAction | None
) -> FlaskClient:
    app = create_app(
        Config(database_path=database),
        config_path=config_file,
        reboot_action=action,
    )
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def client(
    database: Path, config_file: Path, action: RecordingReboot
) -> FlaskClient:
    return client_for(database, config_file, action)


def press(client: FlaskClient, **kwargs):
    """Press the button as the admin page's own form does."""
    page = client.get("/admin").data.decode()
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match, "the admin page carries no token"
    return client.post(
        "/admin/reboot", data={"csrf_token": match.group(1)}, **kwargs
    )


def test_the_admin_page_offers_a_reboot_button(client: FlaskClient) -> None:
    page = client.get("/admin").data.decode()

    assert 'action="/admin/reboot"' in page


def test_pressing_the_button_asks_the_machine_to_reboot(
    client: FlaskClient, action: RecordingReboot
) -> None:
    response = press(client)

    assert response.status_code == 302
    assert action.calls == 1


def test_the_restart_it_leaves_behind_is_recorded_as_expected(
    client: FlaskClient, database: Path
) -> None:
    """Requirement 8's "logs as expected", which is not this route's own doing.

    It writes the reason to the file the scheduled reboot writes, and the next
    startup is what reads it — so the check that matters is the startup after,
    not the response.
    """
    press(client)

    now = datetime.now(timezone.utc)
    with db.connect(database) as conn:
        recorded = reboot.record_startup(
            conn, reboot.request_file_path(database), boot_time=now, now=now
        )

    assert recorded is not None
    assert recorded.expected
    assert "admin page" in recorded.reason


def test_the_page_says_the_reboot_was_asked_for(client: FlaskClient) -> None:
    """Not that it happened: this response is written while the Pi is still up."""
    response = press(client, follow_redirects=True)

    assert response.status_code == 200
    assert b"Reboot requested" in response.data


def test_a_press_without_a_token_is_refused(
    client: FlaskClient, action: RecordingReboot, database: Path
) -> None:
    """The forgery that costs something: any page in a LAN browser posting here."""
    response = client.post("/admin/reboot", data={})

    assert response.status_code == 403
    assert action.calls == 0
    assert not reboot.request_file_path(database).exists()


def test_a_reboot_that_cannot_be_started_is_reported_not_raised(
    database: Path, config_file: Path
) -> None:
    """A refused reboot is the failure this button exists to make visible."""
    client = client_for(
        database, config_file, RecordingReboot(error="the trigger is not writable")
    )

    response = press(client)

    assert response.status_code == 500
    assert b"the trigger is not writable" in response.data


def test_a_refused_reboot_leaves_no_request_behind(
    database: Path, config_file: Path
) -> None:
    """Left in place it would make the next power cut look like this press."""
    client = client_for(
        database, config_file, RecordingReboot(error="the trigger is not writable")
    )

    press(client)

    assert not reboot.request_file_path(database).exists()


def test_an_app_built_without_an_action_reboots_nothing(
    database: Path, config_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Requirement 10's development default, on the one route that would hurt."""
    client = client_for(database, config_file, None)

    with caplog.at_level(logging.WARNING):
        response = press(client)

    assert response.status_code == 302
    assert "no-op reboot action" in caplog.text
