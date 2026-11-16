"""C-047 / BUG-147: firewall rule and chain policy changes are audited.

Create, update and delete on a group and on a host each write one
``firewall_rule`` row naming the caller; a reorder that moves a priority
writes one ``reorder`` row; a policy change writes one ``firewall_policy``
row. Requests that fail write nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from tests.conftest import create_group, create_host, create_rule

pytestmark = pytest.mark.integration

RULE = {"action": "allow", "protocol": "tcp", "direction": "input", "port_start": 22}


async def _me(client) -> int:
    return (await client.get("/api/users/me")).json()["id"]


async def _rows(db, entity_type: str = "firewall_rule") -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.entity_type == entity_type).order_by(AuditLog.id)
    return list((await db.execute(stmt)).scalars().all())


@pytest.fixture(params=["group", "host"])
async def scope(request, db):
    if request.param == "group":
        owner = await create_group(db)
        base = f"/api/groups/{owner.id}/rules"
    else:
        owner = await create_host(db)
        base = f"/api/hosts/{owner.id}/firewall-rules"
    await db.commit()
    return request.param, owner.id, base


async def test_create_writes_one_row(superuser_client, db, scope):
    kind, owner_id, base = scope
    user_id = await _me(superuser_client)

    resp = await superuser_client.post(base, json=RULE)
    assert resp.status_code == 201, resp.text
    rule = resp.json()

    (row,) = await _rows(db)
    assert (row.action, row.entity_id, row.user_id) == ("create", rule["id"], user_id)
    assert row.before_state is None
    assert row.after_state[f"{kind}_id"] == owner_id
    assert row.after_state["port_start"] == 22
    assert row.after_state["action"] == "allow"


async def test_update_writes_one_row_with_both_states(superuser_client, db, scope):
    _, _, base = scope
    rule = (await superuser_client.post(base, json=RULE)).json()
    user_id = await _me(superuser_client)

    resp = await superuser_client.put(f"{base}/{rule['id']}", json={"port_start": 2222})
    assert resp.status_code == 200, resp.text
    # The response is the rule, as before.
    assert resp.json()["port_start"] == 2222

    rows = [r for r in await _rows(db) if r.action == "update"]
    assert len(rows) == 1
    row = rows[0]
    assert (row.entity_id, row.user_id) == (rule["id"], user_id)
    assert row.before_state["port_start"] == 22
    assert row.after_state["port_start"] == 2222


async def test_delete_keeps_the_rule_in_before_state(superuser_client, db, scope):
    _, _, base = scope
    rule = (await superuser_client.post(base, json=RULE)).json()

    resp = await superuser_client.delete(f"{base}/{rule['id']}")
    assert resp.status_code == 204
    assert resp.content == b""

    rows = [r for r in await _rows(db) if r.action == "delete"]
    assert len(rows) == 1
    assert rows[0].entity_id == rule["id"]
    assert rows[0].before_state["port_start"] == 22
    assert rows[0].after_state is None


async def test_failed_requests_write_nothing(superuser_client, db, scope):
    _, _, base = scope

    missing = await superuser_client.put(f"{base}/999999", json={"port_start": 1})
    assert missing.status_code == 404
    gone = await superuser_client.delete(f"{base}/999999")
    assert gone.status_code == 404
    bad = await superuser_client.post(base, json={**RULE, "protocol": "icmp"})
    assert bad.status_code == 400
    bad_ref = await superuser_client.post(base, json={**RULE, "source_host_id": 999999})
    assert bad_ref.status_code == 400

    assert await _rows(db) == []


async def test_reorder_writes_one_row(superuser_client, db):
    group = await create_group(db)
    a = await create_rule(db, group.id, priority=0, port_start=22)
    b = await create_rule(db, group.id, priority=1, port_start=80)
    await db.commit()
    user_id = await _me(superuser_client)

    # Index 0 is the highest priority: put a above b.
    resp = await superuser_client.put(
        f"/api/groups/{group.id}/rules/reorder", json={"rule_ids": [a.id, b.id]}
    )
    assert resp.status_code == 200
    assert resp.json() == {"reordered": 2}

    (row,) = await _rows(db)
    assert (row.action, row.entity_id, row.user_id) == ("reorder", None, user_id)
    assert row.before_state == {
        "group_id": group.id,
        "priorities": [{"id": b.id, "priority": 1}, {"id": a.id, "priority": 0}],
    }
    assert row.after_state == {
        "group_id": group.id,
        "priorities": [{"id": b.id, "priority": 0}, {"id": a.id, "priority": 1}],
    }


async def test_reorder_that_changes_nothing_writes_nothing(superuser_client, db):
    group = await create_group(db)
    a = await create_rule(db, group.id, priority=1, port_start=22)
    b = await create_rule(db, group.id, priority=0, port_start=80)
    await db.commit()

    resp = await superuser_client.put(
        f"/api/groups/{group.id}/rules/reorder", json={"rule_ids": [999999, a.id, b.id]}
    )
    assert resp.status_code == 200

    assert await _rows(db) == []


async def test_policy_change_writes_one_row(superuser_client, db):
    group = await create_group(db)
    await db.commit()
    user_id = await _me(superuser_client)

    resp = await superuser_client.put(
        f"/api/groups/{group.id}/policies",
        json={"input_policy": "accept", "output_policy": "drop"},
    )
    assert resp.status_code == 200
    assert (resp.json()["input"], resp.json()["output"]) == ("accept", "drop")

    (row,) = await _rows(db, "firewall_policy")
    assert (row.action, row.entity_id, row.user_id) == ("update", group.id, user_id)
    assert row.before_state == {"input": None, "output": None}
    assert row.after_state == {"input": "accept", "output": "drop"}


async def test_gitops_locked_group_writes_nothing(superuser_client, db):
    group = await create_group(db)
    rule = await create_rule(db, group.id, port_start=22)
    group.gitops_enabled = True
    await db.commit()
    base = f"/api/groups/{group.id}/rules"

    responses = [
        await superuser_client.post(base, json=RULE),
        await superuser_client.put(f"{base}/{rule.id}", json={"port_start": 1}),
        await superuser_client.delete(f"{base}/{rule.id}"),
        await superuser_client.put(f"{base}/reorder", json={"rule_ids": [rule.id]}),
        await superuser_client.put(
            f"/api/groups/{group.id}/policies", json={"input_policy": "accept"}
        ),
    ]
    assert all(r.status_code in (403, 409) for r in responses), [r.status_code for r in responses]
    assert await _rows(db) == []
    assert await _rows(db, "firewall_policy") == []
