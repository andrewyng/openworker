"""Tests for automation — models, store, next-run math, scheduler loop, tools, REST.

No network and no LLM: the scheduler's runner is injected with a fake; the agent-facing tools
operate on a real SQLite store; execution policy (catch-up, overlap) is exercised directly.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone

import pytest

from coworker.automation import (
    Schedule,
    ScheduledTask,
    Scheduler,
    TaskRun,
    TaskStore,
    compute_next_run,
)
from coworker.automation.tools import scheduling_tools


def _task(**kw) -> ScheduledTask:
    kw.setdefault("title", "Daily brief")
    kw.setdefault("instructions", "search the web and brief me")
    kw.setdefault("schedule", Schedule(kind="cron", cron="10 19 * * *"))
    kw.setdefault("workspace", "/tmp/cw-auto")
    return ScheduledTask(**kw)


# -- model / schedule ----------------------------------------------------------
def test_schedule_human():
    assert Schedule("cron", cron="10 19 * * *").human() == "Every day at ~7:10 PM"
    assert "Monday" in Schedule("cron", cron="0 9 * * 0").human()
    assert Schedule("cron", cron="0 9 5 * *").human() == "Monthly on day 5 at ~9:00 AM"
    assert Schedule("once", fire_at="2026-07-01T09:00:00").human().startswith("Once at")


def test_task_gets_own_thread_id():
    t = _task()
    assert t.task_session_id == f"__task__{t.id}"
    assert t.public()["schedule"] == "Every day at ~7:10 PM"


def test_compute_next_run_cron_explicit_utc():
    t = _task(schedule=Schedule(kind="cron", cron="10 19 * * *", timezone="UTC"))
    after = datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc).timestamp()
    nxt = compute_next_run(t, after=after)
    assert datetime.fromtimestamp(nxt, tz=timezone.utc) == datetime(
        2026, 6, 5, 19, 10, tzinfo=timezone.utc
    )


def test_compute_next_run_defaults_to_local_time():
    """Default 'local' tz: '7:10pm' fires at 19:10 on the *machine's* clock, not UTC."""
    t = _task()  # Schedule default timezone == "local"
    assert t.schedule.timezone == "local"
    nxt = compute_next_run(t)
    local = datetime.fromtimestamp(nxt).astimezone()
    assert (local.hour, local.minute) == (19, 10)


def test_compute_next_run_once_in_past_is_none():
    past = "2020-01-01T00:00:00+00:00"
    t = _task(schedule=Schedule(kind="once", fire_at=past))
    assert compute_next_run(t) is None


# -- store ---------------------------------------------------------------------
def test_store_crud_and_due(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    t = _task(
        schedule=Schedule(kind="cron", cron="* * * * *")
    )  # every minute → due soon
    store.save(t)
    assert store.get(t.id).title == "Daily brief"
    assert [x.id for x in store.list()] == [t.id]
    # next_run computed + due() finds it once we're past next_run
    due = store.due(now=t.next_run + 1)
    assert [x.id for x in due] == [t.id]
    # disabled tasks are not due
    t.enabled = False
    store.save(t)
    assert store.due(now=t.next_run + 1 if t.next_run else 9e9) == []
    assert store.delete(t.id) is True and store.get(t.id) is None


def test_store_runs_history(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    t = _task()
    store.save(t)
    store.add_run(TaskRun(task_id=t.id, status="ok", result_text="hi"))
    store.add_run(TaskRun(task_id=t.id, status="error", error="boom"))
    runs = store.runs(t.id)
    assert len(runs) == 2 and runs[0].status in ("ok", "error")


def test_store_tombstone_rejects_stale_task_and_run_writes(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    task = _task()
    store.save(task)
    stale_task = store.get(task.id)
    stale_run = TaskRun(task_id=task.id)

    assert store.delete(task.id) is True
    assert store.save(stale_task) is None
    assert store.add_run(stale_run) is None
    assert store.get(task.id) is None
    assert store.runs(task.id, limit=None) == []

    # Unknown deletes are not tombstoned: a genuinely new task with that ID
    # remains creatable.
    new_task = _task()
    assert store.delete(new_task.id) is False
    assert store.save(new_task) is new_task


# -- scheduler loop ------------------------------------------------------------
async def test_scheduler_runs_due_task_and_advances(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    t = _task(schedule=Schedule(kind="cron", cron="* * * * *"))
    store.save(t)
    # force it due now
    t.next_run = 1.0
    store.save(t)
    t.next_run = 1.0  # save() recomputes; push it into the past again
    store._conn.execute("UPDATE scheduled_tasks SET next_run=1.0 WHERE id=?", (t.id,))
    store._conn.commit()

    ran: list[str] = []

    async def runner(task, trigger):
        ran.append(task.id)
        return TaskRun(task_id=task.id, status="ok", trigger=trigger)

    sched = Scheduler(store, runner, tick_seconds=0.05)
    sched.start()
    await asyncio.sleep(0.2)
    await sched.stop()
    assert ran == [t.id]
    advanced = store.get(t.id)
    assert advanced.run_count == 1 and advanced.last_status == "ok"
    assert (
        advanced.next_run is not None and advanced.next_run > 1.0
    )  # moved to the future


async def test_scheduler_skips_overlapping_run(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    t = _task()
    store.save(t)
    gate = asyncio.Event()
    started = 0

    async def slow_runner(task, trigger):
        nonlocal started
        started += 1
        await gate.wait()
        return TaskRun(task_id=task.id, status="ok")

    sched = Scheduler(store, slow_runner)
    first = asyncio.create_task(sched.run_task(t, trigger="manual"))
    await asyncio.sleep(0.02)
    second = await sched.run_task(t, trigger="manual")  # overlaps → skipped
    assert second is None and started == 1
    gate.set()
    await first


def test_scheduler_finalization_cannot_resurrect_deleted_task(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "auto.db")
    task = _task()
    store.save(task)
    write_entered = threading.Event()
    release_write = threading.Event()
    errors = []
    original_save = store.save
    scheduler_thread = None

    async def runner(task, trigger):
        return TaskRun(task_id=task.id, status="ok", trigger=trigger)

    scheduler = Scheduler(store, runner)

    def blocked_save(stale_task):
        if threading.current_thread() is scheduler_thread:
            write_entered.set()
            assert release_write.wait(timeout=5)
        return original_save(stale_task)

    monkeypatch.setattr(store, "save", blocked_save)

    def finalize():
        try:
            asyncio.run(scheduler.run_task(task, trigger="scheduled"))
        except BaseException as exc:
            errors.append(exc)

    scheduler_thread = threading.Thread(target=finalize)
    scheduler_thread.start()
    assert write_entered.wait(timeout=5)
    assert store.delete(task.id) is True
    release_write.set()
    scheduler_thread.join(timeout=5)

    assert not scheduler_thread.is_alive()
    assert errors == []
    assert store.get(task.id) is None
    assert store.runs(task.id, limit=None) == []


# -- agent-facing tools --------------------------------------------------------
def test_create_and_list_tools(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    origin = {
        "surface": "cowork",
        "session_id": "s1",
        "workspace": "/tmp/ws",
        "agent": "cowork",
    }
    tools = {
        t.__name__: t
        for t in scheduling_tools(store, origin=origin, default_workspace="/tmp/ws")
    }

    out = tools["create_scheduled_task"](
        title="Brief", instructions="brief me", cron="10 19 * * *"
    )
    assert out["ok"] and out["schedule"] == "Every day at ~7:10 PM"
    # create surfaces a confirm card → gated
    assert (
        tools["create_scheduled_task"].__aisuite_tool_metadata__.requires_approval
        is True
    )

    listed = tools["list_scheduled_tasks"]()["tasks"]
    assert (
        len(listed) == 1
        and listed[0]["origin_session_id" if False else "title"] == "Brief"
    )
    saved = store.list()[0]
    assert saved.origin_session_id == "s1" and saved.workspace == "/tmp/ws"

    bad = tools["create_scheduled_task"](title="x", instructions="y", cron="not-a-cron")
    assert "invalid cron" in bad["error"]
    none = tools["create_scheduled_task"](title="x", instructions="y")
    assert "error" in none  # neither cron nor fire_at


def test_update_and_delete_tools(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    tools = {
        t.__name__: t
        for t in scheduling_tools(
            store, origin={"workspace": "/tmp/ws"}, default_workspace="/tmp/ws"
        )
    }
    tid = tools["create_scheduled_task"](
        title="X", instructions="do", cron="0 9 * * *"
    )["id"]
    assert (
        tools["update_scheduled_task"](id=tid, enabled=False)["task"]["enabled"]
        is False
    )
    assert store.get(tid).next_run is None  # disabled → no next run
    assert tools["delete_scheduled_task"](id=tid)["ok"] is True
    assert tools["update_scheduled_task"](id=tid)["error"]


def test_agent_update_cannot_resurrect_task_deleted_after_lookup(
    tmp_path, monkeypatch
):
    store = TaskStore(tmp_path / "auto.db")
    task = _task()
    store.save(task)
    tools = {
        tool.__name__: tool
        for tool in scheduling_tools(
            store,
            origin={"workspace": "/tmp/ws"},
            default_workspace="/tmp/ws",
        )
    }
    write_entered = threading.Event()
    release_write = threading.Event()
    results = []
    errors = []
    original_save = store.save
    update_thread = None

    def blocked_save(stale_task):
        if threading.current_thread() is update_thread:
            write_entered.set()
            assert release_write.wait(timeout=5)
        return original_save(stale_task)

    monkeypatch.setattr(store, "save", blocked_save)

    def update():
        try:
            results.append(
                tools["update_scheduled_task"](id=task.id, title="stale update")
            )
        except BaseException as exc:
            errors.append(exc)

    update_thread = threading.Thread(target=update)
    update_thread.start()
    assert write_entered.wait(timeout=5)
    assert store.delete(task.id) is True
    release_write.set()
    update_thread.join(timeout=5)

    assert not update_thread.is_alive()
    assert errors == []
    assert results == [{"error": f"no such task: {task.id}"}]
    assert store.get(task.id) is None


def test_delete_tool_can_delegate_lifecycle_cleanup(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    deleted = []
    tools = {
        tool.__name__: tool
        for tool in scheduling_tools(
            store,
            origin={"workspace": "/tmp/ws"},
            default_workspace="/tmp/ws",
            delete_task=lambda task_id: deleted.append(task_id) is None,
        )
    }

    result = tools["delete_scheduled_task"](id="task-id")

    assert result == {"ok": True, "id": "task-id"}
    assert deleted == ["task-id"]


def test_manager_delete_automation_removes_run_sessions_and_outputs(tmp_path):
    from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from coworker.server.manager import SessionManager
    from coworker.sessions import SessionRecord

    class Provider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return AssistantTurn(text="done", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    manager = SessionManager(data_dir=tmp_path / "state", provider=Provider())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = _task()
    task.workspace = str(workspace)
    manager.task_store.save(task)
    run = TaskRun(task_id=task.id, status="ok")
    manager.task_store.add_run(run)
    manager.session_store.save(
        SessionRecord(
            session_id=run.session_id,
            workspace=str(workspace),
            model="m",
            mode="interactive",
        )
    )
    retained = manager.tool_output_store(run.session_id)
    retained.put("call", "tool", "retained")

    result = manager.delete_automation(task.id)

    assert result["ok"] is True
    assert manager.task_store.get(task.id) is None
    assert manager.task_store.runs(task.id, limit=None) == []
    assert manager.session_store.exists(run.session_id) is False
    assert not retained.directory.exists()


async def test_deleting_running_automation_cannot_resurrect_run_or_outputs(tmp_path):
    from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from coworker.server.manager import SessionManager

    started = threading.Event()
    release = threading.Event()

    class BlockingProvider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            started.set()
            assert release.wait(timeout=5)
            return AssistantTurn(text="done", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    manager = SessionManager(
        data_dir=tmp_path / "state",
        provider=BlockingProvider(),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = _task()
    task.workspace = str(workspace)
    manager.task_store.save(task)

    running = asyncio.create_task(manager._run_scheduled_task(task, "scheduled"))
    assert await asyncio.to_thread(started.wait, 5)
    run = manager.task_store.runs(task.id, limit=None)[0]
    retained_directory = manager.tool_output_store(run.session_id).directory

    assert manager.delete_automation(task.id)["ok"] is True
    release.set()
    await running

    assert manager.task_store.get(task.id) is None
    assert manager.task_store.runs(task.id, limit=None) == []
    assert manager.session_store.exists(run.session_id) is False
    assert not retained_directory.exists()
    assert run.session_id not in manager._engines


@pytest.mark.parametrize(
    "operation_name",
    ["mark_seen", "update", "prepare_manual_run", "finalize_manual_run"],
)
def test_automation_mutations_serialize_with_deletion(
    tmp_path, monkeypatch, operation_name
):
    from coworker.server.manager import SessionManager
    from coworker.sessions import SessionRecord

    manager = SessionManager(data_dir=tmp_path / operation_name)
    workspace = tmp_path / f"{operation_name}-workspace"
    workspace.mkdir()
    task = _task(workspace=str(workspace), agent="cowork")
    manager.task_store.save(task)

    run = None
    if operation_name == "finalize_manual_run":
        run = TaskRun(task_id=task.id, trigger="manual")
        manager.task_store.add_run(run)
        manager.session_store.save(
            SessionRecord(
                session_id=run.session_id,
                workspace=str(workspace),
                model="m",
                mode="interactive",
                messages=[{"role": "assistant", "content": "done"}],
            )
        )

    if operation_name == "mark_seen":
        def operation():
            return manager.mark_automation_seen(task.id)

        write_name = "save"
    elif operation_name == "update":
        def operation():
            return manager.update_automation(task.id, {"title": "updated"})

        write_name = "save"
    elif operation_name == "prepare_manual_run":
        def operation():
            return manager.prepare_manual_run(task.id)

        write_name = "add_run"
    else:
        assert run is not None
        def operation():
            return manager.finalize_manual_run(task.id, run.run_id)

        write_name = "add_run"

    write_entered = threading.Event()
    release_write = threading.Event()
    delete_started = threading.Event()
    delete_done = threading.Event()
    operation_results = []
    delete_results = []
    errors = []
    original_write = getattr(manager.task_store, write_name)
    operation_thread = None

    def blocked_write(*args, **kwargs):
        if threading.current_thread() is operation_thread:
            write_entered.set()
            assert release_write.wait(timeout=5)
        return original_write(*args, **kwargs)

    monkeypatch.setattr(manager.task_store, write_name, blocked_write)

    def run_operation():
        try:
            operation_results.append(operation())
        except BaseException as exc:
            errors.append(exc)

    def delete():
        delete_started.set()
        try:
            delete_results.append(manager.delete_automation(task.id))
        except BaseException as exc:
            errors.append(exc)
        finally:
            delete_done.set()

    operation_thread = threading.Thread(target=run_operation)
    delete_thread = threading.Thread(target=delete)
    operation_thread.start()
    assert write_entered.wait(timeout=5)

    lifecycle_lock_was_free = manager._session_lifecycle_lock.acquire(
        blocking=False
    )
    if lifecycle_lock_was_free:
        manager._session_lifecycle_lock.release()

    delete_thread.start()
    assert delete_started.wait(timeout=5)
    delete_interleaved = delete_done.wait(timeout=0.05)
    release_write.set()
    operation_thread.join(timeout=5)
    delete_thread.join(timeout=5)

    assert lifecycle_lock_was_free is False
    assert delete_interleaved is False
    assert not operation_thread.is_alive()
    assert not delete_thread.is_alive()
    assert errors == []
    assert operation_results[0]["ok"] is True
    assert delete_results == [{"ok": True, "id": task.id}]
    assert manager.task_store.get(task.id) is None
    assert manager.task_store.runs(task.id, limit=None) == []


# -- run persists as a continuable session -------------------------------------
async def test_scheduled_run_persists_continuable_session(tmp_path, monkeypatch):
    from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from coworker.server.manager import SessionManager, _last_assistant_text

    class ScriptedProvider(ProviderClient):
        def __init__(self, turns):
            self._turns = list(turns)

        def complete(self, *, model, messages, tools=None, **settings):
            return self._turns.pop(0)

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    ws = tmp_path / "ws"
    ws.mkdir()
    # two turns: the scheduled run, then a follow-up question
    provider = ScriptedProvider(
        [
            AssistantTurn(text="Daily brief: all quiet.", finish_reason="stop"),
            AssistantTurn(text="Sure — here is more detail.", finish_reason="stop"),
        ]
    )
    manager = SessionManager(data_dir=tmp_path / "data", provider=provider)
    task = _task(workspace=str(ws), agent="cowork")
    manager.task_store.save(task)

    run = await manager._run_scheduled_task(task, trigger="manual")
    assert run.status == "ok" and run.session_id == f"__run__{run.run_id}"
    assert run.result_text == "Daily brief: all quiet."

    # the run is now a real, reopenable session with the transcript
    record = manager.session_store.load(run.session_id)
    assert (
        record is not None
        and record.workspace
        and any("Scheduled run" in (m.get("content") or "") for m in record.messages)
    )
    # …and it is continuable: a follow-up turn reuses the same thread
    engine = manager.get_engine(run.session_id, workspace=str(ws), agent="cowork")
    async for _ in engine.run("tell me more"):
        pass
    assert _last_assistant_text(engine.messages) == "Sure — here is more detail."


def test_task_engine_has_no_scheduling_tools(tmp_path, monkeypatch):
    """A scheduled run executes its instructions — it must not be able to (re)schedule. With
    instructions like 'every day at 5:32pm, prepare…', an agent holding create_scheduled_task
    creates another automation instead of doing the task."""
    from coworker.providers import (
        AssistantTurn as _AT,
        ModelCapabilities,
        ProviderClient,
    )
    from coworker.server import SessionManager

    class _Provider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return _AT(text="ok", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    ws = tmp_path / "ws"
    ws.mkdir()
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    task = _task(workspace=str(ws), agent="cowork")
    manager.task_store.save(task)

    engine = manager._build_task_engine(task, session_id="__run__test")
    names = set(engine.registry.names())
    assert "create_scheduled_task" not in names
    assert "update_scheduled_task" not in names
    assert "write_file" in names  # the deliverable tools are still there


async def test_manual_run_prepare_and_finalize(tmp_path, monkeypatch):
    from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from coworker.server.manager import SessionManager

    class ScriptedProvider(ProviderClient):
        def __init__(self, turns):
            self._turns = list(turns)

        def complete(self, *, model, messages, tools=None, **settings):
            return self._turns.pop(0)

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    ws = tmp_path / "ws"
    ws.mkdir()
    manager = SessionManager(
        data_dir=tmp_path / "data",
        provider=ScriptedProvider(
            [AssistantTurn(text="Done — briefing ready.", finish_reason="stop")]
        ),
    )
    task = _task(workspace=str(ws), agent="cowork")
    manager.task_store.save(task)

    # prepare: a "running" run + a session to open live (NOT executed yet)
    prep = manager.prepare_manual_run(task.id)
    assert prep["ok"] and prep["session_id"] == f"__run__{prep['run_id']}"
    # The prompt wraps the instructions in execute-now framing (so the live agent runs the task
    # instead of re-scheduling it) and carries them verbatim.
    assert prep["agent"] == "cowork"
    assert task.instructions in prep["prompt"]
    assert "do not create or modify any scheduled tasks" in prep["prompt"]
    assert manager.task_store.runs(task.id)[0].status == "running"

    # the GUI drives the run live over the session, then finalize records the outcome
    engine = manager.get_engine(prep["session_id"], workspace=str(ws), agent="cowork")
    async for _ in engine.run(prep["prompt"]):
        pass
    manager.save(prep["session_id"], engine)

    out = manager.finalize_manual_run(task.id, prep["run_id"])
    assert out["ok"] and out["run"]["status"] == "ok"
    assert out["run"]["result_text"] == "Done — briefing ready."
    assert manager.task_store.get(task.id).run_count == 1


# -- REST ----------------------------------------------------------------------
def test_automations_rest(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    # seed a task directly via the store
    t = _task(workspace=str(tmp_path / "ws"))
    manager.task_store.save(t)
    client = TestClient(create_app(manager))

    tasks = client.get("/v1/automations").json()["tasks"]
    assert (
        tasks[0]["title"] == "Daily brief"
        and tasks[0]["schedule"] == "Every day at ~7:10 PM"
    )
    assert (
        client.patch(f"/v1/automations/{t.id}", json={"enabled": False}).json()["task"][
            "enabled"
        ]
        is False
    )
    assert client.get(f"/v1/automations/{t.id}").json()["task"]["id"] == t.id
    assert client.delete(f"/v1/automations/{t.id}").json()["ok"] is True


# -- unseen-run tracking (UX-023 sidebar badges) --------------------------------
def test_unseen_runs_counted_and_cleared_by_mark_seen(tmp_path, monkeypatch):
    """list_automations surfaces unseen counts (runs after the seen mark), with
    unseen_failed keyed to the NEWEST unseen run; mark_automation_seen clears them
    and later runs count fresh."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker.server.manager import SessionManager

    manager = SessionManager(data_dir=tmp_path / "data")
    t = manager.task_store.save(_task())
    manager.task_store.add_run(TaskRun(task_id=t.id, status="ok"))
    manager.task_store.add_run(TaskRun(task_id=t.id, status="error"))

    row = manager.list_automations()["tasks"][0]
    assert row["unseen_runs"] == 2
    assert row["unseen_failed"] is True  # newest unseen run errored

    assert manager.mark_automation_seen(t.id)["ok"]
    row = manager.list_automations()["tasks"][0]
    assert row["unseen_runs"] == 0 and row["unseen_failed"] is False

    time.sleep(0.01)  # a run strictly after the seen mark
    manager.task_store.add_run(TaskRun(task_id=t.id, status="ok"))
    row = manager.list_automations()["tasks"][0]
    assert row["unseen_runs"] == 1 and row["unseen_failed"] is False

    assert not manager.mark_automation_seen("task-nope")["ok"]


@pytest.mark.asyncio
async def test_scheduled_run_broadcasts_run_started_event(tmp_path, monkeypatch):
    """UX-026: the moment a scheduled run starts, every /ws/events socket hears
    automation_run_started (the top-right toast). Dead sockets drop silently."""
    from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from coworker.server.manager import SessionManager

    class ScriptedProvider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return AssistantTurn(text="done", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    ws = tmp_path / "ws"
    ws.mkdir()
    manager = SessionManager(data_dir=tmp_path / "data", provider=ScriptedProvider())
    task = _task(workspace=str(ws), agent="cowork")
    manager.task_store.save(task)

    heard: list = []

    async def listener(message):
        heard.append(message)

    async def dead(message):
        raise RuntimeError("socket gone")

    manager.register_event_client(listener)
    manager.register_event_client(dead)
    run = await manager._run_scheduled_task(task, trigger="schedule")

    (event,) = [m for m in heard if m["type"] == "automation_run_started"]
    assert event["data"]["task_id"] == task.id
    assert event["data"]["task_title"] == task.title
    assert event["data"]["session_id"] == run.session_id
    assert event["data"]["trigger"] == "schedule"
    assert dead not in manager._event_clients  # dropped, not fatal
