"""Everything sent and received on a model call (observability E22, technical design §5.2.1).

For every model call the step keeps three things:
- the exchange: the exact request (method, URL, headers, body) and the exact response
  (status, headers, the full body or every stream event), content included, minus secrets.
  Stored as pieces (TD-17): each message, tool definition or response body once, by its
  SHA-256, and the request as a manifest that points at them;
- `provider_meta`: the same responses with content removed, every other field as sent;
- `provider_facts`: fixed names read from `provider_meta`, the same for every provider.

How it is captured: both SDKs the app uses, and the Ollama transport, send their requests
through httpx's `HTTPTransport` (or `httpx2`'s, the fork newer SDKs ship on). Its
`handle_request` is wrapped once. While a model call is being recorded on the current
thread, each request and response it makes is copied into that call's list; on any other
thread, or when nothing is recorded, the wrapper only checks a thread-local and passes the
request through. Calls are recorded on the thread that makes them (the stream thread for a
streamed reply), so two sessions calling at once never mix their requests.

Ported from the Phase 1 lab (`phase-1/lab/raw_capture.py`), where the exact rebuild and the
"nothing missing from the metadata view" checks passed on every live call.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import threading
import zlib
from typing import Any, Callable, Iterator, Optional

import httpx

# -- capture ------------------------------------------------------------------------------

_active = threading.local()
_install_lock = threading.Lock()
_installed = False


def _http_modules() -> list[Any]:
    """httpx, and httpx2 where installed (the fork newer anthropic and openai SDKs use)."""
    mods = [httpx]
    try:
        import httpx2  # type: ignore[import-not-found]

        mods.append(httpx2)
    except ImportError:
        pass
    return mods


def _tee_class(mod: Any) -> type:
    class Tee(mod.SyncByteStream):  # type: ignore[misc, name-defined]
        def __init__(self, inner: Any, sink: bytearray) -> None:
            self.inner, self.sink = inner, sink

        def __iter__(self) -> Iterator[bytes]:
            for chunk in self.inner:
                self.sink.extend(chunk)
                yield chunk

        def close(self) -> None:
            close = getattr(self.inner, "close", None)
            if close:
                close()

    return Tee


def install() -> None:
    """Wrap `HTTPTransport.handle_request` once per process. Safe to call many times."""
    global _installed
    with _install_lock:
        if _installed:
            return
        for mod in _http_modules():
            original = mod.HTTPTransport.handle_request
            tee = _tee_class(mod)

            def handle(self: Any, request: Any, _original: Any = original, _tee: type = tee) -> Any:
                log = getattr(_active, "log", None)
                response = _original(self, request)
                if log is None:
                    return response
                try:
                    body = bytes(request.content or b"")
                except Exception:  # noqa: BLE001 - a streamed request body cannot be re-read
                    body = b""
                entry: dict[str, Any] = {
                    "url": str(request.url),
                    "method": request.method,
                    "status": response.status_code,
                    "request_headers": {
                        k: v for k, v in request.headers.items() if not is_secret_header(k)
                    },
                    "request_body": body,
                    "response_headers": dict(response.headers.items()),
                    "_body": bytearray(),
                }
                response.stream = _tee(response.stream, entry["_body"])
                log.append(entry)
                return response

            mod.HTTPTransport.handle_request = handle
        _installed = True


class HttpCapture:
    """The HTTP exchanges of one model call. Capture is switched on only inside `active()`,
    so a stream left half-read (Stop pressed) never keeps capturing on its thread: the
    recording view reads each stream piece inside `active()`."""

    def __init__(self) -> None:
        install()
        self._log: list[dict[str, Any]] = []

    @contextlib.contextmanager
    def active(self) -> Iterator[None]:
        outer = getattr(_active, "log", None)
        _active.log = self._log
        try:
            yield
        finally:
            _active.log = outer

    def entries(self) -> list[dict[str, Any]]:
        """Every exchange so far, with each response body decoded to text."""
        for e in self._log:
            if "_body" in e:
                e["body"] = _decode(bytes(e.pop("_body")), e["response_headers"]).decode(
                    "utf-8", "replace"
                )
        return self._log


def _decode(data: bytes, headers: dict[str, str]) -> bytes:
    """The transport sees the body as sent on the wire, before content decoding."""
    enc = next((v for k, v in headers.items() if k.lower() == "content-encoding"), "").lower()
    try:
        if enc == "gzip":
            return zlib.decompress(data, 16 + zlib.MAX_WBITS)
        if enc == "deflate":
            return zlib.decompress(data)
        if enc == "br":
            import brotli  # type: ignore[import-not-found]

            return brotli.decompress(data)
        if enc == "zstd":
            import zstandard  # type: ignore[import-not-found]

            return zstandard.ZstdDecompressor().decompressobj().decompress(data)
    except Exception:  # noqa: BLE001 - keep the raw bytes
        return data
    return data


# -- secrets --------------------------------------------------------------------------------

SECRET_HEADERS = {
    "set-cookie", "cookie", "authorization", "x-api-key", "x-goog-api-key", "api-key",
    "proxy-authorization",
}


def is_secret_header(name: str) -> bool:
    n = name.lower()
    return n in SECRET_HEADERS or "token" in n or n.endswith("-key") or "secret" in n


def _public_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if not is_secret_header(k)}


# -- which wire -----------------------------------------------------------------------------

def wire_of_url(url: str) -> str:
    u = url.split("?", 1)[0].rstrip("/")
    if u.endswith("/v1/messages"):
        return "anthropic"
    if u.endswith("/responses"):
        return "openai_responses"
    if u.endswith("/v1/decisions"):
        return "openai_decisions"
    if u.endswith("/v1/systemone"):
        return "typesafe_jev"
    if u.endswith("/api/chat") or u.endswith("/api/show"):
        return "ollama"
    if ":generateContent" in u or ":streamGenerateContent" in u:
        return "gemini"
    return "openai_chat"


def is_auxiliary(url: str) -> bool:
    """A helper request made before the call, not an attempt of it (M-R9): Ollama's /api/show."""
    return url.split("?", 1)[0].rstrip("/").endswith("/api/show")


# -- provider_meta: everything sent, minus content and secrets ------------------------------
#
# A short DENY list, not a list of fields to keep: whatever a provider sends that is not on
# it is saved, including fields it adds later. Paths match an event's flattened field paths;
# "*" applies to every event kind of that wire.

CONTENT_PATHS: dict[str, dict[str, list[str]]] = {
    "anthropic": {
        "message_start": ["message.content"],
        "content_block_start": ["content_block.text", "content_block.thinking",
                                "content_block.signature", "content_block.data",
                                "content_block.input"],
        "content_block_delta": ["delta.text", "delta.partial_json", "delta.thinking",
                                "delta.signature"],
        "message": ["content"],
    },
    "openai_responses": {
        "*": ["delta", "text", "arguments", "part.text", "item.content", "item.arguments",
              "item.summary", "item.encrypted_content", "response.output",
              "response.instructions", "response.tools", "response.text.format.schema",
              "output", "instructions", "tools"],
    },
    "openai_decisions": {"*": ["answers"]},
    "typesafe_jev": {"*": ["answers"]},
    "ollama": {"*": ["message.content", "message.thinking", "message.tool_calls",
                     "message.images"]},
    "openai_chat": {
        "*": ["choices[].delta.content", "choices[].delta.reasoning",
              "choices[].delta.reasoning_content", "choices[].delta.reasoning_details",
              "choices[].delta.tool_calls", "choices[].message", "choices[].logprobs"],
    },
    "gemini": {"*": ["candidates[].content"]},
}

# Request fields that are content (the conversation, tool definitions, system text, a
# decision reviewer's action description and question wording).
_REQUEST_CONTENT = ("messages", "input", "tools", "system", "instructions", "state",
                    "questions", "contents", "systemInstruction")


def request_params(body: bytes) -> dict[str, Any]:
    """Every request parameter except content; `tools_offered` as a count."""
    try:
        parsed = json.loads(body or b"{}")
    except Exception:  # noqa: BLE001 - not JSON
        return {}
    if not isinstance(parsed, dict):
        return {}
    out = {k: v for k, v in parsed.items() if k not in _REQUEST_CONTENT}
    out["tools_offered"] = len(parsed.get("tools") or [])
    if isinstance(parsed.get("questions"), list):
        out["questions_asked"] = len(parsed["questions"])
        out["question_names"] = [
            q.get("name") for q in parsed["questions"] if isinstance(q, dict) and q.get("name")
        ] or None
    return out


def parse_events(body: str) -> list[Any]:
    """Every JSON object in a body: plain JSON, server-sent events, or one object per line."""
    body = body.strip()
    if body.startswith("{") or body.startswith("["):
        try:
            parsed = json.loads(body)
            return parsed if isinstance(parsed, list) else [parsed]
        except ValueError:
            pass
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        if all(ln.startswith("{") for ln in lines):
            events: list[Any] = []
            for ln in lines:
                try:
                    events.append(json.loads(ln))
                except ValueError:
                    events.append({"_unparsed": ln[:200]})
            return events
    events = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data = line[5:].strip()
            if data and data != "[DONE]":
                try:
                    events.append(json.loads(data))
                except ValueError:
                    events.append({"_unparsed": data[:200]})
    return events


def event_kind_of(wire: str, ev: dict[str, Any]) -> str:
    if wire == "openai_chat":
        return "chunk with usage" if ev.get("usage") else "chunk"
    if wire == "ollama":
        return "done" if ev.get("done") else "chunk"
    if wire == "gemini":
        return "chunk with usage" if ev.get("usageMetadata") else "chunk"
    return str(ev.get("type", "response"))


def is_content(wire: str, kind: str, path: str) -> bool:
    rules = CONTENT_PATHS.get(wire, {})
    for pat in rules.get("*", []) + rules.get(kind, []):
        if path == pat or path.startswith(pat + ".") or path.startswith(pat + "["):
            return True
    return False


def _strip(obj: Any, wire: str, kind: str, prefix: str = "") -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if is_content(wire, kind, p):
                continue
            out[k] = _strip(v, wire, kind, p)
        return out
    if isinstance(obj, list):
        return [_strip(v, wire, kind, f"{prefix}[]") for v in obj]
    return obj


def _merge(into: dict[str, Any], new: dict[str, Any]) -> None:
    """Deep merge, later values win (stream counters end at their final value)."""
    for k, v in new.items():
        old = into.get(k)
        if isinstance(v, dict) and isinstance(old, dict):
            _merge(old, v)
        elif isinstance(v, list) and isinstance(old, list) and all(isinstance(x, dict) for x in old + v):
            # Stream pieces repeat lists such as `choices`: merge item by item, so a field
            # sent only in the first piece is not lost to a later one.
            for i, item in enumerate(v):
                if i < len(old):
                    _merge(old[i], item)
                else:
                    old.append(item)
        elif v is not None or k not in into:
            into[k] = v


def provider_meta(entry: dict[str, Any]) -> dict[str, Any]:
    """Everything one HTTP response told us, as sent, minus content and secrets."""
    wire = wire_of_url(entry["url"])
    aux = is_auxiliary(entry["url"])
    events: dict[str, Any] = {}
    counts: dict[str, int] = {}
    for ev in parse_events(entry.get("body", "")):
        if not isinstance(ev, dict):
            continue
        kind = "response" if aux else event_kind_of(wire, ev)
        counts[kind] = counts.get(kind, 0) + 1
        _merge(events.setdefault(kind, {}), _strip(ev, wire, kind))
    out: dict[str, Any] = {
        "wire": wire,
        "status": entry["status"],
        "request": request_params(entry.get("request_body") or b""),
        "headers": _public_headers(entry.get("response_headers", {})),
        "event_counts": counts,
        "events": events,
    }
    if aux:
        out["auxiliary"] = True
    return out


# -- provider_facts: the same names for every provider (§5.2.1, recording-format §6.4) ------

FACT_NAMES = (
    "served_model", "provider_request_id", "provider_response_id", "served_by", "reasoning",
    "cache_read", "cache_write", "cache_write_by_ttl", "provider_total", "requested",
    "applied", "stop", "provider_timing", "rate_limit", "hosted_tools", "cost_reported",
)


def _get(d: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(d, list):
            d = d[0] if d else None
        if not isinstance(d, dict):
            return None
        d = d.get(part)
    return d


def provider_facts(meta: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Fixed-name facts read from one `provider_meta` entry. None = the provider did not send it."""
    if not meta:
        return {}
    w, ev, h, req = meta.get("wire"), meta.get("events", {}), meta.get("headers", {}), meta.get("request", {})
    hl = {k.lower(): v for k, v in h.items()}
    if w == "anthropic":
        msg = _get(ev, "message_start.message") or ev.get("message") or {}
        fin = ev.get("message_delta", {})
        u = {**(msg.get("usage") or {}), **(fin.get("usage") or {})}
        stop_src = fin.get("delta") or msg
        facts = {
            "served_model": msg.get("model"), "provider_response_id": msg.get("id"),
            "provider_request_id": hl.get("request-id"),
            "reasoning": _get(u, "output_tokens_details.thinking_tokens"),
            "cache_read": u.get("cache_read_input_tokens"),
            "cache_write": u.get("cache_creation_input_tokens"),
            "cache_write_by_ttl": u.get("cache_creation"), "provider_total": None,
            "requested": {"thinking": req.get("thinking"), "effort": _get(req, "output_config.effort"),
                          "max_tokens": req.get("max_tokens"), "temperature": req.get("temperature"),
                          "service_tier": req.get("service_tier")},
            "applied": {"service_tier": u.get("service_tier"), "speed": u.get("speed"),
                        "inference_geo": u.get("inference_geo")},
            "stop": {"reason": stop_src.get("stop_reason"), "sequence": stop_src.get("stop_sequence"),
                     "details": stop_src.get("stop_details")},
            "provider_timing": {"server_timing": hl.get("server-timing")},
            "hosted_tools": u.get("server_tool_use"), "cost_reported": None,
        }
    elif w == "openai_responses":
        r = (ev.get("response.completed") or ev.get("response.incomplete")
             or ev.get("response.failed") or {}).get("response") or ev.get("response") or {}
        u = r.get("usage") or {}
        facts = {
            "served_model": r.get("model"), "provider_response_id": r.get("id"),
            "provider_request_id": hl.get("x-request-id"),
            "reasoning": _get(u, "output_tokens_details.reasoning_tokens"),
            "cache_read": _get(u, "input_tokens_details.cached_tokens"),
            "cache_write": _get(u, "input_tokens_details.cache_write_tokens"),
            "cache_write_by_ttl": None, "provider_total": u.get("total_tokens"),
            "requested": {"effort": _get(req, "reasoning.effort"), "max_tokens": req.get("max_output_tokens"),
                          "temperature": req.get("temperature"), "service_tier": req.get("service_tier")},
            "applied": {"effort": _get(r, "reasoning.effort"), "service_tier": r.get("service_tier"),
                        "prompt_cache_retention": r.get("prompt_cache_retention"),
                        "truncation": r.get("truncation")},
            "stop": {"reason": r.get("status"), "details": r.get("incomplete_details")},
            "provider_timing": {"processing_ms": hl.get("openai-processing-ms"),
                                "created_at": r.get("created_at"), "completed_at": r.get("completed_at")},
            "hosted_tools": r.get("tool_usage"), "cost_reported": None,
        }
    elif w in ("openai_decisions", "typesafe_jev"):
        r = ev.get("response", {})
        u = r.get("usage") or {}
        facts = {
            "served_model": r.get("model"), "provider_response_id": r.get("id"),
            "provider_request_id": hl.get("x-request-id") or hl.get("x-typesafe-request-id"),
            "cache_read": _get(u, "input_tokens_details.cached_tokens"),
            "cache_write": _get(u, "input_tokens_details.cache_write_tokens"),
            "reasoning": _get(u, "output_tokens_details.reasoning_tokens"),
            "provider_total": u.get("total_tokens"),
            "requested": {"model": req.get("model"), "questions_asked": req.get("questions_asked")},
            "applied": {}, "stop": {}, "cost_reported": None, "hosted_tools": None,
            "provider_timing": {"processing_ms": hl.get("openai-processing-ms"),
                                "server_timing_total": hl.get("server-timing-total")},
        }
    elif w == "ollama":
        # Native /api/chat: the last line (`done: true`) carries the counts and timings, in
        # nanoseconds. `prompt_eval_cached_count` is the part of the prompt reused (V-20).
        d = ev.get("done", {}) or ev.get("response", {})
        opts = req.get("options") or {}
        facts = {
            "served_model": d.get("model"), "provider_response_id": None, "provider_request_id": None,
            "cache_read": d.get("prompt_eval_cached_count"),
            "reasoning": None, "cache_write": None, "cache_write_by_ttl": None, "provider_total": None,
            "requested": {"num_ctx": opts.get("num_ctx"), "max_tokens": opts.get("num_predict"),
                          "temperature": opts.get("temperature"), "think": req.get("think"),
                          "shift": req.get("shift"), "keep_alive": req.get("keep_alive")},
            "applied": {}, "stop": {"reason": d.get("done_reason")},
            "provider_timing": {k: d.get(k) for k in ("total_duration", "load_duration",
                                                      "prompt_eval_duration", "eval_duration",
                                                      "created_at")},
            "hosted_tools": None, "cost_reported": None,
        }
    elif w == "gemini":
        c = {**ev.get("chunk", {}), **ev.get("chunk with usage", {})}
        u = c.get("usageMetadata") or {}
        facts = {
            "served_model": c.get("modelVersion"), "provider_response_id": c.get("responseId"),
            "provider_request_id": None, "reasoning": u.get("thoughtsTokenCount"),
            "cache_read": u.get("cachedContentTokenCount"), "cache_write": None,
            "cache_write_by_ttl": None, "provider_total": u.get("totalTokenCount"),
            "requested": {"generation_config": req.get("generationConfig")}, "applied": {},
            "stop": {"reason": _get(c, "candidates.finishReason")}, "provider_timing": {},
            "hosted_tools": None, "cost_reported": None,
        }
    else:
        c = {**ev.get("chunk", {}), **ev.get("chunk with usage", {}), **ev.get("response", {})}
        u = c.get("usage") or {}
        facts = {
            "served_model": c.get("model"), "provider_response_id": c.get("id"),
            "provider_request_id": hl.get("x-request-id") or hl.get("x-generation-id"),
            "served_by": c.get("provider"),
            "reasoning": _get(u, "completion_tokens_details.reasoning_tokens"),
            "cache_read": _get(u, "prompt_tokens_details.cached_tokens"),
            "cache_write": _get(u, "prompt_tokens_details.cache_write_tokens"),
            "cache_write_by_ttl": None, "provider_total": u.get("total_tokens"),
            "requested": {"effort": req.get("reasoning_effort") or _get(req, "reasoning.effort"),
                          "max_tokens": req.get("max_tokens") or req.get("max_completion_tokens"),
                          "temperature": req.get("temperature"), "service_tier": req.get("service_tier")},
            "applied": {"service_tier": c.get("service_tier"),
                        "system_fingerprint": c.get("system_fingerprint")},
            "stop": {"reason": _get(c, "choices.finish_reason"),
                     "native_reason": _get(c, "choices.native_finish_reason")},
            "provider_timing": {"processing_ms": hl.get("openai-processing-ms")},
            "hosted_tools": None,
            "cost_reported": ({"amount": u.get("cost"), "details": u.get("cost_details"),
                               "is_byok": u.get("is_byok")} if u.get("cost") is not None else None),
        }
    facts["rate_limit"] = {k: v for k, v in h.items() if "ratelimit" in k.lower()} or None
    return {k: facts.get(k) for k in FACT_NAMES}


# -- the exchange, stored as pieces (TD-17) ---------------------------------------------------

# Request fields whose items are stored as separate pieces, shared across a session's calls.
PIECE_FIELDS = ("messages", "input", "tools", "system", "instructions", "contents",
                "questions", "state")


def _piece(value: Any, put: Callable[[bytes], str]) -> dict[str, str]:
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return {"$blob": put(data.encode("utf-8"))}


def store_exchange(entry: dict[str, Any], put: Callable[[bytes], str]) -> dict[str, Any]:
    """One exchange entry (recording-format §6): the request as a manifest of pieces, the
    response body as one piece. `put(bytes) -> sha256` stores a piece once."""
    body: bytes = entry.get("request_body") or b""
    try:
        parsed = json.loads(body) if body else None
    except ValueError:
        parsed = None
    req_body: dict[str, Any]
    if isinstance(parsed, dict):
        manifest = {}
        for k, v in parsed.items():
            if k in PIECE_FIELDS and isinstance(v, list):
                manifest[k] = [_piece(x, put) for x in v]
            elif k in PIECE_FIELDS:
                manifest[k] = _piece(v, put)
            else:
                manifest[k] = v
        req_body = {"manifest": manifest}
    else:
        req_body = {"blob": put(body)} if body else {}
    resp = (entry.get("body") or "").encode("utf-8")
    out: dict[str, Any] = {
        "request": {
            "method": entry["method"], "url": entry["url"],
            "headers": _public_headers(entry.get("request_headers", {})),
            "body_sha256": hashlib.sha256(body).hexdigest(), "body_size": len(body), **req_body,
        },
        "response": {
            "status": entry["status"],
            "headers": _public_headers(entry.get("response_headers", {})),
            "body_blob": put(resp), "body_size": len(resp),
        },
    }
    if is_auxiliary(entry["url"]):
        out = {"auxiliary": True, **out}
    return out


def rebuild_request(exchange: dict[str, Any], get: Callable[[str], Optional[bytes]]) -> Any:
    """The request JSON exactly as sent (or the raw body bytes, for a non-JSON body)."""
    req = exchange["request"]
    if "blob" in req:
        return get(req["blob"])

    def un(v: Any) -> Any:
        if isinstance(v, dict) and set(v) == {"$blob"}:
            return json.loads(get(v["$blob"]) or b"null")
        if isinstance(v, list):
            return [un(x) for x in v]
        return v

    return {k: un(v) for k, v in req.get("manifest", {}).items()}
