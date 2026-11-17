#!/usr/bin/env bash
# unbork installer — run from inside extracted tarball
# Usage: sudo ./install.sh [--help]

set -euo pipefail

log() { echo "[unbork-install] $*" >&2; }
die() { echo "Error: $*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: sudo ./install.sh [--help]

Installs unbork to standard FHS paths:
  /usr/lib/unbork         — application code
  /etc/unbork             — configuration
  /var/lib/unbork         — persistent data
  /var/log/unbork         — log files
  /usr/lib/systemd/system    — systemd unit files

This script must be run as root from inside the extracted tarball directory.
EOF
    exit 0
}

# --- Parse args ---
for arg in "$@"; do
    case "$arg" in
        --help|-h) usage ;;
        *) die "Unknown option: $arg" ;;
    esac
done

# --- Root check ---
if [ "$(id -u)" -ne 0 ]; then
    die "must run as root (try: sudo $0)"
fi

# --- Locate script directory (tarball root) ---
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

# --- Verify tarball structure ---
for required_dir in usr/lib/unbork systemd tmpfiles.d; do
    if [ ! -d "$SCRIPT_DIR/$required_dir" ]; then
        die "Expected directory '$required_dir' not found in $SCRIPT_DIR — are you running from the extracted tarball?"
    fi
done
if [ ! -f "$SCRIPT_DIR/etc/unbork.toml" ]; then
    die "Expected file 'etc/unbork.toml' not found in $SCRIPT_DIR — are you running from the extracted tarball?"
fi

# --- Create system user ---
log "Creating unbork system user..."
if id unbork >/dev/null 2>&1; then
    log "User 'unbork' already exists, skipping"
else
    # useradd is universally available on both Debian/Ubuntu (passwd
    # package) and RHEL/Rocky (shadow-utils) and accepts the same flags
    # on both — preferred over adduser, which on RPM-family is a
    # useradd wrapper that rejects Debian-style flags.
    if ! command -v useradd >/dev/null 2>&1; then
        die "useradd not found — cannot create system user"
    fi
    useradd --system --user-group --home-dir /var/lib/unbork --no-create-home --shell /usr/sbin/nologin --comment "unbork service account" unbork
    log "Created system user 'unbork'"
fi

# --- Install application files ---
log "Installing application to /usr/lib/unbork..."
cp -r "$SCRIPT_DIR/usr/lib/unbork" /usr/lib/

# --- Install configuration (preserve existing) ---
if [ -f /etc/unbork/unbork.toml ]; then
    log "Existing /etc/unbork/unbork.toml found — preserving (not overwritten)"
else
    log "Installing default configuration to /etc/unbork..."
    mkdir -p /etc/unbork
    # The directory mode matters as much as the file's: unbork.toml holds
    # security.secret_key and security.encryption_key. mkdir uses the invoking
    # root's umask (usually 0755), so without this the tarball install ended up
    # more permissive than the .deb/.rpm, which declare 0750 root:unbork for
    # this path in packaging/nfpm.yaml.
    chmod 750 /etc/unbork
    chown root:unbork /etc/unbork
    cp "$SCRIPT_DIR/etc/unbork.toml" /etc/unbork/
    chmod 640 /etc/unbork/unbork.toml
    chown root:unbork /etc/unbork/unbork.toml
fi

# --- Detect systemd unit path ---
if [ -d /usr/lib/systemd/system ]; then
    SYSTEMD_DIR=/usr/lib/systemd/system
elif [ -d /lib/systemd/system ]; then
    SYSTEMD_DIR=/lib/systemd/system
else
    SYSTEMD_DIR=/usr/lib/systemd/system
    mkdir -p "$SYSTEMD_DIR"
fi

log "Installing systemd unit to $SYSTEMD_DIR..."
cp "$SCRIPT_DIR"/systemd/unbork.service "$SYSTEMD_DIR/"

# --- Install tmpfiles.d ---
log "Installing tmpfiles.d configuration..."
mkdir -p /usr/lib/tmpfiles.d
cp "$SCRIPT_DIR/tmpfiles.d/unbork.conf" /usr/lib/tmpfiles.d/

# --- Create data directories ---
log "Creating data directories..."
mkdir -p /var/lib/unbork /var/log/unbork
chown -R unbork:unbork /var/lib/unbork /var/log/unbork

# --- Create runtime directory ---
log "Creating runtime directory..."
if command -v systemd-tmpfiles >/dev/null 2>&1; then
    systemd-tmpfiles --create unbork.conf 2>/dev/null || true
else
    mkdir -p /run/unbork
    chown unbork:unbork /run/unbork
fi

# --- Reload systemd ---
if command -v systemctl >/dev/null 2>&1; then
    log "Reloading systemd daemon..."
    # Tolerate environments where systemd isn't running as PID 1
    # (containers, chroots, CI). The unit is on disk either way; on
    # a real production host the reload succeeds.
    systemctl daemon-reload || true
fi

# --- Success ---
log "Installation complete."

cat <<'EOF'

unbork installed to /usr/lib/unbork

Next steps:
  1. Edit /etc/unbork/unbork.toml with your settings:
       - [security] secret_key and encryption_key: generate with: openssl rand -base64 32
       - [database] url: your PostgreSQL connection string
       - [security] unbork_server_ip: this server's IP address

  2. Enable and start the service:
       systemctl enable --now unbork.service

  3. Check status:
       systemctl status unbork.service
       journalctl -u unbork -f

EOF
