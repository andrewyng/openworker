"""Scoped board reads, honest approval attribution and interrupted-prompt recovery."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from coworker.inbox import InboxStore
from coworker.server.app import create_app
from coworker.sessions import SessionRecord
from coworker.teams import Actor, Role
from coworker.teams.store import WORKER_WAITING
from coworker.teams.tools import board_tools
from test_engine import ScriptedProvider, _text_turn
from test_team_live_gaps import manager


def create_item(manager, assignee="sam"):
    space = manager.teams.for_lead_session("lead").space
    lead = Actor(id="lead", role=Role.LEAD)
    item = manager.team_store.create_item(
        space,
        lead,
        title="Verify invoice",
        criteria="Tests pass",
        description="Acme sample",
    )
    manager.team_store.assign(space, lead, item["id"], assignee)
    return space, lead, item


def test_get_item_tool_reads_full_evidence_with_worker_visibility(manager):
    space, lead, item = create_item(manager)
    worker = Actor(id="sam", role=Role.WORKER)
    manager.team_store.comment(
        space,
        worker,
        item["id"],
        "Evidence: " + "passed " * 200,
        refs=["test_billing.py:7"],
    )
    _, _, hidden = create_item(manager, "maya")
    read = next(
        t
        for t in board_tools(manager.team_store, space=space, actor=worker)
        if t.__name__ == "get_item"
    )
    detail = read(item["id"])
    assert detail["description"] == "Acme sample"
    assert detail["criteria"] == "Tests pass"
    assert "comments" not in detail
    assert detail["comment_count"] == 1
    read_comments = next(t for t in board_tools(manager.team_store, space=space, actor=worker)
                         if t.__name__ == "get_item_comments")
    assert read_comments(item["id"])["comments"][0]["body"].endswith("passed " * 200)
    assert "error" in read_comments(hidden["id"])
    assert detail["mention"].endswith(f"(task:{item['id']})")
    assert "error" in read(hidden["id"])
    for bad in (0, -1, True, "1"):
        assert "error" in read(bad)
    lead_read = next(
        t
        for t in board_tools(manager.team_store, space=space, actor=lead)
        if t.__name__ == "get_item"
    )
    assert lead_read(hidden["id"])["id"] == hidden["id"]
    assert not any(k in detail for k in ("messages", "transcript"))


@pytest.mark.parametrize("count", [0, 1, 2])
def test_wait_attribution_requires_exactly_one_in_progress_item(manager, count):
    ids = []
    for _ in range(count):
        space, _, item = create_item(manager)
        manager.team_store.transition(
            space, Actor(id="sam", role=Role.WORKER), item["id"], "in_progress"
        )
        ids.append(item["id"])
    manager.note_worker_waiting(
        "worker", "run_shell", prompt_id="sample", preview="Run tests"
    )
    team = manager.teams.for_lead_session("lead")
    event = [
        e for e in manager.team_store.events(team.space) if e["kind"] == WORKER_WAITING
    ][-1]
    assert event["item_id"] == (ids[0] if count == 1 else None)


def save_worker(manager, messages):
    manager.session_store.save(
        SessionRecord(
            session_id="worker",
            workspace=manager.default_workspace,
            agent="swe-worker",
            model="sample",
            mode="interactive",
            messages=messages,
        )
    )


def call(call_id):
    return {
        "role": "assistant",
        "tool_calls": [
            {"id": call_id, "function": {"name": "run_shell", "arguments": "{}"}}
        ],
    }


def test_restart_reconciliation_retires_answered_calls_not_unanswered_or_legacy(
    manager, monkeypatch
):
    save_worker(
        manager,
        [
            call("old"),
            {
                "role": "tool",
                "tool_call_id": "old",
                "content": '{"error":"tool call interrupted"}',
            },
            call("current"),
        ],
    )
    old = manager.inbox.add_approval(
        "worker", "Old interrupted call", tool_call_id="old"
    )
    current = manager.inbox.add_approval(
        "worker", "Current call", tool_call_id="current"
    )
    legacy = manager.inbox.add_approval("worker", "Legacy prompt")
    parent = manager.inbox.add_approval(
        "lead", "Old lead decision", data={"worker_prompt_id": old.id}
    )
    resume = AsyncMock()
    monkeypatch.setattr(manager, "_durable_resume", resume)
    assert asyncio.run(manager.resolve_inbox(old.id, "allow")) is False
    assert old.resolution == "interrupted"
    assert parent.resolution == "superseded"
    resume.assert_not_called()
    assert current.state == legacy.state == "pending"
    assert manager.reconcile_obsolete_prompts("worker") == 0
    assert asyncio.run(manager.resolve_inbox(current.id, "deny")) is True
    resume.assert_awaited_once()
    assert InboxStore(manager.inbox.path).get(old.id).resolution == "interrupted"


def test_new_turn_repairs_and_retires_old_pending_prompt(manager):
    save_worker(manager, [call("abandoned")])
    old = manager.inbox.add_approval("worker", "Abandoned", tool_call_id="abandoned")
    manager.provider = ScriptedProvider([_text_turn("Following the new instruction.")])
    asyncio.run(manager.deliver_to_session("worker", "Do not run the old command."))
    assert old.resolution == "interrupted"
    assert not manager.inbox.pending("worker")


def test_inbox_listing_reconciles_stale_persisted_prompt(manager):
    save_worker(
        manager, [call("old"), {"role": "tool", "tool_call_id": "old", "content": "{}"}]
    )
    old = manager.inbox.add_approval("worker", "Old", tool_call_id="old")
    client = TestClient(create_app(manager))
    result = client.get(
        "/v1/inbox", params={"session_id": "worker", "state": "pending"}
    )
    assert result.status_code == 200
    assert result.json()["items"] == []
    assert old.resolution == "interrupted"


def test_resolution_from_http_thread_releases_async_waiter():
    store = InboxStore()
    item = store.add_approval("worker", "Sample")

    async def run():
        wait = asyncio.create_task(store.wait(item.id))
        await asyncio.sleep(0)
        await asyncio.to_thread(store.resolve, item.id, "deny")
        assert await asyncio.wait_for(wait, 1) == "deny"

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["timer", "completion", "event"])
def test_check_in_messages_have_no_alarm_emoji(manager, kind):
    wake = SimpleNamespace(
        kind=kind,
        job_id="job",
        event_key="sample",
        note="Check tests",
        fire_at="2026-09-20T12:00:00Z",
    )
    message = manager._wake_message(wake)
    assert message.startswith("Check-in")
    assert "⏰" not in message
    assert "Check tests" in message
