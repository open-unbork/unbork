"""S-002: the run gate at a sync's claim (and a CA certificate deploy's).

A refusal fails the job and hands the host on; a deferral keeps it
``pending`` and held, out of the host's queue, until the retry task sends
it again; the inner sync of ``_builtin.sync`` is never asked.
"""

from __future__ import annotations

import contextlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select, update

from app.models.action_run import ActionHostRun, ActionRun
from app.models.sync_job import JobStatus, SyncJob
from app.run_gate import ALLOW, RunRequest, defer, refuse, register_run_gate, reset_run_gate
from tests.conftest import create_host

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

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("app.tasks.host_sync_orchestrator.task_session", new=_fake))
        stack.enter_context(patch("app.db.task_session", new=_fake))
        yield


@pytest.fixture
def sent():
    """Sync jobs sent to their task, by id."""
    calls: list[int] = []
    rec = MagicMock(side_effect=lambda **kw: calls.append(kw["job_id"]))
    with (
        patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=rec),
        patch("app.tasks.ca_cert_action.run_ca_cert_action.delay", new=rec),
    ):
        yield calls


class Gate:
    """A stand-in gate whose answer the test sets, recording what it is asked."""

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


async def _job(db, host_id, module_type="firewall", status="pending", **extra) -> int:
    job = SyncJob(host_id=host_id, status=status, module_type=module_type, **extra)
    db.add(job)
    await db.flush()
    await db.commit()
    return job.id


async def _fresh(db, job_id) -> SyncJob:
    db.expire_all()
    return (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one()


async def _claim(db, job_id, host_id) -> bool:
    from app.tasks.host_sync_orchestrator import _claim_or_defer

    return await _claim_or_defer(db, job_id, host_id)


async def test_allow_claims_as_before(db, gate, sent):
    host_id = (await create_host(db)).id
    job_id = await _job(db, host_id, module_filter=["firewall"])

    assert await _claim(db, job_id, host_id) is True
    (request,) = gate.asked
    assert (request.kind, request.host_ids, request.sync_job_id) == ("sync", (host_id,), job_id)
    assert request.modules == ("firewall",)


async def test_a_refusal_fails_the_job_and_starts_the_next(db, gate, sent):
    host_id = (await create_host(db)).id
    job_id = await _job(db, host_id, module_type="firewall")
    next_id = await _job(db, host_id, module_type="service")
    gate.verdict = refuse("change freeze")

    assert await _claim(db, job_id, host_id) is False

    job = await _fresh(db, job_id)
    assert job.status == JobStatus.failed
    assert job.error_message == "Refused: change freeze"
    assert job.completed_at is not None
    assert sent == [next_id]

    from app.models.audit_log import AuditLog

    (row,) = (
        (await db.execute(select(AuditLog).where(AuditLog.action == "sync_job.gate_refused")))
        .scalars()
        .all()
    )
    assert row.entity_id == job_id
    assert row.after_state["reason"] == "change freeze"


async def test_a_gate_that_says_retry_now_still_holds_the_job(db, gate, sent):
    """Two deferred jobs on one host must not hand the host to each other
    in a loop: a hold is at least MIN_HOLD_SECONDS by the database clock."""
    host_id = (await create_host(db)).id
    first = await _job(db, host_id, module_type="firewall")
    second = await _job(db, host_id, module_type="service")
    gate.verdict = defer("not now", datetime.now(UTC) - timedelta(minutes=5))

    assert await _claim(db, first, host_id) is False
    assert sent == [second]
    assert await _claim(db, second, host_id) is False
    # The first is still held, so the host went to nothing.
    assert sent == [second]


async def test_a_deferral_holds_the_job_without_blocking_the_host(db, gate, sent):
    host_id = (await create_host(db)).id
    held_id = await _job(db, host_id, module_type="firewall")
    later_id = await _job(db, host_id, module_type="service")
    gate.verdict = defer("outside the window")

    assert await _claim(db, held_id, host_id) is False

    held = await _fresh(db, held_id)
    assert held.status == JobStatus.pending
    assert held.pending_reason == "outside the window"
    assert held.gate_retry_at > datetime.now(UTC)
    # The host went on to the later job, not back to the held one.
    assert sent == [later_id]

    # The later job still runs (its caller flips it to running), and once
    # it finishes the held one is not a candidate.
    gate.verdict = ALLOW
    sent.clear()
    assert await _claim(db, later_id, host_id) is True
    from app.tasks.host_lock import dispatch_next_pending_for_host

    assert await dispatch_next_pending_for_host(db, host_id, exclude_sync_job_id=later_id) is None


async def test_the_retry_task_sends_a_due_held_job_and_the_gate_is_asked_again(db, gate, sent):
    from app.tasks.run_gate_retry import _retry_held_async

    host_id = (await create_host(db)).id
    held_id = await _job(db, host_id)
    gate.verdict = defer("not yet", datetime.now(UTC) + timedelta(hours=1))
    await _claim(db, held_id, host_id)
    sent.clear()

    # Not due: nothing is sent.
    assert await _retry_held_async() == {"syncs": 0, "action_runs": 0}
    assert sent == []

    await db.execute(
        update(SyncJob)
        .where(SyncJob.id == held_id)
        .values(gate_retry_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()
    assert await _retry_held_async() == {"syncs": 1, "action_runs": 0}
    assert sent == [held_id]
    assert (await _fresh(db, held_id)).gate_retry_at is None

    gate.verdict = ALLOW
    assert await _claim(db, held_id, host_id) is True
    assert len(gate.asked) == 2


async def test_the_inner_sync_of_builtin_sync_is_not_asked(db, gate, sent):
    host_id = (await create_host(db)).id
    run = ActionRun(
        action_key="_builtin.sync",
        action_version="1",
        host_id=host_id,
        parameters={},
        status="running",
    )
    db.add(run)
    await db.flush()
    row = ActionHostRun(action_run_id=run.id, host_id=host_id, status="running")
    db.add(row)
    await db.flush()
    job_id = await _job(db, host_id, origin_action_host_run_id=row.id)
    gate.verdict = refuse("must not be asked")

    from app.tasks.host_sync_orchestrator import _claim_or_defer

    assert await _claim_or_defer(db, job_id, host_id, exclude_action_run_id=run.id) is True
    assert gate.asked == []


async def test_a_held_job_survives_the_sync_sweeper(db, gate, sent):
    from app.tasks.sync_sweeper import _sweep_stale_syncs_async

    host_id = (await create_host(db)).id
    held_id = await _job(
        db,
        host_id,
        created_at=datetime.now(UTC) - timedelta(hours=3),
    )
    gate.verdict = defer("held", datetime.now(UTC) + timedelta(hours=1))
    await _claim(db, held_id, host_id)
    sent.clear()

    await _sweep_stale_syncs_async()

    held = await _fresh(db, held_id)
    assert held.status == JobStatus.pending
    assert held.gate_retry_at is not None
    assert sent == []


async def test_a_ca_certificate_deploy_is_gated_too(db, gate, sent):
    from app.ca_certs.actions import CA_CERT_MODULE_TYPE
    from app.tasks.ca_cert_action import _claim_or_defer

    host_id = (await create_host(db)).id
    job_id = await _job(db, host_id, module_type=CA_CERT_MODULE_TYPE)
    gate.verdict = refuse("no certificates today")

    assert await _claim_or_defer(job_id, host_id) is False
    job = await _fresh(db, job_id)
    assert job.status == JobStatus.failed
    assert job.error_message == "Refused: no certificates today"
    assert gate.asked[0].modules == ("ca_certs",)

    gate.verdict = ALLOW
    other = await _job(db, host_id, module_type=CA_CERT_MODULE_TYPE)
    assert await _claim_or_defer(other, host_id) is True
    assert (await _fresh(db, other)).status == JobStatus.running


async def test_a_refused_sync_is_announced_as_ended(db, gate, sent):
    from app.run_gate import register_run_end_listener

    ended = []
    register_run_end_listener(ended.append)
    host_id = (await create_host(db)).id
    job_id = await _job(db, host_id)
    gate.verdict = refuse("no")

    await _claim(db, job_id, host_id)

    assert [(e.kind, e.status, e.sync_job_id) for e in ended] == [("sync", "failed", job_id)]


async def test_a_held_job_whose_send_fails_is_held_again(db, gate):
    from app.tasks.run_gate_retry import _retry_held_async

    host_id = (await create_host(db)).id
    held_id = await _job(db, host_id)
    await db.execute(
        update(SyncJob)
        .where(SyncJob.id == held_id)
        .values(gate_retry_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()

    broken = MagicMock(side_effect=ConnectionError("broker down"))
    with patch("app.tasks.host_sync_orchestrator.run_host_sync.delay", new=broken):
        await _retry_held_async()

    job = await _fresh(db, held_id)
    assert job.status == JobStatus.pending
    assert job.gate_retry_at is not None


def test_the_retry_entry_exists_only_while_a_gate_does(monkeypatch):
    from app.tasks import run_gate_retry

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.tasks.beat_registry.ensure_entry", lambda name, **kw: calls.append(("ensure", name))
    )
    monkeypatch.setattr(
        "app.tasks.beat_registry.remove_entry", lambda name, app: calls.append(("remove", name))
    )

    run_gate_retry._register_beat_schedule()
    register_run_gate(Gate())
    run_gate_retry._register_beat_schedule()
    assert calls == [("remove", "retry-held-runs"), ("ensure", "retry-held-runs")]
