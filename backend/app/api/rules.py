from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._gitops_lock import check_gitops_lock
from app.api._scoped_rules import (
    RuleAudit,
    commit_created,
    commit_updated,
    delete_and_commit,
    require_group,
    require_host,
    scoped_row_or_404,
)
from app.audit.logger import log_action
from app.auth.permissions import require
from app.db import get_db
from app.models.firewall_rule import FirewallRule
from app.models.host import Host
from app.models.user import User
from app.rules.merge import (
    get_effective_policies as _merge_get_effective_policies,
)
from app.rules.merge import (
    get_effective_rules as _merge_get_effective_rules,
)
from app.schemas.groups import GroupPoliciesUpdate
from app.schemas.rules import (
    ChainPoliciesResponse,
    EffectiveRuleResponse,
    RuleCreate,
    RuleReorder,
    RuleResponse,
    RuleUpdate,
)

router = APIRouter(tags=["rules"])

_NOT_FOUND = "Rule not found"


def _audit_state(rule: FirewallRule) -> dict:
    """Every operator-set field of a rule, plus where it lives."""
    return {
        "group_id": rule.group_id,
        "host_id": rule.host_id,
        "action": str(rule.action) if rule.action is not None else None,
        "protocol": str(rule.protocol) if rule.protocol is not None else None,
        "direction": str(rule.direction) if rule.direction is not None else None,
        "source_cidr": rule.source_cidr,
        "destination_cidr": rule.destination_cidr,
        "source_host_id": rule.source_host_id,
        "destination_host_id": rule.destination_host_id,
        "port_start": rule.port_start,
        "port_end": rule.port_end,
        "priority": rule.priority,
        "comment": rule.comment,
    }


_AUDIT = RuleAudit("firewall_rule", _audit_state)


@router.get("/groups/{group_id}/rules", response_model=list[RuleResponse])
async def list_rules(
    group_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FirewallRule)
        .where(FirewallRule.group_id == group_id)
        .order_by(FirewallRule.priority.desc(), FirewallRule.id)
    )
    return result.scalars().all()


async def _require_hosts_exist(db: AsyncSession, ids: list[int]) -> None:
    if not ids:
        return
    rows = await db.execute(select(Host.id).where(Host.id.in_(ids)))
    found = {r[0] for r in rows.all()}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"Referenced host(s) not found: {missing}")


async def _validate_host_refs(db: AsyncSession, body) -> None:
    ids = [i for i in (body.source_host_id, body.destination_host_id) if i is not None]
    await _require_hosts_exist(db, ids)


def _check_ports(body: RuleCreate) -> None:
    if body.protocol == "icmp" and body.port_start is not None:
        raise HTTPException(status_code=400, detail="ICMP rules cannot specify ports")
    # Validate port range
    if body.port_start and body.port_end and body.port_end < body.port_start:
        raise HTTPException(status_code=400, detail="port_end must be >= port_start")


@router.post("/groups/{group_id}/rules", response_model=RuleResponse, status_code=201)
async def create_rule(
    group_id: int,
    body: RuleCreate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    _check_ports(body)
    await _validate_host_refs(db, body)
    rule = FirewallRule(group_id=group_id, **body.model_dump())
    db.add(rule)
    await db.flush()
    return await commit_created(db, rule, _AUDIT, user)


@router.put("/groups/{group_id}/rules/reorder", status_code=200)
async def reorder_rules(
    group_id: int,
    body: RuleReorder,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    before: list[dict] = []
    after: list[dict] = []
    for idx, rule_id in enumerate(reversed(body.rule_ids)):
        result = await db.execute(
            select(FirewallRule).where(
                FirewallRule.id == rule_id,
                FirewallRule.group_id == group_id,
            )
        )
        rule = result.scalar_one_or_none()
        if rule:
            before.append({"id": rule.id, "priority": rule.priority})
            after.append({"id": rule.id, "priority": idx})
            rule.priority = idx
    # One row per reorder, and none when no priority moved. A list of
    # objects, not a map: JSON keys are strings and the ids stay numbers.
    if before != after:
        await log_action(
            db,
            action="reorder",
            entity_type="firewall_rule",
            entity_id=None,
            user_id=user.id,
            before_state={"group_id": group_id, "priorities": before},
            after_state={"group_id": group_id, "priorities": after},
        )
    await db.commit()
    return {"reordered": len(body.rule_ids)}


@router.put("/groups/{group_id}/rules/{rule_id}", response_model=RuleResponse)
async def update_rule(
    group_id: int,
    rule_id: int,
    body: RuleUpdate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    rule = await scoped_row_or_404(
        db, FirewallRule, rule_id, FirewallRule.group_id == group_id, detail=_NOT_FOUND
    )
    before = _audit_state(rule)
    await _apply_rule_update(db, rule, body)
    await db.flush()
    return await commit_updated(db, rule, _AUDIT, user, before)


async def _apply_rule_update(db: AsyncSession, rule: FirewallRule, body: RuleUpdate) -> None:
    """Apply a RuleUpdate to an ORM rule, handling cidr↔host_id side swaps."""
    data = body.model_dump(exclude_unset=True)
    # If the update touches one side of source or destination, ensure the
    # opposite half is cleared so the "xor" CHECK still holds.
    if "source_cidr" in data and data.get("source_cidr"):
        data.setdefault("source_host_id", None)
    if "source_host_id" in data and data.get("source_host_id") is not None:
        data.setdefault("source_cidr", None)
    if "destination_cidr" in data and data.get("destination_cidr"):
        data.setdefault("destination_host_id", None)
    if "destination_host_id" in data and data.get("destination_host_id") is not None:
        data.setdefault("destination_cidr", None)

    host_ids = [
        i for i in (data.get("source_host_id"), data.get("destination_host_id")) if i is not None
    ]
    await _require_hosts_exist(db, host_ids)

    for field, value in data.items():
        setattr(rule, field, value)


@router.delete("/groups/{group_id}/rules/{rule_id}", status_code=204)
async def delete_rule(
    group_id: int,
    rule_id: int,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    rule = await scoped_row_or_404(
        db, FirewallRule, rule_id, FirewallRule.group_id == group_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, rule, _AUDIT, user)


@router.get("/hosts/{host_id}/firewall-rules", response_model=list[RuleResponse])
async def list_host_rules(
    host_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    """List host-level firewall rule overrides."""
    result = await db.execute(
        select(FirewallRule)
        .where(FirewallRule.host_id == host_id)
        .order_by(FirewallRule.priority.desc(), FirewallRule.id)
    )
    return result.scalars().all()


@router.post("/hosts/{host_id}/firewall-rules", response_model=RuleResponse, status_code=201)
async def create_host_rule(
    host_id: int,
    body: RuleCreate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    """Create a host-level firewall rule override."""
    await require_host(db, host_id)
    _check_ports(body)
    await _validate_host_refs(db, body)
    rule = FirewallRule(host_id=host_id, **body.model_dump())
    db.add(rule)
    await db.flush()
    return await commit_created(db, rule, _AUDIT, user)


@router.put("/hosts/{host_id}/firewall-rules/{rule_id}", response_model=RuleResponse)
async def update_host_rule(
    host_id: int,
    rule_id: int,
    body: RuleUpdate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    """Update a host-level firewall rule override."""
    rule = await scoped_row_or_404(
        db, FirewallRule, rule_id, FirewallRule.host_id == host_id, detail=_NOT_FOUND
    )
    before = _audit_state(rule)
    await _apply_rule_update(db, rule, body)
    await db.flush()
    return await commit_updated(db, rule, _AUDIT, user, before)


@router.delete("/hosts/{host_id}/firewall-rules/{rule_id}", status_code=204)
async def delete_host_rule(
    host_id: int,
    rule_id: int,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a host-level firewall rule override."""
    rule = await scoped_row_or_404(
        db, FirewallRule, rule_id, FirewallRule.host_id == host_id, detail=_NOT_FOUND
    )
    await delete_and_commit(db, rule, _AUDIT, user)


@router.get("/hosts/{host_id}/effective-rules", response_model=list[EffectiveRuleResponse])
async def effective_rules_endpoint(
    host_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    """Get merged ruleset for a host (all groups, priority-merged, with SSH lockout rule)."""
    return await _merge_get_effective_rules(host_id, db)


@router.get("/groups/{group_id}/policies", response_model=ChainPoliciesResponse)
async def get_group_policies(
    group_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    group = await require_group(db, group_id)
    return ChainPoliciesResponse(
        input=group.input_policy or "drop",
        output=group.output_policy or "accept",
    )


@router.put("/groups/{group_id}/policies", response_model=ChainPoliciesResponse)
async def update_group_policies(
    group_id: int,
    body: GroupPoliciesUpdate,
    user: User = Depends(require("config.edit")),
    db: AsyncSession = Depends(get_db),
):
    await check_gitops_lock(group_id, db)
    group = await require_group(db, group_id)
    before = {"input": group.input_policy, "output": group.output_policy}
    group.input_policy = body.input_policy
    group.output_policy = body.output_policy
    after = {"input": group.input_policy, "output": group.output_policy}
    # Like a reorder that moves nothing: no change, no row.
    if after != before:
        await log_action(
            db,
            action="update",
            entity_type="firewall_policy",
            entity_id=group_id,
            user_id=user.id,
            before_state=before,
            after_state=after,
        )
    await db.commit()
    await db.refresh(group)
    return ChainPoliciesResponse(
        input=group.input_policy or "drop",
        output=group.output_policy or "accept",
    )


@router.get("/hosts/{host_id}/effective-policies", response_model=ChainPoliciesResponse)
async def effective_policies_endpoint(
    host_id: int,
    _: User = Depends(require("hosts.view")),
    db: AsyncSession = Depends(get_db),
):
    """Get merged chain policies for a host (all groups, priority-merged)."""
    return await _merge_get_effective_policies(host_id, db)
