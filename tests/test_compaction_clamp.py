"""The oversized-single-turn case: compaction summarizes whole turns, so one turn whose
tool results exceed the entire budget has no boundary that helps. `clamp_tool_results` is
the last line of defence that keeps such a prompt inside the serving window.

Regression cover for 2026-08-20: a papers run held 58,423 tokens of arXiv responses in a
single assistant turn. `pick_boundary` fell through to its best-effort branch, the
"compacted" outbound view was still 61,547 tokens against an 11,250 budget, it overran the
65,536 window, the runner evicted the head of the prompt to make room to generate, and the
model answered "no user query found in messages" (HTTP 500).
"""

import json

from coworker.compaction import (
    CLAMP_MARKER,
    CompactionState,
    apply_to_outbound,
    clamp_tool_results,
    estimate_tokens,
    pick_boundary,
)


def system(text="You are helpful."):
    return {"role": "system", "content": text}


def user(text):
    return {"role": "user", "content": text}


def assistant(n_calls=0):
    msg = {"role": "assistant", "content": ""}
    if n_calls:
        msg["tool_calls"] = [
            {
                "id": f"c{i}",
                "type": "function",
                "function": {"name": "web_fetch", "arguments": json.dumps({"url": "https://x"})},
            }
            for i in range(n_calls)
        ]
    return msg


def tool(call_id, chars):
    return {"role": "tool", "tool_call_id": call_id, "content": "x" * chars}


def oversized_turn():
    """system + user + one assistant turn carrying three huge tool results."""
    return [
        system(),
        user("Find recent papers."),
        assistant(3),
        tool("c0", 100_000),
        tool("c1", 100_000),
        tool("c2", 20_000),
    ]


def test_fits_already_is_returned_unchanged():
    msgs = [system(), user("hi"), assistant(1), tool("c0", 100)]
    assert clamp_tool_results(msgs, limit=10_000) is msgs


def test_non_positive_limit_is_a_noop():
    msgs = oversized_turn()
    assert clamp_tool_results(msgs, limit=0) is msgs


def test_clamps_under_the_limit():
    msgs = oversized_turn()
    assert estimate_tokens(msgs) > 50_000
    out = clamp_tool_results(msgs, limit=10_000)
    assert estimate_tokens(out) <= 10_000


def test_clamping_never_mutates_the_input():
    msgs = oversized_turn()
    before = json.dumps(msgs)
    clamp_tool_results(msgs, limit=5_000)
    assert json.dumps(msgs) == before


def test_only_tool_results_are_clipped():
    """The system prompt and the user's instruction must survive intact — losing them is
    exactly what produced 'no user query found in messages'."""
    msgs = oversized_turn()
    out = clamp_tool_results(msgs, limit=5_000)
    assert out[0] == msgs[0]
    assert out[1] == msgs[1]
    assert out[2] == msgs[2]
    assert [m["role"] for m in out] == [m["role"] for m in msgs]
    assert any(m["role"] == "user" for m in out)


def test_clipped_results_are_marked_and_keep_a_head():
    msgs = oversized_turn()
    out = clamp_tool_results(msgs, limit=5_000)
    clipped = [m for m in out if m["role"] == "tool" and m["content"].endswith(CLAMP_MARKER)]
    assert clipped, "expected at least one clipped tool result"
    for m in clipped:
        assert len(m["content"]) > len(CLAMP_MARKER)


def test_post_compaction_view_of_the_real_failure_fits_the_window():
    """End to end on the 2026-08-20 shape: compact, then clamp, and the result must fit
    a 65,536-token window with room left to answer."""
    msgs = oversized_turn()
    boundary = pick_boundary(msgs, keep_tokens=11_250)
    state = CompactionState(boundary_index=boundary, summary_text="S", working_state="")
    view = apply_to_outbound(msgs, state)

    # The bug: compaction "succeeds" while freeing essentially nothing.
    assert estimate_tokens(view) > 11_250

    limit = 65_536 - 2_000 - 4_096  # window - tool schemas - generation reserve
    fitted = clamp_tool_results(view, limit=limit)
    assert estimate_tokens(fitted) <= limit
    assert any(m.get("role") == "user" for m in fitted)
    assert fitted[0]["role"] == "system"
