"""Persona state: a corrupt file must not brick startup or silently reset choices.

``PersonaRegistry`` is built eagerly by ``SessionManager.__init__``
(manager.py:545), whose ``__init__`` calls ``_load_state()``:

- a torn file raised ``JSONDecodeError`` straight out of the constructor — the
  server did not start;
- valid JSON that is not an object (``[]``, ``null``) raised ``AttributeError``
  on ``.get`` — there was no ``try`` at all;
- ``{"enabled": "notadict"}`` raised ``ValueError`` from ``dict("...")``;
- ``installed_meta`` values that are not objects crashed ``list_all()`` later
  (``"str".get(...)``), i.e. the Personas settings panel.

``save()`` wrote in place, which is what produces the torn file in the first
place, and a torn read silently returned to defaults: user-disabled personas
came back on, the default persona fell back to ``cowork``, and surfaced worker
personas reappeared in the sidebar — with no log and nothing to recover from.

The fix keeps the bytes in a ``personas.json.corrupt`` sibling (never deleted,
never overwritten by a later save) with a warning naming the reason, refuses
wrong-shape fields per field (the readable ones still load), and writes through
a sibling ``.tmp`` renamed over the file — the shape of #949-#954.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from coworker.personas.registry import DEFAULT_PERSONA_ID, PersonaRegistry


def _corrupt_sibling(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".corrupt")


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


def _registry(path: Path) -> PersonaRegistry:
    return PersonaRegistry(state_path=path)


# -- quarantine -----------------------------------------------------------------


def test_a_good_state_file_still_loads_quietly(tmp_path, caplog):
    path = tmp_path / "personas.json"
    path.write_text(
        json.dumps(
            {"enabled": {"code": False}, "surfaced": {"cowork": True}, "default": "cowork"}
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="coworker.personas.registry"):
        reg = _registry(path)
    assert reg.is_enabled("code") is False
    assert reg.is_surfaced("cowork") is True
    assert _warnings(caplog) == []
    assert not _corrupt_sibling(path).exists()


def test_a_torn_state_file_starts_with_defaults_and_keeps_the_bytes(tmp_path, caplog):
    """The crash: a truncated personas.json raised JSONDecodeError out of
    SessionManager.__init__ — the server did not start."""
    body = '{"enabled": {"code": fa'
    path = tmp_path / "personas.json"
    path.write_text(body, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="coworker.personas.registry"):
        reg = _registry(path)  # must not raise

    assert _corrupt_sibling(path).read_text(encoding="utf-8") == body
    assert not path.exists()
    assert any(str(path) in m for m in _warnings(caplog))
    # documented defaults: cowork is the default persona and everything ships on
    assert reg.default_id() == DEFAULT_PERSONA_ID


@pytest.mark.parametrize("body", ["[]", "null", '"hello"'])
def test_json_that_is_not_an_object_is_quarantined(tmp_path, caplog, body):
    """`[]` parses, then `.get` raises AttributeError — there was no try."""
    path = tmp_path / "personas.json"
    path.write_text(body, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="coworker.personas.registry"):
        reg = _registry(path)  # must not raise

    assert _corrupt_sibling(path).read_text(encoding="utf-8") == body
    assert any(str(path) in m for m in _warnings(caplog))
    assert reg.default_id() == DEFAULT_PERSONA_ID


def test_a_wrong_shape_field_is_ignored_not_fatal(tmp_path, caplog):
    """`{"enabled": "notadict"}` used to raise ValueError from dict("...").
    The readable fields still load; only the bad one falls back."""
    path = tmp_path / "personas.json"
    path.write_text(
        json.dumps({"enabled": "notadict", "surfaced": {"code": False}}),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="coworker.personas.registry"):
        reg = _registry(path)  # must not raise

    # the good field survived
    assert reg.is_surfaced("code") is False
    # the bad one fell back to the entry defaults (code ships surfaced)
    assert any("'enabled'" in m for m in _warnings(caplog))


def test_a_non_object_installed_meta_value_does_not_break_list_all(tmp_path, caplog):
    """`installed_meta: {"cowork": "oops"}` crashed list_all() later with
    AttributeError on `"oops".get(...)` — the Personas settings panel."""
    path = tmp_path / "personas.json"
    path.write_text(
        json.dumps({"installed_meta": {"cowork": "oops"}}), encoding="utf-8"
    )

    with caplog.at_level(logging.WARNING, logger="coworker.personas.registry"):
        reg = _registry(path)  # must not raise
        entries = reg.list_all()  # must not raise

    assert entries  # builtins are listed
    assert any("installed_meta" in m for m in _warnings(caplog))


def test_a_non_string_default_is_reset_to_cowork(tmp_path, caplog):
    """A numeric `default` persisted forever through save(); it must not."""
    path = tmp_path / "personas.json"
    path.write_text(json.dumps({"default": 42}), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="coworker.personas.registry"):
        reg = _registry(path)

    assert reg._default == DEFAULT_PERSONA_ID
    assert any("'default'" in m for m in _warnings(caplog))


def test_state_recovers_after_quarantine(tmp_path):
    path = tmp_path / "personas.json"
    path.write_text("[]", encoding="utf-8")

    reg = _registry(path)
    reg.set_enabled("code", False)

    reopened = _registry(path)
    assert reopened.is_enabled("code") is False
    assert _corrupt_sibling(path).read_text(encoding="utf-8") == "[]"


# -- atomic save ----------------------------------------------------------------


def test_save_writes_a_sibling_tmp_not_the_final_path(tmp_path, monkeypatch):
    """The non-atomic write is the root cause of the torn file."""
    path = tmp_path / "personas.json"
    reg = _registry(path)

    written: list[Path] = []
    original = Path.write_text

    def spy(self, *args, **kwargs):
        written.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    reg.set_enabled("code", False)

    assert path not in written
    assert path.with_suffix(path.suffix + ".tmp") in written


def test_a_failed_save_leaves_the_previous_state_intact(tmp_path, monkeypatch):
    """A write that dies half-way must not tear the live file."""
    path = tmp_path / "personas.json"
    reg = _registry(path)
    reg.set_enabled("code", False)  # good file on disk

    original = Path.write_text

    def torn(self, data, **kwargs):
        original(self, data[: len(data) // 2])
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", torn)
    with pytest.raises(OSError):
        reg.set_enabled("code", True)
    monkeypatch.undo()

    reopened = _registry(path)
    assert reopened.is_enabled("code") is False  # previous state survived


def test_save_is_atomic_and_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "personas.json"
    reg = _registry(path)
    reg.set_enabled("code", False)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["enabled"]["code"] is False
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_a_missing_state_file_reads_the_defaults(tmp_path):
    reg = _registry(tmp_path / "personas.json")
    assert reg.default_id() == DEFAULT_PERSONA_ID
    assert reg.is_enabled(DEFAULT_PERSONA_ID) is True
