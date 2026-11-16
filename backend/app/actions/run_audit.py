"""Audit rows for action runs: one when a run is created, one when it is
cancelled.

Every place that constructs an ``ActionRun`` calls :func:`audit_run_started`
in the transaction that stores the run, so the audit log answers "which runs
were started, and by whom" with one filter (``entity_type="action_run"``).
``tests/test_action_run_audit.py`` fails when a new creator forgets it.

Parameter values never reach the log: they can hold operator input and no
manifest field is marked secret, so only their names are recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from app.actions.validation import DRY_RUN_PARAM
from app.audit.logger import log_action

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.action_run import ActionRun

RunOrigin = Literal["api", "schedule", "run_now", "alert", "assistant"]


async def audit_run_started(
    db: AsyncSession,
    run: ActionRun,
    *,
    origin: RunOrigin,
    user_id: int | None,
    ai_session_id: int | None = None,
    ip_address: str | None = None,
) -> None:
    """Add an ``action_run.started`` row for ``run`` to ``db``.

    The run must be flushed, so it has an id. Never commits.
    """
    if run.id is None:
        raise ValueError("audit_run_started needs a flushed run (run.id is None)")
    parameters = run.parameters or {}
    await log_action(
        db,
        action="action_run.started",
        entity_type="action_run",
        entity_id=run.id,
        user_id=user_id,
        after_state={
            "action_key": run.action_key,
            "action_version": run.action_version,
            "target_kind": run.target_kind,
            "target_label": run.target_label,
            "host_id": run.host_id,
            "group_id": run.group_id,
            "origin": origin,
            "scheduled_action_id": run.scheduled_action_id,
            "alert_event_id": run.alert_event_id,
            "ai_session_id": ai_session_id,
            "dry_run": bool(parameters.get(DRY_RUN_PARAM, False)),
            "parameter_names": sorted(k for k in parameters if k != DRY_RUN_PARAM),
        },
        ip_address=ip_address,
    )


async def audit_run_cancelled(
    db: AsyncSession, run: ActionRun, *, user_id: int | None, status_before: str
) -> None:
    """Add an ``action_run.cancelled`` row for ``run`` to ``db``. Never commits."""
    await log_action(
        db,
        action="action_run.cancelled",
        entity_type="action_run",
        entity_id=run.id,
        user_id=user_id,
        before_state={"status": status_before},
        after_state={"status": run.status},
    )
