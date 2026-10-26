"""OPE-192, Anthropic side. The engine glues its per-turn `<system-context>` block onto the
last user message (the same shape every provider gets) and this provider puts its cache
breakpoint on the last block of the final message. In a tool loop the final message is
pure tool results, so the breakpoint sits on a block that is identical next turn and the
whole conversation prefix is reused. Nothing is relocated any more: the converter sees
exactly what the engine sends."""

from __future__ import annotations

from coworker.providers.anthropic_provider import _add_cache_breakpoints, convert_messages

BLOCK = "\n\n<system-context>\n(automatic per-turn context, not part of the user's message)\nAvailable directories: /app\n</system-context>"


def _tool_loop():
    return [
        {"role": "user", "content": "task" + BLOCK},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "run_shell", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "out"},
    ]


def test_tool_loop_breakpoint_sits_on_the_tool_result_not_the_block():
    system, conv = convert_messages(_tool_loop())
    assert [m["role"] for m in conv] == ["user", "assistant", "user"]
    assert conv[0]["content"][0]["text"] == "task" + BLOCK  # sent as the engine built it
    assert [b["type"] for b in conv[-1]["content"]] == ["tool_result"]
    kw = {"system": system or "s", "messages": conv}
    _add_cache_breakpoints(kw)
    assert "cache_control" in kw["messages"][-1]["content"][-1]
    assert "cache_control" not in kw["messages"][0]["content"][0]


def test_same_history_converts_identically_twice():
    """Byte-stable input, byte-stable request: the converter adds nothing of its own."""
    assert convert_messages(_tool_loop()) == convert_messages(_tool_loop())


def test_chat_block_stays_on_the_newest_user_message():
    system, conv = convert_messages(
        [
            {"role": "user", "content": "draft"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "shorter" + BLOCK},
        ]
    )
    assert [m["role"] for m in conv] == ["user", "assistant", "user"]
    assert conv[-1]["content"][0]["text"] == "shorter" + BLOCK
    assert conv[0]["content"][0]["text"] == "draft"  # earlier turns untouched
    kw = {"system": system or "s", "messages": conv}
    _add_cache_breakpoints(kw)
    assert "cache_control" in kw["messages"][-1]["content"][-1]


def test_content_parts_keep_the_block_as_the_trailing_text_block():
    system, conv = convert_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {"type": "text", "text": "closely"},
                    {"type": "text", "text": BLOCK},
                ],
            },
        ]
    )
    assert len(conv) == 1
    assert [b["text"] for b in conv[0]["content"]] == ["look", "closely", BLOCK]
