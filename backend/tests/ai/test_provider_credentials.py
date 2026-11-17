"""S-014: the program-backed provider types take no credential.

"Claude agent" and "Claude single-shot" use the Claude Code sign-in of the
service account on the server (D-04). The API refuses a token for them; an
empty string still clears one an earlier version stored.
"""

from __future__ import annotations

import uuid

import pytest

from app.ai.models import AIProvider
from app.crypto.encryption import encrypt_ssh_key
from app.crypto.key_management import get_master_key

PROGRAM_BACKED = ("claude_agent", "claude_cli")


def _name() -> str:
    return f"p-{uuid.uuid4().hex[:8]}"


@pytest.mark.parametrize("provider_type", PROGRAM_BACKED)
async def test_create_with_a_token_is_refused(superuser_client, provider_type) -> None:
    resp = await superuser_client.post(
        "/api/ai/providers",
        json={
            "name": _name(),
            "provider_type": provider_type,
            "model": "claude-opus-5",
            "api_key": "sk-ant-oat01-x",
        },
    )
    assert resp.status_code == 422
    assert "Sign in to Claude Code on the server" in resp.json()["detail"]


@pytest.mark.parametrize("provider_type", PROGRAM_BACKED)
async def test_create_without_a_token_works(superuser_client, provider_type) -> None:
    resp = await superuser_client.post(
        "/api/ai/providers",
        json={"name": _name(), "provider_type": provider_type, "model": "claude-opus-5"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["has_api_key"] is False


@pytest.fixture
async def legacy_provider(db):
    """A program-backed provider holding a token saved by an earlier version."""
    provider = AIProvider(
        name=_name(),
        provider_type="claude_agent",
        model="",
        max_tokens=4096,
        temperature=0.0,
        enabled=True,
        encrypted_api_key=encrypt_ssh_key("sk-ant-oat01-old", get_master_key()),
    )
    db.add(provider)
    await db.flush()
    return provider


async def test_update_with_a_token_is_refused(superuser_client, legacy_provider) -> None:
    resp = await superuser_client.patch(
        f"/api/ai/providers/{legacy_provider.id}", json={"api_key": "sk-ant-oat01-new"}
    )
    assert resp.status_code == 422


async def test_clearing_a_stored_token_works(superuser_client, db, legacy_provider) -> None:
    resp = await superuser_client.patch(
        f"/api/ai/providers/{legacy_provider.id}", json={"api_key": ""}
    )
    assert resp.status_code == 200
    assert resp.json()["has_api_key"] is False
    await db.refresh(legacy_provider)
    assert legacy_provider.encrypted_api_key is None
    assert legacy_provider.credential_set_at is None


async def test_anthropic_still_requires_a_key(superuser_client) -> None:
    resp = await superuser_client.post(
        "/api/ai/providers", json={"name": _name(), "provider_type": "anthropic", "model": "m"}
    )
    assert resp.status_code == 400
