"""Per-model settings the user saves: context size, longest reply, thinking, sampling,
auto-compact, and which model new sessions start with.

One file in the state folder, `model_config.json`, keyed by the full model id
(`ollama:qwen3-coder:30b`, `gpt-5.6-sol`). The desktop app's dialog and the CLI write and
read the same file, so a setting made in one holds in the other.

What a saved value does:
- `context_size`: the window a local model is loaded with (Ollama: `num_ctx`; it also sizes
  compaction). For a cloud model it only sizes compaction, for models the matrix does not
  list.
- `max_output_tokens`: the longest reply, sent as `max_tokens`.
- `thinking`: on or off for models with a thinking switch (Ollama's `think`).
- `reasoning_effort`: the level for models with effort levels.
- `temperature`, `top_p`: sampling.
- `compaction_threshold_pct`: when to auto-compact, as a share of the window; overrides
  the machine-wide setting for this model.
- `default`: true on the model new sessions start with (the picker's default).

Unset values mean "the recommendation, else the provider's default"
(providers/recommended.py). `effective()` shows the merge; `model_settings_for()` turns it
into the keyword arguments every provider call takes.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from .providers.recommended import recommendation_for
from .secrets import state_dir

FILE_NAME = "model_config.json"

# The keys a record may hold, with how each value is checked.
_INT_KEYS = ("context_size", "max_output_tokens")
_FLOAT_KEYS = ("temperature", "top_p", "compaction_threshold_pct")
_BOOL_KEYS = ("thinking", "default")
_STR_KEYS = ("reasoning_effort",)
KEYS = _INT_KEYS + _FLOAT_KEYS + _BOOL_KEYS + _STR_KEYS

_lock = threading.Lock()


def _path() -> Path:
    return state_dir() / FILE_NAME


def load() -> dict[str, dict[str, Any]]:
    """Every saved record, keyed by model id. An unreadable file reads as empty."""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): {kk: vv for kk, vv in v.items() if kk in KEYS}
        for k, v in raw.items()
        if isinstance(v, dict)
    }


def _save(records: dict[str, dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def get(model: str) -> dict[str, Any]:
    """The saved record for one model (empty when none)."""
    return load().get(model, {})


def clean(values: dict[str, Any]) -> tuple[dict[str, Any], Optional[str]]:
    """Check and normalise a record. Returns (record, error). A key set to None
    removes that setting."""
    out: dict[str, Any] = {}
    for key, value in (values or {}).items():
        if key not in KEYS:
            return {}, f"unknown setting {key!r}"
        if value is None:
            out[key] = None
            continue
        if key in _INT_KEYS:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                return {}, f"{key} must be a positive whole number"
            out[key] = value
        elif key in _FLOAT_KEYS:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return {}, f"{key} must be a number"
            if key == "compaction_threshold_pct" and not 0.10 <= float(value) <= 0.95:
                return {}, "compaction_threshold_pct must be between 0.10 and 0.95"
            if key == "temperature" and not 0 <= float(value) <= 2:
                return {}, "temperature must be between 0 and 2"
            if key == "top_p" and not 0 < float(value) <= 1:
                return {}, "top_p must be between 0 and 1"
            out[key] = float(value)
        elif key in _BOOL_KEYS:
            if not isinstance(value, bool):
                return {}, f"{key} must be true or false"
            out[key] = value
        else:
            if not isinstance(value, str) or not value.strip():
                return {}, f"{key} must be text"
            out[key] = value.strip()
    return out, None


def set(model: str, values: dict[str, Any]) -> tuple[dict[str, Any], Optional[str]]:  # noqa: A001
    """Merge `values` into the model's record (None removes a key) and save. Setting
    `default` true clears it on every other model. Returns (record, error)."""
    record, error = clean(values)
    if error:
        return {}, error
    with _lock:
        records = load()
        current = dict(records.get(model, {}))
        for key, value in record.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        if current.get("default"):
            for other in records.values():
                other.pop("default", None)
        if current:
            records[model] = current
        else:
            records.pop(model, None)
        _save(records)
    return current, None


def remove(model: str) -> None:
    with _lock:
        records = load()
        if model in records:
            del records[model]
            _save(records)


def default_model() -> Optional[str]:
    for model, record in load().items():
        if record.get("default"):
            return model
    return None


def effective(model: str) -> dict[str, Any]:
    """The settings in force for a model: the saved record over the recommendation.
    Each value says where it came from, for the dialog to show."""
    saved = get(model)
    rec = recommendation_for(model)
    out: dict[str, Any] = {}

    def put(key: str, recommended: Any) -> None:
        if key in saved:
            out[key] = {"value": saved[key], "from": "user"}
        elif recommended is not None:
            out[key] = {"value": recommended, "from": "recommended"}

    put("context_size", None)  # the machine rule fills this in for local models
    put("max_output_tokens", rec.max_output_tokens if rec else None)
    put("thinking", (rec.thinking_default if rec and rec.thinking_available else None))
    put("reasoning_effort", None)
    put("temperature", rec.sampling.get("temperature") if rec else None)
    put("top_p", rec.sampling.get("top_p") if rec else None)
    put("compaction_threshold_pct", None)
    out["default"] = {"value": bool(saved.get("default")), "from": "user"}
    if rec:
        out["recommendation"] = rec.as_dict()
    return out


def model_settings_for(model: str) -> dict[str, Any]:
    """Keyword arguments for the provider call: what the user saved, else what the
    model's maker recommends. Context size and compaction are read elsewhere
    (ollama_context.py, engine._compaction_config)."""
    saved = get(model)
    rec = recommendation_for(model)
    settings: dict[str, Any] = {}
    max_out = saved.get("max_output_tokens") or (rec.max_output_tokens if rec else None)
    if max_out:
        settings["max_tokens"] = int(max_out)
    for key in ("temperature", "top_p"):
        value = saved.get(key)
        if value is None and rec:
            value = rec.sampling.get(key)
        if value is not None:
            settings[key] = float(value)
    if saved.get("reasoning_effort"):
        settings["reasoning_effort"] = saved["reasoning_effort"]
    thinking = saved.get("thinking")
    if thinking is None and rec and rec.thinking_available:
        thinking = rec.thinking_default
    if thinking is not None and "reasoning_effort" not in settings:
        # Ollama's native `think` rides the request body; other providers ignore it.
        settings["extra_body"] = {"think": bool(thinking)}
    return settings


def context_size_for(model: str) -> Optional[int]:
    value = get(model).get("context_size")
    return int(value) if value else None


def compaction_threshold_for(model: str) -> Optional[float]:
    value = get(model).get("compaction_threshold_pct")
    return float(value) if value else None
