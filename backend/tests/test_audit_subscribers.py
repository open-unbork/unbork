"""S-001: extensions hear about audit entries once they are committed.

A subscriber gets one ``AuditEntry`` per committed ``log_action`` row, none
for a row whose transaction or savepoint rolled back, and a subscriber
that raises never breaks the request or the next subscriber.
"""

from __future__ import annotations

import dataclasses
import logging

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.audit import (
    AuditEntry,
    log_action,
    register_audit_subscriber,
    reset_audit_subscribers,
)
from app.auth.permissions import PLUGIN_API
from app.db_after_commit import _KEY
from app.models.audit_log import AuditLog
from app.plugins import is_compatible

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean():
    reset_audit_subscribers()
    yield
    reset_audit_subscribers()


@pytest.fixture
def received():
    entries: list[AuditEntry] = []
    register_audit_subscriber(entries.append)
    return entries


async def test_a_committed_entry_is_delivered_once(db, received):
    await log_action(
        db,
        action="create",
        entity_type="thing",
        entity_id=7,
        before_state=None,
        after_state={"a": 1},
        ip_address="10.0.0.1",
    )
    assert received == []  # nothing before the commit
    await db.commit()

    row = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "thing"))).scalar_one()
    assert len(received) == 1
    entry = received[0]
    assert entry.id == row.id
    assert (entry.action, entry.entity_type, entry.entity_id) == ("create", "thing", 7)
    assert entry.after_state == {"a": 1}
    assert entry.before_state is None
    assert entry.ip_address == "10.0.0.1"
    assert entry.created_at == row.created_at
    assert entry.user_id is None

    # A later commit does not deliver it again.
    await db.commit()
    assert len(received) == 1


async def test_a_rolled_back_entry_is_not_delivered(db, received):
    await log_action(db, action="create", entity_type="thing")
    await db.flush()
    await db.rollback()
    await db.commit()
    assert received == []


async def test_a_rolled_back_savepoint_is_not_delivered(db, received):
    await log_action(db, action="outside", entity_type="thing")
    nested = await db.begin_nested()
    await log_action(db, action="inside", entity_type="thing")
    await db.flush()
    await nested.rollback()
    await log_action(db, action="after", entity_type="thing")
    await db.commit()

    assert [e.action for e in received] == ["outside", "after"]


async def test_a_raising_subscriber_does_not_stop_the_next(db, caplog):
    second: list[AuditEntry] = []

    def broken(entry: AuditEntry) -> None:
        raise RuntimeError("boom")

    register_audit_subscriber(broken)
    register_audit_subscriber(second.append)

    with caplog.at_level(logging.ERROR, logger="app.audit.subscribers"):
        await log_action(db, action="create", entity_type="thing")
        await db.commit()

    assert len(second) == 1
    errors = [r for r in caplog.records if r.name == "app.audit.subscribers"]
    assert len(errors) == 1
    assert "broken" in errors[0].getMessage()


async def test_a_raising_subscriber_does_not_break_a_request(superuser_client, db):
    def broken(entry: AuditEntry) -> None:
        raise RuntimeError("boom")

    register_audit_subscriber(broken)
    resp = await superuser_client.post(
        "/api/groups", json={"name": "subscriber-req", "priority": 971}
    )
    assert resp.status_code == 201, resp.text


async def test_an_entry_cannot_change_the_stored_row(db):
    seen: list[AuditEntry] = []

    def meddle(entry: AuditEntry) -> None:
        entry.after_state["port"] = 9999
        seen.append(entry)

    register_audit_subscriber(meddle)
    await log_action(db, action="create", entity_type="thing", after_state={"port": 22})
    await db.commit()

    db.expire_all()
    row = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "thing"))).scalar_one()
    assert row.after_state == {"port": 22}
    with pytest.raises(dataclasses.FrozenInstanceError):
        seen[0].action = "delete"  # type: ignore[misc]


async def test_an_api_call_delivers_through_get_db(superuser_client, db, received):
    resp = await superuser_client.post(
        "/api/groups", json={"name": "subscriber-api", "priority": 972}
    )
    assert resp.status_code == 201, resp.text

    creates = [e for e in received if e.entity_type == "host_group" and e.action == "create"]
    assert len(creates) == 1
    assert creates[0].entity_id == resp.json()["id"]
    assert creates[0].user_id is not None


async def test_no_subscriber_means_no_callback(db):
    await log_action(db, action="create", entity_type="thing")
    assert not db.sync_session.info.get(_KEY)


def test_the_contract_version():
    major, minor = (int(x) for x in PLUGIN_API.split("."))
    assert (major, minor) >= (1, 3)
    assert is_compatible("1.2")
    assert is_compatible("1.3")


async def test_a_plugin_can_register_a_subscriber(db):
    """The hook as an extension uses it, through the real loader."""
    from importlib.metadata import EntryPoint

    from app.plugins import load_plugins

    loaded = load_plugins(
        FastAPI(),
        [
            EntryPoint(
                name="standin",
                value="tests.test_audit_subscribers:_standin",
                group="unbork.plugins",
            )
        ],
    )
    assert [p.name for p in loaded] == ["standin"]

    await log_action(db, action="create", entity_type="thing")
    await db.commit()
    assert [e.action for e in _STANDIN_SEEN] == ["create"]


_STANDIN_SEEN: list[AuditEntry] = []


def _standin(app: FastAPI) -> None:
    _STANDIN_SEEN.clear()
    register_audit_subscriber(_STANDIN_SEEN.append)


_standin.requires_plugin_api = "1.3"
