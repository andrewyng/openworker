"""Engine + manager integration tests for approval-prompt intent analysis.

Covers: DI plumbing, the synchronous analyze-before-emit branch (success / None /
timeout / raise / user-stop), the composition contract with the permission pipeline
(no card \u2192 no analysis), payload coexistence with the upstream fields, and the
manager/app wiring incl. the annotation-language pref.
"""
import asyncio
import tempfile
import time
from unittest.mock import MagicMock

from coworker.engine import ApprovalOutcome, PermissionRequest, TurnEngine
from coworker.events import EventType
from coworker.permissions import PermissionEngine
from coworker.providers.base import ModelCapabilities, ToolCall
from coworker.tools import ToolRegistry
from coworker.tools.shell import shell_tools


# -- PermissionRequest + TurnEngine.__init__ --


def test_permission_request_has_intent_field():
    """PermissionRequest has an optional intent field (default None)."""
    req = PermissionRequest(tool_name="run_shell", arguments={}, metadata=None, reason="test")
    assert req.intent is None
    req2 = PermissionRequest(
        tool_name="run_shell", arguments={}, metadata=None, reason="test", intent="\u2022 x"
    )
    assert req2.intent == "\u2022 x"


def test_turn_engine_accepts_intent_analyzer_none():
    engine = _build_engine_with_shell()
    assert engine.intent_analyzer is None


def test_turn_engine_accepts_intent_analyzer_callable():
    analyzer = lambda tc, p, m: "\u2022 test"
    engine = _build_engine_with_shell(intent_analyzer=analyzer)
    assert engine.intent_analyzer is analyzer


def test_turn_engine_accepts_intent_analyzer_timeout():
    """Default 20.0; injectable for tests."""
    engine = _build_engine_with_shell()
    assert engine.intent_analyzer_timeout == 20.0
    engine2 = _build_engine_with_shell(analyzer_timeout=0.1)
    assert engine2.intent_analyzer_timeout == 0.1


# -- _authorize: analyze only when a card will actually be shown --


async def test_no_analyzer_payload_intent_none():
    """intent_analyzer=None \u2192 payload.intent is None (feature off = upstream behavior)."""
    events = await _run_authorize()
    ev = _perm_event(events)
    assert ev.data["intent"] is None


async def test_analyzer_success_payload_has_intent():
    def analyzer(tc, p, m):
        return "\u2022 dangerous\n\u2022 irreversible"

    events = await _run_authorize(intent_analyzer=analyzer)
    ev = _perm_event(events)
    assert "dangerous" in ev.data["intent"]


async def test_intent_coexists_with_upstream_payload_fields():
    """The intent annotation rides alongside the upstream card payload (readonly_ok,
    provenance, standing_target) without disturbing them."""
    def analyzer(tc, p, m):
        return "\u2022 reads files"

    events = await _run_authorize(intent_analyzer=analyzer, command="ls -la")
    ev = _perm_event(events)
    assert ev.data["intent"] == "\u2022 reads files"
    # upstream's own fields are intact on the same event
    assert "readonly_ok" in ev.data
    assert ev.data["readonly_ok"] is True  # ls -la classifies read-only


async def test_timeout_degrades_to_none_no_crash():
    """wait_for timeout must not crash _authorize; the card renders unannotated."""

    def slow_analyzer(tc, p, m):
        time.sleep(0.3)
        return "never"

    events = await _run_authorize(intent_analyzer=slow_analyzer, analyzer_timeout=0.1)
    ev = _perm_event(events)
    assert ev.data["intent"] is None


async def test_analyzer_raises_returns_none():
    def bad_analyzer(tc, p, m):
        raise RuntimeError("boom")

    events = await _run_authorize(intent_analyzer=bad_analyzer)
    ev = _perm_event(events)
    assert ev.data["intent"] is None


async def test_no_card_no_analysis():
    """Composition contract: when the permission pipeline resolves the call WITHOUT a
    card (here: a standing allow rule), the analyzer must never run \u2014 its round
    trip is only ever spent on a card the human will actually see."""
    calls = []

    def analyzer(tc, p, m):
        calls.append(tc.name)
        return "\u2022 x"

    engine = _build_engine_with_shell(intent_analyzer=analyzer)
    engine.permissions.allow_tool_for_session("run_shell")  # standing allow \u2192 no card
    items = await _collect_raw(engine, _tool_call())
    assert calls == []  # analyzer never invoked
    assert not [i for i in items if hasattr(i, "type") and i.type == EventType.PERMISSION_REQUIRED]
    # _authorize yields True to hand the call to the execution loop
    assert any(item is True for item in items)


async def test_stop_before_emit_no_card():
    """A Stop that lands before the card is emitted must not flash the card."""
    engine = _build_engine_with_shell(intent_analyzer=lambda *a: None)
    engine._cancel.set()
    events = await _collect(engine, _tool_call())
    assert not [e for e in events if e.type == EventType.PERMISSION_REQUIRED]


async def test_stop_mid_analysis_no_card():
    """Stopping mid-analysis also avoids the flash \u2014 _interruptible resolves via
    its cancel path and the post-analysis guard routes to the interrupted denial."""

    def slow_analyzer(tc, p, m):
        time.sleep(0.3)
        return "never"

    engine = _build_engine_with_shell(intent_analyzer=slow_analyzer, analyzer_timeout=1.0)

    async def cancel_after_start():
        await asyncio.sleep(0.05)
        engine._cancel.set()

    task = asyncio.get_running_loop().create_task(cancel_after_start())
    events = await _collect(engine, _tool_call())
    await task
    assert not [e for e in events if e.type == EventType.PERMISSION_REQUIRED]


# -- build_engine passthrough --


def test_build_engine_passes_intent_analyzer():
    from coworker.agent import build_engine
    from coworker.agents import code_agent

    analyzer = lambda *a: "test"
    engine = build_engine(agent=code_agent(), workspace=".", intent_analyzer=analyzer)
    assert engine.intent_analyzer is analyzer


def test_build_engine_default_intent_analyzer_none():
    from coworker.agent import build_engine
    from coworker.agents import code_agent

    engine = build_engine(agent=code_agent(), workspace=".")
    assert engine.intent_analyzer is None


# -- manager: pref, language, carry-through --


def test_get_settings_default_off():
    import tempfile

    from coworker.server.manager import SessionManager

    with tempfile.TemporaryDirectory() as tmp:
        assert SessionManager(data_dir=tmp).get_settings()["intent_analysis"] is False


def test_manager_injects_analyzer_when_pref_on_with_language(tmp_path):
    """Pref on \u2192 engines get an analyzer whose prompt language follows the stored
    annotation language (here: zh)."""
    from coworker.server.manager import SessionManager

    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager(data_dir=tmp)
        manager.set_intent_analysis(True, language="zh")
        engine = manager.get_engine("s1", agent="cowork", workspace=str(tmp_path))
        assert engine.intent_analyzer is not None
        # the wrapper binds the language: run it against a recording provider
        prov = MagicMock()
        prov.complete.return_value = MagicMock(text="\u2022 ok")
        engine.intent_analyzer(_tool_call(), prov, "m")
        system = prov.complete.call_args.kwargs["messages"][0]["content"]
        assert "\u4e25\u683c\u8f93\u51fa\u4e2d\u6587" in system


def test_manager_no_analyzer_when_pref_off(tmp_path):
    from coworker.server.manager import SessionManager

    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager(data_dir=tmp)
        engine = manager.get_engine("s1", agent="cowork", workspace=str(tmp_path))
        assert engine.intent_analyzer is None


def test_approval_prompt_data_carries_intent():
    """Parked (Inbox) approvals keep the annotation across reconnects."""
    import tempfile

    from coworker.server.manager import SessionManager

    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager(data_dir=tmp)
        req = PermissionRequest(
            tool_name="run_shell",
            arguments={"command": "rm x"},
            metadata=None,
            reason="test",
            intent="\u2022 dangerous\n\u2022 irreversible",
        )
        data = manager.approval_prompt_data("session-1", req)
        assert data["intent"] == "\u2022 dangerous\n\u2022 irreversible"


def test_approval_prompt_data_no_intent_omits_field():
    import tempfile

    from coworker.server.manager import SessionManager

    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager(data_dir=tmp)
        req = PermissionRequest(
            tool_name="run_shell", arguments={}, metadata=None, reason="test", intent=None
        )
        data = manager.approval_prompt_data("session-1", req)
        assert "intent" not in data


def test_rest_roundtrip_including_language():
    """POST persists flag + language; GET reflects both."""
    import tempfile

    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    with tempfile.TemporaryDirectory() as tmp:
        manager = SessionManager(data_dir=tmp)
        client = TestClient(create_app(manager))
        assert client.get("/v1/settings").json()["intent_analysis"] is False
        r = client.post(
            "/v1/settings/intent-analysis", json={"enabled": True, "language": "zh"}
        )
        assert r.json()["intent_analysis"] is True
        assert r.json()["intent_analysis_lang"] == "zh"
        settings = client.get("/v1/settings").json()
        assert settings["intent_analysis"] is True
        assert settings["intent_analysis_lang"] == "zh"


# -- helpers --


def _tool_call(command="rm x"):
    return ToolCall(id="tc1", name="run_shell", arguments={"command": command})


def _build_engine_with_shell(intent_analyzer=None, analyzer_timeout=None):
    registry = ToolRegistry()
    registry.register_all(shell_tools(MagicMock()))
    kwargs = dict(
        provider=MagicMock(),
        registry=registry,
        permissions=PermissionEngine(workspace_root="."),
        model="test",
        intent_analyzer=intent_analyzer,
    )
    if analyzer_timeout is not None:
        kwargs["intent_analyzer_timeout"] = analyzer_timeout
    return TurnEngine(**kwargs)


async def _collect(engine, tool_call):
    async def deny(req):
        return ApprovalOutcome.DENY

    engine.approver = deny
    return [item for item in await _collect_raw(engine, tool_call) if hasattr(item, "type")]


async def _collect_raw(engine, tool_call):
    async def deny(req):
        return ApprovalOutcome.DENY

    engine.approver = deny
    out = []
    async for item in engine._authorize(tool_call):
        out.append(item)  # both Events and the True/False flow signals
    return out


async def _run_authorize(intent_analyzer=None, analyzer_timeout=None, command="rm x"):
    engine = _build_engine_with_shell(
        intent_analyzer=intent_analyzer, analyzer_timeout=analyzer_timeout
    )
    return await _collect(engine, _tool_call(command))


def _perm_event(events):
    return next(e for e in events if e.type == EventType.PERMISSION_REQUIRED)
