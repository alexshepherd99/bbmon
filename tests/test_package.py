"""Smoke test that the package is installed and importable.

This checks the development install, not behaviour — the behavioural tests
live alongside the modules they cover.
"""

import bbmon


def test_version_is_exposed() -> None:
    assert isinstance(bbmon.__version__, str)
    assert bbmon.__version__
