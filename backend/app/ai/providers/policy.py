"""Which AI provider types this install accepts (S-017, PLUGIN_API 1.7).

An extension calls :func:`set_provider_type_policy` from ``register(app)``
with a function that takes a provider type and returns ``None`` to allow
it or a sentence for the operator to refuse it. Community sets no policy
and refuses nothing.

A refused type cannot be created, tested or used: the API refuses it on
create, update and test, :func:`app.ai.service.resolve_provider` refuses it
for every session, investigation, alert and verify step, and the provider
form does not offer it. A provider of that type stored earlier is never
deleted, disabled or converted; it shows the reason until an operator
changes its type or deletes it.

A policy that raises refuses every type, so a broken policy never widens
what is accepted. Extensions load in the API process and in each Celery
process, so a policy set in ``register(app)`` is present wherever a
session starts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

ProviderTypePolicy = Callable[[str], str | None]

FAILED_POLICY_MESSAGE = (
    "This provider type is not available: the install's provider policy failed. See the server log."
)

_policy: ProviderTypePolicy | None = None


def set_provider_type_policy(policy: ProviderTypePolicy | None) -> None:
    """Install ``policy``, replacing any earlier one. ``None`` allows every type."""
    global _policy
    _policy = policy


def provider_type_refusal(provider_type: str) -> str | None:
    """``None`` when ``provider_type`` is allowed, else why it is refused."""
    policy = _policy
    if policy is None:
        return None
    try:
        reason = policy(provider_type)
    except Exception:
        logger.exception("AI provider type policy failed for %r; refusing it", provider_type)
        return FAILED_POLICY_MESSAGE
    if reason is None:
        return None
    return str(reason).strip() or FAILED_POLICY_MESSAGE
