#!/usr/bin/env bash
#
# Update bbmon on this machine from git and restart the services.
#
#   sudo /opt/bbmon/scripts/update.sh
#
# This is requirement 10's refresh path: it runs ON the Pi, pulls committed
# code, and restarts. For the development loop — pushing uncommitted work from
# a laptop — use scripts/deploy.sh from the development machine instead.
#
# Re-installs dependencies and unit files only when the files that define them
# actually changed, so the common case stays a pull and a restart.

set -euo pipefail

INSTALL_DIR=/opt/bbmon
VENV_DIR="$INSTALL_DIR/.venv"
# Read by the web app and shown in the dashboard footer — see BUILD_STAMP_NAME
# in bbmon/web/app.py, and the matching constant in scripts/deploy.sh.
BUILD_STAMP=/var/lib/bbmon/build-stamp
UNITS=(bbmon-init.service bbmon-pinger.service bbmon-speedtest.service bbmon-web.service)
SERVICES=(bbmon-pinger.service bbmon-speedtest.service bbmon-web.service)

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

require_root() {
  [[ $EUID -eq 0 ]] || die "run this with sudo: sudo $0"
}

# scripts/deploy.sh rsyncs uncommitted work onto this machine, which leaves
# tracked files modified relative to git. git then refuses to pull over them and
# suggests committing or stashing — advice that makes no sense on a deploy
# target, where nothing is authored and every local modification is a leftover
# from testing.
#
# Discarding them is therefore the correct reading of "update to committed
# code", but it is never done silently: whatever is thrown away is listed first.
discard_deploy_artifacts() {
  local owner="$1" modified
  # Tracked modifications only. Untracked files do not block a fast-forward and
  # are not ours to delete.
  #
  # --untracked-files=no rather than filtering '??' lines with grep: under
  # `set -o pipefail`, a grep that matches nothing returns 1 and takes the whole
  # assignment — and therefore the script — down with it. On a clean tree, which
  # is the normal case, that made update.sh exit 1 having printed nothing at all.
  modified="$(sudo -u "$owner" git status --porcelain --untracked-files=no | cut -c4-)"
  [[ -n "$modified" ]] || return 0

  log "Discarding local modifications from a previous deploy.sh"
  while read -r path; do note "$path"; done <<< "$modified"
  sudo -u "$owner" git checkout -- .
  note "restored to committed state"
}

# Records what is now deployed, for the dashboard footer.
#
# Written after the pull rather than after the restart: the files on disk are
# what the stamp describes, and a service that then fails to come up is
# reported loudly by this script rather than hidden behind a stale footer.
write_build_stamp() {
  printf 'updated %s from %s by update.sh\n' "$(date -Is)" "$1" > "$BUILD_STAMP"
  chmod 0644 "$BUILD_STAMP"
}

main() {
  require_root
  [[ -d "$INSTALL_DIR/.git" ]] \
    || die "$INSTALL_DIR is not a git checkout. Run scripts/bootstrap.sh first."

  cd "$INSTALL_DIR"

  # The checkout is owned by the admin account, not root, so git runs as that
  # account — pulling as root would leave root-owned objects behind and break
  # the next non-root git command.
  local owner
  owner="$(stat -c '%U' "$INSTALL_DIR")"

  discard_deploy_artifacts "$owner"

  local before after branch
  before="$(sudo -u "$owner" git rev-parse HEAD)"
  branch="$(sudo -u "$owner" git rev-parse --abbrev-ref HEAD)"

  log "Pulling $branch from origin"
  # Remote and branch are named explicitly rather than relying on this clone
  # having upstream tracking configured. That config is easy to lose — a
  # history rewrite removes the remote, and re-adding it does not restore the
  # tracking — and a bare `git pull` then fails with advice about setting an
  # upstream, which reads like a git problem rather than a deploy one.
  sudo -u "$owner" git pull --ff-only origin "$branch"
  after="$(sudo -u "$owner" git rev-parse HEAD)"

  write_build_stamp "$(sudo -u "$owner" git rev-parse --short HEAD)"

  if [[ "$before" == "$after" ]]; then
    log "Already up to date"
    note "$after"
    note "nothing was restarted"
    return 0
  fi

  local changed
  changed="$(sudo -u "$owner" git diff --name-only "$before" "$after")"
  log "Updated $before -> $after"
  while read -r path; do note "$path"; done <<< "$changed"

  if echo "$changed" | grep -q '^pyproject.toml$'; then
    log "Dependencies changed, reinstalling"
    sudo -u "$owner" "$VENV_DIR/bin/pip" install -q -e "$INSTALL_DIR"
  fi

  # ProtectSystem=strict leaves /opt read-only to the services, so they cannot
  # write __pycache__ themselves; refresh it here while we still can.
  sudo -u "$owner" "$VENV_DIR/bin/python" -m compileall -q "$INSTALL_DIR/bbmon" || true

  if echo "$changed" | grep -q '^deploy/systemd/'; then
    log "Unit files changed, reinstalling"
    for unit in "${UNITS[@]}"; do
      install -o root -g root -m 0644 \
        "$INSTALL_DIR/deploy/systemd/$unit" "/etc/systemd/system/$unit"
    done
    systemctl daemon-reload
    note "reinstalled: ${UNITS[*]}"
  fi

  log "Restarting"
  # bbmon-init is a oneshot the others Require=; restarting it re-runs the
  # schema check against the code that was just pulled, which is exactly where
  # a schema-version mismatch should stop an update.
  systemctl restart bbmon-init.service \
    || die "database initialisation failed — the services were left running on
    the previous code. journalctl -u bbmon-init"

  systemctl restart "${SERVICES[@]}"
  sleep 2

  local failed=0
  for service in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$service"; then
      note "$service is active"
    else
      printf '    \033[1;31m%s is NOT running\033[0m — journalctl -u %s -n 50\n' \
        "$service" "$service"
      failed=1
    fi
  done

  [[ "$failed" -eq 0 ]] || die "update finished but not every service came up"

  log "Updated to $(sudo -u "$owner" git rev-parse --short HEAD)"
}

main "$@"
