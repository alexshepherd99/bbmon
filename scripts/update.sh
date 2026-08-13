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
UNITS=(bbmon-init.service bbmon-pinger.service bbmon-speedtest.service bbmon-web.service)
SERVICES=(bbmon-pinger.service bbmon-speedtest.service bbmon-web.service)

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

require_root() {
  [[ $EUID -eq 0 ]] || die "run this with sudo: sudo $0"
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
