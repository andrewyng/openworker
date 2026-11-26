"""Memory settings: a corrupt file must not silently reset what the user chose.

``MemorySettingsStore`` is read on every session build (``enabled`` /
``user_rules``) and written by the Settings PUT. Its ``_save`` wrote in place,
so an interrupted write left a torn file; ``_load`` then returned ``{}`` with
no log — ``enabled`` fell back to ``True`` (the user's OFF came back on) and
``user_rules`` vanished from every future system prompt.

Worse, ``set()`` is read-modify-write: the next toggle read the torn file as
``{}`` and wrote its patch over the ashes, destroying ``user_rules``
**permanently** — no log, no sibling to recover from.

The fix keeps the bytes in a ``.corrupt`` sibling (never deleted, never
overwritten by a later save) with a warning naming the reason, writes through a
sibling ``.tmp`` renamed over the file, and reads ``enabled`` through the flag
policy (only ``True`` / ``1`` / ``"true"`` / ``"1"`` / ``"yes"``) so
``{"enabled": "false"}`` is off, not on — the #937/#947 trap, in a file only
a hand edit or a non-GUI client can put a string into.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from coworker.memory.settings import MemorySettingsStore


def _corrupt_sibling(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".corrupt")


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


# -- quarantine -----------------------------------------------------------------


def test_a_good_file_still_loads_quietly(tmp_path, caplog):
    path = tmp_path / "memory-settings.json"
    path.write_text(
        json.dumps({"enabled": False, "user_rules": "Reply in Hindi"}), encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING, logger="coworker.memory.settings"):
        store = MemorySettingsStore(path)
        assert store.enabled is False
        assert store.user_rules == "Reply in Hindi"
    assert _warnings(caplog) == []
    assert not _corrupt_sibling(path).exists()


def test_a_torn_file_is_quarantined_with_a_warning(tmp_path, caplog):
    """The bytes an interrupted write left behind are kept, and the warning
    names both the reason and where they went."""
    body = '{"enabled": false, "user_ru'
    path = tmp_path / "memory-settings.json"
    path.write_text(body, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="coworker.memory.settings"):
        store = MemorySettingsStore(path)
        # The file is unreadable, so reads fall back to the documented default
        # (§5.4, on) — with a warning, not in silence.
        assert store.enabled is True

    assert _corrupt_sibling(path).read_text(encoding="utf-8") == body
    assert not path.exists()
    assert any(str(path) in m for m in _warnings(caplog))


@pytest.mark.parametrize("body", ["[]", "null", '"hello"'])
def test_json_that_is_not_an_object_is_quarantined_not_silently_reset(
    tmp_path, caplog, body
):
    path = tmp_path / "memory-settings.json"
    path.write_text(body, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="coworker.memory.settings"):
        store = MemorySettingsStore(path)
        assert store.enabled is True

    assert _corrupt_sibling(path).read_text(encoding="utf-8") == body
    assert any(str(path) in m for m in _warnings(caplog))


def test_a_torn_file_keeps_the_user_rules_recoverable(tmp_path, caplog):
    """The kill shot: a torn write followed by ANY later toggle used to destroy
    ``user_rules`` forever — ``set()`` read ``{}`` and wrote the patch over the
    ashes. The old bytes must survive the next save in the ``.corrupt`` sibling."""
    path = tmp_path / "memory-settings.json"
    store = MemorySettingsStore(path)
    store.set(enabled=False, user_rules="Reply in Hindi")

    text = path.read_text(encoding="utf-8")
    # Crash after the rules text landed but before the document closed: the
    # torn bytes still contain the rules, and they must survive the next save.
    torn = text[: text.rindex("}")]
    path.write_text(torn, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="coworker.memory.settings"):
        MemorySettingsStore(path).set(enabled=False)  # the next toggle

    assert _corrupt_sibling(path).read_text(encoding="utf-8") == torn
    assert "Reply in Hindi" in torn
    assert any(str(path) in m for m in _warnings(caplog))
    # and the fresh file is a complete, valid document
    assert json.loads(path.read_text(encoding="utf-8")) == {"enabled": False}


def test_a_quarantined_file_does_not_block_a_fresh_save(tmp_path):
    path = tmp_path / "memory-settings.json"
    path.write_text("[]", encoding="utf-8")

    store = MemorySettingsStore(path)
    assert store.enabled is True
    store.set(enabled=False, user_rules="Reply in Hindi")

    reopened = MemorySettingsStore(path)
    assert reopened.enabled is False
    assert reopened.user_rules == "Reply in Hindi"
    assert _corrupt_sibling(path).read_text(encoding="utf-8") == "[]"


# -- the flag policy ------------------------------------------------------------


def test_enabled_reads_the_flag_policy_not_python_truthiness(tmp_path):
    """`bool("false")` is True: a string "false" in the file used to read as
    memory ON. Only True/1/"true"/"1"/"yes" are true — the #937/#947 policy."""
    path = tmp_path / "memory-settings.json"

    path.write_text(
        json.dumps({"enabled": "false", "user_rules": "Reply in Hindi"}),
        encoding="utf-8",
    )
    assert MemorySettingsStore(path).enabled is False
    # the user rules survive alongside it
    assert MemorySettingsStore(path).user_rules == "Reply in Hindi"

    for raw, expected in (
        ("true", True),
        ("1", True),
        ("yes", True),
        ("TRUE", True),
        (" false ", False),
        ("no", False),
        (0, False),
        (1, True),
        (2, False),
        (None, False),
    ):
        path.write_text(json.dumps({"enabled": raw}), encoding="utf-8")
        assert MemorySettingsStore(path).enabled is expected, raw


def test_set_coerces_a_string_enabled_with_the_same_policy(tmp_path):
    """The write side had the same trap: set(enabled="false") stored True."""
    path = tmp_path / "memory-settings.json"
    store = MemorySettingsStore(path)
    store.set(enabled="false")
    assert json.loads(path.read_text(encoding="utf-8"))["enabled"] is False
    assert MemorySettingsStore(path).enabled is False

    store.set(enabled="yes")
    assert MemorySettingsStore(path).enabled is True


# -- atomic save ----------------------------------------------------------------


def test_save_writes_a_sibling_tmp_not_the_final_path(tmp_path, monkeypatch):
    """The non-atomic write is the root cause: the final path must never be
    opened for writing — only the .tmp sibling, renamed over it at the end."""
    path = tmp_path / "memory-settings.json"
    store = MemorySettingsStore(path)

    written: list[Path] = []
    original = Path.write_text

    def spy(self, *args, **kwargs):
        written.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    store.set(enabled=False, user_rules="Reply in Hindi")

    assert path not in written
    assert path.with_suffix(path.suffix + ".tmp") in written


def test_a_failed_save_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    """A write that dies half-way must not tear the live file: the torn bytes
    belong to the .tmp sibling, which is cleaned up; the previous file stands."""
    path = tmp_path / "memory-settings.json"
    store = MemorySettingsStore(path)
    store.set(user_rules="keep me")

    original = Path.write_text

    def torn(self, data, **kwargs):
        original(self, data[: len(data) // 2])
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", torn)
    with pytest.raises(OSError):
        store.set(user_rules="new")
    monkeypatch.undo()

    assert MemorySettingsStore(path).user_rules == "keep me"


def test_save_is_atomic_and_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "memory-settings.json"
    store = MemorySettingsStore(path)
    store.set(enabled=False, user_rules="Reply in Hindi")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "enabled": False,
        "user_rules": "Reply in Hindi",
    }
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_a_missing_file_reads_the_documented_defaults(tmp_path):
    store = MemorySettingsStore(tmp_path / "memory-settings.json")
    assert store.enabled is True
    assert store.user_rules == ""
