"""A run that dies with its process must say so.

The gap this covers, from a real incident: the server was restarted while a session was
19 tool-steps into a turn. The model's reply was 85 seconds into generation and went
nowhere; the transcript simply ended at the last tool result, with no marker, no way to
tell it apart from an agent that decided to stop, and nothing to resume from.
"""

from coworker.conversations import ConversationStore


# -- the durable marker ----------------------------------------------------------------


def test_the_store_remembers_which_turns_were_in_flight(tmp_path):
    store = ConversationStore(tmp_path)
    store.mark_running("s1", pid=4242, label="builder")
    (row,) = store.running_turns()
    assert row["session_id"] == "s1" and row["pid"] == 4242 and row["started_at"] > 0
    store.clear_running("s1")
    assert store.running_turns() == []
