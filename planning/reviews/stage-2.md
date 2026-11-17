# Stage 2 review

- **Base:** `2e961d7` (`origin/dev` after #306; its code is the same as
  the stage 1 head `77c0e00`).
- **Head reviewed:** `27499b4` (review, CI and live-check fixes included). Planning-only
  commits after it are not part of the code review.
- **Size:** 27 code commits; 431 files changed outside `planning/`
  (+6,743 / -3,034), most of it the rename.
- **Also reviewed:** unbork-enterprise stage 2 (E-032): its suite and checks
  against this branch, report in that repository's
  `planning/reviews/stage-2.md`.

Three read-only reviews ran in parallel: correctness and security,
performance, readability. Each finding was checked against the code before
it was triaged; the reviewers overlapped on four (merged below). Four more
findings came from CI on the pull request (F-24 to F-28) and two from the
live checks on lin-manager-2 (L-01, L-02); the live checks also hit F-25.

## Findings

| ID | Sev | Area | File | Summary | Outcome |
|---|---|---|---|---|---|
| F-01 | high | performance | `app/ai/claude_code.py` | `ensure_installed` ignored a failed install: every session, alert investigation and AI verify step downloaded again, waiting on the flock and holding a `work` slot | fixed in `40bc80e` (a failure younger than 15 min is raised at once; a session never waits on another download; tests) |
| F-02 | high | correctness | `app/api/ai.py`, `docs/scaling-out.md` | Split deployment: the API reads its own `claude-code` directory while the worker installs, so Test said "Installing" forever and the page polled | fixed in `5d37102` (the shared volume is documented as required, in scaling-out and upgrade) and `aea3a20` (polling capped) |
| F-03 | high | readability | `settings/client-page.tsx` | With the assistant off, Settings still had its AI tab and "AI providers" card, which the docs say are gone | fixed in `4f426e0` |
| F-04 | medium | correctness | `app/tasks/host_lock.py` | With the assistant off, a rollback left `running` held its host for ever (nothing releases it) | fixed in `a722d09` (the scan runs only with the assistant; test); AI snapshot pruning deferred as PR-32 |
| F-05 | medium | security | `app/api/ai.py` | Changing a provider's type to a Claude type kept a stored key and sent it as a sign-in token, around S-014 | fixed in `d619d5f` (422 unless the key is cleared; tests) |
| F-06 | medium | correctness | `claude_cli.py`, `agent_sdk/environment.py` | Pinning `CLAUDE_CONFIG_DIR` dropped a sign-in made in the service user's home; the spec's mitigation and the upgrade note were missing | fixed in `5d37102` (home sign-in kept while the state dir has none; `docs/upgrade.md`; tests) |
| F-07 | medium | performance | `app/api/ai.py`, `claude_code.py` | Each extra queued install held a `long_running` slot on the flock for the whole download | fixed in `40bc80e` (the task takes the lock without waiting and returns `installing`) |
| F-08 | medium | performance | `ai-providers/client-page.tsx` | The providers page polled every 5 s for ever for a disabled provider | fixed in `aea3a20` (polls while installing, or for an enabled missing one, at most 24 times) |
| F-09 | medium | readability | `agent_sdk/probe.py` | Test named the version the SDK expects, not the program that ran | fixed in `5e4af02` (`program_version`) |
| F-10 | medium | readability | `claude_cli.py`, four docs | Old provider labels and the "bundled binary" claim were still in errors, docstrings and docs | fixed in `5e4af02` |
| F-11 | low | performance | `app/tasks/ai_task.py` | The one session that does download blocks its event loop | narrowed by `40bc80e`; the rest deferred as PR-31 |
| F-12 | low | correctness | `app/config.py` | Old names were listed only when startup failed for another reason; the old config file was missed beside a new one; old TOML keys passed silently | fixed in `6c08f56` (warning at every start, old keys named; tests) |
| F-13 | low | correctness | `app/api/ai.py` | A provider of a refused type could not even be disabled | fixed in `d619d5f` (turning `enabled` / `is_default` off is allowed; test) |
| F-14 | low | correctness | `claude_code.py` | A killed install left a partial wheel in `.install-*` | fixed in `40bc80e` (removed under the lock; test) |
| F-15 | low | performance | `app/api/ai.py` | `status()` per provider row; `send_task` blocks in async handlers | deferred as PR-33 |
| F-16 | low | performance | `Dockerfile` | An edit to the pin script reinstalls every Python dependency | deferred as PR-33 |
| F-17 | low | readability | `runner.py`, `claude_cli.py`, `probe.py` | Program-path lookup written four times | deferred as PR-33 |
| F-18 | low | readability | `beat_registry.py` | `AI_ENTRY_NAMES` copied by hand; its test true by construction | deferred as PR-33 |
| F-19 | low | readability | `schemas.py`, `claude_code.py` | Unused parameter, unused return value, a repeated `Literal` | fixed in `521ecaa` and `40bc80e`; the `Literal` in PR-33 |
| F-20 | low | readability | several | `uses_subscription()` also means "needs the program"; `AIDisabledError` for a missing program | deferred as PR-33 |
| F-21 | low | readability | `docs/backup-restore.md` | Token-era wording for the `claude-cli` directory | fixed in `5e4af02` |
| F-22 | low | readability | commit `9828765` | Message claims an alias that does not exist | rejected: `app.tasks.ai_alerts.poll_alertmanager` is that alias |
| F-23 | low | readability | frontend | Docs URL hard-coded (an existing habit) | deferred as PR-33 |
| F-24 | low | correctness | `docs/upgrade.md` | An HTML comment broke the docs build (MDX) | fixed in `1b38ad5` |
| F-25 | medium | correctness | `third_party_notices.py` | The notices script raised `IndexError` when copied out of the tree in the image build | fixed in `0b59a6b` |
| F-26 | low | correctness | `packaging/` | The smoke containers drop `/usr/share/doc`, so the packages' notices were not found | fixed in `0b59a6b` (also installed under `/usr/lib/unbork`; smoke checks there) |
| F-27 | low | correctness | `frontend/e2e/groups.spec.ts` | The CA editor test expected the new CN from a certificate with the old one | fixed in `c3fca08` (new certificate, key discarded) |
| F-28 | low | correctness | `.github/workflows/ci.yml` | `image-has-no-claude-code` failed silently: the image now runs as unbork, `find` could not read some directories and `set -e` ended the step | fixed in `27499b4` (searched as root) |
| L-01 | medium | correctness | `app/main.py` | With the assistant left out, `GET /api/ai/providers` (and any unknown `/api/` path) answered 200 with the page shell: the SPA catch-all served every unmatched GET | fixed in `af36f98` (JSON 404 for `/api/`; tests over a small export) |
| L-02 | low | readability | `app/tasks/__init__.py` | With the assistant off, every beat start logged an ERROR for the four assistant entries it had deleted | fixed in `241944e` (test) |

| Severity | Total | Fixed | Deferred | Rejected |
|---|---|---|---|---|
| high | 3 | 3 | 0 | 0 |
| medium | 9 | 9 | 0 | 0 |
| low | 18 | 10 | 7 | 1 |

F-04 counts as fixed; its second part (snapshot pruning) is PR-32. F-11
counts as deferred; `40bc80e` removed the waiting and the retries.

## Numbers before and after

Local runs in this container (PostgreSQL started locally); CI on
`241944e` alongside (`27499b4` changes only a CI step). The base's code is
the stage 1 head, so its numbers are stage 1's.

| Check | Before (`2e961d7`) | After |
|---|---|---|
| Backend tests, CI | 4,968 passed, 1 skipped | 5,062 passed, 1 skipped |
| Backend tests without the assistant, CI | (job is new) | passed (AI tests skip) |
| Backend coverage, CI | 84% (31,203 statements) | **84%** (31,673 statements) |
| Backend tests, local, assistant on | 4,875 passed, 2 failed | 5,041 passed, 5 failed, 8 skipped |
| Backend tests, local, assistant off | (not run) | 3,162 passed, 1 failed, 1,891 skipped |
| ruff check / format | clean / 735 formatted | clean / 750 formatted |
| bandit vs baseline | nothing new | nothing new |
| tsc | clean | clean |
| Frontend lint | 0 errors, 10 warnings | 0 errors, 10 warnings |
| Frontend unit tests | 31 passed | 33 passed |
| CI (all jobs) | green | green, including frontend-e2e and the new image jobs |

Every local failure is environmental and passes in CI: the three Agent
SDK bridge tests (the SDK version in this container), a permission test
that root ignores, and `test_cron_walk` (no `US/Eastern` in tzdata). Local
coverage reads 78% because those modules' tests are skipped or fail
here; CI is the measure.

## Live checks (local Docker on lin-manager-2)

Branch `7b2e355` (the code under review before the review fixes), compose
project `stage2` on 127.0.0.1 with throwaway keys, no real hosts. Both
images (`unbork:stage2` 565 MB, `unbork:stage2-noai` 559 MB, against
961 MB for `dev-latest`) were built from it, with the one-line notices fix
F-25 applied in the scratch clone (the build stopped there as committed).

- **S-009:** no `claude` file anywhere in either image, no
  `claude_agent_sdk/_bundled`, `/var/lib/unbork/claude-code` owned by
  unbork. Creating a Claude agent provider installed the program on a
  worker in 2.9 s (`claude --version` runs); behind a dead proxy, Test
  answered "Installing…", then the stored "Provider unavailable: could not
  download the Claude Code program from files.pythonhosted.org …" with no
  traceback; removing the proxy, Test installed it again.
- **S-010:** LICENSE, SOURCE-OFFER.md and THIRD-PARTY-NOTICES (1.57 MB:
  91 Python, 175 frontend, 123 Debian packages, licence texts) present,
  including ansible-core, fastapi, git, openssh-client, tini, next and
  react; OCI labels `licenses`, `source`, `revision` (the commit) and
  `documentation` set.
- **C-026:** with the noai image and with `UNBORK_ASSISTANT__ENABLED=false`:
  `features.assistant` false, no `ai.*` settings or permissions, no AI
  task modules, no assistant pages or Agent SDK in the noai image. Found
  L-01 and L-02.
- **C-048 / C-049:** every process runs as uid 1000 (unbork); a
  `LABDOG_SECURITY__SECRET_KEY` variable stops start-up with the message
  naming it and pointing to `docs/upgrade.md`.
- Torn down with `docker compose -p stage2 down -v`. The two stage images
  and `dev-latest` are left on lin-manager-2 (no prune).

The fixes for F-25, L-01 and L-02 were not run live again: CI's
`image-without-assistant` and `packaging-smoke` jobs build the image and
the packages from `241944e` and check the notices, and L-01 and L-02
have unit tests.

## Verdict

**Pass.** No critical or high finding is open. S-010 stays blocked on
D-31 (wording, not code).

# Stage 2 report

## Items

| Item | Class | Status | Commits | Open |
|---|---|---|---|---|
| C-048 Rename: configuration and environment | B | review | `babde9b`, `6c08f56` | Dennis's review; no fallbacks (D-29) |
| C-049 Rename: paths, packages and service names | B | review | `e8e12f2` | Dennis's review |
| C-050 Rename: on-host markers, alert labels, metrics, pack variables | B | review | `e97ec3c` | Dennis's review; hosts cleaned by hand (`docs/upgrade.md`) |
| C-051 Rename: README, docs site, CLAUDE.md, changelog | A | done | `a91d627`, `1b38ad5` | none |
| S-008 Rename the Claude provider labels | A | done | `68f9385`, `5e4af02` | none |
| S-014 Remove the subscription token field | B | review | `31884e2`, `d619d5f`, `5d37102` | Dennis's review |
| S-017 Extension hook: restrict AI provider types (1.7) | B | review | `fdb8ed5`, `d619d5f` | Dennis's review |
| S-009 Stop bundling the Claude Code binary | B | review | `70a787b`, `40bc80e`, `aea3a20`, `5d37102` | Dennis's review; PR-31 |
| C-026 Make the assistant removable | B | review | `9828765`, `4f426e0`, `a722d09`, `af36f98`, `241944e` | Dennis's review; PR-32 |
| S-010 Third-party notices and a source offer | B | blocked | `24ae507`, `0b59a6b` | D-31: contact address and wording (A-07 stands in) |

Enterprise: E-032 done (unbork-enterprise #13, `a82e827`); its review
passed.

## Exit criteria

| Criterion | Result |
|---|---|
| Backend tests, ruff, bandit | met in CI (5,062 passed, 84%; the suite also passes without the assistant); locally 5,041 passed with the assistant, 3,162 passed and 1,891 skipped without it, and only the known environmental failures (Agent SDK bridge, a root permission test, tzdata); ruff clean, 750 formatted; bandit nothing new |
| Frontend tsc, lint, unit tests | met: clean; 0 errors, 10 warnings (unchanged); 33 passed |
| docs/, CHANGELOG `[Unreleased]`, BUGS.md | met: `docs/upgrade.md` maps every old name; Changed, Added, Removed, Security and Fixed entries; no BUGS.md entry belonged to this stage |
| roadmap.yaml and PROGRESS.md current | met |
| Review done, no critical or high open | met (this file) |
| `UNBORK_*` and `/etc/unbork/unbork.toml` work; old names not read and listed | met: tests; live start with an old variable stops and names it; old names also logged at every start (F-12) |
| Image has no Claude Code binary; turning the provider on installs it; a failed install reads "provider unavailable" | met: CI job `image-has-no-claude-code`, live check |
| A build or install without the assistant has no AI routes, pages or providers; the suite passes that way | met: CI `backend-test-no-assistant` and `image-without-assistant`; live check (after L-01) |
| No provider label says "Claude Code"; no token field | met: tests, F-10 for the remaining text |
| Image carries third-party notices and a source offer | met in code and live; the offer's wording waits for D-31 |
| A test extension can refuse provider types; API and UI stop offering them | met: tests (`test_provider_type_policy.py`) |

## Numbers

See "Numbers before and after" above.

## Assumptions made without asking

A-05 to A-08 in `planning/DECISIONS.md`: the Alertmanager poller moved to
`app.tasks.alert_poll` with an alias under the old task name; `ai.*`
setting definitions stay and are filtered where served; until D-31, the
source offer names the issue tracker; the package notices list Python and
npm components only.

## Blocked and waiting

- S-010 on D-31 (Dennis supplies the contact address and signs off the
  wording).
- Dennis's review of the class B items before merge.
- unbork-enterprise #13 needs this PR on `dev` before its CI can pass.
