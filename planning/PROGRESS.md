# Progress log

Append-only. Newest entry last.

## 2026-11-14 Planning session, phases 1 to 3

- Read both roadmap boards (36 and 19 draft items), CLAUDE.md, README,
  CONTRIBUTING, BUGS.md, the CI workflow and the Enterprise repository.
- Recorded the baseline on `dev` at `5d7e443`: see PLAN.md.
- Built the inventory: 43 Community, 12 shared, 22 Enterprise items; 16
  class A, 47 class B, 14 class H.
- Wrote 7 proposals here and 3 in the Enterprise repository.
- Stopped at the checkpoint. Nothing is staged, no item specs exist, and
  the skills are not written yet.
- Not done: the Playwright suite, the docs build and the load test were
  not run.

## 2026-11-14 Checkpoint follow-up

- Dennis asked for a thread per open question; 17 threads are exploring
  them. Decisions are logged in DECISIONS.md as he picks.
- Dennis added work: C-044, C-045 and one Enterprise item (source: user).
  New question D-19.

## 2026-11-14 Planning session, phases 4 to 7

- Dennis accepted every open proposal, took the recommendation on the
  last open Community-side question, and gave the go-ahead for staging.
- Final inventory: 114 items in 12 stages (0 to 11), 18 parked. Classes:
  27 A, 79 B, 8 H. Seven of them (S-015 to S-020 and one Enterprise
  item) were added while the specs were written, because the spec
  writers found Community hooks that Enterprise features need and that
  no item covered.
- Wrote `roadmap.yaml`, `stages/stage-0.md` to `stage-11.md`, one spec per
  item under `items/`, and the `/run-stage` and `/review-stage` skills.
- No stage has started. `base_commit` is null for every stage until
  `/run-stage` sets it.
- Questions the spec writers could not settle are D-24 to D-36 in
  DECISIONS.md; each spec states the assumption it was written on. New
  ideas are PR-12 to PR-17, open and not scheduled.
- Self-review: every item in `roadmap.yaml` has a spec; each spec's
  "Depends on" row equals the yaml; no item depends on a later stage or
  on a parked item; no `C-` or `S-` item depends on an `E-` item; every
  class A and B spec has steps, acceptance criteria and a test plan;
  every stage file ends with "Review and hardening".
- Known loose ends: a few specs keep an assumption line that a later
  edit made stale (for example an Enterprise spec asking whether its hook
  should be an item of its own, which it now is). Specs were written
  against this branch, which predates #297 on `dev`.

## 2026-11-15 kennel parked

- Dennis decided to wait with kennel (D-45). Stage 11 is removed and its
  seven items are parked; their specs are kept. S-003 and C-044 are
  unchanged in stage 4. The plan now has stages 0 to 10 and 25 parked
  items.

## 2026-11-15 Stage 0 run

Work branch: `claude/project-thread-bs6l27` (the session's assigned branch, in place of `roadmap/stage-0`). Base: `de69a7b` plus #300 (CLA secret name), so merging this branch also merges #300.

- 2026-11-15 C-064 done f31b2c0. Open: whether `main` should carry `planning/` (D-32).
- 2026-11-15 S-012 done 59904e2. GitHub licence detection to check after merge; unbork-playbooks LICENSE left as is (spec assumption).
- 2026-11-15 S-011 review cf4811a (+ unbork-playbooks#26). Open: steps 7 and 8 (co-author and fork tests) can only run once cla.yml is on dev, since pull_request_target runs the base branch's workflow; the fork test needs Dennis's second account. Dennis marks CLAAssistant required after the first green run.
- 2026-11-15 C-024 done 59a11e8. To confirm on the stage PR's CI run: one pushed tag, trivy passes, :test digest unchanged; fork-PR skip checked with the S-011 fork test.
  (C-024: `unbork/unbork:test` digest before the stage PR: `sha256:e57d503a55a1027c5a08a036f3ed20883be6ec876e42009265d0914df912ce2b`.)
- 2026-11-15 C-025 in_progress cb2761d..a61cfed. Done once every job is green on 26.04 in the stage PR's CI; release-guard and packaging-smoke must pass there before the next release PR.
- 2026-11-15 C-062 in_progress 04bc864. Done once the stage PR's CI prints the figure and PLAN.md records it.
- 2026-11-15 C-061 done 87278f6. frontend-test CI job to confirm on the stage PR.
- 2026-11-15 stage 0 review: pass, 27 findings (0 critical, 0 high, 9 medium, 18 low); 20 fixed, 2 deferred (PR-19, PR-20), 4 rejected, 1 to verify live. planning/reviews/stage-0.md.
- 2026-11-15 C-062 done 04bc864, b3cafa1. CI on 320a361 prints "Backend coverage: 83% of statements"; recorded in PLAN.md.
- 2026-11-15 C-024 check: the stage PR's build-test-image pushed only `:test-d214e8b` (19:30Z). `:test` moved once more at 19:06Z, from the `dev` push that merged #300 with the old workflow, to `sha256:506dff7b33deaeb4a2c870b63d4ba0dbc8b97d0e1c0909b5fc3b620d750dacc0`; the stage PR's run left it there. That digest is the new reference for after merge.
- 2026-11-15 C-025 done. Every job green on ubuntu-26.04 in the stage PR's CI (run 43751734330), frontend-e2e, packaging-smoke and trivy included. C-061's frontend-test job also green there.
- 2026-11-15 C-063 done 290c7d1. S2 300 default on tester (10.10.10.164) at de69a7b: done rc=0 ooms=0; bulk sync 18 s, 692 in the window, backlog never cleared (PR-21). planning/reviews/baseline-loadtest.md.
- 2026-11-16 S-011 D-49 (b): LinkedCommits job in cla.yml here and in unbork-playbooks#26; README, CONTRIBUTING, CHANGELOG updated.

## 2026-11-16 Stage 0 merged

- Dennis approved merging (project chat, 2026-11-16). Merged: unbork #301 (`25b5b1a`), unbork-playbooks #26 (`1e478b2`), unbork-enterprise #8 (`cdbf509`, after rotating the test realm's Keycloak secrets). Stage 0 done; S-011 done (texts approved with the merge).
- S-012: GitHub detects `dev`'s LICENSE as AGPL-3.0 with the copyright line.
- S-011: the CLA check does not run in this repository yet. GitHub takes `pull_request_target` and `issue_comment` workflows from the default branch, which is `main`, so `cla.yml` on `dev` starts nothing (#304 got no CLA run; the Actions workflow list shows only CI). It works in unbork-playbooks, whose default branch is `main`. It starts here with the next `dev` to `main` release. Step 7 (co-author) and step 8 (fork) move there, or to a playbooks PR.
- Still open after merge: S-011 step 8, the fork test (needs Dennis's second account); F-03, a Dependabot PR passing CLAAssistant; Dennis marks `CLAAssistant` and `LinkedCommits` required on `dev` (and `main` in playbooks).

## 2026-11-16 Stage 1 run

Work branch: `claude/project-thread-k49y9e` (the session's assigned branch, in place of `roadmap/stage-1`). Base: `6017631`. Entry criterion met: stage 0 merged 2026-11-16. Dennis said to start ("Start stage 1 when ready", 2026-11-16).
- 2026-11-16 C-008 done 63d286b. Audit page checked from its code (generic entity label), not on a running stack.
- 2026-11-16 C-047 done 2f1b702. BUG-147 deleted.
- 2026-11-16 C-022 done 7a456a1. Checked live against Grafana 12.2.0 on local Docker on lin-manager-2 during the stage review (signature with and without timestamp, wrong secret 401, allowlist 403).
- 2026-11-16 S-001 review 9c7cc71. PLUGIN_API 1.3. Worker delivery on a running stack is left to the stage review.
- 2026-11-16 S-002 review f05429a. PLUGIN_API 1.4, migration 0061 (gate_retry_at). The live check with a deferring plugin on a host named "test" is left to the stage review.
- 2026-11-16 S-015 review 7b06750. PLUGIN_API 1.5. Real worker and beat processes with a test plugin are left to the stage review.
- 2026-11-16 S-016 review ec3817c. PLUGIN_API 1.6. Default role for an extension permission is admin only (assumption, asked in the stage report).
- 2026-11-16 Stage 1 review: pass. 36 findings (4 high, all fixed); 11 deferred as PR-22 to PR-30; live checks on lin-manager-2 all passed. Report in `planning/reviews/stage-1.md`. Stage status `review`, waiting for Dennis's review of S-001, S-002, S-015, S-016.

## 2026-11-17 Stage 1 merged

- Dennis approved merging (2026-11-17). #305 merged as `829ace2`; unbork-enterprise#10 merged as `5ff6890`. S-001, S-002, S-015 and S-016 done. Stage 1 done.

## 2026-11-17 Stage 2 run

Work branch: `claude/project-thread-1sra3u` (the session's assigned branch, in place of `roadmap/stage-2`). Base: `2e961d7` (dev after #306). Entry criterion met: stage 1 merged 2026-11-17. Dennis said to start ("Yes", 2026-11-17). Behaviours revised for D-29 (no old-name fallbacks).
- 2026-11-17 C-048 review babde9b. No fallbacks (D-29): the startup error lists old variables and config files.
- 2026-11-17 C-049 review e8e12f2.
- 2026-11-17 C-050 review e97ec3c. No on-host cleanup (D-29); docs/upgrade.md lists what to remove by hand.
- 2026-11-17 C-051 done a91d627. Old-name guard test with the `old-name-ok` marker.
- 2026-11-17 S-008 done 68f9385.
- 2026-11-17 S-014 review 31884e2.
- 2026-11-17 S-017 review fdb8ed5. PLUGIN_API 1.7.
- 2026-11-17 S-009 review 70a787b. Install on a running stack checked in the stage review.
- 2026-11-17 C-026 review 9828765. Whole suite passes with the assistant off (AI tests skip). Assumptions A-05, A-06.
- 2026-11-17 S-010 blocked 24ae507. Code done; D-31 (contact address and wording) open. Assumptions A-07, A-08.
