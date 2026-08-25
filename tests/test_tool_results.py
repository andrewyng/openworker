"""Global large-tool-result externalization and bounded retrieval."""

from __future__ import annotations

import json

from coworker.engine import TurnEngine
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.tool_results import MAX_READ_CHARS, ToolResultStore
from coworker.tools import ToolRegistry


class _UnusedProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="unused")

    def capabilities(self, model):
        return ModelCapabilities()


def test_small_results_pass_through_unchanged(tmp_path):
    store = ToolResultStore(tmp_path, inline_chars=100)
    value = {"ok": True, "message": "small"}

    prepared = store.prepare("example", value)

    assert prepared.value is value
    assert prepared.reference is None
    assert not (tmp_path / ".openworker").exists()


def test_large_result_is_externalized_and_pageable(tmp_path):
    store = ToolResultStore(tmp_path, inline_chars=1_000)
    value = {"elements": ["界面控件" * 800, "tail marker"]}

    prepared = store.prepare("computer_snapshot", value)

    assert prepared.externalized is True
    assert prepared.reference
    assert prepared.value["openworker_large_result"] is True
    assert prepared.value["result_ref"] == prepared.reference
    target = tmp_path / prepared.reference
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8")) == value

    reader = store.reader_tool()
    first = reader(prepared.reference, max_chars=64)
    assert first["offset"] == 0
    assert first["next_offset"] <= 64
    assert first["complete"] is False
    second = reader(prepared.reference, offset=first["next_offset"], max_chars=64)
    assert second["offset"] == first["next_offset"]
    assert "�" not in first["content"] + second["content"]


def test_result_reader_rejects_escape_and_caps_output(tmp_path):
    store = ToolResultStore(tmp_path)
    prepared = store.prepare("shell", "x" * (MAX_READ_CHARS + 100))
    reader = store.reader_tool()

    escaped = reader("../outside.txt")
    page = reader(prepared.reference, max_chars=MAX_READ_CHARS * 10)

    assert escaped == {"error": "invalid tool-result reference"}
    assert len(page["content"]) <= MAX_READ_CHARS
    assert page["next_offset"] <= MAX_READ_CHARS


def test_reader_never_externalizes_a_page_recursively(tmp_path):
    store = ToolResultStore(tmp_path, inline_chars=1_000)
    prepared = store.prepare("large", '"\\\n' * 2_000)
    page = store.reader_tool()(prepared.reference, max_chars=MAX_READ_CHARS)

    projected = store.prepare("read_tool_result", page)

    assert "content" in page
    assert projected.reference is None
    assert not (
        isinstance(projected.value, dict)
        and projected.value.get("openworker_large_result")
    )


def test_retention_quota_failure_returns_only_a_bounded_preview(tmp_path):
    store = ToolResultStore(
        tmp_path,
        inline_chars=100,
        max_result_bytes=128,
        max_store_bytes=256,
    )

    prepared = store.prepare("large", "x" * 1_000)

    assert prepared.reference is None
    assert "quota" in prepared.value["storage_error"]
    assert len(json.dumps(prepared.value)) < 2_000


def test_engine_uses_global_result_store_and_invalidates_usage(tmp_path):
    registry = ToolRegistry()
    engine = TurnEngine(
        provider=_UnusedProvider(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="test:model",
    )
    engine.tool_result_store.inline_chars = 100
    engine._last_context_tokens = 99_999
    call = ToolCall(id="call-large", name="computer_snapshot", arguments={})

    event = engine._record_result(call, {"tree": "x" * 5_000}, "ok")

    content = json.loads(engine.messages[-1]["content"])
    assert content["openworker_large_result"] is True
    assert event.data["result_ref"] == content["result_ref"]
    assert engine._last_context_tokens is None
    assert registry.get("read_tool_result") is not None
