# Stage 3 review

- **Base:** `94cf763` (`origin/dev` after #308), with `main` (v1.1.1)
  merged in as `eb73c36`.
- **Head reviewed:** `15dd8eb` (all five items). The fixes are `234365f`
  to `472551b`; planning-only commits are not part of the code review.
- **Size:** 9 code commits; 17 files changed outside `planning/`
  (+926 / -94) by the end of the review.
- **Upgrade runs:** `planning/reviews/stage-3-upgrade.md`.

Three read-only reviews ran in parallel: correctness and security,
performance, readability. Each finding was checked against the code
before it was triaged. The reviewers overlapped on four (merged below:
R-01 with the 1.1.1 wording, R-04/R-05 with C-04/C-05, C-08 with R-09).

## Findings

| ID | Sev | Area | File | Summary | Outcome |
|---|---|---|---|---|---|
| F-01 | high | correctness | `docs/upgrade.md`, `app/config.py` | The copied `labdog.toml` keeps `packs_root_dir = "/var/lib/labdog/packs"` (and log paths): under the unit's `ProtectSystem=strict` packs could not sync, and the later clean-up `rm -rf /var/lib/labdog` would delete them. Nothing warned | fixed in `d5b2aa2` (the sed also rewrites the old paths) and `d23a1fd` (the start-up warning names any setting under the old directories; test); `upgrade.sh` asserts it in `472551b` |
| F-02 | high | correctness | `docs/upgrade.md` | Rollback stopped but did not disable `unbork`, and gave a removal command only for `.deb`: after a reboot 2.0.0 would migrate the restored 1.1.x database | fixed in `d5b2aa2` (`systemctl disable --now unbork`; removal per install type) |
| F-03 | high | readability | `docs/upgrade.md`, CI, test README | Text called 1.1.0 "the last release before the rename"; 1.1.1 is, and its image is `unbork/unbork:1.1.1` | fixed in `d5b2aa2` and `472551b` (steps for 1.1.x; the test upgrades from 1.1.1) |
| F-04 | high | correctness | `docs/upgrade.md` | "Before you start" linked to backup commands that use 2.x paths, which do not exist on 1.x: no rollback backup | fixed in `d5b2aa2` (1.x commands inline) |
| F-05 | medium | correctness | `docs/upgrade.md` | The tarball path never stopped `labdog` before copying its data | fixed in `d5b2aa2` |
| F-06 | medium | readability | `docs/upgrade.md` | Rollback pointed to generic steps with 2.x names and a `dnf downgrade` that cannot cross the rename | fixed in `d5b2aa2` (restore commands inline; the generic section points back) |
| F-07 | medium | correctness | `README.md`, `docs/README.md` | "Everything in this repository is AGPL" is not true of the bundled OFL fonts | fixed in `d5b2aa2` |
| F-08 | medium | readability | `docs/upgrade.md` | The 1.0.0 notes said the old `/api/ai/alerts` paths answer "until 2.0"; 2.0.0 still serves them | fixed in `d5b2aa2` ("until a later major release") |
| F-09 | medium | readability | `docs/upgrade.md` | "Nothing renames old names for you" against the table's migration-renamed pack | fixed in `d5b2aa2` (named as the one exception) |
| F-10 | medium | readability | `CHANGELOG.md` | "The documentation site is published again" overstated C-003 | fixed in `d5b2aa2` |
| F-11 | medium | readability | `CHANGELOG.md` | The Next.js 16.4.6 bump (seven advisories) had no Security entry | fixed in `d5b2aa2` |
| F-12 | medium | performance | `ci.yml`, `run-smoke.sh` | Three upgrade targets in series would add about 9 to 15 minutes, over C-052's ten | fixed in `472551b` (`PARALLEL=1`: four targets took 98 s locally, against about 6 minutes in series) |
| F-13 | medium | performance | `ci.yml`, `upgrade.sh` | No job timeout and unbounded `curl`: a hung service could hold a runner for six hours | fixed in `472551b` (`timeout-minutes: 45`; every curl bounded) |
| F-14 | low | correctness | `ci.yml` | The packaging-smoke path filter missed the start-up code, which is what broke in `9e25b74` | fixed in `472551b` |
| F-15 | low | correctness | `upgrade.sh` | The test performs the copy itself, so it cannot catch wrong docs | narrowed in `472551b` (the test README says the two change together, and F-01 is now asserted); a shared source deferred, it is not worth a generator for six lines |
| F-16 | low | correctness | `docs/upgrade.md` | `userdel labdog` after `apt purge` fails: the purge removed the user | fixed in `d5b2aa2` (rpm and tarball only) |
| F-17 | low | performance | `check-readme-links.mjs` | `--live` had no timeout and fetched a page once per anchor | fixed in `472551b` (15 s timeout, page cache); kept sequential, there are 14 links |
| F-18 | low | performance | `package-lock.json` | The bump, made with an older npm, dropped all ten `libc` fields | fixed in `234365f` |
| F-19 | low | readability | `CHANGELOG.md` | The 1.1.1 entry promises 2.0.0 "renames with fallbacks", against D-29 | rejected: released history; the 2.0.0 section says plainly that there are none |
| F-20 | low | readability | `docs/upgrade.md` | The upgrade-path promise names 1.0.0 although only 1.1.x is tested | rejected: 1.0.x and 1.1.x share the layout the steps act on, and the migration chain runs from any 1.x revision |
| F-21 | low | readability | `run-smoke.sh`, `upgrade.sh` | Wrong target count, header without the tarball, marker naming the new version, dump outside `$WORK`, `[ ] && fail` arms, old file names built in two places | fixed in `472551b` (cross-reference comments for the names) |
| F-22 | low | readability | `check-readme-links.mjs` | `hasId` escaped only `-` and `.` | fixed in `472551b` (one `escapeRe` helper) |

Also found while running the upgrade, before the review (details in
`stage-3-upgrade.md`): 2.0.0 died at start when `/etc/labdog` was closed
to it (`9e25b74`); `next build` refused 16.4.3 (`262d9b9`); a rebuild
without `--clean` ships stale code (PR-34).

| Severity | Total | Fixed | Deferred | Rejected |
|---|---|---|---|---|
| high | 4 | 4 | 0 | 0 |
| medium | 9 | 9 | 0 | 0 |
| low | 9 | 6 | 1 | 2 |

F-15 counts as deferred: narrowed, not closed.

## Before and after

| | Base `94cf763` | Head |
|---|---|---|
| Backend tests (local) | 4,995 passed, 12 skipped, 6 failed | same set, plus the two new config tests passing |
| ruff, ruff format, bandit | clean | clean |
| tsc, ESLint, Vitest | clean, 0 errors, 33 passed | same |
| `next build` | refused (16.4.3 advisories) | builds |
| Packaging smoke, local | `deb`, `tarball` | `deb`, `tarball`, `upgrade-deb`, `upgrade-tarball` passed |

The six local failures are this session's environment, not the code: five
appear only while `frontend/out` exists (the SPA fallback; they pass
without it), one because the suite ran as root. CI is the reference.
`upgrade-rpm` and the `rpm` smoke run only in CI (the session's proxy
refuses every Rocky mirror).

## Verdict

Pass, pending CI on the pull request (above all `packaging-smoke` with
`upgrade-rpm`) and the checks that need a running stack with hosts, which
this session could not reach: the first sync of a "test" host on iptables
and nftables after the upgrade, drift, schedule, alert webhook and
`/metrics`. C-003's live check runs after the release merges.
