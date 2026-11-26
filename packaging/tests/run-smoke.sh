#!/usr/bin/env bash
# Containerised packaging smoke harness for unbork.
#
# For each built artifact in packaging/dist/, spin up a clean target-OS
# container, install the artifact the way an operator would, and run the
# in-container assertions (packaging/tests/smoke.sh):
#
#   .deb     → Ubuntu 24.04   (apt-get install ./unbork_*.deb)
#   .rpm     → Rocky Linux 9  (dnf install ./unbork-*.rpm)
#   tarball  → Ubuntu 24.04   (extract + ./install.sh)
#
# Two more targets, not in the default TARGETS, upgrade an install of the
# released 1.1.0 package (with a database and data) to the candidate by the
# steps in docs/upgrade.md, start it, and check its data survived
# (packaging/tests/upgrade.sh):
#
#   upgrade-deb  → Ubuntu 24.04   (1.1.0 .deb from OLD_DIST, then unbork)
#   upgrade-rpm  → Rocky Linux 9  (1.1.0 .rpm from OLD_DIST, then unbork)
#   upgrade-tarball → Ubuntu 24.04 (1.1.0 tarball from OLD_DIST, then unbork)
#
# Requires Docker and the artifacts already built (packaging/build.sh).
# Run it by hand or from CI. Override the base images or restrict which
# targets run via env vars (see below).
#
# Usage:
#   packaging/tests/run-smoke.sh [VERSION]
#
# Env:
#   DIST        artifact dir           (default: packaging/dist)
#   TARGETS     space-separated subset  (default: "deb rpm tarball")
#   DEB_IMAGE   .deb test image        (default: ubuntu:24.04)
#   RPM_IMAGE   .rpm test image        (default: rockylinux:9)
#   TAR_IMAGE   tarball test image     (default: ubuntu:24.04)
#   OLD_VERSION release upgraded from  (default: 1.1.0)
#   OLD_DIST    dir with its packages  (default: packaging/dist-old)
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
PKG_DIR=$(cd "$HERE/.." && pwd)
REPO_ROOT=$(cd "$PKG_DIR/.." && pwd)

VERSION="${1:-$(tr -d '[:space:]' < "$REPO_ROOT/VERSION")}"
DIST="${DIST:-$PKG_DIR/dist}"
TARGETS="${TARGETS:-deb rpm tarball}"
DEB_IMAGE="${DEB_IMAGE:-ubuntu:24.04}"
RPM_IMAGE="${RPM_IMAGE:-rockylinux:9}"
TAR_IMAGE="${TAR_IMAGE:-ubuntu:24.04}"
OLD_VERSION="${OLD_VERSION:-1.1.0}"
OLD_DIST="${OLD_DIST:-$PKG_DIR/dist-old}"

DEB="unbork_${VERSION}-1_amd64.deb"
RPM="unbork-${VERSION}-1.x86_64.rpm"
TARBALL="unbork-${VERSION}-linux-amd64.tar.gz"

command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found on PATH" >&2; exit 1; }

step() { echo; echo "=======================================================================" ; echo ">>> $*"; echo "======================================================================="; }

# All three installs converge on the same on-disk layout, so one assertion
# script (smoke.sh) is shared; only the install command differs per artifact.
smoke_deb() {
    [ -f "$DIST/$DEB" ] || { echo "ERROR: missing artifact $DIST/$DEB" >&2; return 1; }
    step ".deb → $DEB_IMAGE"
    docker run --rm -v "$DIST":/dist:ro -v "$HERE":/tests:ro -e V="$VERSION" "$DEB_IMAGE" \
        bash -euo pipefail -c '
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -qq
            # apt resolves the python3.12 runtime dependency from the local .deb.
            apt-get install -y -qq "/dist/unbork_${V}-1_amd64.deb"
            /tests/smoke.sh "$V"
        '
}

smoke_rpm() {
    [ -f "$DIST/$RPM" ] || { echo "ERROR: missing artifact $DIST/$RPM" >&2; return 1; }
    step ".rpm → $RPM_IMAGE"
    docker run --rm -v "$DIST":/dist:ro -v "$HERE":/tests:ro -e V="$VERSION" "$RPM_IMAGE" \
        bash -euo pipefail -c '
            # dnf resolves the python3.12 runtime dependency from AppStream.
            dnf install -y -q "/dist/unbork-${V}-1.x86_64.rpm"
            /tests/smoke.sh "$V"
        '
}

smoke_tarball() {
    [ -f "$DIST/$TARBALL" ] || { echo "ERROR: missing artifact $DIST/$TARBALL" >&2; return 1; }
    step "tarball → $TAR_IMAGE (via install.sh)"
    docker run --rm -v "$DIST":/dist:ro -v "$HERE":/tests:ro -e V="$VERSION" "$TAR_IMAGE" \
        bash -euo pipefail -c '
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -qq
            # The tarball bundles the venv but not the python3.12 runtime it
            # links against; an operator installs that themselves.
            apt-get install -y -qq python3.12 tar
            tar -xzf "/dist/unbork-${V}-linux-amd64.tar.gz" -C /tmp
            cd "/tmp/unbork-${V}-linux-amd64"
            ./install.sh
            /tests/smoke.sh "$V"
        '
}

# The upgrade starts from the release before the rename, so its files still
# carry the old name.
OLD_DEB="labdog_${OLD_VERSION}-1_amd64.deb"    # old-name-ok
OLD_RPM="labdog-${OLD_VERSION}-1.x86_64.rpm"   # old-name-ok
OLD_TARBALL="labdog-${OLD_VERSION}-linux-amd64.tar.gz"   # old-name-ok

upgrade_in() {
    local image="$1" old="$2" new="$3" format="${4:-}"
    [ -f "$OLD_DIST/$old" ] || { echo "ERROR: missing old package $OLD_DIST/$old (set OLD_DIST)" >&2; return 1; }
    [ -f "$DIST/$new" ] || { echo "ERROR: missing artifact $DIST/$new" >&2; return 1; }
    step "upgrade $old → $new on $image"
    docker run --rm -v "$OLD_DIST":/old:ro -v "$DIST":/dist:ro -v "$HERE":/tests:ro "$image" \
        /tests/upgrade.sh "$OLD_VERSION" "$VERSION" ${format:+"$format"}
}

upgrade_deb() { upgrade_in "$DEB_IMAGE" "$OLD_DEB" "$DEB"; }
upgrade_rpm() { upgrade_in "$RPM_IMAGE" "$OLD_RPM" "$RPM"; }
upgrade_tarball() { upgrade_in "$TAR_IMAGE" "$OLD_TARBALL" "$TARBALL" tarball; }

echo "unbork packaging smoke — version $VERSION, targets: $TARGETS"
for t in $TARGETS; do
    case "$t" in
        deb)     smoke_deb ;;
        rpm)     smoke_rpm ;;
        tarball) smoke_tarball ;;
        upgrade-deb) upgrade_deb ;;
        upgrade-rpm) upgrade_rpm ;;
        upgrade-tarball) upgrade_tarball ;;
        *) echo "ERROR: unknown target '$t' (expected: deb rpm tarball upgrade-deb upgrade-rpm upgrade-tarball)" >&2; exit 1 ;;
    esac
done

echo
echo "ALL PACKAGING SMOKE TESTS PASSED ($TARGETS)"
