"""OPE-238: at the end of a turn the engine says which actions ran without asking.

An action "ran without asking" when auto-approve let it through with no approval card:
a gate tier (OPE-233 inert writes) or a reviewer "allow". The engine emits one
`turn_summary` event listing them, just before the event that ends the turn, so the
event that ends a turn is still the last one. Actions the user approved on a card, or
that the mode allows outright, are not in it.

Each item names its tool call, and the audit log can be filtered by that call's id,
so a surface can link an item to its audit rows.
"""

from __future__ import annotations

import asyncio

from coworker.audit import AuditStore
from coworker.events import EventType
from coworker.providers import AssistantTurn
from test_auto_approve import _engine, _FakeReviewer, _run, _tool_turn


def _summaries(events):
    return [ev for ev in events if ev.type == EventType.TURN_SUMMARY]


def test_lists_gate_tier_and_reviewer_allowed_actions_before_turn_end(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [
            _tool_turn(("write_file", {"path": "summary.txt", "content": "3 tasks done"})),
            _tool_turn(("run_shell", {"command": "pytest -q"})),
            _tool_turn(("write_file", {"path": "tool.py", "content": "print(1)"})),
            AssistantTurn(text="done", finish_reason="stop"),
        ],
    )
    engine.permissions.inert_writes = "on"
    engine.reviewer = _FakeReviewer({"run_shell": "allow", "write_file": "unsure"})
    events = _run(engine, "do the tasks")

    assert approvals == ["write_file"]  # tool.py: the reviewer was unsure, the user approved
    summaries = _summaries(events)
    assert len(summaries) == 1
    # Right before the event that ends the turn.
    assert events[-1].type == EventType.TURN_END
    assert events[-2] is summaries[0]
    items = summaries[0].data["actions"]
    assert [(i["name"], i["origin"]) for i in items] == [
        ("write_file", "gate_tier"),
        ("run_shell", "reviewer"),
    ]
    assert all(i["status"] == "ok" for i in items)
    assert all(i["tool_call_id"] for i in items)
    assert items[1]["note"] == "scripted allow"  # the reviewer's one-line reason rides along
    assert items[0]["tier"] == "inert_write" and "tier" not in items[1]
    assert all(i["effect"] == "change" for i in items)
    assert summaries[0].data["count"] == 2
    assert summaries[0].data["failed"] == 0 and summaries[0].data["reads"] == 0


def test_no_event_when_everything_was_asked(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "make deploy"})), AssistantTurn(text="done", finish_reason="stop")],
    )
    engine.reviewer = _FakeReviewer({"run_shell": "unsure"})
    events = _run(engine, "deploy")

    assert approvals == ["run_shell"]
    assert _summaries(events) == []


def test_no_event_for_a_turn_without_tools(tmp_path):
    engine, _rows, _approvals = _engine(tmp_path, [AssistantTurn(text="hi", finish_reason="stop")])
    assert _summaries(_run(engine, "hello")) == []


def test_a_reviewer_deny_is_not_listed(tmp_path):
    engine, _rows, _approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "rm -rf build"})), AssistantTurn(text="ok", finish_reason="stop")],
    )
    engine.reviewer = _FakeReviewer({"run_shell": "deny"})
    assert _summaries(_run(engine, "clean up")) == []


def test_each_turn_gets_its_own_summary(tmp_path):
    engine, _rows, _approvals = _engine(
        tmp_path,
        [
            _tool_turn(("run_shell", {"command": "pytest -q"})),
            AssistantTurn(text="first", finish_reason="stop"),
            _tool_turn(("run_shell", {"command": "ruff check"})),
            _tool_turn(("run_shell", {"command": "mypy ."})),
            AssistantTurn(text="second", finish_reason="stop"),
        ],
    )
    engine.reviewer = _FakeReviewer({"run_shell": "allow"})

    # Both requests in ONE event loop, as in the app. The engine's stop signal is an
    # asyncio.Event; a second asyncio.run() would wait on it from a different loop, and
    # that wait failing reads as "stopped", dropping a model turn at random.
    async def _both():
        first = [ev async for ev in engine.run("run the tests")]
        second = [ev async for ev in engine.run("lint and type-check")]
        return _summaries(first), _summaries(second)

    first, second = asyncio.run(_both())
    assert [s.data["count"] for s in first] == [1]
    assert [s.data["count"] for s in second] == [2]


def test_a_failed_action_is_listed_with_its_status(tmp_path):
    engine, _rows, _approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "pytest -q"})), AssistantTurn(text="done", finish_reason="stop")],
    )
    engine.reviewer = _FakeReviewer({"run_shell": "allow"})

    def run_shell(command: str) -> str:
        """Run a command.

        Args:
            command: the command line
        """
        raise RuntimeError("exit 1")

    engine.registry.register(run_shell, metadata=engine.registry.get("run_shell").metadata)
    items = _summaries(_run(engine, "run the tests"))[0].data["actions"]
    assert items[0]["name"] == "run_shell"
    assert items[0]["status"] != "ok"


def test_the_summary_carries_no_arguments(tmp_path):
    # The tool events already carry what ran; the summary stays a short index of it.
    engine, _rows, _approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "echo SECRET_SHAPED"})), AssistantTurn(text="done", finish_reason="stop")],
    )
    engine.reviewer = _FakeReviewer({"run_shell": "allow"})
    summary = _summaries(_run(engine, "echo"))[0]
    assert "SECRET_SHAPED" not in repr(summary.data)


def test_closing_the_turn_early_still_closes_the_inner_run(tmp_path):
    # A surface that stops reading mid-turn (a disconnect) must still end the run, so the
    # run grants are cleared as before.
    engine, _rows, _approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "pytest -q"})), AssistantTurn(text="done", finish_reason="stop")],
    )
    engine.reviewer = _FakeReviewer({"run_shell": "allow"})
    cleared: list[bool] = []
    original = engine.permissions.clear_run_allowances

    def spy():
        cleared.append(True)
        original()

    engine.permissions.clear_run_allowances = spy

    async def _go():
        gen = engine.run("run the tests")
        async for ev in gen:
            if ev.type == EventType.TOOL_FINISHED:
                break
        await gen.aclose()

    asyncio.run(_go())
    assert len(cleared) >= 2  # once at the start, once in the run's finally


# -- the audit log can be filtered by tool call ------------------------------------------


def test_audit_store_filters_by_call_id(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    for call_id, stage in (("a1", "started"), ("a1", "finished"), ("b2", "finished")):
        store.append({"session_id": "s", "tool": "run_shell", "stage": stage, "call_id": call_id, "arguments": {}})
    rows = store.list(call_id="a1")
    assert sorted(r["stage"] for r in rows) == ["finished", "started"]
    assert {r["call_id"] for r in rows} == {"a1"}
    assert len(store.list()) == 3


# -- the count means "changed state, and nobody approved it" ---------------------------


def _shell_returning(engine, results: dict[str, dict]):
    """Replace the test shell with one that answers each command from `results`."""

    def run_shell(command: str) -> dict:
        """Run a command.

        Args:
            command: the command line
        """
        return results[command]

    engine.registry.register(run_shell, metadata=engine.registry.get("run_shell").metadata)


def test_the_proposed_event_carries_the_call_id(tmp_path):
    # Two same-named calls in one batch must pair with their own finished events.
    engine, _rows, _approvals = _engine(
        tmp_path,
        [
            _tool_turn(("run_shell", {"command": "pytest -q"}), ("run_shell", {"command": "ruff check"})),
            AssistantTurn(text="done", finish_reason="stop"),
        ],
    )
    engine.reviewer = _FakeReviewer({"run_shell": "allow"})
    events = _run(engine, "check")
    proposed = [ev.data["tool_call_id"] for ev in events if ev.type == EventType.TOOL_PROPOSED]
    finished = [ev.data["tool_call_id"] for ev in events if ev.type == EventType.TOOL_FINISHED]
    assert len(proposed) == 2 and all(proposed)
    assert proposed == finished


def test_a_shell_commands_outcome_rides_on_the_finished_event_not_the_preview(tmp_path):
    long_command = "python -c '" + "x = 1; " * 80 + "'"  # pushes exit_code past the 300-char preview
    engine, _rows, _approvals = _engine(
        tmp_path,
        [
            _tool_turn(("run_shell", {"command": long_command})),
            _tool_turn(("run_shell", {"command": "sleep 999"})),
            _tool_turn(("run_shell", {"command": "npm run dev"})),
            AssistantTurn(text="done", finish_reason="stop"),
        ],
    )
    engine.reviewer = _FakeReviewer({"run_shell": "allow"})
    _shell_returning(
        engine,
        {
            long_command: {"command": long_command, "cwd": "/w", "exit_code": 1, "output": "", "timed_out": False},
            "sleep 999": {"command": "sleep 999", "cwd": "/w", "exit_code": None, "output": "", "timed_out": True},
            "npm run dev": {"task_id": "bg-1", "command": "npm run dev", "status": "running"},
        },
    )
    events = _run(engine, "run things")
    finished = [ev.data for ev in events if ev.type == EventType.TOOL_FINISHED]
    assert '"exit_code"' not in finished[0]["result_preview"]  # the preview alone would say nothing
    assert finished[0]["exit_code"] == 1 and "timed_out" not in finished[0]
    assert finished[1]["exit_code"] is None and finished[1]["timed_out"] is True
    assert finished[2]["background"] is True and "exit_code" not in finished[2]
    # The same facts are persisted with the result, so a reload shows the same thing.
    sidecars = [m["_display"] for m in engine.messages if m.get("role") == "tool"]
    assert sidecars[0]["exit_code"] == 1
    assert sidecars[1]["timed_out"] is True
    assert sidecars[2]["background"] is True
    # The summary: three changes attempted, two failed, one started in the background.
    summary = _summaries(events)[0].data
    assert (summary["count"], summary["failed"], summary["reads"]) == (1, 2, 0)
    assert summary["actions"][0]["exit_code"] == 1
    assert summary["actions"][1]["timed_out"] is True
    assert summary["actions"][2]["background"] is True


def test_a_failed_call_keeps_its_status_on_the_persisted_message(tmp_path):
    engine, _rows, _approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "pytest -q"})), AssistantTurn(text="done", finish_reason="stop")],
    )
    engine.reviewer = _FakeReviewer({"run_shell": "allow"})

    def run_shell(command: str) -> str:
        """Run a command.

        Args:
            command: the command line
        """
        raise RuntimeError("exit 1")

    engine.registry.register(run_shell, metadata=engine.registry.get("run_shell").metadata)
    events = _run(engine, "run the tests")
    tool_msg = next(m for m in engine.messages if m.get("role") == "tool")
    assert tool_msg["_display"]["status"] == "error"
    assert tool_msg["_display"]["approval_origin"] == "reviewer"
    summary = _summaries(events)[0].data
    assert (summary["count"], summary["failed"]) == (0, 1)


def test_effect_follows_the_risk_class_and_the_read_only_tier(tmp_path):
    from coworker.providers import ToolCall

    engine, _rows, _approvals = _engine(tmp_path, [])
    meta = engine.registry.get("run_shell").metadata
    shell = ToolCall(id="c1", name="run_shell", arguments={"command": "cat x"})
    assert engine._effect(shell, meta, "") == "change"  # a command is a change unless classified
    assert engine._effect(shell, meta, "readonly_command") == "read"  # OPE-234's tier
    write = ToolCall(id="c2", name="write_file", arguments={"path": "a.txt", "content": ""})
    assert engine._effect(write, engine.registry.get("write_file").metadata, "inert_write") == "change"
    fetch = ToolCall(id="c3", name="web_fetch", arguments={"url": "https://example.com"})
    assert engine._effect(fetch, None, "") == "read"  # a web read is a read
    mcp = ToolCall(id="c4", name="mcp__jira__create_issue", arguments={})
    assert engine._effect(mcp, None, "") == "change"  # third-party tools that gate are writes or sends
