"""ConversationStore transcript paths must stay inside conv_dir.

A client-minted session_id flows straight into ConversationStore._file() from the REST/WS
routes. Starlette's {session_id} param excludes "/" but not "\\" — a path separator on Windows
— so "..%5C..%5C.." would escape conv_dir and delete()'s unconditional unlink becomes an
arbitrary-delete primitive. These tests pin the store-level guard (platform-independent).
"""

from __future__ import annotations

import pytest

from coworker.conversations import ConversationStore
from coworker.sessions import SessionRecord

HOSTILE_IDS = [
    "../secret",  # posix separator escapes on Linux/macOS
    "..\\..\\secret",  # backslash escapes on Windows
    "sub/evil",  # any embedded separator
    "a.b",  # "." is disallowed — keeps f"{sid}.jsonl" a single filename
    "",
    "x" * 129,  # over the length bound
]


@pytest.mark.parametrize("sid", HOSTILE_IDS)
def test_file_rejects_ids_that_could_escape_conv_dir(tmp_path, sid):
    store = ConversationStore(tmp_path)
    with pytest.raises(ValueError):
        store._file(sid)


def test_delete_with_traversal_id_touches_nothing_and_returns_false(tmp_path):
    store = ConversationStore(tmp_path)
    # A transcript-looking file one level above conv_dir (tmp_path/secret.jsonl).
    secret = tmp_path / "secret.jsonl"
    secret.write_text("keep me", encoding="utf-8")

    # conv_dir is tmp_path/conversations, so "../secret" resolves to the file above — the
    # exact arbitrary-delete the unconditional unlink allowed before the fix.
    assert store.delete("../secret") is False
    assert secret.exists()  # not unlinked


def test_valid_session_id_still_round_trips(tmp_path):
    store = ConversationStore(tmp_path)
    store.save(
        SessionRecord(
            session_id="__task__task-abc123",
            workspace=str(tmp_path),
            model="m",
            mode="interactive",
        )
    )
    assert store.load("__task__task-abc123") is not None
    assert store.delete("__task__task-abc123") is True
    assert store.load("__task__task-abc123") is None
