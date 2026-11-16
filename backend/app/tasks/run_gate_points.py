"""Where syncs and action runs ask the extension run gate (``app.run_gate``).

Each is asked at the moment the work is about to start, which is the one
place every creator passes: a sync in its claim, once the host is free;
an action run when its orchestrator picks it up, before it claims hosts.
With no gate registered both return at once and change nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.run_gate import RunRequest, check_run, has_run_gates, hold_until

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _refused(reason: str) -> str:
    return f"Refused: {reason}"[:2000]


async def gate_sync_job(db: AsyncSession, job_id: int, *, modules: tuple[str, ...]) -> bool:
    """Ask the gate about a sync job whose host is free. True: go ahead.

    False when the gate refused (the job is ``failed``) or deferred it (the
    job stays ``pending``, held until ``gate_retry_at``). Either way the
    write is committed here, and the caller must hand the host on, since
    it is not taking it.

    The inner sync of ``_builtin.sync`` is not asked: its parent run was.
    """
    if not has_run_gates():
        return True
    from sqlalchemy import select

    from app.models.sync_job import JobStatus, SyncJob

    job = (await db.execute(select(SyncJob).where(SyncJob.id == job_id))).scalar_one_or_none()
    if job is None or job.origin_action_host_run_id is not None:
        return True
    verdict = await check_run(
        db,
        RunRequest(
            kind="sync",
            host_ids=(job.host_id,),
            group_id=job.group_id,
            modules=tuple(job.module_filter or modules),
            user_id=job.triggered_by_user_id,
            sync_job_id=job.id,
        ),
    )
    if verdict.allowed:
        job.gate_retry_at = None
        return True
    if verdict.outcome == "refuse":
        job.status = JobStatus.failed
        job.error_message = _refused(verdict.reason)
        job.completed_at = datetime.now(UTC)
        job.pending_reason = None
        job.gate_retry_at = None
    else:
        job.pending_reason = (verdict.reason or "Held by an extension")[:255]
        job.gate_retry_at = hold_until(verdict)
    await db.commit()
    return False


async def gate_action_run(action_run_id: int) -> bool:
    """Ask the gate about a ``queued`` action run before it starts.

    True: go ahead (also when the run is gone or no longer ``queued``; the
    orchestrator's own checks handle that). False when the gate refused it
    (the run is ``failed``, with no per-host rows) or deferred it (the run
    is ``pending``, held until ``gate_retry_at``, and the retry task sends
    it to the orchestrator again). Covers the per-host and the
    group-dispatch shapes, which both start here.
    """
    if not has_run_gates():
        return True
    from sqlalchemy import select

    from app.actions.validation import DRY_RUN_PARAM
    from app.db import task_session
    from app.models.action_run import ActionRun
    from app.tasks.action_orchestrator import _resolve_target_host_ids

    async with task_session() as db:
        run = (
            await db.execute(
                select(ActionRun).where(ActionRun.id == action_run_id).with_for_update()
            )
        ).scalar_one_or_none()
        if run is None or run.status != "queued":
            return True
        host_ids = await _resolve_target_host_ids(db, run)
        verdict = await check_run(
            db,
            RunRequest(
                kind="action_run",
                host_ids=tuple(host_ids),
                group_id=run.group_id,
                action_key=run.action_key,
                user_id=run.triggered_by_user_id,
                action_run_id=run.id,
                scheduled_action_id=run.scheduled_action_id,
                dry_run=bool((run.parameters or {}).get(DRY_RUN_PARAM, False)),
            ),
        )
        if verdict.allowed:
            if run.gate_retry_at is not None:
                run.gate_retry_at = None
                await db.commit()
            return True
        now = datetime.now(UTC)
        if verdict.outcome == "refuse":
            run.status = "failed"
            run.error_message = _refused(verdict.reason)
            run.finished_at = now
            run.pending_reason = None
            run.gate_retry_at = None
        else:
            run.status = "pending"
            run.pending_reason = (verdict.reason or "Held by an extension")[:255]
            run.gate_retry_at = hold_until(verdict)
            run.started_at = None
            run.orchestrator_id = None
            run.heartbeat_at = None
        await db.commit()
        logger.info("action_run %d %s by the run gate", action_run_id, verdict.outcome)
        return False
