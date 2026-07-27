"""P2 gate tests — turn engine + event bus (scripted provider, no network)."""

from __future__ import annotations

import asyncio
import threading
import time

import aisuite as ai
from coworker.engine import ApprovalOutcome, PermissionRequest, TurnEngine
from coworker.events import EventType
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    StreamChunk,
    ToolCall,
)
from coworker.tools import ToolRegistry


def _multi_tool_turn(calls):
    return AssistantTurn(
        tool_calls=[
            ToolCall(id=f"call_{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ],
        finish_reason="tool_calls",
    )


def _text_turn(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _tool_turn(name, args, call_id="call_1"):
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
    )


class ScriptedProvider(ProviderClient):
    """Returns queued AssistantTurns; streams via the base default (one final chunk)."""

    def __init__(self, turns, *, loop=False):
        self._turns = list(turns)
        self._loop = loop
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        return self._turns[0] if self._loop else self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, turns, *, approver=None, loop=False, max_iterations=12,
             mode=None, guard_middleware=None):
    provider = ScriptedProvider(turns, loop=loop)
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    permissions = PermissionEngine(workspace_root=tmp_path, mode=mode or Mode.INTERACTIVE)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        approver=approver,
        max_iterations=max_iterations,
        guard_middleware=guard_middleware,
    )
    return engine, provider


def _collect(engine, user_input):
    async def _run():
        return [ev async for ev in engine.run(user_input)]

    return asyncio.run(_run())


def _types(events):
    return [ev.type for ev in events]


# -- tests ----------------------------------------------------------------------


def test_no_tool_turn(tmp_path):
    engine, _ = _engine(tmp_path, [_text_turn("all done")])
    events = _collect(engine, "hi")
    assert _types(events) == [
        EventType.TURN_START,
        EventType.ASSISTANT_MESSAGE,
        EventType.TURN_END,
    ]
    assert events[1].data["text"] == "all done"
    assert events[-1].data["status"] == "completed"


def test_tool_turn_order_and_execution(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine, _ = _engine(
        tmp_path,
        [_tool_turn("read_file", {"path": "a.txt"}), _text_turn("it says hello")],
    )
    events = _collect(engine, "read a.txt")
    assert EventType.PERMISSION_REQUIRED not in _types(events)
    assert _types(events) == [
        EventType.TURN_START,
        EventType.ASSISTANT_MESSAGE,
        EventType.TOOL_PROPOSED,
        EventType.TOOL_STARTED,
        EventType.TOOL_FINISHED,
        EventType.ITERATION_END,
        EventType.ASSISTANT_MESSAGE,
        EventType.TURN_END,
    ]
    finished = next(e for e in events if e.type == EventType.TOOL_FINISHED)
    assert finished.data["status"] == "ok"
    assert any(
        m.get("role") == "tool" and "hello" in m["content"] for m in engine.messages
    )


def test_write_requires_approval_then_approved(tmp_path):
    async def approve_once(_req: PermissionRequest):
        return ApprovalOutcome.ONCE

    engine, _ = _engine(
        tmp_path,
        [
            _tool_turn("write_file", {"path": "new.py", "content": "print(1)\n"}),
            _text_turn("wrote new.py"),
        ],
        approver=approve_once,
    )
    events = _collect(engine, "create new.py")
    assert EventType.PERMISSION_REQUIRED in _types(events)
    assert (tmp_path / "new.py").read_text() == "print(1)\n"


def test_denied_tool_yields_error_and_continues(tmp_path):
    async def deny(_req: PermissionRequest):
        return ApprovalOutcome.DENY

    engine, _ = _engine(
        tmp_path,
        [
            _tool_turn("write_file", {"path": "new.py", "content": "x"}),
            _text_turn("ok, skipped it"),
        ],
        approver=deny,
    )
    events = _collect(engine, "create new.py")
    assert not (tmp_path / "new.py").exists()
    finished = next(e for e in events if e.type == EventType.TOOL_FINISHED)
    assert finished.data["status"] == "denied"
    assert _types(events)[-1] == EventType.TURN_END
    assert any(
        m.get("role") == "tool" and "not executed" in m["content"]
        for m in engine.messages
    )


def test_max_iterations_rail(tmp_path):
    engine, provider = _engine(
        tmp_path, [_tool_turn("list_files", {})], loop=True, max_iterations=3
    )
    events = _collect(engine, "loop forever")
    end = events[-1]
    assert end.type == EventType.TURN_END
    assert end.data["status"] == "max_iterations_exceeded"
    assert provider.calls == 3


def test_interrupt_between_iterations(tmp_path):
    engine_holder = {}

    async def approve_and_interrupt(_req: PermissionRequest):
        engine_holder["engine"].request_interrupt()
        return ApprovalOutcome.ONCE

    engine, provider = _engine(
        tmp_path,
        [
            _tool_turn("write_file", {"path": "x.py", "content": "x"}),
            _text_turn("should not be reached"),
        ],
        approver=approve_and_interrupt,
    )
    engine_holder["engine"] = engine
    events = _collect(engine, "do a thing")
    assert events[-1].type == EventType.INTERRUPTED
    assert provider.calls == 1


def test_steering_injects_next_turn(tmp_path):
    engine, provider = _engine(tmp_path, [_text_turn("first"), _text_turn("second")])
    engine.queue_steering("actually, also do this")
    events = _collect(engine, "do the first thing")
    assert provider.calls == 2
    assert any(
        m.get("role") == "user" and m["content"] == "actually, also do this"
        for m in engine.messages
    )
    assert events[-1].data["status"] == "completed"


# -- parallel tool execution ------------------------------------------------------


def _bare_engine(tmp_path, turns, *, mode=None, guard_middleware=None):
    provider = ScriptedProvider(turns)
    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path, mode=mode or Mode.INTERACTIVE)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        guard_middleware=guard_middleware,
    )
    return engine, registry


def test_low_risk_tool_calls_run_concurrently(tmp_path):
    # Both tools block on a 2-party barrier: the turn only completes if the engine
    # really runs them at the same time (sequential execution would trip the timeout
    # and surface as an error result).
    barrier = threading.Barrier(2, timeout=5)
    low = ai.ToolMetadata(category="search", risk_level="low", requires_approval=False)

    def side_a():
        """Wait for side_b."""
        barrier.wait()
        return {"side": "a"}

    def side_b():
        """Wait for side_a."""
        barrier.wait()
        return {"side": "b"}

    engine, registry = _bare_engine(
        tmp_path,
        [_multi_tool_turn([("side_a", {}), ("side_b", {})]), _text_turn("done")],
    )
    registry.register(side_a, metadata=low)
    registry.register(side_b, metadata=low)

    events = _collect(engine, "go")
    finished = [e for e in events if e.type == EventType.TOOL_FINISHED]
    assert len(finished) == 2
    assert all(e.data["status"] == "ok" for e in finished)
    # a tool result message exists for every call id
    tool_ids = {
        m.get("tool_call_id") for m in engine.messages if m.get("role") == "tool"
    }
    assert tool_ids == {"call_0", "call_1"}


def test_non_low_risk_tool_calls_stay_sequential(tmp_path):
    order = []
    medium = ai.ToolMetadata(
        category="filesystem", risk_level="medium", requires_approval=False
    )

    def first():
        """Record start/end with a delay."""
        order.append("first-start")
        time.sleep(0.2)
        order.append("first-end")
        return "ok"

    def second():
        """Record start/end."""
        order.append("second-start")
        order.append("second-end")
        return "ok"

    engine, registry = _bare_engine(
        tmp_path,
        [_multi_tool_turn([("first", {}), ("second", {})]), _text_turn("done")],
    )
    registry.register(first, metadata=medium)
    registry.register(second, metadata=medium)

    _collect(engine, "go")
    assert order == ["first-start", "first-end", "second-start", "second-end"]


class StreamingProvider(ProviderClient):
    def complete(self, **kwargs):  # pragma: no cover - streamed instead
        raise NotImplementedError

    def capabilities(self, model):
        return ModelCapabilities()

    def stream(self, *, model, messages, tools=None, **settings):
        for piece in ["Hel", "lo, ", "world"]:
            yield StreamChunk(text_delta=piece)
        yield StreamChunk(turn=AssistantTurn(text="Hello, world", finish_reason="stop"))


def test_streaming_emits_deltas(tmp_path):
    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=StreamingProvider(),
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
    )
    events = _collect(engine, "say hi")
    deltas = [e.data["text"] for e in events if e.type == EventType.ASSISTANT_DELTA]
    assert deltas == ["Hel", "lo, ", "world"]
    final = next(e for e in events if e.type == EventType.ASSISTANT_MESSAGE)
    assert final.data["text"] == "Hello, world"
    assert events[-1].type == EventType.TURN_END


def _pdf_file_part():
    import base64
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    url = "data:application/pdf;base64," + base64.b64encode(buf.getvalue()).decode()
    return {"type": "file", "file": {"filename": "d.pdf", "file_data": url}}


def test_outbound_adapts_pdf_for_non_pdf_models(tmp_path):
    # ScriptedProvider reports default caps (pdf=False) → the file part must be
    # replaced at send time while the stored history keeps the real document.
    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    engine.messages.append(
        {
            "role": "user",
            "content": [{"type": "text", "text": "read this"}, _pdf_file_part()],
        }
    )
    parts = engine._outbound_messages()[-1]["content"]
    assert all(p["type"] != "file" for p in parts)
    assert "d.pdf" in parts[-1]["text"]
    assert engine.messages[-1]["content"][1]["type"] == "file"  # history untouched


def test_outbound_keeps_pdf_for_native_models(tmp_path):
    class NativeProvider(ScriptedProvider):
        def capabilities(self, model):
            return ModelCapabilities(vision=True, pdf=True)

    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    engine.provider = NativeProvider([_text_turn("ok")])
    message = {
        "role": "user",
        "content": [{"type": "text", "text": "read this"}, _pdf_file_part()],
    }
    engine.messages.append(message)
    assert engine._outbound_messages()[-1]["content"][1]["type"] == "file"


def test_provider_extras_persist_on_message_and_survive_outbound(tmp_path):
    """A turn's provider-private sidecar (`extras`, e.g. Gemini thought signatures) rides
    the persisted assistant message and is NOT stripped by _outbound_messages — the owning
    provider needs it back; foreign providers strip it themselves."""
    turn = AssistantTurn(
        text="ok",
        finish_reason="stop",
        extras={"_gemini": {"text_sig": "c2ln", "call_sigs": []}},
    )
    engine, _ = _engine(tmp_path, [turn])
    _collect(engine, "hi")

    persisted = engine.messages[-1]
    assert persisted["_gemini"] == {"text_sig": "c2ln", "call_sigs": []}
    outbound = engine._outbound_messages()[-1]
    assert outbound["_gemini"] == {"text_sig": "c2ln", "call_sigs": []}
    assert "ts" not in outbound  # display sidecars still stripped


def test_switch_model_appends_notice_only_midsession(tmp_path):
    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    # Fresh session: first bind is silent.
    assert engine.switch_model("zai:glm-5.2") is None
    assert engine.model == "zai:glm-5.2"
    _collect(engine, "hi")
    # Same model: no-op.
    assert engine.switch_model("zai:glm-5.2") is None
    # Real mid-session switch: persisted marker with the matrix label.
    text = engine.switch_model("kimi:kimi-k2.6")
    assert "Kimi K2.6" in text and engine.model == "kimi:kimi-k2.6"
    notice = engine.messages[-1]
    assert notice["role"] == "notice" and notice["kind"] == "model_switch"
    assert all(m.get("role") != "notice" for m in engine._outbound_messages())


def test_switch_model_warns_when_images_meet_text_only_model(tmp_path):
    class NoVisionProvider(ScriptedProvider):
        def capabilities(self, model):
            return ModelCapabilities(vision=False)

    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    engine.provider = NoVisionProvider([_text_turn("ok")])
    engine.messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ],
        }
    )
    text = engine.switch_model("zai:glm-5.2")
    assert "images" in text  # degradation is called out in the marker


# -- guard middleware integration ------------------------------------------------


def _guard_engine(tmp_path, turns, rules, *, mode=None, loop=False):
    """Build an engine wired with GuardMiddleware for integration tests.
    Delegates to ``_engine()`` then attaches the guard middleware."""
    from coworker.guard.middleware import GuardMiddleware
    from coworker.guard.ruleset import GuardRuleSet

    engine, provider = _engine(
        tmp_path, turns, mode=mode or Mode.AUTO, loop=loop,
    )
    guard = GuardMiddleware(
        engine.permissions,
        ruleset=GuardRuleSet(rules),
        log_path=str(tmp_path / "guard.log"),
    )
    engine.guard_middleware = guard
    return engine, provider


def test_guard_blocks_disallowed_tool(tmp_path):
    """GuardMiddleware denies a tool that matches a deny rule — the engine
    surfaces it as a denied tool with a guard: prefixed reason, and the file
    is NOT written."""
    from coworker.guard.ruleset import GuardRule

    rules = [
        GuardRule(
            name="block-writes",
            tool="write_file",
            action="deny",
            reason="All writes blocked by guard rule",
        ),
    ]
    engine, _ = _guard_engine(
        tmp_path,
        [
            _tool_turn("write_file", {"path": "x.py", "content": "print(1)\n"}),
            _text_turn("blocked by guard"),
        ],
        rules=rules,
    )
    events = _collect(engine, "write x.py")

    finished = [e for e in events if e.type == EventType.TOOL_FINISHED]
    assert len(finished) == 1
    assert finished[0].data["status"] == "denied"
    assert "guard:" in finished[0].data.get("reason", "")

    # Permission was AUTO so the permission engine itself did NOT block;
    # only the guard layer stopped it. Verify no file was written.
    assert not (tmp_path / "x.py").exists()

    # The error message in history should cite the guard rule.
    assert any(
        m.get("role") == "tool"
        and "guard:" in m.get("content", "")
        and "not executed" in m.get("content", "")
        for m in engine.messages
    )


def test_guard_blocks_in_read_only_mode_via_permission_engine(tmp_path):
    """In PLAN mode, the guard middleware respects the permission engine's
    read-only gate — a consequential tool is blocked even without a matching
    guard rule."""

    # Empty ruleset — the guard has no opinion; the permission engine should
    # still block the write in plan mode.
    engine, _ = _guard_engine(
        tmp_path,
        [
            _tool_turn("write_file", {"path": "x.py", "content": "x"}),
            _text_turn("blocked"),
        ],
        rules=[],
        mode=Mode.PLAN,
    )
    events = _collect(engine, "write x.py")

    finished = [e for e in events if e.type == EventType.TOOL_FINISHED]
    assert len(finished) == 1
    assert finished[0].data["status"] == "denied"
    # The reason should cite plan mode being read-only, not the guard.
    assert "read-only" in finished[0].data.get("reason", "").lower()
    assert not (tmp_path / "x.py").exists()


def _guard_bare_engine(tmp_path, turns, rules):
    """Build a bare engine with GuardMiddleware and no pre-registered tools.
    Delegates to ``_bare_engine()`` then attaches the guard middleware.
    Caller registers tools on the returned ``registry``."""
    from coworker.guard.middleware import GuardMiddleware
    from coworker.guard.ruleset import GuardRuleSet

    engine, registry = _bare_engine(tmp_path, turns)
    guard = GuardMiddleware(
        engine.permissions,
        ruleset=GuardRuleSet(rules),
        log_path=str(tmp_path / "guard.log"),
    )
    engine.guard_middleware = guard
    return engine, registry


def test_guard_allows_non_matching_tool(tmp_path):
    """GuardMiddleware lets through a tool call when no rule matches."""
    from coworker.guard.ruleset import GuardRule

    (tmp_path / "a.txt").write_text("hello")

    # A guard rule that only targets run_shell, not read_file.
    rules = [
        GuardRule(
            name="block-dangerous-shell",
            tool="run_shell",
            action="deny",
            match_command_contains="rm -rf",
        ),
    ]
    engine, _ = _guard_engine(
        tmp_path,
        [
            _tool_turn("read_file", {"path": "a.txt"}),
            _text_turn("it says hello"),
        ],
        rules=rules,
    )
    events = _collect(engine, "read a.txt")

    finished = [e for e in events if e.type == EventType.TOOL_FINISHED]
    assert len(finished) == 1
    assert finished[0].data["status"] == "ok"

    # The result content made it through.
    assert any(
        m.get("role") == "tool" and "hello" in m.get("content", "")
        for m in engine.messages
    )


def test_guard_concurrent_tools_pass_through_within_turn(tmp_path):
    """Two concurrent low-risk tools both pass GuardMiddleware within a single
    turn.  The engine authorises ALL tools before executing ANY of them, so the
    fan-out counter is always 0 during evaluation within one turn — the limit
    can only fire *across* iterations in the current architecture.

    This test verifies the wiring: barrier-based concurrency + guard middleware
    + track_start/track_end all work together, and that after the turn the
    fan-out counter is back to 0.
    """
    from coworker.guard.ruleset import GuardRule

    barrier = threading.Barrier(2, timeout=5)
    low = ai.ToolMetadata(
        category="search", risk_level="low", requires_approval=False
    )

    # Use an empty tool name (match any tool) so the rule fires against
    # whatever the registered function is called.
    rules = [
        GuardRule(
            name="limit-concurrent",
            tool="",
            action="deny",
            max_concurrent=1,
        ),
    ]

    def explore(task: str):
        barrier.wait()
        return {"report": task}

    engine, registry = _guard_bare_engine(
        tmp_path,
        [
            _multi_tool_turn(
                [("explore", {"task": "x"}), ("explore", {"task": "y"})]
            ),
            _text_turn("done"),
        ],
        rules,
    )
    registry.register(explore, metadata=low)

    events = _collect(engine, "research both")
    finished = [e for e in events if e.type == EventType.TOOL_FINISHED]
    assert len(finished) == 2
    # Both succeed because authorisation happens before track_start —
    # the fan-out counter is 0 when both are evaluated.
    assert all(e.data["status"] == "ok" for e in finished)

    # The fan-out counter must be back to 0 after the turn completes.
    guard = engine.guard_middleware
    assert guard._ruleset._fanout_counters.get("explore", 0) == 0


def test_guard_serial_tools_track_correctly(tmp_path):
    """Serial tools with max_concurrent=1 both pass because each finishes before
    the next authorises.  Verifies the engine calls track_start/track_end through
    the guard middleware for non-concurrent (medium-risk) tools.
    """
    from coworker.guard.ruleset import GuardRule

    medium = ai.ToolMetadata(
        category="filesystem", risk_level="medium", requires_approval=False
    )

    # Empty tool name = match any tool.
    rules = [
        GuardRule(
            name="limit-serial",
            tool="",
            action="deny",
            max_concurrent=1,
        ),
    ]
    order: list[str] = []

    def serial_tool():
        order.append("start")
        time.sleep(0.1)
        order.append("end")
        return "ok"

    engine, registry = _guard_bare_engine(
        tmp_path,
        [
            _multi_tool_turn([("serial_tool", {}), ("serial_tool", {})]),
            _text_turn("done"),
        ],
        rules,
    )
    registry.register(serial_tool, metadata=medium)

    _collect(engine, "go")

    # Serial execution order: they run one at a time even when the turn
    # requests two calls (medium-risk = not parallel_safe).
    assert order == ["start", "end", "start", "end"]

    # Fan-out counters reset after turn.
    guard = engine.guard_middleware
    assert guard._ruleset._fanout_counters.get("serial_tool", 0) == 0


def test_guard_concurrent_fanout_limit_denies_overflow(tmp_path):
    """Three concurrent low-risk tools with max_concurrent=2 — the first two
    succeed (proved concurrent via threading.Barrier) and the third is denied
    by the guard middleware's eager ``track_start`` check.

    Unlike the earlier ``pass_through_within_turn`` test, the empty-tool
    rule fires on every ``track_start`` call, so the moment counter reaches
    *max_concurrent* the next tool is blocked before it ever executes.
    """
    from coworker.guard.ruleset import GuardRule

    barrier = threading.Barrier(2, timeout=5)
    low = ai.ToolMetadata(
        category="search", risk_level="low", requires_approval=False
    )

    rules = [
        GuardRule(
            name="limit-concurrent",
            tool="",
            action="deny",
            max_concurrent=2,
        ),
    ]

    def explore(task: str):
        barrier.wait()
        return {"report": task}

    engine, registry = _guard_bare_engine(
        tmp_path,
        [
            _multi_tool_turn(
                [
                    ("explore", {"task": "a"}),
                    ("explore", {"task": "b"}),
                    ("explore", {"task": "c"}),
                ]
            ),
            _text_turn("done"),
        ],
        rules,
    )
    registry.register(explore, metadata=low)

    events = _collect(engine, "research three things")
    finished = [e for e in events if e.type == EventType.TOOL_FINISHED]
    assert len(finished) == 3

    ok_tools = [e for e in finished if e.data["status"] == "ok"]
    denied_tools = [e for e in finished if e.data["status"] == "denied"]

    # Two succeed (concurrent, barrier proves it), one is denied by fanout.
    assert len(ok_tools) == 2, f"expected 2 ok, got {len(ok_tools)}"
    assert len(denied_tools) == 1, f"expected 1 denied, got {len(denied_tools)}"

    # Denied tool cites the guard.
    assert "guard:" in denied_tools[0].data.get("reason", "")

    # Fan-out counter reset after turn completes.
    guard = engine.guard_middleware
    assert guard._ruleset._fanout_counters.get("explore", 0) == 0


def test_guard_middleware_exception_fails_safe_via_engine(tmp_path):
    """When the guard middleware's ruleset raises during ``evaluate()``, the
    middleware catches the exception and returns ``Decision(needs_user=True)``.
    The engine emits ``PERMISSION_REQUIRED``, then the default approver
    (``_deny_all``) denies the tool.
    """

    class _BrokenRuleset:
        def evaluate(self, tool_name, arguments):
            raise RuntimeError("guard ruleset crash")

        def track_start(self, tool_name):
            return True

        def track_end(self, tool_name):
            pass

    from coworker.guard.middleware import GuardMiddleware

    provider = ScriptedProvider(
        [_tool_turn("read_file", {"path": "x.txt"}), _text_turn("done")]
    )
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path)))
    permissions = PermissionEngine(workspace_root=tmp_path)
    guard = GuardMiddleware(
        permissions,
        ruleset=_BrokenRuleset(),  # type: ignore[arg-type]
        log_path=str(tmp_path / "guard.log"),
    )
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        guard_middleware=guard,
    )

    events = _collect(engine, "read x.txt")

    # The middleware exception triggers a permission prompt (fail safe).
    assert EventType.PERMISSION_REQUIRED in _types(events)

    # The PERMISSION_REQUIRED event carries the guard error in its reason.
    perm_events = [e for e in events if e.type == EventType.PERMISSION_REQUIRED]
    assert any("guard" in e.data.get("reason", "") for e in perm_events)

    # The default approver (_deny_all) denies the tool.
    finished = [e for e in events if e.type == EventType.TOOL_FINISHED]
    assert len(finished) == 1
    assert finished[0].data["status"] == "denied"

    # The history carries an explanation.
    assert any(
        m.get("role") == "tool" and "not executed" in m.get("content", "")
        for m in engine.messages
    )


def test_outbound_replaces_images_for_non_vision_models(tmp_path):
    class NoVisionProvider(ScriptedProvider):
        def capabilities(self, model):
            return ModelCapabilities(vision=False)

    engine, _ = _engine(tmp_path, [_text_turn("ok")])
    engine.provider = NoVisionProvider([_text_turn("ok")])
    engine.messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ],
        }
    )
    parts = engine._outbound_messages()[-1]["content"]
    assert all(p["type"] != "image_url" for p in parts)
    assert "not viewable" in parts[-1]["text"]
    assert engine.messages[-1]["content"][1]["type"] == "image_url"  # history untouched
