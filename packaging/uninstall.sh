#!/usr/bin/env bash
# unbork uninstaller
# Usage: sudo ./uninstall.sh [--purge] [--help]

set -euo pipefail

PURGE=0
log() { echo "[unbork-uninstall] $*" >&2; }
die() { echo "Error: $*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: sudo ./uninstall.sh [--purge] [--help]

Removes unbork from the system.

Options:
  --purge   Also remove configuration, data, logs, and the system user
  --help    Show this help message

Without --purge, configuration (/etc/unbork), data (/var/lib/unbork),
logs (/var/log/unbork), and the unbork user are preserved.
EOF
    exit 0
}

for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        --help|-h) usage ;;
        *) die "Unknown option: $arg" ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    die "must run as root (try: sudo $0)"
fi

# --- Stop and disable services ---
if command -v systemctl >/dev/null 2>&1; then
    log "Stopping unbork service..."
    systemctl stop unbork.service 2>/dev/null || true
    systemctl disable unbork.service 2>/dev/null || true
fi

# --- Detect systemd unit path ---
if [ -d /usr/lib/systemd/system ]; then
    SYSTEMD_DIR=/usr/lib/systemd/system
elif [ -d /lib/systemd/system ]; then
    SYSTEMD_DIR=/lib/systemd/system
else
    SYSTEMD_DIR=/usr/lib/systemd/system
fi

# --- Remove unit files ---
log "Removing systemd unit files..."
rm -f "$SYSTEMD_DIR"/unbork.service

# --- Remove tmpfiles config ---
log "Removing tmpfiles.d configuration..."
rm -f /usr/lib/tmpfiles.d/unbork.conf

# --- Remove application ---
log "Removing /usr/lib/unbork..."
rm -rf /usr/lib/unbork

# --- Remove runtime directory ---
rm -rf /run/unbork

# --- Reload systemd ---
# Tolerate environments where systemd isn't running as PID 1 — the
# unit file is already gone, so a failed reload doesn't matter, and
# the --purge block below would otherwise be silently skipped.
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || true
fi

# --- Purge (config, data, logs, user) ---
if [ "$PURGE" -eq 1 ]; then
    log "Purging configuration, data, logs, and user..."
    rm -rf /etc/unbork /var/lib/unbork /var/log/unbork

    if id unbork >/dev/null 2>&1; then
        deluser --system unbork 2>/dev/null || userdel unbork 2>/dev/null || true
        log "Removed system user 'unbork'"
    fi
    if getent group unbork >/dev/null 2>&1; then
        delgroup unbork 2>/dev/null || groupdel unbork 2>/dev/null || true
    fi
fi

log "Uninstall complete."
echo ""
echo "Removed:"
echo "  /usr/lib/unbork          (application)"
echo "  $SYSTEMD_DIR/unbork.service (systemd unit)"
echo "  /usr/lib/tmpfiles.d/unbork.conf"
echo "  /run/unbork              (runtime)"

if [ "$PURGE" -eq 1 ]; then
    echo "  /etc/unbork              (configuration)"
    echo "  /var/lib/unbork          (data)"
    echo "  /var/log/unbork          (logs)"
    echo "  unbork user and group"
else
    echo ""
    echo "Preserved (use --purge to remove):"
    echo "  /etc/unbork              (configuration)"
    echo "  /var/lib/unbork          (data)"
    echo "  /var/log/unbork          (logs)"
    echo "  unbork system user"
fi
