"""Tests for the footer's build indicator.

Requirement 7 asks for a version/build indicator that confirms the update
script deployed the latest code. The package version alone cannot do that: it
does not change between deploys.
"""

from pathlib import Path

import pytest
from flask.testing import FlaskClient

from bbmon import db
from bbmon.config import Config
from bbmon.web.app import BUILD_STAMP_NAME, MAX_STAMP_LENGTH, UNKNOWN_BUILD, create_app


@pytest.fixture
def state(tmp_path: Path) -> Path:
    """The directory holding the database, which is where the stamp lands."""
    db.initialise(tmp_path / "bbmon.db")
    return tmp_path


@pytest.fixture
def client(state: Path) -> FlaskClient:
    app = create_app(Config(database_path=state / "bbmon.db"))
    app.config["TESTING"] = True
    return app.test_client()


def write_stamp(state: Path, text: str) -> None:
    (state / BUILD_STAMP_NAME).write_text(text)


def test_the_footer_shows_the_stamp_the_deploy_script_wrote(
    state: Path, client: FlaskClient
) -> None:
    write_stamp(state, "deployed 2026-08-17T14:02:11+00:00 from 1560e1d by deploy.sh\n")

    page = client.get("/").data.decode()

    assert "deployed 2026-08-17T14:02:11+00:00 from 1560e1d by deploy.sh" in page


def test_a_missing_stamp_reads_as_unknown(client: FlaskClient) -> None:
    """A Pi bootstrapped before this existed, or a developer's own checkout."""
    page = client.get("/").data.decode()

    assert UNKNOWN_BUILD in page


def test_an_empty_stamp_reads_as_unknown(state: Path, client: FlaskClient) -> None:
    """A truncated write must not render as a blank footer with no explanation."""
    write_stamp(state, "   \n")

    assert UNKNOWN_BUILD in client.get("/").data.decode()


def test_an_overlong_stamp_is_truncated(state: Path, client: FlaskClient) -> None:
    write_stamp(state, "x" * (MAX_STAMP_LENGTH * 3))

    page = client.get("/").data.decode()

    assert "x" * MAX_STAMP_LENGTH in page
    assert "x" * (MAX_STAMP_LENGTH + 1) not in page


def test_only_the_first_line_is_shown(state: Path, client: FlaskClient) -> None:
    write_stamp(state, "deployed just now\nand a second line\n")

    page = client.get("/").data.decode()

    assert "deployed just now" in page
    assert "and a second line" not in page


def test_the_stamp_is_read_on_every_request(state: Path, client: FlaskClient) -> None:
    """Reading it once at startup would let the footer go stale.

    deploy.sh restarts only the services whose files changed, so a deploy that
    does not touch the web app would leave it reporting the previous build —
    which is exactly the question the indicator exists to answer.
    """
    write_stamp(state, "first deploy")
    assert "first deploy" in client.get("/").data.decode()

    write_stamp(state, "second deploy")
    assert "second deploy" in client.get("/").data.decode()


def test_a_stamp_is_escaped_rather_than_rendered_as_markup(
    state: Path, client: FlaskClient
) -> None:
    """The file is root-written, but it still reaches a page unescaped-or-not."""
    write_stamp(state, "<script>alert(1)</script>")

    page = client.get("/").data.decode()

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
