"""PR4 — the server validates approval answers, and autonomy changes are recorded.

`POST /v1/inbox/{id}/resolve` takes a raw resolution string, so without validation any
local API caller could mint a grant the UI deliberately never offers — e.g. a session-wide,
any-argument shell grant, which `ApprovalCard.tsx` withholds on purpose in favour of the
narrower command-scoped one.

See `ocw-context/docs/reviewed-auto-mode.md` Part 3.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from coworker.engine import ApprovalOutcome
from coworker.server.manager import SessionManager


@pytest.fixture
def manager(tmp_path):
    mgr = SessionManager(workspace=str(tmp_path), data_dir=tmp_path / "data")
    yield mgr
    mgr.audit_store.close()


def _request(tool: str, arguments=None, metadata=None):
    return SimpleNamespace(
        tool_name=tool, arguments=arguments or {}, metadata=metadata, reason=""
    )


def _stages(manager, session_id: str) -> list[str]:
    rows = manager.audit_store.list(session_id=session_id)
    return [r.get("stage") for r in rows]


# -- grants the UI never offers are downgraded ----------------------------------
def test_always_tool_refused_for_shell(manager):
    out = manager.approval_outcome(
        "always_tool", _request("run_shell", {"command": "rm -rf /"}), "s1"
    )
    assert out is ApprovalOutcome.ONCE, "a session-wide any-command shell grant must not stand"
    assert "grant_refused" in _stages(manager, "s1")


def test_always_tool_refused_for_connector_and_external(manager):
    connector = SimpleNamespace(requires_approval=True, category="connector")
    assert (
        manager.approval_outcome("always_tool", _request("gmail_send", {}, connector), "s2")
        is ApprovalOutcome.ONCE
    )
    # An MCP tool is not category=connector but is still external — same reasoning applies:
    # the grant would be unbounded over every future argument.
    mcp = SimpleNamespace(requires_approval=True, category="mcp")
    assert (
        manager.approval_outcome(
            "always_tool", _request("mcp__github__create_issue", {}, mcp), "s3"
        )
        is ApprovalOutcome.ONCE
    )


def test_always_tool_refused_for_save_skill(manager):
    assert (
        manager.approval_outcome("always_tool", _request("save_skill", {}), "s4")
        is ApprovalOutcome.ONCE
    )


def test_always_command_only_for_shell(manager):
    assert (
        manager.approval_outcome(
            "always_command", _request("write_file", {"path": "a"}), "s5"
        )
        is ApprovalOutcome.ONCE
    )


def test_always_tool_refused_for_url_carrying_egress(manager):
    # §1.9: "always allow web_fetch" would cover every future destination — the
    # domain-scoped grant is the one the card offers, so tool-wide is refused here.
    assert (
        manager.approval_outcome(
            "always_tool", _request("web_fetch", {"url": "https://bbc.com/x"}), "s13"
        )
        is ApprovalOutcome.ONCE
    )
    assert "grant_refused" in _stages(manager, "s13")
    # Fixed-destination egress keeps it: web_search's tool-wide IS provider-wide.
    assert (
        manager.approval_outcome(
            "always_tool", _request("web_search", {"query": "x"}), "s14"
        )
        is ApprovalOutcome.ALWAYS_TOOL
    )


def test_provider_change_clears_web_search_session_grant(manager):
    # §1.9: the search grant is consent to a NAMED destination; a new provider is a new
    # destination, so every live session's grant dies with the old one.
    from types import SimpleNamespace as NS

    eng = NS(permissions=NS(session_allow_tools={"web_search", "run_shell_x"}))
    manager._engines["s15"] = eng
    before = manager.get_web_search()["provider"]
    other = next(p for p in manager.get_web_search()["providers"] if p != before)
    assert manager.set_web_search(other)["ok"]
    assert "web_search" not in eng.permissions.session_allow_tools
    assert "run_shell_x" in eng.permissions.session_allow_tools  # only the search grant dies
    # Re-setting the SAME provider (e.g. adding a key) leaves grants alone.
    eng.permissions.session_allow_tools.add("web_search")
    assert manager.set_web_search(other, api_key="k")["ok"]
    assert "web_search" in eng.permissions.session_allow_tools


def test_always_domain_only_for_egress_with_a_url(manager):
    assert (
        manager.approval_outcome("always_domain", _request("write_file", {"path": "a"}), "s6")
        is ApprovalOutcome.ONCE
    )
    assert (
        manager.approval_outcome("always_domain", _request("web_fetch", {}), "s7")
        is ApprovalOutcome.ONCE
    )


# -- the legitimate grants still work -------------------------------------------
def test_legitimate_grants_survive(manager):
    assert (
        manager.approval_outcome(
            "always_tool", _request("write_file", {"path": "a.txt"}), "ok1"
        )
        is ApprovalOutcome.ALWAYS_TOOL
    )
    assert (
        manager.approval_outcome(
            "always_command", _request("run_shell", {"command": "npm test"}), "ok2"
        )
        is ApprovalOutcome.ALWAYS_COMMAND
    )
    assert (
        manager.approval_outcome(
            "always_domain", _request("web_fetch", {"url": "https://docs.python.org/3"}), "ok3"
        )
        is ApprovalOutcome.ALWAYS_DOMAIN
    )
    assert "grant_refused" not in _stages(manager, "ok1")


def test_plain_vocabularies_unchanged(manager):
    req = _request("write_file", {"path": "a.txt"})
    assert manager.approval_outcome("allow", req, "s8") is ApprovalOutcome.ONCE
    assert manager.approval_outcome("once", req, "s8") is ApprovalOutcome.ONCE
    assert manager.approval_outcome("deny", req, "s8") is ApprovalOutcome.DENY
    assert manager.approval_outcome("nonsense", req, "s8") is ApprovalOutcome.DENY


def test_always_maps_to_tool_grant_and_is_validated(manager):
    # The Inbox/channel vocabulary "always" maps to a tool grant — and is validated too, so
    # a Slack reply cannot mint what the in-app card would refuse.
    assert (
        manager.approval_outcome("always", _request("run_shell", {"command": "x"}), "s9")
        is ApprovalOutcome.ONCE
    )


# -- autonomy changes are recorded ----------------------------------------------
def test_unattended_transition_audited(manager):
    manager.set_unattended("s10", True)
    rows = manager.audit_store.list(session_id="s10")
    assert [r["stage"] for r in rows] == ["unattended_changed"]
    assert rows[0]["status"] == "raised"

    manager.set_unattended("s10", False)
    assert [r["stage"] for r in manager.audit_store.list(session_id="s10")][-1] == (
        "unattended_changed"
    )


def test_unattended_no_op_not_audited(manager):
    manager.set_unattended("s11", False)  # already off
    assert _stages(manager, "s11") == []


def test_mode_change_audited_with_direction(manager):
    manager.audit_autonomy_change("s12", "mode", "interactive", "auto")
    manager.audit_autonomy_change("s12", "mode", "auto", "plan")
    # AuditStore.list is newest-first (ORDER BY id DESC), so reverse for chronology.
    rows = list(reversed(manager.audit_store.list(session_id="s12")))
    assert [r["status"] for r in rows] == ["raised", "lowered"]
    assert rows[0]["reason"] == "mode: interactive → auto"
