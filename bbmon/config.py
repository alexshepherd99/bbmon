"""Loading and validation of the bbmon YAML configuration file.

Every service reads its settings through :func:`load`. Validation lives in
:class:`Config` itself, so an invalid configuration cannot be constructed at
all — a bad edit from the admin page is rejected before it can crash a service
on reload.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("/etc/bbmon/config.yaml")
CONFIG_PATH_ENV_VAR = "BBMON_CONFIG"

MAX_HOSTNAME_LENGTH = 253
_HOSTNAME_LABEL = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

#: Maps a ``(section, key)`` pair in the YAML file to a :class:`Config` field.
_FIELD_MAP: dict[tuple[str, str], str] = {
    ("ping", "interval_seconds"): "ping_interval_seconds",
    ("ping", "targets"): "ping_targets",
    ("speedtest", "interval_hours"): "speedtest_interval_hours",
    ("reboot", "interval_days"): "reboot_interval_days",
    ("retention", "ping_days"): "retention_ping_days",
    ("web", "host"): "web_host",
    ("web", "port"): "web_port",
    ("web", "allowed_hosts"): "web_allowed_hosts",
    ("web", "restart_limit"): "web_restart_limit",
    ("database", "path"): "database_path",
}


class ConfigError(Exception):
    """Raised when configuration is missing, unreadable, or invalid."""


@dataclass(frozen=True)
class Config:
    """Validated bbmon settings, with the documented defaults.

    Constructing a ``Config`` validates it; :class:`ConfigError` is raised for
    any value that would leave a service misbehaving.
    """

    ping_interval_seconds: int = 5
    ping_targets: tuple[str, ...] = ("8.8.8.8", "1.1.1.1", "google.com")
    speedtest_interval_hours: int = 3
    reboot_interval_days: int = 3
    retention_ping_days: int = 30
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    #: Extra names the dashboard answers to, beyond addresses and ``localhost``
    #: — an mDNS or router-assigned name, if the Pi is reached by one. Empty by
    #: default because the name depends on the network and no default could be
    #: right; see :func:`bbmon.web.app.host_is_allowed` for what it protects.
    web_allowed_hosts: tuple[str, ...] = ()
    #: How many restarts the dashboard lists. Requirement 7 asks for the last
    #: 20, configurable.
    web_restart_limit: int = 20
    database_path: Path = Path("/var/lib/bbmon/bbmon.db")

    def __post_init__(self) -> None:
        _require_positive_int(self.ping_interval_seconds, "ping.interval_seconds")
        _require_positive_int(self.speedtest_interval_hours, "speedtest.interval_hours")
        _require_positive_int(self.reboot_interval_days, "reboot.interval_days")
        _require_positive_int(self.retention_ping_days, "retention.ping_days")
        _require_ping_targets(self.ping_targets)
        _require_bind_address(self.web_host)
        _require_port(self.web_port)
        _require_allowed_hosts(self.web_allowed_hosts)
        _require_positive_int(self.web_restart_limit, "web.restart_limit")
        _require_database_path(self.database_path)


def load(path: str | Path | None = None) -> Config:
    """Read and validate the configuration file.

    :param path: The file to read. When omitted, the ``BBMON_CONFIG``
        environment variable is used, falling back to
        ``/etc/bbmon/config.yaml``.
    :raises ConfigError: if the file is missing, unreadable, not valid YAML,
        or contains an invalid or unrecognised setting.
    """
    resolved = resolve_path(path)

    try:
        text = resolved.read_text()
    except OSError as error:
        logger.error("Could not read configuration file %s: %s", resolved, error)
        raise ConfigError(f"Could not read configuration file {resolved}: {error}")

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        logger.error("Configuration file %s is not valid YAML: %s", resolved, error)
        raise ConfigError(f"Configuration file {resolved} is not valid YAML: {error}")

    try:
        return Config(**_to_field_values(document))
    except ConfigError as error:
        logger.error("Invalid configuration in %s: %s", resolved, error)
        raise ConfigError(f"Invalid configuration in {resolved}: {error}")


def reloaded(running: Config, path: str | Path | None = None) -> Config:
    """The configuration file as it now reads, for a service already running on it.

    Requirement 2's "services re-read on ... SIGHUP", and the one place the
    rules for that live, because all three services need the same answer.

    **Never raises, and never applies half a file.** A file that will not load
    leaves the running configuration in place: a service that stopped
    measuring because someone saved a typo has failed at the more important
    job. So does one that moves ``database.path``, which cannot change under a
    service that already has the database open — and which would take the
    reboot trigger and the staged-proposal path with it. That needs a restart,
    and says so rather than applying the rest of the file around it.

    :param running: What the caller is running on now, returned unchanged when
        the file cannot be applied.
    :param path: The file to re-read, defaulting as :func:`load` does.
    """
    try:
        candidate = load(path)
    except ConfigError:
        logger.exception("Keeping the running configuration; the file was not usable")
        return running

    if candidate.database_path != running.database_path:
        logger.warning(
            "Ignoring this configuration: database.path has changed from %s to "
            "%s, which takes effect only when the service restarts",
            running.database_path,
            candidate.database_path,
        )
        return running

    logger.info("Re-read the configuration from %s", resolve_path(path))
    return candidate


def to_document(config: Config) -> dict[str, dict[str, Any]]:
    """Return the nested YAML document for a config — the inverse of :func:`load`.

    Built from the same ``_FIELD_MAP`` that reads the file, so a setting added
    there is written back without a second list to keep in step. Sections come
    out in the order that map declares them, which is the order the shipped
    ``deploy/config.yaml`` uses.

    Every setting is written, including ones the file being replaced left at
    their default. A config file bbmon has written is therefore complete and
    carries no comments; the shipped file's commentary lives in the repository.
    """
    document: dict[str, dict[str, Any]] = {}
    for (section, key), field_name in _FIELD_MAP.items():
        document.setdefault(section, {})[key] = _to_yaml_value(
            getattr(config, field_name)
        )
    return document


def _to_yaml_value(value: Any) -> Any:
    """Convert a :class:`Config` field to something YAML can represent."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def resolve_path(path: str | Path | None = None) -> Path:
    """Return the configuration file location, honouring ``BBMON_CONFIG``."""
    if path is not None:
        return Path(path)
    from_env = os.environ.get(CONFIG_PATH_ENV_VAR)
    return Path(from_env) if from_env else DEFAULT_CONFIG_PATH


def _to_field_values(document: Any) -> dict[str, Any]:
    """Flatten the nested YAML document into :class:`Config` keyword arguments."""
    if document is None:
        return {}
    if not isinstance(document, dict):
        raise ConfigError("the file must contain a mapping of settings")

    known_sections = {section for section, _ in _FIELD_MAP}
    values: dict[str, Any] = {}

    for section, entries in document.items():
        if section not in known_sections:
            raise ConfigError(
                f"unknown section {section!r}; expected one of "
                f"{', '.join(sorted(known_sections))}"
            )
        if not isinstance(entries, dict):
            raise ConfigError(f"section {section!r} must be a mapping")

        for key, value in entries.items():
            field_name = _FIELD_MAP.get((section, key))
            if field_name is None:
                raise ConfigError(f"unknown setting {section}.{key}")
            values[field_name] = _coerce(field_name, value)

    return values


def _coerce(field_name: str, value: Any) -> Any:
    """Convert a YAML value to the type its :class:`Config` field declares."""
    if field_name == "ping_targets":
        if not isinstance(value, list):
            raise ConfigError("ping.targets must be a list")
        return tuple(value)
    if field_name == "web_allowed_hosts":
        if not isinstance(value, list):
            raise ConfigError("web.allowed_hosts must be a list")
        return tuple(value)
    if field_name == "database_path":
        if not isinstance(value, str):
            raise ConfigError("database.path must be a string")
        if not value.strip():
            raise ConfigError("database.path must not be empty")
        return Path(value)
    return value


def _require_positive_int(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be a whole number, got {value!r}")
    if value <= 0:
        raise ConfigError(f"{name} must be greater than 0, got {value}")


def _require_ping_targets(targets: tuple[str, ...]) -> None:
    if not targets:
        raise ConfigError("ping.targets needs at least one target")
    for target in targets:
        if not isinstance(target, str) or not _is_hostname_or_ip(target):
            raise ConfigError(
                f"ping.targets entry {target!r} is not a valid hostname or IP address"
            )


def _require_allowed_hosts(hosts: Any) -> None:
    """Check the extra names the dashboard may answer to.

    An empty list is the default and is valid: addresses and ``localhost`` are
    always answered, and most installations are reached by address alone.
    """
    if isinstance(hosts, str) or not isinstance(hosts, (tuple, list)):
        raise ConfigError(f"web.allowed_hosts must be a list, got {hosts!r}")
    for host in hosts:
        if not isinstance(host, str) or not _is_hostname_or_ip(host):
            raise ConfigError(
                f"web.allowed_hosts entry {host!r} is not a valid hostname "
                "or IP address"
            )


def _is_hostname_or_ip(candidate: str) -> bool:
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        pass

    if not candidate or len(candidate) > MAX_HOSTNAME_LENGTH:
        return False
    labels = candidate.rstrip(".").split(".")
    return all(_HOSTNAME_LABEL.match(label) for label in labels)


def _require_bind_address(host: Any) -> None:
    if not isinstance(host, str):
        raise ConfigError(f"web.host must be an IP address, got {host!r}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        raise ConfigError(
            f"web.host must be an IP address to bind to, got {host!r}"
        ) from None


def _require_port(port: Any) -> None:
    if isinstance(port, bool) or not isinstance(port, int):
        raise ConfigError(f"web.port must be a whole number, got {port!r}")
    if not 1 <= port <= 65535:
        raise ConfigError(f"web.port must be between 1 and 65535, got {port}")


def _require_database_path(path: Any) -> None:
    if not isinstance(path, Path):
        raise ConfigError(f"database.path must be a path, got {path!r}")
    # Path("") collapses to Path("."), so an empty setting arrives here as a
    # directory rather than as a blank string.
    if str(path).strip() in ("", "."):
        raise ConfigError(f"database.path must not be empty, got {str(path)!r}")
