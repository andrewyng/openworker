"""Observability × async subagents (the gap between OPE-244 and the subagents feature
merged 2026-11-07): a background spawn_agent child records like explore's helper —
its model and tool steps nest under the spawn_agent step on the parent's turn, its
tokens reach the ledger, and the spawn step gains `attrs.helper` when it finishes,
even though the child outlives the turn.
"""

from __future__ import annotations

import asyncio
import time

from coworker.engine import TurnEngine
from coworker.observability import Recorder
from coworker.observability.provider_view import RecordingProvider
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.providers.base import TokenUsage
from coworker.tools import ToolRegistry
from coworker.tools.agent_tasks import agent_task_tools


class _DualProvider(ProviderClient):
    """Scripts the parent (spawn, then done) and the explorer child (one read, then a
    report), told apart by the child's system prompt."""

    def complete(self, *, model, messages, tools=None, **settings):
        head = messages[0] if messages else {}
        is_child = head.get("role") == "system" and "read-only code explorer" in str(
            head.get("content", "")
        )
        had_tool = any(m.get("role") == "tool" for m in messages)
        if is_child:
            if had_tool:
                return AssistantTurn(
                    text="report: notes.txt says Friday release",
                    usage=TokenUsage(input=70, output=7),
                )
            return AssistantTurn(
                tool_calls=[ToolCall(id="h1", name="read_file", arguments={"path": "notes.txt"})],
                finish_reason="tool_calls",
            )
        if not had_tool:
            return AssistantTurn(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="spawn_agent",
                        arguments={"task": "what is in notes.txt?", "label": "notes check"},
                    )
                ],
                finish_reason="tool_calls",
            )
        return AssistantTurn(text="spawned, moving on", usage=TokenUsage(input=50, output=5))

    def capabilities(self, model):
        return ModelCapabilities(tools=True)


def test_background_child_records_under_the_spawn_step(tmp_path):
    (tmp_path / "notes.txt").write_text("Friday release", encoding="utf-8")
    base = RecordingProvider(_DualProvider())
    tools, tasks = agent_task_tools(workspace=tmp_path, provider=base, model="openai:model-a")
    registry = ToolRegistry()
    registry.register_all(tools)
    engine = TurnEngine(
        provider=base,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS),
        model="openai:model-a",
    )
    tasks.bind(engine)
    engine.agent_tasks = tasks
    engine.audit_context = {"session_id": "sess_bg"}
    rec = Recorder()
    engine.recorder = rec

    async def go():
        return [ev async for ev in engine.run("survey the notes in the background")]

    asyncio.run(go())

    deadline = time.time() + 10
    while time.time() < deadline:
        snap = tasks.snapshot()
        if snap and snap[0]["status"] == "done":
            break
        time.sleep(0.05)
    assert tasks.snapshot()[0]["status"] == "done"

    d = rec.as_dict()
    [spawn] = [s for s in d["steps"] if s["kind"] == "tool" and s["name"] == "spawn_agent"]
    nested = [s for s in d["steps"] if s.get("parent_step_id") == spawn["step_id"]]
    kinds = {(s["kind"], (s.get("attrs") or {}).get("purpose")) for s in nested}
    assert ("model", "helper") in kinds, "the child's model calls must record as helper steps"
    assert ("tool", None) in kinds, "the child's read must nest under the spawn step"
    [read] = [s for s in nested if s["kind"] == "tool"]
    assert read["name"] == "read_file" and read["attrs"]["decision"] == {"helper": True}
    # Detached helpers mark every step after_turn, so the §7.4 turn-end sweep can never
    # close a step the still-running child owns (the race this test exists to pin).
    assert all(s.get("after_turn") for s in nested)
    assert all(s.get("status") != "interrupted" for s in nested)

    # The child never becomes a second turn, and the spawn step carries the summary
    # pill with the tokens the child actually spent.
    assert len(d["turns"]) == 1
    helper = (spawn.get("attrs") or {}).get("helper")
    assert helper is not None, "the finished child must stamp attrs.helper on its spawn step"
    assert helper["status"] == "ok"
    assert helper["model_calls"] >= 1 and helper["tokens"] > 0
