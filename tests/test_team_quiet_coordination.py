import asyncio
import time
from unittest.mock import Mock

from coworker.teams import Actor, Role
from coworker.teams.store import TeamStore
from coworker.teams.tools import board_tools
from test_engine import ScriptedProvider, _text_turn
from test_team_live_gaps import manager
from test_team_reader_recovery import create_item
from test_idle_checkins import timer, drain, finish

SAM = Actor(id="sam", role=Role.WORKER)


def test_progress_and_publications_are_quiet_and_do_not_cancel_timer(manager):
    space, lead, item = create_item(manager)
    manager.team_store.transition(space, SAM, item["id"], "in_progress")
    manager.team_store.comment(space, SAM, item["id"], "Halfway through")
    manager.team_store.attach_ref(space, SAM, item["id"], "Version one", "attachment://" + "a"*64 + ".md#report.md")
    manager.team_store.attach_ref(space, SAM, item["id"], "Version two", "attachment://" + "b"*64 + ".md#report.md")
    wake = timer(manager)
    assert asyncio.run(drain(manager)) == 0
    assert wake.state == "pending"
    assert not manager.session_store.load("lead").messages
    assert len(manager.team_store.get_item(space, item["id"], actor=lead)["comments"]) == 3


def test_one_review_handoff_after_publications_and_notes(manager):
    manager.provider = ScriptedProvider([_text_turn("Reviewing the evidence")])
    manager.TEAM_BATCH_SECONDS = 0
    space, _, item = create_item(manager)
    manager.team_store.transition(space, SAM, item["id"], "in_progress")
    for n in range(5):
        manager.team_store.comment(space, SAM, item["id"], f"Evidence detail {n}")
    handoff = manager.team_store.transition(space, SAM, item["id"], "review", comment="PASS; published versions 1/2", refs=["report.md"])
    async def run():
        assert await drain(manager) == 1
        await finish(manager)
    asyncio.run(run())
    msgs = [m for m in manager.session_store.load("lead").messages if m["role"] == "user"]
    assert len(msgs) == 1
    rows = msgs[0]["source"]["board"]["rows"]
    assert len(rows) == 1 and rows[0]["seq"] == handoff["seq"]
    assert "Evidence detail" not in msgs[0]["content"]
    assert not manager._pending_board_context("lead")[0]


def test_acceptance_keeps_completed_worker_quiet(manager):
    space, lead, item = create_item(manager)
    manager.team_store.transition(space, SAM, item["id"], "in_progress")
    manager.team_store.transition(space, SAM, item["id"], "review", comment="Verified")
    manager.team_store.consume_feed(space, "sam", manager.team_store.events(space)[-1]["seq"])
    manager.team_store.transition(space, lead, item["id"], "done", comment="Accepted, thanks")
    team = manager.teams.for_lead_session("lead")
    assert asyncio.run(manager._drain_team_member(team, session_id="worker", actor="sam", is_lead=False)) == 0
    assert not manager.team_store.feed_for(space, "sam")
    assert not manager.is_running("worker")


def test_dependency_completion_only_wakes_live_dependent_worker(manager):
    space, lead, task = create_item(manager)
    _, _, prerequisite = create_item(manager, "maya")
    manager.team_store.link(space, lead, prerequisite["id"], "blocks", task["id"])
    maya = Actor(id="maya", role=Role.WORKER)
    manager.team_store.transition(space, maya, prerequisite["id"], "in_progress")
    manager.team_store.transition(space, maya, prerequisite["id"], "review")
    manager.team_store.transition(space, lead, prerequisite["id"], "done")
    event = manager.team_store.events(space)[-1]
    team = manager.teams.for_lead_session("lead")
    assert manager._actionable_team_events(team, [event], actor="sam", is_lead=False)
    manager.team_store.transition(space, lead, task["id"], "canceled")
    assert not manager._actionable_team_events(team, [event], actor="sam", is_lead=False)


def test_explicit_worker_question_wakes_lead_without_batch_delay(manager):
    manager.provider = ScriptedProvider([_text_turn("Answering the question")])
    space, _, item = create_item(manager)
    # Worker-created task: lead is not its creator/assignee. Subscription still works.
    question = manager.team_store.create_item(space, SAM, title="Question", criteria="Decision")
    manager.team_store.comment(space, SAM, question["id"], "Which acceptance rule?", needs_attention=True)
    async def run():
        assert await drain(manager) == 1
        await finish(manager)
    asyncio.run(run())
    messages = manager.session_store.load("lead").messages
    assert any("Which acceptance rule?" in m.get("content", "") for m in messages if m["role"] == "user")


def test_human_note_and_send_back_are_not_muted(manager):
    space, lead, item = create_item(manager)
    manager.team_store.transition(space, SAM, item["id"], "in_progress")
    manager.team_store.transition(space, SAM, item["id"], "review")
    manager.team_store.transition(space, lead, item["id"], "in_progress", comment="Fix this acceptance gap")
    team = manager.teams.for_lead_session("lead")
    event = manager.team_store.events(space)[-1]
    assert manager._actionable_team_events(team, [event], actor="sam", is_lead=False)
    note = manager.team_store.comment(space, Actor(id="user", role=Role.USER), item["id"], "Change the scope")
    assert manager._actionable_team_events(team, [note], actor="sam", is_lead=False)
    assert manager._actionable_team_events(team, [note], actor="lead", is_lead=True)


def test_reassignment_interrupts_previous_busy_owner(manager):
    space, lead, item = create_item(manager)
    manager.team_store.consume_feed(space, "sam", manager.team_store.events(space)[-1]["seq"])
    manager.team_store.assign(space, lead, item["id"], "maya")
    engine = manager.get_engine("worker")
    engine.request_interrupt = Mock()
    manager.mark_running("worker")
    team = manager.teams.for_lead_session("lead")
    assert asyncio.run(manager._drain_team_member(team, session_id="worker", actor="sam", is_lead=False)) == 0
    engine.request_interrupt.assert_called_once()
    assert manager.team_store.feed_for(space, "sam")  # delivery not acknowledged until accepted


def test_cancellation_interrupts_busy_worker_without_acknowledging_early(manager):
    space, lead, item = create_item(manager)
    manager.team_store.consume_feed(space, "sam", manager.team_store.events(space)[-1]["seq"])
    manager.team_store.transition(space, lead, item["id"], "canceled", comment="Stop now")
    engine = manager.get_engine("worker")
    engine.request_interrupt = Mock()
    manager.mark_running("worker")
    team = manager.teams.for_lead_session("lead")
    assert asyncio.run(manager._drain_team_member(team, session_id="worker", actor="sam", is_lead=False)) == 0
    engine.request_interrupt.assert_called_once()
    assert manager.team_store.feed_for(space, "sam")


def test_stale_assignment_does_not_restart_previous_owner(manager):
    space, lead, item = create_item(manager)
    manager.team_store.assign(space, lead, item["id"], "maya")
    team = manager.teams.for_lead_session("lead")
    events = manager.team_store.feed_for(space, "sam")
    chosen = manager._actionable_team_events(team, events, actor="sam", is_lead=False)
    assert len(chosen) == 1 and chosen[0]["payload"]["previous"] == "sam"


def test_shared_scan_does_not_skip_human_note_behind_quiet_page(manager):
    space, _, item = create_item(manager)
    for _ in range(230):
        manager.team_store.comment(space, SAM, item["id"], "Routine evidence")
    note = manager.team_store.comment(space, Actor(id="user", role=Role.USER), item["id"], "Important new restriction")
    manager.team_store.comment(space, SAM, item["id"], "Question", needs_attention=True)
    assert asyncio.run(drain(manager)) == 0  # acknowledge first quiet page only
    text, rows, receipt = manager._pending_board_context("lead")
    assert "Important new restriction" in text
    assert any(r["seq"] == note["seq"] for r in rows)
    assert receipt["feed"] >= note["seq"]


def test_watchdog_ignores_busy_workers_and_human_waits(manager):
    space, _, item = create_item(manager)
    manager.team_store.transition(space, SAM, item["id"], "in_progress")
    team = manager.teams.for_lead_session("lead")
    manager._team_last_alive["lead"] = time.time() - 700
    manager.mark_running("worker")
    assert not manager._lead_backstop_due(team)
    manager._running_sessions.discard("worker")
    prompt = manager.inbox.add_approval("worker", "Run check")
    assert not manager._lead_backstop_due(team)
    manager.inbox.resolve(prompt.id, "deny")
    assert manager._lead_backstop_due(team)
    manager._team_watchdog_alerted["lead"] = manager._stalled_team_state(team)
    assert not manager._lead_backstop_due(team)  # unchanged stall is not polling
    manager.TEAM_LEAD_BACKSTOP_SECS = 0
    assert not manager._lead_backstop_due(team)


def test_native_question_and_transition_kick_delivery(manager):
    space, lead, item = create_item(manager)
    notify = Mock()
    tools = {t.__name__: t for t in board_tools(manager.team_store, space=space, actor=SAM, on_change=notify)}
    tools["comment"](item["id"], "Question", needs_attention=True)
    tools["transition"](item["id"], "in_progress")
    assert notify.call_count == 2
    tools["set_status"](item["id"], "Reading evidence")
    assert notify.call_count == 2


def test_own_accepted_prerequisite_wakes_worker_with_more_work(manager):
    space, lead, first = create_item(manager)
    _, _, second = create_item(manager)
    manager.team_store.link(space, lead, first["id"], "blocks", second["id"])
    manager.team_store.transition(space, SAM, first["id"], "in_progress")
    manager.team_store.transition(space, SAM, first["id"], "review")
    manager.team_store.transition(space, lead, first["id"], "done")
    team = manager.teams.for_lead_session("lead")
    event = manager.team_store.events(space)[-1]
    assert manager._actionable_team_events(team, [event], actor="sam", is_lead=False)


def test_quiet_acknowledgement_survives_store_reopen(manager):
    space, _, item = create_item(manager)
    note = manager.team_store.comment(space, SAM, item["id"], "Routine detail")
    assert asyncio.run(drain(manager)) == 0
    reopened = TeamStore(manager.team_store.db_path)
    try:
        page = reopened.delivery_page(space, "lead", is_lead=True)
        assert page["directs"] == [] and page["subs"] == []
        assert reopened._cursor(f"feed:lead:{space}") >= note["seq"]
    finally:
        reopened.close()
