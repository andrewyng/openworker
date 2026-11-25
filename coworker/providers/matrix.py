"""The curated model matrix — the only models we actively suggest, label, and vouch for.

The rows live in ``model_catalog.json`` next to this file (OPE-252); this module loads them
into ``MATRIX`` and keeps the four helpers every consumer reads. Keyed by the FULL routed id,
exactly as the ProviderRouter receives it — including reseller "ugly names" like
``together:zai-org/GLM-5.2`` (bare ids route to the OpenAI default). Each entry carries the UI
display label and the model's capabilities, making this the single source of truth the
capability probe and the GUI's pickers read from.

Deliberately SMALL (owner call, 2026-07-04): current-generation, agent-capable (tool-calling)
models only. It is not user-editable — users can still add any custom model string, which
falls back to the conservative heuristics in ``capabilities.py`` at their own risk of
degraded results.

Keeping it current is a maintainer job, not a runtime one: the maintainers check the file
against the vendors' published limits and propose edits in a pull request; the app itself
never fetches a catalog from anyone. Each provider's ``note`` in the file names the vendor
page or listing its rows were read from, and when.

Context windows (``context``, tokens) feed the GUI's context-fill meter and compaction. Rows
where the vendor spec wasn't re-checked stay ``null`` — the meter simply hides rather than
showing a made-up denominator, and compaction uses the 128k guess (OPE-170).

``OVERRIDES`` is for a human correction that must beat the file (a wrong window a vendor
page keeps stating): the same shape as a row, applied last. Empty today.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .base import ModelCapabilities

CATALOG_PATH = Path(__file__).with_name("model_catalog.json")
SCHEMA_VERSION = 1

# The signed-in OpenRouter provider serves the same catalog as the key-based one; its rows
# are mirrored at load time and never listed in the file.
_MIRRORS = {"openrouter-account": "openrouter"}

_ROW_KEYS = {
    "label", "tools", "vision", "pdf", "parallel_tool_calls", "context", "output", "note", "pinned",
}
_ROW_REQUIRED = {"label", "tools", "vision", "pdf", "parallel_tool_calls", "context"}
# Fields a maintainer may pin: a maintainers' check reports a differing figure but never
# proposes an edit (a window sized for a pinned host, a capability unverified over this
# backend). The `note` says why.
_PINNABLE = {"context", "tools", "vision", "pdf"}
# `generated` moves when a maintainers' check changes a fact; `last_checked` moves on every
# check. Both are informational to the app.
_TOP_KEYS = {"schema", "serial", "generated", "last_checked", "providers"}
_PROVIDER_KEYS = {"note", "models"}


class CatalogError(ValueError):
    """model_catalog.json is not a catalog this build can read."""


@dataclass(frozen=True)
class ModelEntry:
    label: str  # UI display name, e.g. "GLM-5.2 · via Together"
    caps: ModelCapabilities = ModelCapabilities(
        tools=True, vision=False, parallel_tool_calls=True, streaming=True
    )
    # Max context length in tokens (prompt side), for the GUI's context-fill meter.
    # None = not verified against the vendor spec yet; the meter hides.
    context_window: Optional[int] = None


# Human corrections that beat the file; same keys as MATRIX. Empty today — a row here must
# say in a comment which vendor page it was read from and why the file is wrong.
OVERRIDES: dict[str, ModelEntry] = {}


def full_id(provider: str, model: str) -> str:
    """The routed id a provider's bare model id becomes: OpenAI rows are stored bare (bare
    ids route to the OpenAI default); every other provider is prefixed."""
    return model if provider == "openai" else f"{provider}:{model}"


def split_id(model: str) -> tuple[str, str]:
    """Inverse of :func:`full_id`."""
    if ":" not in model:
        return "openai", model
    provider, bare = model.split(":", 1)
    return provider, bare


def _bool(row: dict[str, Any], key: str, where: str) -> bool:
    value = row[key]
    if not isinstance(value, bool):
        raise CatalogError(f"{where}: {key} must be true or false, got {value!r}")
    return value


def _tokens(row: dict[str, Any], key: str, where: str) -> Optional[int]:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CatalogError(
            f"{where}: {key} must be a positive whole number of tokens or null, got {value!r}"
        )
    return value


def _entry(provider: str, model: str, row: Any) -> ModelEntry:
    where = f"{provider} / {model}"
    if not isinstance(row, dict):
        raise CatalogError(f"{where}: a model must be an object")
    unknown = set(row) - _ROW_KEYS
    if unknown:
        raise CatalogError(f"{where}: unknown field(s) {sorted(unknown)}")
    missing = _ROW_REQUIRED - set(row)
    if missing:
        raise CatalogError(f"{where}: missing field(s) {sorted(missing)}")
    label = row["label"]
    if not isinstance(label, str) or not label.strip():
        raise CatalogError(f"{where}: label must be a non-empty string")
    if "note" in row and not isinstance(row["note"], str):
        raise CatalogError(f"{where}: note must be a string")
    pinned = row.get("pinned", [])
    if (
        not isinstance(pinned, list)
        or any(not isinstance(f, str) or f not in _PINNABLE for f in pinned)
        or len(set(pinned)) != len(pinned)
    ):
        raise CatalogError(f"{where}: pinned must list distinct fields from {sorted(_PINNABLE)}")
    _tokens(row, "output", where)  # validated, not stored: nothing reads it yet
    return ModelEntry(
        label=label,
        caps=ModelCapabilities(
            tools=_bool(row, "tools", where),
            vision=_bool(row, "vision", where),
            pdf=_bool(row, "pdf", where),
            parallel_tool_calls=_bool(row, "parallel_tool_calls", where),
            streaming=True,
        ),
        context_window=_tokens(row, "context", where),
    )


def parse_catalog(doc: Any) -> dict[str, ModelEntry]:
    """Turn a parsed model_catalog.json into MATRIX rows (mirrors and OVERRIDES not applied).
    Raises CatalogError on anything this build cannot read, so a bad file is refused as a
    whole rather than half-loaded."""
    if not isinstance(doc, dict):
        raise CatalogError("the catalog must be a JSON object")
    unknown = set(doc) - _TOP_KEYS
    if unknown:
        raise CatalogError(f"unknown top-level field(s) {sorted(unknown)}")
    if doc.get("schema") != SCHEMA_VERSION:
        raise CatalogError(
            f"schema {doc.get('schema')!r} is not {SCHEMA_VERSION}; this build cannot read it"
        )
    for key in ("serial", "generated"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            raise CatalogError(f"{key} must be a non-empty string")
    if "last_checked" in doc and not isinstance(doc["last_checked"], str):
        raise CatalogError("last_checked must be a string")
    providers = doc.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise CatalogError("providers must be a non-empty object")
    rows: dict[str, ModelEntry] = {}
    for provider, block in providers.items():
        if not isinstance(provider, str) or not provider.strip() or ":" in provider:
            raise CatalogError(f"bad provider name {provider!r}")
        if provider in _MIRRORS:
            raise CatalogError(
                f"{provider} is mirrored from {_MIRRORS[provider]} at load time; do not list it"
            )
        if not isinstance(block, dict):
            raise CatalogError(f"{provider}: must be an object")
        unknown = set(block) - _PROVIDER_KEYS
        if unknown:
            raise CatalogError(f"{provider}: unknown field(s) {sorted(unknown)}")
        if "note" in block and not isinstance(block["note"], str):
            raise CatalogError(f"{provider}: note must be a string")
        models = block.get("models")
        if not isinstance(models, dict) or not models:
            raise CatalogError(f"{provider}: models must be a non-empty object")
        for model, row in models.items():
            if not isinstance(model, str) or not model.strip() or model != model.strip():
                raise CatalogError(f"{provider}: bad model id {model!r}")
            if provider == "openai" and ":" in model:
                raise CatalogError(
                    f"openai / {model}: OpenAI ids are stored bare and may not contain ':'"
                )
            rows[full_id(provider, model)] = _entry(provider, model, row)
    return rows


def load_catalog(path: Path = CATALOG_PATH) -> tuple[dict[str, Any], dict[str, ModelEntry]]:
    """Read and validate a catalog file. Returns (metadata, rows)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CatalogError(f"{path}: {exc}") from exc
    rows = parse_catalog(doc)
    meta = {k: doc.get(k) for k in ("schema", "serial", "generated", "last_checked")}
    return meta, rows


def _assemble(rows: dict[str, ModelEntry]) -> dict[str, ModelEntry]:
    # OVERRIDES go on before the mirror, so a correction to an `openrouter:` row reaches
    # its `openrouter-account:` twin, and again after it, so an override written for the
    # twin itself still wins.
    out = {**rows, **OVERRIDES}
    for mirror, source in _MIRRORS.items():
        prefix = source + ":"
        out.update(
            {
                f"{mirror}:{mid[len(prefix):]}": entry
                for mid, entry in list(out.items())
                if mid.startswith(prefix)
            }
        )
    out.update(OVERRIDES)
    return out


_meta: dict[str, Any] = {}
MATRIX: dict[str, ModelEntry] = {}
# Held while MATRIX is refilled and while the helpers below take their snapshot of it, so
# a reload on one thread (a daily catalog refresh) never races a reader on another.
# Consumers that iterate MATRIX directly should go through the helpers or `snapshot()`.
_lock = threading.RLock()


def reload(path: Path = CATALOG_PATH) -> dict[str, Any]:
    """Load `path` into MATRIX in place (every `from ..matrix import MATRIX` keeps working).
    A file that fails validation leaves MATRIX as it was and raises CatalogError."""
    meta, rows = load_catalog(path)
    assembled = _assemble(rows)
    with _lock:
        MATRIX.clear()
        MATRIX.update(assembled)
        _meta.clear()
        _meta.update(meta, path=str(path))
    return catalog_meta()


def catalog_meta() -> dict[str, Any]:
    """serial / generated / last_checked / path of the loaded catalog (never the rows)."""
    with _lock:
        return dict(_meta)


def snapshot() -> dict[str, ModelEntry]:
    """A copy of MATRIX that a reload cannot change under the caller."""
    with _lock:
        return dict(MATRIX)


reload()


def entry_for(model: str) -> ModelEntry | None:
    return MATRIX.get(model)


def model_labels() -> dict[str, str]:
    """Full-id → display-label map, shipped to the GUI so every picker shows human names."""
    return {mid: e.label for mid, e in snapshot().items()}


def model_context_windows() -> dict[str, int]:
    """Full-id → context-window map (verified entries only), for the GUI's fill meter."""
    return {
        mid: e.context_window for mid, e in snapshot().items() if e.context_window
    }


def models_for_provider(provider: str) -> list[str]:
    """BARE model ids (prefix stripped) the matrix curates for a provider — feeds the
    Settings pane's suggestions and the composer picker so both stay in lockstep with the
    matrix. OpenAI entries are stored without a prefix (bare ids route to the OpenAI
    default), so its list is every un-prefixed id."""
    rows = snapshot()
    if provider == "openai":
        return [mid for mid in rows if ":" not in mid]
    prefix = provider + ":"
    return [mid[len(prefix) :] for mid in rows if mid.startswith(prefix)]
