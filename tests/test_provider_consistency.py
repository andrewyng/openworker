"""Cross-file provider metadata consistency — catches drift between
`providers/registry.py`, `providers/matrix.py`, and `server/manager.py`.

Each file is the single source of truth for one facet; no single test
verifies they still agree after a change.  That's what this file does.
"""

from __future__ import annotations

import ast
from pathlib import Path

from coworker.providers.matrix import MATRIX, models_for_provider
from coworker.providers.registry import DESCRIPTORS, get_descriptor

_PROJECT = Path(__file__).resolve().parents[1]


def _extract_compat_models() -> dict[str, list[str]]:
    """Parse ``COMPAT_MODELS`` from the **source** of
    ``coworker/server/manager.py`` without importing the module (which
    requires the full server dependency tree: fastapi, uvicorn, etc.).

    Returns the same ``{name: [model, ...]}`` dict the class attribute
    would, or raises ``AssertionError`` if the AST shape doesn't match
    expectations (catch structural changes in the source before tests
    silently become no-ops).
    """
    src = (_PROJECT / "coworker" / "server" / "manager.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SessionManager":
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and target.id == "COMPAT_MODELS":
                            d = item.value
                            assert isinstance(d, ast.Dict), "COMPAT_MODELS is not a dict literal"
                            result: dict[str, list[str]] = {}
                            for k, v in zip(d.keys, d.values):
                                assert isinstance(k, ast.Constant) and isinstance(k.value, str)
                                assert isinstance(v, ast.List)
                                result[k.value] = [
                                    e.value for e in v.elts
                                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                                ]
                            return result
    raise AssertionError("COMPAT_MODELS not found in SessionManager class body")


# -- descriptor structure -------------------------------------------------------

def test_descriptor_names_are_unique():
    names = [d.name for d in DESCRIPTORS]
    assert len(names) == len(set(names)), f"duplicate names: {names}"


def test_descriptor_names_are_url_safe():
    for d in DESCRIPTORS:
        assert d.name.isascii() and "/" not in d.name and " " not in d.name, d.name


def test_every_descriptor_has_title_needs_key_and_recommended_model():
    for d in DESCRIPTORS:
        assert d.title, f"{d.name} missing title"
        assert isinstance(d.needs_key, bool), f"{d.name} needs_key not bool"
        if d.needs_key:
            assert d.env_key, f"{d.name} needs_key but no env_key"
            assert d.recommended_model, f"{d.name} needs_key but no recommended_model"


def test_env_key_naming_convention():
    """env_key should be SCREAMING_SNAKE_CASE ending in _API_KEY."""
    for d in DESCRIPTORS:
        if not d.env_key:
            continue
        parts = d.env_key.split("_")
        assert all(p.isupper() and p.isascii() for p in parts), (
            f"{d.name}: env_key '{d.env_key}' not SCREAMING_SNAKE_CASE"
        )
        assert d.env_key.endswith("_API_KEY"), (
            f"{d.name}: env_key '{d.env_key}' doesn't end with _API_KEY"
        )


def test_ollama_has_no_env_key_or_recommended_env_key():
    d = get_descriptor("ollama")
    assert d is not None and d.needs_key is False
    assert d.env_key is None  # keyless provider


def test_openai_descriptor_stands_alone():
    """OpenAI is the default fallback — it gets extra scrutiny."""
    d = get_descriptor("openai")
    assert d is not None
    assert d.name == "openai"
    assert d.recommended_model == "gpt-5.6-sol"
    assert d.env_key == "OPENAI_API_KEY"


# -- descriptor ↔ matrix --------------------------------------------------------

def test_every_compat_descriptor_has_matrix_entries():
    """Every OpenAI-compat / reseller descriptor should have at least one
    model in the matrix keyed by <name>:<model>."""
    for d in DESCRIPTORS:
        if d.name in ("openai", "anthropic", "gemini", "ollama"):
            continue
        prefix = f"{d.name}:"
        matches = [k for k in MATRIX if k.startswith(prefix)]
        assert matches, (
            f"{d.name} has no entries in MATRIX (expected at least one "
            f"keyed '{prefix}...')"
        )


def test_every_prefixed_matrix_key_maps_to_known_descriptor():
    """Every MATRIX id with a colon uses a prefix matching a descriptor name."""
    known = {d.name for d in DESCRIPTORS}
    for mid in MATRIX:
        if ":" not in mid:
            continue
        prefix = mid.split(":", 1)[0]
        assert prefix in known, (
            f"matrix key '{mid}' has prefix '{prefix}' which is not a "
            f"known descriptor name ({sorted(known)})"
        )


def test_bare_matrix_keys_are_openai_models():
    """Ids without a colon route through the OpenAI descriptor."""
    for mid in MATRIX:
        if ":" in mid:
            continue
        # bare ids must at least look like plausible model names
        assert mid and mid[0].isalnum(), f"bare matrix key '{mid}' looks suspicious"


def test_recommended_models_exist_in_matrix():
    """Every descriptor's recommended_model must exist in the matrix (as a
    full routed id or — for openai — as a bare id).  Ollama is excluded:
    its recommended model is a user-provided local model, never curated."""
    for d in DESCRIPTORS:
        if not d.recommended_model or d.name == "ollama":
            continue
        if d.name == "openai":
            assert d.recommended_model in MATRIX, (
                f"openai recommended '{d.recommended_model}' not in MATRIX"
            )
        else:
            full = f"{d.name}:{d.recommended_model}"
            assert full in MATRIX, (
                f"{d.name} recommended '{d.recommended_model}' → full id "
                f"'{full}' not in MATRIX"
            )


def test_models_for_provider_returns_consistent_results():
    """models_for_provider should return the bare ids from the matrix for
    each provider, and every returned id round-trips correctly."""
    for d in DESCRIPTORS:
        if not d.needs_key:
            continue
        bare = models_for_provider(d.name)
        for b in bare:
            if d.name == "openai":
                assert b in MATRIX, (
                    f"openai models_for_provider returned '{b}' not in MATRIX"
                )
            else:
                full = f"{d.name}:{b}"
                assert full in MATRIX, (
                    f"{d.name} models_for_provider returned '{b}' → "
                    f"'{full}' not in MATRIX"
                )


# -- matrix invariants ----------------------------------------------------------

def test_all_matrix_entries_have_tool_calling():
    for mid, entry in MATRIX.items():
        assert entry.caps.tools, f"{mid} has tools=False"
        assert entry.caps.streaming, f"{mid} has streaming=False"


def test_all_matrix_entries_have_labels():
    for mid, entry in MATRIX.items():
        assert entry.label, f"{mid} missing label"
        assert "·" in entry.label, (
            f"{mid}: label '{entry.label}' missing '·' separator"
        )


def test_matrix_size_bounds():
    """Deliberately small: current-gen, agent-capable models only."""
    assert 10 < len(MATRIX) < 60, f"MATRIX has {len(MATRIX)} entries (expect 10–60)"


def test_no_duplicate_labels():
    labels = [e.label for e in MATRIX.values()]
    dupes = {l for l in labels if labels.count(l) > 1}
    assert not dupes, f"duplicate labels: {dupes}"


# -- compat vendor extras (COMPAT_MODELS in manager.py) -------------------------

def test_compat_models_bypass_matrix_correctly():
    """COMPAT_MODELS entries live in ``manager.py`` alongside the matrix
    entries in ``matrix.py`` — they are the union of curated and extra
    suggestions.  This test documents the overlap so when a model
    graduates into the matrix the extra list can be cleaned up (keeping
    it is not wrong, just noise)."""
    extras = _extract_compat_models()
    for name, models in extras.items():
        assert get_descriptor(name) is not None, (
            f"COMPAT_MODELS has key '{name}' with no descriptor"
        )
        for m in models:
            full = f"{name}:{m}" if name != "openai" else m
            if full in MATRIX:
                pass  # graduated — should remove from COMPAT_MODELS


def test_all_compat_models_have_descriptors():
    """Every COMPAT_MODELS key maps to a known descriptor (the reverse
    — every descriptor has an entry — is not required: resellers like
    Together/Fireworks and Meta use ``models_for_provider()`` and have
    no extra suggestions)."""
    extras = _extract_compat_models()
    for name in extras:
        assert get_descriptor(name) is not None, (
            f"COMPAT_MODELS key '{name}' has no matching descriptor"
        )


def test_compat_recommended_models_in_extra_models():
    """Every compat vendor's recommended_model should appear in its
    COMPAT_MODELS list (not just in the matrix)."""
    extras = _extract_compat_models()
    for name in extras:
        d = get_descriptor(name)
        if d and d.recommended_model:
            assert d.recommended_model in extras[name], (
                f"{name}: recommended_model '{d.recommended_model}' not in "
                f"COMPAT_MODELS"
            )


# -- provider-agnostic defaults -------------------------------------------------

def test_default_config_model_is_in_matrix():
    from coworker.config import Config

    default = Config().model
    assert default in MATRIX, (
        f"default model '{default}' from Config is not in MATRIX"
    )


def test_default_models_match_descriptor_recommendations():
    from coworker.config import Config

    cfg = Config()
    d = get_descriptor("openai")
    assert d is not None
    assert cfg.model == d.recommended_model, (
        f"Config default '{cfg.model}' != openai.recommended_model "
        f"'{d.recommended_model}'"
    )
