#!/usr/bin/env bash
# In-container smoke assertions for an installed unbork package.
#
# Run this *inside* a target-OS container after installing one of the
# artifacts (.deb / .rpm / tarball-via-install.sh). It asserts the install
# landed the right files, created the service account, produced a working
# venv, and reports the expected version. It deliberately does NOT start the
# service (that needs PostgreSQL + Redis + secrets) — this is an install-path
# smoke, the same procedure that was run by hand for earlier releases.
#
# Usage: smoke.sh <expected-version>
set -euo pipefail

EXPECTED="${1:?expected version required (e.g. smoke.sh 0.6.1)}"

PREFIX=/usr/lib/unbork
VENV_PY="$PREFIX/venv/bin/python"
BACKEND="$PREFIX/backend"
UNIT=/usr/lib/systemd/system/unbork.service

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok  - $*"; }

echo "== unbork install smoke (expected version: $EXPECTED) =="

# 1. Application code -------------------------------------------------------
[ -x "$VENV_PY" ]            || fail "venv python missing/not executable: $VENV_PY"
[ -d "$BACKEND/app" ]        || fail "backend source missing: $BACKEND/app"
[ -f "$BACKEND/alembic.ini" ] || fail "alembic.ini missing: $BACKEND/alembic.ini"
ok "application code + venv present"

# 2. No action pack in the package: packs are synced from git at runtime ---
[ ! -e "$BACKEND/app/ansible" ] || fail "package ships an action pack at $BACKEND/app/ansible"
ok "no action pack shipped in the package"

# 3. System-integration files ----------------------------------------------
[ -f "$UNIT" ] || fail "systemd unit missing: $UNIT"
grep -q 'ExecStart=/usr/lib/unbork/venv/bin/python -m app' "$UNIT" \
    || fail "systemd unit ExecStart is not the expected 'python -m app' entrypoint"
[ -f /usr/lib/tmpfiles.d/unbork.conf ] || fail "tmpfiles.d config missing"
[ -f /etc/unbork/unbork.toml ]         || fail "config file missing: /etc/unbork/unbork.toml"
ok "systemd unit + tmpfiles.d + config present"

# 3b. Config permissions ----------------------------------------------------
#     unbork.toml holds security.secret_key and security.encryption_key, so
#     both the file and the directory above it matter. nfpm declares 0750 for
#     the directory and 0640 for the file; install.sh has to match, and did
#     not — it created /etc/unbork with the invoking root's umask, typically
#     0755, so the tarball install was more permissive than the .deb/.rpm of
#     the same version. Asserted here for every target so the three installs
#     cannot drift apart again.
CONF_DIR_MODE=$(stat -c '%a' /etc/unbork)
[ "$CONF_DIR_MODE" = "750" ] \
    || fail "/etc/unbork is mode $CONF_DIR_MODE, expected 750 (holds secret_key)"
CONF_MODE=$(stat -c '%a' /etc/unbork/unbork.toml)
[ "$CONF_MODE" = "640" ] \
    || fail "/etc/unbork/unbork.toml is mode $CONF_MODE, expected 640"
CONF_OWNER=$(stat -c '%U:%G' /etc/unbork/unbork.toml)
[ "$CONF_OWNER" = "root:unbork" ] \
    || fail "/etc/unbork/unbork.toml is owned by $CONF_OWNER, expected root:unbork"
ok "config dir 0750 + file 0640 root:unbork"

# 4. Owned directories + service account -----------------------------------
[ -d /var/lib/unbork ] || fail "/var/lib/unbork missing"
[ -d /var/log/unbork ] || fail "/var/log/unbork missing"
id unbork >/dev/null 2>&1            || fail "unbork system user not created"
getent group unbork >/dev/null 2>&1 || fail "unbork group not created"
ok "data dirs + unbork service account present"

# 5. The venv imports the app package + key runtime deps -------------------
#    cwd = backend so `app` resolves the installed source tree, matching
#    how the systemd unit runs it.
( cd "$BACKEND" && "$VENV_PY" - <<'PY'
import importlib
mods = [
    "app", "app.api.version",
    "fastapi", "starlette", "uvicorn",
    "sqlalchemy", "asyncpg", "alembic",
    "celery", "ansible_runner", "asyncssh", "cryptography",
]
for m in mods:
    importlib.import_module(m)
print("  (imported: %d modules)" % len(mods))
PY
) || fail "venv could not import the app package + runtime dependencies"
ok "venv imports app + runtime deps"

# 6. Reported version matches the release ----------------------------------
#    Guards the dist-metadata regression where packaged installs reported
#    0.0.0 because the wheel was built without the VERSION file.
GOT="$("$VENV_PY" -c 'import importlib.metadata as m; print(m.version("unbork-backend"))')" \
    || fail "could not read unbork-backend distribution metadata"
[ "$GOT" = "$EXPECTED" ] || fail "version mismatch: metadata reports '$GOT', expected '$EXPECTED'"
ok "unbork-backend distribution reports $GOT"

echo "== SMOKE PASS =="
