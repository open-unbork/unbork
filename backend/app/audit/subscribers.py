"""Extension hook: hear about audit entries once they are committed.

An extension calls :func:`register_audit_subscriber` from its
``register(app)``. From then on every row :func:`app.audit.log_action`
writes is handed to each subscriber as an :class:`AuditEntry`, after the
transaction that holds it commits, in the API process and in Celery
workers alike (extensions load in both). A row that is rolled back, with
its transaction or with a savepoint, is never delivered.

The promise, and its limits:

* **After commit, at most once.** Delivery runs from the session's
  ``after_commit`` hook. If the process dies between the commit and the
  hook, that entry is not delivered. ``AuditEntry.id`` is the
  ``audit_log`` primary key, so a consumer that needs every entry keeps
  the highest id it has handled and reads later rows from the table when
  it starts.
* **Synchronous and quick.** A subscriber is called inside the commit of
  the request or task that wrote the row, so it must only hand the entry
  on (a queue, a Celery task, a thread) and return. It must not use the
  session that just committed.
* **Isolated.** A subscriber that raises is logged and skipped; the next
  subscriber still runs and the request or task is unaffected.
* **Whole entries.** ``before_state`` and ``after_state`` are delivered as
  stored (deep copies, so a subscriber cannot change them). Redacting
  them for an outside system is the consumer's job.

With no subscriber registered, :func:`log_action` registers no callback,
so a Community install does no extra work.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditEntry:
    """One committed ``audit_log`` row, as an extension sees it."""

    id: int
    created_at: datetime
    user_id: int | None
    action: str
    entity_type: str
    entity_id: int | None
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    ip_address: str | None


#: Called with each committed entry. Must return quickly: see the module
#: docstring.
AuditSubscriber = Callable[[AuditEntry], None]

_subscribers: list[AuditSubscriber] = []


def register_audit_subscriber(fn: AuditSubscriber) -> None:
    """Hand every committed audit entry to ``fn`` from now on.

    Registering the same callable twice is a no-op, since extensions load
    in more than one place per process in some layouts.
    """
    if fn not in _subscribers:
        _subscribers.append(fn)


def reset_audit_subscribers() -> None:
    """Forget every subscriber. For tests."""
    _subscribers.clear()


def has_audit_subscribers() -> bool:
    return bool(_subscribers)


def _name(fn: Callable) -> str:
    return getattr(fn, "__qualname__", None) or repr(fn)


def deliver(entry: AuditEntry) -> None:
    """Call every subscriber with ``entry``, each in its own ``try``."""
    for fn in list(_subscribers):
        try:
            fn(entry)
        except Exception:
            logger.exception("audit subscriber %s failed on entry %s", _name(fn), entry.id)


def _deliver_committed(row: Any) -> None:
    """The after-commit callback :func:`log_action` registers for ``row``.

    Skips a row that is not stored: one added inside a savepoint that was
    rolled back is transient again even though the outer transaction
    committed. Reads only attributes ``log_action`` set plus the id the
    flush filled in, never a lazy load or a query: the session is in the
    middle of its commit.
    """
    from sqlalchemy import inspect

    state = inspect(row)
    if not state.persistent:
        return
    loaded = state.dict
    row_id = loaded.get("id")
    if row_id is None:
        return
    entry = AuditEntry(
        id=row_id,
        created_at=loaded.get("created_at"),
        user_id=loaded.get("user_id"),
        action=loaded.get("action"),
        entity_type=loaded.get("entity_type"),
        entity_id=loaded.get("entity_id"),
        before_state=copy.deepcopy(loaded.get("before_state")),
        after_state=copy.deepcopy(loaded.get("after_state")),
        ip_address=loaded.get("ip_address"),
    )
    deliver(entry)
