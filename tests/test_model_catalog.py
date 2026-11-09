"""OPE-252: the model matrix is a data file, `coworker/providers/model_catalog.json`.

`matrix.py` loads it into MATRIX at import; the four helpers every consumer reads
(`entry_for`, `model_labels`, `model_context_windows`, `models_for_provider`) are unchanged.
These tests pin the file's contract: what it must contain, what the loader refuses, that the
loader fills the same dict object in place (so `from ..matrix import MATRIX` keeps working
after a reload), that OVERRIDES beat the file, that the signed-in OpenRouter provider is a
mirror and never a block of its own, and that the file is in canonical form so a catalog
pull request diff shows only real changes.
"""

from __future__ import annotations

import copy
import importlib.resources
import json
import re
from pathlib import Path

import pytest
from coworker.providers.base import ModelCapabilities
from coworker.providers.capabilities import capabilities_for
from coworker.providers.matrix import (
    CATALOG_PATH,
    MATRIX,
    CatalogError,
    ModelEntry,
    catalog_meta,
    entry_for,
    full_id,
    load_catalog,
    model_context_windows,
    model_labels,
    models_for_provider,
    parse_catalog,
    reload,
    split_id,
)
from coworker.providers import matrix
from coworker.providers.registry import DESCRIPTORS

REPO = Path(__file__).resolve().parents[1]


def _doc() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _row(**over) -> dict:
    row = {
        "label": "Model · Vendor",
        "tools": True,
        "vision": False,
        "pdf": False,
        "parallel_tool_calls": True,
        "context": 128_000,
    }
    row.update(over)
    return row


def _minimal() -> dict:
    """The smallest valid catalog: an OpenAI row (stored bare) and an OpenRouter row (which
    the loader mirrors to openrouter-account)."""
    return {
        "schema": 1,
        "serial": "2026.11.07.1",
        "generated": "2026-11-07",
        "providers": {
            "openai": {"models": {"gpt-x": _row(label="GPT X · OpenAI", vision=True, pdf=True, context=400_000)}},
            "openrouter": {"note": "test rows", "models": {"lab/m": _row(label="M · via OpenRouter", context=None)}},
        },
    }


@pytest.fixture
def restore_matrix():
    """Whatever a test loads, the shipped catalog is back afterwards."""
    yield
    matrix.OVERRIDES.clear()
    reload()


# -- the shipped file -----------------------------------------------------------------------


def test_the_shipped_catalog_is_what_matrix_serves():
    meta, rows = load_catalog(CATALOG_PATH)
    assert meta["schema"] == 1
    assert re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\.\d+", meta["serial"]), meta["serial"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", meta["generated"]), meta["generated"]
    mirrored = {f"openrouter-account:{m[len('openrouter:'):]}" for m in rows if m.startswith("openrouter:")}
    assert set(MATRIX) == set(rows) | mirrored
    assert catalog_meta()["serial"] == meta["serial"]
    assert catalog_meta()["path"] == str(CATALOG_PATH)


def test_every_provider_in_the_file_is_a_registered_cloud_provider():
    known = {d.name for d in DESCRIPTORS if d.kind != "local"}
    unknown = sorted(set(_doc()["providers"]) - known)
    assert not unknown, f"catalog providers the registry does not know: {unknown}"
    assert "openrouter-account" not in _doc()["providers"]  # mirrored, never listed


def test_the_file_is_in_canonical_form():
    """json.dumps(indent=2, ensure_ascii=False) plus one trailing newline, so a catalog pull
    request diffs only the rows that changed, never whitespace."""
    doc = _doc()
    expected = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    assert CATALOG_PATH.read_text(encoding="utf-8") == expected


def test_every_row_is_tool_capable_and_carries_a_label():
    for mid, entry in MATRIX.items():
        assert entry.caps.tools, mid
        assert entry.caps.streaming, mid
        assert entry.label.strip(), mid


def test_pinned_rows_say_why():
    """A pin tells the maintainers' check to report, never propose; the reason must be in
    the file."""
    for provider, block in _doc()["providers"].items():
        for model, row in block["models"].items():
            if row.get("pinned"):
                assert row.get("note", "").strip(), f"{provider}/{model} is pinned without a note"


def test_every_provider_block_names_its_source():
    for provider, block in _doc()["providers"].items():
        assert block.get("note", "").strip(), f"{provider} has no provenance note"


def test_the_catalog_ships_inside_the_package():
    assert importlib.resources.files("coworker.providers").joinpath("model_catalog.json").is_file()
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"providers/*.json"' in pyproject


# -- migration gate (self-retiring) --------------------------------------------------------

SNAPSHOT = Path(__file__).with_name("fixtures") / "model_catalog_snapshot_2026-11-07.json"
MIGRATION_SERIAL = "2026.11.07.1"


def test_the_migration_to_the_data_file_changed_no_fact():
    """The snapshot is MATRIX exactly as the hand-written matrix.py served it on 2026-11-07:
    104 rows (95 in the file plus the openrouter-account mirror) with every id, label,
    capability and window. While the file still carries the migration serial, the loaded
    matrix must equal it. The first weekly sync bumps the serial and retires this gate: from
    then on the file is meant to change, and the snapshot is history, not a contract."""
    if catalog_meta()["serial"] != MIGRATION_SERIAL:
        pytest.skip("the catalog has moved past the migration serial; this gate has done its job")
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert len(snapshot) == 104
    assert set(MATRIX) == set(snapshot)
    for mid, was in snapshot.items():
        entry = MATRIX[mid]
        now = {
            "label": entry.label,
            "tools": entry.caps.tools,
            "vision": entry.caps.vision,
            "pdf": entry.caps.pdf,
            "parallel_tool_calls": entry.caps.parallel_tool_calls,
            "streaming": entry.caps.streaming,
            "context": entry.context_window,
        }
        assert now == was, mid


# -- helpers -------------------------------------------------------------------------------


def test_helpers_agree_with_the_file():
    doc = _doc()
    labels = model_labels()
    windows = model_context_windows()
    for provider, block in doc["providers"].items():
        bare = list(block["models"])
        assert models_for_provider(provider) == bare, provider
        for model, row in block["models"].items():
            mid = full_id(provider, model)
            assert labels[mid] == row["label"]
            assert entry_for(mid).context_window == row["context"]
            assert (mid in windows) == bool(row["context"])
            if row["context"]:
                assert windows[mid] == row["context"]
            caps = entry_for(mid).caps
            assert (caps.vision, caps.pdf, caps.parallel_tool_calls) == (
                row["vision"], row["pdf"], row["parallel_tool_calls"]
            )


def test_openai_rows_are_bare_and_everyone_else_is_prefixed():
    assert all(":" not in m for m in models_for_provider("openai"))
    assert "gpt-5.5" in MATRIX and "openai:gpt-5.5" not in MATRIX
    assert all(mid.startswith("anthropic:") for mid in MATRIX if "claude" in mid and mid.startswith("anthropic"))


def test_openrouter_account_mirrors_openrouter_row_for_row():
    assert models_for_provider("openrouter-account") == models_for_provider("openrouter")
    for model in models_for_provider("openrouter"):
        assert MATRIX[f"openrouter-account:{model}"] == MATRIX[f"openrouter:{model}"]


@pytest.mark.parametrize(
    "provider,model,mid",
    [
        ("openai", "gpt-5.5", "gpt-5.5"),
        ("anthropic", "claude-fable-5-1", "anthropic:claude-fable-5-1"),
        ("bedrock", "claude/anthropic.claude-sonnet-4-6-v1:0", "bedrock:claude/anthropic.claude-sonnet-4-6-v1:0"),
    ],
)
def test_full_id_and_split_id_round_trip(provider, model, mid):
    assert full_id(provider, model) == mid
    assert split_id(mid) == (provider, model)


def test_capabilities_probe_reads_the_file_then_falls_back_to_heuristics():
    assert capabilities_for("anthropic:claude-fable-5-1").pdf is True
    assert capabilities_for("groq:openai/gpt-oss-120b").parallel_tool_calls is False
    assert capabilities_for("anthropic:claude-unlisted-9").tools is True  # heuristic path


def test_unverified_windows_stay_null_and_hidden_from_the_meter():
    assert entry_for("minimax:MiniMax-M2.5").context_window is None
    assert "minimax:MiniMax-M2.5" not in model_context_windows()


# -- reload / overrides --------------------------------------------------------------------


def test_reload_fills_the_same_dict_object_in_place(tmp_path, restore_matrix):
    before = id(MATRIX)
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(_minimal()), encoding="utf-8")
    meta = reload(path)
    assert id(MATRIX) == before
    assert set(MATRIX) == {"gpt-x", "openrouter:lab/m", "openrouter-account:lab/m"}
    assert meta["serial"] == "2026.11.07.1" and meta["path"] == str(path)
    assert models_for_provider("openai") == ["gpt-x"]
    assert model_context_windows() == {"gpt-x": 400_000}
    reload()
    assert "anthropic:claude-fable-5-1" in MATRIX


def test_a_file_that_fails_validation_leaves_matrix_untouched(tmp_path, restore_matrix):
    snapshot = dict(MATRIX)
    bad = _minimal()
    bad["providers"]["openai"]["models"]["gpt-x"]["context"] = -1
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(CatalogError):
        reload(path)
    assert MATRIX == snapshot
    assert catalog_meta()["path"] == str(CATALOG_PATH)


def test_a_missing_or_unparsable_file_is_a_catalog_error(tmp_path):
    with pytest.raises(CatalogError):
        load_catalog(tmp_path / "nope.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(CatalogError):
        load_catalog(broken)


def test_overrides_beat_the_file(restore_matrix):
    matrix.OVERRIDES["gpt-5.5"] = ModelEntry("GPT-5.5 (corrected)", ModelCapabilities(), 7)
    matrix.OVERRIDES["extra:model"] = ModelEntry("Extra")
    reload()
    assert MATRIX["gpt-5.5"].context_window == 7
    assert model_labels()["gpt-5.5"] == "GPT-5.5 (corrected)"
    assert "model" in models_for_provider("extra")


def test_an_override_on_an_openrouter_row_reaches_the_signed_in_twin(restore_matrix):
    matrix.OVERRIDES["openrouter:z-ai/glm-5.3"] = ModelEntry("GLM 5.3 (corrected)", ModelCapabilities(), 131_072)
    reload()
    assert MATRIX["openrouter-account:z-ai/glm-5.3"].context_window == 131_072
    assert MATRIX["openrouter-account:z-ai/glm-5.3"].label == "GLM 5.3 (corrected)"


def test_an_override_written_for_the_twin_itself_still_wins(restore_matrix):
    matrix.OVERRIDES["openrouter:z-ai/glm-5.3"] = ModelEntry("key users", ModelCapabilities(), 1)
    matrix.OVERRIDES["openrouter-account:z-ai/glm-5.3"] = ModelEntry("account users", ModelCapabilities(), 2)
    reload()
    assert MATRIX["openrouter:z-ai/glm-5.3"].context_window == 1
    assert MATRIX["openrouter-account:z-ai/glm-5.3"].context_window == 2


def test_reload_does_not_race_readers(tmp_path, restore_matrix):
    """A daily catalog refresh (PR 3) reloads on one thread while requests read on others:
    no 'dictionary changed size during iteration', and no reader ever sees an empty matrix."""
    import threading

    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(_minimal()), encoding="utf-8")
    stop = threading.Event()
    problems: list = []

    def reader():
        while not stop.is_set():
            try:
                labels = model_labels()
                windows = model_context_windows()
                ids = models_for_provider("openai")
                if not labels or not ids or not windows:
                    problems.append("empty read")
            except RuntimeError as exc:  # pragma: no cover - the failure this test guards
                problems.append(repr(exc))

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    try:
        for _ in range(60):
            reload(path)
            reload()
    finally:
        stop.set()
        for t in threads:
            t.join()
    assert not problems, problems[:3]


def test_snapshot_is_a_copy_that_a_reload_cannot_change(tmp_path, restore_matrix):
    before = matrix.snapshot()
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(_minimal()), encoding="utf-8")
    reload(path)
    assert "anthropic:claude-fable-5-1" in before and "anthropic:claude-fable-5-1" not in MATRIX
    assert before is not MATRIX


# -- validation ----------------------------------------------------------------------------


def _mutate(path: list, value) -> dict:
    """A minimal catalog with one value replaced (`...DELETE` removes the key)."""
    doc = _minimal()
    node = doc
    for key in path[:-1]:
        node = node[key]
    if value == "...DELETE":
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return doc


ROW = ["providers", "openai", "models", "gpt-x"]

INVALID = [
    ("not an object", [], "the catalog must be a JSON object"),
    ("unknown top-level key", ["extra"], "unknown top-level"),
    ("schema 2", ["schema"], "schema 2"),
    ("schema missing", ["schema"], "schema None"),
    ("empty serial", ["serial"], "serial must be"),
    ("generated missing", ["generated"], "generated must be"),
    ("last_checked not a string", ["last_checked"], "last_checked must be"),
    ("providers empty", ["providers"], "providers must be"),
    ("providers not an object", ["providers"], "providers must be"),
    ("provider name with colon", ["providers", "bad:name"], "bad provider name"),
    ("openrouter-account listed", ["providers", "openrouter-account"], "mirrored from openrouter"),
    ("provider block not an object", ["providers", "openai"], "must be an object"),
    ("provider unknown key", ["providers", "openai", "extra"], "unknown field"),
    ("provider note not a string", ["providers", "openai", "note"], "note must be a string"),
    ("models empty", ["providers", "openai", "models"], "models must be"),
    ("model id with spaces", ["providers", "openai", "models", " gpt "], "bad model id"),
    ("model id empty", ["providers", "openai", "models", ""], "bad model id"),
    ("openai id with colon", ["providers", "openai", "models", "openai:gpt"], "stored bare"),
    ("row not an object", ROW, "must be an object"),
    ("row unknown field", ROW + ["speed"], "unknown field"),
    ("row missing field", ROW + ["vision"], "missing field"),
    ("label empty", ROW + ["label"], "label must be"),
    ("label not a string", ROW + ["label"], "label must be"),
    ("tools not a bool", ROW + ["tools"], "tools must be true or false"),
    ("vision not a bool", ROW + ["vision"], "vision must be true or false"),
    ("context zero", ROW + ["context"], "context must be a positive"),
    ("context negative", ROW + ["context"], "context must be a positive"),
    ("context float", ROW + ["context"], "context must be a positive"),
    ("context bool", ROW + ["context"], "context must be a positive"),
    ("context string", ROW + ["context"], "context must be a positive"),
    ("output zero", ROW + ["output"], "output must be a positive"),
    ("note not a string", ROW + ["note"], "note must be a string"),
    ("pinned not a list", ROW + ["pinned"], "pinned must list"),
    ("pinned unknown field", ROW + ["pinned"], "pinned must list"),
    ("pinned duplicate", ROW + ["pinned"], "pinned must list"),
]
VALUES = {
    "not an object": [],
    "unknown top-level key": 1,
    "schema 2": 2,
    "schema missing": "...DELETE",
    "empty serial": "  ",
    "generated missing": "...DELETE",
    "last_checked not a string": 20261107,
    "providers empty": {},
    "providers not an object": [],
    "provider name with colon": {"models": {"m": _row()}},
    "openrouter-account listed": {"models": {"m": _row()}},
    "provider block not an object": [],
    "provider unknown key": 1,
    "provider note not a string": 1,
    "models empty": {},
    "model id with spaces": _row(),
    "model id empty": _row(),
    "openai id with colon": _row(),
    "row not an object": "x",
    "row unknown field": 1,
    "row missing field": "...DELETE",
    "label empty": "  ",
    "label not a string": 3,
    "tools not a bool": "yes",
    "vision not a bool": 1,
    "context zero": 0,
    "context negative": -5,
    "context float": 1.5,
    "context bool": True,
    "context string": "128k",
    "output zero": 0,
    "note not a string": [],
    "pinned not a list": "context",
    "pinned unknown field": ["label"],
    "pinned duplicate": ["context", "context"],
}


@pytest.mark.parametrize("name,path,message", INVALID, ids=[i[0] for i in INVALID])
def test_the_loader_refuses(name, path, message):
    doc = VALUES[name] if not path else _mutate(path, VALUES[name])
    with pytest.raises(CatalogError, match=re.escape(message)):
        parse_catalog(doc)


def test_the_loader_accepts_the_optional_fields():
    doc = _minimal()
    doc["last_checked"] = "2026-11-07"
    row = doc["providers"]["openai"]["models"]["gpt-x"]
    row.update({"output": 128_000, "note": "why", "pinned": ["context", "pdf"]})
    rows = parse_catalog(doc)
    assert rows["gpt-x"].context_window == 400_000
    assert rows["openrouter:lab/m"].context_window is None  # null is "not verified"


def test_parse_does_not_apply_mirrors_or_overrides():
    rows = parse_catalog(_minimal())
    assert "openrouter-account:lab/m" not in rows


def test_the_minimal_catalog_is_valid_and_the_mutations_start_from_it():
    """Guards the test fixture itself: every INVALID case must be a single change away."""
    parse_catalog(_minimal())
    assert copy.deepcopy(_minimal()) == _minimal()
