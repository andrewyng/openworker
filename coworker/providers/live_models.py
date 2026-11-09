"""The models a provider serves right now, asked with the user's own key (OPE-252, layer 1).

The curated catalog (``model_catalog.json``) says what we vouch for; this module says what the
user's key can actually call today, straight from the provider. Nothing here talks to anyone
but the provider the user configured, and nothing is cached across installs: the manager
keeps one small JSON file per machine with the last listing per provider, so "new since last
check" and "not available on this key" can be answered without a request.

What a listing carries differs by provider. OpenRouter, Together, Groq, Fireworks, Mistral,
Gemini and Cohere include the context window and, some of them, tool and image support;
OpenAI, Anthropic and the OpenAI-compatible vendors give ids and names only. Whatever a
listing carries is kept as a fact of source ``provider``; a model with no fact from the
catalog or the listing is ``unverified`` and gets conservative defaults.

A few providers have no listing to ask: the ChatGPT subscription backend, Bedrock and Vertex
(per-region, credentialed differently) and BytePlus Ark. They answer ``listing: none`` and
the card falls back to the curated rows.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from . import probe_http

LISTING_TIMEOUT = 8.0

# Providers whose model list cannot be asked for (and why, for the card).
NO_LISTING: dict[str, str] = {
    "openai-codex": "The ChatGPT subscription backend publishes no model list; the curated rows are shown.",
    "bedrock": "Bedrock lists models per region with AWS credentials; the curated rows are shown.",
    "vertex": "Vertex AI lists models per project and region; the curated rows are shown.",
    "ark": "BytePlus Ark publishes no model list; the curated rows are shown.",
    "ark-agent-plan-cn": "BytePlus Ark publishes no model list; the curated rows are shown.",
}

# Ids that are not chat models whatever the provider says (embeddings, speech, images,
# moderation, rerankers); a picker row for them would only fail in a session.
_NOT_CHAT = ("embed", "tts", "whisper", "dall-e", "moderation", "rerank", "transcri", "image-", "-image", "audio", "sora", "realtime")


@dataclass(frozen=True)
class LiveModel:
    id: str
    name: str
    context: Optional[int] = None
    tools: Optional[bool] = None
    vision: Optional[bool] = None
    created: Optional[str] = None  # ISO date, when the listing says


@dataclass
class Listing:
    provider: str
    ok: bool
    models: list[LiveModel]
    error: Optional[str] = None
    status: Optional[int] = None


# -- the request ---------------------------------------------------------------------------


def key_for(profile: dict[str, Any], env_key: Optional[str]) -> str:
    """The key a session would use: the stored one, else the provider's environment variable
    (the same order as resolve_api_key in every provider)."""
    stored = str((profile or {}).get("api_key") or "").strip()
    if stored:
        return stored
    return str(os.environ.get(env_key) or "").strip() if env_key else ""


def listing_request(name: str, profile: dict[str, Any], descriptor: Any) -> Optional[dict[str, Any]]:
    """url / headers / params for a provider's model list, or None when it has none."""
    if name in NO_LISTING or descriptor is None or getattr(descriptor, "kind", "") == "local":
        return None
    profile = profile or {}
    key = key_for(profile, getattr(descriptor, "env_key", None))
    if name == "anthropic":
        return {
            "url": "https://api.anthropic.com/v1/models",
            "headers": {"x-api-key": key, "anthropic-version": "2023-06-01"},
            "params": {"limit": "1000"},
        }
    if name == "gemini":
        return {
            "url": "https://generativelanguage.googleapis.com/v1beta/models",
            "headers": {},
            "params": {"key": key, "pageSize": "1000"},
        }
    if name == "azure":
        from .registry import normalize_azure_endpoint

        endpoint = normalize_azure_endpoint(profile.get("endpoint"))
        if not endpoint:
            return None
        return {"url": endpoint + "/models", "headers": {"Authorization": f"Bearer {key}"}, "params": None}
    if name == "openrouter-account":
        base = "https://openrouter.ai/api/v1"
    else:
        default = next((f.default for f in getattr(descriptor, "fields", []) if f.key == "base_url" and f.default), "")
        base = str(profile.get("base_url") or "").strip().rstrip("/") or str(default).rstrip("/")
        if not base:
            base = "https://api.openai.com/v1" if name == "openai" else ""
    if not base:
        return None
    return {"url": base + "/models", "headers": {"Authorization": f"Bearer {key}"} if key else {}, "params": None}


# -- the answer ----------------------------------------------------------------------------


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value > 0 else None


def _first_int(item: dict[str, Any], *keys: str) -> Optional[int]:
    for key in keys:
        if "." in key:
            head, tail = key.split(".", 1)
            inner = item.get(head)
            value = inner.get(tail) if isinstance(inner, dict) else None
        else:
            value = item.get(key)
        got = _int(value)
        if got:
            return got
    return None


def _date(value: Any) -> Optional[str]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        try:
            return time.strftime("%Y-%m-%d", time.gmtime(value))
        except (OverflowError, ValueError, OSError):
            return None
    if isinstance(value, str) and len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return None


def _row(name: str, item: dict[str, Any]) -> Optional[LiveModel]:
    raw_id = item.get("id") if isinstance(item.get("id"), str) else item.get("name")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return None
    model_id = raw_id.strip()
    if name == "gemini":
        model_id = model_id[len("models/"):] if model_id.startswith("models/") else model_id
        methods = item.get("supportedGenerationMethods")
        if isinstance(methods, list) and "generateContent" not in methods:
            return None
    endpoints = item.get("endpoints")
    if isinstance(endpoints, list) and endpoints and "chat" not in endpoints:
        return None  # Cohere: embed / rerank models say so
    kind = item.get("type")
    if isinstance(kind, str) and kind not in ("chat", "model", "language", "llm"):
        return None  # Together: image / embedding / audio rows
    lowered = model_id.lower()
    if any(mark in lowered for mark in _NOT_CHAT):
        return None
    display = item.get("display_name") or item.get("displayName")
    if not isinstance(display, str) or not display.strip():
        display = item.get("name") if isinstance(item.get("name"), str) and item.get("name") != raw_id else None
    tools: Optional[bool] = None
    vision: Optional[bool] = None
    params = item.get("supported_parameters")
    if isinstance(params, list):
        tools = "tools" in params or "tool_choice" in params
    caps = item.get("capabilities")
    if isinstance(caps, dict):
        if isinstance(caps.get("function_calling"), bool):
            tools = caps["function_calling"]
        if isinstance(caps.get("vision"), bool):
            vision = caps["vision"]
    arch = item.get("architecture")
    if isinstance(arch, dict) and isinstance(arch.get("input_modalities"), list):
        vision = "image" in arch["input_modalities"]
    return LiveModel(
        id=model_id,
        name=(display or model_id).strip(),
        context=_first_int(item, "context_length", "context_window", "max_context_length", "inputTokenLimit", "max_input_tokens", "top_provider.context_length"),
        tools=tools,
        vision=vision,
        created=_date(item.get("created")) or _date(item.get("created_at")),
    )


def parse_listing(name: str, payload: Any) -> list[LiveModel]:
    """The chat models in a provider's answer, newest first. Tolerant of the three shapes
    in use: {"data": [...]} (OpenAI and most), {"models": [...]} (Gemini, Cohere), a bare
    list (Together)."""
    if isinstance(payload, dict):
        items = payload.get("data")
        if not isinstance(items, list):
            items = payload.get("models")
    else:
        items = payload
    if not isinstance(items, list):
        return []
    rows: dict[str, LiveModel] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        row = _row(name, item)
        if row is not None and row.id not in rows:
            rows[row.id] = row
    return sorted(rows.values(), key=lambda r: (r.created or "", r.id), reverse=True)


def fetch(name: str, profile: dict[str, Any], descriptor: Any, *, timeout: float = LISTING_TIMEOUT) -> Listing:
    """Ask the provider. Never raises: a provider that cannot be asked, reached or read
    comes back as a Listing with ok=False and a one-line reason."""
    if name in NO_LISTING:
        return Listing(name, False, [], error=NO_LISTING[name])
    request = listing_request(name, profile, descriptor)
    if request is None:
        return Listing(name, False, [], error="This provider has no model list to ask.")
    url = request["url"]
    if request.get("params"):
        from urllib.parse import urlencode

        url = url + "?" + urlencode(request["params"])
    try:
        resp = probe_http.get(url, headers=request["headers"], timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - a listing never raises into the route
        return Listing(name, False, [], error=f"Could not reach the provider: {exc.__class__.__name__}.")
    if resp.status_code in (401, 403):
        return Listing(name, False, [], error="The provider refused the key.", status=resp.status_code)
    if resp.status_code >= 300:
        return Listing(name, False, [], error=f"HTTP {resp.status_code} from the provider's model list.", status=resp.status_code)
    try:
        payload = resp.json()
    except ValueError:
        return Listing(name, False, [], error="The provider's model list was not JSON.", status=resp.status_code)
    return Listing(name, True, parse_listing(name, payload), status=resp.status_code)


# -- facts the rest of the app can read without the manager --------------------------------
#
# The manager writes `live_facts.json` beside its prefs: {full model id: {name, context,
# tools, vision}} for the picker's models that the catalog does not list. The engine's
# window resolution and `capabilities_for` read it through the two helpers below.

_facts_lock = threading.Lock()
_facts_path: Optional[Path] = None
_facts_cache: tuple[float, dict[str, dict[str, Any]]] = (0.0, {})


def facts_path() -> Path:
    global _facts_path
    if _facts_path is None:
        from ..secrets import state_dir

        _facts_path = state_dir() / "live_facts.json"
    return _facts_path


def use_facts_path(path: Optional[Path]) -> None:
    """Tests and the manager: where the facts file lives (None → the state dir)."""
    global _facts_path, _facts_cache
    with _facts_lock:
        _facts_path = path
        _facts_cache = (0.0, {})


def write_facts(facts: dict[str, dict[str, Any]]) -> None:
    path = facts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with _facts_lock:
        global _facts_cache
        _facts_cache = (0.0, {})


def all_facts() -> dict[str, dict[str, Any]]:
    """The facts file, re-read when it changed on disk."""
    global _facts_cache
    path = facts_path()
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {}
    with _facts_lock:
        if _facts_cache[0] == stamp and _facts_cache[1]:
            return dict(_facts_cache[1])
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    facts = {k: v for k, v in doc.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(doc, dict) else {}
    with _facts_lock:
        _facts_cache = (stamp, facts)
    return dict(facts)


def facts_for(model: str) -> dict[str, Any]:
    return dict(all_facts().get(model) or {})


def context_window_for(model: str) -> Optional[int]:
    return _int(facts_for(model).get("context"))


def as_dict(row: LiveModel) -> dict[str, Any]:
    return asdict(row)
