"""The Alertmanager poll: the fallback alert producer.

Asks every Mimir hosts ship to what is currently firing and records it,
which catches what happened while unbork was unreachable and the webhook
by definition cannot. Off by default (``alerts.poll_minutes``). Each new
firing alert goes to ``app.tasks.alert_handling.handle_alert``, which runs
its alert action and, when the assistant is loaded, the AI policy.

Moved out of :mod:`app.tasks.ai_alerts` so it keeps working when the
assistant is left out (C-026).
"""

from __future__ import annotations

import asyncio
import logging

from app.db import with_task_engine
from app.tasks import celery_app

logger = logging.getLogger(__name__)


async def _poll() -> dict:
    """Read currently-firing alerts from every Mimir hosts ship to.

    Reuses the registered Grafana instances' auth and TLS settings rather
    than asking the operator for a second endpoint: the Alertmanager API
    lives under the same Mimir deployment unbork already queries. That is
    the default instance plus any instance a host resolves to through its
    own or its groups' assignment, so alerts for hosts on a non-default
    destination are not missed.
    """
    from app.ai.alerts import from_alertmanager_v2, record
    from app.db import task_session
    from app.grafana.models import GrafanaInstance
    from app.grafana.service import client_for_instance, get_default_instance, resolve_all_hosts
    from app.settings_service import get_setting_typed

    async with task_session() as db:
        if not int(await get_setting_typed("alerts.intake_enabled", db)):
            return {"polled": 0, "reason": "alert intake disabled"}

        default = await get_default_instance(db, "mimir")
        instance_ids = {i for i in (await resolve_all_hosts(db, "mimir")).values() if i is not None}
        if default is not None:
            instance_ids.add(default.id)
        if not instance_ids:
            return {"polled": 0, "reason": "no default Mimir instance registered"}

        from app.grafana.client import PrometheusError

        raw: list = []
        errors: list[str] = []
        for instance_id in sorted(instance_ids):
            inst = await db.get(GrafanaInstance, instance_id)
            if inst is None:
                continue
            try:
                raw.extend(await client_for_instance(inst).get_alertmanager_alerts())
            except PrometheusError as exc:
                logger.warning("alert_poll: Alertmanager poll of %s failed: %s", inst.name, exc)
                errors.append(f"{inst.name}: {exc}")
        if errors and not raw:
            return {"polled": 0, "error": "; ".join(errors)}

        from app.notifications.service import notify_alert_fired

        alerts = from_alertmanager_v2(raw)
        created_ids: list[int] = []
        for alert in alerts:
            event, created = await record(db, alert, source="alertmanager_poll")
            if created and alert.is_firing:
                created_ids.append(event.id)
                await notify_alert_fired(db, event)
        await db.commit()

    # Dispatched after the commit so the task cannot read a row that the
    # transaction has not landed yet.
    for event_id in created_ids:
        celery_app.send_task(
            "app.tasks.alert_handling.handle_alert", kwargs={"alert_event_id": event_id}
        )

    return {"polled": len(alerts), "new": len(created_ids)}


@celery_app.task(name="app.tasks.alert_poll.poll_alertmanager")
def poll_alertmanager() -> dict:
    """Fallback producer — see the module docstring."""
    return asyncio.run(with_task_engine(_poll()))


def _register_beat_schedule() -> None:
    """Self-register the poll on RedBeat, matching the drift sweep.

    The interval is read from the database-backed setting at worker
    start, not from the config file, because it is a UI-tunable value.
    A zero interval means "do not poll" — the entry is removed rather
    than scheduled at some arbitrary floor, so turning it off in the UI
    actually stops it.
    """
    import asyncio as _asyncio

    from redbeat import RedBeatSchedulerEntry

    from app.db import task_session
    from app.settings_service import get_setting_typed

    async def _interval() -> int:
        async with task_session() as db:
            return int(await get_setting_typed("alerts.poll_minutes", db))

    minutes = _asyncio.run(with_task_engine(_interval()))
    name = "poll-alertmanager"
    if minutes <= 0:
        try:
            RedBeatSchedulerEntry.from_key(f"redbeat:{name}", app=celery_app).delete()
        except KeyError:
            # Nothing registered, which is the desired end state anyway.
            logger.debug("alert_poll: no %s entry to remove", name)
        return

    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name=name,
        task="app.tasks.alert_poll.poll_alertmanager",
        run_every_seconds=minutes * 60,
        app=celery_app,
    )
    logger.info("alert_poll: Alertmanager poll registered every %d minute(s)", minutes)
