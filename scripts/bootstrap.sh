#!/usr/bin/env bash
#
# First-time setup for bbmon on a Raspberry Pi.
#
#   sudo git clone https://github.com/alexshepherd99/bbmon /opt/bbmon
#   sudo /opt/bbmon/scripts/bootstrap.sh
#
# Creates the bbmon service user, installs dependencies and the Ookla
# Speedtest CLI, deploys the default config, and enables the systemd units.
#
# Safe to re-run: every step checks before it acts, and an existing
# /etc/bbmon/config.yaml is never overwritten. Re-running is the supported way
# to pick up a new unit file or a changed dependency.
#
# For the day-to-day loop use scripts/deploy.sh (push the working tree) or
# scripts/update.sh (git pull) instead — neither needs this script again.

set -euo pipefail

INSTALL_DIR=/opt/bbmon
CONFIG_DIR=/etc/bbmon
CONFIG_FILE="$CONFIG_DIR/config.yaml"
STATE_DIR=/var/lib/bbmon
SERVICE_USER=bbmon
VENV_DIR="$INSTALL_DIR/.venv"
SPEEDTEST_BIN=/usr/local/bin/speedtest

# Pinned deliberately rather than tracking latest. The alternative considered
# was Ookla's apt repository, which was rejected: adding it would give Ookla's
# signing key authority to install any package on this machine through routine
# updates, where a checksummed tarball is a one-time, verifiable grant.
OOKLA_VERSION=1.2.0

# SHA-256 of each published tarball, recorded 2026-08-12 by downloading them.
# The x86_64 value matches the one logged when the binary was first run for
# real, which is what confirms these are that same release.
declare -A OOKLA_SHA256=(
  [aarch64]=3953d231da3783e2bf8904b6dd72767c5c6e533e163d3742fd0437affa431bd3
  [armhf]=e45fcdebbd8a185553535533dd032d6b10bc8c64eee4139b1147b9c09835d08d
  [armel]=629a455a2879224bd0dbd4b36d8c721dda540717937e4660b4d2c966029466bf
  [x86_64]=5690596c54ff9bed63fa3732f818a05dbc2db19ad36ed68f21ca5f64d5cfeeb7
  [i386]=9ff7e18dbae7ee0e03c66108445a2fb6ceea6c86f66482e1392f55881b772fe8
)

APT_PACKAGES=(python3 python3-venv python3-pip iputils-ping rsync curl ca-certificates)

UNITS=(bbmon-init.service bbmon-pinger.service bbmon-speedtest.service bbmon-web.service)

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

require_root() {
  [[ $EUID -eq 0 ]] || die "run this with sudo: sudo $0"
}

# The account that will own the checkout, so scripts/deploy.sh can rsync into
# it over SSH without needing sudo on the remote side.
admin_user() {
  echo "${SUDO_USER:-root}"
}

# Ookla names its tarballs by architecture; uname -m does not use the same
# strings. Mapping here rather than hardcoding one keeps this working on a
# 32-bit Raspberry Pi OS install, which a Pi 3 can perfectly well be running.
ookla_arch() {
  case "$(uname -m)" in
    aarch64|arm64) echo aarch64 ;;
    armv7l|armv8l) echo armhf ;;
    armv6l) echo armel ;;
    x86_64|amd64) echo x86_64 ;;
    i386|i486|i586|i686) echo i386 ;;
    *) die "unsupported architecture $(uname -m); no Ookla Speedtest CLI build is mapped for it" ;;
  esac
}

install_packages() {
  log "Installing system packages"
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${APT_PACKAGES[@]}"
  note "installed: ${APT_PACKAGES[*]}"
}

create_service_user() {
  log "Creating the $SERVICE_USER service user"
  if id -u "$SERVICE_USER" >/dev/null 2>&1; then
    note "already exists"
  else
    # Home is the state directory, not somewhere under /home, so ProtectHome=yes
    # in the units does not hide it from the Ookla CLI's settings file.
    useradd --system --home-dir "$STATE_DIR" --create-home \
            --shell /usr/sbin/nologin "$SERVICE_USER"
    note "created as a system user with no login shell"
  fi

  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 "$STATE_DIR"
}

install_code() {
  log "Installing the application to $INSTALL_DIR"
  local source_dir
  source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

  if [[ "$source_dir" != "$INSTALL_DIR" ]]; then
    note "copying from $source_dir"
    install -d -m 0755 "$INSTALL_DIR"
    # .git comes too, so scripts/update.sh has a checkout to pull into.
    rsync -a --delete --exclude '.venv/' "$source_dir/" "$INSTALL_DIR/"
  else
    note "already running from $INSTALL_DIR"
  fi

  local owner
  owner="$(admin_user)"
  chown -R "$owner":"$owner" "$INSTALL_DIR"
  note "owned by $owner, so deploy.sh needs no sudo to rsync into it"
}

install_python_environment() {
  log "Building the Python environment"
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    # A virtualenv rather than a system-wide pip install: Bookworm marks the
    # system Python as externally managed (PEP 668), and this keeps bbmon's
    # dependencies from mixing with apt's.
    sudo -u "$(admin_user)" python3 -m venv "$VENV_DIR"
  fi

  # Editable, so scripts/deploy.sh and scripts/update.sh take effect by
  # replacing files and restarting — no reinstall step in the day-to-day loop.
  sudo -u "$(admin_user)" "$VENV_DIR/bin/pip" install -q --upgrade pip
  sudo -u "$(admin_user)" "$VENV_DIR/bin/pip" install -q -e "$INSTALL_DIR"

  # ProtectSystem=strict makes /opt read-only to the services, so they cannot
  # write __pycache__ themselves. Compiling now spares a Pi 3 the work on
  # every start.
  sudo -u "$(admin_user)" "$VENV_DIR/bin/python" -m compileall -q "$INSTALL_DIR/bbmon" || true
  note "installed editable into $VENV_DIR"
}

install_speedtest_cli() {
  log "Installing the Ookla Speedtest CLI"
  local arch expected
  arch="$(ookla_arch)"
  expected="${OOKLA_SHA256[$arch]}"

  if [[ -x "$SPEEDTEST_BIN" ]] && "$SPEEDTEST_BIN" --version 2>/dev/null | grep -q "$OOKLA_VERSION"; then
    note "$OOKLA_VERSION already installed"
    return
  fi

  local tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN

  local url="https://install.speedtest.net/app/cli/ookla-speedtest-${OOKLA_VERSION}-linux-${arch}.tgz"
  note "downloading $url"
  curl -fsSL -o "$tmp/speedtest.tgz" "$url" \
    || die "could not download the Speedtest CLI from $url"

  local actual
  actual="$(sha256sum "$tmp/speedtest.tgz" | cut -d' ' -f1)"
  if [[ "$actual" != "$expected" ]]; then
    die "checksum mismatch for the $arch Speedtest CLI.
    expected $expected
    actual   $actual
    Refusing to install. Either the pinned version was re-published or the
    download was tampered with; do not work around this by editing the hash
    without establishing which."
  fi
  note "sha256 verified"

  tar -xzf "$tmp/speedtest.tgz" -C "$tmp" speedtest
  install -o root -g root -m 0755 "$tmp/speedtest" "$SPEEDTEST_BIN"
  note "installed $("$SPEEDTEST_BIN" --version 2>/dev/null | head -1)"
}

install_config() {
  log "Deploying the configuration"
  install -d -m 0755 "$CONFIG_DIR"

  if [[ -f "$CONFIG_FILE" ]]; then
    note "$CONFIG_FILE already exists, leaving it alone"
  else
    install -o root -g "$SERVICE_USER" -m 0640 \
      "$INSTALL_DIR/deploy/config.yaml" "$CONFIG_FILE"
    note "installed the default config, readable by $SERVICE_USER only"
  fi
}

# Works out what net.ipv4.ping_group_range should become so that GID is inside
# it, given the range currently in force. Prints nothing when GID is already
# permitted and no change is needed.
#
# The rule is that this never *narrows* the existing range. Writing a bare
# "$gid $gid" would be a system-wide revocation of unprivileged ICMP from every
# other account, which is not bootstrap.sh's business — and is not theoretical:
# Raspberry Pi OS ships this wide open at "0 2147483647", not empty as was
# assumed when this was first written.
#
# A range is disabled by having its low bound above its high bound; the kernel
# default for that state is "1 0".
ping_group_range_for() {
  local gid="$1" low="$2" high="$3"

  if (( low <= gid && gid <= high )); then
    return 0
  fi
  if (( low > high )); then
    echo "$gid $gid"
    return 0
  fi
  echo "$(( low < gid ? low : gid )) $(( high > gid ? high : gid ))"
}

allow_unprivileged_ping() {
  log "Allowing unprivileged ICMP for the $SERVICE_USER group"
  local gid
  gid="$(getent group "$SERVICE_USER" | cut -d: -f3)"
  [[ -n "$gid" ]] || die "could not determine the $SERVICE_USER group id"

  local current low high wanted
  current="$(cat /proc/sys/net/ipv4/ping_group_range)"
  low="$(echo "$current" | awk '{print $1}')"
  high="$(echo "$current" | awk '{print $2}')"
  wanted="$(ping_group_range_for "$gid" "$low" "$high")"

  if [[ -z "$wanted" ]]; then
    note "group $gid is already within the current range ($low $high), leaving it alone"
    return 0
  fi

  # Why this is needed: NoNewPrivileges=yes in the units makes the kernel
  # ignore /usr/bin/ping's cap_net_raw file capability, so ping would fall back
  # to a raw socket and fail with "Operation not permitted". Putting the group
  # in ping_group_range instead lets ping open an unprivileged ICMP datagram
  # socket, which needs no capability at all — so the sandbox stays intact and
  # the service still holds no CAP_NET_RAW, as plan.md decided it should not.
  #
  cat > /etc/sysctl.d/60-bbmon-ping.conf <<EOF
# Installed by bbmon's scripts/bootstrap.sh. Lets the bbmon service group use
# unprivileged ICMP datagram sockets, so the pinger works under
# NoNewPrivileges=yes without being granted CAP_NET_RAW.
#
# Widened from "$low $high" to include group $gid. This never narrows the
# range that was already in force — revoking unprivileged ICMP from other
# accounts is not this installer's business.
net.ipv4.ping_group_range = $wanted
EOF
  sysctl -q --system
  note "net.ipv4.ping_group_range = $(cat /proc/sys/net/ipv4/ping_group_range)"
}

install_units() {
  log "Installing the systemd units"
  for unit in "${UNITS[@]}"; do
    install -o root -g root -m 0644 \
      "$INSTALL_DIR/deploy/systemd/$unit" "/etc/systemd/system/$unit"
  done
  systemctl daemon-reload
  systemctl enable -q "${UNITS[@]}"
  note "enabled: ${UNITS[*]}"
}

start_services() {
  log "Starting the services"
  systemctl restart "${UNITS[@]}"
  sleep 2
  local failed=0
  for unit in "${UNITS[@]}"; do
    if systemctl is-active --quiet "$unit"; then
      note "$unit is active"
    else
      printf '    \033[1;31m%s is NOT running\033[0m — journalctl -u %s\n' "$unit" "$unit"
      failed=1
    fi
  done
  return "$failed"
}

main() {
  require_root
  install_packages
  create_service_user
  install_code
  install_python_environment
  install_speedtest_cli
  install_config
  allow_unprivileged_ping
  install_units

  if start_services; then
    local port
    port="$(awk '/^  port:/ {print $2}' "$CONFIG_FILE")"
    log "Done"
    note "Dashboard: http://$(hostname -I | awk '{print $1}'):${port:-8080}"
    note "Config:    $CONFIG_FILE"
    note "Database:  $STATE_DIR/bbmon.db"
    note "Logs:      journalctl -u bbmon-pinger -f"
  else
    die "bootstrap finished but not every service came up — see the lines above"
  fi
}

# Sourcing defines the functions without installing anything, which is how
# tests/test_bootstrap_script.py exercises ping_group_range_for without a Pi.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
