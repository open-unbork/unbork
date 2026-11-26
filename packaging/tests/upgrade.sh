#!/usr/bin/env bash
# In-container upgrade test: a 1.1.0 package install (before the rename) with data,
# upgraded to the candidate unbork package by the steps in docs/upgrade.md
# ("Upgrading to 2.0.0", packages).
#
# Run inside a clean Ubuntu or Rocky container by run-smoke.sh, with the old
# packages at /old, the candidate at /dist and this directory at /tests.
#
# Containers have no systemd, so each version is started the way its unit
# says: User=, WorkingDirectory=, Environment= and ExecStart= are read from
# the installed unit file, and /run/<name> is created from its tmpfiles.d
# entry. The unit's sandboxing is not applied; C-001's manual runs on a real
# systemd host cover that.
#
# Usage: upgrade.sh <old-version> <new-version> [deb|rpm|tarball]
# The format defaults to the distribution's own package format.
set -euo pipefail

OLD="${1:?old version required (e.g. 1.1.0)}"
NEW="${2:?new version required (e.g. 2.0.0)}"

OLD_NAME=labdog   # old-name-ok: the package being upgraded from
NEW_NAME=unbork
OLD_UNIT="/usr/lib/systemd/system/${OLD_NAME}.service"
NEW_UNIT="/usr/lib/systemd/system/${NEW_NAME}.service"
OLD_CONF="/etc/${OLD_NAME}/${OLD_NAME}.toml"
NEW_CONF="/etc/${NEW_NAME}/${NEW_NAME}.toml"
OLD_DATA="/var/lib/${OLD_NAME}"
NEW_DATA="/var/lib/${NEW_NAME}"

BASE=http://127.0.0.1:8000
EMAIL=admin@example.com
PASSWORD='Upgrade-Test-Passw0rd!'
WORK=$(mktemp -d)
PG_DB=upgradedb
PG_USER=upgrader
PG_PASS=$(head -c 18 /dev/urandom | od -An -tx1 | tr -d ' \n')
MARKER="# upgrade-test-marker $(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')"

fail() { echo "UPGRADE FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok  - $*"; }
step() { echo; echo "== $* =="; }

if command -v apt-get >/dev/null 2>&1; then FAMILY=deb; else FAMILY=rpm; fi
FORMAT="${3:-$FAMILY}"
case "$FORMAT" in deb|rpm|tarball) ;; *) fail "unknown format '$FORMAT'" ;; esac

# --- PostgreSQL and Redis from the distribution ------------------------------
step "PostgreSQL and Redis ($FAMILY)"
if [ "$FAMILY" = deb ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq postgresql redis-server curl tar >/dev/null
    PG_VER=$(find /etc/postgresql -mindepth 1 -maxdepth 1 -printf '%f\n' | sort -n | tail -1)
    pg_ctlcluster "$PG_VER" main start
else
    # The package says PostgreSQL 14+; Rocky 9's default stream is 13.
    dnf module -y -q enable postgresql:16 >/dev/null
    dnf install -y -q postgresql-server redis procps-ng >/dev/null
    runuser -u postgres -- initdb -D /var/lib/pgsql/data \
        --auth-local=peer --auth-host=scram-sha-256 >/dev/null
    runuser -u postgres -- pg_ctl -D /var/lib/pgsql/data -l /var/lib/pgsql/server.log -w start >/dev/null
fi
redis-server --daemonize yes >/dev/null
psql_admin() { runuser -u postgres -- psql -v ON_ERROR_STOP=1 -qAt "$@"; }
psql_admin -c "CREATE ROLE $PG_USER LOGIN PASSWORD '$PG_PASS'"
psql_admin -c "CREATE DATABASE $PG_DB OWNER $PG_USER"
psql_db() { psql_admin -d "$PG_DB" -c "$1"; }
ok "PostgreSQL $(psql_admin -c 'SHOW server_version' | cut -d' ' -f1) and Redis running"

# --- Start and stop a version the way its unit does --------------------------
# Sets SVC_PID (a session leader, so the whole process tree can be stopped).
start_unit() {
    local unit="$1" log="$2" user wd exec envs=() line
    user=$(sed -n 's/^User=//p' "$unit")
    wd=$(sed -n 's/^WorkingDirectory=//p' "$unit")
    exec=$(sed -n 's/^ExecStart=//p' "$unit")
    while IFS= read -r line; do envs+=("$line"); done < <(sed -n 's/^Environment=//p' "$unit")
    [ -n "$user" ] && [ -n "$wd" ] && [ -n "$exec" ] || fail "cannot read User/WorkingDirectory/ExecStart from $unit"
    # What systemd-tmpfiles would create at boot: the "d" lines of the
    # package's own tmpfiles.d file.
    local tmpfiles
    tmpfiles="/usr/lib/tmpfiles.d/$(basename "$unit" .service).conf"
    if [ -f "$tmpfiles" ]; then
        awk '$1 == "d" { print $2, $3, $4, $5 }' "$tmpfiles" | while read -r path mode owner group; do
            install -d -m "$mode" -o "$owner" -g "$group" "$path"
        done
    fi
    ( cd "$wd" && exec setsid runuser -u "$user" -- env -i HOME=/ "${envs[@]}" sh -c "exec $exec" ) >"$log" 2>&1 &
    SVC_PID=$!
}

stop_unit() {
    local user="$1"
    kill -TERM -- "-$SVC_PID" 2>/dev/null || true
    for _ in $(seq 1 90); do
        pgrep -u "$user" >/dev/null 2>&1 || return 0
        sleep 1
    done
    pkill -KILL -u "$user" || true
    sleep 1
    pgrep -u "$user" >/dev/null 2>&1 && fail "processes of $user still running after stop"
    return 0
}

wait_healthy() {
    local log="$1"
    for _ in $(seq 1 180); do
        curl -fsS "$BASE/health" >/dev/null 2>&1 && return 0
        kill -0 "$SVC_PID" 2>/dev/null || { tail -50 "$log" >&2; fail "service exited before /health answered"; }
        sleep 1
    done
    tail -50 "$log" >&2
    fail "/health did not answer within 180 s"
}

# Exits with the service's status; for a version expected to refuse.
wait_exit() {
    local log="$1"
    for _ in $(seq 1 60); do
        kill -0 "$SVC_PID" 2>/dev/null || { wait "$SVC_PID" 2>/dev/null; return $?; }
        sleep 1
    done
    tail -50 "$log" >&2
    fail "service still running after 60 s; it should have refused to start"
}

version_of() { curl -fsS "$BASE/api/version" | sed -n 's/.*"version":"\([^"]*\)".*/\1/p'; }

# --- API helpers (cookie session + double-submit CSRF) ------------------------
JAR="$WORK/cookies"
login() {
    local code
    rm -f "$JAR"
    code=$(curl -sS -o "$WORK/login.out" -w '%{http_code}' -c "$JAR" \
        --data-urlencode "username=$EMAIL" --data-urlencode "password=$PASSWORD" \
        "$BASE/api/auth/jwt/login")
    [ "$code" = 204 ] || [ "$code" = 200 ] || { cat "$WORK/login.out" >&2; fail "login returned $code"; }
}
csrf() { awk -v n="$1_csrf" '$6 == n { print $7 }' "$JAR"; }
api() {  # api <cookie-prefix> <method> <path> [json]
    local prefix="$1" method="$2" path="$3" body="${4:-}"
    local args=(-fsS -b "$JAR" -X "$method" -H "X-CSRF-Token: $(csrf "$prefix")")
    [ -n "$body" ] && args+=(-H 'Content-Type: application/json' --data "$body")
    curl "${args[@]}" "$BASE$path"
}

# =============================================================================
# A tarball install runs the extracted tree's install.sh; its uninstall.sh
# stays in that tree, which is where an operator runs it from.
tarball_dir() { echo "$WORK/$1-$2-linux-amd64"; }
install_tarball() {  # install_tarball <dir> <name> <version>
    tar -xzf "$1/$2-$3-linux-amd64.tar.gz" -C "$WORK"
    ( cd "$(tarball_dir "$2" "$3")" && ./install.sh >/dev/null )
}

step "Install $OLD_NAME $OLD ($FORMAT)"
case "$FORMAT" in
    deb)     apt-get install -y -qq "/old/${OLD_NAME}_${OLD}-1_amd64.deb" >/dev/null ;;
    rpm)     dnf install -y -q "/old/${OLD_NAME}-${OLD}-1.x86_64.rpm" >/dev/null ;;
    tarball) apt-get install -y -qq python3.12 >/dev/null
             install_tarball /old "$OLD_NAME" "$OLD" ;;
esac
OLD_PY="/usr/lib/${OLD_NAME}/venv/bin/python"
key32() { "$OLD_PY" -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())'; }
SECRET=$(key32)
ENCKEY=$(key32)
sed -i \
    -e "s|^url = \"postgresql+asyncpg://.*\"|url = \"postgresql+asyncpg://$PG_USER:$PG_PASS@127.0.0.1:5432/$PG_DB\"|" \
    -e "s|^secret_key = .*|secret_key = \"$SECRET\"|" \
    -e "s|^encryption_key = .*|encryption_key = \"$ENCKEY\"|" \
    "$OLD_CONF"
echo "$MARKER" >> "$OLD_CONF"
grep -q "$PG_DB" "$OLD_CONF" || fail "database url not written to $OLD_CONF"
ok "configured $OLD_CONF"

step "Start $OLD and seed data through its API"
start_unit "$OLD_UNIT" "$WORK/old.log"
wait_healthy "$WORK/old.log"
[ "$(version_of)" = "$OLD" ] || fail "/api/version reports '$(version_of)', expected $OLD"
ok "/api/version reports $OLD"

curl -fsS -H 'Content-Type: application/json' \
    --data "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
    "$BASE/api/auth/register" >/dev/null || fail "registering the first admin failed"
login
api "$OLD_NAME" POST /api/groups \
    '{"name":"upgrade-group","description":"seeded before the upgrade","priority":100}' >/dev/null \
    || fail "creating a group failed"
# An Ed25519 key in OpenSSH format, made with the old venv's cryptography.
"$OLD_PY" - > "$WORK/id_test" <<'EOF'
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
print(Ed25519PrivateKey.generate().private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
    serialization.NoEncryption()).decode(), end="")
EOF
KEY_JSON=$("$OLD_PY" -c 'import json, sys; print(json.dumps({"name": "upgrade-key", "private_key": open(sys.argv[1]).read(), "ssh_user": "root"}))' "$WORK/id_test")
api "$OLD_NAME" POST /api/ssh-keys "$KEY_JSON" >/dev/null || fail "creating an SSH key failed"
ok "seeded: first admin, group 'upgrade-group', SSH key 'upgrade-key'"

OLD_REV=$(psql_db "SELECT version_num FROM alembic_version")
echo "upgrade-marker $NEW" > "$OLD_DATA/upgrade-marker"
chown "$OLD_NAME:$OLD_NAME" "$OLD_DATA/upgrade-marker"
ok "alembic revision before the upgrade: $OLD_REV"

step "Stop $OLD and take the pre-upgrade backup"
stop_unit "$OLD_NAME"
runuser -u postgres -- pg_dump -Fc -f /tmp/pre-upgrade.dump "$PG_DB"
ok "stopped; database dumped"

# =============================================================================
# From here the steps are docs/upgrade.md, "Upgrading to 2.0.0", packages.
step "Install $NEW_NAME $NEW next to $OLD_NAME"
case "$FORMAT" in
    deb)     apt-get install -y -qq "/dist/${NEW_NAME}_${NEW}-1_amd64.deb" >/dev/null ;;
    rpm)     dnf install -y -q "/dist/${NEW_NAME}-${NEW}-1.x86_64.rpm" >/dev/null ;;
    tarball) install_tarball /dist "$NEW_NAME" "$NEW" ;;
esac
ok "installed"

# D-29: 2.0 reads none of the old names. Started before the config is
# copied, it must refuse, and say which old names it found.
start_unit "$NEW_UNIT" "$WORK/refuse.log"
if wait_exit "$WORK/refuse.log"; then fail "$NEW started with only the old config in place"; fi
if ! grep -q "FATAL: unbork cannot start" "$WORK/refuse.log" \
    || ! grep -q "no longer read: .*$OLD_CONF" "$WORK/refuse.log"; then
    cat "$WORK/refuse.log" >&2
    fail "no startup error naming $OLD_CONF"
fi
ok "refuses to start on the old config alone, and names $OLD_CONF"

step "Copy the config and the data"
install -o root -g "$NEW_NAME" -m 0640 "$OLD_CONF" "$NEW_CONF"
sed -i "s/^${OLD_NAME}_server_ip/${NEW_NAME}_server_ip/" "$NEW_CONF"
cp -a "$OLD_DATA/." "$NEW_DATA/"
chown -R "$NEW_NAME:$NEW_NAME" "$NEW_DATA"
ok "copied $OLD_CONF and $OLD_DATA"

step "Remove $OLD_NAME (not purge)"
case "$FORMAT" in
    deb)
        apt-get remove -y -qq "$OLD_NAME" >/dev/null
        dpkg-query -W -f='${db:Status-Abbrev}' "$OLD_NAME" 2>/dev/null | grep -q '^ii' \
            && fail "$OLD_NAME is still installed" ;;
    rpm)
        dnf remove -y -q "$OLD_NAME" >/dev/null
        rpm -q "$OLD_NAME" >/dev/null 2>&1 && fail "$OLD_NAME is still installed" ;;
    tarball)
        ( cd "$(tarball_dir "$OLD_NAME" "$OLD")" && ./uninstall.sh >/dev/null )
        [ -e "/usr/lib/$OLD_NAME" ] && fail "/usr/lib/$OLD_NAME is still there" ;;
esac
[ -f "$OLD_DATA/upgrade-marker" ] || fail "removing $OLD_NAME deleted $OLD_DATA"
ok "$OLD_NAME removed, $OLD_DATA kept"

# --- What the upgrade must have kept -----------------------------------------
step "Check the copied config and data"
tail -1 "$NEW_CONF" | grep -qxF "$MARKER" || fail "$NEW_CONF lost the edited old config (marker line missing)"
grep -q "^${NEW_NAME}_server_ip" "$NEW_CONF" || fail "${NEW_NAME}_server_ip missing from $NEW_CONF"
[ "$(stat -c '%U:%G %a' "$NEW_CONF")" = "root:$NEW_NAME 640" ] || fail "$NEW_CONF is $(stat -c '%U:%G %a' "$NEW_CONF")"
[ "$(stat -c '%U' "$NEW_DATA/upgrade-marker")" = "$NEW_NAME" ] || fail "marker in $NEW_DATA not owned by $NEW_NAME"
ok "config carries the operator's edits; data owned by $NEW_NAME"

step "Start $NEW and check the data"
start_unit "$NEW_UNIT" "$WORK/new.log"
wait_healthy "$WORK/new.log"
[ "$(version_of)" = "$NEW" ] || fail "/api/version reports '$(version_of)', expected $NEW"
ok "/api/version reports $NEW"

HEAD_REV=$(cd "/usr/lib/${NEW_NAME}/backend" && "/usr/lib/${NEW_NAME}/venv/bin/python" -c '
from alembic.config import Config
from alembic.script import ScriptDirectory
print(ScriptDirectory.from_config(Config("alembic.ini")).get_current_head())')
NEW_REV=$(psql_db "SELECT version_num FROM alembic_version")
[ "$NEW_REV" = "$HEAD_REV" ] || fail "alembic revision is $NEW_REV, the candidate's head is $HEAD_REV"
[ "$NEW_REV" != "$OLD_REV" ] || fail "no migration ran (still at $OLD_REV)"
ok "migrated $OLD_REV -> $NEW_REV (head)"

login
api "$NEW_NAME" GET /api/users/me > "$WORK/me.json" || fail "GET /api/users/me failed after the upgrade"
grep -q '"role":"admin"' "$WORK/me.json" || { cat "$WORK/me.json" >&2; fail "the first admin is not role admin"; }
ok "the admin signs in with the old password and has role admin"

api "$NEW_NAME" GET /api/groups | grep -q '"name":"upgrade-group"' || fail "group 'upgrade-group' missing"
api "$NEW_NAME" GET /api/ssh-keys | grep -q '"name":"upgrade-key"' || fail "SSH key 'upgrade-key' missing"
ok "group and SSH key present"

# The encryption key still decrypts what 1.1.0 encrypted.
psql_db "SELECT encode(encrypted_private_key, 'hex') FROM ssh_keys WHERE name = 'upgrade-key'" > "$WORK/key.hex"
chmod 0644 "$WORK/key.hex" "$WORK/id_test"
chmod 0755 "$WORK"
( cd "/usr/lib/${NEW_NAME}/backend" && runuser -u "$NEW_NAME" -- env -i HOME=/ "UNBORK_CONFIG=$NEW_CONF" \
    "/usr/lib/${NEW_NAME}/venv/bin/python" -c '
import sys
from app.crypto.encryption import decrypt_ssh_key
from app.crypto.key_management import get_master_key
blob = bytes.fromhex(open(sys.argv[1]).read().strip())
sys.exit(0 if decrypt_ssh_key(blob, get_master_key()) == open(sys.argv[2]).read() else 1)
' "$WORK/key.hex" "$WORK/id_test" ) || fail "the SSH key does not decrypt to what was stored"
ok "the SSH key decrypts to the key stored under $OLD"

stop_unit "$NEW_NAME"

step "Fresh-install assertions on the upgraded system"
/tests/smoke.sh "$NEW"

# =============================================================================
# docs/upgrade.md, "Rolling back to 1.1.0": restore the backup, put the old
# version back, start it.
step "Roll back to $OLD"
runuser -u postgres -- dropdb "$PG_DB"
runuser -u postgres -- createdb -O "$PG_USER" "$PG_DB"
runuser -u postgres -- pg_restore --no-owner --role="$PG_USER" -d "$PG_DB" /tmp/pre-upgrade.dump
case "$FORMAT" in
    deb)     apt-get install -y -qq "/old/${OLD_NAME}_${OLD}-1_amd64.deb" >/dev/null ;;
    rpm)     dnf install -y -q "/old/${OLD_NAME}-${OLD}-1.x86_64.rpm" >/dev/null
             # Removing the .rpm saved the edited config aside.
             [ -f "$OLD_CONF.rpmsave" ] && mv -f "$OLD_CONF.rpmsave" "$OLD_CONF" ;;
    tarball) ( cd "$(tarball_dir "$OLD_NAME" "$OLD")" && ./install.sh >/dev/null ) ;;
esac
tail -1 "$OLD_CONF" | grep -qxF "$MARKER" || fail "$OLD_CONF is not the operator's config after reinstalling $OLD"
start_unit "$OLD_UNIT" "$WORK/rollback.log"
wait_healthy "$WORK/rollback.log"
[ "$(version_of)" = "$OLD" ] || fail "after the rollback /api/version reports '$(version_of)', expected $OLD"
[ "$(psql_db "SELECT version_num FROM alembic_version")" = "$OLD_REV" ] || fail "alembic revision is not $OLD_REV after the restore"
login
api "$OLD_NAME" GET /api/groups | grep -q '"name":"upgrade-group"' || fail "group missing after the rollback"
stop_unit "$OLD_NAME"
ok "$OLD runs again on the restored database, with its config and data"

echo
echo "UPGRADE $OLD_NAME $OLD -> $NEW_NAME $NEW: PASSED"
