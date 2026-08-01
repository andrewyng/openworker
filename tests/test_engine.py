"""P2 gate tests — turn engine + event bus (scripted provider, no network)."""

from __future__ import annotations

import asyncio
import threading
import time

import aisuite as ai
from coworker.browser_security.destination import DestinationPolicy
from coworker.browser_security.site_permissions import (
    BrowserSitePermissionStore,
)
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


def _engine(tmp_path, turns, *, approver=None, loop=False, max_iterations=12):
    provider = ScriptedProvider(turns, loop=loop)
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        approver=approver,
        max_iterations=max_iterations,
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


def test_browser_consequential_action_still_confirms_in_full_access(tmp_path):
    requests: list[PermissionRequest] = []

    async def approve_once(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ONCE

    provider = ScriptedProvider(
        [_tool_turn("browser_click", {"tab_id": "t", "snapshot_id": "s", "ref": "r"}), _text_turn("done")]
    )
    registry = ToolRegistry()

    def browser_click(tab_id: str, snapshot_id: str, ref: str):
        return {"ok": True, "tab_id": tab_id, "snapshot_id": snapshot_id, "ref": ref}

    browser_click.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="browser_click",
        category="connector",
        risk_level="medium",
        capabilities=["browser"],
        requires_approval=True,
    )
    registry.register(browser_click)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO),
        model="gpt-5.5",
        approver=approve_once,
    )

    events = _collect(engine, "click it")
    assert EventType.PERMISSION_REQUIRED in _types(events)
    assert [request.tool_name for request in requests] == ["browser_click"]


def test_browser_approval_never_persists_typed_values(tmp_path):
    requests: list[PermissionRequest] = []
    executed: list[dict[str, str]] = []

    async def approve_once(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ONCE

    raw_arguments = {
        "tab_id": "tab_1",
        "snapshot_id": "snap_1",
        "ref": "field_1",
        "value": "super-secret-password",
    }
    provider = ScriptedProvider(
        [_tool_turn("browser_fill", raw_arguments), _text_turn("done")]
    )
    registry = ToolRegistry()

    def browser_fill(
        tab_id: str, snapshot_id: str, ref: str, value: str
    ):
        executed.append(
            {
                "tab_id": tab_id,
                "snapshot_id": snapshot_id,
                "ref": ref,
                "value": value,
            }
        )
        return {"ok": True}

    browser_fill.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="browser_fill",
        category="connector",
        risk_level="medium",
        capabilities=["browser"],
        requires_approval=True,
    )
    registry.register(browser_fill)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(
            workspace_root=tmp_path, mode=Mode.AUTO
        ),
        model="gpt-5.5",
        approver=approve_once,
    )

    events = _collect(engine, "fill the password")
    permission = next(
        event
        for event in events
        if event.type == EventType.PERMISSION_REQUIRED
    )
    assert permission.data["arguments"]["value"] == (
        "[redacted browser input]"
    )
    assert requests[0].arguments["value"] == "[redacted browser input]"
    assert executed == [raw_arguments]


def test_browser_dialog_always_confirms_and_redacts_prompt_text(tmp_path):
    requests: list[PermissionRequest] = []
    executed: list[dict[str, str]] = []

    async def approve_once(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ONCE

    raw_arguments = {
        "action": "accept",
        "prompt_text": "dialog-secret-value",
    }
    provider = ScriptedProvider(
        [_tool_turn("browser_dialog", raw_arguments), _text_turn("done")]
    )
    registry = ToolRegistry()

    def browser_dialog(action: str, prompt_text: str = ""):
        executed.append({"action": action, "prompt_text": prompt_text})
        return {"ok": True}

    # Even an incorrectly low-risk declaration must not bypass the Browser Use
    # point-of-action confirmation boundary.
    browser_dialog.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="browser_dialog",
        category="connector",
        risk_level="low",
        capabilities=["browser"],
        requires_approval=False,
    )
    registry.register(browser_dialog)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(
            workspace_root=tmp_path, mode=Mode.AUTO
        ),
        model="gpt-5.5",
        approver=approve_once,
    )

    events = _collect(engine, "accept the dialog")
    assert EventType.PERMISSION_REQUIRED in _types(events)
    assert requests[0].arguments == {
        "action": "accept",
        "prompt_text": "[redacted browser input]",
    }
    assert executed == [raw_arguments]


def test_browser_routine_actions_do_not_need_redundant_session_grant(tmp_path):
    requests: list[PermissionRequest] = []
    executed: list[str] = []

    async def approve(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ONCE

    provider = ScriptedProvider(
        [
            _tool_turn(
                "browser_open_url",
                {"url": "https://example.com"},
                call_id="browser-open",
            ),
            _tool_turn(
                "browser_click",
                {"tab_id": "t", "snapshot_id": "s", "ref": "ordinary"},
                call_id="browser-click",
            ),
            _text_turn("done"),
        ]
    )
    registry = ToolRegistry()

    def browser_open_url(url: str):
        executed.append("open")
        return {"ok": True, "url": url}

    def browser_click(tab_id: str, snapshot_id: str, ref: str):
        executed.append("click")
        return {"ok": True}

    for fn in (browser_open_url, browser_click):
        fn.__aisuite_tool_metadata__ = ai.ToolMetadata(
            name=fn.__name__,
            category="connector",
            risk_level="medium",
            capabilities=["browser"],
            requires_approval=True,
        )
        fn.__coworker_browser_confirmation__ = lambda _arguments: {
            "requires_confirmation": False,
            "reasons": [],
        }
        registry.register(fn)

    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO),
        model="gpt-5.5",
        approver=approve,
    )
    events = _collect(engine, "browse")

    assert not [
        event for event in events if event.type == EventType.PERMISSION_REQUIRED
    ]
    assert requests == []
    assert executed == ["open", "click"]


def test_browser_hostname_approval_persists_across_tasks(tmp_path):
    requests: list[PermissionRequest] = []
    executed: list[str] = []
    policy = BrowserSitePermissionStore(
        tmp_path / "browser-settings.json",
        destination_policy=DestinationPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )

    async def approve(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ONCE

    def make_engine(call_id: str) -> TurnEngine:
        provider = ScriptedProvider(
            [
                _tool_turn(
                    "browser_open_url",
                    {"url": "https://example.com/private?token=secret"},
                    call_id=call_id,
                ),
                _text_turn("done"),
            ]
        )
        registry = ToolRegistry()

        def browser_open_url(url: str):
            executed.append(url)
            return {"ok": True, "url": url}

        browser_open_url.__aisuite_tool_metadata__ = ai.ToolMetadata(
            name="browser_open_url",
            category="connector",
            risk_level="medium",
            capabilities=["browser"],
            requires_approval=True,
        )
        browser_open_url.__coworker_browser_confirmation__ = (
            lambda _arguments: {
                "requires_confirmation": False,
                "reasons": [],
            }
        )
        registry.register(browser_open_url)
        permissions = PermissionEngine(workspace_root=tmp_path)
        permissions.browser_site_policy = policy
        return TurnEngine(
            provider=provider,
            registry=registry,
            permissions=permissions,
            model="gpt-5.5",
            approver=approve,
        )

    first_events = _collect(make_engine("site-1"), "open it")
    second_events = _collect(make_engine("site-2"), "open it again")

    prompts = [
        event
        for event in [*first_events, *second_events]
        if event.type == EventType.PERMISSION_REQUIRED
    ]
    assert len(prompts) == 1
    assert prompts[0].data["scope"] == "browser_site"
    assert prompts[0].data["arguments"]["url"] == "https://example.com"
    assert prompts[0].data["arguments"]["origin"] == "https://example.com"
    assert [request.scope for request in requests] == ["browser_site"]
    assert policy.settings()["allowed_hosts"] == ["example.com"]
    assert len(executed) == 2


def test_blocked_browser_hostname_is_denied_without_prompt_or_execution(
    tmp_path,
):
    requests: list[PermissionRequest] = []
    executed: list[str] = []
    policy = BrowserSitePermissionStore(
        tmp_path / "browser-settings.json",
        destination_policy=DestinationPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    policy.update(blocked_hosts=["example.com"])

    async def approve(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ONCE

    provider = ScriptedProvider(
        [
            _tool_turn(
                "browser_open_url",
                {"url": "https://example.com/private"},
            ),
            _text_turn("blocked"),
        ]
    )
    registry = ToolRegistry()

    def browser_open_url(url: str):
        executed.append(url)
        return {"ok": True}

    browser_open_url.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="browser_open_url",
        category="connector",
        risk_level="medium",
        capabilities=["browser"],
        requires_approval=True,
    )
    browser_open_url.__coworker_browser_confirmation__ = lambda _arguments: {
        "requires_confirmation": False,
        "reasons": [],
    }
    registry.register(browser_open_url)
    permissions = PermissionEngine(workspace_root=tmp_path)
    permissions.browser_site_policy = policy
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        approver=approve,
    )

    events = _collect(engine, "open it")
    assert EventType.PERMISSION_REQUIRED not in _types(events)
    assert requests == []
    assert executed == []
    finished = next(
        event for event in events if event.type == EventType.TOOL_FINISHED
    )
    assert finished.data["status"] == "denied"
    assert "example.com" in finished.data["reason"]


def test_cross_site_consequential_click_asks_site_then_action(tmp_path):
    requests: list[PermissionRequest] = []
    executed: list[str] = []
    policy = BrowserSitePermissionStore(
        tmp_path / "browser-settings.json",
        destination_policy=DestinationPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )

    async def approve(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ONCE

    provider = ScriptedProvider(
        [
            _tool_turn(
                "browser_click",
                {"tab_id": "t", "snapshot_id": "s", "ref": "checkout"},
            ),
            _text_turn("done"),
        ]
    )
    registry = ToolRegistry()

    def browser_click(tab_id: str, snapshot_id: str, ref: str):
        executed.append(ref)
        return {"ok": True}

    browser_click.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="browser_click",
        category="connector",
        risk_level="medium",
        capabilities=["browser"],
        requires_approval=True,
    )
    browser_click.__coworker_browser_confirmation__ = lambda _arguments: {
        "requires_confirmation": True,
        "reasons": ["consequential_control"],
        "destination_url": "https://checkout.example/pay?secret=1",
    }
    registry.register(browser_click)
    permissions = PermissionEngine(workspace_root=tmp_path)
    permissions.browser_site_policy = policy
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        approver=approve,
    )

    events = _collect(engine, "click checkout")
    assert [
        event.data["scope"]
        for event in events
        if event.type == EventType.PERMISSION_REQUIRED
    ] == ["browser_site", "browser_action"]
    assert [request.scope for request in requests] == [
        "browser_site",
        "browser_action",
    ]
    assert policy.settings()["allowed_hosts"] == ["checkout.example"]
    assert executed == ["checkout"]


def test_user_navigated_site_is_gated_before_agent_read(tmp_path):
    requests: list[PermissionRequest] = []
    executed: list[str] = []
    policy = BrowserSitePermissionStore(
        tmp_path / "browser-settings.json",
        destination_policy=DestinationPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )

    async def approve(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ONCE

    provider = ScriptedProvider(
        [_tool_turn("browser_snapshot", {}), _text_turn("done")]
    )
    registry = ToolRegistry()

    def browser_snapshot():
        executed.append("snapshot")
        return {"ok": True}

    browser_snapshot.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="browser_snapshot",
        category="connector",
        risk_level="medium",
        capabilities=["browser"],
        requires_approval=False,
    )
    browser_snapshot.__coworker_browser_confirmation__ = lambda _arguments: {
        "requires_confirmation": False,
        "reasons": [],
        "current_url": "https://manual.example/account?private=1",
    }
    registry.register(browser_snapshot)
    permissions = PermissionEngine(workspace_root=tmp_path)
    permissions.browser_site_policy = policy
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        approver=approve,
    )

    events = _collect(engine, "read this page")
    assert [
        event.data["scope"]
        for event in events
        if event.type == EventType.PERMISSION_REQUIRED
    ] == ["browser_site"]
    assert requests[0].arguments["origin"] == "https://manual.example"
    assert executed == ["snapshot"]


def test_browser_consequential_actions_confirm_each_time_after_session_grant(
    tmp_path,
):
    requests: list[PermissionRequest] = []
    executed: list[str] = []

    async def approve(request: PermissionRequest):
        requests.append(request)
        return ApprovalOutcome.ONCE

    provider = ScriptedProvider(
        [
            _tool_turn(
                "browser_click",
                {"tab_id": "t", "snapshot_id": "s1", "ref": "submit"},
                call_id="submit-1",
            ),
            _tool_turn(
                "browser_click",
                {"tab_id": "t", "snapshot_id": "s2", "ref": "submit"},
                call_id="submit-2",
            ),
            _text_turn("done"),
        ]
    )
    registry = ToolRegistry()

    def browser_click(tab_id: str, snapshot_id: str, ref: str):
        executed.append(snapshot_id)
        return {"ok": True}

    browser_click.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="browser_click",
        category="connector",
        risk_level="medium",
        capabilities=["browser"],
        requires_approval=True,
    )
    browser_click.__coworker_browser_confirmation__ = lambda _arguments: {
        "requires_confirmation": True,
        "reasons": ["form_submission"],
    }
    registry.register(browser_click)
    permissions = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)
    permissions.allow_browser_for_session()
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        approver=approve,
    )

    events = _collect(engine, "submit twice")
    prompts = [
        event for event in events if event.type == EventType.PERMISSION_REQUIRED
    ]
    assert len(prompts) == 2
    assert [event.data["scope"] for event in prompts] == [
        "browser_action",
        "browser_action",
    ]
    assert [request.scope for request in requests] == [
        "browser_action",
        "browser_action",
    ]
    assert executed == ["s1", "s2"]


def test_browser_execution_rechecks_target_before_using_session_grant(tmp_path):
    executed: list[str] = []
    decisions = iter(
        [
            {"requires_confirmation": False, "reasons": []},
            {
                "requires_confirmation": True,
                "reasons": ["consequential_control"],
            },
        ]
    )

    async def approve(_request: PermissionRequest):
        return ApprovalOutcome.ONCE

    provider = ScriptedProvider(
        [
            _tool_turn(
                "browser_click",
                {"tab_id": "t", "snapshot_id": "s", "ref": "changed"},
                call_id="changed-target",
            ),
            _text_turn("stopped"),
        ]
    )
    registry = ToolRegistry()

    def browser_click(tab_id: str, snapshot_id: str, ref: str):
        executed.append(ref)
        return {"ok": True}

    browser_click.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="browser_click",
        category="connector",
        risk_level="medium",
        capabilities=["browser"],
        requires_approval=True,
    )
    browser_click.__coworker_browser_confirmation__ = (
        lambda _arguments: next(decisions)
    )
    registry.register(browser_click)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO),
        model="gpt-5.5",
        approver=approve,
    )

    events = _collect(engine, "click it")
    finished = [
        event
        for event in events
        if event.type == EventType.TOOL_FINISHED
    ][0]
    assert finished.data["status"] == "error"
    assert "BROWSER_CONFIRMATION_REQUIRED" in finished.data["result_preview"]
    assert executed == []


def test_browser_confirmation_is_bound_to_same_live_target_at_execution(tmp_path):
    executed: list[str] = []
    decisions = iter(
        [
            {
                "requires_confirmation": True,
                "reasons": ["consequential_control"],
                "binding": "document-a:target-a",
            },
            {
                "requires_confirmation": True,
                "reasons": ["consequential_control"],
                "binding": "document-a:target-b",
            },
        ]
    )

    async def approve(_request: PermissionRequest):
        return ApprovalOutcome.ONCE

    provider = ScriptedProvider(
        [
            _tool_turn(
                "browser_click",
                {"tab_id": "t", "snapshot_id": "s", "ref": "target"},
                call_id="bound-target",
            ),
            _text_turn("stopped"),
        ]
    )
    registry = ToolRegistry()

    def browser_click(tab_id: str, snapshot_id: str, ref: str):
        executed.append(ref)
        return {"ok": True}

    browser_click.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="browser_click",
        category="connector",
        risk_level="medium",
        capabilities=["browser"],
        requires_approval=True,
    )
    browser_click.__coworker_browser_confirmation__ = (
        lambda _arguments: next(decisions)
    )
    registry.register(browser_click)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO),
        model="gpt-5.5",
        approver=approve,
    )

    events = _collect(engine, "click it")
    finished = next(
        event for event in events if event.type == EventType.TOOL_FINISHED
    )
    assert finished.data["status"] == "error"
    assert "BROWSER_CONFIRMATION_STALE" in finished.data["result_preview"]
    assert executed == []


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


def _multi_tool_turn(calls):
    return AssistantTurn(
        tool_calls=[
            ToolCall(id=f"call_{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ],
        finish_reason="tool_calls",
    )


def _bare_engine(tmp_path, turns):
    provider = ScriptedProvider(turns)
    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
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
