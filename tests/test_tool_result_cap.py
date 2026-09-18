"""OPE-186 change 1: every tool result is bounded before it enters the conversation.

Unit tests for `coworker.toolresult` and an engine round trip with a scripted provider
(no network). The contract: a result that fits is untouched byte-for-byte; an oversized
result keeps its head and tail, names a spill file holding the full text, stays valid
JSON when it was a dict, and is deterministic (no timestamps) so later requests carry an
identical prefix for the provider's prompt cache.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aisuite as ai

from coworker import toolresult
from coworker.engine import TurnEngine
from coworker.events import EventType
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.tools import ToolRegistry


def _bounded(result, *, max_bytes=10_000, spill: Path | None, step=7, tool="run_shell"):
    return toolresult.bound_tool_result(
        result, max_bytes=max_bytes, spill_dir=spill, step=step, tool_name=tool
    )


def _size(x) -> int:
    return len(toolresult.serialize_result(x).encode("utf-8"))


def test_small_result_is_returned_unchanged(tmp_path):
    result = {"command": "ls", "exit_code": 0, "output": "a\nb\n", "truncated": False}
    out = _bounded(result, spill=tmp_path)
    assert out is result  # same object, not a copy
    assert not list(tmp_path.iterdir())  # nothing spilled


def test_large_shell_output_keeps_head_and_tail_and_spills_full_text(tmp_path):
    lines = "\n".join(f"line{i:05d}" for i in range(4000))  # ~44 KB
    result = {"command": "make", "cwd": "/app", "exit_code": 1, "output": lines, "truncated": False}
    out = _bounded(result, spill=tmp_path)
    assert out is not result and out["exit_code"] == 1 and out["command"] == "make"
    assert _size(out) <= 10_000
    assert out["output"].startswith("line00000")  # the head survives
    assert out["output"].rstrip().endswith("line03999")  # and the tail
    assert "bytes omitted here" in out["output"] and "run_shell: sed -n" in out["output"]
    spilled = list(tmp_path.iterdir())
    assert len(spilled) == 1 and spilled[0].name == "0007-run_shell-output.txt"
    assert spilled[0].read_text(encoding="utf-8") == lines  # complete, untouched
    assert str(spilled[0]) in out["output"]  # the marker names the file
    # Still a well-formed message: the model gets JSON it can parse.
    json.loads(toolresult.serialize_result(out))


def test_read_file_content_is_bounded_the_same_way(tmp_path):
    content = "\n".join(f"{i:>6}\t{'x' * 80}" for i in range(3000))
    result = {"path": "big.py", "start_line": 1, "end_line": 3000, "total_lines": 3000, "content": content}
    out = _bounded(result, spill=tmp_path, tool="read_file")
    assert _size(out) <= 10_000 and out["path"] == "big.py" and out["total_lines"] == 3000
    assert "bytes omitted here" in out["content"]
    assert (tmp_path / "0007-read_file-content.txt").read_text(encoding="utf-8") == content


def test_plain_string_result_is_bounded(tmp_path):
    text = "y" * 50_000
    out = _bounded(text, spill=tmp_path, tool="web_fetch")
    assert isinstance(out, str) and len(out.encode()) <= 10_000
    assert out.startswith("yyyy") and out.endswith("yyyy") and "bytes omitted" in out
    assert (tmp_path / "0007-web_fetch.txt").read_text(encoding="utf-8") == text


def test_multibyte_text_is_cut_safely(tmp_path):
    text = "é☃𝄞" * 20_000  # 1-, 3- and 4-byte characters
    out = _bounded(text, spill=tmp_path)
    out.encode("utf-8")  # no lone surrogates / broken sequences
    assert len(out.encode()) <= 10_000


def test_deterministic_and_cache_stable(tmp_path):
    result = {"output": "z" * 30_000, "exit_code": 0}
    a = _bounded(result, spill=tmp_path)
    b = _bounded(result, spill=tmp_path)
    assert a == b  # identical text both times: no timestamps, no counters


def test_disabled_with_zero_or_none(tmp_path):
    result = {"output": "z" * 30_000}
    assert _bounded(result, max_bytes=0, spill=tmp_path) is result
    assert _bounded(result, max_bytes=None, spill=tmp_path) is result


def test_two_big_fields_are_both_bounded(tmp_path):
    result = {"stdout": "a" * 20_000, "stderr": "b" * 20_000, "exit_code": 2}
    out = _bounded(result, spill=tmp_path)
    assert _size(out) <= 10_000
    assert "bytes omitted" in out["stdout"] and "bytes omitted" in out["stderr"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["0007-run_shell-stderr.txt", "0007-run_shell-stdout.txt"]


def test_without_spill_dir_marker_says_so(tmp_path):
    out = _bounded({"output": "q" * 30_000}, spill=None)
    assert "full text not saved" in out["output"]


# -- engine round trip --------------------------------------------------------------------


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _run(engine, prompt):
    async def go():
        return [ev async for ev in engine.run(prompt)]

    return asyncio.run(go())


def test_engine_bounds_tool_results_and_keeps_the_rest_of_the_message(tmp_path):
    low = ai.ToolMetadata(category="search", risk_level="low", requires_approval=False)

    def big_out():
        """Return a very large result."""
        return {"output": "\n".join(f"row{i}" for i in range(6000)), "exit_code": 0}

    registry = ToolRegistry()
    registry.register(big_out, metadata=low)
    permissions = PermissionEngine(workspace_root=tmp_path)
    spill = tmp_path / "spill"
    engine = TurnEngine(
        provider=_Scripted([
            AssistantTurn(tool_calls=[ToolCall(id="c1", name="big_out", arguments={})], finish_reason="tool_calls"),
            AssistantTurn(text="done", finish_reason="stop"),
        ]),
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        approver=None,
        tool_result_max_bytes=10_000,
        tool_result_spill_dir=spill,
    )
    events = _run(engine, "go")
    assert [e for e in events if e.type == EventType.TOOL_FINISHED][0].data["status"] == "ok"
    tool_msgs = [m for m in engine.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    content = tool_msgs[0]["content"]
    assert len(content.encode()) <= 10_000
    parsed = json.loads(content)
    assert parsed["exit_code"] == 0 and parsed["output"].startswith("row0")
    assert "bytes omitted here" in parsed["output"]
    files = list(spill.iterdir())
    assert len(files) == 1 and files[0].read_text(encoding="utf-8").endswith("row5999")


def test_engine_default_cap_is_ten_thousand_and_zero_disables(tmp_path):
    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    e1 = TurnEngine(provider=_Scripted([]), registry=registry, permissions=permissions, model="m")
    assert e1._tool_result_max_bytes == toolresult.DEFAULT_TOOL_RESULT_MAX_BYTES == 10_000
    e2 = TurnEngine(provider=_Scripted([]), registry=registry, permissions=permissions, model="m", tool_result_max_bytes=0)
    assert e2._tool_result_max_bytes == 0
