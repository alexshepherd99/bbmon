#!/usr/bin/env bash
#
# Push the local working tree to the Pi and restart what it affects.
#
#   scripts/deploy.sh              # deploy to $BBMON_HOST, default raspberrypi
#   scripts/deploy.sh pi@192.0.2.10   # deploy somewhere else
#   scripts/deploy.sh --dry-run    # show what would change, change nothing
#
# This is the development loop: it deliberately does NOT require a commit, so
# testing a change on real hardware does not fill main with WIP. Use
# scripts/update.sh for a proper deploy from committed code.
#
# Run scripts/bootstrap.sh on the Pi first — this script only moves code, it
# does not create users, install dependencies, or install unit files.

set -euo pipefail

INSTALL_DIR=/opt/bbmon
DEFAULT_HOST=raspberrypi

# Read by the web app and shown in the dashboard footer, so requirement 7's
# build indicator says what is actually deployed. The app derives this path
# from database.path; move that setting and the footer says "build unknown".
# See BUILD_STAMP_NAME in bbmon/web/app.py.
BUILD_STAMP=/var/lib/bbmon/build-stamp

# Never pushed: the venv is architecture-specific, .git belongs to the Pi's own
# checkout (update.sh pulls into it), and var/ plus the caches are local junk.
EXCLUDES=(
  --exclude '.venv/'
  --exclude '.git/'
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude '.pytest_cache/'
  --exclude 'build/'
  --exclude '*.egg-info/'
  --exclude 'var/'
  --exclude 'node_modules/'
  --exclude 'dev-config.yaml'
)

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

repo_root() { cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd; }

# Reads rsync --itemize-changes output on stdin and prints the paths whose
# *content* changed, one per line.
#
# The first character of an itemised line is the update type, and only some of
# them mean the file's content moved:
#
#   <  >   transferred to/from the remote — real content change
#   c      created locally (a new directory, a symlink)
#   h      turned into a hard link
#   .      NO update happened; only attributes such as mtime or permissions
#   *      a message follows, e.g. "*deleting"
#
# Treating "." as a change is wrong and was the original bug: rsync -a syncs
# mtimes, and a fresh git clone on the Pi has clone-time mtimes throughout, so
# every identical file itemised as ".f..t......" and deploy.sh restarted all
# three services on every deploy while reporting unit files as modified. That
# silently defeated the entire point of working out which services to restart.
changed_paths_from_itemize() {
  awk '
    $1 == "*deleting" && NF > 1 { print $2; next }
    $1 ~ /^[<>ch]/ && NF > 1    { print $2 }
  '
}

# Maps a changed file to the services that need restarting. Shared modules
# (config, db, models, service) are used by all three, so a change to one of
# them restarts everything; a change confined to the web app or to a single
# collector restarts only that service.
services_for_path() {
  case "$1" in
    bbmon/web/*)                       echo "bbmon-web" ;;
    bbmon/pinger.py|bbmon/collectors/ping.py)
                                       echo "bbmon-pinger" ;;
    bbmon/speedtest.py|bbmon/collectors/speedtest.py)
                                       echo "bbmon-speedtest" ;;
    bbmon/*)                           echo "bbmon-pinger bbmon-speedtest bbmon-web" ;;
    *)                                 echo "" ;;
  esac
}

# The line recorded on the Pi as "what is deployed here".
#
# The revision is marked "+local" whenever the working tree differs from HEAD,
# because this script deliberately pushes uncommitted work — without that
# marker the stamp would name a commit that is not what was actually copied,
# which is worse than saying nothing.
build_stamp_text() {
  local root="$1" revision
  revision="$(git -C "$root" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  if [[ -n "$(git -C "$root" status --porcelain 2>/dev/null)" ]]; then
    revision="$revision+local"
  fi
  printf 'deployed %s from %s by deploy.sh\n' "$(date -Is)" "$revision"
}

main() {
  local host="${BBMON_HOST:-$DEFAULT_HOST}"
  local dry_run=0
  local rsync_extra=()

  for arg in "$@"; do
    case "$arg" in
      --dry-run) dry_run=1; rsync_extra+=(--dry-run) ;;
      -*) die "unknown option $arg" ;;
      *) host="$arg" ;;
    esac
  done

  local root
  root="$(repo_root)"

  log "Deploying $root to $host:$INSTALL_DIR"
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" true \
    || die "cannot reach $host over SSH without a password.
    Check docs/pi-access.md — key-only access is set up there."

  # --itemize-changes is what makes "restart only what changed" possible;
  # without it rsync gives no machine-readable account of what it did.
  local output
  output="$(rsync -az --delete --itemize-changes "${EXCLUDES[@]}" "${rsync_extra[@]}" \
    "$root/" "$host:$INSTALL_DIR/")"

  # Written whether or not anything changed, and before the early return
  # below: the stamp records what is deployed here, and an unchanged deploy
  # still confirms it. The text goes over stdin rather than onto the remote
  # command line, so nothing in it is ever interpreted by a shell.
  if [[ "$dry_run" -eq 0 ]]; then
    # shellcheck disable=SC2029  # BUILD_STAMP is a literal constant above,
    # expanded locally on purpose; nothing derived from input reaches here.
    build_stamp_text "$root" | ssh "$host" "sudo tee $BUILD_STAMP >/dev/null" \
      || die "could not write $BUILD_STAMP on $host"
  fi

  local changed
  changed="$(echo "$output" | changed_paths_from_itemize)"

  if [[ -z "$changed" ]]; then
    log "Nothing changed"
    note "no files differ from $host, so no service was restarted"
    return 0
  fi

  log "Changed files"
  while read -r path; do note "$path"; done <<< "$changed"

  if echo "$changed" | grep -q '^deploy/systemd/'; then
    printf '\n\033[1;33m    Unit files changed. deploy.sh does not install them —\n'
    printf '    run scripts/bootstrap.sh on %s to pick them up.\033[0m\n' "$host"
  fi

  if echo "$changed" | grep -q '^pyproject.toml$'; then
    printf '\n\033[1;33m    pyproject.toml changed. If dependencies changed, run\n'
    printf '    scripts/bootstrap.sh on %s — the editable install only tracks code.\033[0m\n' "$host"
  fi

  local restart=()
  while read -r path; do
    [[ -n "$path" ]] || continue
    for service in $(services_for_path "$path"); do
      # shellcheck disable=SC2076
      [[ " ${restart[*]-} " == *" $service "* ]] || restart+=("$service")
    done
  done <<< "$changed"

  if [[ ${#restart[@]} -eq 0 ]]; then
    log "No service code changed"
    note "nothing to restart"
    return 0
  fi

  if [[ "$dry_run" -eq 1 ]]; then
    log "Dry run"
    note "would restart: ${restart[*]}"
    return 0
  fi

  log "Restarting ${restart[*]}"
  # Each collector flushes its buffer on SIGTERM, so a restart loses nothing.
  #
  # The service names expand locally before the remote shell sees them, which
  # is intended. They are safe to expand because services_for_path only ever
  # returns literals from its own case statement — nothing derived from a
  # filename, an argument, or the config reaches this command line.
  # shellcheck disable=SC2029
  ssh "$host" "sudo systemctl restart ${restart[*]}"

  for service in "${restart[@]}"; do
    # shellcheck disable=SC2029  # literal service name, as above
    if ssh "$host" "systemctl is-active --quiet $service"; then
      note "$service is active"
    else
      printf '    \033[1;31m%s is NOT running\033[0m — ssh %s journalctl -u %s -n 50\n' \
        "$service" "$host" "$service"
      exit 1
    fi
  done

  log "Deployed"
}

# Sourcing this file defines its functions without deploying anything, which is
# how tests/test_deploy_script.py exercises services_for_path without a Pi.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
