"""Where syncs and action runs ask the extension run gate (``app.run_gate``).

Each is asked at the moment the work is about to start, which is the one
place every creator passes: a sync in its claim, once the host is free;
an action run when its orchestrator picks it up, before it claims hosts.
With no gate registered both return at once and change nothing.

Either way, work the gate refuses or holds is not taking its hosts, so
those hosts go on to their next op here or in the caller: a held op must
never stall the ops queued behind it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.run_gate import (
    MIN_HOLD_SECONDS,
    RunEnded,
    RunRequest,
    Verdict,
    check_run,
    has_run_gates,
    hold_until,
    notify_run_ended,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: ``pending_reason`` is a ``String(255)`` on both tables.
_REASON_MAX = 255


def _refused(reason: str) -> str:
    return f"Refused: {reason}"[:2000]


def _held_reason(verdict: Verdict) -> str:
    return (verdict.reason or "Held by an extension")[:_REASON_MAX]


def _hold_expr(verdict: Verdict) -> Any:
    """``gate_retry_at`` for a deferral, at least ``MIN_HOLD_SECONDS`` from
    now *by the database's clock*, which is the clock the host queue's
    ``gate_retry_at <= now()`` filter reads. A gate that answers "retry
    now", or an app server whose clock runs ahead, would otherwise leave
    the row a queue candidate at once, and two deferred ops on one host
    would hand the host to each other in a tight loop."""
    from sqlalchemy import func

    return func.greatest(hold_until(verdict), func.now() + timedelta(seconds=MIN_HOLD_SECONDS))


async def gate_sync_job(db: AsyncSession, job_id: int, *, modules: tuple[str, ...]) -> bool:
    """Ask the gate about a sync job whose host is free. True: go ahead.

    False when the gate refused (the job is ``failed``, with an audit row)
    or deferred it (the job stays ``pending``, held until
    ``gate_retry_at``). Either way the write is committed here, and the
    caller must hand the host on, since it is not taking it.

    The inner sync of ``_builtin.sync`` is not asked: its parent run was.
    """
    if not has_run_gates():
        return True
    from sqlalchemy import select

    from app.audit.logger import log_action
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
        await log_action(
            db,
            action="sync_job.gate_refused",
            entity_type="sync_job",
            entity_id=job.id,
            user_id=None,
            after_state={"host_id": job.host_id, "reason": verdict.reason},
        )
    else:
        job.pending_reason = _held_reason(verdict)
        job.gate_retry_at = _hold_expr(verdict)
    await db.commit()
    return False


async def gate_action_run(action_run_id: int) -> bool:
    """Ask the gate about a ``queued`` action run before it starts.

    True: go ahead (also when the run is gone or no longer ``queued``; the
    orchestrator's own checks handle that). False when the gate refused it
    (the run is ``failed``, with no per-host rows and an audit row) or
    deferred it (the run is ``pending``, held until ``gate_retry_at``, and
    the retry task sends it to the orchestrator again). Covers the
    per-host and the group-dispatch shapes, which both start here.

    The verdict is given here, once, before the run waits for any host;
    a run that then waits behind a busy host is not asked again.

    The gate is asked without a row lock (it may call out, and a cancel or
    the sweeper must not wait on it); the verdict is written only if the
    run is still ``queued``. On False the run's hosts are handed on, since
    the host queue may have sent this run as a host's next op.
    """
    if not has_run_gates():
        return True
    from sqlalchemy import select, update

    from app.actions.validation import DRY_RUN_PARAM
    from app.audit.logger import log_action
    from app.db import task_session
    from app.models.action_run import ActionRun
    from app.tasks.action_orchestrator import _resolve_target_host_ids
    from app.tasks.host_lock import release_host_queue

    async with task_session() as db:
        run = (
            await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
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
        if verdict.outcome == "refuse":
            values: dict[str, Any] = {
                "status": "failed",
                "error_message": _refused(verdict.reason),
                "finished_at": datetime.now(UTC),
                "pending_reason": None,
                "gate_retry_at": None,
            }
        else:
            values = {
                "status": "pending",
                "pending_reason": _held_reason(verdict),
                "gate_retry_at": _hold_expr(verdict),
                "started_at": None,
                "orchestrator_id": None,
                "heartbeat_at": None,
            }
        # Conditional, so a cancel that got there first wins.
        written = (
            await db.execute(
                update(ActionRun)
                .where(ActionRun.id == action_run_id, ActionRun.status == "queued")
                .values(**values)
                .returning(ActionRun.id)
                .execution_options(synchronize_session="fetch")
            )
        ).scalar_one_or_none()
        if written is None:
            await db.rollback()
            return False
        if verdict.outcome == "refuse":
            # A Core UPDATE is not seen by the flush listener that announces
            # ends, so this one is announced here.
            notify_run_ended(
                db,
                RunEnded(
                    kind="action_run",
                    status="failed",
                    finished_at=values["finished_at"],
                    host_ids=(run.host_id,) if run.host_id is not None else (),
                    group_id=run.group_id,
                    action_key=run.action_key,
                    user_id=run.triggered_by_user_id,
                    action_run_id=action_run_id,
                    scheduled_action_id=run.scheduled_action_id,
                ),
            )
            await log_action(
                db,
                action="action_run.gate_refused",
                entity_type="action_run",
                entity_id=action_run_id,
                user_id=None,
                after_state={"action_key": run.action_key, "reason": verdict.reason},
            )
        await db.commit()
    logger.info("action_run %d %s by the run gate", action_run_id, verdict.outcome)
    await release_host_queue(
        host_ids, after=f"action_run_id={action_run_id}", exclude_action_run_id=action_run_id
    )
    return False
