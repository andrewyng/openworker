"""Serving context window for local (ollama) models.

The curated matrix covers hosted models only, so an `ollama:*` id resolves to no
`context_window` and compaction falls back to `DEFAULT_CONTEXT_WINDOW` (128k). That
fallback is not merely imprecise, it is unsafe: ollama silently truncates a prompt that
exceeds its serving window, reports the TRUNCATED `prompt_tokens`, and returns no error.
Nothing downstream ever learns the conversation was cut, so an over-large assumption is
never corrected — the run simply loses its earliest turns (its instructions) and drifts.

Measured on a 65,536-token ollama window: 40k in -> 40,019 reported; 90k in -> 32,770
reported, finish_reason "length", no error. The reported figure *falls* once truncation
starts, so it cannot be used to detect the overflow either (see engine._compaction_due,
which now takes the max of reported and locally estimated tokens).

`/api/ps` reports the real serving `context_length` per loaded model, which is the number
compaction must trigger under. Only loaded models appear there; an unloaded model returns
None and the caller keeps its existing fallback.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional

# Native API root (not the /v1 OpenAI-compatible path — /api/ps lives at the root).
_DEFAULT_ROOT = "http://localhost:11434"
# Probed per model id and cached: this is consulted on every compaction check, which runs
# once per turn, and the answer only changes when a model is reloaded.
_TTL_SECONDS = 300.0
_TIMEOUT_SECONDS = 0.75

_cache: dict[str, tuple[float, Optional[int]]] = {}


def _root_url() -> str:
    """Ollama's native API root. OLLAMA_HOST may be a bare `host:port`."""
    raw = (os.environ.get("OLLAMA_HOST") or "").strip().rstrip("/")
    if not raw:
        return _DEFAULT_ROOT
    if not raw.startswith(("http://", "https://")):
        raw = "http://" + raw
    return raw


def _bare_model(model_id: str) -> str:
    """`ollama:qwen3.8-27b:latest` -> `qwen3.8-27b:latest` (the tag keeps its colon)."""
    return model_id.split(":", 1)[1] if ":" in model_id else model_id


def _probe(root: str) -> dict[str, int]:
    """Loaded model name -> serving context_length. Never raises: a local model server
    being down must not break the compaction check that calls this."""
    try:
        req = urllib.request.Request(f"{root}/api/ps", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return {}
    out: dict[str, int] = {}
    for entry in payload.get("models") or []:
        name = entry.get("name") or entry.get("model")
        window = entry.get("context_length")
        if isinstance(name, str) and isinstance(window, int) and window > 0:
            out[name] = window
    return out


def local_context_window(model_id: str) -> Optional[int]:
    """The window ollama is actually serving `model_id` with, or None when unknown
    (not an ollama id, model not loaded, or the server unreachable)."""
    if not model_id.startswith("ollama:"):
        return None
    now = time.monotonic()
    cached = _cache.get(model_id)
    if cached and now - cached[0] < _TTL_SECONDS:
        return cached[1]

    # Belt and braces: `_probe` already swallows the expected network/parse failures, but
    # this is called from the per-turn compaction check — no probe fault of any kind may
    # take down a run. Unknown simply means "keep the caller's fallback".
    try:
        loaded = _probe(_root_url())
    except Exception:
        loaded = {}
    bare = _bare_model(model_id)
    window = loaded.get(bare)
    if window is None and ":" not in bare:
        # `ollama:qwen3.8-27b` should still match a loaded `qwen3.8-27b:latest`.
        window = loaded.get(f"{bare}:latest")
    _cache[model_id] = (now, window)
    return window
