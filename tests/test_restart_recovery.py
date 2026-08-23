"""A run that dies with its process must say so.

The gap this covers, from a real incident: the server was restarted while a session was
19 tool-steps into a turn. The model's reply was 85 seconds into generation and went
nowhere; the transcript simply ended at the last tool result, with no marker, no way to
tell it apart from an agent that decided to stop, and nothing to resume from.
"""

import asyncio

from coworker.automation.models import TaskRun
from coworker.conversations import ConversationStore
from coworker.engine import closing_messages
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server.manager import SessionManager


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _mgr(tmp_path, turns=()):
    return SessionManager(workspace=tmp_path, provider=ScriptedProvider(list(turns)))


def _kinds(messages):
    return [m.get("kind") or m.get("role") for m in messages]


# -- the helper ------------------------------------------------------------------------


def test_a_thread_cut_off_mid_generation_gets_a_marker():
    # The incident's exact shape: the last thing to land was a tool result, and the reply
    # that would have followed it died with the process.
    thread = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "tool_calls": [{"id": "t1", "function": {"name": "read_file"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "{}"},
    ]
    out = closing_messages(thread, "the server restarted")
    assert _kinds(out) == ["server_restart"]


def test_an_unanswered_tool_call_is_closed_out_first():
    thread = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "tool_calls": [{"id": "t1", "function": {"name": "shell"}}]},
    ]
    out = closing_messages(thread, "the server restarted")
    # An assistant message whose tool_calls have no results is invalid input to most chat
    # templates — without this the thread could not be continued at all.
    assert _kinds(out) == ["tool", "server_restart"]
    assert out[0]["tool_call_id"] == "t1"


def test_a_finished_thread_is_left_alone():
    assert closing_messages([{"role": "assistant", "content": "done"}], "r") == []


def test_a_closed_thread_is_not_closed_twice():
    # Restarting the server five times while a session sits idle must not stack five markers.
    thread = [{"role": "user", "content": "hi"}, {"role": "notice", "kind": "server_restart"}]
    assert closing_messages(thread, "r") == []


def test_an_unanswered_user_message_counts_as_cut_off():
    assert _kinds(closing_messages([{"role": "user", "content": "go"}], "r")) == ["server_restart"]


# -- the durable marker ----------------------------------------------------------------


def test_the_store_remembers_which_turns_were_in_flight(tmp_path):
    store = ConversationStore(tmp_path)
    store.mark_running("s1", pid=4242, label="builder")
    (row,) = store.running_turns()
    assert row["session_id"] == "s1" and row["pid"] == 4242 and row["started_at"] > 0
    store.clear_running("s1")
    assert store.running_turns() == []


def test_marking_a_turn_running_survives_the_manager_that_did_it(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.get_engine("s-live", agent="cowork", workspace=str(tmp_path))
    assert mgr.try_mark_running("s-live")
    assert [r["session_id"] for r in mgr.session_store.running_turns()] == ["s-live"]
    mgr.mark_idle("s-live")
    assert mgr.session_store.running_turns() == []


# -- the reap --------------------------------------------------------------------------


def _dead_session(tmp_path, sid="dead", messages=None):
    """A session that was mid-turn when its process vanished: thread on disk, marker set,
    no live engine — exactly what the next process finds."""
    mgr = _mgr(tmp_path)
    engine = mgr.get_engine(sid, agent="cowork", workspace=str(tmp_path))
    engine.messages.extend(
        messages
        if messages is not None
        else [
            {"role": "user", "content": "go"},
            {"role": "assistant", "tool_calls": [{"id": "t1", "function": {"name": "read_file"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "{}"},
        ]
    )
    mgr.save(sid, engine)
    mgr.session_store.mark_running(sid, pid=999999, label="cowork")
    return mgr


def test_startup_marks_a_run_that_died_with_its_process(tmp_path):
    mgr = _dead_session(tmp_path)
    mgr._engines.clear()  # a fresh process has no engines

    assert mgr.reap_interrupted_runs() == 1

    tail = mgr.session_store.load("dead").messages[-1]
    assert tail["role"] == "notice" and tail["kind"] == "server_restart"
    assert "restarted" in tail["text"]
    assert mgr.session_store.running_turns() == []  # and the marker is spent


def test_a_session_that_finished_normally_is_not_marked(tmp_path):
    mgr = _dead_session(
        tmp_path,
        messages=[{"role": "user", "content": "go"}, {"role": "assistant", "content": "done"}],
    )
    mgr._engines.clear()
    assert mgr.reap_interrupted_runs() == 0
    assert mgr.session_store.load("dead").messages[-1]["role"] == "assistant"
    assert mgr.session_store.running_turns() == []


def test_reaping_twice_marks_once(tmp_path):
    mgr = _dead_session(tmp_path)
    mgr._engines.clear()
    mgr.reap_interrupted_runs()
    mgr.session_store.mark_running("dead", pid=999999)  # a second crash, nothing new to close
    assert mgr.reap_interrupted_runs() == 0
    kinds = [m.get("kind") for m in mgr.session_store.load("dead").messages if m["role"] == "notice"]
    assert kinds == ["server_restart"]


def test_a_scheduled_run_does_not_sit_at_running_forever(tmp_path):
    mgr = _dead_session(tmp_path, sid="__run__r1")
    run = TaskRun(task_id="task-1", run_id="r1")
    assert run.status == "running"
    mgr.task_store.add_run(run)
    mgr._engines.clear()

    mgr.reap_interrupted_runs()

    after = mgr.task_store.find_run("r1")
    # Every surface — the Scheduled badge, the MCP health report, the freshness exporter —
    # reads "running" as still working.
    assert after.status == "incomplete"
    assert "restarted" in (after.error or "") and after.finished_at


def test_a_broken_row_does_not_stop_the_others(tmp_path):
    mgr = _dead_session(tmp_path)
    mgr._engines.clear()
    mgr.session_store.mark_running("never-existed", pid=999999)
    assert mgr.reap_interrupted_runs() == 1  # the ghost row is skipped, the real one is closed
    assert mgr.session_store.running_turns() == []


# -- shutdown --------------------------------------------------------------------------


def test_shutdown_tells_the_engine_why_and_leaves_the_marker_for_the_next_boot(tmp_path):
    """One writer. The engine's own cancel path writes the marker (so a partial answer lands
    before it, not between two of them); shutdown's job is to say WHY and to leave the durable
    row alone, because a loop that never runs again can only be cleaned up next boot."""
    mgr = _mgr(tmp_path)
    sid = "live"
    engine = mgr.get_engine(sid, agent="cowork", workspace=str(tmp_path))
    engine.messages.extend(
        [
            {"role": "user", "content": "go"},
            {"role": "assistant", "tool_calls": [{"id": "t1", "function": {"name": "shell"}}]},
        ]
    )
    mgr.mark_running(sid)
    seen = []

    async def collect(payload):
        seen.append(payload)

    mgr.register_session_client(sid, collect)

    assert asyncio.run(mgr.interrupt_running_sessions()) == 1

    assert engine._shutdown_reason and "restarted" in engine._shutdown_reason
    assert [p["type"] for p in seen] == ["run_interrupted"]
    # The row survives us: it is the only thing that can tell the next process this turn
    # never finished.
    assert [r["session_id"] for r in mgr.session_store.running_turns()] == [sid]


def test_a_turn_the_dying_loop_never_closed_is_closed_next_boot(tmp_path):
    """The two halves together, in the order the real incident happened: the server goes down
    mid-turn, the loop never gets another slice, and the next process finds the thread open."""
    mgr = _mgr(tmp_path)
    sid = "live"
    engine = mgr.get_engine(sid, agent="cowork", workspace=str(tmp_path))
    engine.messages.extend([{"role": "user", "content": "go"}])
    mgr.save(sid, engine)
    mgr.mark_running(sid)
    asyncio.run(mgr.interrupt_running_sessions())  # …and the process dies here

    mgr._engines.clear()  # next boot
    assert mgr.reap_interrupted_runs() == 1
    kinds = [m.get("kind") for m in mgr.session_store.load(sid).messages if m["role"] == "notice"]
    assert kinds == ["server_restart"]


def test_a_running_session_with_no_engine_is_closed_out_at_shutdown(tmp_path):
    mgr = _dead_session(tmp_path)  # thread + durable marker on disk
    mgr._engines.clear()
    mgr._running_sessions.add("dead")
    asyncio.run(mgr.interrupt_running_sessions())
    assert mgr.session_store.load("dead").messages[-1]["kind"] == "server_restart"


def test_shutdown_does_not_mark_a_session_that_is_merely_idle(tmp_path):
    mgr = _mgr(tmp_path)
    engine = mgr.get_engine("idle", agent="cowork", workspace=str(tmp_path))
    engine.messages.extend([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}])
    mgr.save("idle", engine)
    assert asyncio.run(mgr.interrupt_running_sessions()) == 0
    assert mgr.session_store.load("idle").messages[-1]["role"] == "assistant"


# -- the shutdown race -----------------------------------------------------------------


def test_a_turn_cancelled_by_the_shutdown_says_restart_not_interrupted(tmp_path):
    """The loop's own cancel path and the server's close-out both fire on the way down, and
    they race. Whoever wins, the thread must end with ONE marker — the resumable one."""
    from coworker.engine import TurnEngine
    from coworker.events import EventType
    from coworker.permissions import PermissionEngine
    from coworker.providers import StreamChunk
    from coworker.tools import ToolRegistry

    class EndlessStream(ProviderClient):
        def complete(self, **kw):
            raise NotImplementedError

        def capabilities(self, model):
            return ModelCapabilities()

        def stream(self, *, model, messages, tools=None, **settings):
            for i in range(200):
                yield StreamChunk(text_delta=f"w{i} ")
            yield StreamChunk(turn=AssistantTurn(text="full", finish_reason="stop"))

    engine = TurnEngine(
        provider=EndlessStream(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )

    async def run():
        async for ev in engine.run("go"):
            if ev.type == EventType.ASSISTANT_DELTA:
                engine.mark_shutting_down("the server restarted")
                engine.request_interrupt()

    asyncio.run(run())
    notices = [m for m in engine.messages if m.get("role") == "notice"]
    assert [n["kind"] for n in notices] == ["server_restart"]
    assert notices[0]["text"] == "the server restarted"


def test_a_second_closing_marker_is_dropped(tmp_path):
    """The other order: the server writes the restart marker, then the cancelled loop reaches
    its own. Two stacked markers would leave "Interrupted." as the tail — which is not
    resumable, so the Resume button would vanish on exactly the run that needs it."""
    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine
    from coworker.tools import ToolRegistry

    engine = TurnEngine(
        provider=ScriptedProvider([]),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )
    engine.messages.extend([{"role": "user", "content": "go"}])
    engine._append_notice("server_restart", "the server restarted")
    engine._append_interrupted()
    assert [m["kind"] for m in engine.messages if m["role"] == "notice"] == ["server_restart"]


def test_a_user_stop_is_still_a_user_stop(tmp_path):
    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine
    from coworker.tools import ToolRegistry

    engine = TurnEngine(
        provider=ScriptedProvider([]),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )
    engine.messages.append({"role": "user", "content": "go"})
    engine._append_interrupted()
    assert engine.messages[-1]["kind"] == "interrupted"


# -- resume ----------------------------------------------------------------------------


def test_the_user_can_resume_a_run_the_restart_cut_off(tmp_path):
    mgr = _dead_session(tmp_path)
    mgr._engines.clear()
    mgr.reap_interrupted_runs()

    # Same path the Resume button takes: rebuild from the persisted thread and retry.
    mgr.provider = ScriptedProvider([AssistantTurn(text="picked it back up", finish_reason="stop")])
    engine = mgr.get_engine("dead", agent="cowork", workspace=str(tmp_path))
    engine.provider = mgr.provider

    async def scenario():
        async for _ in engine.retry():
            pass

    asyncio.run(scenario())
    assert any(m.get("content") == "picked it back up" for m in engine.messages)
