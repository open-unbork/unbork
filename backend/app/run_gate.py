"""Extension hook: gate a run before it starts, and hear when it ends.

An extension calls :func:`register_run_gate` from its ``register(app)`` to
be asked, just before work starts, whether it may: a sync (and a CA
certificate deploy), an action run, a schedule's dispatch, or a change the
assistant is about to make. The gate answers with a :class:`Verdict`:

* ``allow``: the work starts as it would without the gate.
* ``refuse``: the work does not start. A sync or action run ends ``failed``
  with ``"Refused: <reason>"``; a schedule skips that fire and audits it;
  the assistant is told the reason instead of running the command.
* ``defer``: the work waits. A sync or action run stays ``pending`` with
  the reason, *held* until ``retry_at`` (or a minute), and does not block
  the host's queue meanwhile; :mod:`app.tasks.run_gate_retry` sends it
  again then, and the gate is asked again. A schedule stays due and is
  asked again on the next tick. A full-auto assistant session parks the
  change for a human; at any other autonomy level it is refused.

Gates are asked in registration order and the first answer that is not
``allow`` wins. A gate receives the open session and may read through it;
it must not write or commit. A gate that raises refuses the run ("run gate
failed") and is logged: failing closed is the safe direction, and removing
the extension restores Community behaviour.

:func:`register_run_end_listener` is told when each of those ends (a sync,
an action run, an assistant session that made a change) with a
:class:`RunEnded`, after the transaction that wrote the end commits, and
never for one that was rolled back. Listeners are synchronous and must
only hand the event on, like audit subscribers (:mod:`app.audit.subscribers`).

Gates and listeners see ids, not rows, and read what they need themselves.
With nothing registered, :func:`check_run` returns at once without a
query, and no end event is collected.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session, SessionTransaction

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

RunKind = Literal["sync", "action_run", "schedule_dispatch", "ai_fix"]
Outcome = Literal["allow", "refuse", "defer"]

#: How long a deferred sync or action run is held when the gate names no time.
DEFAULT_HOLD_SECONDS = 60
#: The shortest hold, whatever ``retry_at`` the gate gives: a hold that has
#: already passed would make the row a queue candidate again at once.
MIN_HOLD_SECONDS = 10


@dataclass(frozen=True)
class RunRequest:
    """What is about to start."""

    kind: RunKind
    host_ids: tuple[int, ...] = ()
    group_id: int | None = None
    action_key: str | None = None
    modules: tuple[str, ...] = ()
    user_id: int | None = None
    sync_job_id: int | None = None
    action_run_id: int | None = None
    scheduled_action_id: int | None = None
    ai_session_id: int | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class Verdict:
    """A gate's answer."""

    outcome: Outcome
    reason: str = ""
    retry_at: datetime | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"


ALLOW = Verdict("allow")


def refuse(reason: str) -> Verdict:
    return Verdict("refuse", reason)


def defer(reason: str, retry_at: datetime | None = None) -> Verdict:
    return Verdict("defer", reason, retry_at)


@dataclass(frozen=True)
class RunEnded:
    """Something a gate could have been asked about has finished."""

    kind: Literal["sync", "action_run", "ai_fix"]
    status: str
    finished_at: datetime | None = None
    host_ids: tuple[int, ...] = ()
    group_id: int | None = None
    action_key: str | None = None
    modules: tuple[str, ...] = ()
    user_id: int | None = None
    sync_job_id: int | None = None
    action_run_id: int | None = None
    scheduled_action_id: int | None = None
    ai_session_id: int | None = None


RunGate = Callable[["AsyncSession", RunRequest], Awaitable[Verdict]]
RunEndListener = Callable[[RunEnded], None]


@dataclass
class _Registry:
    gates: list[RunGate] = field(default_factory=list)
    listeners: list[RunEndListener] = field(default_factory=list)


_registry = _Registry()


def register_run_gate(gate: RunGate) -> None:
    """Ask ``gate`` before every sync, action run, schedule dispatch and
    assistant change. Registering the same gate twice is a no-op."""
    if gate not in _registry.gates:
        _registry.gates.append(gate)


def register_run_end_listener(fn: RunEndListener) -> None:
    """Tell ``fn`` when each sync, action run and changing assistant
    session ends, after commit. Registering it twice is a no-op."""
    if fn not in _registry.listeners:
        _registry.listeners.append(fn)


def reset_run_gate() -> None:
    """Forget every gate and listener. For tests."""
    _registry.gates.clear()
    _registry.listeners.clear()


def has_run_gates() -> bool:
    return bool(_registry.gates)


def has_run_end_listeners() -> bool:
    return bool(_registry.listeners)


def _name(fn: Callable) -> str:
    return getattr(fn, "__qualname__", None) or repr(fn)


async def check_run(db: AsyncSession, request: RunRequest) -> Verdict:
    """Ask every gate about ``request``; the first non-allow answer wins."""
    if not _registry.gates:
        return ALLOW
    for gate in list(_registry.gates):
        try:
            verdict = await gate(db, request)
        except Exception:
            logger.exception(
                "run gate %s failed on %s; refusing the run", _name(gate), request.kind
            )
            return refuse("run gate failed")
        if not isinstance(verdict, Verdict):
            logger.error(
                "run gate %s answered %r, not a Verdict; refusing the run", _name(gate), verdict
            )
            return refuse("run gate failed")
        if not verdict.allowed:
            logger.info(
                "run gate %s: %s %s (%s)",
                _name(gate),
                verdict.outcome,
                request.kind,
                verdict.reason,
            )
            return verdict
    return ALLOW


def hold_until(verdict: Verdict) -> datetime:
    """When a deferred sync or action run is sent again: the gate's
    ``retry_at`` (naive means UTC), else a minute from now, and never
    sooner than ``MIN_HOLD_SECONDS`` from now."""
    now = datetime.now(UTC)
    floor = now + timedelta(seconds=MIN_HOLD_SECONDS)
    if verdict.retry_at is None:
        return now + timedelta(seconds=DEFAULT_HOLD_SECONDS)
    retry_at = verdict.retry_at
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(retry_at, floor)


# ---------------------------------------------------------------------------
# End notifications
# ---------------------------------------------------------------------------


def _deliver(event_: RunEnded) -> None:
    for fn in list(_registry.listeners):
        try:
            fn(event_)
        except Exception:
            logger.exception("run end listener %s failed on %s", _name(fn), event_.kind)


def notify_run_ended(db: AsyncSession | Session, event_: RunEnded) -> None:
    """Deliver ``event_`` to every listener once ``db`` commits.

    Sync and action-run ends are collected automatically when their status
    is flushed (see :func:`_collect_ends`); this is for the rest.
    """
    if not _registry.listeners:
        return
    from app.db_after_commit import run_after_commit

    run_after_commit(db, lambda: _deliver(event_))


_SEEN_KEY = "unbork_run_ended_seen"
_SYNC_TERMINAL = frozenset({"success", "failed", "cancelled"})
_ACTION_TERMINAL = frozenset({"succeeded", "failed", "partial", "cancelled"})


def _status_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _changed_to_terminal(obj: Any, terminal: frozenset[str], is_new: bool) -> bool:
    state = inspect(obj)
    current = _status_str(state.dict.get("status"))
    if current not in terminal:
        return False
    if is_new:
        return True
    history = state.attrs.status.history
    if not history.added:
        return False
    before = {_status_str(v) for v in history.deleted or ()}
    return current not in before


def _sync_event(obj: Any) -> Callable[[], RunEnded | None]:
    def build() -> RunEnded | None:
        state = inspect(obj)
        loaded = state.dict
        status = _status_str(loaded.get("status"))
        if not state.persistent or status not in _SYNC_TERMINAL:
            return None
        return RunEnded(
            kind="sync",
            status=status,
            finished_at=loaded.get("completed_at"),
            host_ids=(loaded["host_id"],) if loaded.get("host_id") is not None else (),
            group_id=loaded.get("group_id"),
            modules=tuple(loaded.get("module_filter") or ()),
            user_id=loaded.get("triggered_by_user_id"),
            sync_job_id=loaded.get("id"),
        )

    return build


def _action_event(obj: Any) -> Callable[[], RunEnded | None]:
    def build() -> RunEnded | None:
        state = inspect(obj)
        loaded = state.dict
        status = _status_str(loaded.get("status"))
        if not state.persistent or status not in _ACTION_TERMINAL:
            return None
        return RunEnded(
            kind="action_run",
            status=status,
            finished_at=loaded.get("finished_at"),
            host_ids=(loaded["host_id"],) if loaded.get("host_id") is not None else (),
            group_id=loaded.get("group_id"),
            action_key=loaded.get("action_key"),
            user_id=loaded.get("triggered_by_user_id"),
            action_run_id=loaded.get("id"),
            scheduled_action_id=loaded.get("scheduled_action_id"),
        )

    return build


@event.listens_for(Session, "after_flush")
def _collect_ends(session: Session, flush_context: Any) -> None:
    """Queue a :class:`RunEnded` for every sync job and action run whose
    status this flush moved to a terminal one.

    Every place that ends one writes the status through the ORM, so this
    is the one choke point; a site added later is covered without anyone
    remembering to call :func:`notify_run_ended`. The event is built when
    the commit has happened, from the row's loaded state, and dropped if
    the row is no longer stored (a rolled-back savepoint).

    The one Core write that ends a row is the cancel of a still-pending
    inner sync of ``_builtin.sync`` (``cancel_run`` in ``app.api.actions``).
    That sync was never put to a gate either; its parent run's end, which
    goes through the ORM, is the event a listener hears.
    """
    if not _registry.listeners:
        return
    from app.db_after_commit import run_after_commit
    from app.models.action_run import ActionRun
    from app.models.sync_job import SyncJob

    seen: set = session.info.setdefault(_SEEN_KEY, set())
    for objs, is_new in ((session.new, True), (session.dirty, False)):
        for obj in list(objs):
            if isinstance(obj, SyncJob):
                terminal, build = _SYNC_TERMINAL, _sync_event(obj)
            elif isinstance(obj, ActionRun):
                terminal, build = _ACTION_TERMINAL, _action_event(obj)
            else:
                continue
            key = (type(obj).__name__, id(obj))
            if key in seen or not _changed_to_terminal(obj, terminal, is_new):
                continue
            seen.add(key)

            def deliver(build: Callable[[], RunEnded | None] = build) -> None:
                built = build()
                if built is not None:
                    _deliver(built)

            run_after_commit(session, deliver)


@event.listens_for(Session, "after_transaction_end")
def _forget_seen(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        session.info.pop(_SEEN_KEY, None)
