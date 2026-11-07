"""Async subagents (OPE-254/255/258): spawn in the background, report back, and keep
the human the only approver — an expired approval wait resolves as declined and SAYS so
in the child's tool result.
"""

from __future__ import annotations

import asyncio
import time

import aisuite as ai
import pytest

from coworker.engine import TurnEngine
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.providers.base import TokenUsage
from coworker.sessions import usage_totals
from coworker.tools import ToolRegistry
from coworker.tools.agent_tasks import INHERIT_INSTRUCTIONS, agent_task_tools


class DualRoleProvider(ProviderClient):
    """Scripts BOTH the parent and its children, told apart by the system prompt:
    a child carries the inherit/explorer instructions, the parent does not."""

    def __init__(self, spawn_args: dict):
        self.spawn_args = spawn_args

    def _is_child(self, messages) -> bool:
        head = messages[0] if messages else {}
        content = str(head.get("content", ""))
        return head.get("role") == "system" and (
            "background subagent" in content or "read-only code explorer" in content
        )

    def complete(self, *, model, messages, tools=None, **settings):
        if self._is_child(messages):
            if any(m.get("role") == "tool" for m in messages):
                return AssistantTurn(text="child report: finished without the blocked step")
            tool_names = [t["function"]["name"] for t in (tools or [])]
            if "write_marker" in tool_names:
                return AssistantTurn(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="write_marker", arguments={})],
                )
            return AssistantTurn(
                text="child report: all clear",
                usage=TokenUsage(input=100, output=20, cache_read=0, cache_write=0),
            )
        # Parent: one spawn, then a plain reply.
        if not any(m.get("role") == "tool" for m in messages):
            return AssistantTurn(
                text="", tool_calls=[ToolCall(id="p1", name="spawn_agent", arguments=self.spawn_args)]
            )
        return AssistantTurn(text="spawned, moving on")

    def capabilities(self, model):
        return ModelCapabilities(tools=True)


def _parent(tmp_path, provider, extra_tools=None):
    tools, registry = agent_task_tools(
        workspace=tmp_path, provider=provider, model="m"
    )
    reg = ToolRegistry()
    reg.register_all(tools)
    for t in extra_tools or []:
        reg.register(t)
    engine = TurnEngine(
        provider=provider,
        registry=reg,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE),
        model="m",
    )
    registry.bind(engine)
    engine.agent_tasks = registry
    return engine, registry


async def _wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_read_only_spawn_reports_back_and_counts_tokens(tmp_path):
    provider = DualRoleProvider({"task": "map the repo", "label": "repo map"})
    engine, registry = _parent(tmp_path, provider)

    events = [e async for e in engine.run("go research")]
    assert any(e.data.get("text") == "spawned, moving on" for e in events if e.data)

    assert await _wait_for(lambda: registry.snapshot() and registry.snapshot()[0]["status"] == "done")
    snap = registry.snapshot()[0]
    assert snap["label"] == "repo map"
    assert snap["usage"]["m"]["input"] == 100 and snap["usage"]["m"]["turns"] == 1

    # No manager wired: the report reaches the parent as steering — consumed into the
    # transcript when the child beat the turn's end, queued otherwise. The usage
    # notice lands in the transcript where usage_totals folds it (OPE-258).
    delivered = [s[0] for s in engine._steering] + [
        str(m.get("content", "")) for m in engine.messages if m.get("role") == "user"
    ]
    assert any("child report: all clear" in d for d in delivered)
    totals = usage_totals(engine.messages)
    assert totals["m"]["input"] >= 100  # the child's spend is visible now

    # agent_task_output can still peek at the finished run.
    out = engine.registry.execute("agent_task_output", {"task_id": snap["id"]})
    assert out["status"] == "done" and "all clear" in out["report"]


@pytest.mark.asyncio
async def test_expired_approval_is_declined_and_says_so(tmp_path, monkeypatch):
    """OPE-255 ruling: expiry = declined, never approved, and the child's tool result
    must say the request expired — the honest-fallback contract."""
    from coworker.tools import agent_tasks as module

    monkeypatch.setattr(module, "_MIN_APPROVAL_TIMEOUT", 0.2)

    def write_marker() -> dict:
        """Writes a marker file (needs approval)."""
        return {"ok": True}

    marker_tool = ai.tool(
        write_marker,
        metadata=ai.ToolMetadata(
            category="files", risk_level="high", capabilities=["write"], requires_approval=True
        ),
    )

    provider = DualRoleProvider(
        {"task": "write the marker", "label": "marker run", "mode": "inherit", "approval_timeout": 0.2}
    )
    engine, registry = _parent(tmp_path, provider, extra_tools=[marker_tool])
    registry.loop = asyncio.get_running_loop()

    asked, expired_ids = asyncio.Event(), []

    async def never_answers(req):
        # The card carries the subagent's name and a prefixed id (no Inbox collision).
        assert req.reason and req.reason.startswith("[subagent: marker run]")
        assert req.tool_call_id.startswith("agent-")
        asked.set()
        await asyncio.Event().wait()  # the human never responds

    engine.approver = never_answers
    registry.resolve_expired = expired_ids.append

    [e async for e in engine.run("go")]
    assert await _wait_for(lambda: registry.snapshot() and registry.snapshot()[0]["status"] == "done")
    assert asked.is_set()

    task = registry.get(registry.snapshot()[0]["id"])
    tool_msgs = [m for m in task.engine.messages if m.get("role") == "tool"]
    assert tool_msgs, "the denied call must leave an honest tool result"
    assert "expired" in str(tool_msgs[0].get("content", ""))
    assert "NOT approved" in str(tool_msgs[0].get("content", ""))
    assert expired_ids and expired_ids[0].startswith(task.id + ":")
    assert "without the blocked step" in task.report


@pytest.mark.asyncio
async def test_delivery_fires_through_the_server_hooks(tmp_path):
    """The production path the first live run broke: registry.loop SET (the manager
    wires it) and a deliver hook — the notice must land and the hook must fire."""
    provider = DualRoleProvider({"task": "map the repo", "label": "hooked run"})
    engine, registry = _parent(tmp_path, provider)
    registry.loop = asyncio.get_running_loop()
    delivered: list[tuple[str, dict]] = []
    registry.deliver = lambda text, source: delivered.append((text, source))

    [e async for e in engine.run("go")]
    assert await _wait_for(lambda: bool(delivered))
    text, source = delivered[0]
    assert "child report: all clear" in text and source["title"] == "hooked run"
    # The usage notice went through the loop (scheduled, so give it a beat).
    assert await _wait_for(
        lambda: any(m.get("role") == "notice" and m.get("kind") == "agent_task" for m in engine.messages)
    )


def test_store_marks_live_runs_lost_truncates_and_caps(tmp_path):
    from coworker.tools.agent_tasks import AgentTaskStore, _KEEP_FINISHED

    path = tmp_path / "agent_tasks.json"
    store = AgentTaskStore(path)
    rows = [
        {"id": "agent-1", "label": "live one", "status": "running", "created": 1},
        {"id": "agent-2", "label": "big report", "status": "done", "created": 2, "finished": 99_999, "report": "x" * 20_000},
    ] + [
        {"id": f"agent-{i}", "status": "done", "created": i, "finished": i}
        for i in range(3, 3 + _KEEP_FINISHED + 10)
    ]
    store.put("s1", rows)

    # A fresh store (the restart) reads the same file: the live run is honestly lost,
    # the oversized report is truncated, and the finished list is capped at N.
    reopened = AgentTaskStore(path)
    out = reopened.load("s1")
    lost = next(r for r in out if r["id"] == "agent-1")
    assert lost["status"] == "error" and "restart" in lost["note"]
    big = next(r for r in out if r["id"] == "agent-2")
    assert len(big["report"]) < 9_000 and "truncated" in big["report"]
    assert sum(1 for r in out if r["status"] == "done") <= _KEEP_FINISHED

    reopened.drop("s1")
    assert AgentTaskStore(path).load("s1") == []


@pytest.mark.asyncio
async def test_registry_persists_runs_and_rehydrates_without_id_collisions(tmp_path):
    from coworker.tools.agent_tasks import AgentTaskStore

    store = AgentTaskStore(tmp_path / "agent_tasks.json")
    provider = DualRoleProvider({"task": "map the repo", "label": "first run"})
    engine, registry = _parent(tmp_path, provider)
    registry.attach_store(store, "sess-1")
    [e async for e in engine.run("go")]
    assert await _wait_for(lambda: store.load("sess-1") and store.load("sess-1")[0]["status"] == "done")

    # A new registry (the rebuilt engine) rehydrates the history and keeps counting
    # ABOVE it, so a fresh spawn never reuses agent-1.
    _, registry2 = _parent(tmp_path, provider)
    registry2.attach_store(store, "sess-1")
    snap = registry2.snapshot()
    assert snap and snap[0]["label"] == "first run" and snap[0]["status"] == "done"
    t = registry2.new_task("second", "t", "read_only", None)
    assert t.id != snap[0]["id"]


@pytest.mark.asyncio
async def test_child_tool_calls_are_audited_under_the_parent_with_the_task_id(tmp_path):
    """OPE-262: a child's reads land in the SESSION's audit log, stamped with its task id."""
    from coworker.audit import AuditStore

    store = AuditStore(tmp_path / "audit.db")
    provider = DualRoleProvider({"task": "map the repo", "label": "audited run"})
    engine, registry = _parent(tmp_path, provider)
    engine.audit_sink = store.append
    engine.audit_context = {"session_id": "sess-audit", "agent": "security", "workspace": str(tmp_path)}
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")

    [e async for e in engine.run("go")]
    assert await _wait_for(lambda: registry.snapshot() and registry.snapshot()[0]["status"] == "done")

    rows = store.list(session_id="sess-audit", limit=200)
    child_rows = [r for r in rows if r.get("subagent")]
    assert child_rows, "the child's calls must reach the parent session's audit log"
    assert {r["subagent"] for r in child_rows} == {registry.snapshot()[0]["id"]}
    assert all(r["session_id"] == "sess-audit" for r in child_rows)
    # The parent's own spawn_agent row stays unstamped: it is the parent acting.
    assert any(r["tool"] == "spawn_agent" and not r.get("subagent") for r in rows)


@pytest.mark.asyncio
async def test_child_transcript_is_persisted_pruned_and_purged(tmp_path):
    """OPE-262: the child's full message log lives beside the run record, on demand."""
    from coworker.tools.agent_tasks import AgentTaskStore, _KEEP_FINISHED

    store = AgentTaskStore(tmp_path / "agent_tasks.json")
    provider = DualRoleProvider({"task": "map the repo", "label": "kept run"})
    engine, registry = _parent(tmp_path, provider)
    registry.attach_store(store, "sess-t")
    [e async for e in engine.run("go")]
    assert await _wait_for(lambda: registry.snapshot() and registry.snapshot()[0]["status"] == "done")
    task_id = registry.snapshot()[0]["id"]

    transcript = store.read_transcript("sess-t", task_id)
    assert transcript, "the finished child's transcript must be on disk"
    assert transcript[0]["role"] == "system"  # the explorer's own instructions
    assert any(m.get("role") == "assistant" and "all clear" in str(m.get("content")) for m in transcript)
    assert store.read_transcript("sess-t", "agent-999") is None

    # Retention: a run pruned past the finished cap loses its transcript file too.
    rows = store.load("sess-t")
    newer = time.time() + 60  # all NEWER than the real run, so the real run is what gets pruned
    for i in range(_KEEP_FINISHED + 1):
        rows.append({"id": f"agent-{100 + i}", "status": "done", "created": newer + i, "finished": newer + i})
    store.put("sess-t", rows)
    assert store.read_transcript("sess-t", task_id) is None

    # Deleting the session purges whatever is left.
    store.write_transcript("sess-t", "agent-150", [{"role": "user", "content": "x"}])
    store.drop("sess-t")
    assert store.read_transcript("sess-t", "agent-150") is None


@pytest.mark.asyncio
async def test_cancel_stops_a_running_child(tmp_path):
    class SlowChildProvider(DualRoleProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            if self._is_child(messages):
                time.sleep(0.3)
                return AssistantTurn(
                    text="", tool_calls=[ToolCall(id="l1", name="grep", arguments={"pattern": "x"})]
                )
            return super().complete(model=model, messages=messages, tools=tools, **settings)

    provider = SlowChildProvider({"task": "loop forever", "label": "loopy"})
    engine, registry = _parent(tmp_path, provider)
    [e async for e in engine.run("go")]
    assert await _wait_for(lambda: bool(registry.snapshot()))
    task_id = registry.snapshot()[0]["id"]
    engine.registry.execute("agent_task_cancel", {"task_id": task_id})
    assert await _wait_for(lambda: registry.snapshot()[0]["status"] in ("canceled", "done"))
