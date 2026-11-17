"""The events a user can subscribe to.

Kept in one table so the API, the subscription check and the UI all list
the same set. Adding an event means adding it here and calling
:func:`app.notifications.service.notify` where it happens; nobody is
subscribed to it until they choose to be.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventType:
    key: str
    label: str
    #: Shown beside the checkbox. Says when it fires, which is what a
    #: person deciding whether to want it needs to know.
    description: str
    #: Only raised by the assistant, so not offered when it is left out
    #: (C-026).
    assistant: bool = False


EVENT_TYPES: tuple[EventType, ...] = (
    EventType(
        "alert_fired",
        "Alert fired",
        "A firing alert arrives from Grafana or Alertmanager.",
    ),
    EventType(
        "approval_requested",
        "Approval requested",
        "The assistant wants to change a host and is waiting for someone to approve it.",
        assistant=True,
    ),
    EventType(
        "approval_expiring",
        "Approval about to expire",
        "A change is still waiting for a decision a few hours before its request expires.",
        assistant=True,
    ),
    EventType(
        "approval_expired",
        "Approval expired",
        "Nobody decided in time, so the session finished without the change.",
        assistant=True,
    ),
    EventType(
        "alert_action",
        "Actions run by alerts",
        "An alert's unbork_action label ran an action on a host: whether it fixed the alert, "
        "and whether the alert was handed to the AI.",
    ),
    EventType(
        "alert_remediation",
        "Automatic fixes",
        "A full-auto alert investigation changed a host — what ran, and the snapshot taken — "
        "and, once unbork has checked, whether it worked or was rolled back.",
        assistant=True,
    ),
)

EVENT_KEYS = frozenset(e.key for e in EVENT_TYPES)


def offered_event_types() -> tuple[EventType, ...]:
    """The events this server can raise: without the assistant's (C-026)."""
    from app.features import assistant_enabled

    if assistant_enabled():
        return EVENT_TYPES
    return tuple(e for e in EVENT_TYPES if not e.assistant)
