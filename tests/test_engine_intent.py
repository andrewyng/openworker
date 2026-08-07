"""Engine + manager integration tests for AI intent analysis."""
import asyncio
from unittest.mock import MagicMock

import pytest

from coworker.engine import PermissionRequest, TurnEngine, EventType


# -- PermissionRequest + TurnEngine.__init__ --


def test_permission_request_has_intent_field():
    """PermissionRequest has an optional intent field (default None)."""
    req = PermissionRequest(
        tool_name="run_shell", arguments={}, metadata=None, reason="test"
    )
    assert req.intent is None
    req2 = PermissionRequest(
        tool_name="run_shell", arguments={}, metadata=None, reason="test", intent="• x"
    )
    assert req2.intent == "• x"


def test_turn_engine_accepts_intent_analyzer_none():
    """TurnEngine.__init__ accepts intent_analyzer, default None."""
    engine = _build_minimal_engine(intent_analyzer=None)
    assert engine.intent_analyzer is None


def test_turn_engine_accepts_intent_analyzer_callable():
    analyzer = lambda tc, p, m: "• test"
    engine = _build_minimal_engine(intent_analyzer=analyzer)
    assert engine.intent_analyzer is analyzer


def test_turn_engine_accepts_intent_analyzer_timeout():
    """TurnEngine.__init__ accepts intent_analyzer_timeout, default 20.0."""
    engine = _build_minimal_engine()
    assert engine.intent_analyzer_timeout == 20.0
    engine2 = _build_minimal_engine(intent_analyzer_timeout=0.1)
    assert engine2.intent_analyzer_timeout == 0.1


def _build_minimal_engine(intent_analyzer=None, intent_analyzer_timeout=None):
    """Build a minimal TurnEngine (no turn run, just verify field assignment)."""
    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine, Mode
    from coworker.tools import ToolRegistry
    from coworker.providers.base import ProviderClient

    kwargs = dict(
        provider=MagicMock(spec=ProviderClient),
        registry=ToolRegistry(),
        permissions=PermissionEngine(mode=Mode.INTERACTIVE, workspace_root="."),
        model="test",
        intent_analyzer=intent_analyzer,
    )
    if intent_analyzer_timeout is not None:
        kwargs["intent_analyzer_timeout"] = intent_analyzer_timeout
    return TurnEngine(**kwargs)


# -- _authorize synchronous analysis branch --


@pytest.mark.asyncio
async def test_authorize_no_analyzer_payload_intent_none():
    """intent_analyzer=None → event payload.intent is None (upstream behavior)."""
    events = await _run_authorize(intent_analyzer=None)
    perm_event = next(e for e in events if e.type == EventType.PERMISSION_REQUIRED)
    assert perm_event.data["intent"] is None


@pytest.mark.asyncio
async def test_authorize_analyzer_success_payload_has_intent():
    """Injected analyzer returning text → event payload.intent carries it."""

    def analyzer(tc, p, m):
        return "• dangerous\n• irreversible"

    events = await _run_authorize(intent_analyzer=analyzer)
    perm_event = next(e for e in events if e.type == EventType.PERMISSION_REQUIRED)
    assert "dangerous" in perm_event.data["intent"]


@pytest.mark.asyncio
@pytest.mark.slow
async def test_authorize_analyzer_timeout_does_not_crash():
    """A timeout must not crash _authorize (analysis degrades to intent=None).

    Uses a small injectable timeout (0.1s) to avoid a real 20s wait. The engine's
    intent_analyzer_timeout defaults to 20.0; tests inject 0.1.
    """
    import time

    def slow_analyzer(tc, p, m):
        time.sleep(0.3)  # well over the 0.1s test timeout
        return "never"

    events = await _run_authorize(intent_analyzer=slow_analyzer, analyzer_timeout=0.1)
    perm_event = next(e for e in events if e.type == EventType.PERMISSION_REQUIRED)
    assert perm_event.data["intent"] is None


@pytest.mark.asyncio
async def test_authorize_analyzer_raises_returns_none():
    """An analyzer that raises → intent=None."""

    def bad_analyzer(tc, p, m):
        raise RuntimeError("boom")

    events = await _run_authorize(intent_analyzer=bad_analyzer)
    perm_event = next(e for e in events if e.type == EventType.PERMISSION_REQUIRED)
    assert perm_event.data["intent"] is None


@pytest.mark.asyncio
async def test_authorize_stop_before_emit_no_card():
    """If Stop fired before the card is emitted, no PERMISSION_REQUIRED surfaces."""
    engine = _build_minimal_engine_with_shell(intent_analyzer=lambda *a: None)
    engine._cancel.set()  # stopped before analysis
    events = await _collect_authorize_events(engine, _shell_tool_call())
    perm_events = [e for e in events if e.type == EventType.PERMISSION_REQUIRED]
    assert len(perm_events) == 0  # no card flashed


@pytest.mark.asyncio
@pytest.mark.slow
async def test_authorize_stop_mid_analysis_no_card():
    """Stopping mid-analysis must also avoid flashing a card — _interruptible
    resolves early via its cancel_wait path."""
    import asyncio, time

    def slow_analyzer(tc, p, m):
        time.sleep(0.3)  # leaves room for a mid-flight cancel
        return "never"

    engine = _build_minimal_engine_with_shell(intent_analyzer=slow_analyzer, analyzer_timeout=1.0)

    async def cancel_after_start():
        await asyncio.sleep(0.05)  # analysis has started
        engine._cancel.set()

    asyncio.create_task(cancel_after_start())
    events = await _collect_authorize_events(engine, _shell_tool_call())
    perm_events = [e for e in events if e.type == EventType.PERMISSION_REQUIRED]
    assert len(perm_events) == 0


# -- build_engine passthrough --


def test_build_engine_passes_intent_analyzer():
    """build_engine forwards intent_analyzer to TurnEngine."""
    from coworker.agent import build_engine
    from coworker.agents import code_agent

    analyzer = lambda *a: "test"
    engine = build_engine(agent=code_agent(), workspace=".", intent_analyzer=analyzer)
    assert engine.intent_analyzer is analyzer


def test_build_engine_default_intent_analyzer_none():
    """Omitting intent_analyzer defaults to None (upstream behavior unchanged)."""
    from coworker.agent import build_engine
    from coworker.agents import code_agent

    engine = build_engine(agent=code_agent(), workspace=".")
    assert engine.intent_analyzer is None


# -- manager: approval_prompt_data + settings --


def test_approval_prompt_data_carries_intent():
    """approval_prompt_data writes request.intent into data (Inbox snapshot).

    Uses a real SessionManager (matching tests/test_settings.py); with no
    automation run, task_for_run_session returns None and we hit the early
    return, isolating the intent-carry behavior.
    """
    import tempfile

    from coworker.server.manager import SessionManager

    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager(data_dir=tmp)
        req = PermissionRequest(
            tool_name="run_shell",
            arguments={"command": "rm x"},
            metadata=None,
            reason="test",
            intent="• dangerous\n• irreversible",
        )
        data = manager.approval_prompt_data("session-1", req)
        assert data["intent"] == "• dangerous\n• irreversible"


def test_approval_prompt_data_no_intent_omits_field():
    """intent=None → data has no 'intent' key (avoids null pollution)."""
    import tempfile

    from coworker.server.manager import SessionManager

    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager(data_dir=tmp)
        req = PermissionRequest(
            tool_name="run_shell", arguments={}, metadata=None, reason="test", intent=None
        )
        data = manager.approval_prompt_data("session-1", req)
        assert "intent" not in data


def test_get_settings_includes_intent_analysis_default_false():
    """get_settings returns intent_analysis, defaulting to False (opt-in)."""
    import tempfile

    from coworker.server.manager import SessionManager

    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager(data_dir=tmp)
        settings = manager.get_settings()
        assert settings.get("intent_analysis") is False


def test_set_intent_analysis_via_rest():
    """POST /v1/settings/intent-analysis persists, GET reflects it."""
    import tempfile
    from fastapi.testclient import TestClient
    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager(data_dir=tmp)
        app = create_app(manager)
        client = TestClient(app)
        # default False (opt-in)
        assert client.get("/v1/settings").json().get("intent_analysis") is False
        # turn on
        r = client.post("/v1/settings/intent-analysis", json={"enabled": True})
        assert r.json()["intent_analysis"] is True
        # GET reflects
        assert client.get("/v1/settings").json().get("intent_analysis") is True


# -- helpers --


def _shell_tool_call():
    from coworker.engine import ToolCall

    return ToolCall(id="tc1", name="run_shell", arguments={"command": "rm x"})


async def _run_authorize(intent_analyzer=None, analyzer_timeout=None):
    """Run _authorize for a run_shell call and collect yielded events. Approver denies."""
    engine = _build_minimal_engine_with_shell(
        intent_analyzer=intent_analyzer, analyzer_timeout=analyzer_timeout
    )
    return await _collect_authorize_events(engine, _shell_tool_call())


async def _collect_authorize_events(engine, tool_call):
    from coworker.engine import ApprovalOutcome

    async def deny_approver(req):
        return ApprovalOutcome.DENY

    engine.approver = deny_approver
    events = []
    async for item in engine._authorize(tool_call):
        if hasattr(item, "type"):
            events.append(item)
    return events


def _build_minimal_engine_with_shell(intent_analyzer=None, analyzer_timeout=None):
    """Build an engine that can run run_shell _authorize (shell tool registered)."""
    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine, Mode
    from coworker.tools import ToolRegistry
    from coworker.tools.shell import shell_tools
    from coworker.providers.base import ProviderClient

    registry = ToolRegistry()
    registry.register_all(shell_tools(MagicMock()))
    kwargs = dict(
        provider=MagicMock(spec=ProviderClient),
        registry=registry,
        permissions=PermissionEngine(mode=Mode.INTERACTIVE, workspace_root="."),
        model="test",
        intent_analyzer=intent_analyzer,
    )
    if analyzer_timeout is not None:
        kwargs["intent_analyzer_timeout"] = analyzer_timeout
    return TurnEngine(**kwargs)
