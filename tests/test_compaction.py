"""OPE-27 — auto-compaction pure functions: trigger math, boundary picking, mechanical
extraction, summarizer seam, trim fallback, outbound view. No engine involved."""

import json

import pytest

from coworker import compaction as C
from coworker.compaction import (
    CompactionState,
    DEFAULT_CAP_TOKENS,
    DEFAULT_CONTEXT_WINDOW,
    apply_to_outbound,
    build_state,
    compacted_block,
    estimate_tokens,
    extract_user_messages,
    extract_working_state,
    is_context_overflow,
    pick_boundary,
    should_compact,
    summarize_span,
    summarizer_messages,
    trigger_tokens,
    trim_state,
)


# -- message builders ---------------------------------------------------------


def user(text):
    return {"role": "user", "content": text, "ts": 1.0}


_call_seq = 0


def assistant(text="", tool_calls=None):
    global _call_seq
    msg = {"role": "assistant", "content": text, "ts": 1.0}
    if tool_calls:
        calls = []
        for name, args in tool_calls:
            calls.append(
                {
                    "id": f"c{_call_seq}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            )
            _call_seq += 1
        msg["tool_calls"] = calls
    return msg


def tool(call_id, content):
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": content if isinstance(content, str) else json.dumps(content),
        "ts": 1.0,
    }


def tool_turn(name, args, result):
    """[assistant tool-call, matching tool result] with a properly paired call id."""
    a = assistant(tool_calls=[(name, args)])
    return [a, tool(a["tool_calls"][0]["id"], result)]


def convo(turns=6, bulk=2000):
    """system + N user/assistant turns with bulky assistant text."""
    msgs = [{"role": "system", "content": "You are a coworker."}]
    for i in range(turns):
        msgs.append(user(f"request {i}"))
        msgs.append(assistant(f"answer {i} " + "x" * bulk))
    return msgs


class FakeSummarizer:
    # The default must satisfy the OPE-189 quality gate (all eight sections, past the
    # minimum length) — an unusable summary is now refused, not stored. GOOD_SUMMARY is
    # defined further down the module and resolved when a fake is constructed.
    def __init__(self, text=None, fail_times=0):
        self.text = GOOD_SUMMARY if text is None else text
        self.fail_times = fail_times
        self.calls = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append({"model": model, "messages": messages, "tools": tools, **settings})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("summarizer down")

        class Turn:
            pass

        t = Turn()
        t.text = self.text
        return t


# -- trigger math -------------------------------------------------------------


def test_trigger_is_min_of_pct_and_cap():
    assert trigger_tokens(100_000) == 80_000
    assert trigger_tokens(1_000_000) == DEFAULT_CAP_TOKENS  # the 250k cap wins
    assert trigger_tokens(None) == int(0.8 * DEFAULT_CONTEXT_WINDOW)
    # both knobs are user-overridable
    assert trigger_tokens(100_000, threshold_pct=0.5, cap_tokens=40_000) == 40_000
    assert trigger_tokens(100_000, threshold_pct=0.5, cap_tokens=999_999) == 50_000


def test_should_compact_crosses_threshold():
    assert not should_compact(79_999, 100_000)
    assert should_compact(80_000, 100_000)


def test_estimate_tokens_is_chars_over_four():
    msgs = [user("a" * 400)]
    est = estimate_tokens(msgs)
    assert 100 <= est <= 120  # 400 chars of content + json overhead, /4


# -- boundary -----------------------------------------------------------------


def test_boundary_prefers_earliest_user_turn_that_fits():
    msgs = convo(turns=6)
    per_turn = estimate_tokens(msgs[1:3])
    boundary = pick_boundary(msgs, keep_tokens=per_turn * 2 + 10)
    assert msgs[boundary]["role"] == "user"
    assert msgs[boundary]["content"] == "request 4"  # newest two turns survive


def test_boundary_falls_inside_a_giant_final_turn():
    # One user turn followed by a huge tool loop: the turn alone exceeds the budget,
    # so the cut lands on an assistant (iteration) boundary inside it — never a tool row.
    msgs = [{"role": "system", "content": "s"}, user("go")]
    for i in range(8):
        a = assistant("step " + "y" * 3000, tool_calls=[("run_shell", {"command": f"cmd{i}"})])
        msgs += [a, tool(a["tool_calls"][0]["id"], {"exit_code": 0, "out": "z" * 3000})]
    boundary = pick_boundary(msgs, keep_tokens=estimate_tokens(msgs[-3:]))
    assert msgs[boundary]["role"] == "assistant"


def test_boundary_none_when_nothing_to_summarize():
    msgs = [{"role": "system", "content": "s"}, user("hi"), assistant("hello")]
    assert pick_boundary(msgs, keep_tokens=10_000_000) is None


# -- mechanical extraction ----------------------------------------------------


def test_working_state_files_commands_tools():
    span = [
        user("write it"),
        *tool_turn("write_file", {"path": "a.py", "content": "x"}, {"ok": True}),
        *tool_turn("run_shell", {"command": "pytest -q"}, {"exit_code": 1}),
        *tool_turn("write_file", {"path": "b.py", "content": "y"}, {"ok": True}),
        *tool_turn("write_file", {"path": "a.py", "content": "x2"}, {"ok": True}),
    ]
    block = extract_working_state(span)
    # deduped, most recent first
    assert block.index("- a.py") < block.index("- b.py")
    assert block.count("a.py") == 1
    assert "pytest -q" in block and "[exit 1]" in block
    assert "run_shell" in block and "write_file" in block


def test_working_state_empty_span():
    assert extract_working_state([user("hi"), assistant("yo")]) == ""


def test_user_messages_extracted_verbatim_and_uncut():
    # OPE-189: extraction no longer clips — a 1,400-char task statement reached the block
    # cut mid-sentence, losing the output path and the field names the grader checks.
    span = [
        user("first ask"),
        assistant("a"),
        user([{"type": "text", "text": "second"}, {"type": "image_url", "image_url": {}}]),
        assistant("b"),
        user("bulk " + "z" * 2000),
    ]
    out = extract_user_messages(span)
    assert out[0] == "first ask"
    assert out[1] == "second [image]"
    assert out[2] == "bulk " + "z" * 2000  # verbatim; fitting is fit_user_messages' job


# -- summarizer seam ----------------------------------------------------------


def test_summarizer_messages_clip_tool_results_and_fold_prior():
    span = [user("go"), *tool_turn("read_file", {"path": "big.txt"}, "huge " * 500)]
    msgs = summarizer_messages(span, prior_summary="OLD SUMMARY")
    body = msgs[1]["content"]
    assert "OLD SUMMARY" in body
    # The 2500-char tool result got clipped hard; the tail instruction is the only other bulk.
    assert len(body) < 3000 + len(C.SUMMARY_TAIL_INSTRUCTION)
    assert msgs[0]["role"] == "system" and "Primary request and intent" in msgs[0]["content"]


def test_summarizer_instruction_comes_after_the_transcript():
    """OPE-189: recency decides. With the instruction only up front, the last thing the
    model read was the coworker mid-task and it continued that voice instead of
    summarizing. The transcript is delimited and the instruction restated last."""
    span = [user("go"), *tool_turn("run_shell", {"command": "ls"}, "a.txt b.txt")]
    body = summarizer_messages(span)[1]["content"]
    assert body.startswith("**--- BEGIN TRANSCRIPT TO SUMMARIZE ---**")
    assert body.rstrip().endswith(C.SUMMARY_TAIL_INSTRUCTION)
    assert body.index("[tool result]") < body.index("**--- END OF TRANSCRIPT ---**")
    assert "## 1. Primary request and intent" in C.SUMMARY_TAIL_INSTRUCTION


GOOD_SUMMARY = "\n".join(
    f"## {i}. {name}\ndetail line with enough substance to clear the minimum length bar"
    for i, name in enumerate(
        [
            "Primary request and intent",
            "Key concepts and decisions",
            "Artifacts and files",
            "Errors and fixes",
            "All user messages",
            "Pending tasks",
            "Current work",
            "Next step",
        ],
        start=1,
    )
)


def test_summarize_span_passes_model_and_budget():
    fake = FakeSummarizer(text=GOOD_SUMMARY)
    out = summarize_span(fake, "prov:model-x", [user("hi")], max_tokens=9999)
    assert out == GOOD_SUMMARY
    assert fake.calls[0]["model"] == "prov:model-x"
    assert fake.calls[0]["tools"] is None
    assert fake.calls[0]["max_tokens"] == 9999


def test_summarize_span_refuses_an_unusable_summary():
    """OPE-189: 'not empty' accepted a 61-char next-action sentence as the coworker's whole
    memory of 75 turns. Empty, too short, and role-slipped replies are all refused so the
    caller's retry (then trim) policy runs."""
    for bad in (
        "  ",
        "The output got truncated. Let me re-run the remaining checks.",
        "## Artifacts and files\n" + "detail " * 200,  # long, but not the anchor section
    ):
        with pytest.raises(RuntimeError):
            summarize_span(FakeSummarizer(text=bad), "m", [user("hi")])


def test_summary_quality_problem_names_the_fault():
    assert C.summary_quality_problem(GOOD_SUMMARY) is None
    assert C.summary_quality_problem("") == "empty"
    assert "too short" in C.summary_quality_problem("## 1. Primary request and intent")
    slipped = "Let me re-run the remaining checks. " * 20
    assert "does not open the sections" in C.summary_quality_problem(slipped)


# -- build + repeated compaction ----------------------------------------------


def test_build_state_and_outbound_view():
    msgs = convo(turns=6)
    fake = FakeSummarizer(text=GOOD_SUMMARY + "\nthe gist")
    state = build_state(
        msgs, provider=fake, model="m", keep_tokens=estimate_tokens(msgs[-4:]) + 10
    )
    assert state is not None and not state.trimmed
    assert state.user_messages[0] == "request 0"

    out = apply_to_outbound(msgs, state)
    assert out[0]["role"] == "system"  # instructions survive
    assert "<compacted-history>" in out[1]["content"]
    assert "the gist" in out[1]["content"]
    assert "request 0" in out[1]["content"]  # mechanical user-message list
    assert out[2] is msgs[state.boundary_index]  # verbatim tail, canonical untouched
    assert len(msgs) == 13  # canonical history unchanged


def test_repeated_compaction_summarizes_prior_plus_new_turns():
    msgs = convo(turns=4)
    fake = FakeSummarizer()
    first = build_state(msgs, provider=fake, model="m", keep_tokens=estimate_tokens(msgs[-4:]) + 10)
    # session grows
    for i in range(4, 8):
        msgs.append(user(f"request {i}"))
        msgs.append(assistant(f"answer {i} " + "x" * 2000))
    second = build_state(
        msgs, provider=fake, model="m",
        keep_tokens=estimate_tokens(msgs[-4:]) + 10, prior=first,
    )
    assert second is not None and second.boundary_index > first.boundary_index
    # the second summarizer call folds the prior summary in
    assert "previous compaction summary" in fake.calls[1]["messages"][1]["content"]
    # user messages accumulate across compactions
    assert "request 0" in second.user_messages[0]
    assert any("request 5" in u for u in second.user_messages)


def test_build_state_none_when_boundary_stale():
    msgs = convo(turns=3)
    fake = FakeSummarizer()
    state = build_state(msgs, provider=fake, model="m", keep_tokens=estimate_tokens(msgs[-2:]) + 10)
    again = build_state(
        msgs, provider=fake, model="m",
        keep_tokens=10_000_000, prior=state,
    )
    assert again is None  # nothing new fits below the prior boundary


# -- trim fallback ------------------------------------------------------------


def test_trim_advances_boundary_and_keeps_user_messages():
    msgs = convo(turns=10)
    state = trim_state(msgs)
    assert state is not None and state.trimmed
    assert msgs[state.boundary_index]["role"] in ("user", "assistant")
    assert state.user_messages  # preserved mechanically even without a summary
    assert "trimmed" in state.summary_text
    out = apply_to_outbound(msgs, state)
    assert len(out) < len(msgs) + 1


def test_trim_from_prior_state_never_lands_on_tool_row():
    msgs = [{"role": "system", "content": "s"}, user("go")]
    for i in range(10):
        msgs += tool_turn("run_shell", {"command": f"c{i}"}, {"exit_code": 0})
    prior = trim_state(msgs)
    later = trim_state(msgs, prior=prior)
    assert later.boundary_index > prior.boundary_index
    assert msgs[later.boundary_index]["role"] != "tool"


def test_trim_none_when_too_small():
    assert trim_state([user("hi"), assistant("yo")]) is None


# -- state round-trip + overflow detection ------------------------------------


def test_state_dict_round_trip():
    state = CompactionState(
        boundary_index=7, summary_text="s", working_state="w",
        user_messages=["u1"], created_at=1.5, model_used="m", trimmed=True,
    )
    assert CompactionState.from_dict(state.as_dict()) == state
    assert CompactionState.from_dict(None) is None
    assert CompactionState.from_dict({}) is None


def test_apply_to_outbound_noop_on_stale_or_missing_state():
    msgs = convo(turns=2)
    assert apply_to_outbound(msgs, None) is msgs
    stale = CompactionState(boundary_index=999, summary_text="s", working_state="")
    assert apply_to_outbound(msgs, stale) is msgs


def test_is_context_overflow():
    assert is_context_overflow(Exception("Error 400: maximum context length is 128000 tokens"))
    assert is_context_overflow(Exception("context_length_exceeded"))
    assert is_context_overflow(Exception("Prompt is too long: 210000 tokens > limit"))
    assert not is_context_overflow(Exception("rate limit exceeded"))
    assert not is_context_overflow(Exception("connection reset"))


def test_user_messages_pin_the_first_and_budget_the_rest():
    """OPE-189: the list must not grow forever, but the OLD rule (newest 40) silently threw
    away the opening message — the one that says why the session exists. Now the first is
    pinned and the newest fill a token budget; the gap is in the middle."""
    msgs = [{"role": "system", "content": "s"}]
    msgs.append({"role": "user", "content": "THE ORIGINAL TASK: " + "spec " * 100})
    msgs.append({"role": "assistant", "content": "starting"})
    for i in range(300):
        msgs.append({"role": "user", "content": f"ask {i} " + "filler " * 40})
        msgs.append({"role": "assistant", "content": f"answer {i}"})

    state = None
    while True:
        nxt = trim_state(msgs, prior=state, fraction=0.4, user_budget_tokens=2_000)
        if nxt is None:
            break
        state = nxt

    assert state is not None
    assert state.user_messages[0].startswith("THE ORIGINAL TASK:")  # pinned, never dropped
    # The newest preserved message is from the end of the run (the very last turns stay in
    # the verbatim tail rather than the block, so this is the newest that was compacted).
    assert state.user_messages[-1].startswith("ask 29")
    assert state.user_messages_dropped > 0  # the middle is what gives
    block = compacted_block(state)
    assert "older user messages omitted here" in block
    assert block.index("THE ORIGINAL TASK") < block.index("omitted here")

    restored = CompactionState.from_dict(state.as_dict())
    assert restored.user_messages == state.user_messages
    assert restored.user_messages_dropped == state.user_messages_dropped


def test_fit_user_messages_pins_first_clips_giants_and_counts_drops():
    budget = 100  # tokens, i.e. ~400 chars
    msgs = ["FIRST " + "a" * 200, "old " + "b" * 400, "mid", "newest"]
    kept, dropped = C.fit_user_messages(msgs, prior_dropped=3, budget_tokens=budget)
    assert kept[0] == msgs[0]  # pinned whole: well under the pin cap
    assert kept[-1] == "newest"
    assert dropped >= 3  # running total carries forward
    assert "mid" in kept  # cheap messages still fit

    # A first message far over the pin cap is clipped, never dropped.
    huge = "X" * 100_000
    kept, _ = C.fit_user_messages([huge, "later"], prior_dropped=0, budget_tokens=budget)
    assert kept[0].startswith("XXX") and kept[0].endswith("…")
    assert len(kept[0]) <= C._USER_PIN_MAX_TOKENS * 4

    # A single newest message over budget is clipped to fit, not dropped.
    kept, _ = C.fit_user_messages(["first", huge], prior_dropped=0, budget_tokens=budget)
    assert len(kept) == 2 and kept[1].endswith("…")


def test_user_message_budget_scales_with_the_trigger():
    assert C.user_message_budget(250_000) == 20_000  # at the default trigger
    assert C.user_message_budget(60_000) == 4_800  # a lowered trigger keeps its saving
    assert C.user_message_budget(1_000) == C._USER_BUDGET_MIN  # floor
    assert C.user_message_budget(10_000_000) == C._USER_BUDGET_MAX  # ceiling
