"""#32 rec #1: an unattended run never auto-allowss an EXTERNAL call to a target the
user did not pre-bind.

Session / config / run / MCP-trust grants are tool-name-only, so while nobody is
watching an injected call could ride them to a new destination with no card at all.
The floor turns such allows into the ordinary ask: parked in the Inbox (attendance
"inbox") or refused by rule (attendance "auto" — never a hang, never a silent allow).
A standing rule that matched this call's exact target IS the pre-binding and stays
allowed; bypass modes are an explicit opt-out; attended sessions are unchanged.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from coworker.connectors.tool_defs import TARGET_ARGS
from coworker.engine import ApprovalOutcome, TurnEngine
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.tools import ToolRegistry


@dataclass
class _Meta:
    category: str = ""
    risk_level: str = "high"
    requires_approval: bool = False


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _tool_turn(name, args, call_id="c1"):
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
    )


def _text(text="done"):
    return AssistantTurn(text=text, finish_reason="stop")


def _engine(
    tmp_path,
    turns,
    *,
    mode=Mode.INTERACTIVE,
    attendance="attended",
    outcomes=None,
):
    """An engine with one EXTERNAL tool (`deliver_note`, names a target) and one READ
    tool (`peek`). `outcomes` overrides the approver's answers in order (default ONCE)."""
    delivered: list[str] = []
    peeked: list[str] = []
    rows: list[dict] = []

    def deliver_note(target: str) -> dict:
        delivered.append(target)
        return {"ok": True, "target": target}

    def peek(name: str) -> str:
        peeked.append(name)
        return f"note:{name}"

    registry = ToolRegistry()
    registry.register(deliver_note, metadata=_Meta(requires_approval=True))
    registry.register(peek, metadata=_Meta())

    answers = list(outcomes or [])
    cards: list[str] = []

    async def approver(request):
        cards.append(request.tool_name)
        if answers:
            return answers.pop(0)
        return ApprovalOutcome.ONCE

    engine = TurnEngine(
        provider=_Scripted(turns),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=mode),
        model="test-model",
        approver=approver,
        audit_sink=rows.append,
    )
    if attendance is not None:
        engine.attendance = lambda: attendance
    return engine, delivered, peeked, cards, rows


def _run(engine, text="go"):
    async def _go():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(_go())


def _tool_result(engine):
    msg = [m for m in engine.messages if m.get("role") == "tool"][-1]
    return json.loads(msg["content"])


# -- attended: unchanged -------------------------------------------------------------


def test_attended_session_allow_still_runs_without_a_card(tmp_path):
    engine, delivered, _, cards, _ = _engine(
        tmp_path,
        [_tool_turn("deliver_note", {"target": "alice@corp.com"}), _text()],
        attendance="attended",
    )
    engine.permissions.allow_tool_for_session("deliver_note")
    _run(engine)
    assert cards == [], "an attended session grant must stay cardless"
    assert delivered == ["alice@corp.com"]


def test_no_attendance_getter_reads_as_attended(tmp_path):
    engine, delivered, _, cards, _ = _engine(
        tmp_path,
        [_tool_turn("deliver_note", {"target": "alice@corp.com"}), _text()],
        attendance=None,
    )
    engine.permissions.allow_tool_for_session("deliver_note")
    _run(engine)
    assert cards == [] and delivered == ["alice@corp.com"]


# -- unattended: the floor fires -----------------------------------------------------


def test_unattended_session_allow_asks_instead_of_running_silently(tmp_path):
    """The headline gap: a tool-name-only session grant must not wave an EXTERNAL
    call through while nobody is watching — it becomes the ordinary card."""
    engine, delivered, _, cards, _ = _engine(
        tmp_path,
        [_tool_turn("deliver_note", {"target": "evil@attacker.test"}), _text()],
        attendance="inbox",
    )
    engine.permissions.allow_tool_for_session("deliver_note")
    _run(engine)
    assert cards == ["deliver_note"], "the unattended EXTERNAL call must be asked"
    assert delivered == ["evil@attacker.test"]  # the person answered ONCE


def test_the_ask_is_human_only_and_says_why_the_grant_was_not_enough(tmp_path):
    engine, _, _, cards, rows = _engine(
        tmp_path,
        [_tool_turn("deliver_note", {"target": "evil@attacker.test"}), _text()],
        attendance="inbox",
    )
    engine.permissions.allow_tool_for_session("deliver_note")
    _run(engine)
    assert cards == ["deliver_note"]
    asked = [r for r in rows if r.get("stage") == "approval_requested"]
    assert asked and "pre-bound" in asked[0].get("reason", "")
    # human_only: the Auto-Approve reviewer cannot clear it either — a person sees it.
    path = engine._decisions["c1"]
    assert path.stage("permission")["outcome"] == "human_only"


def test_unattended_auto_refuses_the_new_target_and_never_runs_it(tmp_path):
    engine, delivered, _, cards, _ = _engine(
        tmp_path,
        [_tool_turn("deliver_note", {"target": "evil@attacker.test"}), _text()],
        attendance="auto",
    )
    engine.permissions.allow_tool_for_session("deliver_note")
    _run(engine)
    assert cards == [], "attendance auto never shows a card"
    assert delivered == [], "and must not run the unfloored allow either"
    result = _tool_result(engine)
    assert "attendance: auto" in str(result.get("reason") or result.get("error", ""))


def test_unattended_read_tools_are_not_floored(tmp_path):
    engine, _, peeked, cards, _ = _engine(
        tmp_path,
        [_tool_turn("peek", {"name": "notes"}), _text()],
        attendance="inbox",
    )
    engine.permissions.allow_tool_for_session("peek")
    _run(engine)
    assert cards == [] and peeked == ["notes"], "reads re-run harmlessly; no floor"


# -- pre-bound targets stay allowed --------------------------------------------------


def test_unattended_standing_rule_exact_target_stays_cardless(tmp_path, monkeypatch):
    """A standing rule binds tool → exact target: that IS the user's pre-binding."""
    monkeypatch.setitem(TARGET_ARGS, "deliver_note", "target")
    engine, delivered, _, cards, _ = _engine(
        tmp_path,
        [_tool_turn("deliver_note", {"target": "alice@corp.com"}), _text()],
        attendance="inbox",
    )
    engine.permissions.task_rules["deliver_note"] = {"alice@corp.com"}
    _run(engine)
    assert cards == [], "a matched standing rule is pre-bound and must stay silent"
    assert delivered == ["alice@corp.com"]


def test_unattended_session_grant_does_not_cover_a_target_the_rule_bound_elsewhere(
    tmp_path, monkeypatch
):
    """The standing rule binds alice only; the tool-name session grant must not use
    that as cover for a NEW target while unattended."""
    monkeypatch.setitem(TARGET_ARGS, "deliver_note", "target")
    engine, delivered, _, cards, _ = _engine(
        tmp_path,
        [_tool_turn("deliver_note", {"target": "evil@attacker.test"}), _text()],
        attendance="inbox",
    )
    engine.permissions.task_rules["deliver_note"] = {"alice@corp.com"}
    engine.permissions.allow_tool_for_session("deliver_note")
    _run(engine)
    assert cards == ["deliver_note"], "the new target must be asked, not allowed"
    assert delivered == ["evil@attacker.test"]


def test_unattended_run_grant_does_not_cover_a_later_call_to_the_same_tool(tmp_path):
    """"Allow for this request" mints a tool-name grant for the rest of the run; a
    LATER call to the same tool with a new target is not what the person answered."""
    engine, delivered, _, cards, _ = _engine(
        tmp_path,
        [
            _tool_turn("deliver_note", {"target": "alice@corp.com"}, "c1"),
            _tool_turn("deliver_note", {"target": "evil@attacker.test"}, "c2"),
            _text(),
        ],
        attendance="inbox",
        outcomes=[ApprovalOutcome.THIS_RUN, ApprovalOutcome.DENY],
    )
    _run(engine)
    assert cards == ["deliver_note", "deliver_note"], (
        "the second call must ask again — the run grant does not cover a new target"
    )
    assert delivered == ["alice@corp.com"], "the denied second call must not deliver"


# -- explicit opt-out ----------------------------------------------------------------


def test_bypass_mode_is_not_floored(tmp_path):
    engine, delivered, _, cards, _ = _engine(
        tmp_path,
        [_tool_turn("deliver_note", {"target": "anyone@anywhere"}), _text()],
        mode=Mode.BYPASS_APPROVALS,
        attendance="inbox",
    )
    _run(engine)
    assert cards == [] and delivered == ["anyone@anywhere"], (
        "bypass is the user's explicit opt-out of asking"
    )
