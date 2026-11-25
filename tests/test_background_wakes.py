"""Background shell tasks wake their session when they exit.

A turn can end while a `run_shell(run_in_background=true)` task is still working (a long
download, a build). Before this, nothing told the agent when it finished: `wake_on` stored a
completion wake but no code ever marked it due, and a user message cancelled the agent's
`sleep_for` check-in, so the session went quiet until the user asked again.
"""

from __future__ import annotations

import asyncio

import pytest

from coworker.selfwake import WakeStore
from coworker.server.manager import SessionManager
from coworker.sessions import SessionRecord
from test_engine import ScriptedProvider, _text_turn


class FakeExecutor:
    """Stands in for the session's shell; only the background-task listing matters here."""

    def __init__(self, *tasks, cwd=""):
        self.tasks = [dict(t) for t in tasks]
        self.cwd = cwd

    def background_tasks(self):
        return [dict(t) for t in self.tasks]

    def set(self, task_id, **fields):
        next(t for t in self.tasks if t["task_id"] == task_id).update(fields)


def task(task_id="bg-1", status="running", seen=False, command="python collect_tasks.py --download"):
    return {"task_id": task_id, "command": command, "status": status, "exit_code": None, "seen": seen}


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "repo").mkdir()
    m = SessionManager(data_dir=tmp_path / "data", workspace=tmp_path / "repo")
    m.provider = ScriptedProvider([])
    monkeypatch.setattr(m, "_maybe_autotitle", lambda *args: None)
    for sid, agent in (("s1", "cowork"), ("c1", "code")):
        m.session_store.save(
            SessionRecord(session_id=sid, workspace=m.default_workspace, model="sample", mode="interactive", agent=agent)
        )
    yield m
    asyncio.run(m.aclose())


def shell(manager, *tasks, session_id="s1"):
    executor = FakeExecutor(*tasks, cwd=str(manager.default_workspace))
    manager.get_engine(session_id).executor = executor
    return executor


def completion_wakes(manager, session_id="s1"):
    return [w for w in manager.wakes.pending(session_id) if w.kind == "completion"]


async def finish(manager, session_id="s1"):
    for _ in range(200):
        await asyncio.sleep(.01)
        if not manager._team_inflight and not manager.is_running(session_id):
            return
    raise AssertionError("delivery did not finish")


def test_turn_ending_with_a_running_task_waits_for_it(manager):
    shell(manager, task())
    manager.sync_background_wakes()
    [wake] = completion_wakes(manager)
    assert wake.job_id == "bg-1" and "collect_tasks.py" in wake.note
    assert manager._session_liveness("s1") == "sleeping"
    manager.sync_background_wakes()
    assert len(completion_wakes(manager)) == 1  # armed once, not every tick


def test_task_exit_wakes_the_session(manager):
    provider = ScriptedProvider([_text_turn("The background task finished.")])
    manager.provider = provider
    executor = shell(manager, task())
    manager.sync_background_wakes()
    executor.set("bg-1", status="exited", exit_code=0)

    async def run():
        assert await manager.resume_due_wakes() == 1
        await finish(manager)
        assert await manager.resume_due_wakes() == 0

    asyncio.run(run())
    wake_message = [m for m in manager.session_store.load("s1").messages if m["role"] == "user"][-1]
    assert "bg-1" in wake_message["content"] and "finished" in wake_message["content"]
    assert not manager.wakes.pending("s1")
    assert provider.calls == 1
    assert any(m["role"] == "assistant" and m["content"] == "The background task finished."
               for m in manager.session_store.load("s1").messages)


def test_a_status_question_does_not_drop_the_watch(manager):
    # The field case: the agent's sleep_for check-in was cancelled by the user's
    # "Ask for a status" message, and nothing else was left to wake it.
    shell(manager, task())
    manager.sync_background_wakes()
    manager.prepare_activity("s1", "user activity")
    assert [w.job_id for w in completion_wakes(manager)] == ["bg-1"]


def test_an_exit_the_agent_already_read_does_not_wake_it_again(manager):
    executor = shell(manager, task())
    manager.sync_background_wakes()
    executor.set("bg-1", status="exited", exit_code=0, seen=True)
    assert asyncio.run(manager.resume_due_wakes()) == 0
    assert not manager.wakes.pending("s1")
    assert not manager.wakes.cancelled_context("s1")  # nothing to tell the next turn


def test_explicit_wake_on_fires_when_the_task_exits(manager):
    executor = shell(manager, task("bg-2"))
    manager.wakes.add_completion("s1", "bg-2", note="build done")
    manager.sync_background_wakes()
    assert len(completion_wakes(manager)) == 1  # the agent's own wake, no duplicate
    executor.set("bg-2", status="exited", exit_code=1)
    manager.sync_background_wakes()
    assert [w.job_id for w in manager.wakes.due()] == ["bg-2"]


def test_a_task_the_shell_no_longer_knows_still_wakes(manager):
    executor = shell(manager, task())
    manager.sync_background_wakes()
    executor.tasks.clear()  # e.g. the sandbox restarted and took the task with it
    manager.sync_background_wakes()
    assert [w.job_id for w in manager.wakes.due()] == ["bg-1"]


def test_a_running_turn_is_not_armed(manager):
    shell(manager, task())
    manager.mark_running("s1")
    manager.sync_background_wakes()
    assert not completion_wakes(manager)


def test_a_stopped_session_is_not_armed(manager):
    shell(manager, task())
    manager.stop_session("s1")
    manager.sync_background_wakes()
    assert not completion_wakes(manager)


def test_sessions_without_self_wake_are_left_alone(manager):
    # The code persona has no self-wake tools; it is never resumed on its own.
    shell(manager, task(), session_id="c1")
    manager.sync_background_wakes()
    assert not completion_wakes(manager, "c1")


def test_completion_is_scoped_to_its_session(tmp_path):
    # Task ids restart at bg-1 in every shell, so one session's exit must not wake another.
    store = WakeStore(tmp_path / "wakes.json")
    mine = store.add_completion("s1", "bg-1")
    other = store.add_completion("s2", "bg-1")
    assert store.complete_job("bg-1", session_id="s1") == [mine]
    assert other in store.pending("s2") and other not in store.due()


def test_add_completion_reuses_a_pending_wake(tmp_path):
    store = WakeStore(tmp_path / "wakes.json")
    first = store.add_completion("s1", "bg-1", note="first")
    assert store.add_completion("s1", "bg-1", note="second") is first
    assert len(store.pending("s1")) == 1


def test_exit_before_first_scheduler_tick_wakes_once(manager):
    provider = ScriptedProvider([_text_turn("The background task finished.")])
    manager.provider = provider
    shell(manager, task(status="exited"))

    async def run():
        assert await manager.resume_due_wakes() == 1
        await finish(manager)
        assert await manager.resume_due_wakes() == 0

    asyncio.run(run())
    assert not completion_wakes(manager)
    assert provider.calls == 1
    assert any(m["role"] == "assistant" and m["content"] == "The background task finished."
               for m in manager.session_store.load("s1").messages)


def test_completion_waits_for_the_final_output(manager):
    executor = shell(manager, task())
    manager.sync_background_wakes()
    executor.set("bg-1", status="exited", exit_code=0, output_drained=False)
    manager.sync_background_wakes()
    assert not manager.wakes.due()
    executor.set("bg-1", output_drained=True)
    manager.sync_background_wakes()
    assert [w.job_id for w in manager.wakes.due()] == ["bg-1"]


def test_reading_an_exit_while_busy_retires_a_due_wake(manager):
    executor = shell(manager, task())
    manager.sync_background_wakes()
    manager.mark_running("s1")
    executor.set("bg-1", status="exited", exit_code=0)
    manager.sync_background_wakes()
    assert manager.wakes.due()
    executor.set("bg-1", seen=True)
    manager.sync_background_wakes()
    assert not completion_wakes(manager)
    assert not manager.wakes.cancelled_context("s1")


def test_stopping_an_armed_session_prevents_completion_resume(manager):
    executor = shell(manager, task())
    manager.sync_background_wakes()
    manager.stop_session("s1")
    executor.set("bg-1", status="exited", exit_code=0)
    assert asyncio.run(manager.resume_due_wakes()) == 0
    assert not completion_wakes(manager)


def test_stop_after_scheduling_prevents_completion_delivery(manager):
    shell(manager, task(status="exited"))
    wake = manager.wakes.add_completion("s1", "bg-1")
    manager.wakes.complete_job("bg-1", session_id="s1")
    manager.stop_session("s1")
    assert asyncio.run(manager.deliver_to_session("s1", "task finished", wake=wake)) is False
    assert not manager.session_store.load("s1").messages


def test_add_completion_reuses_a_due_wake_and_keeps_the_new_note(tmp_path):
    store = WakeStore(tmp_path / "wakes.json")
    first = store.add_completion("s1", "bg-1", note="automatic watch")
    store.complete_job("bg-1", session_id="s1")
    assert store.add_completion("s1", "bg-1", note="summarize the output") is first
    assert first.note == "summarize the output"
    assert len(store.pending("s1")) == 1


def test_runner_listing_failure_preserves_the_completion_watch(manager):
    from coworker.sandbox.executor import RunnerExecutor
    from coworker.sandbox.runner import protocol as P

    class Client:
        def call(self, method, params, **kwargs):
            if method == "task.start":
                return {"task_id": "bg-1"}
            raise P.RunnerError(P.INTERNAL_ERROR, "temporary disconnect")

    executor = RunnerExecutor(Client(), cwd=str(manager.default_workspace))
    executor.run_background("build")
    manager.get_engine("s1").executor = executor
    wake = manager.wakes.add_completion("s1", "bg-1")
    manager.sync_background_wakes()
    assert completion_wakes(manager) == [wake]
    assert not manager.wakes.due()


def test_runner_without_background_tasks_does_not_start_a_sandbox(tmp_path):
    from coworker.sandbox.executor import RunnerExecutor

    class Client:
        def call(self, *args, **kwargs):
            raise AssertionError("listing must not contact an unused sandbox")

    assert RunnerExecutor(Client(), cwd=str(tmp_path)).background_tasks() == []
