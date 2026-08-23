"""A run that dies with its process must say so.

The gap this covers, from a real incident: the server was restarted while a session was
19 tool-steps into a turn. The model's reply was 85 seconds into generation and went
nowhere; the transcript simply ended at the last tool result, with no marker, no way to
tell it apart from an agent that decided to stop, and nothing to resume from.
"""

import asyncio
import os
import threading

import pytest

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


def _dead_pid() -> int:
    """A pid that is definitely not running — what a claim row left by a process that died
    looks like. The reap spares rows whose owner is still alive, so a test that means "this
    process is gone" has to actually say so rather than stamp its own pid."""
    pid = 999999
    while pid < 4_000_000:
        try:
            os.kill(pid, 0)
        except PermissionError:
            pass  # it exists, it just isn't ours
        except OSError:
            return pid
        pid += 1
    raise AssertionError("no free pid to stand in for a dead process")


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
    mgr.session_store.mark_running(sid, pid=_dead_pid(), label="cowork")
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
    mgr.session_store.mark_running("dead", pid=_dead_pid())  # a second crash, nothing new to close
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
    mgr.session_store.mark_running("never-existed", pid=_dead_pid())
    assert mgr.reap_interrupted_runs() == 1  # the ghost row is skipped, the real one is closed
    assert mgr.session_store.running_turns() == []


# -- scheduled runs --------------------------------------------------------------------


def _scheduled(tmp_path, provider):
    """A real scheduled task, driven through the real path — the claim has to be made by
    `_run_scheduled_task` itself, not by the test."""
    from coworker.automation import Schedule, ScheduledTask

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    mgr = SessionManager(data_dir=tmp_path / "data", provider=provider)
    task = ScheduledTask(
        title="Daily brief",
        instructions="brief me",
        schedule=Schedule(kind="cron", cron="10 19 * * *"),
        workspace=str(ws),
        agent="cowork",
    )
    mgr.task_store.save(task)
    return mgr, task


def test_a_scheduled_run_claims_the_turn_while_it_works(tmp_path):
    """The gap this closes: `mark_running` is wired into the WebSocket path and the Inbox
    resume path, but `_run_scheduled_task` called engine.run() directly — so four automations
    could be mid-flight with nothing on disk saying so, and a restart would leave every one of
    them reading as "still working" forever."""
    seen = {}

    class Recording(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            seen["during"] = box["mgr"].session_store.running_turns()
            return AssistantTurn(text="all quiet.", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    box = {}
    mgr, task = _scheduled(tmp_path, Recording())
    box["mgr"] = mgr

    run = asyncio.run(mgr._run_scheduled_task(task, trigger="manual"))

    assert run.status == "ok"
    assert [r["session_id"] for r in seen["during"]] == [run.session_id]
    assert mgr.session_store.running_turns() == []  # and released when it finished


def test_a_scheduled_run_cancelled_on_shutdown_does_not_stay_running(tmp_path):
    """Graceful shutdown cancels the task, so its `finally` DOES run: the row is accounted
    for here rather than left for the reap. What must not happen is what used to — the run
    keeping `status: "running"` with a finished_at beside it."""
    started, release = threading.Event(), threading.Event()

    class Blocking(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            started.set()
            release.wait(timeout=10)  # still "generating" when the process goes down
            return AssistantTurn(text="too late", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    mgr, task = _scheduled(tmp_path, Blocking())

    async def scenario():
        job = asyncio.create_task(mgr._run_scheduled_task(task, trigger="manual"))
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(0.02)
        assert started.is_set(), "the run never reached the model"
        assert len(mgr.session_store.running_turns()) == 1  # claimed while it works
        job.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await job

    asyncio.run(scenario())

    (row,) = mgr.task_store.runs(task.id)
    assert row.status == "incomplete" and "interrupted" in (row.error or "")
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
    # A next boot is a different process: re-stamp the claim as one nobody is running any
    # more, or the reap correctly leaves it to the (still very much alive) owner.
    mgr.session_store.mark_running(sid, pid=_dead_pid())
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

    stopped = threading.Event()

    class EndlessStream(ProviderClient):
        def complete(self, **kw):
            raise NotImplementedError

        def capabilities(self, model):
            return ModelCapabilities()

        def stream(self, *, model, messages, tools=None, **settings):
            yield StreamChunk(text_delta="w0 ")
            # Still generating when the shutdown lands. Without the handshake the fake
            # stream can run to completion in its producer thread before the event loop
            # delivers the first delta, and then the turn simply finishes — no interrupt to
            # assert on (a 1-in-150 flake).
            stopped.wait(timeout=5)
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
                stopped.set()

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


# -- the reap must not step on a live instance -----------------------------------------


def test_the_reap_leaves_another_live_instances_turn_alone(tmp_path):
    """Two instances over one state dir is a supported configuration: the desktop shell runs
    its sidecar on a random free port so it can coexist with a hand-run server on 8765, and
    both are pinned to the same `state_dir()`. The second one's startup reap used to close out
    the first one's LIVE turn — and then the corruption: `save` appends by count and knows
    nothing about `append_messages`, so the live engine's next save dropped exactly as many
    real messages as the reap had added."""
    mgr = _mgr(tmp_path)
    sid = "live-elsewhere"
    engine = mgr.get_engine(sid, agent="cowork", workspace=str(tmp_path))
    engine.messages.extend(
        [
            {"role": "user", "content": "go"},
            {"role": "assistant", "tool_calls": [{"id": "t9", "function": {"name": "shell"}}]},
        ]
    )
    mgr.save(sid, engine)
    # pid 1 stands in for the other instance: always running, and never us.
    mgr.session_store.mark_running(sid, pid=1, label="builder")
    mgr._engines.clear()  # we are the OTHER process; its engines are not ours

    assert mgr.reap_interrupted_runs() == 0
    # …and the claim is left for its owner to clear, not spent by us.
    assert [r["session_id"] for r in mgr.session_store.running_turns()] == [sid]

    # The owner's turn then finishes normally and saves. Both real messages must survive.
    engine.messages.append({"role": "tool", "tool_call_id": "t9", "content": "the real result"})
    engine.messages.append({"role": "assistant", "content": "the real answer"})
    mgr.save(sid, engine)
    contents = [m.get("content") for m in mgr.session_store.load(sid).messages]
    assert "the real result" in contents and "the real answer" in contents
    assert not [m for m in mgr.session_store.load(sid).messages if m.get("role") == "notice"]


def test_a_claim_from_before_this_boot_is_dead_however_alive_its_pid_looks(tmp_path):
    """Pid recycling across a reboot: pid 1 is alive on every boot, so liveness alone would
    spare a row written by last week's process forever."""
    mgr = _dead_session(tmp_path, sid="from-last-boot")
    mgr._engines.clear()
    mgr.session_store.mark_running("from-last-boot", pid=1, label="cowork")
    with mgr.session_store._lock:  # stamp it as claimed before the machine came up
        mgr.session_store._conn.execute(
            "UPDATE running_turns SET started_at = 1 WHERE session_id = ?", ("from-last-boot",)
        )
        mgr.session_store._conn.commit()

    assert mgr.reap_interrupted_runs() == 1
    assert mgr.session_store.load("from-last-boot").messages[-1]["kind"] == "server_restart"


# -- the reap must not answer a prompt a human still owns ------------------------------


def _park_on_approval(mgr, sid, engine):
    """Drive a turn until it parks on an Inbox approval, then kill it the way a SIGKILL does:
    engine gone, but the durable claim still on disk."""

    async def first():
        async for event in engine.run("go"):
            if event.type.value in ("turn_start", "permission_required"):
                mgr.save(sid, engine)

    task = asyncio.create_task(first())
    return task


def test_the_reap_does_not_answer_a_prompt_parked_in_the_inbox(tmp_path):
    """The regression durable resume cannot survive: a turn suspended on an Inbox approval
    ends on an unanswered tool call too, so the reap answered it — and that answer is the one
    `TurnEngine.resume()` re-executes. With it already answered, `resume()` finds nothing
    pending and returns, while `resolve_inbox` has consumed the item: the user clicks Allow,
    the card disappears, and the approved tool never runs."""
    from coworker.providers import ToolCall

    target = tmp_path / "approved.txt"
    mgr = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(
            [
                AssistantTurn(
                    tool_calls=[
                        ToolCall(
                            id="call_w",
                            name="write_file",
                            arguments={"path": str(target), "content": "ok"},
                        )
                    ]
                ),
                AssistantTurn(text="Done — file written.", finish_reason="stop"),
            ]
        ),
    )
    sid = "parked"

    async def scenario():
        engine = mgr.get_engine(sid, agent="cowork", workspace=str(tmp_path))
        assert mgr.try_mark_running(sid)  # the WS path claims the turn durably
        task = _park_on_approval(mgr, sid, engine)
        pend = []
        for _ in range(200):
            await asyncio.sleep(0.02)
            pend = mgr.inbox.pending(sid)
            if pend:
                break
        assert pend, "the approval never reached the Inbox"
        # SIGKILL: the turn and the engine die, the claim row does not.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        mgr._engines.pop(sid, None)
        mgr._running_sessions.discard(sid)
        assert [r["session_id"] for r in mgr.session_store.running_turns()] == [sid]

        mgr.session_store.mark_running(sid, pid=_dead_pid())  # next boot
        assert mgr.reap_interrupted_runs() == 0
        # The pending card IS this thread's marker; the transcript must be untouched.
        assert not [m for m in mgr.session_store.load(sid).messages if m.get("role") == "tool"]

        await mgr.resolve_inbox(pend[0].id, "allow")

    asyncio.run(scenario())
    assert target.exists() and target.read_text() == "ok"
    texts = [m.get("content") for m in mgr.session_store.load(sid).messages]
    assert "Done — file written." in texts


# -- the automation half runs whatever the transcript half decides ---------------------


def test_a_scheduled_run_killed_before_its_first_save_still_leaves_running(tmp_path):
    """The SIGKILL/OOM case the durable row exists for. A scheduled run has no mid-turn
    checkpoint saves — its only `save` is in the `finally` — so at kill time there is a claim
    row and a TaskRun at "running" and NO session row at all. Bailing out on "no transcript to
    mark" skipped the automation half with it, and nothing could ever move that row again:
    the claim it needed was spent on the way past."""
    mgr = _mgr(tmp_path)
    run = TaskRun(task_id="task-1", run_id="r1")
    assert run.status == "running"
    mgr.task_store.add_run(run)
    mgr.session_store.mark_running("__run__r1", pid=_dead_pid(), label="cowork")
    assert mgr.session_store.load("__run__r1") is None  # never persisted a message

    assert mgr.reap_interrupted_runs() == 1

    after = mgr.task_store.find_run("r1")
    assert after.status == "incomplete" and after.finished_at
    assert "restarted" in (after.error or "")


def test_a_parked_scheduled_run_is_unstuck_even_though_its_thread_is_left_alone(tmp_path):
    """The two fixes have to compose: a run parked on an Inbox prompt keeps its transcript
    (so Allow still re-executes the tool) but must NOT keep `status: running` — the process
    that would have finished it is gone."""
    mgr = _dead_session(
        tmp_path,
        sid="__run__r2",
        messages=[
            {"role": "user", "content": "go"},
            {"role": "assistant", "tool_calls": [{"id": "t1", "function": {"name": "shell"}}]},
        ],
    )
    run = TaskRun(task_id="task-1", run_id="r2")
    mgr.task_store.add_run(run)
    item = mgr.inbox.add_approval(
        "__run__r2", "Run `shell`?", body="rm -rf", tool_call_id="t1"
    )
    assert item.state == "pending"
    mgr._engines.clear()

    mgr.reap_interrupted_runs()

    assert mgr.task_store.find_run("r2").status == "incomplete"
    assert not [m for m in mgr.session_store.load("__run__r2").messages if m.get("role") == "notice"]


# -- a graceful restart mid scheduled run ----------------------------------------------


def test_shutdown_reaches_a_scheduled_runs_engine(tmp_path):
    """A scheduled run is deliberately kept OUT of `_running_sessions`, so iterating that set
    alone walked straight past it: its engine was never told why it was dying."""
    started, release = threading.Event(), threading.Event()

    class Blocking(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            started.set()
            release.wait(timeout=10)
            return AssistantTurn(text="too late", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    mgr, task = _scheduled(tmp_path, Blocking())

    async def scenario():
        job = asyncio.create_task(mgr._run_scheduled_task(task, trigger="manual"))
        for _ in range(400):
            if started.is_set():
                break
            await asyncio.sleep(0.02)
        assert started.is_set(), "the run never reached the model"
        assert not mgr._running_sessions  # by design — the claim row is the only trace
        (sid,) = [r["session_id"] for r in mgr.session_store.running_turns()]

        assert await mgr.interrupt_running_sessions() == 1
        assert "restarted" in mgr._engines[sid]._shutdown_reason

        job.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await job

    asyncio.run(scenario())


def test_a_scheduled_run_cancelled_on_shutdown_leaves_a_marker_on_its_transcript(tmp_path):
    """`systemctl restart` mid-run: the engine is blocked in the model call when
    `scheduler.stop()` cancels the task, so it never reaches a cancel checkpoint and writes
    nothing. Reopening the run showed a thread that just stops — no marker, no explanation,
    and no Resume, because that tail is not retriable. The next boot's reap cannot cover for
    it either: the `finally` spends the claim row it would have needed."""
    started, release = threading.Event(), threading.Event()

    class Blocking(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            started.set()
            release.wait(timeout=10)
            return AssistantTurn(text="too late", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    mgr, task = _scheduled(tmp_path, Blocking())

    async def scenario():
        job = asyncio.create_task(mgr.scheduler.run_task(task, trigger="manual"))
        for _ in range(400):
            if started.is_set():
                break
            await asyncio.sleep(0.02)
        assert started.is_set(), "the run never reached the model"
        await mgr.interrupt_running_sessions()  # the real lifespan order…
        job.cancel()  # …then aclose() → scheduler.stop()
        release.set()
        try:
            await job
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())

    (row,) = mgr.task_store.runs(task.id)
    assert row.status == "incomplete"
    messages = mgr.session_store.load(row.session_id).messages
    tail = messages[-1]
    assert tail["role"] == "notice" and tail["kind"] == "server_restart"
    assert "restarted" in tail["text"]
    # …and exactly one marker, not one from the engine and one from the close-out.
    assert [m.get("kind") for m in messages if m.get("role") == "notice"] == ["server_restart"]


def test_a_scheduled_run_that_cannot_even_start_leaves_no_orphan_running_row(tmp_path):
    """The row was filed as "running" before anything that can raise had run — the workspace
    is created inside `_build_task_engine`, and a project folder that was deleted or has gone
    read-only raises there. The `finally` that owns the close-out is never entered and no
    durable claim exists yet, so neither it nor the reap can move the row: one permanent
    "running" orphan per tick, hidden behind the separate "error" row the scheduler files."""
    from coworker.automation import Schedule, ScheduledTask

    unwritable = tmp_path / "media"
    unwritable.mkdir()
    mgr = SessionManager(data_dir=tmp_path / "data", provider=ScriptedProvider([]))
    task = ScheduledTask(
        title="Daily brief",
        instructions="brief me",
        schedule=Schedule(kind="cron", cron="10 19 * * *"),
        workspace=str(unwritable / "gone"),
        agent="cowork",
    )
    mgr.task_store.save(task)
    unwritable.chmod(0o500)
    try:

        async def scenario():
            for _ in range(3):  # three ticks of a task that can never start
                await mgr.scheduler.run_task(task, trigger="schedule")

        asyncio.run(scenario())
    finally:
        unwritable.chmod(0o700)

    rows = mgr.task_store.runs(task.id)
    assert rows and all(r.status == "error" for r in rows)
    assert not [r for r in rows if r.status == "running"]


# -- the repair itself ------------------------------------------------------------------


def test_a_provider_that_renumbers_tool_call_ids_still_gets_its_repair():
    """Two shipped providers mint ids per RESPONSE, not per thread: gemini_provider restarts
    at `call_0` on every response and the OpenAI text-salvage path at `call_salvaged_0` (the
    route local/ollama models take). Matching the dead step's call against every `tool` row in
    the thread let an earlier step's result mask it, so the repair was skipped and the thread
    kept the dangling tool_call this function exists to remove — un-resumable, and un-continuable
    for any provider that enforces the pairing rule."""
    thread = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "tool_calls": [{"id": "call_0", "function": {"name": "read_file"}}]},
        {"role": "tool", "tool_call_id": "call_0", "content": "{}"},
        {"role": "assistant", "tool_calls": [{"id": "call_0", "function": {"name": "shell"}}]},
    ]
    out = closing_messages(thread, "the server restarted")
    assert _kinds(out) == ["tool", "server_restart"]
    assert out[0]["tool_call_id"] == "call_0"


def test_an_already_answered_call_is_not_answered_again():
    thread = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "x"}}, {"id": "b", "function": {"name": "y"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "{}"},
    ]
    out = closing_messages(thread, "r")
    assert _kinds(out) == ["tool", "server_restart"] and out[0]["tool_call_id"] == "b"


def test_a_model_switch_after_the_marker_does_not_reopen_the_thread():
    """Switching model is the supported step between a closed-out run and Resume, so it lands
    AFTER the closing marker — and reading only the last message made the thread look open
    again. Each restart then stacked another one: "restarted / model switched / restarted"."""
    thread = [
        {"role": "user", "content": "hi"},
        {"role": "notice", "kind": "server_restart", "text": "the server restarted"},
        {"role": "notice", "kind": "model_switch", "text": "Model switched to gpt-5.5"},
    ]
    assert closing_messages(thread, "r") == []


def test_a_finished_thread_a_model_switch_later_is_still_finished():
    thread = [
        {"role": "assistant", "content": "done"},
        {"role": "notice", "kind": "model_switch", "text": "Model switched to gpt-5.5"},
    ]
    assert closing_messages(thread, "r") == []


def test_the_reap_does_not_stack_a_second_marker_after_a_model_switch(tmp_path):
    mgr = _dead_session(
        tmp_path,
        sid="switched",
        messages=[
            {"role": "user", "content": "go"},
            {"role": "notice", "kind": "server_restart", "text": "the server restarted"},
            {"role": "notice", "kind": "model_switch", "text": "Model switched to gpt-5.5"},
        ],
    )
    mgr._engines.clear()
    assert mgr.reap_interrupted_runs() == 0
    kinds = [m.get("kind") for m in mgr.session_store.load("switched").messages if m["role"] == "notice"]
    assert kinds == ["server_restart", "model_switch"]


# -- whoever stopped the turn first owns the marker -------------------------------------


def test_a_user_stop_that_races_the_shutdown_is_still_a_user_stop(tmp_path):
    """The other order from `test_a_turn_cancelled_by_the_shutdown_...`: the user presses Stop
    and the shutdown hook lands a beat later. Branching on `_shutdown_reason` alone relabelled
    the deliberate stop "the agent server restarted" — and, since that tail IS retriable, put a
    Resume button on a run the user chose to end."""
    from coworker.engine import TurnEngine
    from coworker.events import EventType
    from coworker.permissions import PermissionEngine
    from coworker.providers import StreamChunk
    from coworker.tools import ToolRegistry

    stopped = threading.Event()

    class EndlessStream(ProviderClient):
        def complete(self, **kw):
            raise NotImplementedError

        def capabilities(self, model):
            return ModelCapabilities()

        def stream(self, *, model, messages, tools=None, **settings):
            yield StreamChunk(text_delta="w0 ")
            stopped.wait(timeout=5)  # still generating when the user hits Stop
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
                engine.request_interrupt()  # the user's Stop…
                # …and the shutdown hook, in the same event-loop slice — before the loop has
                # reached the cancel checkpoint that writes the marker.
                engine.mark_shutting_down("the server restarted")
                stopped.set()

    asyncio.run(run())
    assert [m["kind"] for m in engine.messages if m.get("role") == "notice"] == ["interrupted"]
    assert not engine._tail_is_retriable_error()  # so no Resume on a run the user ended


def test_a_new_turn_does_not_inherit_the_previous_shutdown_reason(tmp_path):
    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine
    from coworker.tools import ToolRegistry

    engine = TurnEngine(
        provider=ScriptedProvider([AssistantTurn(text="hi", finish_reason="stop")]),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )
    engine.mark_shutting_down("the server restarted")  # …which never actually happened

    async def scenario():
        async for _ in engine.run("go"):
            pass

    asyncio.run(scenario())
    assert engine._shutdown_reason == ""
    engine._append_interrupted()
    assert engine.messages[-1]["kind"] == "interrupted"
