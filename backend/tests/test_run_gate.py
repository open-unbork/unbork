"""S-002: the run gate's own contract, and end notifications.

``check_run`` asks gates in order and the first non-allow answer wins; a
gate that raises refuses. End listeners hear about every sync job and
action run that reaches a terminal status, once, after the commit, and
never about a rolled-back write.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.auth.permissions import PLUGIN_API
from app.models.action_run import ActionRun
from app.models.sync_job import JobStatus, SyncJob
from app.plugins import is_compatible
from app.run_gate import (
    ALLOW,
    RunEnded,
    RunRequest,
    Verdict,
    check_run,
    defer,
    hold_until,
    notify_run_ended,
    refuse,
    register_run_end_listener,
    register_run_gate,
    reset_run_gate,
)
from tests.conftest import create_host

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean():
    reset_run_gate()
    yield
    reset_run_gate()


@pytest.fixture
def ended():
    events: list[RunEnded] = []
    register_run_end_listener(events.append)
    return events


REQ = RunRequest(kind="sync", host_ids=(1,))


class _NoQueries:
    """A session that fails the test if anything touches it."""

    def __getattr__(self, name):
        raise AssertionError(f"check_run used the session ({name}) with no gate registered")


# ---------------------------------------------------------------------------
# check_run
# ---------------------------------------------------------------------------


async def test_no_gate_allows_without_a_query():
    assert await check_run(_NoQueries(), REQ) is ALLOW


async def test_the_first_answer_that_is_not_allow_wins():
    asked: list[str] = []

    async def allows(db, request):
        asked.append("allows")
        return ALLOW

    async def defers(db, request):
        asked.append("defers")
        return defer("window closed")

    async def refuses(db, request):  # pragma: no cover - must not be asked
        asked.append("refuses")
        return refuse("never")

    for gate in (allows, defers, refuses):
        register_run_gate(gate)
    register_run_gate(allows)  # a second registration is a no-op

    verdict = await check_run(None, REQ)
    assert (verdict.outcome, verdict.reason) == ("defer", "window closed")
    assert asked == ["allows", "defers"]


async def test_a_raising_gate_refuses_and_logs(caplog):
    async def broken(db, request):
        raise RuntimeError("boom")

    register_run_gate(broken)
    with caplog.at_level(logging.ERROR, logger="app.run_gate"):
        verdict = await check_run(None, REQ)
    assert (verdict.outcome, verdict.reason) == ("refuse", "run gate failed")
    assert any("broken" in r.getMessage() for r in caplog.records)


async def test_an_answer_that_is_not_a_verdict_refuses():
    async def sloppy(db, request):
        return True

    register_run_gate(sloppy)
    assert (await check_run(None, REQ)).outcome == "refuse"


def test_the_types_are_frozen_and_the_helpers_build_verdicts():
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        REQ.kind = "action_run"  # type: ignore[misc]
    assert ALLOW == Verdict("allow") and ALLOW.allowed
    assert refuse("r") == Verdict("refuse", "r")
    when = datetime.now(UTC) + timedelta(minutes=5)
    assert defer("d", when) == Verdict("defer", "d", when)


def test_hold_until():
    when = datetime.now(UTC) + timedelta(minutes=5)
    assert hold_until(defer("d", when)) == when
    default = hold_until(defer("d"))
    assert timedelta(seconds=50) < default - datetime.now(UTC) <= timedelta(seconds=60)


def test_the_contract_version():
    major, minor = (int(x) for x in PLUGIN_API.split("."))
    assert (major, minor) >= (1, 4)
    assert is_compatible("1.2")


# ---------------------------------------------------------------------------
# End notifications
# ---------------------------------------------------------------------------


async def _job(db, status="running") -> SyncJob:
    host = await create_host(db)
    job = SyncJob(host_id=host.id, status=status, module_type="firewall")
    db.add(job)
    await db.flush()
    await db.commit()
    return job


async def _run(db, status="running") -> ActionRun:
    host = await create_host(db)
    run = ActionRun(
        action_key="x", action_version="1", host_id=host.id, parameters={}, status=status
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run


async def test_a_finished_sync_is_announced_once_after_commit(db, ended):
    job = await _job(db)
    job.status = JobStatus.success
    job.completed_at = datetime.now(UTC)
    await db.flush()
    assert ended == []  # not before the commit
    await db.commit()

    assert len(ended) == 1
    event = ended[0]
    assert (event.kind, event.status, event.sync_job_id) == ("sync", "success", job.id)
    assert event.host_ids == (job.host_id,)

    # Touching the row again does not announce it again.
    job.error_message = "later note"
    await db.commit()
    assert len(ended) == 1


@pytest.mark.parametrize("status", ["succeeded", "partial", "failed", "cancelled"])
async def test_every_terminal_action_status_is_announced(db, ended, status):
    run = await _run(db)
    run.status = status
    await db.commit()

    assert [(e.kind, e.status, e.action_run_id) for e in ended] == [("action_run", status, run.id)]


async def test_a_non_terminal_change_is_not_announced(db, ended):
    run = await _run(db, status="queued")
    run.status = "running"
    await db.commit()
    job = await _job(db, status="pending")
    job.status = JobStatus.running
    await db.commit()
    assert ended == []


async def test_a_rolled_back_end_is_not_announced(db, ended):
    run = await _run(db)
    run.status = "failed"
    await db.flush()
    await db.rollback()
    await db.commit()
    assert ended == []


async def test_a_rolled_back_savepoint_is_not_announced(db, ended):
    run = await _run(db)
    nested = await db.begin_nested()
    other = ActionRun(action_key="x", action_version="1", parameters={}, status="failed")
    other.host_id = run.host_id
    db.add(other)
    await db.flush()
    await nested.rollback()
    run.status = "succeeded"
    await db.commit()
    assert [e.action_run_id for e in ended] == [run.id]


async def test_a_raising_listener_changes_nothing_else(db, caplog):
    second: list[RunEnded] = []

    def broken(event):
        raise RuntimeError("boom")

    register_run_end_listener(broken)
    register_run_end_listener(second.append)
    run = await _run(db)
    run.status = "failed"
    with caplog.at_level(logging.ERROR, logger="app.run_gate"):
        await db.commit()

    assert len(second) == 1
    stored = (await db.execute(select(ActionRun).where(ActionRun.id == run.id))).scalar_one()
    assert stored.status == "failed"


async def test_no_listener_collects_nothing(db):
    from app.db_after_commit import _KEY

    run = await _run(db)
    run.status = "failed"
    await db.flush()
    assert not db.sync_session.info.get(_KEY)


async def test_notify_run_ended_delivers_after_commit(db, ended):
    notify_run_ended(db, RunEnded(kind="ai_fix", status="completed", ai_session_id=3))
    assert ended == []
    await db.commit()
    assert [(e.kind, e.ai_session_id) for e in ended] == [("ai_fix", 3)]
