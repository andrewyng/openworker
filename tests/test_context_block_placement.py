"""OPE-192: the per-turn `<system-context>` block (folders, skill menu, mode notices) starts
on the last user message and stays there while unchanged — one shape for every provider.
It must hold nothing that moves
on its own. In a tool loop that message is the task prompt, message one; a provider's
prompt cache is reusable only up to the first byte that differs, so a self-changing value
there (the live clock this block used to carry) re-processed the whole conversation on
every turn. No engine loop here: `_outbound_messages` alone."""

from __future__ import annotations

from copy import deepcopy

import pytest

from coworker.compaction import CompactionState
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


def _next_question(eng, text="next question"):
    eng.messages.extend([
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": text},
    ])


def test_new_questions_keep_the_previous_request_as_a_prefix(tmp_path):
    eng = _engine(tmp_path, _tool_loop(), [FOLDERS])
    before = eng._outbound_messages()
    for _ in range(3):
        _next_question(eng)
        canonical = deepcopy(eng.messages)
        after = eng._outbound_messages()
        assert after[:len(before)] == before
        assert str(after).count("<system-context>") == 1
        assert eng.messages == canonical  # no context sidecars or changed user text
        before = after


def test_steering_mid_tool_loop_does_not_move_unchanged_context(tmp_path):
    eng = _engine(tmp_path, _tool_loop(), [FOLDERS])
    before = eng._outbound_messages()
    eng.messages.append({"role": "user", "content": "use the smaller file instead"})
    after = eng._outbound_messages()
    assert after[:-1] == before
    assert after[-1] == eng.messages[-1]


def test_changed_or_empty_context_removes_all_old_instructions(tmp_path):
    ctx = ["Plan mode is active. Available directories: /private"]
    eng = _engine(tmp_path, _tool_loop(), ctx)
    eng._outbound_messages()
    _next_question(eng)
    ctx[0] = FOLDERS
    changed = eng._outbound_messages()
    assert "Plan mode is active" not in str(changed)
    assert "/private" not in str(changed)
    assert changed[1] == eng.messages[1]
    assert changed[-1]["content"].endswith(f"{FOLDERS}\n</system-context>")
    assert eng._last_context_block == FOLDERS
    _next_question(eng)
    assert eng._outbound_messages()[:len(changed)] == changed
    ctx[0] = ""
    assert eng._outbound_messages() == eng.messages
    assert eng._last_context_block == ""
    ctx[0] = FOLDERS
    restored = eng._outbound_messages()
    assert restored[:-1] == eng.messages[:-1]
    assert "<system-context>" in restored[-1]["content"]


def test_content_parts_and_display_sidecars_keep_the_anchor(tmp_path):
    message = {"role": "user", "mid": "u1", "ts": 1, "content": [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
    ]}
    from coworker.providers import ModelCapabilities

    class VisionProvider:
        def capabilities(self, model):
            return ModelCapabilities(vision=True)

    eng = _engine(tmp_path, [message], [FOLDERS])
    eng.provider = VisionProvider()
    canonical = deepcopy(message)
    before = eng._outbound_messages()
    _next_question(eng)
    after = eng._outbound_messages()
    assert after[:1] == before
    assert "mid" not in after[0] and "ts" not in after[0]
    assert after[0]["content"][:2] == canonical["content"]
    assert message == canonical


def test_equal_user_text_does_not_select_an_older_message(tmp_path):
    eng = _engine(tmp_path, [{"role": "user", "content": "same"}], [FOLDERS])
    _next_question(eng, "same")
    before = eng._outbound_messages()  # first request anchors the newest occurrence
    assert "<system-context>" not in before[0]["content"]
    _next_question(eng, "same")
    assert eng._outbound_messages()[:len(before)] == before


@pytest.mark.parametrize("restore", [False, True])
def test_replaced_or_restored_history_starts_at_current_user(tmp_path, restore):
    eng = _engine(tmp_path, _tool_loop(), [FOLDERS])
    eng._outbound_messages()
    _next_question(eng)
    history = deepcopy(eng.messages)
    if restore:
        eng = _engine(tmp_path, history, [FOLDERS])
    else:
        eng.messages = history
        eng.context_provider = lambda: FOLDERS
    out = eng._outbound_messages()
    assert out[:-1] == history[:-1]
    assert str(out).count("<system-context>") == 1
    assert "<system-context>" in out[-1]["content"]
    _next_question(eng)
    assert eng._outbound_messages()[:len(out)] == out


@pytest.mark.parametrize("keep_user", [False, True])
def test_compaction_reanchors_once_and_then_keeps_the_prefix(tmp_path, keep_user):
    eng = _engine(tmp_path, _tool_loop(), [FOLDERS])
    eng._outbound_messages()
    if keep_user:
        _next_question(eng)
    eng.compaction_state = CompactionState(
        boundary_index=2, summary_text="prior work", working_state="continue",
    )
    first = eng._outbound_messages()
    assert str(first).count("<system-context>") == 1
    assert eng._outbound_messages() == first  # synthetic user is rebuilt each call
    _next_question(eng)
    second = eng._outbound_messages()
    assert second[:len(first)] == first
    # A new compaction removes the previous anchor; its context must still be present.
    eng.compaction_state = CompactionState(
        boundary_index=len(eng.messages) - 1, summary_text="more work", working_state="continue",
    )
    third = eng._outbound_messages()
    assert str(third).count("<system-context>") == 1
    assert "<system-context>" in third[-1]["content"]


def test_anthropic_cached_text_prefix_survives_a_new_question(tmp_path):
    from coworker.providers.anthropic_provider import _add_cache_breakpoints, convert_messages

    eng = _engine(tmp_path, _tool_loop(), [FOLDERS])
    system, before = convert_messages(eng._outbound_messages())
    request = {"system": system, "messages": deepcopy(before)}
    _add_cache_breakpoints(request)
    assert "cache_control" in request["messages"][-1]["content"][-1]
    _next_question(eng)
    next_system, after = convert_messages(eng._outbound_messages())
    assert next_system == system
    # Compare actual provider text/blocks before per-request breakpoint placement.
    assert after[:len(before)] == before


def test_activity_expansion_and_repeated_message_objects_keep_the_position(tmp_path):
    same = {"role": "user", "content": "same", "_activity": {"text": "earlier receipt"}}
    eng = _engine(tmp_path, [same, {"role": "assistant", "content": "ok"}, same], [FOLDERS])
    first = eng._outbound_messages()
    assert "<system-context>" not in first[1]["content"]
    assert "<system-context>" in first[-1]["content"]
    _next_question(eng)
    assert eng._outbound_messages()[:len(first)] == first
    # Removing the anchor cannot keep it alive merely because another equal user exists.
    eng.messages = [{"role": "user", "content": "replacement"}]
    out = eng._outbound_messages()
    assert out[0]["content"].startswith("replacement\n\n<system-context>")
    assert "_activity" not in str(out)


def test_changed_synthetic_compaction_reanchors_to_latest_user(tmp_path):
    eng = _engine(tmp_path, _tool_loop(), [FOLDERS])
    state = CompactionState(boundary_index=2, summary_text="old", working_state="continue")
    eng.compaction_state = state
    eng._outbound_messages()
    _next_question(eng)
    state.summary_text = "updated summary"  # same state object, different rendered block
    out = eng._outbound_messages()
    assert "<system-context>" not in out[1]["content"]
    assert "<system-context>" in out[-1]["content"]


def test_live_mode_roots_and_disabled_skill_replace_the_single_block(tmp_path):
    from coworker.agent import build_engine
    from coworker.agents import code_agent
    from coworker.permissions import Mode
    from coworker.providers import ModelCapabilities
    from coworker.roots import RootDir

    class Provider:
        def capabilities(self, model):
            return ModelCapabilities()

    workspace = tmp_path / "workspace"
    skill_dir = workspace / ".coworker" / "skills" / "cache-test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: cache-test-skill\ndescription: Cache regression skill\n---\nDo this task.",
        encoding="utf-8",
    )
    allowed = {"cache-test-skill"}
    eng = build_engine(
        agent=code_agent(), workspace=workspace, provider=Provider(), mode=Mode.PLAN,
        skill_filter=lambda: allowed,
    )
    try:
        extra = tmp_path / "granted"
        extra.mkdir()
        eng.roots.append(RootDir(path=extra, writable=True))
        eng.messages.extend([
            {"role": "user", "content": "first task"},
            {"role": "assistant", "tool_calls": [{"id": "skill", "type": "function",
                "function": {"name": "load_skill", "arguments": '{"name":"cache-test-skill"}'}}]},
            {"role": "tool", "tool_call_id": "skill", "content": '{"name":"cache-test-skill","instructions":"Do this task."}'},
        ])
        first = eng._outbound_messages()
        assert "Plan mode is active" in str(first)
        _next_question(eng)
        assert eng._outbound_messages()[:len(first)] == first
        eng.permissions.mode = Mode.DISCUSS
        eng.roots[:] = [r for r in eng.roots if r.path != extra]
        allowed.clear()
        canonical = deepcopy(eng.messages)
        changed = eng._outbound_messages()
        block = changed[-1]["content"].split("<system-context>")[1]
        assert "Discuss mode is active" in block
        assert "Plan mode is active" not in str(changed)
        assert str(extra) not in block
        assert 'skill "cache-test-skill" has been disabled' in block
        assert not eng.permissions._under_writable_root(str(extra / "file.txt"))
        assert str(changed).count("<system-context>") == 1
        assert eng.messages == canonical
        allowed.add("cache-test-skill")
        eng.permissions.mode = Mode.INTERACTIVE
        final = eng._outbound_messages()
        assert "Discuss mode is active" not in str(final)
        assert "disabled by the user" not in str(final)
        assert str(final).count("<system-context>") == 1
    finally:
        eng.executor.close()
