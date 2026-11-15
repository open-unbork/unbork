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
