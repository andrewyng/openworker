"""llama.cpp and vLLM: servers the user starts, reached through their OpenAI-compatible
`/v1`. One module for both, since OpenWorker treats them the same way and the two
differ only in what they report about themselves.

- llama.cpp `llama-server`: `localhost:8080` by default; `/v1/models` lists the loaded
  model (its id is the file path unless the server was started with `--alias`), with
  `meta.n_ctx_train` for the trained window; `/props` carries the window the server was
  started with (`default_generation_settings.n_ctx`), which cannot change while it runs.
  Tool calls go through the model's chat template (`--jinja`, on by default).
- vLLM: `localhost:8000` by default, one model per server; `/v1/models` gives the id and
  `max_model_len`. Tool calls work only when the server was started with
  `--enable-auto-tool-choice --tool-call-parser <name>`; the harness cannot turn them on,
  but it can find out, and say so, before a session breaks.

Neither tells the harness what the machine is; that is local_machine.py. Both may take an
API key; without one any value is accepted, and OpenWorker sends a placeholder.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

import httpx

from .. import model_config
from . import local_machine
from .recommended import recommendation_for

LOCAL_SERVERS: dict[str, dict[str, str]] = {
    "llamacpp": {"title": "llama.cpp", "default_base": "http://localhost:8080"},
    "vllm": {"title": "vLLM", "default_base": "http://localhost:8000"},
}
PLACEHOLDER_KEY = "none"
CACHE_SECONDS = 60.0

_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_lock = threading.Lock()


def v1_base(name: str, base_url: Optional[str]) -> str:
    """The server's `/v1` root from what the user typed: the root, or already `/v1`."""
    base = (base_url or LOCAL_SERVERS[name]["default_base"]).strip().rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def _headers(api_key: Optional[str]) -> dict[str, str]:
    key = (api_key or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def alive(name: str, base_url: Optional[str], api_key: Optional[str] = None) -> bool:
    try:
        return httpx.get(v1_base(name, base_url) + "/models", headers=_headers(api_key), timeout=0.8).status_code == 200
    except Exception:  # noqa: BLE001 - a probe never raises
        return False


def model_facts(
    name: str, base_url: Optional[str], api_key: Optional[str] = None, *, fresh: bool = False
) -> list[dict[str, Any]]:
    """One row per model the server serves (usually one). Empty when it does not answer."""
    base = v1_base(name, base_url)
    key = (name, base)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and not fresh and now - hit[0] < CACHE_SECONDS:
            return [dict(r) for r in hit[1]]
    rows = _fetch(name, base, api_key)
    with _lock:
        _cache[key] = (now, rows)
    return [dict(r) for r in rows]


def forget_all() -> None:
    with _lock:
        _cache.clear()


# The server each provider client was built against (registry._local_server), so a
# window lookup from the engine can ask the right server without a profile in hand.
_built: dict[str, tuple[str, Optional[str]]] = {}


def remember_server(name: str, base_url: Optional[str], api_key: Optional[str]) -> None:
    _built[name] = (base_url or "", api_key)


def context_window_for(model: str) -> Optional[int]:
    """The window a served model runs at: from the last facts read, else read now from
    the server the provider was built against. None when unknown or not a local-server
    model."""
    vendor, _, bare = model.partition(":")
    if vendor not in LOCAL_SERVERS:
        return None

    def find(rows: list[dict[str, Any]]) -> Optional[int]:
        for row in rows:
            if row.get("name") == bare and row.get("context"):
                return int(row["context"])
        return None

    with _lock:
        for (name, _base), (_at, rows) in _cache.items():
            if name == vendor:
                found = find(rows)
                if found:
                    return found
    base_url, api_key = _built.get(vendor, ("", None))
    return find(model_facts(vendor, base_url or None, api_key))


def _fetch(name: str, base: str, api_key: Optional[str]) -> list[dict[str, Any]]:
    headers = _headers(api_key)
    try:
        listing = httpx.get(base + "/models", headers=headers, timeout=4.0).json()
    except (httpx.HTTPError, ValueError, OSError):
        return []
    items = listing.get("data") if isinstance(listing, dict) else None
    if not isinstance(items, list):
        return []
    props: dict[str, Any] = {}
    if name == "llamacpp":
        try:
            got = httpx.get(base[: -len("/v1")] + "/props", headers=headers, timeout=4.0).json()
            props = got if isinstance(got, dict) else {}
        except (httpx.HTTPError, ValueError, OSError):
            props = {}
    tools: Optional[bool] = None
    if name == "vllm":
        tools = _vllm_tools_on(base, headers, str((items[0] or {}).get("id") or "")) if items else None
    elif name == "llamacpp":
        template = str(props.get("chat_template") or "")
        tools = ("tools" in template) if template else None
    rows = []
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        rows.append(_row(name, item, props, tools))
    return rows


def _vllm_tools_on(base: str, headers: dict[str, str], model: str) -> Optional[bool]:
    """Ask vLLM for a one-token reply with a tool offered. A server started without tool
    calling refuses the request and names the flag; one with it answers."""
    probe = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
        "tools": [{"type": "function", "function": {"name": "noop", "parameters": {"type": "object", "properties": {}}}}],
    }
    try:
        resp = httpx.post(base + "/chat/completions", json=probe, headers=headers, timeout=30.0)
    except (httpx.HTTPError, OSError):
        return None
    if resp.status_code == 200:
        return True
    text = resp.text.lower()
    if "tool" in text and ("enable-auto-tool-choice" in text or "tool-call-parser" in text or "not supported" in text):
        return False
    return None


def _row(name: str, item: dict[str, Any], props: dict[str, Any], tools: Optional[bool]) -> dict[str, Any]:
    served_id = str(item["id"])
    model_id = f"{name}:{served_id}"
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    if name == "llamacpp":
        context = _int((props.get("default_generation_settings") or {}).get("n_ctx"))
        context_max = _int(meta.get("n_ctx_train"))
        size = _int(meta.get("size"))
    else:
        context = _int(item.get("max_model_len"))
        context_max = context
        size = None
    rec = recommendation_for(model_id)
    chosen = model_config.context_size_for(model_id)
    return {
        "model": model_id,
        "name": served_id,
        "size_bytes": size,
        "tools": tools,
        "thinking": rec.thinking_available if rec else None,
        "vision": None,
        "remote": False,
        "parameter_size": None,
        "quantization": None,
        "context_max": context_max,
        # The server fixes its window at start; a saved size only sizes compaction.
        "context": chosen or context,
        "context_from": "user" if chosen else "server",
        "fit": local_machine.fit_for(size) if size else "unknown",
        "recommendation": rec.name if rec else None,
    }


def _int(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None
