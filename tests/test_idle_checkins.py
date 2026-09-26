"""Idle deadlines, durable acceptance and batched lead delivery."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from coworker.selfwake import WakeStore, selfwake_tools
from coworker.teams import Actor, Role
from test_engine import ScriptedProvider, _text_turn
from test_team_live_gaps import manager
from test_team_reader_recovery import create_item


def timer(manager, seconds=180, note="Verify Maya's tests"):
    return manager.wakes.add_timer("lead", datetime.now(timezone.utc) + timedelta(seconds=seconds), note=note)


def review(manager):
    space, lead, item = create_item(manager)
    worker = Actor(id="sam", role=Role.WORKER)
    manager.team_store.transition(space, worker, item["id"], "in_progress")
    manager.team_store.transition(space, worker, item["id"], "review", comment="Tests passed", refs=["test_billing.py"])
    return space, item


async def drain(manager):
    team = manager.teams.for_lead_session("lead")
    return await manager._drain_team_member(team, session_id="lead", actor="lead", is_lead=True)


async def finish(manager):
    for _ in range(100):
        await asyncio.sleep(.01)
        if not manager._team_inflight and not manager.is_running("lead"):
            return
    raise AssertionError("delivery did not finish")


def test_both_sleep_tools_replace_each_other_without_changing_subscriptions(tmp_path):
    store = WakeStore(tmp_path / "wakes.json")
    relative, absolute, job, event = selfwake_tools(store, "lead")
    first = relative(180, "Old")
    second = absolute((datetime.now(timezone.utc) + timedelta(seconds=100)).isoformat(), "New")
    job("job"); event("event")
    assert first["wake_id"] not in {w.id for w in store.pending()}
    assert len(store.pending()) == 3
    store.cancel_sleep("lead", "board activity")
    reloaded = WakeStore(store.path)
    assert {w.kind for w in reloaded.pending()} == {"completion", "event"}
    assert [w.id for w in reloaded.cancelled_context("lead")] == [second["wake_id"]]
    assert reloaded.due(datetime.now(timezone.utc) + timedelta(days=1)) == []


def test_reminder_survives_crash_before_input_and_acknowledges_saved_input(manager):
    wake = timer(manager)
    activity = manager.prepare_activity("lead", "user activity")
    assert wake.id in activity["wake_ids"]
    assert WakeStore(manager.wakes.path).cancelled_context("lead")
    record = manager.session_store.load("lead")
    record.messages.append({"role": "user", "content": "New scope", "_activity": activity})
    manager.session_store.save(record)
    # Simulate restart after saving input but before acknowledging the wake.
    manager.wakes = WakeStore(manager.wakes.path)
    manager.reconcile_activity_receipts("lead")
    assert not manager.wakes.cancelled_context("lead")
    assert not manager.prepare_activity("lead", "user activity")["wake_ids"]


def test_user_input_is_verbatim_with_prior_reminder_and_board_context(manager):
    manager.provider = ScriptedProvider([_text_turn("Understood")])
    review(manager)
    wake = timer(manager)
    asyncio.run(manager.deliver_to_session("lead", "Ignore the old scope; inspect only."))
    engine = manager.get_engine("lead")
    user = next(m for m in engine.messages if m["role"] == "user")
    assert user["content"] == "Ignore the old scope; inspect only."
    assert "Verify Maya's tests" in user["_activity"]["text"]
    assert "review" in user["_activity"]["text"]
    outbound = engine._outbound_messages()
    index = next(i for i, m in enumerate(outbound) if isinstance(m.get("content"), str) and m["content"].startswith(user["content"]))
    assert outbound[index - 1]["role"] == "assistant"
    assert "newer user instructions take precedence" in outbound[index - 1]["content"]
    assert all("_activity" not in m for m in outbound)
    assert wake.state == "cancelled" and wake.context_delivered
    assert not manager._pending_board_context("lead")[0]


def test_busy_timer_defers_instead_of_steering_or_being_lost(manager):
    timer(manager, -1)
    manager.mark_running("lead")
    assert asyncio.run(manager.resume_due_wakes()) == 0
    assert len(manager.wakes.due()) == 1


def test_lead_timer_has_replayable_display_source_without_changing_receipt(manager):
    manager.provider = ScriptedProvider([_text_turn("Checked")])
    wake = timer(manager, -1, "Check verifier evidence")
    asyncio.run(manager._resume_wake(wake))
    message = next(m for m in manager.session_store.load("lead").messages if m["role"] == "user")
    assert message["source"]["connector"] == "board"
    assert message["source"]["board"]["check_in"] is True
    assert "Check verifier evidence" in message["source"]["text"]
    assert message["source"]["text"] == message["content"]
    assert wake.id in message["_activity"]["wake_ids"]
    assert not manager.wakes.pending("lead")


def test_due_timer_and_board_produce_one_turn(manager):
    manager.provider = ScriptedProvider([_text_turn("Checked")])
    review(manager)
    wake = timer(manager, -1)
    async def run():
        assert await drain(manager) == 1
        assert await manager.resume_due_wakes() == 0
        await finish(manager)
        assert await manager.resume_due_wakes() == 0
    asyncio.run(run())
    messages = manager.session_store.load("lead").messages
    assert len([m for m in messages if m["role"] == "user"]) == 1
    assert wake.state == "cancelled" and wake.context_delivered


def test_user_wins_queued_board_dispatch(manager):
    manager.provider = ScriptedProvider([_text_turn("Following your scope")])
    manager.TEAM_BATCH_SECONDS = 0
    review(manager)
    timer(manager)
    async def run():
        assert await drain(manager) == 1
        # The board task is scheduled but has not claimed the session yet.
        await manager.deliver_to_session("lead", "User's new scope")
        await finish(manager)
        assert await drain(manager) == 0
    asyncio.run(run())
    messages = manager.session_store.load("lead").messages
    assert [m["content"] for m in messages if m["role"] == "user"] == ["User's new scope"]


def test_stop_cancels_sleep_and_blocks_automatic_board_restart(manager):
    wake = timer(manager)
    review(manager)
    manager.stop_session("lead")
    assert asyncio.run(drain(manager)) == 0
    assert not manager.wakes.pending("lead")
    assert WakeStore(manager.wakes.path).stopped_sessions == {"lead"}
    manager.provider = ScriptedProvider([_text_turn("Continuing")])
    asyncio.run(manager.deliver_to_session("lead", "Continue with the new scope"))
    assert "lead" not in manager.wakes.stopped_sessions
    assert wake.context_delivered


def test_fixed_batch_window_does_not_slide_and_sends_all_items(manager):
    manager.provider = ScriptedProvider([_text_turn("Reviewing both")])
    review(manager)
    async def run():
        assert await drain(manager) == 0
        deadline = manager._team_batch_deadlines["lead"]
        review(manager)
        assert await drain(manager) == 0
        assert manager._team_batch_deadlines["lead"] == deadline
        manager._team_batch_deadlines["lead"] = 0
        assert await drain(manager) == 1
        await finish(manager)
    asyncio.run(run())
    messages = [m for m in manager.session_store.load("lead").messages if m["role"] == "user"]
    assert len(messages) == 1
    rows = messages[0]["source"]["board"]["rows"]
    assert len([r for r in rows if r.get("to") == "review"]) == 2


def test_approval_bypasses_window_and_keeps_independent_prompt_ids(manager):
    manager.provider = ScriptedProvider([_text_turn("Waiting for the human")])
    ids = []
    for _ in range(2):
        prompt = manager.inbox.add_approval("worker", "Run tests")
        ids.append(prompt.id)
        manager.note_worker_waiting("worker", "run_shell", prompt_id=prompt.id, preview="python -m unittest")
    async def run():
        assert await drain(manager) == 1
        await finish(manager)
    asyncio.run(run())
    message = next(m for m in manager.session_store.load("lead").messages if m["role"] == "user")
    assert {r["prompt_id"] for r in message["source"]["board"]["rows"]} == set(ids)


def test_resolved_approval_does_not_wake_or_cancel_sleep(manager):
    wake = timer(manager)
    prompt = manager.inbox.add_approval("worker", "Run tests")
    manager.note_worker_waiting("worker", "run_shell", prompt_id=prompt.id)
    manager.inbox.resolve(prompt.id, "deny")
    assert asyncio.run(drain(manager)) == 0
    assert wake.state == "pending"


def test_status_only_does_not_cancel_sleep(manager):
    space, lead, item = create_item(manager)
    worker = Actor(id="sam", role=Role.WORKER)
    manager.team_store.transition(space, worker, item["id"], "in_progress")
    manager.team_store.set_status(space, worker, item["id"], "Checking tests")
    wake = timer(manager)
    assert asyncio.run(drain(manager)) == 0
    assert wake.state == "pending"


def test_expiry_records_one_receipt_and_no_second_wake(manager):
    manager.provider = ScriptedProvider([_text_turn("Checked")])
    wake = timer(manager, -1)
    async def run():
        assert await manager.resume_due_wakes() == 1
        await finish(manager)
        assert await manager.resume_due_wakes() == 0
    asyncio.run(run())
    assert wake.state == "fired" and wake.context_delivered


def test_reopened_review_no_longer_wakes_lead_for_a_stale_decision(manager):
    space, item = review(manager)
    manager.team_store.transition(space, Actor(id="lead", role=Role.LEAD), item["id"], "in_progress", comment="Needs another test")
    text, rows, receipt = manager._pending_board_context("lead")
    assert text == "" and rows == []
    assert receipt["feed"] > 0  # still acknowledged without deleting audit history


def test_failed_delivery_keeps_due_timer(manager, monkeypatch):
    wake = timer(manager, -1)
    monkeypatch.setattr(manager, "get_engine", lambda *a: None)
    async def run():
        assert await manager.resume_due_wakes() == 1
        await finish(manager)
    asyncio.run(run())
    assert wake in manager.wakes.due()
    assert wake.state == "pending"


def test_persisted_board_receipt_recovers_after_crash(manager):
    review(manager)
    activity = manager.prepare_activity("lead", "user activity")
    record = manager.session_store.load("lead")
    record.messages.append({"role": "user", "content": "Review", "_activity": activity})
    manager.session_store.save(record)
    assert manager._pending_board_context("lead")[0]
    manager.reconcile_activity_receipts("lead")
    assert not manager._pending_board_context("lead")[0]


def test_question_is_urgent_but_not_an_approval(manager):
    prompt = manager.inbox.add_question("worker", "Which invoice?")
    manager.note_worker_waiting("worker", "ask_user", prompt_id=prompt.id)
    text, rows, _ = manager._pending_board_context("lead")
    assert "needs a human answer" in text
    assert "Answer with decide_worker_call" not in text
    assert rows[0]["kind"] == "waiting"


def test_blocker_bypasses_batch_window(manager):
    manager.provider = ScriptedProvider([_text_turn("Checking blocker")])
    space, _, item = create_item(manager)
    manager.team_store.transition(space, Actor(id="sam", role=Role.WORKER), item["id"], "in_progress")
    manager.team_store.transition(space, Actor(id="sam", role=Role.WORKER), item["id"], "blocked", comment="Need fixture")
    async def run():
        assert await drain(manager) == 1
        await finish(manager)
    asyncio.run(run())


def test_stop_catches_sleep_registered_late_in_interrupted_turn(manager):
    manager.stop_session("lead")
    wake = timer(manager)
    manager.mark_idle("lead")
    assert wake.state == "cancelled"
    assert not manager.wakes.pending("lead")


def test_idle_batch_flushes_without_waiting_for_scheduler(manager):
    manager.TEAM_BATCH_SECONDS = .02
    manager.provider = ScriptedProvider([_text_turn("Reviewing")])
    review(manager)
    async def run():
        assert await drain(manager) == 0
        # Limit this test's automatic tick to the lead, not the sample worker.
        async def lead_tick():
            return await drain(manager)
        manager.team_tick = lead_tick
        await asyncio.sleep(.05)
        await finish(manager)
    asyncio.run(run())
    assert len([m for m in manager.session_store.load("lead").messages if m["role"] == "user"]) == 1


def test_multiple_steering_messages_carry_reminder_and_board_only_once(manager):
    manager.provider = ScriptedProvider([_text_turn("Following the new scope")])
    engine = manager.get_engine("lead")
    review(manager)
    timer(manager)
    manager.mark_running("lead")
    async def run():
        await manager.deliver_to_session("lead", "First instruction")
        await manager.deliver_to_session("lead", "Second instruction")
        first = engine._steering[0][2]
        second = engine._steering[1][2]
        assert first["wake_ids"] and not second["wake_ids"]
        assert first["board_text"] and not second["board_text"]
        late = timer(manager, note="Registered while interrupted")
        await manager.broadcast_session("lead", {"type": "iteration_end", "data": {}})
        assert late.state == "cancelled"
        assert late.id in engine._steering[0][2]["wake_ids"]
        engine._inject_steering()
        manager.save("lead", engine)
        manager.reconcile_activity_receipts("lead")
        assert not manager.wakes.cancelled_context("lead")
        assert not manager._pending_board_context("lead")[0]
    asyncio.run(run())


def test_unsaved_in_memory_receipt_does_not_acknowledge_context(manager):
    engine = manager.get_engine("lead")
    timer(manager)
    activity = manager.prepare_activity("lead", "user activity")
    engine.messages.append({"role": "user", "content": "New scope", "_activity": activity})
    manager.reconcile_activity_receipts("lead")
    assert manager.wakes.cancelled_context("lead")


def test_legacy_multiple_timers_migrate_to_latest_sleep(tmp_path):
    path = tmp_path / "wakes.json"
    # Fixture construction only: emulate a persisted record from the old runtime.
    path.write_text(json.dumps({"wakes": [
        {"id": "old", "session_id": "lead", "kind": "timer", "created_at": "2026-09-19T01:00:00Z"},
        {"id": "new", "session_id": "lead", "kind": "timer", "created_at": "2026-09-19T02:00:00Z"}
    ]}))
    store = WakeStore(path)
    assert [w.id for w in store.pending("lead")] == ["new"]
    assert not store.cancelled_context("lead")
    assert [w.id for w in WakeStore(path).pending("lead")] == ["new"]
