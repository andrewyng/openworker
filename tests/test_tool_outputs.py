"""Public behavior tests for retained oversized tool output."""

from __future__ import annotations

import sys

import pytest

from coworker.tools.shell import LocalExecutor
from coworker.tool_outputs import (
    SessionToolOutputStore,
    ToolOutputPolicy,
    ToolOutputStoreError,
    ToolResultProjector,
    is_valid_output_ref,
    read_tool_output_tool,
    serialize_tool_result,
)


class _RejectWholeStringEncode(str):
    """Catch regressions that duplicate an entire retained string in memory."""

    def encode(self, *args, **kwargs):
        raise AssertionError("the complete string must not be encoded at once")


def _policy(**overrides: int) -> ToolOutputPolicy:
    values = {
        "inline_limit_chars": 100,
        "preview_chars": 20,
        "read_default_bytes": 16,
        "read_max_bytes": 64,
        "max_single_output_bytes": 1_024,
        "max_session_output_bytes": 4_096,
        "min_disk_headroom_bytes": 0,
    }
    values.update(overrides)
    return ToolOutputPolicy(**values)


def test_small_result_remains_inline_without_changing_its_value(tmp_path):
    store = SessionToolOutputStore(tmp_path, "session-1", _policy())
    value = {"ok": True, "count": 3}

    projected = ToolResultProjector(store).project("call-1", "search", value)

    assert projected.model_value is value
    assert projected.stored is None
    assert store.list_references() == set()


def test_large_result_is_replaced_by_bounded_preview_and_opaque_reference(tmp_path):
    policy = _policy(
        inline_limit_chars=500,
        preview_chars=40,
        max_single_output_bytes=4_096,
        max_session_output_bytes=8_192,
    )
    store = SessionToolOutputStore(tmp_path, "customer/session", policy)
    result = "HEAD" + ("x" * 1_000) + "TAIL"

    projected = ToolResultProjector(store).project(
        "../../provider-call-id",
        "crm_search",
        result,
    )

    assert projected.stored is not None
    assert is_valid_output_ref(projected.stored.ref)
    assert "provider-call-id" not in projected.stored.ref
    assert store.list_references() == {projected.stored.ref}

    envelope = projected.model_value
    assert envelope["output_ref"] == projected.stored.ref
    assert envelope["truncated"] is True
    assert envelope["original_chars"] == len(result)
    assert envelope["preview"].startswith("HEAD")
    assert envelope["preview"].endswith("TAIL")
    assert "characters omitted" in envelope["preview"]
    assert len(serialize_tool_result(envelope)) <= policy.inline_limit_chars


def test_utf8_output_can_be_read_in_exact_byte_pages(tmp_path):
    policy = _policy(read_default_bytes=7, read_max_bytes=20)
    store = SessionToolOutputStore(tmp_path, "session-utf8", policy)
    text = "é🙂漢字" * 10
    record = store.put("call-utf8", "search", text)

    with pytest.raises(ValueError, match="UTF-8"):
        store.read(record.ref, offset_bytes=1, limit_bytes=7)

    pages = []
    next_offset = 0
    while next_offset is not None:
        page = store.read(record.ref, next_offset, limit_bytes=7)
        pages.append(page["content"])
        next_offset = page["next_offset_bytes"]

    assert "".join(pages) == text
    assert page["complete"] is True
    assert page["total_chars"] == len(text)
    assert page["total_bytes"] == len(text.encode("utf-8"))
    assert page["sha256"] == record.sha256


def test_per_result_quota_rejects_output_without_publishing_a_reference(tmp_path):
    policy = _policy(max_single_output_bytes=10)
    store = SessionToolOutputStore(tmp_path, "session-quota", policy)

    with pytest.raises(ToolOutputStoreError, match="per-result quota"):
        store.put("call", "search", "x" * 11)

    assert store.list_references() == set()


def test_put_encodes_large_strings_in_bounded_chunks(tmp_path):
    policy = _policy(
        inline_limit_chars=100,
        max_single_output_bytes=4_096,
        max_session_output_bytes=8_192,
    )
    store = SessionToolOutputStore(tmp_path, "session-streaming", policy)
    value = _RejectWholeStringEncode("x" * 1_000)

    record = store.put("call", "search", value)

    assert record.bytes == 1_000
    assert store.read(record.ref, limit_bytes=64)["content"] == "x" * 64


def test_projector_streams_large_structured_results_without_full_serialization(
    tmp_path, monkeypatch
):
    import coworker.tool_outputs as tool_outputs

    policy = _policy(
        inline_limit_chars=100,
        max_single_output_bytes=4_096,
        max_session_output_bytes=8_192,
    )
    store = SessionToolOutputStore(tmp_path, "session-structured", policy)
    original_serialize = tool_outputs.serialize_tool_result

    def reject_full_result(value):
        if isinstance(value, dict) and "huge_payload" in value:
            raise AssertionError("large structured results must be streamed")
        return original_serialize(value)

    monkeypatch.setattr(
        tool_outputs,
        "serialize_tool_result",
        reject_full_result,
    )

    projected = ToolResultProjector(store).project(
        "call",
        "search",
        {"huge_payload": "x" * 1_000},
    )

    assert projected.stored is not None
    assert projected.model_value["output_ref"] == projected.stored.ref
    pages = []
    offset = 0
    while offset is not None:
        page = store.read(projected.stored.ref, offset, limit_bytes=64)
        pages.append(page["content"])
        offset = page["next_offset_bytes"]
    assert "".join(pages) == original_serialize({"huge_payload": "x" * 1_000})


def test_session_quota_counts_content_and_metadata(tmp_path):
    policy = _policy(
        max_single_output_bytes=20,
        max_session_output_bytes=700,
    )
    store = SessionToolOutputStore(tmp_path, "session-total-quota", policy)
    first = store.put("call-1", "search", "x" * 10)
    second = store.put("call-2", "search", "y" * 10)

    with pytest.raises(ToolOutputStoreError, match="session quota"):
        store.put("call-3", "search", "z" * 10)

    assert store.list_references() == {first.ref, second.ref}


def test_put_rejects_unreadable_oversized_metadata_without_publishing(tmp_path):
    policy = _policy(
        max_single_output_bytes=1_024,
        max_session_output_bytes=256_000,
    )
    store = SessionToolOutputStore(tmp_path, "session-metadata-limit", policy)

    with pytest.raises(ToolOutputStoreError, match="metadata"):
        store.put("call", "x" * 70_000, "retained")

    assert store.list_references() == set()


def test_global_quota_is_shared_across_session_stores(tmp_path):
    policy = _policy(
        max_single_output_bytes=20,
        max_session_output_bytes=4_096,
        max_global_output_bytes=500,
    )
    SessionToolOutputStore(tmp_path, "session-global-one", policy).put(
        "call-1",
        "search",
        "x" * 10,
    )
    second = SessionToolOutputStore(tmp_path, "session-global-two", policy)

    with pytest.raises(ToolOutputStoreError, match="global quota"):
        second.put("call-2", "search", "y" * 10)

    assert second.list_references() == set()


def test_store_rejects_symlinked_session_directory(tmp_path):
    from coworker.tool_outputs import session_output_key

    output_root = tmp_path / "tool-outputs"
    output_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (output_root / session_output_key("session-link")).symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(ToolOutputStoreError, match="unsafe"):
        SessionToolOutputStore(tmp_path, "session-link", _policy())


def test_open_existing_store_rejects_symlinked_output_root(tmp_path):
    from coworker.tool_outputs import session_output_key

    outside = tmp_path / "outside-root"
    (outside / session_output_key("session-root-link")).mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    (state / "tool-outputs").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ToolOutputStoreError, match="unsafe"):
        SessionToolOutputStore(
            state,
            "session-root-link",
            _policy(),
            create=False,
        )


def test_read_rejects_same_length_content_tampering(tmp_path):
    store = SessionToolOutputStore(tmp_path, "session-integrity", _policy())
    record = store.put("call", "search", "hello")
    content_path = store.directory / f"{record.ref}.txt"
    content_path.write_text("jello", encoding="utf-8")

    with pytest.raises(ToolOutputStoreError, match="content is corrupt"):
        store.read(record.ref)


@pytest.mark.parametrize("suffix", [".json", ".txt"])
def test_read_never_follows_symlinked_result_files(tmp_path, suffix):
    store = SessionToolOutputStore(tmp_path, f"session-link-{suffix}", _policy())
    record = store.put("call", "search", "hello")
    managed_path = store.directory / f"{record.ref}{suffix}"
    outside_path = tmp_path / f"outside{suffix}"
    outside_path.write_bytes(managed_path.read_bytes())
    managed_path.unlink()
    managed_path.symlink_to(outside_path)

    with pytest.raises(ToolOutputStoreError, match="unsafe|unavailable"):
        store.read(record.ref)


def test_reopening_store_reconciles_interrupted_writes(tmp_path):
    session_id = "session-reconcile"
    store = SessionToolOutputStore(tmp_path, session_id, _policy())
    record = store.put("call", "search", "hello")
    metadata_path = store.directory / f"{record.ref}.json"
    metadata_path.unlink()
    pending_path = store.directory / ".pending-interrupted"
    pending_path.write_text("partial", encoding="utf-8")

    reopened = SessionToolOutputStore(
        tmp_path,
        session_id,
        _policy(),
        create=False,
    )

    assert reopened.list_references() == set()
    assert not (reopened.directory / f"{record.ref}.txt").exists()
    assert not pending_path.exists()


def test_delete_all_removes_the_session_store(tmp_path):
    store = SessionToolOutputStore(tmp_path, "session-cleanup", _policy())
    store.put("call", "search", "retained")
    directory = store.directory

    store.delete_all()

    assert not directory.exists()
    with pytest.raises(FileNotFoundError):
        SessionToolOutputStore(
            tmp_path,
            "session-cleanup",
            _policy(),
            create=False,
        )


def test_read_tool_output_exposes_bounded_pages_and_controlled_errors(tmp_path):
    store = SessionToolOutputStore(
        tmp_path,
        "session-tool",
        _policy(inline_limit_chars=500),
    )
    record = store.put("call", "search", "abcdefghij")
    read_tool_output = read_tool_output_tool(store)

    page = read_tool_output(record.ref, limit_bytes=5)

    assert page["content"] == "abcde"
    assert page["next_offset_bytes"] == 5
    assert page["complete"] is False
    assert (
        read_tool_output.__coworker_schema__["function"]["name"] == "read_tool_output"
    )
    assert read_tool_output.__aisuite_tool_metadata__.risk_level == "low"
    assert read_tool_output("not-a-ref")["error_kind"] == "invalid"
    assert read_tool_output("out_" + ("0" * 32))["error_kind"] == "missing"


def test_read_tool_output_result_is_never_stored_recursively(tmp_path):
    store = SessionToolOutputStore(tmp_path, "session-no-recursion", _policy())
    projected = ToolResultProjector(store).project(
        "call",
        "read_tool_output",
        {"content": "x" * 500},
    )

    assert projected.stored is None
    assert projected.model_value["error_kind"] == "limit"
    assert store.list_references() == set()


def test_preview_discloses_when_the_source_was_already_incomplete(tmp_path):
    policy = _policy(
        inline_limit_chars=500,
        max_single_output_bytes=4_096,
        max_session_output_bytes=8_192,
    )
    store = SessionToolOutputStore(tmp_path, "session-partial", policy)
    result = {
        "output": "x" * 1_000,
        "retained_complete": False,
        "discarded_bytes": 400,
    }

    projected = ToolResultProjector(store).project(
        "call",
        "run_shell",
        result,
    )

    assert projected.stored is not None
    assert projected.stored.content_complete is False
    assert projected.model_value["content_complete"] is False
    assert "not fully recoverable" in projected.model_value["instruction"]


def test_projected_shell_tail_discloses_that_original_output_is_incomplete(tmp_path):
    command = (
        'foreach ($i in 1..1000) { "line$i" }'
        if sys.platform == "win32"
        else "for i in $(seq 1 1000); do echo line$i; done"
    )
    executor = LocalExecutor(cwd=tmp_path, max_output_chars=800, default_timeout=10)
    try:
        shell_result = executor.run(command)
    finally:
        executor.close()
    assert shell_result["truncated"] is True

    policy = _policy(
        inline_limit_chars=500,
        preview_chars=40,
        max_single_output_bytes=4_096,
        max_session_output_bytes=8_192,
    )
    store = SessionToolOutputStore(tmp_path, "session-shell-tail", policy)
    projected = ToolResultProjector(store).project(
        "call-shell",
        "run_shell",
        shell_result,
    )

    assert projected.stored is not None
    assert projected.stored.content_complete is False
    assert projected.model_value["content_complete"] is False
    assert "not fully recoverable" in projected.model_value["instruction"]
