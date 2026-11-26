# Stage 3: upgrade runs from 1.1.0 to 2.0.0

Each run followed "Upgrading to 2.0.0: the rename" in `docs/upgrade.md`
as written, by `packaging/tests/upgrade.sh` (C-052), which does the
documented commands one by one. Candidate: built from the stage branch
with `VERSION` 2.0.0 (`packaging/build.sh --clean --version=2.0.0`). Old
artifacts: the `v1.1.0` release assets, checked against its `SHA256SUMS`.

Runs were in the session's own Docker, not on lin-manager-2: the
session has no SSH to lin-manager or lin-manager-2. No managed host was
touched.

| Install type | Where | Upgrade | Result |
|---|---|---|---|
| `.deb` | `ubuntu:24.04` container, PostgreSQL 16, Redis | install `unbork` next to `labdog`, copy config and data, `apt remove labdog` | passed |
| tarball | `ubuntu:24.04` container | 2.0.0 `install.sh`, copy, 1.1.0 `uninstall.sh` without `--purge` | passed |
| `.rpm` | `rockylinux:9` | as `.deb` with `dnf` | **not run here**: the session's proxy refuses every Rocky mirror. Runs in CI (`packaging-smoke`, target `upgrade-rpm`) on the PR |
| Docker | | | **not run**: Docker Hub answered 429 for `openlabdog/labdog:1.1.0` and `unbork/unbork:dev-latest` all session |

Checked after each passing run: `/api/version` reports 2.0.0; Alembic
moved from `0055_metrics_destinations` to the head `0062_rename_labdog_names`;
the first admin signs in with the 1.1.0 password and has role `admin`;
the group and the SSH key seeded through the 1.1.0 API are listed; the
key decrypts with the copied encryption key to exactly what 1.1.0 stored;
the copied config keeps the operator's edits, `root:unbork 0640`; data
under `/var/lib/unbork` owned by `unbork`; removing 1.1.0 kept
`/var/lib/labdog`; the fresh-install `smoke.sh` holds afterwards. Before
the config is copied, 2.0.0 refuses to start and names
`/etc/labdog/labdog.toml` (D-29).

## Findings

1. **2.0.0 did not start after the documented upgrade** (fixed,
   `9e25b74`). `apt remove labdog` leaves `/etc/labdog` as
   `root:labdog 0750`; the startup check for the old config file called
   `stat()` inside it as `unbork`, got `PermissionError`, and the import
   of `app.config` died. The same traceback also hid the D-29 refusal.
2. **The package steps were in an order that loses the config on
   `.rpm`** (docs fixed). The draft said to remove `labdog` before
   copying `/etc/labdog/labdog.toml`; `rpm -e` renames an edited config
   to `labdog.toml.rpmsave` (checked with `rpm -i --nodeps` and `rpm -e`
   in `rockylinux:9`). The docs now copy first, then remove.
3. **"Move /var/lib/labdog to /var/lib/unbork" did not work as written**
   (docs fixed): the `unbork` package already creates
   `/var/lib/unbork`, so `mv` would nest the old directory inside it. The
   docs now `cp -a /var/lib/labdog/. /var/lib/unbork/`, which also keeps
   the old copy for a rollback.
4. **The rollback steps forgot the `.rpmsave`** (docs fixed): after
   reinstalling the 1.1.0 `.rpm`, its config must be moved back from
   `labdog.toml.rpmsave`.
5. **`next build` refused Next.js 16.4.3** over seven moderate
   advisories, which stopped `packaging/build.sh` and so the release
   (fixed, `262d9b9`: 16.4.6).
6. A local rebuild without `--clean` shipped stale backend code
   (PR-34; CI is not affected).

## Not verified, and why

- The `.rpm` upgrade and the Docker upgrade, as above.
- The first sync of a "test" host on iptables and on nftables after the
  upgrade, a drift check before it, the schedule firing, the alert
  webhook with the new labels and `/metrics`: they need a running stack
  with managed hosts (lin-manager-2 and the "test" hosts), which this
  session cannot reach. Under D-29 the sync does not remove old chains
  or files; the docs list the manual clean-up, which was not run on a
  real host.
- The rollback on `.rpm` and Docker. On `.deb` and tarball the written
  rollback ran after each upgrade (restore the dump, reinstall 1.1.0) and
  1.1.0 started on its config at `0055` with the seeded group.
