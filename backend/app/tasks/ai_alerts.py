"""The assistant's half of alert intake: the investigation decision.

``investigate_alert`` and ``_decide_and_run`` decide whether one recorded
alert is worth a session, and start it if so, where the policy, the budget
and the dedup state can say no cheaply. The Alertmanager poll that used to
live here is :mod:`app.tasks.alert_poll`, which is loaded with or without
the assistant (C-026).
"""

from __future__ import annotations

import asyncio
import logging

from app.db import with_task_engine
from app.tasks import celery_app

logger = logging.getLogger(__name__)


async def build_mission(db, event) -> str:  # noqa: ANN001 - AlertEvent, AsyncSession
    """The prompt this alert's investigation starts from.

    Reads the operator's template if they have edited one; the built-in
    wording in :mod:`app.ai.alert_mission` is the default and the
    fallback.
    """
    from app.ai.alert_mission import render
    from app.settings_service import get_setting_typed

    template = str(await get_setting_typed("ai.alert_mission_template", db))
    return render(template, event)


async def start_investigation(db, event, provider, *, user=None, already_tried=None):  # noqa: ANN001, ANN201
    """Create ``event``'s session at the level its policy allows.

    The one place an alert session is made, shared by the automatic path
    and the investigate button, so the two cannot disagree about what an
    alert may change. The level comes from :mod:`app.ai.alert_autonomy`;
    the system prompt is built for it, so a session that may fix things
    is told what fixing is allowed to mean.

    Flushes and does not commit. The caller commits and only then
    dispatches, so a worker cannot read a session that has not landed —
    and the remediation lock :func:`app.ai.alert_autonomy.resolve` takes
    is held until that commit, which is what makes its in-flight check
    see a session another alert has just created.
    """
    from app.ai import service
    from app.ai.alert_autonomy import resolve
    from app.ai.loop import build_system_prompt
    from app.ai.models import AISession
    from app.audit.logger import log_action

    mission = await build_mission(db, event)
    if already_tried:
        # Appended rather than templated: it is unbork's own account of
        # what it did, not part of the operator's prompt.
        mission = f"{mission}\n\n## What unbork already tried\n\n{already_tried}"
    autonomy = await resolve(db, event)
    session = AISession(
        provider_id=provider.id,
        mode="alert_investigation",
        title=f"Alert: {event.alertname}"[:200],
        mission=mission,
        autonomy_level=autonomy.level,
        status="queued",
        target_host_ids=[event.host_id] if event.host_id else [],
        alert_event_id=event.id,
        created_by_user_id=user.id if user else None,
    )
    db.add(session)
    await db.flush()
    await service.append_message(
        db,
        session.id,
        role="system",
        content=build_system_prompt(autonomy.level, mode="alert_investigation"),
    )
    await service.append_message(db, session.id, role="user", content=mission)

    event.investigation_session_id = session.id
    event.investigation_outcome = "started"
    event.investigation_detail = f"started by {user.email}" if user else None
    event.investigation_autonomy = autonomy.level
    event.investigation_autonomy_note = autonomy.note or None

    # A read-only investigation changes nothing and is already on the
    # alert row. One that may change a host is a decision unbork made on
    # the operator's behalf, and belongs in the audit trail beside the
    # commands it goes on to run.
    if autonomy.level != "read_only":
        await log_action(
            db,
            action="ai_session_created",
            entity_type="ai_session",
            entity_id=session.id,
            user_id=user.id if user else None,
            after_state={
                "autonomy_level": autonomy.level,
                "autonomy_note": autonomy.note,
                "alert_event_id": event.id,
                "alertname": event.alertname,
                "target_host_ids": session.target_host_ids,
                "provider": provider.name,
            },
        )
    return session


async def _decide_and_run(alert_event_id: int, *, already_tried: str | None = None) -> dict:
    """Apply the auto-investigation policy to one recorded alert.

    The alert must ask for the AI with its labels — ``unbork_ai=true``,
    or full auto (:func:`app.alerts.labels.wants_ai`).

    ``already_tried`` is set when the alert's ``unbork_action`` ran first
    and did not fix the alert, or could not run
    (:mod:`app.tasks.alert_handling`); it is added to the mission so the
    session starts from what was done.

    Every path writes ``investigation_outcome``. "Why did nothing happen
    for this alert?" has half a dozen answers and an operator should not
    have to reconstruct which one applied from logs that may have
    rotated.
    """
    from app.ai import service
    from app.ai.models import AlertEvent
    from app.alerts.labels import wants_ai
    from app.db import task_session
    from app.settings_service import get_setting_typed

    async def _finish(db, event, outcome: str, detail: str = "") -> dict:
        event.investigation_outcome = outcome
        event.investigation_detail = detail[:2000] or None
        await db.commit()
        return {"alert_event_id": event.id, "outcome": outcome}

    async with task_session() as db:
        event = await db.get(AlertEvent, alert_event_id)
        if event is None:
            return {"alert_event_id": alert_event_id, "outcome": "missing"}

        if not int(await get_setting_typed("ai.auto_investigate_enabled", db)):
            return await _finish(db, event, "skipped_disabled")

        # A resolved alert has nothing live to look at. Investigating it
        # would read a healthy host and report that nothing is wrong,
        # which is true and useless.
        if event.status != "firing":
            return await _finish(db, event, "skipped_resolved")

        if event.investigation_session_id is not None:
            return await _finish(db, event, "skipped_duplicate", "already investigated")

        if not await wants_ai(db, event):
            return await _finish(
                db,
                event,
                "skipped_no_label",
                "not labelled unbork_ai=true or unbork_ai_automate=true",
            )

        try:
            provider = await service.resolve_provider(db, None)
            # An investigation on a backend that cannot run tools is
            # guesswork presented as fact — the same refusal the chat
            # page makes, for the same reason.
            service.assert_can_investigate(provider)
            await service.assert_within_budget(db, provider)
        except (service.AIDisabledError, service.BudgetExceededError) as exc:
            outcome = "skipped_budget" if isinstance(exc, service.BudgetExceededError) else "failed"
            return await _finish(db, event, outcome, str(exc))

        session = await start_investigation(db, event, provider, already_tried=already_tried)
        await db.commit()

        celery_app.send_task(
            "app.tasks.ai_task.run_chat_session", kwargs={"session_id": session.id}
        )
        return {
            "alert_event_id": event.id,
            "outcome": "started",
            "session_id": session.id,
        }


@celery_app.task(name="app.tasks.ai_alerts.investigate_alert")
def investigate_alert(alert_event_id: int) -> dict:
    """Kept for messages queued before ``handle_alert`` existed.

    New alerts go to ``app.tasks.alert_handling.handle_alert``, which acts
    on the alert's unbork_action label first; this does the same, so a message already in the
    queue during an upgrade is not handled the old way.
    """
    from app.tasks.alert_handling import _handle

    return asyncio.run(with_task_engine(_handle(alert_event_id)))


@celery_app.task(name="app.tasks.ai_alerts.poll_alertmanager")
def poll_alertmanager() -> dict:
    """Kept for messages queued before the poll moved to
    :mod:`app.tasks.alert_poll`, which is loaded with or without the
    assistant (C-026)."""
    from app.tasks.alert_poll import _poll

    return asyncio.run(with_task_engine(_poll()))
