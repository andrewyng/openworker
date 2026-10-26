"""Team View contracts: status ownership, quiet delivery, projections and timing."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from coworker.teams import Actor, AuthorityError, BoardError, Role, TeamStore
from coworker.teams.store import ITEM_STATUS, WORKER_WAITING
from coworker.teams.summary import (
    all_events,
    breakdown,
    waiting_items,
    tokens,
    make_summary,
)
from coworker.teams.tools import board_tools, with_mention
from coworker.teams.registry import TeamRegistry, TeamWorker
from coworker.inbox import InboxStore
from coworker.sessions import SessionRecord
from coworker.server.manager import SessionManager

LEAD = Actor(id="lead", role=Role.LEAD)
WORKER = Actor(id="sam", role=Role.WORKER)
OTHER = Actor(id="maya", role=Role.WORKER)


@pytest.fixture
def store(tmp_path):
    store = TeamStore(tmp_path / "teams.db")
    yield store
    store.close()


def assigned(store):
    item = store.create_item(
        "acme", LEAD, title="Invoice PDF", criteria="Download verified"
    )
    return store.assign("acme", LEAD, item["id"], WORKER.id)


def test_status_is_owned_and_does_not_change_work_state(store):
    item = assigned(store)
    updated = store.set_status("acme", WORKER, item["id"], "Testing 18 of 24 cases")
    assert updated["state"] == item["state"]
    assert updated["updated_seq"] == item["updated_seq"]
    assert updated["status"] == "Testing 18 of 24 cases"
    with pytest.raises(BoardError):
        store.set_status("acme", OTHER, item["id"], "Wrong item")
    with pytest.raises(AuthorityError):
        store.set_status("acme", LEAD, item["id"], "Wrong role")


def test_reassignment_revokes_status_and_clears_old_line(store):
    item = assigned(store)
    store.set_status("acme", WORKER, item["id"], "Testing")
    moved = store.assign("acme", LEAD, item["id"], OTHER.id)
    assert moved["status"] == ""
    with pytest.raises(BoardError):
        store.set_status("acme", WORKER, item["id"], "Still testing")
    assert store.set_status("acme", OTHER, item["id"], "Reading hand-off")["status"]


@pytest.mark.parametrize("text", ["x" * 81, "two\nlines", "two\rlines", None])
def test_status_validation(store, text):
    item = assigned(store)
    with pytest.raises(BoardError):
        store.set_status("acme", WORKER, item["id"], text)


def test_status_boundary_empty_and_replay(store):
    item = assigned(store)
    store.set_status("acme", WORKER, item["id"], "x" * 80)
    store.rebuild("acme")
    assert store.get_item("acme", item["id"], actor=LEAD)["status"] == "x" * 80
    assert store.set_status("acme", WORKER, item["id"], "")["status"] == ""


def test_status_is_excluded_before_feed_pagination(store):
    item = assigned(store)
    store.consume_feed("acme", LEAD.id, 2)
    store.consume_subscription("acme", LEAD.id, 2)
    for n in range(205):
        store.set_status("acme", WORKER, item["id"], f"Testing step {n}")
    assert not store.feed_for("acme", LEAD.id)
    assert not store.subscribed_events("acme", LEAD.id)
    store.transition("acme", WORKER, item["id"], "in_progress")
    store.transition("acme", WORKER, item["id"], "review")
    assert store.subscribed_events("acme", LEAD.id)[0]["payload"]["to"] == "review"
    assert all(e["kind"] != ITEM_STATUS for e in store.feed_for("acme", "observer"))


def test_worker_with_multiple_items_sets_the_explicit_one(store):
    first, second = assigned(store), assigned(store)
    store.set_status("acme", WORKER, second["id"], "Testing the second task")
    assert store.get_item("acme", first["id"], actor=LEAD)["status"] == ""


def test_mention_escapes_markdown_and_does_not_mutate():
    item = {"id": 2, "title": "[x](https://example.com) *bold*\nnext"}
    mention = with_mention(item)
    assert mention["mention"].endswith("](task:2)")
    assert "\\[x\\]" in mention["mention"]
    assert "mention" not in item


def test_role_filtered_tool_surface(store):
    assert "set_status" in {
        t.__name__ for t in board_tools(store, space="acme", actor=WORKER)
    }
    assert "set_status" not in {
        t.__name__ for t in board_tools(store, space="acme", actor=LEAD)
    }


def test_waiting_requires_a_current_pending_prompt():
    inbox = InboxStore()
    p = inbox.add_approval("worker-session", "Run tests")
    events = [
        {
            "kind": WORKER_WAITING,
            "item_id": 2,
            "actor": "sam",
            "payload": {
                "prompt_id": p.id,
                "session_id": "worker-session",
                "tool": "run_shell",
            },
        }
    ]
    items = [{"id": 2, "assignee": "sam", "state": "in_progress"}]
    assert 2 in waiting_items(events, inbox, items)
    inbox.resolve(p.id, "allow")
    assert not waiting_items(events, inbox, items)


def test_parallel_time_is_wall_clock_union():
    result = breakdown(
        [(0, 5, "tool_ms"), (2, 8, "tool_ms"), (8, 9, "model_ms")], 0, 10
    )
    assert result == dict(model_ms=1000, tool_ms=8000, waited_ms=0, queued_ms=1000)
    assert sum(result.values()) == 10000


def test_waiting_has_priority_and_intervals_are_clipped():
    result = breakdown(
        [(-5, 20, "model_ms"), (3, 6, "waited_ms"), (2, 7, "tool_ms")], 0, 10
    )
    assert result == dict(model_ms=5000, tool_ms=2000, waited_ms=3000, queued_ms=0)


def test_usage_is_normalized_defensively():
    assert tokens(
        dict(input=-1, output="12", cache_read=None, cache_write="bad")
    ) == dict(input=0, output=12, cache_read=0, cache_write=0)


def test_event_reader_paginates(store):
    for _ in range(2005):
        store.append_event("acme", ITEM_STATUS, WORKER, payload={"text": ""})
    assert len(list(all_events(store, "acme"))) == 2005


def test_real_manager_summary_and_current_wait(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path / "repo", data_dir=tmp_path / "data")
    manager.session_store.save(
        SessionRecord(
            session_id="lead-session",
            workspace=str(tmp_path / "repo"),
            model="sample",
            mode="interactive",
            agent="swe-lead",
        )
    )
    team = manager.teams.create(
        space=manager._board_space("lead-session"),
        lead_session="lead-session",
        lead_actor="lead",
        workers=[
            TeamWorker(actor="sam", persona="swe-worker", session_id="worker-session")
        ],
    )
    item = manager.team_store.create_item(
        team.space, LEAD, title="Invoice PDF", criteria="Verified"
    )
    manager.team_store.assign(team.space, LEAD, item["id"], WORKER.id)
    manager.team_store.transition(team.space, WORKER, item["id"], "in_progress")
    manager.team_store.set_status(team.space, WORKER, item["id"], "Running tests")
    manager.session_store.save(
        SessionRecord(
            session_id="worker-session",
            workspace=str(tmp_path / "repo"),
            model="sample",
            mode="interactive",
            agent="swe-worker",
            messages=[
                {
                    "role": "assistant",
                    "content": "Testing",
                    "ts": 10,
                    "usage": {
                        "input": 10,
                        "cache_read": 20,
                        "output": 5,
                        "cache_write": 2,
                    },
                }
            ],
        )
    )
    summary = manager.team_summary(team.team_id)
    assert summary["items"][0]["status"] == "Running tests"
    assert summary["totals"]["tokens"] == 37
    assert summary["counts"]["working"] == 1
    assert summary["items"][0]["timing"] is None
    assert manager.team_summary("missing") == {"error": "team not found"}
    asyncio.run(manager.aclose())


def test_engine_records_and_strips_timing(tmp_path):
    from test_engine import _engine, _tool_turn, _text_turn, _collect

    (tmp_path / "sample.txt").write_text("sample")
    engine, _ = _engine(
        tmp_path, [_tool_turn("read_file", {"path": "sample.txt"}), _text_turn("Read")]
    )
    _collect(engine, "read")
    model = next(m for m in engine.messages if m["role"] == "assistant")
    tool = next(m for m in engine.messages if m["role"] == "tool")
    assert model["timing"]["model_ms"] >= 0
    assert tool["timing"]["tool_ms"] >= 0
    assert all("timing" not in m for m in engine._outbound_messages())


def test_call_after_review_is_not_attributed_to_the_previous_item():
    events = [
        {"seq": 1, "ts": 1, "kind": "item_created", "item_id": 1, "payload": {}},
        {
            "seq": 2,
            "ts": 10,
            "kind": "item_assigned",
            "item_id": 1,
            "payload": {"assignee": "sam"},
        },
        {
            "seq": 3,
            "ts": 20,
            "kind": "item_transitioned",
            "item_id": 1,
            "payload": {"to": "review"},
        },
        {
            "seq": 4,
            "ts": 30,
            "kind": "item_transitioned",
            "item_id": 1,
            "payload": {"to": "done"},
        },
    ]
    item = {
        "id": 1,
        "title": "Task",
        "created_ts": 1,
        "creator": "lead",
        "assignee": "sam",
        "state": "done",
        "refs": [],
        "links": [],
    }
    worker = SessionRecord(
        session_id="worker",
        workspace="acme",
        model="sample",
        mode="interactive",
        messages=[
            {
                "role": "assistant",
                "ts": 29,
                "usage": {"input": 100},
                "timing": {"model_started": 25, "model_ms": 10000},
            }
        ],
    )
    manager = SimpleNamespace(
        session_board=lambda sid: {"items": [item]},
        team_store=SimpleNamespace(
            events=lambda space, since_seq, limit: [
                e for e in events if e["seq"] > since_seq
            ]
        ),
        inbox=InboxStore(),
        _engines={},
        is_running=lambda sid: False,
        session_store=SimpleNamespace(
            load=lambda sid: worker if sid == "worker" else None
        ),
    )
    team = SimpleNamespace(
        lead_session="lead",
        lead_actor="lead",
        team_id="t",
        space="acme",
        created_at=1,
        workers=[TeamWorker(actor="sam", persona="swe-worker", session_id="worker")],
    )
    result = make_summary(manager, team, 40)
    assert result["items"][0]["timing"] is None
    assert sum(result["items"][0]["tokens"].values()) == 0
    assert result["totals"]["tokens"] == 100
    # Worker accounting covers the session, not whichever task the UI opens.
    assert sum(result["workers"][0]["tokens"].values()) == 100
    assert result["workers"][0]["usage_partial"] is False


def test_chart_bucketing_preserves_all_tokens_by_role():
    from coworker.teams.summary import compact_usage_points

    points = [
        {"ts": n, "role": "lead" if n % 2 else "swe-worker", "tokens": 3}
        for n in range(10000)
    ]
    result = compact_usage_points(points)
    assert len(result) <= 240
    assert sum(p["tokens"] for p in result) == 30000
    assert sum(p["tokens"] for p in result if p["role"] == "lead") == 15000
