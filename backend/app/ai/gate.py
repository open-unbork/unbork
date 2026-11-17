"""Decide whether one tool call may run, before it runs.

Both runners consult this, and that is the point of it existing as its
own module. They arrive from opposite directions:

* :class:`~app.ai.agent_sdk.runner.AgentSDKRunner` is *asked* — the SDK
  calls ``can_use_tool`` for every call and honours a denial by never
  dispatching it (verified live: the denied tool's Python function was
  not entered, and the model was handed the refusal text and routed
  around it).
* :class:`~app.ai.loop.AgentLoop` owns its loop and could enforce
  autonomy anywhere, and used to do it *inside* ``tools/ssh.py`` — which
  is too late to pause a session, because by then the call is running.

One function for both means the answer to "may this run" cannot drift
between backends, which it silently would have: the SDK path gained a
gate first, and the two were already implementing the same policy twice.

Two things make this fragile in ways that do not announce themselves, so
both are pinned by tests:

**A populated ``allowed_tools`` silently disables the SDK's gate.** An
entry there auto-approves the tool *before* the callback is consulted;
the SDK warns about it (``CanUseToolShadowedWarning``) and the warning is
easy to lose in logs. unbork leaves ``allowed_tools`` empty so every call
falls through to here. Adding to it is a change to the safety posture,
not a convenience.

**A tool's ceiling is not its verdict.** ``run_ssh_command`` is declared
``mutating`` because it *can* be, but most calls are a ``journalctl``
read. Gating on the ceiling would refuse every read in a read-only
session — the sessions unbork runs by default — so the decision uses
:meth:`ToolHandler.verdict_for`, which re-reads the arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.safety import Classification, Verdict, is_allowed
from app.ai.tools import ToolHandler


@dataclass(frozen=True)
class GateDecision:
    """The verdict on one proposed call, and why."""

    allowed: bool
    #: Shown to the model on refusal, so it reads as a policy answer
    #: rather than a malfunction it should retry around.
    reason: str
    classification: Classification
    #: Set when the call was refused only because a human has not approved
    #: it yet, as opposed to being refused outright. The caller turns these
    #: into approval requests; everything else is a plain refusal.
    needs_approval: bool = False
    #: The full verdict, carried through so a caller that parks the
    #: session can record *why* the classifier called this a write without
    #: re-running the classifier and risking a different answer.
    verdict: Verdict | None = None


def target_host(arguments: dict[str, Any], target_host_ids: list | None) -> int | None:
    """The host a call names, when it is one of the session's targets.

    ``host_id`` comes from the model, and the rows that record a call
    (``ai_tool_calls`` and ``ai_approval_requests``) keep it in a foreign
    key to ``hosts``. A made-up id would fail the insert and lose the
    record, so anything the session could not run on is left unset:
    ``run_ssh_command`` refuses a host outside the targets anyway.
    """
    host_id = arguments.get("host_id")
    if isinstance(host_id, bool) or not isinstance(host_id, int):
        return None
    return host_id if host_id in (target_host_ids or []) else None


def decide(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    permitted: dict[str, ToolHandler],
    autonomy_level: str,
) -> GateDecision:
    """Whether ``tool_name`` may run with ``arguments`` in this session.

    ``tool_name`` is unbork's own name for the tool; the SDK runner
    unqualifies the MCP name before calling.

    ``permitted`` is the session's own handler set. A tool absent from it
    is refused even if it exists in the registry: the model can ask for a
    name it was never offered, and a narrowed session is a spend and
    blast-radius bound that has to hold here too.
    """
    handler = permitted.get(tool_name)

    if handler is None:
        available = ", ".join(sorted(permitted)) or "none"
        return GateDecision(
            allowed=False,
            reason=(
                f"The {tool_name} tool is not available to this session. You may use: {available}"
            ),
            classification="denied",
        )

    verdict = handler.verdict_for(arguments)
    allowed, reason = is_allowed(verdict, autonomy_level)

    # `is_allowed` reports "not now" and "never" the same way. The
    # difference decides whether the session can park and come back, so it
    # is recovered here rather than re-derived by each caller.
    needs_approval = (
        not allowed and autonomy_level == "approval" and verdict.classification != "denied"
    )

    return GateDecision(
        allowed=allowed,
        reason=reason,
        classification=verdict.classification,
        needs_approval=needs_approval,
        verdict=verdict,
    )


async def gate_mutation(
    db: Any,
    session: Any,
    tool_name: str,
    arguments: dict[str, Any],
    decision: GateDecision,
) -> GateDecision:
    """Ask an installed extension's run gate (``app.run_gate``) about a
    change the assistant may make. Returns ``decision`` itself unless the
    gate says otherwise.

    Asked only when :func:`decide` allowed a ``mutating`` call; a call
    parked for a person's approval is asked when it is approved and about
    to run (``app.ai.approvals.execute_approved``). A refusal
    becomes a refused call carrying the gate's reason. A deferral parks a
    full-auto session for a human, as an approval would, and is a plain
    refusal at any other level (an approval session already asked one).
    :func:`decide` stays synchronous and unchanged.
    """
    from app.run_gate import RunRequest, check_run, has_run_gates

    if not has_run_gates() or not decision.allowed or decision.classification != "mutating":
        return decision
    host_id = target_host(arguments, getattr(session, "target_host_ids", None))
    verdict = await check_run(
        db,
        RunRequest(
            kind="ai_fix",
            host_ids=(host_id,) if host_id is not None else tuple(session.target_host_ids or ()),
            user_id=getattr(session, "created_by_user_id", None),
            ai_session_id=session.id,
            action_key=tool_name,
        ),
    )
    if verdict.allowed:
        return decision
    reason = verdict.reason or "an installed extension holds changes now"
    if verdict.outcome == "defer" and session.autonomy_level == "full_auto":
        return GateDecision(
            allowed=False,
            reason=f"Held for approval: {reason}",
            classification=decision.classification,
            needs_approval=True,
            verdict=decision.verdict,
        )
    return GateDecision(
        allowed=False,
        reason=f"Refused: {reason}. Do not retry this change in this session.",
        classification=decision.classification,
        verdict=decision.verdict,
    )
