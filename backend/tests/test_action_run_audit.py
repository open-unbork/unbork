"""C-008: every action run leaves one ``action_run.started`` audit row.

Four places create an ``ActionRun`` (the API, Run now, the scheduler and an
alert label). Each writes the row in the transaction that stores the run,
naming who or what started it, and cancelling a run writes
``action_run.cancelled``. Parameter values never reach the log.

The last test is a guard: it walks ``app/`` and fails when a function builds
an ``ActionRun`` without calling ``audit_run_started``.
"""

from __future__ import annotations

import ast
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.actions.run_audit import audit_run_started
from app.models.action_run import ActionRun
from app.models.audit_log import AuditLog
from app.models.scheduled_action import ScheduledAction
from app.models.user import User
from tests.conftest import create_host

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("sample_pack")]

APP_DIR = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture(autouse=True)
def stub_celery_dispatch():
    with patch("celery.app.base.Celery.send_task"):
        yield


@pytest.fixture
def tasks_use_test_db(db):
    @asynccontextmanager
    async def _cm():
        yield db

    with patch("app.db.task_session", new=_cm):
        yield


async def _rows(db, action: str, entity_id: int | None = None) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.action == action)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    return list((await db.execute(stmt.order_by(AuditLog.id))).scalars().all())


async def _me(client) -> int:
    resp = await client.get("/api/users/me")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _make_user(db) -> User:
    user = User(
        email=f"owner_{uuid.uuid4().hex[:8]}@test.com",
        hashed_password="x",
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    return user


# ---------------------------------------------------------------------------
# The four creators
# ---------------------------------------------------------------------------


async def test_api_run_writes_one_started_row(superuser_client, db):
    host = await create_host(db)
    await db.commit()
    user_id = await _me(superuser_client)

    resp = await superuser_client.post(
        "/api/actions/runs",
        json={
            "action_key": "example",
            "host_id": host.id,
            "parameters": {"marker_dir": "/srv/secret-path"},
        },
    )
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["id"]

    rows = await _rows(db, "action_run.started")
    assert len(rows) == 1
    row = rows[0]
    assert (row.entity_type, row.entity_id, row.user_id) == ("action_run", run_id, user_id)
    state = row.after_state
    assert state["origin"] == "api"
    assert state["action_key"] == "example"
    assert (state["target_kind"], state["host_id"], state["group_id"]) == ("host", host.id, None)
    assert state["target_label"] == host.hostname
    assert state["scheduled_action_id"] is None
    assert state["alert_event_id"] is None
    assert state["dry_run"] is False
    assert "marker_dir" in state["parameter_names"]
    # No parameter value anywhere in the row.
    assert "/srv/secret-path" not in repr(row.after_state)
    assert row.before_state is None


async def test_dry_run_is_recorded_without_the_internal_parameter(superuser_client, db):
    host = await create_host(db)
    await db.commit()

    resp = await superuser_client.post(
        "/api/actions/runs",
        json={"action_key": "example", "host_id": host.id, "dry_run": True},
    )
    if resp.status_code == 422:
        pytest.skip("this action does not take a dry run")
    assert resp.status_code == 201, resp.text

    (row,) = await _rows(db, "action_run.started")
    assert row.after_state["dry_run"] is True
    assert "__dry_run" not in row.after_state["parameter_names"]


async def test_run_now_writes_one_started_row(superuser_client, db):
    host = await create_host(db)
    await db.commit()
    user_id = await _me(superuser_client)
    created = await superuser_client.post(
        "/api/scheduled-actions",
        json={
            "target_kind": "host",
            "target_id": host.id,
            "action_key": "linux-upgrade",
            "schedule_cron": "0 3 * * 0",
        },
    )
    sa_id = created.json()["id"]

    resp = await superuser_client.post(f"/api/scheduled-actions/{sa_id}/run-now")
    assert resp.status_code == 201, resp.text

    (row,) = await _rows(db, "action_run.started")
    assert row.entity_id == resp.json()["id"]
    assert row.user_id == user_id
    assert row.after_state["origin"] == "run_now"
    assert row.after_state["scheduled_action_id"] == sa_id
    # The schedule's own row stays.
    assert len(await _rows(db, "scheduled_action.dispatched", sa_id)) == 1


async def test_schedule_tick_writes_one_started_row_for_the_owner(db, tasks_use_test_db):
    from app.tasks.scheduled_action_schedule import _check_due_async

    owner = await _make_user(db)
    host = await create_host(db)
    sa = ScheduledAction(
        target_kind="host",
        target_id=host.id,
        action_key="_builtin.collect_state",
        schedule_cron="* * * * *",
        enabled=True,
        owner_user_id=owner.id,
        last_dispatched_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    db.add(sa)
    await db.commit()

    result = await _check_due_async()
    assert result["dispatched"] == 1

    run = (
        await db.execute(select(ActionRun).where(ActionRun.scheduled_action_id == sa.id))
    ).scalar_one()
    (row,) = await _rows(db, "action_run.started")
    assert row.entity_id == run.id
    assert row.user_id == owner.id
    assert row.after_state["origin"] == "schedule"
    assert row.after_state["scheduled_action_id"] == sa.id
    assert len(await _rows(db, "scheduled_action.dispatched", sa.id)) == 1


async def test_alert_run_writes_one_started_row_with_no_user(db, monkeypatch):
    from app.ai.alert_autonomy import MIN_WEBHOOK_TOKEN_CHARS
    from app.ai.models import AlertEvent
    from app.alerts.labels import dispatch_for
    from app.alerts.models import AlertAction
    from app.config import settings

    # Alert-started actions need a strong webhook token.
    monkeypatch.setattr(settings.alerts, "webhook_token", "t" * MIN_WEBHOOK_TOKEN_CHARS)
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
        labels={"alertname": "NginxDown", "labdog_action": "example"},
        annotations={},
        starts_at=datetime.now(UTC),
        host_id=host.id,
    )
    db.add(event)
    await db.flush()

    dispatch = await dispatch_for(db, event)
    assert dispatch.outcome == "started"

    (row,) = await _rows(db, "action_run.started")
    assert row.entity_id == dispatch.action_run_id
    assert row.user_id is None
    assert row.after_state["origin"] == "alert"
    assert row.after_state["alert_event_id"] == event.id
    assert "/tmp/x" not in repr(row.after_state)


async def test_assistant_origin_carries_the_session(db):
    host = await create_host(db)
    user = await _make_user(db)
    run = ActionRun(
        action_key="example",
        action_version="1.0",
        host_id=host.id,
        target_kind="host",
        target_label=host.hostname,
        parameters={},
        parallelism=1,
        status="queued",
        triggered_by_user_id=user.id,
    )
    db.add(run)
    await db.flush()

    await audit_run_started(db, run, origin="assistant", user_id=user.id, ai_session_id=42)
    await db.flush()

    (row,) = await _rows(db, "action_run.started")
    assert row.after_state["origin"] == "assistant"
    assert row.after_state["ai_session_id"] == 42
    assert row.user_id == user.id


async def test_unflushed_run_is_refused(db):
    run = ActionRun(action_key="example", action_version="1.0", parameters={}, status="queued")
    with pytest.raises(ValueError, match="flushed"):
        await audit_run_started(db, run, origin="api", user_id=None)


# ---------------------------------------------------------------------------
# Refused requests
# ---------------------------------------------------------------------------


async def test_refused_requests_write_no_row(superuser_client, regular_user_client, db):
    host = await create_host(db)
    await db.commit()

    unknown = await superuser_client.post(
        "/api/actions/runs", json={"action_key": "no-such-action", "host_id": host.id}
    )
    assert unknown.status_code == 400

    first = await superuser_client.post(
        "/api/actions/runs", json={"action_key": "example", "host_id": host.id}
    )
    assert first.status_code == 201, first.text
    duplicate = await superuser_client.post(
        "/api/actions/runs", json={"action_key": "example", "host_id": host.id}
    )
    assert duplicate.status_code == 409

    # Only the one accepted request is audited.
    assert len(await _rows(db, "action_run.started")) == 1


async def test_forbidden_request_writes_no_row(app, db):
    """A viewer may not start a run, and nothing is audited."""
    from fastapi_users.password import PasswordHelper

    from tests.conftest import _CsrfAutoTransport

    host = await create_host(db)
    email = f"viewer_{uuid.uuid4().hex[:8]}@test.com"
    db.add(
        User(
            email=email,
            hashed_password=PasswordHelper().hash("TestPass1!"),
            is_active=True,
            is_superuser=False,
            role="viewer",
            is_verified=True,
        )
    )
    await db.commit()
    import httpx

    async with httpx.AsyncClient(
        transport=_CsrfAutoTransport(app=app), base_url="http://testserver"
    ) as viewer:
        login = await viewer.post(
            "/api/auth/jwt/login", data={"username": email, "password": "TestPass1!"}
        )
        assert login.status_code in (200, 204)
        resp = await viewer.post(
            "/api/actions/runs", json={"action_key": "example", "host_id": host.id}
        )
    assert resp.status_code == 403
    assert await _rows(db, "action_run.started") == []


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


async def _cancel(client, run_id):
    with patch("redis.from_url", return_value=MagicMock()):
        resp = await client.post(f"/api/actions/runs/{run_id}/cancel")
    assert resp.status_code == 200, resp.text


async def _queued_run(db, status="queued") -> int:
    host = await create_host(db)
    run = ActionRun(
        action_key="example",
        action_version="1.0",
        host_id=host.id,
        parameters={},
        parallelism=1,
        status=status,
    )
    db.add(run)
    await db.flush()
    run_id = run.id
    await db.commit()
    return run_id


async def test_cancelling_a_queued_run_writes_one_row(superuser_client, db):
    run_id = await _queued_run(db)
    user_id = await _me(superuser_client)

    await _cancel(superuser_client, run_id)

    (row,) = await _rows(db, "action_run.cancelled", run_id)
    assert row.entity_type == "action_run"
    assert row.user_id == user_id
    assert row.before_state == {"status": "queued"}
    assert row.after_state == {"status": "cancelled"}


async def test_cancelling_a_finished_run_writes_nothing(superuser_client, db):
    run_id = await _queued_run(db, status="succeeded")

    await _cancel(superuser_client, run_id)

    assert await _rows(db, "action_run.cancelled") == []


# ---------------------------------------------------------------------------
# Guard: every creator audits
# ---------------------------------------------------------------------------


def _creators_without_audit(root: Path) -> list[str]:
    """Functions under ``root`` that build ``ActionRun(...)`` and never call
    ``audit_run_started`` in the same function body."""
    missing: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            builds = audits = False
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                name = (
                    inner.func.id
                    if isinstance(inner.func, ast.Name)
                    else inner.func.attr
                    if isinstance(inner.func, ast.Attribute)
                    else None
                )
                if name == "ActionRun":
                    builds = True
                elif name == "audit_run_started":
                    audits = True
            if builds and not audits:
                missing.append(f"{path.relative_to(root.parent)}:{node.lineno} {node.name}")
    return missing


def test_every_action_run_creator_audits():
    missing = _creators_without_audit(APP_DIR)
    assert missing == [], (
        "These functions create an ActionRun without calling "
        "app.actions.run_audit.audit_run_started in the same function:\n" + "\n".join(missing)
    )


def test_the_guard_finds_the_four_creators():
    """The guard is only worth something if it sees the creators."""
    builders = set()
    for path in APP_DIR.rglob("*.py"):
        if "ActionRun(" in path.read_text():
            builders.add(path.relative_to(APP_DIR).as_posix())
    assert {
        "api/actions.py",
        "api/scheduled_actions.py",
        "tasks/scheduled_action_schedule.py",
        "alerts/labels.py",
    } <= builders


def test_the_guard_fails_without_the_call(tmp_path):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "creator.py").write_text(
        "async def make(db):\n    run = ActionRun(action_key='x')\n    db.add(run)\n"
    )
    (pkg / "fine.py").write_text(
        "async def make(db):\n"
        "    run = ActionRun(action_key='x')\n"
        "    await audit_run_started(db, run, origin='api', user_id=None)\n"
    )
    assert _creators_without_audit(pkg) == ["app/creator.py:1 make"]
