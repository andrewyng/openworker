"""The trajectory writer renders engine notices by their text, a compacted notice's
summary lands in the trajectory, and every agent step carries the raw token counts so a
consumer can price the call itself. Direct unit tests, no subprocess."""

from __future__ import annotations

from coworker.headless.trajectory import build_trajectory


def _traj(messages, model="anthropic:claude-sonnet-5"):
    return build_trajectory(
        messages,
        agent_name="openworker",
        agent_version="test",
        model_name=model,
        session_id="s",
        outcome="completed",
    )


def test_compacted_notice_step_includes_the_summary():
    messages = [
        {"role": "user", "content": "do the thing", "ts": 1.0},
        {"role": "assistant", "content": "working", "finish_reason": "tool_calls", "ts": 2.0},
        {
            "role": "notice",
            "kind": "compacted",
            "text": "Context compacted — earlier turns were summarized",
            "compaction": {
                "boundary_index": 7,
                "summary_text": "## Summary\nWrote interp.py; tests 1-40 pass.",
                "working_state": "next: fix tail calls",
                "user_messages": ["do the thing"],
                "user_messages_dropped": 0,
                "created_at": 3.0,
                "model_used": "claude-sonnet-5",
                "trimmed": False,
            },
            "ts": 3.0,
        },
        {"role": "assistant", "content": "done", "finish_reason": "stop", "ts": 4.0},
    ]
    steps = _traj(messages)["steps"]
    system = [s for s in steps if s["source"] == "system"]
    assert len(system) == 1
    msg = system[0]["message"]
    assert msg.startswith("Context compacted")
    assert "[compaction summary]" in msg and "tests 1-40 pass" in msg
    assert "[working state]" in msg and "fix tail calls" in msg
    assert "trimmed" not in msg


def test_trimmed_notice_and_plain_notices_render_their_text():
    messages = [
        {"role": "user", "content": "go", "ts": 1.0},
        {"role": "notice", "kind": "compacted", "text": "Context trimmed — oldest turns dropped",
         "compaction": {"boundary_index": 3, "summary_text": "", "working_state": "", "trimmed": True}, "ts": 2.0},
        {"role": "notice", "kind": "truncated", "text": "cut off 3 times", "ts": 3.0},
        {"role": "notice", "kind": "interrupted", "ts": 4.0},
    ]
    system = [s["message"] for s in _traj(messages)["steps"] if s["source"] == "system"]
    assert system[0].startswith("Context trimmed") and "no summary was produced" in system[0]
    assert system[1] == "cut off 3 times"
    assert system[2] == "interrupted"  # kind is the fallback when a notice has no text


def test_agent_steps_carry_raw_counts_host_and_a_priced_total():
    messages = [
        {"role": "user", "content": "go", "ts": 1.0},
        {
            "role": "assistant",
            "content": "done",
            "finish_reason": "stop",
            "ts": 2.0,
            "usage": {"input": 1000, "output": 100, "cache_read": 500, "cache_write": 200},
            "served_by": "Together",
        },
    ]
    traj = _traj(messages)
    step = [s for s in traj["steps"] if s["source"] == "agent"][0]
    assert step["metrics"]["prompt_tokens"] == 1700 and step["metrics"]["cached_tokens"] == 500
    assert step["metrics"]["extra"] == {
        "input": 1000,
        "output": 100,
        "cache_read": 500,
        "cache_write": 200,
        "served_by": "Together",
    }
    # Sonnet 5: 1000*2 + 100*10 + 500*0.2 + 200*2.5 per million.
    assert step["metrics"]["cost_usd"] == round((2000 + 1000 + 100 + 500) / 1e6, 6)
    assert traj["final_metrics"]["total_cost_usd"] == step["metrics"]["cost_usd"]


def test_an_unpriced_model_gets_counts_but_no_cost():
    messages = [
        {"role": "user", "content": "go", "ts": 1.0},
        {"role": "assistant", "content": "done", "finish_reason": "stop", "ts": 2.0,
         "usage": {"input": 10, "output": 5}},
    ]
    traj = _traj(messages, model="together:some/unpriced-model")
    step = [s for s in traj["steps"] if s["source"] == "agent"][0]
    assert "cost_usd" not in step["metrics"] and step["metrics"]["prompt_tokens"] == 10
    assert "total_cost_usd" not in traj["final_metrics"]
