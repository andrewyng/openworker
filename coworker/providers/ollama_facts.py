"""What Ollama knows about the models on this machine, for the models table.

`/api/tags` lists the pulled models with their file size; `/api/show` says what each can
do (`capabilities`: `tools`, `thinking`, `vision`, ...) and the window it was trained for.
OpenWorker used to guess both. Each row also carries how the model sits on this machine
(local_machine.fit_for), the window it would be loaded with (ollama_context.resolve_num_ctx,
or the size the user saved), and the recommended-settings entry if there is one.

Answers are cached for a minute per server: the settings page asks often, the list
changes rarely, and `/api/show` is one request per model.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

import httpx

from .. import model_config
from . import local_machine
from .ollama_context import context_length_from_show, resolve_num_ctx
from .recommended import recommendation_for

CACHE_SECONDS = 60.0
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_lock = threading.Lock()


def native_base(base_url: Optional[str]) -> str:
    base = (base_url or "http://localhost:11434").strip().rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def model_facts(base_url: Optional[str] = None, *, fresh: bool = False) -> list[dict[str, Any]]:
    """One row per pulled model. Empty when Ollama does not answer. Never raises."""
    base = native_base(base_url)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(base)
        if hit and not fresh and now - hit[0] < CACHE_SECONDS:
            return [dict(r) for r in hit[1]]
    rows = _fetch(base)
    with _lock:
        _cache[base] = (now, rows)
    return [dict(r) for r in rows]


def forget(base_url: Optional[str] = None) -> None:
    with _lock:
        _cache.pop(native_base(base_url), None)


def forget_all() -> None:
    with _lock:
        _cache.clear()


def _fetch(base: str) -> list[dict[str, Any]]:
    # Module-level httpx calls, so a test can stand the server in with two fakes.
    try:
        tags = httpx.get(base + "/api/tags", timeout=4.0).json()
    except (httpx.HTTPError, ValueError, OSError):
        return []
    rows = []
    for item in tags.get("models") or []:
        name = item.get("name")
        if not name:
            continue
        show: dict[str, Any] = {}
        try:
            show = httpx.post(base + "/api/show", json={"model": name}, timeout=4.0).json()
        except (httpx.HTTPError, ValueError, OSError):
            show = {}
        rows.append(_row(name, item, show if isinstance(show, dict) else {}))
    return rows


def _row(name: str, tag: dict[str, Any], show: dict[str, Any]) -> dict[str, Any]:
    raw_caps = show.get("capabilities") or tag.get("capabilities")
    caps = [str(c) for c in raw_caps] if isinstance(raw_caps, list) else None
    details = show.get("details") or tag.get("details") or {}
    size = tag.get("size") if isinstance(tag.get("size"), int) else None
    model_id = f"ollama:{name}"
    rec = recommendation_for(model_id)
    context_max = context_length_from_show(show) if show else None
    chosen = model_config.context_size_for(model_id)
    return {
        "model": model_id,
        "name": name,
        "size_bytes": size,
        # None when the server did not say (an older Ollama): not known, not "no".
        "tools": ("tools" in caps) if caps is not None else None,
        "thinking": ("thinking" in caps) if caps is not None else None,
        "vision": ("vision" in caps) if caps is not None else None,
        "remote": bool(tag.get("remote_model") or tag.get("remote_host")),
        "parameter_size": details.get("parameter_size"),
        "quantization": details.get("quantization_level"),
        "context_max": context_max,
        "context": chosen or (resolve_num_ctx(show) if show else None),
        "context_from": "user" if chosen else "machine",
        "fit": "cloud" if tag.get("remote_model") else local_machine.fit_for(size),
        "recommendation": rec.name if rec else None,
    }
