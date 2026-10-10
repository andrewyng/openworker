"""Regression tests for ConversationStore tool-call/result pairing repair.

When a turn is interrupted at the wrong moment, the append-only JSONL can end
up with a user message between an assistant ``tool_calls`` block and its
``tool`` result.  Providers reject this ordering (Anthropic 400/2013, OpenAI
"tool_call_ids did not have response messages"), making the session permanently
unrecoverable.

``ConversationStore._repair_tool_pairing`` reorders messages on load so every
tool result immediately follows its call, synthesising a placeholder when no
result exists.
"""

from __future__ import annotations

import json

from coworker.conversations import ConversationStore


# ── helpers ──────────────────────────────────────────────────────────────────

def _assistant_with_calls(call_id: str = "c1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": "run_shell", "arguments": "{}"}}
        ],
    }


def _tool_result(call_id: str = "c1", content: str = '{"ok": true}') -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _user(text: str = "continue") -> dict:
    return {"role": "user", "content": text}


# ── tests ────────────────────────────────────────────────────────────────────

def test_passthrough_well_formed():
    """A well-formed thread with tool results immediately after calls is unchanged."""
    messages = [
        _user("go"),
        _assistant_with_calls("c1"),
        _tool_result("c1"),
        _assistant_with_calls("c2"),
        _tool_result("c2"),
        _user("done"),
    ]
    assert ConversationStore._repair_tool_pairing(messages) == messages


def test_interleaved_user_between_call_and_result():
    """A user message between a tool call and its result is moved after the result."""
    messages = [
        _user("go"),
        _assistant_with_calls("c1"),
        _user("continue"),          # interleaved — this caused the 400
        _tool_result("c1"),
    ]
    repaired = ConversationStore._repair_tool_pairing(messages)
    # The tool result must come right after the assistant message.
    assert repaired[1]["role"] == "assistant"
    assert repaired[2]["role"] == "tool"
    assert repaired[2]["tool_call_id"] == "c1"
    # The user message is pushed after the result.
    assert repaired[3]["role"] == "user"
    assert repaired[3]["content"] == "continue"


def test_dangling_call_gets_placeholder():
    """A tool call with no matching result gets a synthesised placeholder — but
    only when the thread has moved past the call (not a trailing pending call)."""
    messages = [
        _user("go"),
        _assistant_with_calls("c1"),
        _user("next"),  # thread moved past → call is corrupt, not pending
    ]
    repaired = ConversationStore._repair_tool_pairing(messages)
    assert repaired[0]["role"] == "user"
    assert repaired[1]["role"] == "assistant"
    assert repaired[2]["role"] == "tool"
    assert repaired[2]["tool_call_id"] == "c1"
    assert "error" in repaired[2]["content"]
    assert repaired[3]["role"] == "user"


def test_trailing_pending_call_not_repaired():
    """A tool call as the last message is a pending/interrupted call — the
    engine will resume it, so we must not inject a placeholder."""
    messages = [
        _user("go"),
        _assistant_with_calls("c1"),
    ]
    repaired = ConversationStore._repair_tool_pairing(messages)
    assert repaired == messages  # unchanged — no placeholder injected


def test_multi_call_block():
    """Multiple tool calls in one assistant message each get their result in order."""
    messages = [
        _user("go"),
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "run_shell", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
            ],
        },
        _user("wait"),
        _tool_result("b", '{"file": true}'),
        _tool_result("a", '{"shell": true}'),
    ]
    repaired = ConversationStore._repair_tool_pairing(messages)
    # Both results must follow the assistant message, in call order.
    assert repaired[1]["role"] == "assistant"
    assert repaired[2]["role"] == "tool"
    assert repaired[2]["tool_call_id"] == "a"
    assert repaired[3]["role"] == "tool"
    assert repaired[3]["tool_call_id"] == "b"
    # The user message comes after both results.
    assert repaired[4]["role"] == "user"


def test_idempotent():
    """Running repair twice produces the same output as running it once."""
    messages = [
        _user("go"),
        _assistant_with_calls("c1"),
        _user("continue"),
        _tool_result("c1"),
        _assistant_with_calls("c2"),
        _user("again"),
        _tool_result("c2"),
    ]
    once = ConversationStore._repair_tool_pairing(messages)
    twice = ConversationStore._repair_tool_pairing(once)
    assert once == twice


def test_no_tool_calls_unchanged():
    """A thread with no tool calls passes through unchanged."""
    messages = [
        _user("hello"),
        {"role": "assistant", "content": "hi there"},
        _user("bye"),
    ]
    assert ConversationStore._repair_tool_pairing(messages) == messages


def test_empty_list():
    """An empty message list passes through unchanged."""
    assert ConversationStore._repair_tool_pairing([]) == []


def test_load_repairs_corrupt_jsonl(tmp_path):
    """End-to-end: a corrupt JSONL file is repaired on load()."""
    store = ConversationStore(tmp_path)
    sid = "test-sid"

    # Persist a corrupt thread: user message between call and result.
    corrupt_messages = [
        _user("go"),
        _assistant_with_calls("c1"),
        _user("continue"),
        _tool_result("c1"),
    ]
    # Insert the session row so load() can find it.
    store._conn.execute(
        "INSERT INTO sessions (session_id, workspace, model, mode, n_msgs) "
        "VALUES (?, ?, ?, ?, ?)",
        (sid, str(tmp_path), "test-model", "interactive", len(corrupt_messages)),
    )
    store._conn.commit()
    # Write the corrupt JSONL.
    with open(store._file(sid), "w", encoding="utf-8") as f:
        for m in corrupt_messages:
            f.write(json.dumps(m) + "\n")

    record = store.load(sid)
    assert record is not None
    msgs = record.messages
    # The tool result must immediately follow the assistant message.
    assert msgs[1]["role"] == "assistant"
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == "c1"
    # The user message is after the result.
    assert msgs[3]["role"] == "user"
    assert msgs[3]["content"] == "continue"