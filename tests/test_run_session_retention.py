"""Run-session and prompt-waiter retention (#766).

Under sustained automation every run used to leave its engine (full message
history + sandbox) and its store-side saved-prefix copy in memory until the
session was deleted, and every resolved prompt left its waiter/loop entries
behind — growth with no cap. These pin the release points: a finished run lets
its engine go (the transcript is persisted; a follow-up rebuilds), dropping an
engine forgets the store's append-detection cache, and resolving an item pops
its waiters.
"""

from __future__ import annotations

import asyncio

from coworker.automation.models import Schedule, ScheduledTask
from coworker.conversations import SessionRecord
from coworker.inbox import InboxStore
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server.manager import SessionManager, _last_assistant_text


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _task(**kw) -> ScheduledTask:
    kw.setdefault("title", "Daily brief")
    kw.setdefault("instructions", "search the web and brief me")
    kw.setdefault("schedule", Schedule(kind="cron", cron="10 19 * * *"))
    kw.setdefault("workspace", "/tmp/cw-auto")
    return ScheduledTask(**kw)


def _manager(tmp_path, turns) -> SessionManager:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return SessionManager(data_dir=tmp_path / "data", provider=ScriptedProvider(turns))


def test_finished_run_releases_its_engine_but_keeps_the_thread(tmp_path):
    """The run's engine (messages + sandbox) must not outlive the run; the
    persisted transcript must still support a follow-up turn (#766)."""
    manager = _manager(
        tmp_path,
        [
            AssistantTurn(text="Daily brief: all quiet.", finish_reason="stop"),
            AssistantTurn(text="Sure — here is more detail.", finish_reason="stop"),
        ],
    )
    ws = tmp_path / "ws"
    task = _task(workspace=str(ws), agent="cowork")
    manager.task_store.save(task)

    run = asyncio.run(manager._run_scheduled_task(task, trigger="manual"))
    assert run.status == "ok"

    # released: the engine (history + sandbox) is gone, not parked until delete
    assert run.session_id not in manager._engines
    # …and its saved-prefix copy with it
    assert run.session_id not in manager.session_store._persisted

    # the thread survives on disk and a follow-up rebuilds from it
    record = manager.session_store.load(run.session_id)
    assert record is not None and any(
        "Scheduled run" in (m.get("content") or "") for m in record.messages
    )
    engine = manager.get_engine(run.session_id, workspace=str(ws), agent="cowork")
    assert engine is not None
    assert run.session_id in manager._engines  # rebuilt on demand
    asyncio.run(_drain(engine, "tell me more"))
    assert _last_assistant_text(engine.messages) == "Sure — here is more detail."


async def _drain(engine, prompt: str) -> None:
    async for _ in engine.run(prompt):
        pass


def test_mark_idle_releases_a_run_engine_but_not_a_normal_one(tmp_path, monkeypatch):
    """A follow-up turn inside a run session ends at mark_idle like any other; the
    run's engine is released there too, while a normal session keeps its engine
    for the instant-follow-up design (#766)."""
    manager = SessionManager(data_dir=tmp_path / "data")
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))

    class _Engine:
        sandbox_workspace = None
        messages: list = []  # autotitle's completion probe reads this

    manager._engines["__run__r1"] = _Engine()  # type: ignore[assignment]
    manager._engines["normal"] = _Engine()  # type: ignore[assignment]

    manager.mark_idle("__run__r1")
    assert "__run__r1" not in manager._engines
    assert "normal" in manager._engines

    manager.mark_idle("normal")
    assert "normal" in manager._engines  # ordinary sessions are untouched


def test_dropping_an_engine_forgets_the_saved_prefix_cache(tmp_path, monkeypatch):
    """`_persisted` is only an append-detection optimization; it must not keep a
    dropped session's messages alive in memory (#766)."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    record = SessionRecord(
        session_id="s1",
        workspace=str(tmp_path),
        model="m",
        mode="ask",
        messages=[{"role": "user", "content": "hi"}],
    )
    manager.session_store.save(record)
    assert "s1" in manager.session_store._persisted

    manager._drop_engine("s1")  # no live engine — the cache still must go
    assert "s1" not in manager.session_store._persisted
    # and a later save still works (falls back to comparing the file)
    manager.session_store.save(record)
    assert "s1" in manager.session_store._persisted


def test_resolving_an_item_pops_its_waiters(tmp_path):
    """Every resolved prompt used to leave its Event and loop reference in the
    store forever (#766)."""
    store = InboxStore(path=str(tmp_path / "inbox.json"))
    item = store.add_approval("s1", "Run it?")

    async def scenario() -> str:
        waiter = asyncio.create_task(store.wait(item.id))
        await asyncio.sleep(0.01)  # let wait() register
        assert item.id in store._waiters
        assert store.resolve(item.id, "allow")
        return await waiter

    assert asyncio.run(scenario()) == "allow"
    assert item.id not in store._waiters
    assert item.id not in store._waiter_loops


def test_resolving_pops_waiters_even_if_the_waiter_was_cancelled(tmp_path):
    """A cancelled wait leaves its entry behind until the item resolves — resolve
    must still pop it, not find nothing and skip (#766)."""
    store = InboxStore(path=str(tmp_path / "inbox.json"))
    item = store.add_approval("s1", "Run it?")

    async def scenario() -> None:
        waiter = asyncio.create_task(store.wait(item.id))
        await asyncio.sleep(0.01)
        waiter.cancel()
        try:
            await waiter
        except asyncio.CancelledError:
            pass
        assert item.id in store._waiters  # still registered after the cancel
        assert store.resolve(item.id, "deny")

    asyncio.run(scenario())
    assert item.id not in store._waiters
    assert item.id not in store._waiter_loops
