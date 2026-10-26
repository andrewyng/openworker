"""Side tasks (coworker/sidetasks.py): the parent's `start_side_task`, the child's
`report_back`, the spawn on the parent's coworker/folder/model, the report card on the
parent (a notice the model never sees), and the silent-child fallback."""

import asyncio

from coworker.providers.base import AssistantTurn, ToolCall
from coworker.server.manager import SessionManager
from coworker.sessions import SessionRecord
from coworker.sidetasks import SIDE_TASK_REPORT_KIND, SIDE_TASK_STATUS_KIND
from tests.test_engine import ScriptedProvider, _text_turn


def _mgr(tmp_path, monkeypatch, turns=()):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(workspace=tmp_path, provider=ScriptedProvider(list(turns)))


def _parent(mgr, tmp_path, sid="p1"):
    ws = tmp_path / "proj"
    ws.mkdir(exist_ok=True)
    engine = mgr.get_engine(sid, workspace=str(ws), agent="cowork")
    engine.messages.append({"role": "user", "content": "Draft the scope of work for the client."})
    mgr.save(sid, engine)
    return engine


def _report_notices(mgr, sid):
    return [
        m
        for m in mgr.get_engine(sid).messages
        if m.get("role") == "notice" and m.get("kind") == SIDE_TASK_REPORT_KIND
    ]


def test_spawn_runs_child_on_parent_setup_and_silent_child_still_reports(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch, [_text_turn("Hoops use break-beam, net-motion or vision sensors.")])
    parent = _parent(mgr, tmp_path)
    assert "start_side_task" in set(parent.registry.names())
    assert "report_back" not in set(parent.registry.names())

    assert asyncio.run(mgr._spawn_side_task("p1", "c1", "Hoop sensing", "How do hoops sense a made shot?"))

    child = mgr.get_engine("c1")
    names = set(child.registry.names())
    assert "report_back" in names and "start_side_task" not in names  # no nesting
    assert "web_search" in child.permissions.session_allow_tools  # research needs no card
    record = mgr.session_store.load("c1")
    assert record.title == "Hoop sensing"
    assert record.workspace == mgr.session_store.load("p1").workspace
    assert record.model == parent.model
    assert record.agent == "cowork"
    # The brief reached the child as its opening message.
    opening = next(m for m in record.messages if m["role"] == "user")
    assert "How do hoops sense a made shot?" in opening["content"]
    # The scripted child answered in prose and never called report_back: its turn's end
    # reported its last message, and the child is done.
    assert record.side_task["parent_session"] == "p1"
    assert record.side_task["status"] == "done"
    (card,) = _report_notices(mgr, "p1")
    assert card["side_task"]["title"] == "Hoop sensing"
    assert card["side_task"]["session_id"] == "c1"
    assert card["side_task"]["status"] == "new"
    assert "break-beam" in card["text"]
    # The card is display-only: the parent's model never sees the report.
    assert all("break-beam" not in str(m.get("content", "")) for m in mgr.get_engine("p1")._outbound_messages())
    # Sidebar rows: the child carries its parent, the parent nothing.
    rows = {r["session_id"]: r for r in mgr.list_sessions()}
    assert rows["c1"]["side_task"] == {"parent_session": "p1", "title": "Hoop sensing", "status": "done"}
    assert rows["p1"]["side_task"] == {}


def test_report_back_tool_cards_parent_and_ends_the_turn(tmp_path, monkeypatch):
    call = ToolCall(
        id="t1",
        name="report_back",
        arguments={"summary": "Three methods: break-beam, net-motion, vision.", "report_path": "side-tasks/hoops.md"},
    )
    mgr = _mgr(tmp_path, monkeypatch, [AssistantTurn(tool_calls=[call]), _text_turn("never used")])
    _parent(mgr, tmp_path)
    asyncio.run(mgr._spawn_side_task("p1", "c1", "Hoop sensing", "How do hoops sense a made shot?"))

    record = mgr.session_store.load("c1")
    assert record.side_task["status"] == "done"
    assert record.side_task["report"]["report_path"] == "side-tasks/hoops.md"
    (card,) = _report_notices(mgr, "p1")
    assert card["side_task"]["summary"] == "Three methods: break-beam, net-motion, vision."
    assert card["side_task"]["report_path"] == "side-tasks/hoops.md"
    # A successful report ends the child's turn: the second scripted reply was never requested.
    assert mgr.provider._turns == [_text_turn("never used")]
    # The card survives reload exactly as persisted.
    stored = [m for m in mgr.session_store.load("p1").messages if m.get("kind") == SIDE_TASK_REPORT_KIND]
    assert stored and stored[0]["side_task"]["id"] == card["side_task"]["id"]


def test_report_waits_while_parent_is_mid_turn(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch, [_text_turn("first findings")])
    _parent(mgr, tmp_path)
    asyncio.run(mgr._spawn_side_task("p1", "c1", "Hoop sensing", "brief"))
    assert len(_report_notices(mgr, "p1")) == 1

    mgr.mark_running("p1")
    assert mgr.report_side_task("c1", "late findings")["ok"]
    assert len(_report_notices(mgr, "p1")) == 1  # not inside the reply
    assert [p["summary"] for p in mgr._pending_side_reports["p1"]] == ["late findings"]
    mgr.mark_idle("p1")
    assert [c["text"] for c in _report_notices(mgr, "p1")] == ["first findings", "late findings"]
    assert "p1" not in mgr._pending_side_reports


def test_report_status_is_the_users_and_persists(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch, [_text_turn("findings")])
    _parent(mgr, tmp_path)
    asyncio.run(mgr._spawn_side_task("p1", "c1", "Hoop sensing", "brief"))
    (card,) = _report_notices(mgr, "p1")
    report_id = card["side_task"]["id"]

    assert mgr.set_side_task_report_status("p1", report_id, "added") == {"ok": True}
    assert _report_notices(mgr, "p1")[0]["side_task"]["status"] == "added"
    # The log is append-only: the verdict persists as its own marker after the card.
    stored = mgr.session_store.load("p1").messages
    markers = [m for m in stored if m.get("kind") == SIDE_TASK_STATUS_KIND]
    assert markers == [{**markers[0], "side_task": {"id": report_id, "status": "added"}}]
    assert stored.index(markers[0]) > next(i for i, m in enumerate(stored) if m.get("kind") == SIDE_TASK_REPORT_KIND)
    assert mgr.set_side_task_report_status("p1", report_id, "dismissed") == {"ok": True}
    assert _report_notices(mgr, "p1")[0]["side_task"]["status"] == "dismissed"
    # Markers are display-only too: the model sees none of this.
    assert all(m.get("role") != "notice" for m in mgr.get_engine("p1")._outbound_messages())
    assert not mgr.set_side_task_report_status("p1", report_id, "archived")["ok"]
    assert not mgr.set_side_task_report_status("p1", "nope", "added")["ok"]


def test_start_side_task_validates_and_caps(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _parent(mgr, tmp_path)
    assert "needs a title" in mgr.start_side_task("p1", "", "brief")["error"]
    assert "needs a brief" in mgr.start_side_task("p1", "Title", "")["error"]
    # Three running children already: the fourth is refused before anything is spawned.
    for i in range(3):
        sid = f"c{i}"
        mgr.session_store.save(SessionRecord(session_id=sid, workspace=str(tmp_path / "proj"), model="m", mode="interactive", agent="cowork"))
        mgr.session_store.set_side_task(sid, {"parent_session": "p1", "title": f"Job {i}", "status": "running"})
    assert "3 side tasks are already running" in mgr.start_side_task("p1", "Fourth", "brief")["error"]
    # One finished: there is room again — but without the server loop nothing can spawn.
    mgr.session_store.set_side_task("c0", {"parent_session": "p1", "title": "Job 0", "status": "done"})
    assert "running server" in mgr.start_side_task("p1", "Fourth", "brief")["error"]
    assert mgr._spawning_side_tasks == {}


def test_report_back_refuses_outside_a_side_task(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _parent(mgr, tmp_path)
    assert "not a side task" in mgr.report_side_task("p1", "summary")["error"]
    assert _report_notices(mgr, "p1") == []
