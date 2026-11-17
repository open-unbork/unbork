import hashlib
import hmac
import ipaddress
import json
import logging
import time
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.client_ip import get_client_ip
from app.config import settings
from app.db import get_db
from app.gitops.webhook_secret import get_webhook_secret
from app.models.git_repository import GitRepository
from app.models.host_group import HostGroup
from app.tasks import celery_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_github_signature(
    body: bytes,
    secret: str,
    signature_header: str | None,
) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = (
        "sha256="
        + hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
    )
    return hmac.compare_digest(expected, signature_header)


def _verify_gitea_signature(
    body: bytes,
    secret: str,
    signature_header: str | None,
) -> bool:
    if not signature_header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _verify_gitlab_token(secret: str, token_header: str | None) -> bool:
    if not token_header:
        return False
    return hmac.compare_digest(secret, token_header)


async def _find_repos_by_url(url: str, db: AsyncSession) -> list[GitRepository]:
    """Every repository row for *url*, with or without a ``.git`` suffix.

    BUG-141: two rows for one URL (two branches of one repository is a
    normal setup) made ``scalar_one_or_none`` raise, and every delivery for
    that repository answered 500. Callers pick the row by branch.
    """
    if not url:
        return []
    alt_url = url.removesuffix(".git") if url.endswith(".git") else url + ".git"
    result = await db.execute(
        select(GitRepository)
        .where(GitRepository.url.in_([url, alt_url]))
        .order_by(GitRepository.id)
    )
    return list(result.scalars().all())


def _authenticated(
    repos: list[GitRepository], check: Callable[[str], bool], *, what: str
) -> list[GitRepository]:
    """The rows whose own webhook secret verifies this request.

    Each row has its own secret, so each is checked; 401 when none does.
    """
    # SEC-28: ciphertext on the row; decrypted only to compare against
    # the signature on this request and never returned anywhere.
    secrets = [(repo, get_webhook_secret(repo)) for repo in repos]
    if not any(secret for _, secret in secrets):
        raise HTTPException(status_code=401, detail="Webhook secret not configured")
    verified = [repo for repo, secret in secrets if secret and check(secret)]
    if not verified:
        raise HTTPException(status_code=401, detail=f"Invalid {what}")
    return verified


def _on_branch(repos: list[GitRepository], ref: str) -> list[GitRepository]:
    return [repo for repo in repos if ref == f"refs/heads/{repo.branch}"]


async def _dispatch_webhook(
    repo: GitRepository,
    commit_sha: str,
    db: AsyncSession,
) -> None:
    """Find all groups linked to this repo with gitops enabled and dispatch.

    SEC-48: *commit_sha* is only logged and passed along as a hint. The
    task imports the tip of the branch it fetches, so a replayed delivery
    cannot roll the groups back to the older commit it names.
    """
    result = await db.execute(
        select(HostGroup).where(
            HostGroup.git_repository_id == repo.id,
            HostGroup.gitops_enabled == True,  # noqa: E712
        ),
    )
    groups = result.scalars().all()
    if not groups:
        logger.info(f"Webhook for repo {repo.name}: no gitops-enabled groups")
        return

    celery_app.send_task(
        "gitops.process_webhook",
        kwargs={"repo_id": repo.id, "commit_sha": commit_sha},
    )
    logger.info(
        f"Dispatched gitops webhook task for repo {repo.name}, SHA {commit_sha[:8]}",
    )


async def _dispatch_all(repos: list[GitRepository], commit_sha: str, db: AsyncSession) -> None:
    for repo in repos:
        await _dispatch_webhook(repo, commit_sha, db)


@router.post("/github")
async def github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await request.body()
    payload = await request.json()

    # Find repo by URL
    repo_url = payload.get("repository", {}).get("clone_url", "") or payload.get(
        "repository",
        {},
    ).get("ssh_url", "")
    repos = await _find_repos_by_url(repo_url, db)
    if not repos:
        return {"status": "ignored", "reason": "unknown repository"}

    sig = request.headers.get("X-Hub-Signature-256")
    repos = _authenticated(
        repos, lambda secret: _verify_github_signature(body, secret, sig), what="signature"
    )

    # Check event type
    event = request.headers.get("X-GitHub-Event", "")
    if event != "push":
        return {"status": "ignored", "reason": f"event type: {event}"}

    # Check for branch deletion
    ref = payload.get("ref", "")
    after = payload.get("after", "")
    deleted = payload.get("deleted", False)
    if deleted or after == "0" * 40:
        return {"status": "ignored", "reason": "branch deletion"}

    # Check branch matches
    matching = _on_branch(repos, ref)
    if not matching:
        return {"status": "ignored", "reason": f"branch mismatch: {ref}"}

    await _dispatch_all(matching, after, db)
    return {"status": "accepted"}


@router.post("/gitlab")
async def gitlab_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    payload = await request.json()

    repo_url = payload.get("project", {}).get(
        "git_http_url",
        "",
    ) or payload.get("project", {}).get("git_ssh_url", "")
    repos = await _find_repos_by_url(repo_url, db)
    if not repos:
        return {"status": "ignored", "reason": "unknown repository"}

    token = request.headers.get("X-Gitlab-Token")
    repos = _authenticated(repos, lambda secret: _verify_gitlab_token(secret, token), what="token")

    # Check for branch deletion
    after = payload.get("checkout_sha") or payload.get("after", "")
    if not after or after == "0" * 40:
        return {"status": "ignored", "reason": "branch deletion"}

    ref = payload.get("ref", "")
    matching = _on_branch(repos, ref)
    if not matching:
        return {"status": "ignored", "reason": f"branch mismatch: {ref}"}

    await _dispatch_all(matching, after, db)
    return {"status": "accepted"}


@router.post("/gitea")
async def gitea_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await request.body()
    payload = await request.json()

    repo_url = payload.get("repository", {}).get("clone_url", "") or payload.get(
        "repository",
        {},
    ).get("ssh_url", "")
    repos = await _find_repos_by_url(repo_url, db)
    if not repos:
        return {"status": "ignored", "reason": "unknown repository"}

    sig = request.headers.get("X-Gitea-Signature")
    repos = _authenticated(
        repos, lambda secret: _verify_gitea_signature(body, secret, sig), what="signature"
    )

    ref = payload.get("ref", "")
    after = payload.get("after", "")
    if after == "0" * 40:
        return {"status": "ignored", "reason": "branch deletion"}

    matching = _on_branch(repos, ref)
    if not matching:
        return {"status": "ignored", "reason": f"branch mismatch: {ref}"}

    await _dispatch_all(matching, after, db)
    return {"status": "accepted"}


def _authorized(request: Request) -> bool:
    """Constant-time check of the shared alert-webhook token.

    Grafana contact points can send either an ``Authorization: Bearer``
    header or HTTP basic auth; both are accepted, because which one is
    available depends on the Grafana version and how the contact point
    was created. An unset token refuses everything — see
    :class:`app.config.AlertsConfig`.
    """
    expected = settings.alerts.webhook_token
    if not expected:
        return False

    header = request.headers.get("Authorization") or ""
    if header.startswith("Bearer "):
        return hmac.compare_digest(expected, header[len("Bearer ") :])
    if header.startswith("Basic "):
        import base64

        try:
            decoded = base64.b64decode(header[len("Basic ") :]).decode()
        except Exception:
            return False
        # Grafana sends user:password; the token may be either half, so
        # that an operator can put it wherever their contact-point form
        # makes available.
        user, _, password = decoded.partition(":")
        return hmac.compare_digest(expected, password) or hmac.compare_digest(expected, user)
    return False


def _source_allowed(client_ip: str) -> bool:
    """Whether ``client_ip`` is in ``alerts.allowed_sources`` (empty: any)."""
    allowed = settings.alerts.allowed_sources
    if not allowed:
        return True
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    # A dual-stack listener sees IPv4 clients as ::ffff:a.b.c.d.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in ipaddress.ip_network(entry, strict=False) for entry in allowed)


def _signature_valid(request: Request, body: bytes) -> bool:
    """Check the HMAC-SHA256 of ``body`` the way a Grafana webhook contact
    point with HMAC signing computes it (grafana/alerting ``http/hmac.go``):
    hex-encoded, over ``<timestamp>:<body>`` when a timestamp header is
    configured and over the body alone otherwise."""
    cfg = settings.alerts
    signature = request.headers.get(cfg.webhook_hmac_header) or ""
    if not signature:
        return False
    signed = body
    if cfg.webhook_hmac_timestamp_header:
        stamp = (request.headers.get(cfg.webhook_hmac_timestamp_header) or "").strip()
        if not (stamp.isascii() and stamp.isdigit()):
            return False
        if abs(time.time() - int(stamp)) > cfg.webhook_hmac_max_skew_seconds:
            return False
        signed = stamp.encode() + b":" + body
    expected = hmac.new(cfg.webhook_hmac_secret.encode(), signed, hashlib.sha256).hexdigest()
    # Bytes, not str: compare_digest refuses a str with non-ASCII in it,
    # and a header can carry any latin-1 character.
    given = signature.strip().lower().encode("latin-1", errors="replace")
    return hmac.compare_digest(expected.encode(), given)


@router.post("/grafana-alerts")
async def grafana_alerts_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive alerts from a Grafana contact point.

    Records every alert in the payload and returns immediately. Deciding
    what each one sets off — the action its unbork_action label names,
    an AI investigation, or nothing — happens in a Celery
    task, per alert — Grafana retries a webhook that does not answer
    quickly, and doing policy work inline would turn a slow provider into
    duplicate notifications.

    Returns 200 with a count even when nothing was eligible. A webhook
    that returns an error for "recorded, decided not to investigate"
    teaches the sender to retry something that already succeeded.
    """
    from app.ai.alerts import from_grafana_webhook, record
    from app.settings_service import get_setting_typed

    # Checked in this order, before anything touches the database: the
    # source, then the token, and only then is the body read for the
    # signature, so an unauthenticated sender cannot make the server
    # buffer a body (C-022).
    client_ip = get_client_ip(request)
    if not _source_allowed(client_ip):
        logger.warning("grafana-alerts: refused %s: source address not allowed", client_ip)
        raise HTTPException(status_code=403, detail="Source address not allowed")

    if not _authorized(request):
        logger.warning("grafana-alerts: refused %s: invalid or missing token", client_ip)
        raise HTTPException(status_code=401, detail="Invalid or missing webhook token")

    body = await request.body()
    if settings.alerts.webhook_hmac_secret and not _signature_valid(request, body):
        logger.warning("grafana-alerts: refused %s: invalid or missing signature", client_ip)
        raise HTTPException(status_code=401, detail="Invalid or missing signature")

    if not int(await get_setting_typed("alerts.intake_enabled", db)):
        return {"status": "ignored", "reason": "alert intake is disabled"}

    # Parse the bytes that were verified, so the body checked is the body used.
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Body is not JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")

    alerts = from_grafana_webhook(payload)
    if not alerts:
        # A payload unbork could not parse into a single usable alert is
        # worth saying out loud — silently accepting it would look like
        # working while nothing was recorded.
        logger.info("grafana-alerts: payload contained no usable alerts")
        return {"status": "accepted", "recorded": 0, "investigating": 0}

    from app.notifications.service import notify_alert_fired

    new_ids: list[int] = []
    for alert in alerts:
        event, created = await record(db, alert, source="grafana_webhook")
        if created and alert.is_firing:
            new_ids.append(event.id)
            await notify_alert_fired(db, event)
    await db.commit()

    for event_id in new_ids:
        celery_app.send_task(
            "app.tasks.alert_handling.handle_alert", kwargs={"alert_event_id": event_id}
        )

    logger.info(
        "grafana-alerts: recorded %d alert(s), %d new firing",
        len(alerts),
        len(new_ids),
    )
    return {"status": "accepted", "recorded": len(alerts), "investigating": len(new_ids)}
