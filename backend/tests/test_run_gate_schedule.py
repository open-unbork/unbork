"""S-002: the run gate at a schedule's fire.

A refusal skips that fire (it counts as handled) and audits the reason; a
deferral leaves the schedule due, so the next tick asks again.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.action_run import ActionRun
from app.models.audit_log import AuditLog
from app.models.scheduled_action import ScheduledAction
from app.run_gate import ALLOW, RunRequest, defer, refuse, register_run_gate, reset_run_gate
from app.tasks.scheduled_action_schedule import _check_due_async
from tests.conftest import create_host

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean():
    reset_run_gate()
    yield
    reset_run_gate()


@pytest.fixture(autouse=True)
def patch_task_session(db):
    @asynccontextmanager
    async def _fake():
        yield db

    with patch("app.db.task_session", new=_fake):
        yield


@pytest.fixture(autouse=True)
def stub_celery_dispatch():
    with patch("celery.app.base.Celery.send_task"):
        yield


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


async def _due_schedule(db) -> tuple[int, int, datetime]:
    host_id = (await create_host(db)).id
    last = datetime.now(UTC) - timedelta(minutes=2)
    sa = ScheduledAction(
        target_kind="host",
        target_id=host_id,
        action_key="_builtin.collect_state",
        schedule_cron="* * * * *",
        enabled=True,
        last_dispatched_at=last,
    )
    db.add(sa)
    await db.commit()
    return sa.id, host_id, last


async def _runs(db, sa_id) -> list[ActionRun]:
    return list(
        (await db.execute(select(ActionRun).where(ActionRun.scheduled_action_id == sa_id)))
        .scalars()
        .all()
    )


async def _last_dispatched(db, sa_id) -> datetime | None:
    db.expire_all()
    return await db.scalar(
        select(ScheduledAction.last_dispatched_at).where(ScheduledAction.id == sa_id)
    )


async def test_allow_dispatches_and_the_gate_sees_the_schedule(db, gate):
    sa_id, host_id, _ = await _due_schedule(db)

    result = await _check_due_async()

    assert result["dispatched"] == 1
    (request,) = gate.asked
    assert request.kind == "schedule_dispatch"
    assert request.host_ids == (host_id,)
    assert request.scheduled_action_id == sa_id
    assert request.action_key == "_builtin.collect_state"
    assert len(await _runs(db, sa_id)) == 1


async def test_a_refusal_skips_the_fire_and_audits_it(db, gate):
    sa_id, _, last = await _due_schedule(db)
    gate.verdict = refuse("change freeze")

    result = await _check_due_async()

    assert result["dispatched"] == 0
    assert result["skipped_gate"] == 1
    assert await _runs(db, sa_id) == []
    assert await _last_dispatched(db, sa_id) > last
    rows = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "scheduled_action.gate_refused",
                    AuditLog.entity_id == sa_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert [r.after_state for r in rows] == [{"reason": "change freeze"}]

    # The fire was handled: the next tick in the same minute does not ask again.
    gate.asked.clear()
    assert (await _check_due_async())["skipped_gate"] == 0
    assert gate.asked == []


async def test_a_deferral_leaves_it_due_and_the_next_tick_asks_again(db, gate):
    sa_id, _, last = await _due_schedule(db)
    gate.verdict = defer("outside the window")

    result = await _check_due_async()

    assert result["deferred_gate"] == 1
    assert result["dispatched"] == 0
    assert await _runs(db, sa_id) == []
    assert await _last_dispatched(db, sa_id) == last

    gate.verdict = ALLOW
    assert (await _check_due_async())["dispatched"] == 1
    assert len(await _runs(db, sa_id)) == 1
    assert len(gate.asked) == 2
