"""Durable tool-output store, projector, and retrieval tool."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pytest

from coworker.tool_outputs import (
    SessionToolOutputStore,
    ToolOutputPolicy,
    ToolOutputStoreError,
    ToolResultProjector,
    is_valid_output_ref,
    read_tool_output_tool,
    serialize_tool_result,
)


def _policy(**kwargs) -> ToolOutputPolicy:
    base = dict(
        inline_limit_chars=40,
        preview_chars=16,
        read_default_bytes=16,
        read_max_bytes=64,
        max_single_output_bytes=1024,
        max_session_output_bytes=4096,
        min_disk_headroom_bytes=0,
    )
    base.update(kwargs)
    if base["preview_chars"] >= base["inline_limit_chars"]:
        base["preview_chars"] = max(2, base["inline_limit_chars"] // 2)
    return ToolOutputPolicy(**base)


def test_small_result_keeps_exact_python_value(tmp_path):
    store = SessionToolOutputStore(tmp_path, "s", _policy())
    value = {"ok": True, "n": 3}
    projected = ToolResultProjector(store).project("c1", "example", value)
    assert projected.stored is None
    assert projected.model_value is value


def test_string_and_structured_round_trip(tmp_path):
    store = SessionToolOutputStore(tmp_path, "s", _policy(inline_limit_chars=10))
    projector = ToolResultProjector(store)
    for value in ("x" * 50, {"items": list(range(30))}):
        projected = projector.project("call", "tool", value)
        assert projected.stored is not None
        pieces, offset = [], 0
        while True:
            page = store.read(projected.stored.ref, offset)
            pieces.append(page["content"])
            if page["complete"]:
                break
            offset = page["next_offset_bytes"]
        assert "".join(pieces) == serialize_tool_result(value)


def test_large_envelope_has_head_tail_and_omission(tmp_path):
    store = SessionToolOutputStore(
        tmp_path, "s", _policy(inline_limit_chars=600, preview_chars=20)
    )
    raw = "HEAD" + ("m" * 800) + "TAIL"
    projected = ToolResultProjector(store).project("c", "t", raw)
    env = projected.model_value
    assert env["truncated"] is True
    assert env["output_ref_version"] == 1
    assert env["preview"].startswith("HEAD")
    assert env["preview"].endswith("TAIL")
    assert "characters omitted" in env["preview"]
    assert "mmmmmmmmmm" not in env["preview"]


def test_escape_heavy_preview_stays_within_inline_limit(tmp_path):
    policy = _policy(
        inline_limit_chars=600,
        preview_chars=200,
        max_single_output_bytes=10_000,
        max_session_output_bytes=10_000,
    )
    store = SessionToolOutputStore(tmp_path, "s", policy)
    projected = ToolResultProjector(store).project(
        "c",
        "t",
        "\x00🙂" * 1_000,
    )
    assert projected.stored is not None
    assert len(serialize_tool_result(projected.model_value)) <= 600


def test_non_ascii_counts_and_utf8_paging(tmp_path):
    store = SessionToolOutputStore(
        tmp_path,
        "s",
        _policy(inline_limit_chars=5, read_default_bytes=5, read_max_bytes=20),
    )
    text = "é🙂漢字" * 20
    record = store.put("c", "t", text)
    assert record.chars == len(text)
    assert record.bytes == len(text.encode("utf-8"))
    with pytest.raises(ValueError, match="UTF-8"):
        store.read(record.ref, offset_bytes=1)
    pieces, offset = [], 0
    while True:
        page = store.read(record.ref, offset, limit_bytes=7)
        pieces.append(page["content"])
        if page["complete"]:
            break
        offset = page["next_offset_bytes"]
    assert "".join(pieces) == text
    assert page["sha256"] == record.sha256


def test_restart_reopens_same_bytes(tmp_path):
    policy = _policy(inline_limit_chars=8)
    store = SessionToolOutputStore(tmp_path, "session-a", policy)
    projected = ToolResultProjector(store).project(
        "provider/../../id", "example", "Z" * 80
    )
    ref = projected.stored.ref
    reopened = SessionToolOutputStore(tmp_path, "session-a", policy, create=False)
    assert reopened.read(ref)["total_chars"] == 80


def test_invalid_refs_offsets_limits(tmp_path):
    store = SessionToolOutputStore(tmp_path, "s", _policy())
    record = store.put("c", "t", "hello")
    with pytest.raises(ValueError):
        store.read("../../etc/passwd")
    with pytest.raises(ValueError):
        store.read("out_" + "a" * 64)
    with pytest.raises(ValueError):
        store.read(record.ref, offset_bytes=-1)
    with pytest.raises(ValueError):
        store.read(record.ref, limit_bytes=0)
    with pytest.raises(ValueError):
        store.read(record.ref, limit_bytes=10_000)
    with pytest.raises(ValueError):
        store.read(record.ref, offset_bytes=len("hello") + 1)
    with pytest.raises(KeyError):
        store.read("out_" + "0" * 32)
    assert not is_valid_output_ref("out_short")


def test_session_isolation_and_provider_id_independence(tmp_path):
    a = SessionToolOutputStore(tmp_path, "a", _policy())
    b = SessionToolOutputStore(tmp_path, "b", _policy())
    r1 = a.put("../../untrusted", "tool", "one")
    r2 = a.put("../../untrusted", "tool", "two")
    assert r1.ref != r2.ref
    with pytest.raises(KeyError):
        b.read(r1.ref)
    assert "untrusted" not in str(a.directory)
    assert r1.ref.startswith("out_")


def test_concurrent_puts_are_distinct(tmp_path):
    store = SessionToolOutputStore(
        tmp_path, "s", _policy(max_session_output_bytes=100_000)
    )
    refs = []

    def _put(i):
        refs.append(store.put(f"c{i}", "t", f"payload-{i}-" + ("x" * 20)).ref)

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(_put, range(20)))
    assert len(set(refs)) == 20
    assert store.list_references() == set(refs)


def test_quota_and_headroom_failures(tmp_path):
    store = SessionToolOutputStore(
        tmp_path, "s", _policy(max_single_output_bytes=20, max_session_output_bytes=50)
    )
    with pytest.raises(ToolOutputStoreError):
        store.put("c", "t", "x" * 40)
    store.put("c", "t", "x" * 10)
    store.put("c", "t", "y" * 10)
    # Content + metadata for another 40-byte payload exceeds the session budget.
    with pytest.raises(ToolOutputStoreError):
        store.put("c", "t", "z" * 40)
    tight = SessionToolOutputStore(
        tmp_path, "disk", _policy(min_disk_headroom_bytes=10**18)
    )
    with pytest.raises(ToolOutputStoreError, match="headroom"):
        tight.put("c", "t", "small")


def test_atomic_failure_leaves_no_reference(tmp_path):
    store = SessionToolOutputStore(tmp_path, "s", _policy())
    with mock.patch.object(store, "_atomic_write", side_effect=OSError("boom")):
        with pytest.raises(OSError):
            store.put("c", "t", "payload")
    assert store.list_references() == set()


def test_delete_all_and_create_false(tmp_path):
    store = SessionToolOutputStore(tmp_path, "s", _policy())
    store.put("c", "t", "bye")
    path = store.directory
    store.delete_all()
    assert not path.exists()
    with pytest.raises(FileNotFoundError):
        SessionToolOutputStore(tmp_path, "s", create=False)


def test_restrict_to_user_invoked(tmp_path, monkeypatch):
    calls = []

    def fake(path, *, is_dir):
        calls.append((Path(path), is_dir))

    monkeypatch.setattr("coworker.tool_outputs.restrict_to_user", fake)
    SessionToolOutputStore(tmp_path, "s", _policy()).put("c", "t", "hi")
    assert any(is_dir for _, is_dir in calls)
    assert any(not is_dir for _, is_dir in calls)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_posix_modes(tmp_path):
    store = SessionToolOutputStore(tmp_path, "s", _policy())
    record = store.put("c", "t", "secret")
    assert oct(store.directory.stat().st_mode & 0o777) == "0o700"
    assert (
        oct((store.directory / f"{record.ref}.txt").stat().st_mode & 0o777) == "0o600"
    )


def test_read_tool_output_tool_errors(tmp_path):
    store = SessionToolOutputStore(tmp_path, "s", _policy())
    tool = read_tool_output_tool(store)
    assert tool.__aisuite_tool_metadata__.requires_approval is False
    assert tool.__aisuite_tool_metadata__.risk_level == "low"
    bad = tool("not-a-ref")
    assert bad["error_kind"] == "invalid"
    missing = tool("out_" + "0" * 32)
    assert missing["error_kind"] == "missing"


def test_read_tool_adapts_escape_heavy_page_to_inline_limit(tmp_path):
    policy = _policy(
        inline_limit_chars=300,
        read_default_bytes=64,
        read_max_bytes=256,
        max_single_output_bytes=10_000,
    )
    store = SessionToolOutputStore(tmp_path, "s", policy)
    record = store.put("c", "t", "\x00🙂" * 100)
    result = read_tool_output_tool(store)(record.ref, limit_bytes=256)
    assert "content" in result
    assert len(serialize_tool_result(result)) <= policy.inline_limit_chars
    assert result["complete"] is False


def test_projector_never_recursively_stores_read_result(tmp_path):
    store = SessionToolOutputStore(tmp_path, "s", _policy(inline_limit_chars=80))
    huge = {"content": "Y" * 200}
    projected = ToolResultProjector(store).project("c", "read_tool_output", huge)
    assert projected.stored is None
    assert projected.model_value["error_kind"] == "limit"
    assert store.list_references() == set()


def test_corrupt_metadata_is_controlled_error(tmp_path):
    store = SessionToolOutputStore(tmp_path, "s", _policy())
    record = store.put("c", "t", "hello")
    (store.directory / f"{record.ref}.json").write_text("{", encoding="utf-8")
    with pytest.raises(ToolOutputStoreError, match="metadata is corrupt"):
        store.read(record.ref)
    result = read_tool_output_tool(store)(record.ref)
    assert result["error_kind"] == "corrupt"


def test_corrupt_content_length_is_controlled_error(tmp_path):
    store = SessionToolOutputStore(tmp_path, "s", _policy())
    record = store.put("c", "t", "hello")
    (store.directory / f"{record.ref}.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(ToolOutputStoreError, match="content is corrupt"):
        store.read(record.ref)
