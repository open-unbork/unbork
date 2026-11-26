"""C-022: the alert webhook's source allowlist and body signature.

Both are off until ``[alerts]`` sets them. With them on, a request from a
source that is not listed gets 403, and one without a valid HMAC-SHA256 of
its body (hex, over ``<timestamp>:<body>`` when a timestamp header is
configured, as Grafana's webhook contact point signs) gets 401. Neither
records anything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.ai.models import AlertEvent
from app.config import AlertsConfig, settings

pytestmark = pytest.mark.integration

TOKEN = "t" * 40
SECRET = "s3cret-hmac-key"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _payload(name: str = "DiskFull") -> dict:
    return {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": name, "instance": "unknown-host"},
                "annotations": {},
                "startsAt": "2026-11-16T10:00:00Z",
                "fingerprint": f"fp-{name}",
            }
        ],
    }


def _sign(body: bytes, timestamp: str | None = None, secret: str = SECRET) -> str:
    signed = body if timestamp is None else timestamp.encode() + b":" + body
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def alerts_config(monkeypatch):
    monkeypatch.setattr(settings.alerts, "webhook_token", TOKEN)
    monkeypatch.setattr(settings.alerts, "allowed_sources", [])
    monkeypatch.setattr(settings.alerts, "webhook_hmac_secret", "")
    monkeypatch.setattr(settings.alerts, "webhook_hmac_header", "X-Grafana-Alerting-Signature")
    monkeypatch.setattr(settings.alerts, "webhook_hmac_timestamp_header", "")
    monkeypatch.setattr(settings.alerts, "webhook_hmac_max_skew_seconds", 300)
    monkeypatch.setattr(settings.server, "trusted_proxies", [])


@pytest.fixture(autouse=True)
async def intake_on(db):
    from app.models.app_setting import AppSetting
    from app.settings_service import invalidate_cache

    existing = (
        await db.execute(select(AppSetting).where(AppSetting.key == "alerts.intake_enabled"))
    ).scalar_one_or_none()
    if existing:
        existing.value = "1"
    else:
        db.add(AppSetting(key="alerts.intake_enabled", value="1"))
    await db.flush()
    invalidate_cache()
    with patch("app.api.webhooks.celery_app.send_task"):
        yield
    invalidate_cache()


@pytest.fixture
def peer(app):
    """A client whose TCP peer is the given address, with no CSRF token
    (a webhook sender has none)."""

    def _make(address: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(address, 4321)),
            base_url="http://testserver",
        )

    return _make


async def _post(client: httpx.AsyncClient, body: bytes, headers: dict | None = None):
    async with client:
        return await client.post(
            "/api/webhooks/grafana-alerts",
            content=body,
            headers={"Content-Type": "application/json", **AUTH, **(headers or {})},
        )


async def _recorded(db) -> int:
    return await db.scalar(select(func.count()).select_from(AlertEvent))


# ---------------------------------------------------------------------------
# Source allowlist
# ---------------------------------------------------------------------------


async def test_a_source_outside_the_list_is_refused(db, peer, monkeypatch):
    monkeypatch.setattr(settings.alerts, "allowed_sources", ["10.0.0.0/8"])
    before = await _recorded(db)

    resp = await _post(peer("192.0.2.1"), json.dumps(_payload()).encode())

    assert resp.status_code == 403
    assert resp.json() == {"detail": "Source address not allowed"}
    assert await _recorded(db) == before


async def test_a_listed_source_is_accepted(db, peer, monkeypatch):
    monkeypatch.setattr(settings.alerts, "allowed_sources", ["10.0.0.0/8", "192.0.2.7"])

    resp = await _post(peer("10.1.2.3"), json.dumps(_payload()).encode())
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "accepted"

    resp = await _post(peer("192.0.2.7"), json.dumps(_payload("Other")).encode())
    assert resp.status_code == 200, resp.text


async def test_the_source_is_checked_before_the_token(peer, monkeypatch):
    monkeypatch.setattr(settings.alerts, "allowed_sources", ["10.0.0.0/8"])
    async with peer("192.0.2.1") as client:
        resp = await client.post("/api/webhooks/grafana-alerts", json=_payload())
    assert resp.status_code == 403


async def test_behind_a_trusted_proxy_the_forwarded_address_is_checked(peer, monkeypatch):
    monkeypatch.setattr(settings.alerts, "allowed_sources", ["10.0.0.0/8"])
    monkeypatch.setattr(settings.server, "trusted_proxies", ["172.18.0.0/16"])
    body = json.dumps(_payload()).encode()

    allowed = await _post(peer("172.18.0.5"), body, {"X-Forwarded-For": "10.9.9.9"})
    assert allowed.status_code == 200, allowed.text

    refused = await _post(peer("172.18.0.5"), body, {"X-Forwarded-For": "192.0.2.1"})
    assert refused.status_code == 403


async def test_an_untrusted_peer_cannot_forward_its_way_in(peer, monkeypatch):
    monkeypatch.setattr(settings.alerts, "allowed_sources", ["10.0.0.0/8"])
    monkeypatch.setattr(settings.server, "trusted_proxies", ["172.18.0.0/16"])

    resp = await _post(
        peer("192.0.2.1"), json.dumps(_payload()).encode(), {"X-Forwarded-For": "10.9.9.9"}
    )
    assert resp.status_code == 403


def test_an_unparsable_source_stops_startup():
    with pytest.raises(ValidationError, match="10.0.0/88"):
        AlertsConfig(allowed_sources=["10.0.0.0/8", "10.0.0/88"])


# ---------------------------------------------------------------------------
# Body signature
# ---------------------------------------------------------------------------


async def test_a_correct_signature_is_accepted(peer, monkeypatch):
    monkeypatch.setattr(settings.alerts, "webhook_hmac_secret", SECRET)
    body = json.dumps(_payload()).encode()

    resp = await _post(peer("10.0.0.1"), body, {"X-Grafana-Alerting-Signature": _sign(body)})

    assert resp.status_code == 200, resp.text
    assert resp.json()["recorded"] == 1


@pytest.mark.parametrize("case", ["missing", "wrong_secret", "other_body", "garbage"])
async def test_a_bad_signature_is_refused(db, peer, monkeypatch, case):
    monkeypatch.setattr(settings.alerts, "webhook_hmac_secret", SECRET)
    body = json.dumps(_payload()).encode()
    headers = {
        "missing": {},
        "wrong_secret": {"X-Grafana-Alerting-Signature": _sign(body, secret="other")},
        "other_body": {
            "X-Grafana-Alerting-Signature": _sign(json.dumps(_payload("Other")).encode())
        },
        "garbage": {"X-Grafana-Alerting-Signature": "not-hex"},
    }[case]
    before = await _recorded(db)

    resp = await _post(peer("10.0.0.1"), body, headers)

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Invalid or missing signature"}
    assert await _recorded(db) == before


async def test_the_token_is_still_required_with_a_signature(peer, monkeypatch):
    monkeypatch.setattr(settings.alerts, "webhook_hmac_secret", SECRET)
    body = json.dumps(_payload()).encode()
    async with peer("10.0.0.1") as client:
        resp = await client.post(
            "/api/webhooks/grafana-alerts",
            content=body,
            headers={"X-Grafana-Alerting-Signature": _sign(body)},
        )
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Invalid or missing webhook token"}


async def test_a_custom_header_name_is_used(peer, monkeypatch):
    monkeypatch.setattr(settings.alerts, "webhook_hmac_secret", SECRET)
    monkeypatch.setattr(settings.alerts, "webhook_hmac_header", "X-Sig")
    body = json.dumps(_payload()).encode()

    resp = await _post(peer("10.0.0.1"), body, {"X-Sig": _sign(body)})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Timestamp
# ---------------------------------------------------------------------------


@pytest.fixture
def with_timestamp(monkeypatch):
    monkeypatch.setattr(settings.alerts, "webhook_hmac_secret", SECRET)
    monkeypatch.setattr(
        settings.alerts, "webhook_hmac_timestamp_header", "X-Grafana-Alerting-Timestamp"
    )


async def test_a_fresh_timestamped_signature_is_accepted(peer, with_timestamp):
    body = json.dumps(_payload()).encode()
    stamp = str(int(time.time()))
    resp = await _post(
        peer("10.0.0.1"),
        body,
        {
            "X-Grafana-Alerting-Timestamp": stamp,
            "X-Grafana-Alerting-Signature": _sign(body, stamp),
        },
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("offset", [-400, 400])
async def test_a_timestamp_outside_the_skew_is_refused(peer, with_timestamp, offset):
    body = json.dumps(_payload()).encode()
    stamp = str(int(time.time()) + offset)
    resp = await _post(
        peer("10.0.0.1"),
        body,
        {
            "X-Grafana-Alerting-Timestamp": stamp,
            "X-Grafana-Alerting-Signature": _sign(body, stamp),
        },
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("case", ["no_timestamp_in_signature", "missing", "not_numeric"])
async def test_a_bad_timestamp_is_refused(peer, with_timestamp, case):
    body = json.dumps(_payload()).encode()
    stamp = str(int(time.time()))
    headers = {
        "no_timestamp_in_signature": {
            "X-Grafana-Alerting-Timestamp": stamp,
            "X-Grafana-Alerting-Signature": _sign(body),
        },
        "missing": {"X-Grafana-Alerting-Signature": _sign(body, stamp)},
        "not_numeric": {
            "X-Grafana-Alerting-Timestamp": "soon",
            "X-Grafana-Alerting-Signature": _sign(body, "soon"),
        },
    }[case]
    resp = await _post(peer("10.0.0.1"), body, headers)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Off by default, logging, intake status
# ---------------------------------------------------------------------------


async def test_with_nothing_set_any_source_and_no_signature_pass(peer):
    resp = await _post(peer("198.51.100.4"), json.dumps(_payload()).encode())
    assert resp.status_code == 200, resp.text


async def test_a_refusal_logs_the_address_and_no_secret(peer, monkeypatch, caplog):
    monkeypatch.setattr(settings.alerts, "webhook_hmac_secret", SECRET)
    body = json.dumps(_payload("LeakCheck")).encode()
    bad = _sign(body, secret="other")

    with caplog.at_level(logging.WARNING, logger="app.api.webhooks"):
        resp = await _post(peer("10.4.4.4"), body, {"X-Grafana-Alerting-Signature": bad})
    assert resp.status_code == 401

    lines = [r.getMessage() for r in caplog.records if r.name == "app.api.webhooks"]
    assert len(lines) == 1
    assert "10.4.4.4" in lines[0]
    for secret in (TOKEN, SECRET, bad, "LeakCheck"):
        assert secret not in lines[0]


async def test_the_intake_status_reports_both_locks(regular_user_client, monkeypatch):
    data = (await regular_user_client.get("/api/alerts/intake")).json()
    assert (data["source_allowlist"], data["body_signature"]) == (False, False)

    monkeypatch.setattr(settings.alerts, "allowed_sources", ["10.0.0.0/8"])
    monkeypatch.setattr(settings.alerts, "webhook_hmac_secret", SECRET)
    data = (await regular_user_client.get("/api/alerts/intake")).json()
    assert (data["source_allowlist"], data["body_signature"]) == (True, True)


# ---------------------------------------------------------------------------
# Review fixes (stage 1)
# ---------------------------------------------------------------------------


async def test_an_ipv4_client_on_a_dual_stack_listener_matches_its_range(db, peer, monkeypatch):
    monkeypatch.setattr(settings.alerts, "allowed_sources", ["10.0.0.0/8"])
    resp = await _post(peer("::ffff:10.1.2.3"), json.dumps(_payload()).encode())
    assert resp.status_code != 403


async def test_a_non_ascii_signature_is_a_401_not_a_500(db, peer, monkeypatch):
    monkeypatch.setattr(settings.alerts, "webhook_hmac_secret", SECRET)
    body = json.dumps(_payload()).encode()
    resp = await _post(
        peer("10.1.2.3"), body, {"X-Grafana-Alerting-Signature": ("é" * 64).encode("latin-1")}
    )
    assert resp.status_code == 401


async def test_a_non_ascii_digit_timestamp_is_a_401_not_a_500(db, peer, monkeypatch):
    monkeypatch.setattr(settings.alerts, "webhook_hmac_secret", SECRET)
    monkeypatch.setattr(
        settings.alerts, "webhook_hmac_timestamp_header", "X-Grafana-Alerting-Timestamp"
    )
    body = json.dumps(_payload()).encode()
    resp = await _post(
        peer("10.1.2.3"),
        body,
        {
            "X-Grafana-Alerting-Signature": _sign(body),
            "X-Grafana-Alerting-Timestamp": "²".encode("latin-1"),
        },
    )
    assert resp.status_code == 401
