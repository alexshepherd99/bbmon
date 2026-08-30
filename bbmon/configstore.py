"""Writing the configuration file back, from a process that cannot write it.

Requirement 8's admin page edits ``/etc/bbmon/config.yaml``. That file is
``root:bbmon 0640`` and the web service runs as ``bbmon``, so it cannot write
it — deliberately. Making it writable is the obvious fix and the wrong one: it
would hand an unauthenticated, LAN-reachable web process write access to a
root-owned file in ``/etc``, on the same service that also carries a reboot
button.

**The shape is M4's reboot, reused.** The web app writes a *proposal* into the
one directory it can write, ``/var/lib/bbmon``. ``bbmon-config.path`` notices
the write and systemd — not bbmon — starts ``bbmon-config.service``, which runs
:func:`main` as root. The web app can therefore *ask* for a configuration and
cannot *instruct* root in any other way: there is no argument, path or option
for a request to reach, and no bbmon process gains a privilege.

**What root checks before believing the file**, in order, because each of these
is a way the asking side could be lying:

- it is not a symbolic link, so that naming ``/etc/shadow`` copies nothing;
- it is a regular file owned by the service user, so it came from bbmon;
- it loads as a valid configuration, so a bug in the form cannot leave every
  service unable to start;
- it does not move ``database.path`` out from under ``bbmon-reboot.path``,
  which would silently disconnect the reboot mechanism.

The proposal is consumed either way. Like the reboot trigger, it is a request
rather than a record: one that has been ruled on is spent, and one that was
refused should not sit in the state directory waiting to be tried again.
"""

from __future__ import annotations

import errno
import logging
import os
import pwd
import stat
import tempfile
from pathlib import Path

import yaml

from bbmon import reboot
from bbmon.config import (
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    load,
    to_document,
)

logger = logging.getLogger(__name__)

#: The user every bbmon service runs as, and so the only user whose proposal
#: root will act on.
SERVICE_USER = "bbmon"

#: Written beside the database, the one directory the services can write to
#: under ``ProtectSystem=strict``. Named for what it is: a proposal, not the
#: configuration itself.
STAGED_CONFIG_FILENAME = "config-staged.yaml"

#: The path ``bbmon-config.path`` watches, spelled out literally there as the
#: reboot trigger is in its own unit. The code derives its own from
#: ``database.path``; :func:`install` refuses a configuration that would move
#: the two apart, and ``tests/test_systemd_units.py`` holds the literal in step
#: with this constant.
WATCHED_STAGE_PATH = Path("/var/lib/bbmon") / STAGED_CONFIG_FILENAME

#: Fallback permissions for a destination that does not exist yet. The mode of
#: an existing file is preserved instead, so that whatever ``bootstrap.sh``
#: installed is what survives an edit.
DEFAULT_CONFIG_MODE = 0o640


class ConfigInstallError(Exception):
    """Raised when a proposed configuration is refused, or cannot be installed."""


def staged_path(database_path: str | Path) -> Path:
    """Where a proposed configuration is staged, given the configured database.

    Derived from ``database.path`` rather than configured separately, for the
    same reason as the reboot trigger: both have to sit in the one writable
    state directory, and a second setting would only be a way for them to
    disagree.
    """
    return Path(database_path).parent / STAGED_CONFIG_FILENAME


def to_yaml(config: Config) -> str:
    """Render a configuration as the YAML document :func:`load` reads back."""
    return yaml.safe_dump(to_document(config), sort_keys=False)


def stage(config: Config, path: str | Path) -> None:
    """Write a proposed configuration for root to rule on.

    Written through a temporary file and a rename, so that the watcher never
    sees a half-written proposal: the rename is atomic, and until it happens
    there is nothing at the watched path to notice.

    :raises ConfigInstallError: if the proposal could not be written.
    """
    path = Path(path)
    try:
        _write_atomically(path, to_yaml(config), mode=DEFAULT_CONFIG_MODE)
    except OSError as error:
        logger.error("Could not stage a configuration at %s: %s", path, error)
        raise ConfigInstallError(f"could not stage a configuration: {error}")

    logger.info("Staged a proposed configuration at %s", path)


def install(
    staged: str | Path, destination: str | Path, expected_owner_uid: int
) -> None:
    """Install a staged configuration, as root, if it survives every check.

    Does nothing when no proposal is pending: systemd may start the unit for
    reasons other than a fresh write, and an empty run is not a fault.

    :param expected_owner_uid: The service user. A proposal owned by anyone
        else did not come from bbmon and is refused.
    :raises ConfigInstallError: if the proposal is refused. The destination is
        left exactly as it was, and the proposal is consumed either way.
    """
    staged, destination = Path(staged), Path(destination)

    proposal = _read_proposal(staged, expected_owner_uid)
    if proposal is None:
        logger.info("No configuration proposal is pending at %s", staged)
        return

    _install_validated(proposal, destination)
    logger.info("Installed a new configuration at %s", destination)


def _read_proposal(staged: Path, expected_owner_uid: int) -> str | None:
    """Read a proposal without following anything, and consume it.

    ``O_NOFOLLOW`` is what makes the symbolic-link case safe: the open fails
    rather than reading the target, so a proposal naming a root-only file
    cannot have its contents copied into a file the service group can read.
    Every subsequent check is made against the same open file descriptor, so
    nothing swapped in afterwards can be what gets installed.
    """
    try:
        descriptor = os.open(staged, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as error:
        _consume(staged)
        if error.errno == errno.ELOOP:
            logger.error("Refused a configuration proposal: %s is a symlink", staged)
            raise ConfigInstallError(
                f"{staged} is a symbolic link; a proposal must be a regular file"
            )
        logger.error("Could not read the configuration proposal %s: %s", staged, error)
        raise ConfigInstallError(f"could not read {staged}: {error}")

    try:
        with os.fdopen(descriptor, "r") as handle:
            details = os.fstat(handle.fileno())
            if not stat.S_ISREG(details.st_mode):
                raise ConfigInstallError(f"{staged} is not a regular file")
            if details.st_uid != expected_owner_uid:
                raise ConfigInstallError(
                    f"{staged} is owned by uid {details.st_uid}, but only "
                    f"uid {expected_owner_uid} may propose a configuration"
                )
            return handle.read()
    except ConfigInstallError as error:
        logger.error("Refused a configuration proposal: %s", error)
        raise
    except OSError as error:
        logger.error("Could not read the configuration proposal %s: %s", staged, error)
        raise ConfigInstallError(f"could not read {staged}: {error}")
    finally:
        _consume(staged)


def _install_validated(proposal: str, destination: Path) -> None:
    """Validate the proposal's exact bytes, then put them in place.

    Validated as a file beside the destination rather than as a string, so that
    what is checked and what is renamed into place are the same bytes — and so
    that the rename is on one filesystem and therefore atomic. A service
    reading the file mid-install sees the old configuration or the new one,
    never a partial write.
    """
    handle, temporary = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}."
    )
    candidate = Path(temporary)
    try:
        with os.fdopen(handle, "w") as file:
            file.write(proposal)
        _match_destination(candidate, destination)
        _require_installable(load(candidate))
        os.replace(candidate, destination)
    except ConfigError as error:
        candidate.unlink(missing_ok=True)
        logger.error("Refused an invalid configuration proposal: %s", error)
        raise ConfigInstallError(f"the proposed configuration is invalid: {error}")
    except Exception:
        candidate.unlink(missing_ok=True)
        raise


def _require_installable(config: Config) -> None:
    """Refuse a valid configuration that would break the reboot mechanism.

    ``plan.md`` records this as the rule the admin form has to validate
    against, and it is checked here too because this is the side that root
    acts on. The units name the reboot trigger literally while the code derives
    it from ``database.path``; move that setting and writes to the trigger keep
    succeeding while nothing watches it, so the Pi simply stops rebooting.
    """
    trigger = reboot.trigger_file_path(config.database_path)
    if trigger == reboot.WATCHED_TRIGGER_PATH:
        return

    logger.error(
        "Refused a configuration whose database.path would move the reboot "
        "trigger to %s, away from the %s that bbmon-reboot.path watches",
        trigger,
        reboot.WATCHED_TRIGGER_PATH,
    )
    raise ConfigInstallError(
        f"database.path would put the reboot trigger at {trigger}, but "
        f"bbmon-reboot.path watches {reboot.WATCHED_TRIGGER_PATH} — nothing "
        f"would notice a reboot request. Keep the database in "
        f"{reboot.WATCHED_TRIGGER_PATH.parent}."
    )


def _match_destination(candidate: Path, destination: Path) -> None:
    """Give the replacement the ownership and permissions it is replacing.

    Whatever ``bootstrap.sh`` installed is what should survive an edit, and a
    freshly created temporary file is root-owned and 0600 — installing that
    would leave the services unable to read their own configuration.
    """
    try:
        existing = destination.stat()
    except FileNotFoundError:
        candidate.chmod(DEFAULT_CONFIG_MODE)
        return

    candidate.chmod(stat.S_IMODE(existing.st_mode))
    os.chown(candidate, existing.st_uid, existing.st_gid)


def _write_atomically(path: Path, text: str, mode: int) -> None:
    """Replace a file's contents in one step, at the given permissions."""
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    candidate = Path(temporary)
    try:
        with os.fdopen(handle, "w") as file:
            file.write(text)
        candidate.chmod(mode)
        os.replace(candidate, path)
    except Exception:
        candidate.unlink(missing_ok=True)
        raise


def _consume(staged: Path) -> None:
    """Remove a proposal that has been ruled on, successfully or not.

    ``unlink`` does not follow symbolic links, so a proposal that turned out to
    be one takes the link with it and leaves whatever it pointed at alone.
    """
    try:
        staged.unlink(missing_ok=True)
    except OSError as error:
        logger.error(
            "Could not remove the configuration proposal %s: %s", staged, error
        )


def main() -> int:
    """Entrypoint for ``python -m bbmon.configstore``, run as root by systemd.

    Both paths are named literally rather than read from the configuration:
    this process runs as root and must not take direction from the file it is
    being asked to replace.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    try:
        service_user = pwd.getpwnam(SERVICE_USER)
    except KeyError:
        logger.error("There is no %s user; has bootstrap.sh been run?", SERVICE_USER)
        return 1

    try:
        install(WATCHED_STAGE_PATH, DEFAULT_CONFIG_PATH, service_user.pw_uid)
    except ConfigInstallError:
        logger.exception("The proposed configuration was not installed")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
