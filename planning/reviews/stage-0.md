# Stage 0 review

- **Base:** `de69a7b` (`origin/dev` when the stage started). The branch
  also carries `f518f1e` from #300, so merging it merges #300.
- **Head reviewed:** `320a361` (review fixes included). Planning-only
  commits after it are not part of the code review.
- **Size:** 19 commits; 29 files changed, 24 outside `planning/`
  (+2,480 / -132 outside `planning/`).
- **Also reviewed:** unbork-playbooks#26 (`c19f96d`, the CLA workflow
  copy) and unbork-enterprise#8 (E-001, E-022, E-028), against their own
  specs.

Three read-only reviews ran in parallel: correctness and security,
performance, readability. Each finding was checked against the code
before it was triaged.

## Findings

| ID | Sev | Area | File | Summary | Outcome |
|---|---|---|---|---|---|
| F-01 | medium | security | `.github/workflows/cla.yml` (allowlist) | CLA Assistant falls back to the git author *name* when no commit email is linked to an account, so a fork PR committed as `Claude` or `therealscienta` passes unsigned | deferred as PR-19, decision D-49 |
| F-02 | medium | correctness | `cla.yml` (signing via `issue_comment`) | The action re-runs the latest `pull_request_target` run filtered by branch name only, so signing can re-run another fork's PR from a same-named branch | README fallback (push a commit) in `69ea4ee`; live check in the S-011 fork test |
| F-03 | medium | correctness | `cla.yml` (`PERSONAL_ACCESS_TOKEN`) | Dependabot-triggered runs may not see Actions secrets, which would fail the check on every Dependabot PR | not verified: needs a real Dependabot PR after merge (fix if so: add the token as an org Dependabot secret) |
| F-04 | medium | performance | `backend/pyproject.toml`, `ci.yml` | `concurrency = greenlet` forces coverage's C tracer: 1.8-2.5x the CPU-bound test time | fixed in `b3cafa1` (sysmon core, same totals) |
| F-05 | medium | readability | `CLAUDE.md` | CI and Running Tests sections missed frontend-test, coverage, `:test-<sha>` only and the CLA check | fixed in `673a119` |
| F-06 | medium | readability | `frontend/package.json` | `@testing-library/user-event` unused | fixed in `673a119` |
| F-07 | medium | readability | `cla.yml` | Signing sentence duplicated in `if:` and `custom-pr-sign-comment` | fixed in `320a361` (`contains()` on part of it, comment ties the copies) |
| F-08 | medium | correctness | `cla.yml` (`path-to-document`) | Link to `blob/dev` lets a later edit change what earlier signers were shown | fixed in `320a361` (commit permalink, both repos) |
| F-09 | medium | readability | `frontend/FRONTEND.md` | New state-in-effect rule contradicted by `lib/local-pref.ts` | fixed in `673a119` (names the sanctioned exception) |
| F-10 | low | correctness | `cla.yml` (`if:`) | Exact, case-sensitive match on the signing comment; a trailing full stop starts no run | fixed in `320a361` |
| F-11 | low | correctness | `cla.yml` (concurrency) | Signings in both repos write one file; a near-simultaneous pair gets a 409 | fixed in `69ea4ee` (README: post `recheck`) |
| F-12 | low | correctness | `cla.yml` (triggers) | `closed` trigger re-runs the check on closed PRs, useful only for locking (off) | fixed in `320a361` (dropped) |
| F-13 | low | security | `cla.yml` (permissions) | `statuses: write` unused by the pinned action | fixed in `320a361` (dropped) |
| F-14 | low | correctness | Docker Hub `:test` | Old floating tag stays frozen on a PR image | rejected: C-024 keeps the tag on purpose (its digest is the acceptance check); removal is D-32's business |
| F-15 | low | correctness | `tests/test_permissions.py` | Four tests fail when `frontend/out/` exists (unguarded SPA catch-all); CI unaffected | deferred as PR-20 |
| F-16 | low | readability | `ci.yml` | `frontend-test` sat in the LINT block | fixed in `673a119` |
| F-17 | low | readability | `ci.yml` | build-test-image summary comment did not match its `if:` | fixed in `673a119` |
| F-18 | low | correctness | `ci.yml` (coverage summary) | `if: always()` step failed again when pytest never ran | fixed in `b3cafa1` (prints "not measured") |
| F-19 | low | readability | `frontend/vitest.config.mts` | `exclude` redundant with `include` | rejected: C-061's spec asks for the explicit exclude so the e2e separation is stated in config |
| F-20 | low | readability | `legal/cla/v1/*.md` | Licence/license spelling mixed | fixed in `69ea4ee` (one note per text) |
| F-21 | low | readability | `legal/cla/v1/CCLA.md` | "Point of Contact" capitalised two ways | fixed in `69ea4ee` |
| F-22 | low | readability | CLA texts vs README | Texts cover the whole org; only two repos check | fixed in `69ea4ee` |
| F-23 | low | readability | `legal/cla/README.md`, `cla.yml` | No CCLA contact route; CCLA allowlist mechanics undocumented | allowlist note fixed in `320a361`; contact address is an open question under D-49 |
| F-24 | low | readability | `CONTRIBUTING.md`, CLA README | "covers PRs in unbork-playbooks" is false until #26 merges | rejected: handled by merge order (#26 merges with #301) |
| F-25 | low | performance | `ci.yml` | `frontend-test` is a fifth `npm ci` runner for ~2 s of tests | rejected: C-061's spec asks for a job, so it can be a required check |
| F-26 | low | performance | `frontend/vitest.config.mts` | Every file paid for jsdom | fixed in `673a119` (node default, jsdom docblock) |
| F-27 | low | performance | `ci.yml` | Full per-file coverage table lengthens the log | fixed in `b3cafa1` (`term:skip-covered`) |

Found in the same pass but outside the reviewers' scope: two literal OIDC
client secrets in unbork-enterprise `test-services/identity/unbork-realm.json`,
committed before this stage, now caught by E-022's gitleaks job. Not
allowlisted; reported to Dennis with an offer to rotate and re-export.

## Counts

| Severity | Found | Fixed | Deferred | Rejected | Not verified |
|---|---|---|---|---|---|
| critical | 0 | 0 | 0 | 0 | 0 |
| high | 0 | 0 | 0 | 0 | 0 |
| medium | 9 | 7 | 1 | 0 | 1 |
| low | 18 | 13 | 1 | 4 | 0 |

F-02 counts as fixed (the documented fallback); its live behaviour is
checked in the fork test.

## Numbers before and after

Local runs in this container (PostgreSQL and Redis started locally, no
Docker daemon); CI on `320a361` alongside.

| Check | Before (`de69a7b`) | After (`320a361`) |
|---|---|---|
| Backend tests, local | 4,722 passed, 2 failed, 12 skipped | 4,722 passed, 2 failed, 12 skipped |
| Backend tests, CI | n/a | 4,822 passed, 1 skipped |
| Backend coverage | 76% (planning session, C tracer, `--cov=app`) | 82% local, **83% CI** (30,486 statements) |
| ruff check / format | clean / 715 formatted | clean / 715 formatted |
| bandit vs baseline | nothing new | nothing new |
| tsc | clean | clean |
| Frontend lint | 0 errors, 11 warnings | 0 errors, 10 warnings |
| Frontend unit tests | none | 31 passed (3 files) |
| Frontend E2E, CI | did not run on 26.04 | passed (Playwright 1.65.1) |

The two local failures are the container (root ignores directory
permissions; no `US/Eastern` in tzdata) and pass in CI. The coverage rise
is mostly measurement: the 76% baseline used the default tracer, which
misses lines run inside greenlets; sysmon sees them. No test was removed.

## Not verified, and why

- **F-03 Dependabot and the CLA check:** needs a Dependabot PR after
  `cla.yml` is on `dev`.
- **CLA fork test and co-author test (S-011 steps 7 and 8):**
  `pull_request_target` runs the base branch's workflow, so neither can
  run before merge. The fork test needs a second GitHub account.
- **Performance against a load-test baseline:** no baseline existed
  before this stage (C-063 creates it), and nothing in this stage touches
  a `dev/loadtest/` path. Not run.
- **Playwright against a local stack on lin-manager-2:** the only page
  change is CronInput, covered by unit tests and CI's frontend-e2e.

## Verdict

**Pass.** No critical or high finding is open. Open mediums: F-01
(PR-19, decision D-49) and F-03 (to check live).

# Stage 0 report

## Items

| Item | Class | Status | Commits | Open |
|---|---|---|---|---|
| C-064 planning/ named exception | A | done | `f31b2c0` | whether `main` carries `planning/` (D-32) |
| S-012 Copyright line in LICENSE | A | done | `59904e2` | GitHub licence detection, after merge |
| S-011 CLA and CLA check | B | review | `cf4811a`, `69ea4ee`, `320a361`; unbork-playbooks#26 | Dennis reviews the texts; co-author and fork tests after merge (second account); mark `CLAAssistant` required; D-49 |
| C-024 Stop moving `:test` | A | done | `59a11e8` | none |
| C-025 26.04 runners, compiler lint rules | A | done | `cb2761d`..`a61cfed`, `9038476` | none |
| C-062 Coverage in CI | A | done | `04bc864`, `b3cafa1` | none |
| C-061 Frontend unit harness | A | done | `87278f6`, `673a119` | PR-18 (Vitest 5) |
| C-063 Load-test baseline | A | done | `290c7d1` (harness fix) | run on `tester`, not lin-manager-2 (too small); PR-21 from its finding |

Enterprise half (unbork-enterprise#8): E-001 licence placeholder
(review, class B), E-022 CI parity (done; gitleaks red on two committed
test-realm secrets until rotated), E-028 no-Ansible-imports check (done).
E-021 skipped by Dennis.

## Exit criteria

| Criterion | Result |
|---|---|
| Backend tests, ruff, bandit | met in CI (4,822 passed); locally the same 2 environmental failures as the baseline |
| Frontend tsc, lint, unit tests | met: clean; 0 errors, 10 warnings (11 before); 31 passed |
| docs/ and CHANGELOG `[Unreleased]` | met: CHANGELOG has the `:test`, CLA and LICENSE lines; no user-visible `docs/` change; no BUGS.md entry fixed |
| roadmap.yaml and PROGRESS.md current | met |
| Review done, no critical or high open | met (this file) |
| Fork PR fails the CLA check until signed | not verified: runs only once `cla.yml` is on `dev`; needs a second account |
| LICENSE names Dennis Tyresson | met |
| A PR no longer moves `:test` | met: the stage PR's run pushed only `:test-d214e8b` |
| CI prints coverage; PLAN.md records it | met: 83%, recorded |
| `npm test` runs a real test, CI runs it | met: `frontend-test` green |
| `baseline-loadtest.md` holds an S2 300 run | met, on `tester` instead of lin-manager-2 (7.75 GB RAM, 3 GB disk), as Dennis directed: `done rc=0 ... ooms=0` |
| planning/ vs plans/ in docs, template, .dockerignore | met |

## Before and after

See "Numbers before and after" above: tests unchanged, coverage 76% to
83% (measurement change), frontend lint warnings 11 to 10, frontend unit
tests 0 to 31.

## Blocked and waiting

- S-011 and E-001: Dennis's review of the CLA texts and the Enterprise
  licence placeholder.
- unbork-enterprise#8 gitleaks: rotating the two test-realm client
  secrets (offered, waiting for Dennis).
