"""OPE-136 "Allow for this request" — the run grant: covers the exact tool for the
remainder of the current run, in memory only, cleared at the run boundary.

The design ladder it slots into: once → THIS RUN → (mode switch) → always.
It exists because the EXTERNAL ladder was once-or-forever, and approval fatigue
drained into permanent trust rules (observed in owner testing 2026-08-30).
"""
from __future__ import annotations

from types import SimpleNamespace

from coworker.permissions import Mode, PermissionEngine

MCP_META = SimpleNamespace(requires_approval=True, category="mcp")
CONNECTOR_META = SimpleNamespace(requires_approval=True, category="connector")
MCP_TOOL = "mcp__atlassian__searchJiraIssuesUsingJql"


def engine(mode: Mode, tmp_path) -> PermissionEngine:
    return PermissionEngine(workspace_root=tmp_path, mode=mode)


# -- the grant covers the tool, and only until cleared ----------------------------
def test_run_grant_allows_then_dies_at_the_boundary(tmp_path):
    eng = engine(Mode.INTERACTIVE, tmp_path)
    assert not eng.evaluate(MCP_TOOL, {}, MCP_META).allowed  # asks before the grant

    eng.allow_tool_for_run(MCP_TOOL)
    d = eng.evaluate(MCP_TOOL, {}, MCP_META)
    assert d.allowed and d.reason == "tool allowed for this request"

    # The run boundary IS the expiry: after clearing, the same call asks again.
    eng.clear_run_allowances()
    assert not eng.evaluate(MCP_TOOL, {}, MCP_META).allowed


def test_run_grant_covers_connectors_unlike_the_session_grant(tmp_path):
    # The session grant excludes connectors; the run grant exists FOR the EXTERNAL
    # family — connector loops ("email these five people") are its main scenario.
    eng = engine(Mode.INTERACTIVE, tmp_path)
    eng.allow_tool_for_session("gmail_send_email")
    assert not eng.evaluate("gmail_send_email", {}, CONNECTOR_META).allowed

    eng.allow_tool_for_run("gmail_send_email")
    d = eng.evaluate("gmail_send_email", {}, CONNECTOR_META)
    assert d.allowed and d.reason == "tool allowed for this request"


def test_run_grant_never_skips_the_auto_approve_judge(tmp_path):
    # §1.5: an in-flow click may not skip the reviewer — same law as session grants.
    eng = engine(Mode.AUTO_APPROVE, tmp_path)
    eng.allow_tool_for_run(MCP_TOOL)
    d = eng.evaluate(MCP_TOOL, {}, MCP_META)
    assert not d.allowed and d.needs_user and not d.human_only


def test_run_grant_cannot_override_a_read_only_mode(tmp_path):
    # Grants only ever turn an "ask" into an "allow" — Discuss's fence stands.
    eng = engine(Mode.DISCUSS, tmp_path)
    eng.allow_tool_for_run(MCP_TOOL)
    d = eng.evaluate(MCP_TOOL, {}, MCP_META)
    assert not d.allowed and not d.needs_user


# -- server side: offered ONLY for the EXTERNAL family ----------------------------
def test_this_run_grant_is_external_only():
    from coworker.engine import ApprovalOutcome
    from coworker.server.manager import _grant_offered

    def req(name: str, category: str = "", approval: bool = False, args: dict | None = None):
        return SimpleNamespace(
            tool_name=name,
            metadata=SimpleNamespace(requires_approval=approval, category=category)
            if category or approval
            else None,
            arguments=args or {},
        )

    offered = ApprovalOutcome.THIS_RUN
    assert _grant_offered(offered, req("mcp__srv__tool", "mcp", approval=True))
    assert _grant_offered(offered, req("gmail_send_email", "connector", approval=True))
    # Core messaging classifies EXTERNAL via its metadata, as wired in production.
    assert _grant_offered(offered, req("send_message", approval=True))
    # EXEC: tool-wide shell is a blank check at any duration.
    assert not _grant_offered(offered, req("run_shell"))
    # EGRESS: the domain-scoped grant is the honest instrument there.
    assert not _grant_offered(offered, req("web_fetch", args={"url": "https://x.y"}))
    # Local writes have the session grant; the run rung isn't theirs.
    assert not _grant_offered(offered, req("write_file"))


def test_approval_outcome_downgrades_unoffered_grants_to_once():
    # POST /v1/inbox/{id}/resolve takes a raw string — the server validates every
    # grant vocabulary, including this_run and (gap closed) always_trust.
    from coworker.engine import ApprovalOutcome
    from coworker.server.manager import SessionManager

    refused: list[str] = []
    fake = SimpleNamespace(
        _audit_grant_refused=lambda _s, _r, res: refused.append(res),
        mint_task_rule=lambda *_a, **_k: False,
    )
    shell_req = SimpleNamespace(tool_name="run_shell", metadata=None, arguments={})
    out = SessionManager.approval_outcome(fake, "this_run", shell_req, "s1")
    assert out is ApprovalOutcome.ONCE and refused == ["this_run"]

    trust_req = SimpleNamespace(tool_name="write_file", metadata=None, arguments={})
    out = SessionManager.approval_outcome(fake, "always_trust", trust_req, "s1")
    assert out is ApprovalOutcome.ONCE and refused[-1] == "always_trust"

    mcp_req = SimpleNamespace(
        tool_name="mcp__srv__tool",
        metadata=SimpleNamespace(requires_approval=True, category="mcp"),
        arguments={},
    )
    assert (
        SessionManager.approval_outcome(fake, "this_run", mcp_req, "s1")
        is ApprovalOutcome.THIS_RUN
    )


# -- end to end: one card per run, covered calls chip-annotated, boundary honored --
def test_engine_end_to_end_one_card_per_run(tmp_path):
    """The whole promise in one flow: THIS_RUN from the card covers the SAME tool's
    later calls in the same run (no second card), stamps the covered call with the
    run_grant origin for the transcript chip, and the next run() asks again."""
    import asyncio

    import aisuite as ai
    from coworker.engine import ApprovalOutcome, TurnEngine
    from coworker.providers import (
        AssistantTurn,
        ModelCapabilities,
        ProviderClient,
        ToolCall,
    )
    from coworker.tools import ToolRegistry

    class Scripted(ProviderClient):
        def __init__(self, turns):
            self._turns = list(turns)

        def complete(self, *, model, messages, tools=None, **settings):
            return self._turns.pop(0)

        def capabilities(self, model):
            return ModelCapabilities()

    def tool_turn(call_id: str) -> AssistantTurn:
        return AssistantTurn(
            tool_calls=[
                ToolCall(id=call_id, name="mcp__srv__search", arguments={"q": "x"})
            ],
            finish_reason="tool_calls",
        )

    def done(text: str) -> AssistantTurn:
        return AssistantTurn(text=text, finish_reason="stop")

    asks: list[str] = []

    async def approver(req):
        asks.append(req.tool_name)
        return ApprovalOutcome.THIS_RUN

    def mcp__srv__search(q: str = ""):
        """Fake MCP search."""
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        mcp__srv__search,
        metadata=ai.ToolMetadata(category="mcp", requires_approval=True),
    )
    engine = TurnEngine(
        provider=Scripted(
            # Run 1: the tool twice, then done. Run 2: the tool once more, then done.
            [tool_turn("c1"), tool_turn("c2"), done("done"), tool_turn("c3"), done("ok")]
        ),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        approver=approver,
    )

    async def scenario():
        events_one = [ev async for ev in engine.run("find things")]
        # ONE card for two calls: c1 asked, c2 rode the grant.
        assert asks == ["mcp__srv__search"]
        # The covered call is chip-annotated — silent to attention, never invisible.
        origins = [
            m.get("_display", {}).get("approval_origin")
            for m in engine.messages
            if m.get("role") == "tool"
        ]
        assert "run_grant" in origins
        events_two = [ev async for ev in engine.run("find more")]
        # The run boundary held: the very same tool asks again in the next run.
        assert asks == ["mcp__srv__search", "mcp__srv__search"]
        return events_one, events_two

    asyncio.run(scenario())
