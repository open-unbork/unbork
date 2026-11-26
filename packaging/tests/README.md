# Packaging smoke harness

Containerised install-path smoke tests for unbork's release artifacts.
They turn the smoke procedure that used to be run by hand before a release
into something CI runs automatically (and that you can run locally).

## What it does

For each artifact built into `packaging/dist/`, a clean target-OS container
installs it the way an operator would, then runs shared assertions:

| Artifact | Image | Install |
|----------|-------|---------|
| `.deb` | `ubuntu:24.04` | `apt-get install ./unbork_*.deb` |
| `.rpm` | `rockylinux:9` | `dnf install ./unbork-*.rpm` |
| tarball | `ubuntu:24.04` | extract + `./install.sh` |

The assertions ([`smoke.sh`](smoke.sh)) check that the install:

- landed the app code + a working venv and no in-package action pack,
  the systemd unit, tmpfiles.d config, and `/etc/unbork/unbork.toml`;
- created the `unbork` service account and data directories;
- produced a venv that imports the `app` package and every key runtime
  dependency (cross-distro — the venv is built on Ubuntu and must also run
  on Rocky);
- reports the **correct version** via `importlib.metadata` (guards the
  regression where packaged installs reported `0.0.0`).

These three do **not** start the service. The upgrade targets below do.

## Upgrade from 1.1.x

Three more targets take an install of the last release before the rename
(1.1.1, the same application as 1.1.0; `OLD_VERSION` picks another)
through the steps of "Upgrading to 2.0.0" in
[`docs/upgrade.md`](../../docs/upgrade.md), with real data
([`upgrade.sh`](upgrade.sh)):

| Target | Image | From | To |
|--------|-------|------|----|
| `upgrade-deb` | `ubuntu:24.04` | the 1.1.1 `.deb` | `unbork_*.deb` |
| `upgrade-rpm` | `rockylinux:9` | the 1.1.1 `.rpm` | `unbork-*.rpm` |
| `upgrade-tarball` | `ubuntu:24.04` | the 1.1.1 tarball | `unbork-*.tar.gz` |

In the container it installs PostgreSQL (16; on Rocky from the
`postgresql:16` module stream, since the default 13 is below the 14 the
package asks for) and Redis from the distribution, installs 1.1.1,
configures it and starts it. There is no systemd in the container, so each
version is started from what its own unit file says (`User=`,
`WorkingDirectory=`, `Environment=`, `ExecStart=`, and the `/run`
directory from its tmpfiles.d file), without the unit's sandboxing. Then:

- **Seeds** through the old API: the first admin, a group and an SSH key
  (an encrypted secret); writes a marker line into the config and a marker
  file into the data directory, and records the Alembic revision.
- **Upgrades** as the docs say: installs `unbork` next to it, copies the
  config and the data, removes (not purges) the old package, or for the
  tarball runs the old tree's `uninstall.sh` without `--purge`.
- **Asserts** that the new version refuses to start on the old config
  alone and names the old file; the copied config carries the marker
  line, its owner and mode, and no path setting under the old
  directories (the new version must not warn about one); removing the old package kept its data; the
  new version starts, reports the candidate version and is at the
  candidate's Alembic head; the admin signs in with the old password and
  has role `admin`; the group and the key are there; the key decrypts to
  exactly what the old version stored; and the fresh-install `smoke.sh` holds.
- **Rolls back** as the docs say: restores the database dump taken
  before the upgrade, reinstalls the old version and checks it starts on the
  operator's config at its old Alembic revision with the group in place.

The old packages are not in the repository. Fetch them from the `v1.1.1`
release into `packaging/dist-old/` (or point `OLD_DIST` elsewhere):

```bash
mkdir -p packaging/dist-old && cd packaging/dist-old
gh release download v1.1.1 --repo open-unbork/unbork \
  --pattern '*_1.1.1-1_amd64.deb' --pattern '*-1.1.1-1.x86_64.rpm' \
  --pattern '*-1.1.1-linux-amd64.tar.gz' --pattern SHA256SUMS
sha256sum -c --ignore-missing SHA256SUMS && cd -

TARGETS="upgrade-deb upgrade-rpm upgrade-tarball" ./packaging/tests/run-smoke.sh
```

Each upgrade target takes three to five minutes on its own, most of it
package installs. `PARALLEL=1` runs the targets at the same time, as CI
does; four targets took under two minutes that way locally.

## Run it locally

```bash
# build the artifacts first (needs python3.12, node, nfpm)
./packaging/build.sh --version="$(cat VERSION)"

# then smoke them (needs Docker)
./packaging/tests/run-smoke.sh
```

Restrict targets or override images with env vars:

```bash
TARGETS="deb tarball" ./packaging/tests/run-smoke.sh
RPM_IMAGE=almalinux:9 ./packaging/tests/run-smoke.sh 0.6.1
```

## In CI

The `packaging-smoke` job in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)
builds the artifacts, downloads and checksums the 1.1.1 artifacts, and runs
all six targets on pushes to `main` (release commits) and on pull requests
that touch packaging, `backend/alembic/` or the startup code
(`backend/app/config.py`, `backend/app/__main__.py`), so a broken install path or a
migration that breaks an upgrade fails a check instead of a customer's
install.

The upgrade steps in `upgrade.sh` are a copy of the ones in
`docs/upgrade.md`; change the two together.
