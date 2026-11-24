"""Quiesce hooks around the snapshot window (rollback phase 2).

An action whose manifest declares ``quiesce_playbook`` and
``unquiesce_playbook`` closes the host to writes before unbork snapshots
it, and reopens it after verify and any rollback, on every exit path. Both
executors are covered: the per-host one (``action_host``) and group
dispatch (``action_group``). ``run_ansible`` and Proxmox are fakes that
write to one shared timeline, so each test reads the order of phases off
it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy import select

from app.models.action_run import ActionHostRun, ActionRun
from tests.conftest import create_group, create_host, create_ssh_key

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("sample_pack")]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def exists(self, *_args):
        return 0

    def publish(self, channel, payload):
        self.published.append((channel, payload))

    def setex(self, *_args, **_kwargs):
        return None


@pytest.fixture(autouse=True)
def patch_task_session(db):
    @asynccontextmanager
    async def _fake():
        yield db

    with patch("app.db.task_session", new=_fake):
        yield


@pytest.fixture(autouse=True)
def fake_redis():
    fr = _FakeRedis()
    with patch("redis.from_url", return_value=fr):
        yield fr


@pytest.fixture(autouse=True)
def bypass_preflight():
    async def _ok(_host_id, _ssh_key_path):
        return True, None

    with patch("app.tasks.action_host._preflight_reachable", side_effect=_ok):
        yield


@pytest.fixture(autouse=True)
def released():
    """Record which hosts each run hands on, without dispatching anything."""
    mock = AsyncMock()
    with patch("app.tasks.host_lock.release_host_queue", new=mock):
        yield mock


class _Timeline:
    """Every playbook run and Proxmox call, in order."""

    def __init__(self) -> None:
        self.entries: list[tuple] = []
        #: playbook basename -> what its next run does: "ok", "fail", an
        #: exception instance to raise, or a set of inventory names to fail.
        self.behaviour: dict[str, object] = {}

    def names(self) -> list[str]:
        return [e[0] for e in self.entries]

    def calls(self, name: str) -> list[tuple]:
        return [e for e in self.entries if e[0] == name]


_LOOKUP = re.compile(r"\{\{ (\w+)\[inventory_hostname\] \| default\((.*)\) \}\}")


def _seen_by(entry: tuple, host: str, var: str):
    """What *var* resolves to on *host* in a recorded run, as Ansible would.

    A group run gives the hook variables per member as an extra-var that
    looks the member up in a table (see ``_per_member_var``); anything else
    is a plain extra-var. Missing means the playbook never gets it.
    """
    extra = entry[1]
    value = extra.get(var, KeyError)
    if value is KeyError:
        return KeyError
    match = _LOOKUP.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return value
    return extra[match[1]].get(host, yaml.safe_load(match[2]))


def _runner_for(inventory_json: str, failing: set[str] | bool):
    hosts = list(json.loads(inventory_json)["all"]["hosts"])
    runner = MagicMock()
    runner.stdout = "PLAY RECAP\n"
    failed = set(hosts) if failing is True else set(failing or ())
    runner.status = "failed" if failed else "successful"
    runner.rc = 2 if failed else 0
    runner.events = [
        {"event": "runner_on_failed", "event_data": {"host": h, "res": {"msg": f"{h} failed"}}}
        if h in failed
        else {"event": "runner_on_ok", "event_data": {"host": h}}
        for h in hosts
    ]
    return runner


@pytest.fixture
def timeline():
    tl = _Timeline()

    def _fake_run_ansible(**kw):
        name = Path(kw["playbook_path"]).stem
        inventory = json.loads(kw["inventory_json"])["all"]["hosts"]
        tl.entries.append((name, kw.get("extra_vars") or {}, inventory))
        what = tl.behaviour.get(name, "ok")
        if isinstance(what, BaseException):
            raise what
        if what == "ok":
            return _runner_for(kw["inventory_json"], False)
        if what == "fail":
            return _runner_for(kw["inventory_json"], True)
        return _runner_for(kw["inventory_json"], set(what))

    with patch("app.ansible_runtime.runner.run_ansible", side_effect=_fake_run_ansible):
        yield tl


@pytest.fixture
def fake_proxmox(timeline):
    class _Client:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            pass

        async def create_snapshot(self, pve_node, vmid, name, description="", *, vm_type="qemu"):  # noqa: ARG002
            timeline.entries.append(("snapshot", vmid))
            return "UPID:snap"

        async def delete_snapshot(self, pve_node, vmid, name, *, vm_type="qemu"):  # noqa: ARG002
            timeline.entries.append(("snapshot_deleted", vmid))
            return "UPID:rm"

        async def wait_for_task(self, *args, **kwargs):  # noqa: ARG002
            return None

    async def _fake_rollback(
        client, pve_node, vmid, snapshot_name, host, key, db, *, vm_type="qemu"
    ):  # noqa: ARG001
        timeline.entries.append(("rollback", vmid))
        return {"success": True}

    async def _verify(*args, **kwargs):  # noqa: ARG001
        timeline.entries.append(("verify",))
        return {"passed": True, "services_ok": True, "packages_ok": True}

    with (
        patch("app.proxmox.client.ProxmoxClient", new=_Client),
        patch("app.workflows.steps.rollback.rollback_to_snapshot", side_effect=_fake_rollback),
        patch("app.workflows.steps.verify.run_verification", side_effect=_verify),
    ):
        yield


async def _seed_proxmox_node(db) -> int:
    from app.crypto.encryption import encrypt_ssh_key
    from app.crypto.key_management import get_master_key
    from app.proxmox.models import ProxmoxNode

    node = ProxmoxNode(
        name=f"pve-q-{id(db)}",
        api_url="https://pve.test:8006",
        token_id="unbork@pve!ci",
        encrypted_token_secret=encrypt_ssh_key("token-secret", get_master_key()),
        verify_ssl=False,
    )
    db.add(node)
    await db.flush()
    return node.id


async def _map_vm(db, host_id: int, node_id: int, vmid: int) -> None:
    from app.proxmox.vm_mapping import VMMapping

    db.add(
        VMMapping(
            host_id=host_id,
            proxmox_node_id=node_id,
            pve_node_name="pve1",
            vmid=vmid,
            vm_name=f"vm-{vmid}",
        )
    )
    await db.flush()


async def _host_run(
    db,
    *,
    parameters: dict | None = None,
    snapshot_enabled: bool = True,
    mapped: bool = True,
):
    key = await create_ssh_key(db)
    host = await create_host(db, ssh_key_id=key.id, hostname="quiesce-host")
    if mapped:
        await _map_vm(db, host.id, await _seed_proxmox_node(db), vmid=901)
    run = ActionRun(
        action_key="stateful-upgrade",
        action_version="1.0",
        host_id=host.id,
        parameters=parameters or {},
        parallelism=1,
        status="queued",
        snapshot_enabled=snapshot_enabled,
        verify_enabled=True,
        auto_rollback=True,
    )
    db.add(run)
    await db.flush()
    hr = ActionHostRun(action_run_id=run.id, host_id=host.id, status="queued")
    db.add(hr)
    await db.flush()
    await db.commit()
    return run.id, hr.id


async def _run_host(run_id: int, hr_id: int) -> None:
    from app.tasks.action_host import _run_action_host_async

    await _run_action_host_async(run_id, hr_id)


async def _row(db, hr_id: int) -> ActionHostRun:
    return (await db.execute(select(ActionHostRun).where(ActionHostRun.id == hr_id))).scalar_one()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _manifest(**extra):
    from app.actions.manifest import ActionManifest

    return ActionManifest(
        key="x",
        name="x",
        description="x",
        icon="x",
        playbook="playbook.yml",
        version="1",
        estimated_duration="1m",
        **extra,
    )


def test_a_manifest_needs_both_hooks_or_neither():
    with pytest.raises(ValidationError, match="declare both or neither"):
        _manifest(quiesce_playbook="quiesce.yml")
    with pytest.raises(ValidationError, match="declare both or neither"):
        _manifest(unquiesce_playbook="unquiesce.yml")
    m = _manifest(quiesce_playbook="q.yml", unquiesce_playbook="u.yml")
    assert m.quiesce_timeout_seconds == 300


def test_a_manifest_cannot_declare_the_reserved_vars_as_parameters():
    for key in ("unbork_quiesced", "unbork_run_outcome"):
        with pytest.raises(ValidationError, match="reserved"):
            _manifest(parameters=[{"key": key, "label": "x", "type": "string"}])


def test_the_loader_resolves_both_hooks():
    from app.actions.registry import ACTION_REGISTRY

    action = ACTION_REGISTRY["stateful-upgrade"]
    assert action.quiesces
    assert action.quiesce_playbook_path.name == "quiesce.yml"
    assert action.unquiesce_playbook_path.name == "unquiesce.yml"
    assert action.quiesce_timeout_seconds == 120
    assert not ACTION_REGISTRY["linux-upgrade"].quiesces


async def test_the_catalog_says_which_actions_quiesce(superuser_client):
    resp = await superuser_client.get("/api/actions")
    assert resp.status_code == 200
    by_key = {a["key"]: a for a in resp.json()}
    assert by_key["stateful-upgrade"]["quiesces"] is True
    assert by_key["linux-upgrade"]["quiesces"] is False


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


def test_the_hooks_widen_the_per_host_deadline_and_the_hard_margin():
    from app.tasks.action_timeouts import (
        HARD_LIMIT_MARGIN_SECONDS,
        hard_limit_margin_seconds,
        quiesce_budget_seconds,
    )

    assert quiesce_budget_seconds("stateful-upgrade") == 240
    assert quiesce_budget_seconds("linux-upgrade") == 0
    assert hard_limit_margin_seconds("linux-upgrade") == HARD_LIMIT_MARGIN_SECONDS
    # 120 s unquiesce + slack fits inside the default margin.
    assert hard_limit_margin_seconds("stateful-upgrade") == HARD_LIMIT_MARGIN_SECONDS


def test_a_long_unquiesce_raises_the_hard_margin():
    from dataclasses import replace

    from app.actions.registry import ACTION_REGISTRY
    from app.tasks.action_timeouts import hard_limit_margin_seconds, per_host_deadline_seconds

    base = ACTION_REGISTRY["stateful-upgrade"]
    before = per_host_deadline_seconds("stateful-upgrade")
    slow = replace(base, quiesce_timeout_seconds=900)
    with patch.dict(ACTION_REGISTRY, {"stateful-upgrade": slow}):
        assert hard_limit_margin_seconds("stateful-upgrade") == 900 + 120
        assert per_host_deadline_seconds("stateful-upgrade") == before + 2 * (900 - 120)


# ---------------------------------------------------------------------------
# Per-host executor
# ---------------------------------------------------------------------------


async def test_success_runs_quiesce_snapshot_playbook_verify_unquiesce_cleanup(
    db, timeline, fake_proxmox
):
    run_id, hr_id = await _host_run(db)
    await _run_host(run_id, hr_id)

    assert timeline.names() == [
        "quiesce",
        "snapshot",
        "playbook",
        "verify",
        "unquiesce",
        "snapshot_deleted",
    ]
    assert "unbork_quiesced" not in timeline.calls("quiesce")[0][1]
    assert timeline.calls("playbook")[0][1]["unbork_quiesced"] is True
    assert timeline.calls("unquiesce")[0][1]["unbork_run_outcome"] == "succeeded"
    hr = await _row(db, hr_id)
    assert hr.status == "succeeded"
    assert "[quiesce] exit=0" in hr.output
    assert "[unquiesce] exit=0" in hr.output


async def test_a_failed_playbook_rolls_back_then_unquiesces(db, timeline, fake_proxmox):
    run_id, hr_id = await _host_run(db)
    timeline.behaviour["playbook"] = "fail"
    await _run_host(run_id, hr_id)

    assert timeline.names() == [
        "quiesce",
        "snapshot",
        "playbook",
        "rollback",
        "unquiesce",
        "snapshot_deleted",
    ]
    assert timeline.calls("unquiesce")[0][1]["unbork_run_outcome"] == "rolled_back"
    assert (await _row(db, hr_id)).status == "failed"


async def test_a_failed_unquiesce_after_a_rollback_keeps_the_snapshot(db, timeline, fake_proxmox):
    run_id, hr_id = await _host_run(db)
    timeline.behaviour["playbook"] = "fail"
    timeline.behaviour["unquiesce"] = "fail"
    await _run_host(run_id, hr_id)

    assert "rollback" in timeline.names()
    assert "snapshot_deleted" not in timeline.names()
    hr = await _row(db, hr_id)
    assert "Unquiesce failed" in hr.error_message
    assert "kept — unquiesce failed" in hr.output


async def test_a_failed_quiesce_takes_no_snapshot_and_still_unquiesces(
    db, timeline, fake_proxmox, released
):
    run_id, hr_id = await _host_run(db)
    timeline.behaviour["quiesce"] = "fail"
    await _run_host(run_id, hr_id)

    assert timeline.names() == ["quiesce", "unquiesce"]
    assert timeline.calls("unquiesce")[0][1]["unbork_run_outcome"] == "failed"
    hr = await _row(db, hr_id)
    assert hr.status == "failed"
    assert hr.error_message.startswith("Quiesce failed")
    assert hr.snapshot_name is None
    released.assert_awaited()


async def test_an_exception_after_quiesce_still_unquiesces_and_releases(
    db, timeline, fake_proxmox, released
):
    run_id, hr_id = await _host_run(db)
    timeline.behaviour["playbook"] = RuntimeError("runner exploded")
    with pytest.raises(RuntimeError):
        await _run_host(run_id, hr_id)

    assert timeline.names()[-1] == "unquiesce"
    assert timeline.calls("unquiesce")[0][1]["unbork_run_outcome"] == "failed"
    hr = await _row(db, hr_id)
    assert hr.status == "failed"
    assert "runner exploded" in hr.error_message
    assert "[unquiesce] exit=0" in hr.output
    released.assert_awaited()


async def test_a_soft_timeout_after_quiesce_still_unquiesces(db, timeline, fake_proxmox, released):
    from celery.exceptions import SoftTimeLimitExceeded

    run_id, hr_id = await _host_run(db)
    timeline.behaviour["playbook"] = SoftTimeLimitExceeded()
    with pytest.raises(SoftTimeLimitExceeded):
        await _run_host(run_id, hr_id)

    assert len(timeline.calls("unquiesce")) == 1
    hr = await _row(db, hr_id)
    assert hr.status == "failed"
    assert "soft time limit" in hr.error_message
    released.assert_awaited()


async def test_a_failed_unquiesce_after_success_keeps_the_snapshot(db, timeline, fake_proxmox):
    run_id, hr_id = await _host_run(db)
    timeline.behaviour["unquiesce"] = "fail"
    await _run_host(run_id, hr_id)

    assert "rollback" not in timeline.names()
    assert "snapshot_deleted" not in timeline.names()
    hr = await _row(db, hr_id)
    assert hr.status == "failed"
    assert hr.error_message.startswith("Unquiesce failed")
    assert "kept — unquiesce failed" in hr.output


async def test_unquiesce_runs_once_even_when_it_fails_after_a_failure(db, timeline, fake_proxmox):
    run_id, hr_id = await _host_run(db)
    timeline.behaviour["quiesce"] = "fail"
    timeline.behaviour["unquiesce"] = "fail"
    await _run_host(run_id, hr_id)

    assert len(timeline.calls("unquiesce")) == 1
    hr = await _row(db, hr_id)
    assert "Quiesce failed" in hr.error_message
    assert "Unquiesce failed" in hr.error_message


@pytest.mark.parametrize(
    "kwargs",
    [
        {"parameters": {"__dry_run": True}},
        {"snapshot_enabled": False},
        {"mapped": False},
    ],
    ids=["dry-run", "snapshot-disabled", "no-vm-mapping"],
)
async def test_no_snapshot_means_no_hooks(db, timeline, fake_proxmox, kwargs):
    run_id, hr_id = await _host_run(db, **kwargs)
    await _run_host(run_id, hr_id)

    assert "quiesce" not in timeline.names()
    assert "unquiesce" not in timeline.names()
    assert "unbork_quiesced" not in timeline.calls("playbook")[0][1]
    assert (await _row(db, hr_id)).status == "succeeded"


def test_a_non_destructive_action_never_quiesces():
    from app.tasks.action_host import _Envelope, _RunSpec, _should_quiesce

    env = _Envelope(client=object(), pve_node="pve1", vmid=1)
    fields = {f: None for f in _RunSpec.__dataclass_fields__}
    fields.update(
        quiesce_playbook_path="q.yml",
        unquiesce_playbook_path="u.yml",
        snapshot_enabled=True,
        destructive=True,
    )
    assert _should_quiesce(_RunSpec(**fields), env, dry_run=False)
    fields["destructive"] = False
    assert not _should_quiesce(_RunSpec(**fields), env, dry_run=False)


# ---------------------------------------------------------------------------
# Group executor
# ---------------------------------------------------------------------------


async def _group_run(db, *, mapped=("g-h1", "g-h2"), unmapped=()):
    key = await create_ssh_key(db)
    group = await create_group(db)
    node_id = await _seed_proxmox_node(db)
    for i, name in enumerate([*mapped, *unmapped]):
        host = await create_host(db, hostname=name, ssh_key_id=key.id, group_ids=[group.id])
        if name in mapped:
            await _map_vm(db, host.id, node_id, vmid=950 + i)
    run = ActionRun(
        action_key="stateful-cluster-upgrade",
        action_version="1.0",
        host_id=None,
        group_id=group.id,
        parameters={},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run.id


async def _member_rows(db, run_id: int) -> dict[str, ActionHostRun]:
    rows = (
        (await db.execute(select(ActionHostRun).where(ActionHostRun.action_run_id == run_id)))
        .scalars()
        .all()
    )
    return {r.hostname: r for r in rows}


async def test_group_success_quiesces_mapped_members_around_the_window(
    db, timeline, fake_proxmox, released
):
    from app.tasks.action_group import _run_action_group_async

    run_id = await _group_run(db, unmapped=("g-h3",))
    await _run_action_group_async(run_id)

    names = timeline.names()
    assert names.index("quiesce") < names.index("snapshot") < names.index("playbook")
    assert names.index("playbook") < names.index("unquiesce") < names.index("snapshot_deleted")
    # Only the VM-mapped members are quiesced, and only they are told so.
    assert set(timeline.calls("quiesce")[0][2]) == {"g-h1", "g-h2"}
    main = timeline.calls("playbook")[0]
    assert _seen_by(main, "g-h1", "unbork_quiesced") is True
    assert _seen_by(main, "g-h3", "unbork_quiesced") is False
    unquiesce = timeline.calls("unquiesce")[0]
    assert set(unquiesce[2]) == {"g-h1", "g-h2"}
    assert _seen_by(unquiesce, "g-h1", "unbork_run_outcome") == "succeeded"
    rows = await _member_rows(db, run_id)
    assert {r.status for r in rows.values()} == {"succeeded"}
    assert "[unquiesce] exit=0" in rows["g-h1"].output


async def test_group_member_failing_quiesce_is_reopened_and_dropped(db, timeline, fake_proxmox):
    from app.tasks.action_group import _run_action_group_async

    run_id = await _group_run(db)
    timeline.behaviour["quiesce"] = {"g-h2"}
    await _run_action_group_async(run_id)

    first_unquiesce = timeline.calls("unquiesce")[0]
    assert set(first_unquiesce[2]) == {"g-h2"}
    assert _seen_by(first_unquiesce, "g-h2", "unbork_run_outcome") == "failed"
    assert set(timeline.calls("playbook")[0][2]) == {"g-h1"}
    assert [e[1] for e in timeline.calls("snapshot")] == [950]
    rows = await _member_rows(db, run_id)
    assert rows["g-h2"].status == "failed"
    assert rows["g-h2"].error_message.startswith("Quiesce failed")
    assert rows["g-h1"].status == "succeeded"


async def test_group_failed_member_rolls_back_then_unquiesces(db, timeline, fake_proxmox):
    from app.tasks.action_group import _run_action_group_async

    run_id = await _group_run(db)
    timeline.behaviour["playbook"] = {"g-h1"}
    await _run_action_group_async(run_id)

    names = timeline.names()
    # The restored snapshot outlives the unquiesce, as in the per-host path.
    assert names.index("rollback") < names.index("unquiesce") < names.index("snapshot_deleted")
    assert sorted(e[1] for e in timeline.calls("snapshot_deleted")) == [950, 951]
    unquiesce = timeline.calls("unquiesce")[0]
    assert _seen_by(unquiesce, "g-h1", "unbork_run_outcome") == "rolled_back"
    assert _seen_by(unquiesce, "g-h2", "unbork_run_outcome") == "succeeded"


async def test_group_failed_unquiesce_keeps_that_members_snapshot(db, timeline, fake_proxmox):
    from app.tasks.action_group import _run_action_group_async

    run_id = await _group_run(db)
    timeline.behaviour["unquiesce"] = {"g-h1"}
    await _run_action_group_async(run_id)

    assert [e[1] for e in timeline.calls("snapshot_deleted")] == [951]
    assert "rollback" not in timeline.names()
    rows = await _member_rows(db, run_id)
    assert rows["g-h1"].status == "failed"
    assert "Unquiesce failed" in rows["g-h1"].error_message
    assert rows["g-h2"].status == "succeeded"


async def test_group_abort_after_quiesce_still_unquiesces(db, timeline, fake_proxmox, released):
    from app.tasks.action_group import _run_action_group_async

    run_id = await _group_run(db)
    timeline.behaviour["playbook"] = RuntimeError("runner exploded")
    await _run_action_group_async(run_id)

    unquiesce = timeline.calls("unquiesce")
    assert len(unquiesce) == 1
    assert set(unquiesce[0][2]) == {"g-h1", "g-h2"}
    assert {_seen_by(unquiesce[0], h, "unbork_run_outcome") for h in unquiesce[0][2]} == {"failed"}
    run = await db.get(ActionRun, run_id)
    assert run.status == "failed"
    released.assert_awaited()


async def test_group_dry_run_never_quiesces(db, timeline, fake_proxmox):
    from app.actions.validation import DRY_RUN_PARAM
    from app.tasks.action_group import _run_action_group_async

    run_id = await _group_run(db)
    run = await db.get(ActionRun, run_id)
    run.parameters = {DRY_RUN_PARAM: True}
    await db.commit()
    await _run_action_group_async(run_id)

    assert timeline.names() == ["playbook"]


@pytest.mark.skipif(shutil.which("ansible-playbook") is None, reason="needs ansible-core")
def test_per_member_vars_outrank_a_plays_own_defaults(tmp_path):
    """The live finding behind ``_per_member_var``: run real Ansible.

    A play that gives the hook variables a default in ``vars:`` must
    still see each member's own value. As inventory host vars they lost to
    that default; as extra-vars nothing outranks them.
    """
    from app.actions.manifest import (
        QUIESCED_HOSTS_VAR,
        QUIESCED_VAR,
        RUN_OUTCOME_VAR,
        RUN_OUTCOMES_VAR,
    )
    from app.tasks.action_group import _per_member_var

    extra = {
        **_per_member_var(QUIESCED_VAR, QUIESCED_HOSTS_VAR, {"a": True}, default="false"),
        **_per_member_var(
            RUN_OUTCOME_VAR, RUN_OUTCOMES_VAR, {"a": "rolled_back"}, default="'failed'"
        ),
    }
    (tmp_path / "extra.json").write_text(json.dumps(extra))
    (tmp_path / "inv.ini").write_text("a ansible_connection=local\nb ansible_connection=local\n")
    (tmp_path / "play.yml").write_text(
        "- hosts: all\n"
        "  gather_facts: false\n"
        "  vars:\n"
        "    unbork_quiesced: false\n"
        "    unbork_run_outcome: unknown\n"
        "  tasks:\n"
        "    - ansible.builtin.debug:\n"
        '        msg: "seen {{ inventory_hostname }} {{ unbork_quiesced | to_json }} '
        '{{ unbork_run_outcome }}"\n'
    )
    result = subprocess.run(  # noqa: S603
        [
            shutil.which("ansible-playbook"),
            "-i",
            str(tmp_path / "inv.ini"),
            "-e",
            f"@{tmp_path / 'extra.json'}",
            str(tmp_path / "play.yml"),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
        timeout=120,
    )
    assert "seen a true rolled_back" in result.stdout
    assert "seen b false failed" in result.stdout
