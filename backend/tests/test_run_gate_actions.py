"""S-002: the run gate when an action run's orchestrator picks it up.

A refusal fails the run before any per-host row exists; a deferral puts
it back to ``pending``, held, until the retry task sends it to the
orchestrator again, where the gate is asked again. A held run is not a
queue candidate for its host and is left alone by the action sweeper.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import func, select, update

from app.models.action_run import ActionHostRun, ActionRun
from app.run_gate import (
    ALLOW,
    RunRequest,
    defer,
    refuse,
    register_run_end_listener,
    register_run_gate,
    reset_run_gate,
)
from tests.conftest import create_group, create_host

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean():
    reset_run_gate()
    yield
    reset_run_gate()


@pytest.fixture(autouse=True)
def sessions(db):
    @asynccontextmanager
    async def _fake():
        yield db

    with patch("app.db.task_session", new=_fake):
        yield


@pytest.fixture
def sent():
    """Action runs sent to the orchestrator, by id."""
    calls: list[int] = []
    rec = MagicMock(side_effect=lambda run_id, *a, **kw: calls.append(run_id))
    with patch("app.tasks.action_orchestrator.run_action.delay", new=rec):
        yield calls


class Gate:
    def __init__(self, verdict=ALLOW):
        self.verdict = verdict
        self.asked: list[RunRequest] = []

    async def __call__(self, db, request):
        self.asked.append(request)
        return self.verdict


@pytest.fixture
def gate():
    g = Gate()
    register_run_gate(g)
    return g


async def _run(db, *, host_id=None, group_id=None, parameters=None) -> int:
    run = ActionRun(
        action_key="example.restart",
        action_version="1.0",
        host_id=host_id,
        group_id=group_id,
        parameters=parameters or {},
        parallelism=1,
        status="queued",
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run.id


async def _fresh(db, run_id) -> ActionRun:
    db.expire_all()
    return (await db.execute(select(ActionRun).where(ActionRun.id == run_id))).scalar_one()


async def _host_rows(db, run_id) -> int:
    return await db.scalar(
        select(func.count()).select_from(ActionHostRun).where(ActionHostRun.action_run_id == run_id)
    )


async def test_no_gate_changes_nothing(db):
    from app.tasks.run_gate_points import gate_action_run

    host_id = (await create_host(db)).id
    run_id = await _run(db, host_id=host_id)
    assert await gate_action_run(run_id) is True
    assert (await _fresh(db, run_id)).status == "queued"


async def test_the_gate_is_told_what_the_run_is(db, gate):
    from app.tasks.run_gate_points import gate_action_run

    host_id = (await create_host(db)).id
    run_id = await _run(db, host_id=host_id, parameters={"__dry_run": True, "x": 1})

    assert await gate_action_run(run_id) is True
    (request,) = gate.asked
    assert request.kind == "action_run"
    assert request.host_ids == (host_id,)
    assert request.action_key == "example.restart"
    assert request.action_run_id == run_id
    assert request.dry_run is True


async def test_a_group_run_names_every_member(db, gate):
    from app.tasks.run_gate_points import gate_action_run

    group = await create_group(db)
    group_id = group.id
    a = (await create_host(db, group_ids=[group_id])).id
    b = (await create_host(db, ip="10.0.0.2", group_ids=[group_id])).id
    run_id = await _run(db, group_id=group_id)

    await gate_action_run(run_id)
    (request,) = gate.asked
    assert sorted(request.host_ids) == sorted([a, b])
    assert request.group_id == group_id


async def test_a_refusal_fails_the_run_before_any_host_row(db, gate):
    from app.tasks.action_orchestrator import _run_action_async

    ended = []
    register_run_end_listener(ended.append)
    host_id = (await create_host(db)).id
    run_id = await _run(db, host_id=host_id)
    gate.verdict = refuse("change freeze")

    await _run_action_async(run_id)

    run = await _fresh(db, run_id)
    assert run.status == "failed"
    assert run.error_message == "Refused: change freeze"
    assert run.finished_at is not None
    assert await _host_rows(db, run_id) == 0
    assert [(e.kind, e.status, e.action_run_id) for e in ended] == [
        ("action_run", "failed", run_id)
    ]


async def test_a_deferral_holds_the_run_and_the_retry_sends_it_again(db, gate, sent):
    from app.tasks.action_orchestrator import _run_action_async
    from app.tasks.host_lock import dispatch_next_pending_for_host
    from app.tasks.run_gate_retry import _retry_held_async

    host_id = (await create_host(db)).id
    run_id = await _run(db, host_id=host_id)
    gate.verdict = defer("outside the window", datetime.now(UTC) + timedelta(hours=1))

    await _run_action_async(run_id)

    run = await _fresh(db, run_id)
    assert run.status == "pending"
    assert run.pending_reason == "outside the window"
    assert run.gate_retry_at is not None
    assert run.started_at is None
    assert await _host_rows(db, run_id) == 0

    # Held: not the host's next op, and not sent before it is due.
    assert await dispatch_next_pending_for_host(db, host_id) is None
    assert await _retry_held_async() == {"syncs": 0, "action_runs": 0}
    assert sent == []

    await db.execute(
        update(ActionRun)
        .where(ActionRun.id == run_id)
        .values(gate_retry_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()
    assert await _retry_held_async() == {"syncs": 0, "action_runs": 1}
    assert sent == [run_id]
    run = await _fresh(db, run_id)
    assert (run.status, run.gate_retry_at) == ("queued", None)

    # The orchestrator asks again; this time the gate refuses.
    gate.verdict = refuse("still no")
    await _run_action_async(run_id)
    assert (await _fresh(db, run_id)).status == "failed"
    assert len(gate.asked) == 2


async def test_a_held_run_survives_the_action_sweeper(db, gate):
    from app.tasks.action_orchestrator import _run_action_async
    from app.tasks.action_sweeper import _sweep_stale_action_runs_async

    host_id = (await create_host(db)).id
    run_id = await _run(db, host_id=host_id)
    await db.execute(
        update(ActionRun)
        .where(ActionRun.id == run_id)
        .values(created_at=datetime.now(UTC) - timedelta(hours=3))
    )
    await db.commit()
    gate.verdict = defer("held", datetime.now(UTC) + timedelta(hours=1))
    await _run_action_async(run_id)

    await _sweep_stale_action_runs_async()

    run = await _fresh(db, run_id)
    assert run.status == "pending"
    assert run.gate_retry_at is not None


@pytest.mark.parametrize("verdict", [refuse("no"), defer("later")])
async def test_a_gated_run_hands_its_host_to_the_next_op(db, gate, verdict):
    """The host queue may have sent this run as the host's next op; if the
    gate stops it, the op behind it must still be started."""
    from app.models.sync_job import SyncJob
    from app.tasks.action_orchestrator import _run_action_async

    host_id = (await create_host(db)).id
    run_id = await _run(db, host_id=host_id)
    job = SyncJob(host_id=host_id, status="pending", module_type="firewall")
    db.add(job)
    await db.flush()
    await db.commit()
    job_id = job.id
    gate.verdict = verdict

    sent: list[int] = []
    rec = MagicMock(side_effect=lambda **kw: sent.append(kw["job_id"]))
    with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=rec):
        await _run_action_async(run_id)

    assert sent == [job_id]


async def test_a_refused_run_is_audited(db, gate):
    from app.models.audit_log import AuditLog
    from app.tasks.action_orchestrator import _run_action_async

    host_id = (await create_host(db)).id
    run_id = await _run(db, host_id=host_id)
    gate.verdict = refuse("change freeze")
    await _run_action_async(run_id)

    rows = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "action_run.gate_refused", AuditLog.entity_id == run_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [r.after_state["reason"] for r in rows] == ["change freeze"]


async def test_a_cancel_that_got_there_first_wins(db, gate):
    """The gate is asked without a row lock; its verdict is written only
    if the run is still queued."""
    from app.tasks.run_gate_points import gate_action_run

    host_id = (await create_host(db)).id
    run_id = await _run(db, host_id=host_id)

    async def cancelling_gate(db_, request):
        await db_.execute(
            update(ActionRun).where(ActionRun.id == run_id).values(status="cancelled")
        )
        await db_.commit()  # stands in for the cancel's own transaction
        return refuse("no")

    reset_run_gate()
    register_run_gate(cancelling_gate)
    assert await gate_action_run(run_id) is False
    assert (await _fresh(db, run_id)).status == "cancelled"
