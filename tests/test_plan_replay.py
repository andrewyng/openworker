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

    replay_res = manager.replay_plan(session_id="s-origin")
    new_sid = replay_res["session_id"]
    assert new_sid.startswith("replay-")
    assert replay_res["plan_id"] == "plan-auto-1"

    # Verify new session record in store
    new_rec = manager.session_store.load(new_sid)
    assert new_rec is not None
    assert new_rec.plan["id"] == "plan-auto-1"
    assert new_rec.plan["origin_session_id"] == "s-origin"
    assert any("Execute the approved plan" in m["content"] for m in new_rec.messages)

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
