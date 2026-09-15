"""Phase 2 gate — the Inbox: 3 item kinds, the resolve state machine, reconciliation, approver."""

from __future__ import annotations

import asyncio

from coworker.inbox import (
    KIND_APPROVAL,
    KIND_NOTIFICATION,
    STATE_RESOLVED,
    InboxStore,
    inbox_approver,
)


def test_add_and_filter(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    store.add_approval("s1", "Run shell?")
    store.add_question("s1", "Which env?")
    store.add_notification("s2", "Report ready")
    assert len(store.list(session_id="s1")) == 2
    assert len(store.pending("s1")) == 2
    assert store.list(session_id="s2")[0].kind == KIND_NOTIFICATION


def test_resolve_is_idempotent_first_responder_wins(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    item = store.add_approval("s1", "Run shell?")
    assert store.resolve(item.id, "allow") is True
    # A second resolution from any surface is a no-op; the first answer stands.
    assert store.resolve(item.id, "deny") is False
    got = store.get(item.id)
    assert got.state == STATE_RESOLVED and got.resolution == "allow"


def test_resolve_unknown_item(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    assert store.resolve("nope", "allow") is False


def test_persistence(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    item = store.add_approval("s1", "Run shell?")
    store.resolve(item.id, "allow")
    reloaded = InboxStore(tmp_path / "inbox.json")
    assert reloaded.get(item.id).resolution == "allow"


def test_reconcile_on_resume(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    answered = store.add_approval("s1", "Deploy?")
    store.resolve(answered.id, "allow")
    store.add_question("s1", "Still pending?")
    store.add_approval("other", "Not mine")
    out = store.reconcile_on_resume("s1")
    assert [i["title"] for i in out["pending"]] == ["Still pending?"]
    assert [i["title"] for i in out["recap"]] == ["Deploy?"]


def test_inbox_approver_allow(tmp_path):
    async def run():
        store = InboxStore(tmp_path / "inbox.json")
        from coworker.engine import ApprovalOutcome, PermissionRequest

        approver = inbox_approver(store, "s1")
        req = PermissionRequest("run_shell", {}, None, "needs approval")

        async def resolve_soon():
            for _ in range(200):
                pend = store.pending("s1")
                if pend:
                    store.resolve(pend[0].id, "allow")
                    return
                await asyncio.sleep(0.001)

        outcome, _ = await asyncio.gather(approver(req), resolve_soon())
        assert outcome is ApprovalOutcome.ONCE
        # The approval came in as an Inbox item.
        assert store.list(session_id="s1")[0].kind == KIND_APPROVAL

    asyncio.run(run())


def test_inbox_approver_deny(tmp_path):
    async def run():
        store = InboxStore(tmp_path / "inbox.json")
        from coworker.engine import ApprovalOutcome, PermissionRequest

        approver = inbox_approver(store, "s1")
        req = PermissionRequest("rm", {}, None, "danger")

        async def resolve_soon():
            for _ in range(200):
                pend = store.pending("s1")
                if pend:
                    store.resolve(pend[0].id, "deny")
                    return
                await asyncio.sleep(0.001)

        outcome, _ = await asyncio.gather(approver(req), resolve_soon())
        assert outcome is ApprovalOutcome.DENY

    asyncio.run(run())


def test_args_preview():
    from coworker.inbox import args_preview

    assert (
        args_preview({"path": "g.txt", "content": "buy milk"})
        == "path: g.txt · content: buy milk"
    )
    assert args_preview(None) == "" and args_preview({}) == ""
    assert "\n" not in args_preview({"x": "a\nb\nc"})  # newlines collapsed
    assert args_preview({"content": "z" * 300}).endswith("…")  # long values truncated


def test_approval_body_includes_tool_args():
    from coworker.engine import PermissionRequest
    from coworker.server.manager import _approval_body

    req = PermissionRequest(
        "write_file", {"path": "groceries.txt", "content": "buy milk"}, None, ""
    )
    body = _approval_body(req)
    assert "groceries.txt" in body and "buy milk" in body  # the card now shows *what*

    req2 = PermissionRequest("rm", {"path": "/x"}, None, "destructive")
    assert _approval_body(req2).startswith("destructive")  # reason leads when present


def test_approval_body_strips_boilerplate_reason():
    """OPE-136 found-in-testing: the live card filters the engine's default
    "requires approval" boilerplate — the parked/mirrored body must not bake it in."""
    from coworker.engine import PermissionRequest
    from coworker.server.manager import _approval_body

    req = PermissionRequest("write_file", {"path": "g.txt"}, None, "requires approval")
    body = _approval_body(req)
    assert "requires approval" not in body
    assert "g.txt" in body  # the args preview still carries the evidence


def test_approval_prompt_data_carries_mcp_evidence():
    """§35 parity: the parked item's data holds the same category, destination, and
    (non-boilerplate) reason the live card's event carries — so a reopened session
    renders the identical scope chip and evidence, never the vague fallback."""
    from types import SimpleNamespace

    from coworker.engine import PermissionRequest
    from coworker.server.manager import SessionManager

    fake_mgr = SimpleNamespace(
        task_store=SimpleNamespace(task_for_run_session=lambda _s: None)
    )
    req = PermissionRequest(
        "mcp__my_jira__searchJiraIssuesUsingJql",
        {"cloudId": "0bbb", "jql": "ORDER BY created DESC"},
        SimpleNamespace(category="mcp"),
        "requires approval",
        mcp_destination={"transport": "http", "host": "mcp.atlassian.com"},
    )
    data = SessionManager.approval_prompt_data(fake_mgr, "s1", req)
    assert data["category"] == "mcp"
    assert data["mcp_destination"] == {"transport": "http", "host": "mcp.atlassian.com"}
    assert "reason" not in data  # boilerplate never travels

    real = PermissionRequest(
        "mcp__my_jira__editJiraIssue",
        {"issueIdOrKey": "OPE-1"},
        SimpleNamespace(category="mcp"),
        "the reviewer wasn't sure this edit was asked for",
        mcp_destination={"transport": "http", "host": "mcp.atlassian.com"},
    )
    real_data = SessionManager.approval_prompt_data(fake_mgr, "s1", real)
    assert real_data["reason"] == "the reviewer wasn't sure this edit was asked for"

    # Non-MCP requests stay lean: no category/destination keys invented for them.
    plain = PermissionRequest("write_file", {"path": "g.txt"}, None, "requires approval")
    plain_data = SessionManager.approval_prompt_data(fake_mgr, "s1", plain)
    assert "mcp_destination" not in plain_data and "category" not in plain_data


def test_inbox_item_expiry_calculation_and_is_expired(tmp_path):
    from datetime import datetime, timedelta, timezone

    from coworker.inbox import is_expired

    store = InboxStore(tmp_path / "inbox.json")
    item_ttl = store.add_approval("s1", "Deploy?", ttl_seconds=60)
    assert item_ttl.expires_at is not None
    assert is_expired(item_ttl) is False

    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    item_past = store.add_approval("s1", "Old request", expires_at=past)
    assert is_expired(item_past) is True


def test_inbox_store_default_ttl(tmp_path):
    store = InboxStore(tmp_path / "inbox.json", default_ttl_seconds=120)
    item = store.add_approval("s1", "Deploy?")
    assert item.expires_at is not None

    # Explicit ttl overrides default
    item2 = store.add_approval("s1", "Fast", ttl_seconds=10)
    assert item2.expires_at is not None


def test_inbox_resolve_expired_item_rejected(tmp_path):
    from datetime import datetime, timedelta, timezone

    store = InboxStore(tmp_path / "inbox.json")
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    item = store.add_approval("s1", "Old request", expires_at=past)

    # Late resolve attempt fails and marks resolution as expired
    ok = store.resolve(item.id, "allow")
    assert ok is False
    assert item.state == STATE_RESOLVED
    assert item.resolution == "expired"


def test_inbox_list_and_get_auto_expire(tmp_path):
    from datetime import datetime, timedelta, timezone

    store = InboxStore(tmp_path / "inbox.json")
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    item = store.add_approval("s1", "Old request", expires_at=past)

    # Pending list does not include expired item
    assert len(store.pending("s1")) == 0

    # get() returns it resolved as expired
    got = store.get(item.id)
    assert got.state == STATE_RESOLVED
    assert got.resolution == "expired"


def test_inbox_wait_auto_resolves_when_ttl_elapses(tmp_path):
    async def run():
        store = InboxStore(tmp_path / "inbox.json")
        item = store.add_approval("s1", "Quick TTL", ttl_seconds=0.05)
        res = await store.wait(item.id)
        assert res == "expired"
        assert item.state == STATE_RESOLVED
        assert item.resolution == "expired"

    asyncio.run(run())


def test_inbox_approver_expired_outcome(tmp_path):
    async def run():
        store = InboxStore(tmp_path / "inbox.json")
        from coworker.engine import ApprovalOutcome, PermissionRequest

        approver = inbox_approver(store, "s1")
        req = PermissionRequest("run_shell", {}, None, "needs approval", ttl_seconds=0.05)
        outcome = await approver(req)
        assert outcome is ApprovalOutcome.EXPIRED

    asyncio.run(run())



def test_wait_already_expired_does_not_deadlock():
    import subprocess
    import sys
    from pathlib import Path

    result = subprocess.run([sys.executable, "-c", """
import asyncio
from coworker.inbox import InboxStore
s = InboxStore()
i = s.add_approval('session', 'approval', expires_at='2000-01-01T00:00:00+00:00')
assert asyncio.run(s.wait(i.id)) == 'expired'
assert s.get(i.id).resolution == 'expired'
"""], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
