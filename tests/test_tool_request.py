"""`request_tool` — the agent asks for a missing CLI instead of dropping the check (OPE-85).

Engine-intercepted like `request_directory`: it never goes through the permission path,
because the user's out-of-band decision IS the consent.
"""

from __future__ import annotations

import pytest

from coworker.engine import EventType, TurnEngine
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.tools import ToolRegistry


class ScriptedProvider(ProviderClient):
    """One turn that calls request_tool, then a plain reply."""

    def __init__(self, tool: str = "gitleaks"):
        self.calls = 0
        self.tool = tool

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        if self.calls == 1:
            return AssistantTurn(
                text="",
                tool_calls=[
                    ToolCall(
                        id="t1",
                        name="request_tool",
                        arguments={"name": self.tool, "reason": "scan history for secrets"},
                    )
                ],
            )
        return AssistantTurn(text="done", tool_calls=[])

    def capabilities(self, model):
        return ModelCapabilities(tools=True)


def _engine(tmp_path, requester, tool: str = "gitleaks"):
    return TurnEngine(
        provider=ScriptedProvider(tool),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE),
        model="m",
        tool_requester=requester,
    )


async def _run(engine) -> list:
    return [e async for e in engine.run("check this repo")]


@pytest.mark.asyncio
async def test_emits_tool_requested_and_reports_install(tmp_path):
    async def requester(args, tool_call_id=None):
        assert args["name"] == "gitleaks"
        return {"installed": True, "path": "/tmp/gitleaks", "version": "8.30.1"}

    events = await _run(_engine(tmp_path, requester))
    requested = [e for e in events if e.type is EventType.TOOL_REQUESTED]
    assert requested and requested[0].data["name"] == "gitleaks"
    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished[0].data["status"] == "ok"


@pytest.mark.asyncio
async def test_declining_tells_the_agent_to_fall_back_openly(tmp_path, monkeypatch):
    """A refusal must not read as 'check done'. The tool result has to push the agent
    toward a disclosed fallback, which is the whole point of the contract."""
    from coworker import toolchain

    # Truly absent — otherwise the decline-time re-check (below) would find the dev
    # machine's real gitleaks and turn this into the user-provided-copy path.
    monkeypatch.setattr(toolchain, "resolve", lambda name: None)

    async def requester(args, tool_call_id=None):
        return {"installed": False, "reason": "the user declined to install it"}

    engine = _engine(tmp_path, requester)
    events = await _run(engine)
    assert [e for e in events if e.type is EventType.TOOL_REQUESTED]
    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished[0].data["status"] == "denied"

    tool_msg = [m for m in engine.messages if m.get("role") == "tool"][-1]
    body = str(tool_msg["content"]).lower()
    assert "degraded" in body or "fallback" in body


@pytest.mark.asyncio
async def test_decline_recheck_finds_a_copy_the_user_installed_themselves(tmp_path, monkeypatch):
    """The card says "or install it yourself and continue" — that has to be real. A user
    who brews the tool while the prompt is up and clicks Continue has PROVIDED the tool;
    the agent must be handed their copy's path, not a refusal."""
    from coworker import toolchain

    monkeypatch.setattr(toolchain, "resolve", lambda name: "/opt/homebrew/bin/gitleaks")

    async def requester(args, tool_call_id=None):
        return {"installed": False, "reason": "the user declined to install it"}

    engine = _engine(tmp_path, requester)
    events = await _run(engine)
    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished[0].data["status"] == "ok"

    tool_msg = [m for m in engine.messages if m.get("role") == "tool"][-1]
    body = str(tool_msg["content"])
    assert "/opt/homebrew/bin/gitleaks" in body
    assert "own copy" in body  # attributed to the user, not to a managed install


@pytest.mark.asyncio
async def test_event_tells_the_truth_about_installability(tmp_path, monkeypatch):
    """Owner-hit 2026-08-14: the card offered Install for a tool with no pinned build —
    the surface guessed because the event said nothing. The event must carry the
    registry's verdict for catalog tools."""
    from coworker import toolchain

    monkeypatch.setattr(toolchain, "_platform_key", lambda: "darwin_arm64")

    async def requester(args, tool_call_id=None):
        return {"installed": False, "reason": "declined"}

    events = await _run(_engine(tmp_path, requester, tool="gitleaks"))
    data = [e for e in events if e.type is EventType.TOOL_REQUESTED][0].data
    assert data["installable"] is True
    assert data["version"] == toolchain.MANAGED["gitleaks"].version
    assert data["summary"]
    assert data["source"] == "github.com/gitleaks"


@pytest.mark.asyncio
async def test_non_catalog_tool_gets_no_card_and_a_shell_steer(tmp_path, monkeypatch):
    """Owner-hit 2026-08-20: agents routed ordinary brew/pip installs through the
    install card, which could only fail AFTER the user approved. A non-catalog name
    must produce NO prompt at all — just a result steering the agent to the shell."""
    from coworker import toolchain

    monkeypatch.setattr(toolchain, "_platform_key", lambda: "darwin_arm64")
    called = []

    async def requester(args, tool_call_id=None):
        called.append(args)
        return {"installed": False, "reason": "declined"}

    engine = _engine(tmp_path, requester, tool="semgrep")
    events = await _run(engine)
    assert not [e for e in events if e.type is EventType.TOOL_REQUESTED]
    assert called == []  # the user was never asked
    tool_msg = [m for m in engine.messages if m.get("role") == "tool"][-1]
    body = str(tool_msg["content"])
    assert "not in the pinned tool catalog" in body
    assert "shell" in body and "gitleaks" in body  # the catalog is named


@pytest.mark.asyncio
async def test_no_requester_still_returns_guidance(tmp_path):
    """Headless surfaces have nobody to ask — the agent must still be told to disclose
    rather than assume the check passed."""
    engine = _engine(tmp_path, None)
    events = await _run(engine)
    assert not [e for e in events if e.type is EventType.TOOL_REQUESTED]
    tool_msg = [m for m in engine.messages if m.get("role") == "tool"][-1]
    assert "degraded" in str(tool_msg["content"]).lower()
