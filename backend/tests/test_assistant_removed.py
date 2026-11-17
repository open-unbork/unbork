"""C-026: with ``[assistant] enabled = false`` the assistant is not loaded.

No AI route, task, setting, permission, notification or built-in action is
offered, and alerts still run their actions. Runs in both configurations:
the switch is flipped per test where the behaviour is decided at request
time, and a fresh process or app is built where it is decided at start.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import settings
from app.settings_service import invalidate_cache

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def assistant_off(monkeypatch):
    monkeypatch.setattr(settings.assistant, "enabled", False)
    invalidate_cache()
    yield
    invalidate_cache()


def _route_paths(app) -> set[str]:
    """Every served path, router prefixes included (lazy routers too)."""
    try:
        from fastapi.routing import _iter_routes_with_context
    except ImportError:  # older FastAPI flattens included routers
        return {getattr(r, "path", "") for r in app.routes}
    return {
        ctx.path if ctx is not None else getattr(route, "path", "")
        for route, ctx in _iter_routes_with_context(app.routes)
    }


class TestStartup:
    def test_no_ai_route_is_registered(self, assistant_off):
        from app.main import create_app

        paths = _route_paths(create_app())
        assert not [p for p in paths if p.startswith("/api/ai")], paths
        assert "/api/alerts/{alert_id}/investigate" not in paths
        assert not [p for p in paths if p.endswith("/investigate")]
        # Everything else is still there.
        assert "/api/actions/runs" in paths
        assert "/api/alerts" in paths
        assert "/api/webhooks/grafana-alerts" in paths

    @pytest.mark.needs_assistant
    def test_with_the_assistant_the_routes_are_there(self):
        from app.main import create_app

        paths = _route_paths(create_app())
        assert "/api/ai/providers" in paths
        assert "/api/alerts/{alert_id}/investigate" in paths

    def test_no_ai_task_is_registered_in_a_worker(self):
        """A fresh process, as a worker starts: only the modules it includes."""
        code = (
            "import importlib, json\n"
            "from app.tasks import celery_app\n"
            "for m in celery_app.conf.include: importlib.import_module(m)\n"
            "print(json.dumps(sorted(celery_app.tasks)))\n"
        )
        env = {**os.environ, "UNBORK_ASSISTANT__ENABLED": "false"}
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        ).stdout
        names = json.loads(out.strip().splitlines()[-1])
        assert not [n for n in names if n.startswith("app.tasks.ai_")], names
        assert "app.tasks.alert_handling.handle_alert" in names
        assert "app.tasks.alert_poll.poll_alertmanager" in names
        assert "app.tasks.action_orchestrator.run_action" in names

    def test_the_task_module_lists(self):
        from app.tasks import AI_TASK_MODULES, task_modules

        without = task_modules(False)
        assert not [m for m in without if m.rsplit(".", 1)[-1].startswith("ai_")]
        assert set(task_modules(True)) == set(without) | set(AI_TASK_MODULES)
        assert "app.tasks.alert_poll" in without

    def test_beat_deletes_the_assistants_schedules(self, assistant_off):
        from app.tasks import beat_registry, celery_app

        removed: list[str] = []
        include = celery_app.conf.include
        celery_app.conf.include = []
        try:
            with (
                patch.object(
                    beat_registry, "remove_entry", side_effect=lambda n, a: removed.append(n)
                ),
                patch.object(beat_registry, "_register_extension_entries"),
            ):
                results = beat_registry.register_all(celery_app)
        finally:
            celery_app.conf.include = include
        assert sorted(removed) == sorted(beat_registry.AI_ENTRY_NAMES)
        assert all(k.startswith("assistant-off:") for k in results)

    @pytest.mark.needs_assistant
    def test_with_the_assistant_beat_keeps_them(self):
        from app.tasks import beat_registry, celery_app

        include = celery_app.conf.include
        celery_app.conf.include = []
        try:
            with (
                patch.object(beat_registry, "remove_entry") as remove,
                patch.object(beat_registry, "_register_extension_entries"),
            ):
                beat_registry.register_all(celery_app)
        finally:
            celery_app.conf.include = include
        remove.assert_not_called()

    def test_the_ai_builtins_are_not_in_the_registry(self, assistant_off):
        from app.actions.builtins import AI_BUILTIN_KEYS, register_builtins

        registry: dict = {}
        register_builtins(registry)
        assert not AI_BUILTIN_KEYS & set(registry)
        assert "_builtin.sync" in registry


class TestRequests:
    async def test_version_reports_the_feature(self, client, assistant_off):
        resp = await client.get("/api/version")
        assert resp.json()["features"] == {"assistant": False}

    @pytest.mark.needs_assistant
    async def test_version_with_the_assistant(self, client):
        resp = await client.get("/api/version")
        assert resp.json()["features"] == {"assistant": True}

    async def test_no_ai_setting_is_served(self, superuser_client, assistant_off):
        listed = await superuser_client.get("/api/settings")
        assert listed.status_code == 200
        keys = [s["key"] for s in listed.json()]
        assert not [k for k in keys if k.startswith("ai.")]
        assert "alerts.intake_enabled" in keys

        refused = await superuser_client.patch("/api/settings/ai.enabled", json={"value": "1"})
        assert refused.status_code == 404

    @pytest.mark.needs_assistant
    async def test_with_the_assistant_the_settings_are_served(self, superuser_client):
        keys = [s["key"] for s in (await superuser_client.get("/api/settings")).json()]
        assert "ai.enabled" in keys

    async def test_me_leaves_out_the_ai_permissions(self, superuser_client, assistant_off):
        me = (await superuser_client.get("/api/users/me")).json()
        for field in ("permissions", "permissions_anywhere"):
            assert "ai.use" not in me[field]
            assert "ai.approve" not in me[field]
        assert "sync.run" in me["permissions"]
        scoped = (await superuser_client.get("/api/users/me/permissions")).json()
        assert "ai.use" not in scoped["permissions"]

    @pytest.mark.needs_assistant
    async def test_me_with_the_assistant(self, superuser_client):
        me = (await superuser_client.get("/api/users/me")).json()
        assert "ai.use" in me["permissions"]

    async def test_no_ai_notification_is_offered(self, superuser_client, assistant_off):
        resp = await superuser_client.get("/api/notifications/events")
        if resp.status_code == 404:
            pytest.skip("no event list route")
        keys = {e["key"] for e in resp.json()}
        assert "alert_fired" in keys and "alert_action" in keys
        assert not keys & {
            "approval_requested",
            "approval_expiring",
            "approval_expired",
            "alert_remediation",
        }

    async def test_alert_intake_reports_no_ai(self, superuser_client, db, assistant_off):
        from app.models.app_setting import AppSetting

        db.add(AppSetting(key="ai.enabled", value="1"))
        await db.flush()
        invalidate_cache()
        resp = await superuser_client.get("/api/alerts/intake")
        assert resp.status_code == 200
        assert resp.json()["ai_enabled"] is False
        assert resp.json()["auto_investigate_enabled"] is False


class TestAlertsStillRunActions:
    @pytest.fixture
    def tasks_use_test_db(self, db):
        @asynccontextmanager
        async def _cm():
            yield db

        with patch("app.db.task_session", new=_cm):
            yield

    async def test_an_action_label_still_runs_its_action(
        self, db, sample_pack, tasks_use_test_db, assistant_off, monkeypatch
    ):
        from app.ai.alert_autonomy import MIN_WEBHOOK_TOKEN_CHARS

        # Alert actions need a webhook token long enough to trust.
        monkeypatch.setattr(settings.alerts, "webhook_token", "t" * MIN_WEBHOOK_TOKEN_CHARS)
        from app.ai.models import AlertEvent
        from app.alerts.models import AlertAction
        from app.tasks import celery_app
        from app.tasks.alert_handling import _handle
        from tests.conftest import create_host

        host = await create_host(db)
        db.add(
            AlertAction(
                action_key="example",
                target_kind="fleet",
                target_id=None,
                parameters={"marker_dir": "/tmp/x"},
                snapshot_enabled=False,
                cooldown_minutes=60,
                enabled=True,
            )
        )
        event = AlertEvent(
            source="grafana_webhook",
            fingerprint=uuid.uuid4().hex,
            alertname="NginxDown",
            severity="critical",
            status="firing",
            # unbork_ai is ignored without the assistant.
            labels={"alertname": "NginxDown", "unbork_action": "example", "unbork_ai": "true"},
            annotations={},
            starts_at=datetime.now(UTC),
            host_id=host.id,
        )
        db.add(event)
        await db.flush()

        with patch.object(celery_app, "send_task") as sent:
            result = await _handle(event.id)

        assert result["outcome"] == "action"
        run_id = result["actions"][0]["action_run_id"]
        sent.assert_called_once_with("app.tasks.action_orchestrator.run_action", args=[run_id])
        await db.refresh(event)
        assert event.investigation_outcome == "handled_by_action"

    async def test_an_alert_without_an_action_is_only_recorded(
        self, db, tasks_use_test_db, assistant_off
    ):
        from app.ai.models import AlertEvent
        from app.tasks.alert_handling import _handle

        event = AlertEvent(
            source="grafana_webhook",
            fingerprint=uuid.uuid4().hex,
            alertname="DiskFull",
            severity="warning",
            status="firing",
            labels={"alertname": "DiskFull", "unbork_ai": "true"},
            annotations={},
            starts_at=datetime.now(UTC),
            host_id=None,
        )
        db.add(event)
        await db.flush()

        with patch("app.tasks.ai_alerts._decide_and_run") as decide:
            result = await _handle(event.id)
        assert result["outcome"] == "no_assistant"
        decide.assert_not_called()


async def test_ai_verify_is_inconclusive_without_the_assistant(db, assistant_off):
    from app.ai.verify import run_verify_session

    outcome = await run_verify_session(
        db, hostname="h", ip="10.0.0.1", instructions="", evidence=[], fail_closed=True
    )
    assert outcome.verdict == "inconclusive"
    assert outcome.passed is False
    assert "not part of this install" in outcome.detail


async def test_a_rollback_left_running_does_not_hold_the_host(db, assistant_off):
    """Only the assistant's sweep closes a stale rollback; without it the
    row must not block every later op on the host."""
    from app.ai.models import AIRollback
    from app.tasks.host_lock import check_host_busy
    from tests.conftest import create_host

    host = await create_host(db)
    db.add(
        AIRollback(
            host_id=host.id,
            hostname=host.hostname,
            trigger="automatic",
            status="running",
            started_at=datetime.now(UTC),
        )
    )
    await db.flush()
    assert await check_host_busy(db, host.id) is None
    settings.assistant.enabled = True
    try:
        busy = await check_host_busy(db, host.id)
    finally:
        settings.assistant.enabled = False
    assert busy is not None and busy.kind == "ai_rollback"
