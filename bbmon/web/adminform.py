"""Requirement 8's config form: the fields it offers and how they read back.

The form is the untrusted side of the write-back. Everything it submits is a
string, and every string becomes a :class:`~bbmon.config.Config` here — which
is where validation happens, because constructing one validates it. A value
that would leave a service unable to start is refused before anything is
written, rather than after.

**``database.path`` is not on the form**, alone among the settings. Pointing a
running service at a different database from a web page is a good way to
appear to have lost all the data, and moving it also moves the reboot trigger
out from under the unit that watches it. The current value is carried through
untouched instead, so a write-back never changes it — and
:func:`bbmon.configstore.require_installable` still checks it, because "the
form cannot set it" is an argument about this file rather than a guarantee
about the file that reaches root.

``tests/test_web_admin.py`` holds :data:`FIELDS` in step with ``Config``: a
setting added to one and not the other fails there rather than quietly
becoming uneditable.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from bbmon.config import Config, ConfigError

#: The one setting the form deliberately does not offer. See the module
#: docstring: it is carried through from the running configuration instead.
UNEDITABLE_FIELD = "database_path"


@dataclass(frozen=True)
class Field:
    """One editable setting, as the form presents it.

    :param name: The form input's name, spelled as the YAML file spells the
        setting, so an error message names something the reader can find in
        the file itself.
    :param attribute: The :class:`~bbmon.config.Config` field it sets.
    :param kind: ``"int"``, ``"text"`` or ``"list"`` — how the submitted
        string is read back.
    """

    name: str
    attribute: str
    label: str
    kind: str
    note: str


FIELDS: tuple[Field, ...] = (
    Field(
        name="ping.interval_seconds",
        attribute="ping_interval_seconds",
        label="Ping interval",
        kind="int",
        note="Seconds between rounds of pings. Applied on the next cycle.",
    ),
    Field(
        name="ping.targets",
        attribute="ping_targets",
        label="Ping targets",
        kind="list",
        note="One host name or address per line. At least one is required.",
    ),
    Field(
        name="speedtest.interval_hours",
        attribute="speedtest_interval_hours",
        label="Speed test interval",
        kind="int",
        note="Hours between speed tests.",
    ),
    Field(
        name="reboot.interval_days",
        attribute="reboot_interval_days",
        label="Reboot interval",
        kind="int",
        note="Days between scheduled reboots.",
    ),
    Field(
        name="retention.ping_days",
        attribute="retention_ping_days",
        label="Ping retention",
        kind="int",
        note="Days of ping history kept. Older rows are purged daily.",
    ),
    Field(
        name="web.host",
        attribute="web_host",
        label="Web bind address",
        kind="text",
        note=(
            "The address the dashboard listens on: 0.0.0.0 for the whole LAN. "
            "Takes effect when the web service restarts."
        ),
    ),
    Field(
        name="web.port",
        attribute="web_port",
        label="Web port",
        kind="int",
        note="Takes effect when the web service restarts.",
    ),
    Field(
        name="web.allowed_hosts",
        attribute="web_allowed_hosts",
        label="Allowed host names",
        kind="list",
        note=(
            "Extra names this dashboard answers to, one per line. Addresses "
            "and localhost are always answered; a name is only needed if the "
            "Pi is reached by one."
        ),
    ),
    Field(
        name="web.restart_limit",
        attribute="web_restart_limit",
        label="Restarts listed",
        kind="int",
        note="How many restarts the dashboard's list shows.",
    ),
)


def editable_config_fields() -> tuple[str, ...]:
    """The ``Config`` fields the form is expected to cover."""
    return tuple(
        field.name for field in fields(Config) if field.name != UNEDITABLE_FIELD
    )


def values_from_config(config: Config) -> dict[str, str]:
    """Render a configuration as the strings the form's inputs hold."""
    return {field.name: _to_input(getattr(config, field.attribute)) for field in FIELDS}


def config_from_form(submitted: Any, current: Config) -> Config:
    """Build the proposed configuration from what the form submitted.

    :param current: The configuration being edited, which supplies the one
        setting the form does not offer.
    :raises ConfigError: if a field is missing, is not the type it should be,
        or holds a value :class:`~bbmon.config.Config` refuses.
    """
    values: dict[str, Any] = {}
    for field in FIELDS:
        raw = submitted.get(field.name)
        if raw is None:
            raise ConfigError(f"{field.name} is missing from the form")
        values[field.attribute] = _from_input(field, raw)

    values[UNEDITABLE_FIELD] = getattr(current, UNEDITABLE_FIELD)
    return Config(**values)


def _to_input(value: Any) -> str:
    """A ``Config`` value as text for an input box.

    A list becomes one entry per line, which is what the textarea shows and
    what :func:`_from_input` reads back.
    """
    if isinstance(value, tuple):
        return "\n".join(str(entry) for entry in value)
    return str(value)


def _from_input(field: Field, raw: str) -> Any:
    if field.kind == "int":
        return _to_int(field, raw)
    if field.kind == "list":
        return _to_list(raw)
    return raw.strip()


def _to_int(field: Field, raw: str) -> int:
    """Read a whole number, refusing the browser's empty box by name.

    ``Config`` would refuse ``""`` too, but as a type complaint about a string
    — "must be a whole number, got ''" reads as a bug rather than as an empty
    field the reader can go and fill in.
    """
    text = raw.strip()
    if not text:
        raise ConfigError(f"{field.name} must not be empty")
    try:
        return int(text)
    except ValueError:
        raise ConfigError(
            f"{field.name} must be a whole number, got {text!r}"
        ) from None


def _to_list(raw: str) -> tuple[str, ...]:
    """Read a textarea as a list, one entry per line.

    Commas separate too: a list of host names is as likely to be typed on one
    line as on several, and neither reading is surprising. Blank lines are
    dropped rather than becoming empty entries, so trailing newlines from a
    textarea do not have to be trimmed by the person typing.
    """
    entries = (entry.strip() for line in raw.splitlines() for entry in line.split(","))
    return tuple(entry for entry in entries if entry)
