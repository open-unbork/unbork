# Stage 1 review

- **Base:** `6017631` (`origin/dev` when the stage started).
- **Head reviewed:** `77c0e00` (review fixes included). Planning-only
  commits after it are not part of the code review.
- **Size:** 21 commits; 62 files changed outside `planning/`
  (+4,988 / -159).
- **Also reviewed:** unbork-enterprise stage 1 (no Enterprise items):
  its suite and checks against this branch, report in that repository's
  `planning/reviews/stage-1.md`, verdict pass.

Three read-only reviews ran in parallel: correctness and security,
performance, readability. Each finding was checked against the code
before it was triaged. A further five findings came from the live checks
on lin-manager-2 (L-xx).

## Findings

| ID | Sev | Area | File | Summary | Outcome |
|---|---|---|---|---|---|
| F-01 | high | correctness | `app/tasks/run_gate_points.py` | An action run the gate refused or held did not hand its host on; an op queued behind it waited for an unrelated finish | fixed in `751dd94` (test: later op dispatched, refuse and defer) |
| F-02 | high | security | `app/ai/approvals.py` | Approval-mode changes were parked before the gate was asked, and `execute_approved` ran them without asking: an approved command ran through a freeze | fixed in `751dd94` (gated when approved and about to run) |
| F-03 | high | performance | `app/run_gate.py`, `run_gate_points.py` | A `retry_at` in the past (or app clock ahead of the DB) left a held row a queue candidate at once; two deferred ops on a host looped | fixed in `751dd94` (hold of at least 10 s by the DB clock; test) |
| F-04 | high | security | `backend/dump.rdb` | A local Redis snapshot was committed with C-047 (RedBeat state only, no secrets) | fixed in `356ee78` (removed, `dump.rdb` ignored); not rewritten out of history: no force-push |
| F-05 | medium | correctness | `app/plugins.py`, `app/tasks/__init__.py` | Extensions now run `register()` in the worker parent before the fork; threads or sockets started there die in the children | fixed in `2f4f5c1` by documenting the rule (CLAUDE.md, PLUGIN_API 1.5 comment); Enterprise starts none |
| F-06 | medium | correctness | `run_gate_points.py` | An action run is gated once, at orchestration, not again after waiting for a busy host | documented in `2f4f5c1`; full fix deferred as PR-25 |
| F-07 | medium | performance | `host_sync_orchestrator.py`, `ca_cert_action.py` | The gate is awaited while the host's advisory lock is held | deferred as PR-22 |
| F-08 | medium | performance | `run_gate_points.py` | `gate_action_run` held a row lock on the run while awaiting the gate | fixed in `751dd94` (no lock; conditional write; test for a cancel that wins) |
| F-09 | medium | performance | `run_gate_retry.py` | Each held row costs a full task every time it is due | deferred as PR-23 |
| F-10 | medium | readability | `docs/ui/operations.md` | `assistant` origin documented as live; it waits for C-009 | fixed in `2f4f5c1` |
| F-11 | medium | readability | `docs/ui/operations.md` | `scheduled_action.gate_refused` missing from the audit action list | fixed in `2f4f5c1` (with the two new `*.gate_refused` rows) |
| F-12 | medium | readability | `run_gate_points.py` | Refuse/defer bookkeeping and the 255 limit written out twice | fixed in `751dd94` (`_held_reason`, `_REASON_MAX`) |
| F-13 | medium | readability | several | New code imports underscore helpers across modules | deferred as PR-27 |
| F-14 | low | correctness | `run_gate_retry.py` | A due row whose send failed was left pending (or queued) with nothing to wake it | fixed in `f66579a` (held again; test) |
| F-15 | low | performance | `run_gate_retry.py` | `retry-held-runs` ran every minute on a Community install | fixed in `f66579a` (entry only while a gate exists; test; seen live) |
| F-16 | low | correctness | `app/api/webhooks.py` | Non-ASCII signature or timestamp gave a 500 to a token holder | fixed in `b38b1e2` (tests fail without it) |
| F-17 | low | correctness | `app/api/webhooks.py` | IPv4 clients on a dual-stack bind never matched an IPv4 range | fixed in `b38b1e2` (test) |
| F-18 | low | correctness | `run_gate_points.py` | A refused sync or action run left no audit row | fixed in `751dd94` (`sync_job.gate_refused`, `action_run.gate_refused`) |
| F-19 | low | performance | `scheduled_action_schedule.py` | A deferred schedule asks the gate every tick | deferred as PR-24 |
| F-20 | low | performance | `app/api/rules.py` | Reorder is one query per id and the list is unbounded | deferred as PR-26 |
| F-21 | low | performance | `app/audit/subscribers.py` | Deep copy per row inside the commit when subscribers exist | rejected: one copy per row is the contract (frozen entries); documented |
| F-22 | low | correctness | `beat_registry.py` | `ensure_entry` assumed the `redbeat:` prefix | fixed in `f66579a` (reads `redbeat_key_prefix`) |
| F-23 | low | readability | `run_gate.py`, `audit/subscribers.py` | Same registry boilerplate twice | deferred as PR-27 |
| F-24 | low | readability | `app/main.py` | `_is_trusted_proxy` / `_get_client_ip` aliases kept for one test | deferred as PR-27 |
| F-25 | low | readability | `app/api/rules.py` | A policy update that changes nothing still wrote a row | fixed in `2f4f5c1` (test) |
| F-26 | low | readability | `app/tasks/__init__.py` | Pool-child loader docstring out of date | fixed in `2f4f5c1` |
| F-27 | low | readability | `beat_registry.py` | Prefix computed twice | fixed in `f66579a` (`_prefix`) |
| F-28 | low | readability | `run_gate.py` | Loose return types, local imports | fixed in `751dd94` |
| F-29 | low | readability | four test files | Contract-version test copied four times | deferred as PR-27 |
| F-30 | low | readability | `CLAUDE.md` | One unwrapped line in "Per-host serialization" | fixed in `2f4f5c1` |
| F-31 | low | correctness | `app/tasks/extensions.py` | Celery caches its module list, so a module registered after it was read was not imported (found by CI ordering) | fixed in `751dd94` |
| L-01 | low | correctness | `app/plugins.py` | Plugin "loaded" line missing from Celery logs (logging not set up at `import_modules`) | deferred as PR-29 |
| L-02 | medium | correctness | `app/api/hosts.py` | Host create/edit/delete write no audit row (older than this stage) | deferred as PR-28 |
| L-03 | low | readability | sync and action rows | `pending_reason` stays on a finished row | deferred as PR-30 |
| L-04 | low | correctness | `run_audit.py` | `ip_address` is null on `action_run.*` rows | rejected for now: the API caller has no IP plumbing yet; noted with C-009 |
| L-05 | low | correctness | live stack | Syncs on the test containers failed on firewall (no firewall in the container) | rejected: environment, not unbork |

| Severity | Total | Fixed | Deferred | Rejected |
|---|---|---|---|---|
| high | 4 | 4 | 0 | 0 |
| medium | 10 | 6 | 4 | 0 |
| low | 22 | 12 | 7 | 3 |

F-05 and F-06 count as fixed by documentation; F-06's code change is
PR-25.

## Numbers before and after

Local runs in this container (PostgreSQL and Redis started locally); CI on
`77c0e00` alongside.

| Check | Before (`6017631`) | After (`77c0e00`) |
|---|---|---|
| Backend tests, local | 4,729 passed, 2 failed, 5 skipped | 4,875 passed, 2 failed, 5 skipped |
| Backend tests, CI | 4,822 passed (stage 0 end) | 4,968 passed, 1 skipped |
| Backend coverage, CI | 83% | **84%** (31,203 statements) |
| ruff check / format | clean / 715 formatted | clean / 735 formatted |
| bandit vs baseline | nothing new | nothing new |
| tsc | clean | clean |
| Frontend lint | 0 errors, 10 warnings | 0 errors, 10 warnings |
| Frontend unit tests | 31 passed | 31 passed |
| CI (all jobs) | green | green, including frontend-e2e |

The two local failures are the same environmental ones as before
(root ignores directory permissions; no `US/Eastern` in tzdata).

## Live checks (local Docker on lin-manager-2)

Branch `77c0e00` on the existing `dev-5d7e443` image (same Python
dependencies; frontend from that image), `all` role only (about 2 GB free),
throwaway sshd containers named `test` and `test-b`, Grafana 12.2.0, and a
throwaway extension using every new hook. All passed:

- Extension loads in API, worker, orchestrator and beat; a raising
  `register()` is skipped in each and every process still starts.
- S-001: subscriber sees API rows and worker rows (`sync_failed`).
- S-002: a two-minute hold left a sync and a `collect_state` run on
  `test` pending with the gate's reason; both ran 19 s after the hold
  ended; a sync on `test-b` meanwhile was not blocked; end listener heard
  every end, including a cancel.
- S-015: the extension's task ran every 30 s from `ext-testplugin-tick`;
  uninstalling removed that entry and `retry-held-runs` at the next beat
  start.
- S-016: the extension route answered 200 to admin and operator, 403 to a
  viewer; `/me` matched.
- C-008: `action_run.started` (origin `api`) and `action_run.cancelled`
  rows for runs cancelled while held and while queued.
- C-022: Grafana HMAC signing accepted, with and without a timestamp
  header; a wrong secret is 401; a source outside `allowed_sources` is
  403. Grafana sends `X-Grafana-Alerting-Signature` (hex) and the
  timestamp header named in the contact point.

## Not verified, and why

- **Split roles (`dev/docker-compose.split.yml`):** not enough memory on
  lin-manager-2; the `all` role exercised the same processes (worker
  parent, orchestrator, beat).
- **Load test:** nothing on a `dev/loadtest/` path changed with no
  extension installed (the gate and listeners return before any query).

## Verdict

**Pass.** No critical or high finding is open.

# Stage 1 report

## Items

| Item | Class | Status | Commits | Open |
|---|---|---|---|---|
| C-008 Audit who starts and cancels action runs | A | done | `63d286b` | `assistant` origin waits for C-009 |
| C-047 Audit firewall rule changes (BUG-147) | A | done | `2f1b702` | none |
| C-022 Harden the alert webhook | A | done | `7a456a1`, `b38b1e2` | none |
| S-001 Extension hook: audit subscribers (1.3) | B | review | `9c7cc71` | Dennis's review |
| S-002 Extension hook: run gate and run ends (1.4) | B | review | `f05429a`, `751dd94`, `f66579a` | Dennis's review; PR-22 to PR-25 |
| S-015 Extension hook: Celery tasks and periodic jobs (1.5) | B | review | `7b06750`, `751dd94` | Dennis's review; the 10 s floor |
| S-016 Extension hook: extension permissions (1.6) | B | review | `ec3817c` | Dennis's review; admin-only default |

Enterprise: no items; review passed (unbork-enterprise, branch
`claude/project-thread-k49y9e`).

## Exit criteria

| Criterion | Result |
|---|---|
| Backend tests, ruff, bandit | met in CI (4,968 passed); locally the same 2 environmental failures as the baseline |
| Frontend tsc, lint, unit tests | met: clean; 0 errors, 10 warnings (unchanged); 31 passed |
| docs/, CHANGELOG `[Unreleased]`, BUGS.md | met: Added and Security entries, docs for audit, webhook, scaling-out and settings; BUG-147 deleted |
| roadmap.yaml and PROGRESS.md current | met |
| Review done, no critical or high open | met (this file) |
| Action-run start (API, schedule) audited, guard test, cancel audited | met: tests plus live check |
| Firewall rule create/edit/delete/reorder audited; BUG-147 gone | met |
| Webhook rejects what the spec says, with tests | met: tests plus live Grafana check |
| Audit subscriber: committed entries only; a raising one is harmless | met: tests plus live check (API and worker) |
| Run gate refuses and defers sync, action run, schedule, AI fix; ends heard | met: tests for each; live check for sync and action run |
| PLUGIN_API minor once per hook, Community unchanged without extension | met: 1.2 to 1.6, one per hook; with nothing registered no extra query, no retry beat entry |
| Extension Celery task and periodic job run in worker and beat | met: tests plus live check (`all` role) |
| Extension permission, route requires it, permission sweep still covers every route | met: tests plus live check |

## Assumptions made without asking

- D-34 (from the S-002 spec): a refused run is created (201) and then
  ends `failed` with "Refused: ...", rather than the API answering 4xx.
- S-001: delivery is at most once (a crash between commit and delivery
  loses the entry); the entry id is the catch-up cursor.
- S-015: fixed intervals only, 10 s minimum, no new queue.
- S-016: an extension permission goes to admin only unless the extension
  names more roles.
- S-002: a held run is retried every minute by default when the gate
  names no time.

## Blocked and waiting

- Dennis's review of the four class B items (S-001, S-002, S-015, S-016)
  before merge.
