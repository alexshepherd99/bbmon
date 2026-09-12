"""Checks that bbmon's logs stay in the journal, which bounds them.

Requirement 10 asks for log rotation so that the services' own log files cannot
grow unbounded. bbmon meets it by having no log files: every service logs to
stderr, systemd hands that to journald, and journald caps its own size — on the
Pi the journal is volatile, held in ``/run`` and limited to a tenth of it. So
the thing to hold in place is the absence of a file, since the first one added
would be the one nothing rotates.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
UNIT_DIR = REPO_ROOT / "deploy" / "systemd"
PACKAGE_DIR = REPO_ROOT / "bbmon"

#: Ways Python code could send log records to a file of its own.
FILE_LOGGING = re.compile(r"FileHandler|basicConfig\([^)]*filename\s*=", re.DOTALL)


def _service_units() -> list[Path]:
    return sorted(UNIT_DIR.glob("*.service"))


@pytest.mark.parametrize("unit_path", _service_units(), ids=lambda p: p.name)
def test_no_unit_redirects_its_output_to_a_file(unit_path: Path) -> None:
    """A unit's output goes to the journal, not to a file systemd appends to."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(unit_path)
    service = parser["Service"]

    assert "LogsDirectory" not in service
    for directive in ("StandardOutput", "StandardError"):
        assert service.get(directive, "journal") in ("journal", "inherit")


@pytest.mark.parametrize(
    "source_path",
    sorted(PACKAGE_DIR.rglob("*.py")),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_no_module_logs_to_a_file(source_path: Path) -> None:
    """No module attaches a file handler; logging reaches stderr only."""
    assert not FILE_LOGGING.search(source_path.read_text())
