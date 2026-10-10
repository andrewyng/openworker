"""Rehearsal regressions: inspect the real action, retain floors, expose every ask."""
import asyncio
import json
from pathlib import Path

import pytest

from coworker.engine import ApprovalOutcome, PermissionRequest
from coworker.permissions import Mode
from coworker.providers import ToolCall
from coworker.reviewer import Verdict, build_messages
from coworker.runtime_context import capture
from coworker.teams import Actor, Role
from test_engine import ScriptedProvider
from test_team_live_gaps import manager, request_for


def lead_for_review(manager, monkeypatch, *, verdict="allow", during=None):
    manager.provider = ScriptedProvider([])
    engine = manager.get_engine("lead", agent="swe-lead")
    engine.permissions.mode = Mode.AUTO_APPROVE
    engine.messages.append({"role": "user", "content": "Fix billing and run local regression tests. No deployment."})
    asked = []
    class Reviewer:
        async def review(self, **kwargs):
            asked.append(kwargs)
            if during:
                during()
            return Verdict(verdict, "Check the actual worker action")
    engine.reviewer = Reviewer()
    monkeypatch.setattr(engine, "_reviewer_active", lambda: True)
    async def deny(_request):
        return ApprovalOutcome.DENY
    engine.approver = deny
    return engine, asked


def authorize(engine, request):
    async def run():
        return [value async for value in engine._authorize(ToolCall(id="decision", name=request.tool_name, arguments=request.arguments))]
    return asyncio.run(run())


def test_lead_reviewer_receives_exact_worker_action_not_proxy(manager, monkeypatch):
    engine, asked = lead_for_review(manager, monkeypatch)
    original, request = request_for(manager)
    request.arguments["note"] = "Agent-authored reassurance is not authority"
    assert authorize(engine, request)[-1] is True
    assert len(asked) == 1
    assert asked[0]["tool_name"] == "run_shell"
    assert asked[0]["arguments"] == original.data["arguments"]
    assert asked[0]["action_context"]["workspace"] == manager.default_workspace
    assert "reassurance" not in json.dumps(asked)
    assert original.state == "pending"  # authorization test executes nothing


@pytest.mark.parametrize("mutation", ["foreign", "resolved", "missing", "unknown_tool", "hard_deny", "human_floor"])
def test_proxy_cannot_bypass_ownership_staleness_or_worker_floors(manager, monkeypatch, mutation):
    engine, asked = lead_for_review(manager, monkeypatch)
    original, request = request_for(manager)
    if mutation == "foreign":
        request.arguments["worker"] = "maya"
    elif mutation == "resolved":
        manager.inbox.resolve(original.id, "deny")
    elif mutation == "missing":
        request.arguments["call_id"] = "missing"
    else:
        manager.inbox.resolve(original.id, "deny")
        name, args = {
            "unknown_tool": ("missing_tool", {}),
            "hard_deny": ("write_file", {"path": "/outside-acme/a.txt", "content": "test"}),
            "human_floor": ("write_file", {"path": ".github/workflows/check.yml", "content": "test"}),
        }[mutation]
        request.arguments["call_id"] = manager.inbox.add_approval("worker", "Check", data={"tool": name, "arguments": args}).id
    result = authorize(engine, request)
    assert result[-1] is False
    assert asked == []
    if mutation == "human_floor":
        card = next(e.data for e in result[:-1] if e.type.value == "permission_required")
        assert card["escalation"]["kind"] == "human_required"


def test_proxy_verdict_is_not_used_after_original_is_answered(manager, monkeypatch):
    original, request = request_for(manager)
    engine, asked = lead_for_review(manager, monkeypatch, during=lambda: manager.inbox.resolve(original.id, "deny"))
    assert authorize(engine, request)[-1] is False
    assert len(asked) == 1


def test_escalation_reason_survives_live_and_parked_card(manager, monkeypatch):
    engine, _ = lead_for_review(manager, monkeypatch, verdict="unsure")
    _, request = request_for(manager)
    captured = []
    async def deny(req):
        captured.append(req)
        return ApprovalOutcome.DENY
    engine.approver = deny
    result = authorize(engine, request)
    card = next(e.data for e in result[:-1] if e.type.value == "permission_required")
    parked = manager.approval_prompt_data("lead", captured[0])
    assert card["escalation"] == parked["escalation"] == {"kind": "reviewer_unsure", "reason": "Check the actual worker action"}


def test_background_staffing_fanout_has_the_same_connector_options(manager, monkeypatch):
    extras = {"offer": {"swe-worker": ["github"]}, "other_connected": ["slack"], "lead_mode": "auto-approve"}
    monkeypatch.setattr(manager, "team_card_extras", lambda *_: extras)
    received = [[], []]
    async def first(event): received[0].append(event)
    async def second(event): received[1].append(event)
    manager.register_session_client("lead", first)
    manager.register_session_client("lead", second)
    asyncio.run(manager.broadcast_session("lead", {"type": "team_proposed", "data": {"members": []}}))
    assert received[0] == received[1]
    assert received[0][0]["data"]["other_connected"] == ["slack"]


def test_mode_broadcast_carries_server_mode(manager):
    manager.provider = ScriptedProvider([])
    engine = manager.get_engine("lead", agent="swe-lead")
    engine.permissions.mode = Mode.AUTO_APPROVE
    received = []
    async def collect(event): received.append(event)
    manager.register_session_client("lead", collect)
    asyncio.run(manager.broadcast_session("lead", {"type": "mode_notice", "data": {"text": "Mode changed"}}))
    assert received[0]["data"]["mode"] == "auto-approve"


def test_unattributed_request_is_visible_and_forwarded_gate_is_not_double_counted(manager):
    team = manager.teams.for_lead_session("lead")
    item = manager.team_store.create_item(team.space, Actor("lead", Role.LEAD), title="Billing", criteria="Verified")
    manager.team_store.assign(team.space, Actor("lead", Role.LEAD), item["id"], "sam")
    original, request = request_for(manager)
    summary = manager.team_summary(team.team_id)
    assert summary["counts"]["waiting"] == 0
    assert summary["totals"]["asks_waiting"] == 1
    assert summary["pending_requests"][0]["worker"] == "sam"
    assert not summary["pending_requests"][0]["represented_by_task"]
    forwarded = manager.inbox.add_approval("lead", "Worker approval", data=manager.approval_prompt_data("lead", request))
    summary = manager.team_summary(team.team_id)
    assert summary["totals"]["asks_waiting"] == 1
    assert [r["id"] for r in summary["pending_requests"]] == [forwarded.id]
    manager.inbox.resolve(forwarded.id, "deny")
    summary = manager.team_summary(team.team_id)
    assert summary["pending_requests"] == []
    assert summary["totals"]["asks_waiting"] == 0
    assert original.resolution == "deny"  # denying the lead's allow resolves both cards


def test_runtime_facts_do_not_read_configuration_or_follow_workspace_symlinks(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("PASSWORD=do-not-disclose")
    (tmp_path / "web").symlink_to(tmp_path.parent, target_is_directory=True)
    def forbidden(*args, **kwargs):
        raise AssertionError("Runtime discovery must never read file contents")
    monkeypatch.setattr(Path, "read_text", forbidden)
    facts = capture(tmp_path, [(tmp_path, True)])
    assert not facts["project_entries"]["web/node_modules"]
    assert "do-not-disclose" not in json.dumps(facts)
    assert "PASSWORD" not in json.dumps(facts)
    assert set(facts["tools_available"]) == {"git", "python3", "node", "npm", "uv"}


def test_reviewer_message_contains_harness_context():
    messages = build_messages(known_world="", history=[], request="Verify billing", tool_name="run_shell", arguments={"command": "python -m pytest"}, action_context={"worker": "sam", "workspace": "/acme/billing-service"})
    assert "WORKER ACTION CONTEXT" in messages[-1]["content"]
    assert "/acme/billing-service" in messages[-1]["content"]


def test_verdict_cannot_authorize_a_changed_argument_object(manager, monkeypatch):
    original, request = request_for(manager)
    engine, _ = lead_for_review(manager, monkeypatch, during=lambda: original.data["arguments"].update(command="different command"))
    assert authorize(engine, request)[-1] is False


def test_request_answered_after_authorization_is_not_executed(manager, monkeypatch):
    engine, _ = lead_for_review(manager, monkeypatch)
    original, request = request_for(manager)
    assert authorize(engine, request)[-1] is True
    manager.inbox.resolve(original.id, "deny")
    result, status = engine._execute_sync(ToolCall(id="decision", name=request.tool_name, arguments=request.arguments))
    assert status == "error"
    assert "changed before execution" in result["error"]


def test_persisted_human_floor_survives_engine_rebuild(manager, monkeypatch):
    engine, asked = lead_for_review(manager, monkeypatch)
    original = manager.inbox.add_approval("worker", "Run script", data={
        "tool": "run_shell", "arguments": {"command": "python downloaded.py"},
        "escalation": {"kind": "human_required", "reason": "Downloaded executable"},
    })
    request = PermissionRequest("decide_worker_call", {"worker": "sam", "call_id": original.id, "decision": "allow"}, None, "requires approval")
    assert authorize(engine, request)[-1] is False
    assert asked == []


def test_two_live_viewers_receive_authoritative_mode(manager):
    from fastapi.testclient import TestClient
    from coworker.server import create_app
    manager.provider = ScriptedProvider([])
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/lead") as first:
        assert first.receive_json()["type"] == "ready"
        with client.websocket_connect("/ws/session/lead") as second:
            assert second.receive_json()["type"] == "ready"
            first.send_json({"type": "set_mode", "mode": "auto-approve"})
            for socket in (first, second):
                notice = socket.receive_json()
                assert notice["type"] == "mode_notice"
                assert notice["data"]["mode"] == "auto-approve"
