"""Phase 1 and 2 problems found by the Phase 3 design (observability/FIX-LIST.md, P3-n):
P3-3 a manual run is closed when its turn ends, and at start; P3-4 Issues are re-checked at
start; P3-5 `usage_reported` is a model step's column only; P3-6 "today" is the viewer's day;
P3-7 a model call with no purpose is "unattributed"."""

from __future__ import annotations

import importlib

from coworker.observability import calculator, read_api
from coworker.observability.store import _step_row
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient

auto = importlib.import_module("test_automation")


class _Scripted(ProviderClient):
    def __init__(self, n=4):
        self._turns = [AssistantTurn(text=f"Done {i}.", finish_reason="stop") for i in range(n)]

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path, monkeypatch):
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return SessionManager(data_dir=tmp_path / "data", provider=_Scripted()), ws


async def _manual_turn(manager, ws):
    task = auto._task(workspace=str(ws), agent="cowork")
    manager.task_store.save(task)
    prep = manager.prepare_manual_run(task.id)
    engine = manager.get_engine(prep["session_id"], workspace=str(ws), agent="cowork")
    async for _ in engine.run(prep["prompt"]):
        pass
    return task, prep


async def test_a_manual_run_is_closed_when_its_turn_ends(tmp_path, monkeypatch):
    manager, ws = _manager(tmp_path, monkeypatch)
    task, prep = await _manual_turn(manager, ws)
    assert manager.task_store.runs(task.id)[0].status == "running"
    # No window asks for it: the turn's end is enough.
    await manager.broadcast_session(prep["session_id"], {"type": "turn_done", "data": {}})
    run = manager.task_store.runs(task.id)[0]
    assert run.status == "ok" and run.result_text == "Done 0."
    assert manager.task_store.get(task.id).run_count == 1
    # The desktop's own finalize, arriving later, changes nothing.
    manager.finalize_manual_run(task.id, prep["run_id"])
    assert manager.task_store.get(task.id).run_count == 1


async def test_stale_manual_runs_are_closed_at_start(tmp_path, monkeypatch):
    manager, ws = _manager(tmp_path, monkeypatch)
    done, _ = await _manual_turn(manager, ws)
    cut, cut_prep = await _manual_turn(manager, ws)
    waiting = auto._task(workspace=str(ws), agent="cowork")
    manager.task_store.save(waiting)
    manager.prepare_manual_run(waiting.id)  # opened, never run
    store = manager.recordings_store
    store.flush()
    with store._lock, store._conn:  # the app stopped mid-turn: start closed it as unknown
        store._conn.execute("UPDATE turns SET status = 'unknown' WHERE session_id = ?", (cut_prep["session_id"],))

    assert manager.finish_stale_manual_runs() == 2
    assert manager.task_store.runs(done.id)[0].status == "ok"
    failed = manager.task_store.runs(cut.id)[0]
    assert failed.status == "error" and failed.error
    assert manager.task_store.get(cut.id).last_status == "error"
    assert manager.task_store.runs(waiting.id)[0].status == "running"
    assert manager.finish_stale_manual_runs() == 0


async def test_issues_are_rechecked_at_start(tmp_path, monkeypatch):
    from coworker.observability import signals

    manager, ws = _manager(tmp_path, monkeypatch)
    engine = manager.get_engine("s1", workspace=str(ws))
    async for _ in engine.run("hello"):
        pass
    store = manager.recordings_store
    store.flush()
    version = signals.version()
    with store._lock, store._conn:  # a turn that ended before checks existed, or an older version
        store._conn.execute("UPDATE turns SET issues = NULL WHERE session_id = 's1'")
    assert store.sessions_needing_check(version) == ["s1"]

    assert manager.recheck_issues() == 1
    store.flush()
    [result] = store.read_issues("s1").values()
    assert result["signals_version"] == version
    assert result["seen"] is True  # an old turn's result never lights the sidebar dot
    assert store.sessions_needing_check(version) == []
    assert manager.recheck_issues() == 0


async def test_background_checks_run_in_a_thread(tmp_path, monkeypatch):
    manager, ws = _manager(tmp_path, monkeypatch)
    task, _ = await _manual_turn(manager, ws)
    t = manager.start_background_checks()
    t.join(timeout=10)
    assert not t.is_alive()
    assert manager.task_store.runs(task.id)[0].status == "ok"


def test_usage_reported_is_a_model_steps_column_only():
    cols = __import__("coworker.observability.store", fromlist=["_STEP_COLUMNS"])._STEP_COLUMNS
    at = cols.index("usage_reported")
    model = {"step_id": "m", "kind": "model", "attrs": {"usage": {"reported": True, "input_fresh": 5, "output": 1}}}
    check = {"step_id": "a", "kind": "auto_approver", "attrs": {"usage_reported": False}}
    assert _step_row(model)[at] == 1
    assert _step_row(check)[at] is None  # it read as a call of 0 tokens to any sum over the column


def test_a_model_call_with_no_purpose_is_unattributed():
    u = {"reported": True, "input_fresh": 10, "cache_read": 0, "cache_write": 0, "output": 2}
    rec = {"turns": [{"turn_id": "t", "status": "ok"}],
           "steps": [{"step_id": "m", "turn_id": "t", "kind": "model", "status": "ok",
                      "attrs": {"model": "model-a", "usage": u}}]}
    assert calculator.usage(rec)["by_purpose"] == {"unattributed": 12}
    assert list(read_api.usage(rec, "s")["by_model_parts"]) == ["model-a (unattributed)"]


def test_today_is_the_viewers_day(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch)
    obs = manager.observability
    seen = []
    monkeypatch.setattr(obs.store, "outside_calls", lambda since_ms: seen.append(since_ms) or {"calls": 0, "tokens": 0})
    obs.status(day_start_ms=1_700_000_000_000)
    obs.status()  # no viewer's day: this machine's midnight
    obs.status(day_start_ms=-5)  # nonsense: the same
    assert seen[0] == 1_700_000_000_000
    assert seen[1] == seen[2] != seen[0]
