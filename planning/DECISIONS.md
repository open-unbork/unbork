# Decisions

Open questions first, then the log of decisions made. Each open question
lists its options, a recommendation and the items it blocks. An item that
depends on an open decision is not scheduled until the decision is logged
below. Questions about Enterprise items (D-05, D-06, D-08 to D-12, D-15,
D-16) are in the Enterprise repository's `planning/DECISIONS.md`.

## Open

Raised while the item specs were written (2026-11-14). Unless a question
says "Blocks", the spec states an assumption and the item can run on it;
Dennis confirms or corrects it at the stage review. Stage numbers in the
log below that differ from `roadmap.yaml` predate the final staging;
`roadmap.yaml` wins.

### D-25 What the CLA check still needs from Dennis
Item: S-011, its fork test only (near the end of the item). A second
GitHub account that is not on the allowlist, to prove the check on a pull
request from a fork. Everything else is settled: see D-48 in the log.

### D-49 How to close the CLA allowlist's name loophole (contact address still open)
Parked: Dennis put all CCLA work on hold on 2026-11-16.
Item: S-011. The CCLA README tells a company to send the CCLA "to the
maintainer" and has no address. Which contact address should it give
(the same question as D-31's source-offer address)? The loophole half was
answered 2026-11-16 (see the Log).

### D-31 Source offer wording and contact address
Parked with the CCLA work (2026-11-16), since it needs the same address.
Blocks: finishing S-010. Dennis supplies the address and signs off the
text.

### D-36 kennel shape
Parked with kennel (D-45). The questions to settle before it is taken up
again: where kennel's responsibility ends and other services begin (for
example Kubernetes autoscalers), whether to specialise in Proxmox,
whether it ships from this repository or its own (revisits D-22), whether
surge nodes are in its first scope, the stack, and the sandbox network
(formerly D-24). Dennis confirmed the hold on 2026-11-15 and asked for
the findings to be kept. They are in three places: the evaluation at
https://claude.ai/artifact/5LMKqifR89FUAqhCDiYbTb, the Proxmox strategy
document at https://claude.ai/artifact/Ts94GFSUAKVx7BfHDeWAMC, and a
summary in the project's shared files (`kennel/kennel-status.md`).

## Assumptions made without asking

- A-01. "Harden the webhook" (C-022) is taken from the board text as
  written; the spec will quote it and add nothing.
- A-02. C-015 is planned although its code is in the private pack
  repository, because the board item names those two actions.
- A-03. Board P3 items stay parked, except C-029, C-030 and C-037, which
  were raised to P2. The ranking table says why for each.
- A-04. Stage runs test on local Docker on lin-manager-2, and on hosts
  named "test" when a real host is needed. The live instance on
  lin-manager is semi-production and other hosts must never break.
- A-05. (Stage 2, C-026) The Alertmanager poller moves from
  `app.tasks.ai_alerts` to `app.tasks.alert_poll`, keeping an alias task
  under the old name, so alerts are still polled without the assistant.
  The spec did not name the poller.
- A-06. (Stage 2, C-026) The `ai.*` setting definitions stay in the
  settings registry and are filtered where they are served, rather than
  moved into an assistant-only module.
- A-07. (Stage 2, S-010) Until D-31 is answered, SOURCE-OFFER.md names
  the GitHub issue tracker as the channel for source requests.
- A-08. (Stage 2, S-010) The .deb, .rpm and tarball notices list Python
  and npm components only: those packages carry no Debian packages of
  their own. An entry in `packaging/licence-overrides.toml` corrects
  metadata and cannot accept an incompatible licence.

## Log

| Date | Decision | By |
|---|---|---|
| 2026-11-22 | D-53: C-014 is built ahead of stage 6 and before C-004, which is not on `dev` yet. It records on the run whether the host was quiesced; the rule that a quiesced run relaxes the `stateful` default to automatic rollback lands with C-004. | Dennis (decision card) |
| 2026-11-16 | D-52: Open proposals taken as recommended. Accepted: PR-13, PR-16 (split out of C-033), PR-18, PR-20, PR-21 (its next step only: rerun the baseline in two-host mode and with more sync slots, then decide), and the stage 1 review's PR-22 to PR-30, bundled as one "run gate follow-ups" item (PR-22 to PR-25, before the first Enterprise gate ships) and one "stage 1 tidy-up" item (PR-26 to PR-30, PR-28 first; PR-26 caps a reorder at 1,000 ids). Deferred: PR-14 (until a user needs it), PR-15 (with kennel), PR-17 (to C-038's stage), Enterprise PR-18 (while D-16 is on hold). PR-12 is in C-016 (D-28). | Dennis (decision card) |
| 2026-11-16 | D-35: Quiesce closes the service to everything but loopback, so verify still works (C-014, C-015). Packs cannot ask for unquiesce before verify. | Dennis (decision card) |
| 2026-11-16 | D-34: A run a gate refuses or defers is accepted, then fails or waits with the reason shown; a gate that raises refuses. No 409 at the API. As built in S-002. | Dennis (decision card) |
| 2026-11-16 | D-33: Every role may create API tokens for itself (`tokens.manage` on viewer, operator and admin); a token acts as its user. No service accounts (C-030). | Dennis (decision card) |
| 2026-11-16 | D-30: Claude Code is installed from the pinned `claude-agent-sdk` wheel, hash-checked against the lock file; labels become "Claude agent" and "Claude single-shot" (S-009, S-008). | Dennis (decision card) |
| 2026-11-16 | D-29: Revises D-02. No fallbacks for the old names: 2.0 uses only the new ones (env prefix, paths, packages, user, metrics, alert labels, on-host markers), with no code to migrate or clean up old names on hosts. Dennis cleans up his own install and hosts by hand, since nobody else runs LabDog. C-048 to C-050 shrink accordingly. | Dennis, in the open-decisions thread |
| 2026-11-16 | D-28: A protected host refuses config writes (sync, CA certificates, post-run hooks), action runs unless the manifest opts in, and any assistant command that is not read-only. PR-12 is folded into C-016, which grows by about an M and adds a manifest field to the pack contract. Setting protection needs `config.edit`; lifting it needs an admin (a global check). | Dennis (decision cards) |
| 2026-11-16 | D-27: The rollback policy (C-004) is per host only, with the `confirm` mode included, since `stateful` defaults to `confirm`. C-004 grows beyond M. | Dennis (decision card) |
| 2026-11-16 | D-26: A destructive run on a host that cannot be snapshotted is refused by default, with a setting to restore the old behaviour (C-005). The release notes and an upgrade check list the schedules this stops. | Dennis (decision card) |
| 2026-11-16 | D-51: Extension periodic jobs keep the 10-second minimum interval (S-015, as built). | Dennis (decision card) |
| 2026-11-16 | D-50: A permission an extension registers goes to admin only unless the extension names more roles (S-016, as built). | Dennis, in the open-decisions thread |
| 2026-11-16 | D-32: Release details. A `2.0.0-rc.1` comes before 2.0.0; `planning/` reaches `main` with the release PR; the stale floating `:test` tag on Docker Hub is left alone. | Dennis (decision cards) |
| 2026-11-16 | CCLA work (D-49's contact address) and D-31 are parked. | Dennis |
| 2026-11-16 | D-49: Close the CLA name loophole with option (b): a second job in `cla.yml` (both repositories) fails a pull request from a fork while any commit is linked to no GitHub account as author or committer. `Claude` stays in the allowlist for the maintainer's own branches; the job also fails fork commits attributed to an allowlisted account other than the PR author, since a git email is not proof of identity. The CCLA contact address is still open. | Dennis |
| 2026-11-15 | D-48: For the CLA check (S-011): the private repository `open-unbork/cla-signatures` exists; the token is the organisation secret `PERSONAL_ACCESS_TOKEN`, scoped to `unbork` and `unbork-playbooks`; the suggested allowlist is accepted (Dennis's own login, the Dependabot and GitHub Actions bots, and the Claude app's bot); the CLA texts live in this repository under `legal/cla/v1/`. | Dennis |
| 2026-11-15 | D-46: The Community hooks S-015 to S-020 and the Enterprise item that follows the rename stay scheduled items, not proposals. | Dennis (decision card) |
| 2026-11-15 | D-47: The load-test baseline (C-063) is run through a Remote Control session on Dennis's computer, with his own SSH key to lin-manager-2. Cloud sessions cannot reach that network. | Dennis (decision card) |
| 2026-11-15 | D-45: Wait with kennel. S-003 (snapshot provider interface) and C-044 (RAM in the rollback snapshot) stay in stage 4; C-028, C-045, C-054, C-055, C-057, C-058 and C-059 are parked and stage 11 is removed. First decide where kennel's responsibility ends. | Dennis |
| 2026-11-14 | D-23 (kennel scope): an isolated sandbox network is supported from the start (a VLAN or SDN zone with no route to production, a jump host for unbork's SSH, outbound NAT), so rehearsing a change on a copy of a VM is in scope. PBS restore goes beside the original first; in-place restore over a production VM comes later, opt-in, with a second token, and only inside an unbork run. The name stays "kennel" for now and is renamed later. | Dennis, in the kennel thread |
| 2026-11-14 | D-22: build kennel (working name). C-028 leaves the parked list and is split and staged in Phase 4 as its own track. Scope to plan, from the exploration write-up and not yet confirmed by Dennis: a standalone AGPL "VM operations service" owning restore tests, PBS restore points, leases for surge nodes and test VMs, sandbox copies, a sweeper and a per-VM readiness API. Sandbox networking, in-place restore and the name are settled in D-23. C-045 moves with it. | Dennis, in the kennel thread |
| 2026-11-14 | D-04: Claude Code is installed on first use and no longer ships in the image (S-009, C-026). Community keeps only the operator's own Claude Code sign-in: new item S-014 removes the subscription token field and corrects its help text (`frontend/app/(dashboard)/ai-providers/client-page.tsx`, `docs/ui/assistant.md`), stage 1, S, class B. | Dennis, as relayed by the coordinator |
| 2026-11-14 | D-19: RAM in unbork, PBS in kennel. C-044 (memory in the rollback snapshot) goes ahead in stage 2. C-045 is recast as "PBS restore points through kennel" and parked with C-028. A Proxmox backup never holds RAM (checked in the qemu-server source; write-up https://claude.ai/artifact/MJo5wKpzFdKspSeMNDrL3S). S-003 carries: a per-VM capability check (snapshot, RAM, restore point, free space), which C-005 calls; `create(vm, label, include_ram)` returning an opaque handle; `rollback(handle, start)`; `delete`; asynchronous completion. | Dennis, on the card |
| 2026-11-14 | D-01: Apache-style individual and corporate CLAs, granted to Dennis "and his successors and assigns", with an explicit transfer clause and a promise that contributions stay available under the AGPL. CLA Assistant Lite checks every PR on unbork and unbork-playbooks, with signatures stored in a private repository in the organisation. Write-up: https://claude.ai/artifact/T7vmNSbLCWN54EF9EWJXFn. S-011 (texts, workflow and required check, CONTRIBUTING licensing section) is stage 0, class B. | Dennis, as relayed by the coordinator |
| 2026-11-14 | D-07: Dennis personally is the copyright holder. S-012 moves to stage 0, class A. | Dennis, as relayed by the coordinator |
| 2026-11-14 | D-02: Full rename with the old names as fallbacks, including on-host markers, alert labels, metrics and pack variables. Postgres database and role names stay; only new example defaults change. Host checks must recognise both names; the first sync removes old chains and files; test on hosts named "test" first. Map: `/mnt/project-files/rename-depth/rename-map.md` (project files). This accepts proposal PR-02 and unblocks C-001. | Dennis, as relayed from the D-02 thread |
| 2026-11-14 | D-03: The first unbork release is 2.0.0. The release PR (C-002) also needs an "Upgrading to 2.0.0" section in docs/upgrade.md, a reworded Versioning section, and a fix for docs/production-deploy.md, which pins an image tag that does not exist (unbork/unbork:1.0.0). | Dennis, as relayed from the D-03 thread |
| 2026-11-14 | D-05: Zabbix alert intake is Community: C-046, an adapter on the S-005 alert source seam, next to Grafana. | Dennis, as relayed by the D-05 thread |
| 2026-11-14 | D-13: propose_action hands off. It creates the ActionRun and ends the turn with a new `waiting_action` session status; the session resumes when the run finishes; `host_lock.py` is unchanged. Approval uses the existing `AIApprovalRequest` cards with frozen arguments. `allowed_action_keys` limits the tool at every level, and under full_auto a listed key runs without asking. The approver needs `ai.approve` and `actions.run` on the target. No AI snapshot. Write-up: https://claude.ai/artifact/AVhU8tSKwe6LeeQmfzE4AG. C-009 becomes class B. | Dennis, as relayed by the D-13 thread |
| 2026-11-14 | D-14: a new `ai.policy.manage` permission, admins only, checked at global scope, with a `PLUGIN_API` minor bump. Replaces the 2026-10-02 "any signed-in user" decision. Unblocks C-019. | Dennis, as relayed by the D-14 thread |
| 2026-11-14 | D-17: keep `planning/` on dev and amend the rule; `plans/` stays branch-scoped scratch. To amend in Phase 6: CONTRIBUTING.md (the `plans/` section and the release checklist line), CLAUDE.md, `.github/PULL_REQUEST_TEMPLATE/release.md`, and `.dockerignore` (add `planning/`). | Dennis, on the card in the D-17 thread |
| 2026-11-14 | D-18: customer-facing Enterprise feature titles appear in the public plan with an Enterprise tag and a no-commitment line; scores, specs, decisions and internal items stay private. | Dennis |
