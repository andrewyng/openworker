"""OPE-192: the per-turn `<system-context>` block (folders, skill menu, mode notices) rides
on the last user message — one shape for every provider — and must hold nothing that moves
on its own. In a tool loop that message is the task prompt, message one; a provider's
prompt cache is reusable only up to the first byte that differs, so a self-changing value
there (the live clock this block used to carry) re-processed the whole conversation on
every turn. No engine loop here: `_outbound_messages` alone."""

from __future__ import annotations

from coworker.engine import TurnEngine
from coworker.permissions import PermissionEngine
from coworker.tools import ToolRegistry


def _engine(tmp_path, messages, ctx):
    return TurnEngine(
        provider=object(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="x",
        messages=messages,
        context_provider=lambda: ctx[0],
    )


def _tool_loop(turns=3):
    msgs = [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "Reconstruct the scene file from this image."},
    ]
    for i in range(turns):
        msgs.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": f"c{i}", "function": {"name": "run_shell", "arguments": '{"command": "ls"}'}}],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"output {i}"})
    return msgs


FOLDERS = "Available directories: /app"


def test_tool_loop_block_rides_on_the_task_prompt_and_nowhere_else(tmp_path):
    eng = _engine(tmp_path, _tool_loop(), [FOLDERS])
    out = eng._outbound_messages()
    assert len(out) == len(eng.messages)  # no extra message
    assert out[1]["content"].startswith("Reconstruct the scene file from this image.\n\n<system-context>\n(automatic per-turn context")
    assert out[1]["content"].endswith(f"\n{FOLDERS}\n</system-context>")
    # Everything the model said and ran is untouched; the final message is the tool result.
    assert out[-1]["role"] == "tool" and out[-1]["content"] == "output 2"
    assert "<system-context>" not in str(out[2:])
    # Ephemeral: the canonical history never carries the block.
    assert eng.messages[1]["content"] == "Reconstruct the scene file from this image."


def test_tool_loop_request_is_byte_identical_turn_to_turn(tmp_path):
    """The property that keeps the prompt cache warm: with nothing self-changing in the
    block, two consecutive requests over the same history are equal, byte for byte."""
    eng = _engine(tmp_path, _tool_loop(), [FOLDERS])
    assert eng._outbound_messages() == eng._outbound_messages()


def test_tool_loop_next_turn_keeps_the_previous_request_as_a_prefix(tmp_path):
    eng = _engine(tmp_path, _tool_loop(turns=2), [FOLDERS])
    before = eng._outbound_messages()
    eng.messages.append(
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c9", "function": {"name": "run_shell", "arguments": "{}"}}]}
    )
    eng.messages.append({"role": "tool", "tool_call_id": "c9", "content": "output 9"})
    after = eng._outbound_messages()
    assert after[: len(before)] == before  # the whole previous request is a prefix
    assert after[-1]["content"] == "output 9"


def test_a_block_change_touches_only_the_message_it_rides_on(tmp_path):
    """When the user grants a folder or flips a mode the block changes — once, and only
    the message carrying it differs. That is the cost of a user action, not of time."""
    ctx = [FOLDERS]
    eng = _engine(tmp_path, _tool_loop(), ctx)
    first = eng._outbound_messages()
    ctx[0] = FOLDERS + "\nAvailable directories: /data"
    second = eng._outbound_messages()
    assert first[0] == second[0] and first[2:] == second[2:]
    assert first[1] != second[1]


def test_chat_block_rides_on_the_newest_user_message(tmp_path):
    msgs = [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "draft an email"},
        {"role": "assistant", "content": "draft"},
        {"role": "user", "content": "make it shorter"},
    ]
    eng = _engine(tmp_path, msgs, [FOLDERS])
    out = eng._outbound_messages()
    assert len(out) == len(msgs)
    assert out[-1]["content"].startswith("make it shorter\n\n<system-context>")
    assert out[1]["content"] == "draft an email"  # earlier user turns untouched
    assert eng.messages[-1]["content"] == "make it shorter"


def test_content_parts_get_the_block_as_a_trailing_text_part(tmp_path):
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "text", "text": "closely"}]},
    ]
    eng = _engine(tmp_path, msgs, [FOLDERS])
    out = eng._outbound_messages()
    assert len(out) == 1
    parts = out[0]["content"]
    assert parts[:2] == msgs[0]["content"]
    assert parts[2]["type"] == "text" and parts[2]["text"].startswith("\n\n<system-context>")


def test_no_context_means_no_block_anywhere(tmp_path):
    eng = TurnEngine(
        provider=object(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="x",
        messages=_tool_loop(),
        context_provider=lambda: "",
    )
    out = eng._outbound_messages()
    assert out == eng.messages and "<system-context>" not in str(out)
