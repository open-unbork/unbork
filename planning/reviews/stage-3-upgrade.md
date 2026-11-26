# Stage 3: upgrade runs from 1.1.x to 2.0.0

Each run followed "Upgrading to 2.0.0: the rename" in `docs/upgrade.md`
as written, by `packaging/tests/upgrade.sh` (C-052), which does the
documented commands one by one. Candidate: built from the stage branch
with `VERSION` 2.0.0 (`packaging/build.sh --clean --version=2.0.0`). Old
artifacts: the `v1.1.0` release assets at first, then, once the merge of
`main` showed 1.1.1 to be the last release before the rename, the
`v1.1.1` assets, each checked against its `SHA256SUMS`. The final run,
after the review fixes, upgraded from 1.1.1 with all four targets in
parallel (`PARALLEL=1`) and passed in about 100 s.

Runs were in the session's own Docker, not on lin-manager-2: the
session has no SSH to lin-manager or lin-manager-2. No managed host was
touched.

| Install type | Where | Upgrade | Result |
|---|---|---|---|
| `.deb` | `ubuntu:24.04` container, PostgreSQL 16, Redis | install `unbork` next to `labdog`, copy config and data, `apt remove labdog` | passed from 1.1.0 and from 1.1.1 |
| tarball | `ubuntu:24.04` container | stop labdog, 2.0.0 `install.sh`, copy, the old `uninstall.sh` without `--purge` | passed from 1.1.0 and from 1.1.1 |
| `.rpm` | `rockylinux:9` | as `.deb` with `dnf` | **not run here**: the session's proxy refuses every Rocky mirror. Runs in CI (`packaging-smoke`, target `upgrade-rpm`) on the PR |
| Docker | lin-manager-2, local Docker, Postgres 18, Redis (2026-11-26, after the review) | 1.1.1 compose with data, image built from the branch, the documented Docker steps, rollback from the backup, upgrade again | passed, with the documentation findings 9 to 13 below |

Checked after each passing run: `/api/version` reports 2.0.0; Alembic
moved from `0055_metrics_destinations` to the head `0062_rename_labdog_names`;
the first admin signs in with the old password and has role `admin`;
the group and the SSH key seeded through the old API are listed; the
key decrypts with the copied encryption key to exactly what the old version stored;
the copied config keeps the operator's edits, `root:unbork 0640`, and no path setting under the old directories (`packs_root_dir` is `/var/lib/unbork/packs`); data
under `/var/lib/unbork` owned by `unbork`; removing the old version kept
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
7. **The copied config kept 1.1.x paths** (fixed in the stage review,
   F-01 in `stage-3.md`): `packs_root_dir = "/var/lib/labdog/packs"` is
   set, not commented, in the shipped `labdog.toml`. Under the unit's
   `ProtectSystem=strict` packs could not sync, and the clean-up step
   would have deleted them. The docs' sed now rewrites the paths, and
   2.0.0 warns at start about any setting still under the old
   directories.
8. **The rollback left `unbork` enabled** (fixed, F-02): a reboot would
   have started 2.0.0 on the restored 1.1.x database and migrated it.

9. **The Docker steps left the mounted config file behind** (fixed
   after the live checks): the 1.1.x compose mounts
   `./labdog.toml:/etc/labdog/labdog.toml`. Followed as written, 2.0.0
   started, warned that the file is not read, and ignored it, so
   `unbork_server_ip` fell back to `127.0.0.1`, which can lock unbork
   out of a host on its next firewall sync. The steps now move the mount
   and rename its key and paths.
10. **Renaming only the prefix of `LABDOG_SECURITY__LABDOG_SERVER_IP`**
    (`old-name-ok`) gives a variable nothing reads, with the same
    lockout risk (found while fixing 9). The docs name the full new
    variable, and the start-up warning now names such a variable.
11. The documented pre-check ran `docker compose run … unbork`; the
    1.1.x compose calls the service `labdog` (docs fixed).
12. The Docker rollback did not say to put back the `.env` with the old
    names, its restore commands assumed a `postgres` OS user, and the
    backup step had no Docker `pg_dump` (docs fixed).
13. The rollback pointed to clean-up commands "below" that are above
    (docs fixed).

Live checks on lin-manager-2 on the upgraded Docker stack, all passed:
the first sync of an iptables target (a throwaway container, since the
"test" VM `tester` was powered off) put the rules in `UNBORK-INPUT` /
`UNBORK-OUTPUT` for IPv4 and IPv6, with the old `LABDOG-*` chains left
unreached; on `tester2` (nftables) `inet filter` was replaced with the new
names; the documented manual clean-up is right and safe to run twice.
Drift was detected, a `*/2` `_builtin.drift_check` schedule fired, a
Grafana alert with `unbork_*` labels started its action, the alert email
(to a local Mailpit) carried only the new names, and `/metrics` (with
`[metrics] enabled`) served 45 series, all `unbork_*`. `tester2`'s
firewall was put back afterwards.

## Not verified, and why

- The `.rpm` upgrade locally: it runs in CI (`upgrade-rpm`), where it
  passed.
- A real iptables VM (the "test" VM was powered off; a container stood
  in), and the email reporting an alert-started action's outcome, which
  had not arrived when the stack was torn down.
- The rollback on `.rpm`. On `.deb` and tarball the written
  rollback ran after each upgrade (restore the dump, reinstall the old
  version) and it started on its config at `0055` with the seeded group.
