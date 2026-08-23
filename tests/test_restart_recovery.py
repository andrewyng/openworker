"""A run that dies with its process must say so.

The gap this covers, from a real incident: the server was restarted while a session was
19 tool-steps into a turn. The model's reply was 85 seconds into generation and went
nowhere; the transcript simply ended at the last tool result, with no marker, no way to
tell it apart from an agent that decided to stop, and nothing to resume from.
"""

import asyncio

from coworker.conversations import ConversationStore
from coworker.engine import closing_messages
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient


# -- the helper ----------------------------------------------------------------


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


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
