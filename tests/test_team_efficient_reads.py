import json

import pytest

from coworker.teams import Actor, Role
from coworker.teams.store import TeamStore
from coworker.teams.tools import board_tools


@pytest.fixture
def board(tmp_path):
    store = TeamStore(tmp_path / "board.db")
    lead = Actor(id="lead", role=Role.LEAD)
    item = store.create_item("acme", lead, title="Verify billing", criteria="Independent evidence")
    store.assign("acme", lead, item["id"], "sam")
    yield store, lead, item["id"], {t.__name__: t for t in board_tools(store, space="acme", actor=lead)}
    store.close()


def test_mutation_receipts_do_not_grow_with_history(board):
    store, lead, item, tools = board
    for _ in range(40):
        store.comment("acme", lead, item, "Detailed findings " * 200)
    full = store.get_item("acme", item, actor=lead)
    assert len(json.dumps(full)) > 100000
    for args in [("transition", {"item": item, "to": "in_progress"}),
                 ("transition", {"item": item, "to": "review", "comment": "verified"}),
                 ("transition", {"item": item, "to": "done", "comment": "Accepted"})]:
        result = tools[args[0]](**args[1])
        assert "error" not in result
        assert len(json.dumps(result)) < 300
        assert "comments" not in result
    snapshot = tools["get_item"](item)
    assert snapshot["comment_count"] == 42
    assert len(json.dumps(snapshot)) < 1000
    assert len(store.get_item("acme", item, actor=lead)["comments"]) == 42


def test_cursor_replays_complete_comments_without_consuming_feed(board):
    store, lead, item, tools = board
    seqs = [store.comment("acme", lead, item, f"Finding {n}")["seq"] for n in range(55)]
    cursor, got = 0, []
    while True:
        page = tools["get_item_comments"](item, cursor, 20)
        got.extend(c["seq"] for c in page["comments"])
        cursor = page["next_after_seq"]
        if not page["has_more"]:
            break
    assert got == seqs
    # Explicit replay works even with a new tool binding (restart/compaction).
    replay = {t.__name__: t for t in board_tools(store, space="acme", actor=lead)}
    assert replay["get_item_comments"](item, 0, 20)["comments"][0]["seq"] == seqs[0]
    new = store.comment("acme", lead, item, "Concurrent new finding")
    assert tools["get_item_comments"](item, cursor)["comments"][0]["seq"] == new["seq"]


def test_oversized_comment_is_explicit_and_fully_retrievable(board):
    store, lead, item, tools = board
    body = "Evidence α\n" * 7000
    seq = store.comment("acme", lead, item, body)["seq"]
    page = tools["get_item_comments"](item)
    assert len(json.dumps(page)) < 1000
    assert page["comments"][0]["body_omitted"] is True
    text, offset = "", 0
    while True:
        part = tools["get_item_comment"](item, seq, offset, 12000)
        from coworker.toolresult import bound_tool_result
        delivered = bound_tool_result(part, max_bytes=10000, spill_dir=None, step=1, tool_name="get_item_comment")
        assert delivered["text"] == part["text"]  # cursor never skips a head/tail omission
        text += part["text"]
        offset = part["next_offset"]
        if not part["has_more"]:
            break
    assert text == body
    assert "error" in tools["get_item_comment"](item, seq + 1)


def test_new_comment_reader_cross_item_and_scope_protection(board):
    store, lead, item, _ = board
    other = store.create_item("acme", lead, title="Private task", criteria="Check")
    store.assign("acme", lead, other["id"], "maya")
    seq = store.comment("acme", lead, other["id"], "Private finding")["seq"]
    worker = Actor(id="sam", role=Role.WORKER)
    tools = {t.__name__: t for t in board_tools(store, space="acme", actor=worker)}
    assert "error" in tools["get_item_comments"](other["id"])
    assert "error" in tools["get_item_comment"](item, seq)
    assert "error" in tools["get_proposal"](other["id"])
    for bad in [True, -1, "2"]:
        assert "error" in tools["get_item_comments"](item, bad)


def test_comments_past_legacy_500_event_boundary_remain_readable(board):
    store, lead, item, tools = board
    for n in range(505):
        seq = store.comment("acme", lead, item, f"Finding {n}")["seq"]
    assert tools["get_item"](item)["comment_count"] == 505
    assert tools["get_item_comment"](item, seq)["text"] == "Finding 504"


def test_listing_pages_without_full_descriptions(board):
    store, lead, _, tools = board
    for n in range(60):
        store.create_item("acme", lead, title=f"Task {n}", criteria="Verify", description="long " * 500)
    page = tools["list_items"](limit=50)
    assert page["has_more"] and len(page["items"]) == 50
    assert all("description" not in i for i in page["items"])
    tail = tools["list_items"](after_item=page["next_after_item"])
    assert len(tail["items"]) == 11 and not tail["has_more"]


def test_shared_proposal_remains_explicitly_readable_without_repetition(board):
    from proposal_fixtures import work_proposal
    store, lead, _, tools = board
    plan = work_proposal()
    result = store.create_proposal("acme", lead, plan)
    item = result["items"][0]["id"]
    detail = tools["get_item"](item)
    assert "proposal" not in detail
    assert detail["proposal_ref"] == {"item": item, "read_tool": "get_proposal"}
    assert detail["activity"] == "review"
    proposal = tools["get_proposal"](item)
    assert proposal["proposal"]["external_actions"] == plan["external_actions"]
    assert proposal["authority"] == "intent_not_access_grants"
