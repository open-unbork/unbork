"""Send syncs and action runs an extension's run gate held, once due.

``app.run_gate``: a gate that defers a sync or an action run leaves it
``pending`` with ``gate_retry_at`` set. The host queue skips such a row
until then (``host_lock._not_held``), so it never stalls the host, and
nothing else would wake it: a pending row is otherwise woken only when
another op on its host finishes. Every minute this task finds the held
rows that are due, clears ``gate_retry_at`` and sends each through the
same dispatch helper the host queue uses; the claim then asks the gate
again. A row whose host is busy by then waits as an ordinary pending row.
"""

from __future__ import annotations

import asyncio
import logging

from app.db import with_task_engine
from app.tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.run_gate_retry.retry_held")
def retry_held() -> dict:
    return asyncio.run(with_task_engine(_retry_held_async()))


async def _retry_held_async() -> dict:
    from sqlalchemy import func, update

    from app.db import task_session
    from app.models.action_run import ActionRun
    from app.models.sync_job import JobStatus, SyncJob
    from app.tasks.host_lock import (
        _dispatch_pending_action_run,
        _dispatch_pending_sync_job,
    )

    counts = {"syncs": 0, "action_runs": 0}
    async with task_session() as db:
        # Claim each due row by clearing its hold in a conditional UPDATE,
        # so two beats (or a beat racing a host release) send it once.
        sync_ids = (
            (
                await db.execute(
                    update(SyncJob)
                    .where(
                        SyncJob.status == JobStatus.pending,
                        SyncJob.gate_retry_at.is_not(None),
                        SyncJob.gate_retry_at <= func.now(),
                    )
                    .values(gate_retry_at=None)
                    .returning(SyncJob.id)
                )
            )
            .scalars()
            .all()
        )
        run_ids = (
            (
                await db.execute(
                    update(ActionRun)
                    .where(
                        ActionRun.status == "pending",
                        ActionRun.gate_retry_at.is_not(None),
                        ActionRun.gate_retry_at <= func.now(),
                    )
                    .values(gate_retry_at=None)
                    .returning(ActionRun.id)
                )
            )
            .scalars()
            .all()
        )
        await db.commit()

        for job_id in sync_ids:
            try:
                job = await db.get(SyncJob, job_id)
                if job is not None and await _dispatch_pending_sync_job(db, job):
                    counts["syncs"] += 1
            except Exception:
                logger.exception("run_gate_retry: could not send held sync job %s", job_id)
        for run_id in run_ids:
            try:
                run = await db.get(ActionRun, run_id)
                if run is not None and await _dispatch_pending_action_run(db, run):
                    counts["action_runs"] += 1
            except Exception:
                logger.exception("run_gate_retry: could not send held action run %s", run_id)
    if counts["syncs"] or counts["action_runs"]:
        logger.info("run_gate_retry: sent %s", counts)
    return counts


def _register_beat_schedule() -> None:
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="retry-held-runs",
        task="app.tasks.run_gate_retry.retry_held",
        run_every_seconds=60,
        app=celery_app,
    )
