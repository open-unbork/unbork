"""S-017: an extension can limit which AI provider types an install accepts.

A stand-in extension, loaded through the real plugin loader, refuses
``claude_cli``. Community itself sets no policy.
"""

from __future__ import annotations

import logging
import uuid
from importlib.metadata import EntryPoint

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.ai import service
from app.ai.models import AIProvider
from app.ai.providers.policy import (
    FAILED_POLICY_MESSAGE,
    provider_type_refusal,
    set_provider_type_policy,
)
from app.ai.service import AIDisabledError
from app.auth.permissions import PLUGIN_API
from app.models.app_setting import AppSetting
from app.plugins import is_compatible, load_plugins
from app.settings_service import invalidate_cache

REASON = "This install does not offer the single-shot Claude provider."


def _policy(provider_type: str) -> str | None:
    return REASON if provider_type == "claude_cli" else None


def register(app):
    set_provider_type_policy(_policy)


register.requires_plugin_api = "1.7"


def _raising_policy(provider_type: str) -> str | None:
    raise RuntimeError("policy backend down")


@pytest.fixture(autouse=True)
def _no_policy():
    set_provider_type_policy(None)
    yield
    set_provider_type_policy(None)


@pytest.fixture
def stand_in_extension():
    ep = EntryPoint(
        name="provider-policy",
        value="tests.ai.test_provider_type_policy:register",
        group="unbork.plugins",
    )
    loaded = load_plugins(FastAPI(), [ep])
    assert [p.name for p in loaded] == ["provider-policy"]


async def _set(db, key: str, value: str) -> None:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.flush()
    invalidate_cache(key)


@pytest.fixture
async def cli_provider(db):
    """A claude_cli provider stored before the policy was set."""
    provider = AIProvider(
        name=f"cli-{uuid.uuid4().hex[:8]}",
        provider_type="claude_cli",
        model="claude-opus-5",
        max_tokens=4096,
        temperature=0.0,
        is_default=True,
        enabled=True,
    )
    db.add(provider)
    await db.flush()
    return provider


def _body(provider_type: str, **extra) -> dict:
    return {
        "name": f"p-{uuid.uuid4().hex[:8]}",
        "provider_type": provider_type,
        "model": "m",
        **extra,
    }


class TestTheModule:
    def test_no_policy_allows_everything(self):
        for t in ("openai_compat", "anthropic", "claude_cli", "claude_agent"):
            assert provider_type_refusal(t) is None

    def test_a_raising_policy_refuses_with_the_fixed_message(self, caplog):
        set_provider_type_policy(_raising_policy)
        with caplog.at_level(logging.ERROR, logger="app.ai.providers.policy"):
            assert provider_type_refusal("openai_compat") == FAILED_POLICY_MESSAGE
        assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1

    def test_the_contract_version(self):
        major, minor = (int(x) for x in PLUGIN_API.split("."))
        assert (major, minor) >= (1, 7)
        assert is_compatible("1.2")


@pytest.mark.usefixtures("stand_in_extension")
class TestTheApi:
    async def test_create_is_refused_for_the_refused_type(self, superuser_client):
        resp = await superuser_client.post("/api/ai/providers", json=_body("claude_cli"))
        assert resp.status_code == 400
        assert resp.json()["detail"] == REASON

    async def test_create_works_for_an_allowed_type(self, superuser_client):
        resp = await superuser_client.post(
            "/api/ai/providers",
            json=_body("openai_compat", base_url="http://localhost:11434/v1"),
        )
        assert resp.status_code == 201
        assert resp.json()["refused_reason"] is None

    async def test_a_stored_refused_provider(self, superuser_client, cli_provider):
        listed = await superuser_client.get("/api/ai/providers")
        row = next(p for p in listed.json() if p["id"] == cli_provider.id)
        assert row["refused_reason"] == REASON

        tested = await superuser_client.post(f"/api/ai/providers/{cli_provider.id}/test")
        assert tested.json() == {"ok": False, "message": REASON}

        renamed = await superuser_client.patch(
            f"/api/ai/providers/{cli_provider.id}", json={"name": "still-cli"}
        )
        assert renamed.status_code == 400

        moved = await superuser_client.patch(
            f"/api/ai/providers/{cli_provider.id}",
            json={"provider_type": "openai_compat", "base_url": "http://localhost:11434/v1"},
        )
        assert moved.status_code == 200
        assert moved.json()["refused_reason"] is None

        deleted = await superuser_client.delete(f"/api/ai/providers/{cli_provider.id}")
        assert deleted.status_code == 204

    async def test_provider_types_lists_every_type(self, superuser_client):
        resp = await superuser_client.get("/api/ai/provider-types")
        assert resp.status_code == 200
        rows = {r["type"]: r for r in resp.json()}
        assert set(rows) == {"openai_compat", "anthropic", "claude_cli", "claude_agent"}
        assert rows["claude_cli"] == {"type": "claude_cli", "allowed": False, "reason": REASON}
        assert all(r["allowed"] for t, r in rows.items() if t != "claude_cli")

    async def test_a_session_on_the_refused_provider(self, db, cli_provider):
        await _set(db, "ai.enabled", "1")
        await _set(db, "ai.allow_cloud_providers", "1")
        try:
            with pytest.raises(AIDisabledError, match=REASON):
                await service.resolve_provider(db, cli_provider.id)
            with pytest.raises(AIDisabledError, match=REASON):
                await service.resolve_provider(db, None)
        finally:
            invalidate_cache()


async def test_without_a_policy_nothing_is_refused(superuser_client, cli_provider):
    listed = await superuser_client.get("/api/ai/providers")
    assert all(p["refused_reason"] is None for p in listed.json())
    types = await superuser_client.get("/api/ai/provider-types")
    assert all(r["allowed"] for r in types.json())
