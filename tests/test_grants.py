"""Tests for standing-grant revocation dashboard (#620).

Covers:
- Explicit revoke_trust on WorkspaceTrustStore and RiskOverrideStore
- PermissionEngine session revocation methods
- SessionManager.list_active_grants across all 5 autonomy layers
- SessionManager.revoke_grant and audit trail recording (stage="grant_revoked", status="revoked")
- Workspace trust changes auditing
- REST endpoints /v1/grants and /v1/grants/revoke
"""

from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

from coworker.automation import Schedule, ScheduledTask
from coworker.overrides import RiskOverrideStore
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app
from coworker.sessions import SessionRecord
from coworker.workspace_trust import WorkspaceTrustStore


class _DummyProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def test_workspace_trust_store_revoke(tmp_path: Path):
    store = WorkspaceTrustStore(tmp_path / "trust.json")
    proj = tmp_path / "project"
    proj.mkdir()

    store.set_trusted(proj, True)
    assert store.is_trusted(proj) is True

    # Revoke trust explicitly
    assert store.revoke_trust(proj) is True
    assert store.is_trusted(proj) is False

    # Second revocation returns False (already revoked)
    assert store.revoke_trust(proj) is False


def test_risk_override_store_revoke(tmp_path: Path):
    store = RiskOverrideStore(tmp_path / "overrides.json")
    pattern = "mcp__github__create_issue"

    store.set_trust(pattern)
    assert pattern in store.trust_patterns()

    assert store.revoke_trust(pattern) is True
    assert pattern not in store.trust_patterns()

    assert store.revoke_trust(pattern) is False


def test_permission_engine_session_revocations(tmp_path: Path):
    eng = PermissionEngine(workspace_root=tmp_path)

    # Tool grant & revoke
    eng.allow_tool_for_session("run_shell")
    assert "run_shell" in eng.session_allow_tools
    assert eng.revoke_tool_for_session("run_shell") is True
    assert "run_shell" not in eng.session_allow_tools
    assert eng.revoke_tool_for_session("run_shell") is False

    # Command grant & revoke
    eng.allow_command_for_session("pytest")
    assert "pytest" in eng.session_allow_commands
    assert eng.revoke_command_for_session("pytest") is True
    assert "pytest" not in eng.session_allow_commands
    assert eng.revoke_command_for_session("pytest") is False

    # Domain grant & revoke
    eng.allow_domain_for_session("https://api.github.com/v1")
    assert "api.github.com" in eng.session_allow_domains
    assert eng.revoke_domain_for_session("api.github.com") is True
    assert "api.github.com" not in eng.session_allow_domains
    assert eng.revoke_domain_for_session("api.github.com") is False

    # Readonly grant & revoke
    eng.allow_readonly_for_session()
    assert eng.session_readonly is True
    assert eng.revoke_readonly_for_session() is True
    assert eng.session_readonly is False
    assert eng.revoke_readonly_for_session() is False


def test_manager_workspace_trust_audit(tmp_path: Path):
    manager = SessionManager(workspace=tmp_path, provider=_DummyProvider())
    proj = tmp_path / "trusted_proj"
    proj.mkdir()

    # Grant trust
    res = manager.set_workspace_trust(proj, trusted=True)
    assert res["ok"] is True

    events = manager.audit_store.list()
    grant_events = [e for e in events if e.get("stage") == "workspace_trust_granted"]
    assert len(grant_events) >= 1
    assert grant_events[-1]["status"] == "granted"

    # Revoke trust
    res = manager.revoke_workspace_trust(proj)
    assert res["ok"] is True

    events = manager.audit_store.list()
    revoke_events = [
        e for e in events if e.get("stage") == "grant_revoked" and e.get("status") == "revoked"
    ]
    assert len(revoke_events) >= 1
    assert str(proj.resolve()) in revoke_events[-1]["reason"]


def test_manager_mcp_trust_audit(tmp_path: Path):
    manager = SessionManager(workspace=tmp_path, provider=_DummyProvider())
    override_store = manager._override_store()
    pattern = "mcp__slack__post_message"
    override_store.set_trust(pattern)

    res = manager.revoke_mcp_trust("slack", "post_message")
    assert res["ok"] is True

    events = manager.audit_store.list()
    revoke_events = [
        e for e in events if e.get("stage") == "grant_revoked" and e.get("tool") == pattern
    ]
    assert len(revoke_events) == 1
    assert revoke_events[0]["status"] == "revoked"


def test_manager_standing_automation_audit(tmp_path: Path):
    manager = SessionManager(workspace=tmp_path, provider=_DummyProvider())
    task = ScheduledTask(
        title="Slack Bot",
        instructions="do something",
        schedule=Schedule(kind="cron", cron="0 9 * * 1"),
        workspace=str(tmp_path),
        always_allowed_tools=["send_message slack:C123"],
    )
    manager.task_store.save(task)

    # Revoke via update_automation
    res = manager.update_automation(task.id, {"revoke": "send_message slack:C123"})
    assert res["ok"] is True

    events = manager.audit_store.list()
    revoke_events = [
        e for e in events if e.get("stage") == "grant_revoked" and e.get("tool") == "send_message"
    ]
    assert len(revoke_events) == 1
    assert revoke_events[0]["status"] == "revoked"


def test_list_and_revoke_active_grants(tmp_path: Path):
    manager = SessionManager(workspace=tmp_path, provider=_DummyProvider())

    # 1. Setup workspace trust
    proj = tmp_path / "project_alpha"
    proj.mkdir()
    manager.set_workspace_trust(proj, trusted=True)

    # 2. Setup MCP trust
    manager._override_store().set_trust("mcp__github__create_issue")

    # 3. Setup standing automation rule
    task = ScheduledTask(
        title="Sync Task",
        instructions="sync files",
        schedule=Schedule(kind="cron", cron="0 0 * * *"),
        workspace=str(tmp_path),
        always_allowed_tools=["web_search", "send_message slack:general"],
    )
    manager.task_store.save(task)

    # 4. Setup session grants (persisted and live)
    # Stored session
    rec = SessionRecord(
        session_id="stored_sess_1",
        workspace=str(tmp_path),
        model="test-model",
        mode="interactive",
        title="Old Session",
        grants={
            "tools": ["run_shell"],
            "commands": ["npm test"],
            "domains": ["api.example.com"],
            "readonly": True,
        },
    )
    manager.session_store.save(rec)

    # Live engine session
    live_eng = manager.get_engine("live_sess_2")
    live_eng.permissions.allow_tool_for_session("write_file")
    live_eng.permissions.allow_command_for_session("git status")
    live_eng.permissions.allow_domain_for_session("live.example.com")

    # List active grants
    grants = manager.list_active_grants()
    grant_ids = {g["id"] for g in grants}

    assert f"workspace:{proj.resolve()}" in grant_ids
    assert "mcp:mcp__github__create_issue" in grant_ids
    assert f"task:{task.id}:web_search" in grant_ids
    assert f"task:{task.id}:send_message slack:general" in grant_ids
    assert "session:stored_sess_1:tool:run_shell" in grant_ids
    assert "session:stored_sess_1:command:npm test" in grant_ids
    assert "session:stored_sess_1:domain:api.example.com" in grant_ids
    assert "session:stored_sess_1:readonly" in grant_ids
    assert "session:live_sess_2:tool:write_file" in grant_ids
    assert "session:live_sess_2:command:git status" in grant_ids
    assert "session:live_sess_2:domain:live.example.com" in grant_ids

    # Revoke standing automation grant via revoke_grant
    res = manager.revoke_grant(f"task:{task.id}:web_search")
    assert res["ok"] is True
    updated_task = manager.task_store.get(task.id)
    assert "web_search" not in updated_task.always_allowed_tools

    # Revoke live session tool grant via revoke_grant
    res = manager.revoke_grant("session:live_sess_2:tool:write_file")
    assert res["ok"] is True
    assert "write_file" not in live_eng.permissions.session_allow_tools

    # Revoke stored session command grant via revoke_grant
    res = manager.revoke_grant("session:stored_sess_1:command:npm test")
    assert res["ok"] is True
    reloaded_rec = manager.session_store.load("stored_sess_1")
    assert "npm test" not in reloaded_rec.grants.get("commands", [])

    # Revoke MCP tool grant
    res = manager.revoke_grant("mcp:mcp__github__create_issue")
    assert res["ok"] is True
    assert "mcp__github__create_issue" not in manager._override_store().trust_patterns()

    # Revoke workspace trust
    res = manager.revoke_grant(f"workspace:{proj.resolve()}")
    assert res["ok"] is True
    assert manager.workspace_trust.is_trusted(proj) is False


def test_grants_rest_api(tmp_path: Path):
    manager = SessionManager(workspace=tmp_path, provider=_DummyProvider())
    client = TestClient(create_app(manager))

    proj = tmp_path / "rest_proj"
    proj.mkdir()
    manager.set_workspace_trust(proj, trusted=True)

    # GET /v1/grants
    resp = client.get("/v1/grants")
    assert resp.status_code == 200
    data = resp.json()
    assert "grants" in data
    grant_names = [g["name"] for g in data["grants"]]
    assert str(proj.resolve()) in grant_names

    # POST /v1/grants/revoke
    resp = client.post(
        "/v1/grants/revoke",
        json={"grant_id": f"workspace:{proj.resolve()}"},
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data.get("ok") is True

    # Check that it's no longer trusted
    assert manager.workspace_trust.is_trusted(proj) is False
