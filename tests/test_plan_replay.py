"""Tests for replayable plan artifacts (#623)."""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

import aisuite as ai
from coworker.audit import AuditStore
from coworker.conversations import ConversationStore
from coworker.engine import TurnEngine
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    ToolCall,
)
from coworker.server import SessionManager, create_app
from coworker.sessions import SessionRecord
from coworker.tools import ToolRegistry
from coworker.tools.plan import propose_plan_tool


class ScriptedProvider(ProviderClient):
    def __init__(self, turns=None):
        self._turns = list(turns or [])

    def complete(self, *, model, messages, tools=None, **settings):
        if not self._turns:
            return AssistantTurn(text="ok", finish_reason="stop")
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _tool_turn(name, args, call_id="call_1"):
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
    )


def _text_turn(text):
    return AssistantTurn(text=text, finish_reason="stop")


def test_conversation_store_plan_persistence(tmp_path):
    store = ConversationStore(tmp_path / "sessions.db")
    rec = SessionRecord(
        session_id="sess-1",
        workspace=str(tmp_path),
        model="gpt-5.5",
        mode=Mode.PLAN.value,
        title="Session 1",
        plan={"id": "plan-1", "title": "My Plan", "plan": "1. step one\n2. step two"},
    )
    store.save(rec)

    loaded = store.load("sess-1")
    assert loaded is not None
    assert loaded.plan == {"id": "plan-1", "title": "My Plan", "plan": "1. step one\n2. step two"}

    # Update plan via set_plan
    store.set_plan("sess-1", {"id": "plan-2", "title": "Updated", "plan": "revised"})
    updated = store.load("sess-1")
    assert updated is not None
    assert updated.plan["id"] == "plan-2"
    assert updated.plan["title"] == "Updated"

    # Verify list() loads plan
    all_sess = store.list()
    assert len(all_sess) == 1
    assert all_sess[0].plan["id"] == "plan-2"
    store.close()


def test_audit_store_plan_id_tracking_and_filtering(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    store.append({
        "session_id": "sess-a",
        "stage": "tool_executed",
        "tool": "write_file",
        "plan_id": "plan-alpha",
    })
    store.append({
        "session_id": "sess-b",
        "stage": "tool_executed",
        "tool": "read_file",
        "plan_id": "plan-beta",
    })
    store.append({
        "session_id": "sess-a",
        "stage": "tool_executed",
        "tool": "run_shell",
        "plan_id": "plan-alpha",
    })

    alpha_events = store.list(plan_id="plan-alpha")
    assert len(alpha_events) == 2
    assert all(e["plan_id"] == "plan-alpha" for e in alpha_events)

    beta_events = store.list(plan_id="plan-beta")
    assert len(beta_events) == 1
    assert beta_events[0]["plan_id"] == "plan-beta"

    gamma_events = store.list(plan_id="plan-gamma")
    assert len(gamma_events) == 0
    store.close()


def test_turn_engine_tags_audit_context_on_plan_approval(tmp_path):
    recorded_audit = []

    def sink(ev):
        recorded_audit.append(dict(ev))

    async def approve(args, tool_call_id=None):
        return {"approved": True, "mode": "auto", "plan_id": "plan-xyz"}

    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    registry.register(propose_plan_tool())
    permissions = PermissionEngine(workspace_root=tmp_path, mode=Mode.PLAN)

    engine = TurnEngine(
        provider=ScriptedProvider([
            _tool_turn("propose_plan", {"plan": "1. create plan.txt"}),
            _tool_turn("write_file", {"path": "plan.txt", "content": "hello\n"}, "call_2"),
            _text_turn("done"),
        ]),
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        plan_approver=approve,
        audit_sink=sink,
    )

    async def _run():
        return [ev async for ev in engine.run("start")]

    asyncio.run(_run())

    assert engine.audit_context.get("plan_id") == "plan-xyz"
    # Verify subsequent write_file tool audit events carry plan_id
    write_audits = [e for e in recorded_audit if e.get("tool") == "write_file"]
    assert len(write_audits) > 0
    assert all(e.get("plan_id") == "plan-xyz" for e in write_audits)


def test_manager_save_and_get_session_plan(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    plan_text = "# Reorganize Documentation\n1. Move guides\n2. Update index"
    saved = manager.save_plan_artifact(
        session_id="s1",
        plan_text=plan_text,
        plan_id="plan-doc-1",
    )

    assert saved["id"] == "plan-doc-1"
    assert saved["title"] == "Reorganize Documentation"
    assert saved["path"] == "plans/plan-doc-1.md"

    # Check scratch files
    scratch = Path(manager.scratch_base()) / "s1"
    assert (scratch / "plan.md").exists()
    assert (scratch / "plans" / "plan-doc-1.md").exists()
    assert (scratch / "plan.md").read_text(encoding="utf-8") == plan_text

    # Retrieve via get_session_plan
    plan = manager.get_session_plan("s1")
    assert plan is not None
    assert plan["id"] == "plan-doc-1"
    assert plan["plan"] == plan_text

    # Check list_plans
    all_plans = manager.list_plans()
    assert len(all_plans) == 1
    assert all_plans[0]["id"] == "plan-doc-1"

    # Check audit log
    audits = manager.audit_store.list(plan_id="plan-doc-1")
    assert any(a["stage"] == "plan_persisted" and a["status"] == "approved" for a in audits)


def test_manager_replay_plan(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    plan_text = "# Automated Verification Plan\n- Run tests\n- Lint code"
    manager.save_plan_artifact(
        session_id="s-origin",
        plan_text=plan_text,
        plan_id="plan-auto-1",
    )

    async def replay():
        result = await manager.replay_plan(session_id="s-origin")
        await asyncio.gather(*manager._plan_replay_tasks)
        return result

    replay_res = asyncio.run(replay())
    new_sid = replay_res["session_id"]
    assert new_sid.startswith("replay-")
    assert replay_res["plan_id"] == "plan-auto-1"

    # Verify new session record in store
    new_rec = manager.session_store.load(new_sid)
    assert new_rec is not None
    assert new_rec.plan["id"] == "plan-auto-1"
    assert new_rec.plan["origin_session_id"] == "s-origin"
    users = [m for m in new_rec.messages if m["role"] == "user"]
    assert len(users) == 1
    assert "Execute the approved plan" in users[0]["content"]
    assert any(m["role"] == "assistant" for m in new_rec.messages)
    assert new_rec.mode == Mode.INTERACTIVE.value
    assert not new_rec.grants

    # Verify new session engine carries audit_context
    new_engine = manager.get_engine(new_sid)
    assert new_engine.audit_context["plan_id"] == "plan-auto-1"
    assert new_engine.audit_context["replay_from"] == "s-origin"

    # Verify audit event logged for replay
    replay_audits = [
        e for e in manager.audit_store.list(session_id=new_sid)
        if e["stage"] == "plan_replayed"
    ]
    assert len(replay_audits) == 1
    assert replay_audits[0]["plan_id"] == "plan-auto-1"
    assert replay_audits[0]["status"] == "started"


def test_rest_plan_endpoints(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    client = TestClient(create_app(manager))

    plan_text = "# Core Refactor Plan\n1. Decouple modules"
    manager.save_plan_artifact(
        session_id="s-rest",
        plan_text=plan_text,
        plan_id="plan-rest-1",
    )

    # GET /v1/sessions/{session_id}/plan
    resp = client.get("/v1/sessions/s-rest/plan")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "plan-rest-1"
    assert data["plan"] == plan_text

    # GET /v1/sessions/{nonexistent}/plan
    resp = client.get("/v1/sessions/nonexistent/plan")
    assert resp.status_code == 404

    # GET /v1/plans
    resp = client.get("/v1/plans")
    assert resp.status_code == 200
    assert any(p["id"] == "plan-rest-1" for p in resp.json())

    # POST /v1/sessions/{session_id}/plan/replay
    resp = client.post("/v1/sessions/s-rest/plan/replay", json={})
    assert resp.status_code == 200
    replay_data = resp.json()
    assert replay_data["session_id"].startswith("replay-")
    assert replay_data["plan_id"] == "plan-rest-1"

    # POST /v1/plans/replay by plan_id
    resp = client.post("/v1/plans/replay", json={"plan_id": "plan-rest-1"})
    assert resp.status_code == 200
    replay_data2 = resp.json()
    assert replay_data2["session_id"].startswith("replay-")
    assert replay_data2["plan_id"] == "plan-rest-1"


def test_replay_selects_saved_version_and_rejects_unknown_id(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    manager.save_plan_artifact("versions", "# First version", plan_id="first")
    manager.save_plan_artifact("versions", "# Second version", plan_id="second")
    assert {p["id"] for p in manager.list_plans()} == {"first", "second"}

    async def replay():
        with pytest.raises(ValueError):
            await manager.replay_plan(session_id="versions", plan_id="missing")
        result = await manager.replay_plan(session_id="versions", plan_id="first")
        await asyncio.gather(*manager._plan_replay_tasks)
        return result

    result = asyncio.run(replay())
    rec = manager.session_store.load(result["session_id"])
    assert rec.plan["id"] == "first"
    users = [m["content"] for m in rec.messages if m["role"] == "user"]
    assert users == ["Execute the approved plan:\n\n# First version"]
    # Versions survive a store reopen, including the previous latest version.
    reopened = ConversationStore(manager.session_store.base)
    assert {p["id"] for p in reopened.list_plans()} == {"first", "second"}
    reopened.close()


def test_unapproved_workspace_plan_is_not_replayable(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    scratch = Path(manager._provision_scratch("unapproved"))
    (scratch / "plan.md").write_text("agent-written proposal")
    assert manager.get_session_plan("unapproved") is None
    with pytest.raises(ValueError):
        asyncio.run(manager.replay_plan(session_id="unapproved"))
    with pytest.raises(ValueError):
        manager.save_plan_artifact("unapproved", "unsafe", plan_id="../../outside")


def test_live_socket_approval_persists_plan(tmp_path):
    provider = ScriptedProvider([
        _tool_turn("propose_plan", {"plan": "# Live approved plan"}),
        _text_turn("done"),
    ])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    manager._maybe_autotitle = lambda sid: None
    with TestClient(create_app(manager)) as client:
        with client.websocket_connect("/ws/session/live-plan") as ws:
            while ws.receive_json()["type"] != "ready":
                pass
            ws.send_json({"type": "set_mode", "mode": "plan"})
            ws.send_json({"type": "user_message", "text": "make a plan"})
            for _ in range(100):
                event = ws.receive_json()
                if event["type"] == "plan_proposed":
                    ws.send_json({"type": "plan_response", "approved": True, "mode": "interactive"})
                if event["type"] == "turn_done":
                    break
            else:
                pytest.fail("turn did not finish")
            plan = manager.get_session_plan("live-plan")
            assert plan and plan["plan"] == "# Live approved plan"
            assert manager.get_engine("live-plan").audit_context["plan_id"] == plan["id"]
