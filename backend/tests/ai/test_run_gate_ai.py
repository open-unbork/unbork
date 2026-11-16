"""S-002: the run gate before the assistant makes a change.

Only a call the autonomy gate already allowed *and* classed ``mutating``
is put to the extension. A refusal is handed to the model as the call's
result; a deferral parks a full-auto session for a human; a read is never
asked about.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ai.gate import decide, gate_mutation
from app.ai.loop import AgentLoop, LoopCaps
from app.ai.models import AIApprovalRequest, AIToolCall
from app.ai.tools import TOOL_REGISTRY
from app.run_gate import ALLOW, RunRequest, defer, refuse, register_run_gate, reset_run_gate
from tests.ai.fake_provider import FakeProvider, ScriptedTurn, call
from tests.conftest import create_host

WRITE = "systemctl restart nginx"
READ = "journalctl -u nginx -n 50"


@pytest.fixture(autouse=True)
def _clean():
    reset_run_gate()
    yield
    reset_run_gate()


class Gate:
    def __init__(self, verdict=ALLOW):
        self.verdict = verdict
        self.asked: list[RunRequest] = []

    async def __call__(self, db, request):
        self.asked.append(request)
        return self.verdict


@pytest.fixture
def gate():
    g = Gate()
    register_run_gate(g)
    return g


class _Session:
    id = 7
    autonomy_level = "full_auto"
    target_host_ids = [3]
    created_by_user_id = 11


def _decision(command: str, level: str = "full_auto"):
    return decide(
        "run_ssh_command",
        {"host_id": 3, "command": command},
        permitted=dict(TOOL_REGISTRY),
        autonomy_level=level,
    )


async def test_no_gate_returns_the_decision_itself():
    d = _decision(WRITE)
    assert await gate_mutation(None, _Session(), "run_ssh_command", {}, d) is d


async def test_a_read_is_not_asked(gate):
    d = _decision(READ)
    assert await gate_mutation(None, _Session(), "run_ssh_command", {"host_id": 3}, d) is d
    assert gate.asked == []


async def test_a_call_the_autonomy_gate_refused_is_not_asked(gate):
    d = _decision(WRITE, level="read_only")
    assert await gate_mutation(None, _Session(), "run_ssh_command", {"host_id": 3}, d) is d
    assert gate.asked == []


async def test_an_allowed_change_is_described_to_the_gate(gate):
    d = _decision(WRITE)
    args = {"host_id": 3, "command": WRITE}
    assert await gate_mutation(None, _Session(), "run_ssh_command", args, d) is d
    (request,) = gate.asked
    assert request.kind == "ai_fix"
    assert request.host_ids == (3,)
    assert (request.ai_session_id, request.user_id) == (7, 11)
    assert request.action_key == "run_ssh_command"


async def test_a_refusal_is_a_refused_call_with_the_reason(gate):
    gate.verdict = refuse("change freeze")
    out = await gate_mutation(None, _Session(), "run_ssh_command", {"host_id": 3}, _decision(WRITE))
    assert out.allowed is False and out.needs_approval is False
    assert out.reason.startswith("Refused: change freeze.")


async def test_a_deferral_parks_full_auto_and_refuses_elsewhere(gate):
    gate.verdict = defer("outside the window")
    out = await gate_mutation(None, _Session(), "run_ssh_command", {"host_id": 3}, _decision(WRITE))
    assert out.allowed is False and out.needs_approval is True
    assert "outside the window" in out.reason

    class _Approval(_Session):
        autonomy_level = "approval"

    # An approval session's call was already approved by a person; the
    # extension's hold is a refusal there.
    allowed = _decision(WRITE)
    out = await gate_mutation(None, _Approval(), "run_ssh_command", {"host_id": 3}, allowed)
    assert out.allowed is False and out.needs_approval is False


async def _loop(db, session, provider_row, turns):
    loop = AgentLoop(db, session, provider_row, LoopCaps(), provider=FakeProvider(turns))
    return await loop.run()


async def test_the_loop_hands_the_refusal_to_the_model(db, ai_provider, make_session, gate):
    host_id = (await create_host(db)).id
    session = await make_session(autonomy_level="full_auto", target_host_ids=[host_id])
    gate.verdict = refuse("change freeze")

    await _loop(
        db,
        session,
        ai_provider,
        [
            ScriptedTurn(tool_calls=[call("run_ssh_command", host_id=host_id, command=WRITE)]),
            ScriptedTurn(text="The change was refused, so I stopped."),
        ],
    )

    (record,) = (
        (await db.execute(select(AIToolCall).where(AIToolCall.session_id == session.id)))
        .scalars()
        .all()
    )
    assert record.status == "blocked"
    assert record.result_summary.startswith("Refused: change freeze.")
    assert len(gate.asked) == 1


async def test_the_loop_parks_a_held_full_auto_change(db, ai_provider, make_session, gate):
    host_id = (await create_host(db)).id
    session = await make_session(autonomy_level="full_auto", target_host_ids=[host_id])
    gate.verdict = defer("outside the window")

    await _loop(
        db,
        session,
        ai_provider,
        [
            ScriptedTurn(tool_calls=[call("run_ssh_command", host_id=host_id, command=WRITE)]),
            ScriptedTurn(text="unreachable"),
        ],
    )

    approvals = (
        (
            await db.execute(
                select(AIApprovalRequest).where(AIApprovalRequest.session_id == session.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(approvals) == 1


async def test_an_approved_call_is_asked_when_it_runs(db, make_session, gate):
    """A person's approval does not lift a freeze: the parked call is put
    to the gate when it is about to run, and a hold is a refusal there."""
    from app.ai import approvals
    from tests.ai.test_approvals import _host, _parked_session, _ssh_runs

    host = await _host(db)
    session, approval = await _parked_session(db, make_session, host)
    approval.status = "approved"
    ran: list = []

    async def fake_run(ctx, args):  # pragma: no cover - must not run
        ran.append(args)

    gate.verdict = defer("change freeze")
    with _ssh_runs(fake_run):
        result = await approvals.execute_approved(db, session, approval)

    assert ran == []
    assert result.ok is False
    assert result.content.startswith("Refused: change freeze.")
    (request,) = gate.asked
    assert (request.kind, request.ai_session_id) == ("ai_fix", session.id)
    record = (
        await db.execute(select(AIToolCall).where(AIToolCall.approval_id == approval.id))
    ).scalar_one()
    assert record.status == "blocked"


async def test_an_approved_call_runs_when_the_gate_allows(db, make_session, gate):
    from app.ai import approvals
    from app.ai.tools import ToolResult
    from tests.ai.test_approvals import _host, _parked_session, _ssh_runs

    host = await _host(db)
    session, approval = await _parked_session(db, make_session, host)
    approval.status = "approved"

    async def fake_run(ctx, args):
        return ToolResult("exit status: 0", ok=True, target_host_id=args["host_id"])

    with _ssh_runs(fake_run):
        result = await approvals.execute_approved(db, session, approval)
    assert result.ok is True
    assert len(gate.asked) == 1
