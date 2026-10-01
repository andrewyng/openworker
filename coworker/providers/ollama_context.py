"""Give Ollama a context window big enough for a Cowork prompt.

Ollama's OpenAI-compatible `/v1/chat/completions` handler has no `num_ctx` field, so a
request inherits the server's VRAM default. On a machine under ~24 GiB that default is
4,096 tokens. A Cowork turn (instructions + tool schemas + the user message) is about
7,000. Ollama then context-shifts the rendered prompt down to roughly half the window
minus a few tokens — 2,051 — keeping only the tail. The system prompt and the file
tools sit at the front, so the model never sees them.

The native `/api/chat` endpoint accepts `options.num_ctx`. This transport rewrites
chat-completions calls onto that endpoint and asks for the window the model was
trained with, read once per model from `/api/show` (`model_info["<arch>.context_length"]`,
or a `num_ctx` the Modelfile pins). `OPENWORKER_OLLAMA_NUM_CTX` caps that on machines
that cannot hold the full KV cache. `shift` is off: once the prompt no longer fits,
Ollama must error instead of silently discarding the front of the prompt again; the
engine treats that error as a compaction trigger.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Iterator, Optional

import httpx

# Used when `/api/show` is unreachable or does not report a context length. 128k is
# the trained window of most current local models (Gemma 4, Qwen 3, Llama 3.x).
DEFAULT_OLLAMA_NUM_CTX = 131_072
# Below this the Cowork prompt cannot fit at all, so smaller caps are ignored.
MIN_OLLAMA_NUM_CTX = 2048
_NUM_CTX_ENV = "OPENWORKER_OLLAMA_NUM_CTX"
_MODELFILE_NUM_CTX = re.compile(r"^\s*num_ctx\s+(\d+)\s*$", re.MULTILINE)

# Bare model name → the num_ctx the transport actually sent for it. The engine reads
# this to size compaction against the real window rather than a guess.
_resolved: dict[str, int] = {}


def num_ctx_cap() -> Optional[int]:
    """`OPENWORKER_OLLAMA_NUM_CTX` as an upper bound, or None when unset or invalid."""
    raw = os.environ.get(_NUM_CTX_ENV, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= MIN_OLLAMA_NUM_CTX else None


def apply_cap(value: int) -> int:
    cap = num_ctx_cap()
    return min(value, cap) if cap else value


def context_length_from_show(show: dict[str, Any]) -> Optional[int]:
    """The window a model should run at, from an `/api/show` response.

    A `num_ctx` pinned in the Modelfile wins (the user chose it deliberately); otherwise
    the trained length under `model_info["<architecture>.context_length"]`."""
    params = show.get("parameters")
    if isinstance(params, str):
        match = _MODELFILE_NUM_CTX.search(params)
        if match and int(match.group(1)) >= MIN_OLLAMA_NUM_CTX:
            return int(match.group(1))
    info = show.get("model_info")
    if not isinstance(info, dict):
        return None
    arch = info.get("general.architecture")
    keyed = info.get(f"{arch}.context_length") if isinstance(arch, str) else None
    if not isinstance(keyed, int):
        keyed = next(
            (v for k, v in info.items() if k.endswith(".context_length") and isinstance(v, int)),
            None,
        )
    return keyed if isinstance(keyed, int) and keyed >= MIN_OLLAMA_NUM_CTX else None


def resolve_num_ctx(show: Optional[dict[str, Any]]) -> int:
    """Trained window (or the default when unknown), capped by the env override."""
    trained = context_length_from_show(show) if show else None
    return apply_cap(trained or DEFAULT_OLLAMA_NUM_CTX)


def context_window_for(model: str) -> Optional[int]:
    """Compaction window for an `ollama:` model. None for every other provider.

    Before the first chat call resolves the model's real window this is the default
    (capped); afterwards it is exactly the num_ctx being sent."""
    vendor, _, bare = model.partition(":")
    if vendor != "ollama":
        return None
    return _resolved.get(bare) or apply_cap(DEFAULT_OLLAMA_NUM_CTX)


def ollama_http_client() -> httpx.Client:
    """SDK client whose chat calls carry `num_ctx`. A long local generation must not
    die on httpx's 5s default; the OpenAI SDK's own timeout still applies per request
    when it sets one."""
    return httpx.Client(
        transport=OllamaContextTransport(),
        timeout=httpx.Timeout(600.0, connect=10.0),
    )


class _ByteChunks(httpx.SyncByteStream):
    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self._chunks


class OllamaContextTransport(httpx.BaseTransport):
    """Rewrite `/v1/chat/completions` onto Ollama's native `/api/chat`.

    `num_ctx` pins one window for every call. When None (the production default) the
    window is looked up per model from `/api/show` on first use and cached."""

    def __init__(
        self, num_ctx: Optional[int] = None, inner: Optional[httpx.BaseTransport] = None
    ) -> None:
        self.num_ctx = num_ctx
        self._inner = inner if inner is not None else httpx.HTTPTransport()
        self._by_model: dict[str, int] = {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or not request.url.path.endswith("/chat/completions"):
            return self._inner.handle_request(request)
        try:
            body = json.loads(request.content.decode() or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._inner.handle_request(request)
        model = str(body.get("model") or "")
        native = to_native_chat(body, self._num_ctx_for(model, request.url))
        native_request = httpx.Request(
            "POST",
            _native_url(request.url, "/api/chat"),
            headers={"content-type": "application/json"},
            content=json.dumps(native),
        )
        response = self._inner.handle_request(native_request)
        if body.get("stream") and response.status_code == 200:
            return self._stream_response(response)
        # The native transport hands back an unread stream. Buffer it so the OpenAI
        # SDK can parse a normal JSON body.
        response.read()
        if response.status_code != 200:
            return httpx.Response(response.status_code, content=response.content)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(to_openai_completion(response.json())),
        )

    def _stream_response(self, response: httpx.Response) -> httpx.Response:
        def chunks() -> Iterator[bytes]:
            saw_tools = False
            first = True
            try:
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        native = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    calls = (native.get("message") or {}).get("tool_calls") or []
                    if calls:
                        saw_tools = True
                    for event in to_openai_stream_events(native, include_role=first, saw_tools=saw_tools):
                        first = False
                        yield f"data: {json.dumps(event)}\n\n".encode()
                yield b"data: [DONE]\n\n"
            finally:
                response.close()

        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_ByteChunks(chunks()),
        )

    def _num_ctx_for(self, model: str, url: httpx.URL) -> int:
        if self.num_ctx is not None:
            return self.num_ctx
        cached = self._by_model.get(model)
        if cached:
            return cached
        show = self._show(model, url)
        value = resolve_num_ctx(show)
        if show is not None:
            self._by_model[model] = value
            _resolved[model] = value
        return value

    def _show(self, model: str, url: httpx.URL) -> Optional[dict[str, Any]]:
        """`/api/show` for `model`, or None when the server cannot answer. A failed
        lookup is not cached: the chat call that follows will surface the real error."""
        request = httpx.Request(
            "POST",
            _native_url(url, "/api/show"),
            headers={"content-type": "application/json"},
            content=json.dumps({"model": model}),
        )
        try:
            response = self._inner.handle_request(request)
        except httpx.HTTPError:
            return None
        try:
            response.read()
            if response.status_code != 200:
                return None
            body = response.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return None
        finally:
            response.close()
        return body if isinstance(body, dict) else None

    def close(self) -> None:
        self._inner.close()


def _native_url(url: httpx.URL, endpoint: str) -> httpx.URL:
    """`…/v1/chat/completions` → `…/api/<endpoint>`, keeping any path prefix in front of
    `/v1` (a reverse proxy mounting Ollama under a sub-path)."""
    path = url.path
    marker = "/v1/"
    prefix = path[: path.rfind(marker)] if marker in path else ""
    return url.copy_with(path=f"{prefix}{endpoint}")


def to_native_chat(body: dict[str, Any], num_ctx: int) -> dict[str, Any]:
    """OpenAI chat-completions JSON → Ollama `/api/chat` JSON."""
    messages = body.get("messages") or []
    native: dict[str, Any] = {
        "model": body.get("model") or "",
        "messages": [_native_message(m, messages) for m in messages if isinstance(m, dict)],
        "stream": bool(body.get("stream")),
        # Never discard the front of an over-long prompt. The engine compacts instead.
        "shift": False,
        "options": {"num_ctx": num_ctx},
    }
    tools = body.get("tools")
    if tools:
        native["tools"] = tools
    options = native["options"]
    predict = body.get("max_tokens", body.get("max_completion_tokens"))
    if isinstance(predict, int):
        options["num_predict"] = predict
    if isinstance(body.get("temperature"), (int, float)):
        options["temperature"] = body["temperature"]
    if isinstance(body.get("top_p"), (int, float)):
        options["top_p"] = body["top_p"]
    if body.get("stop") is not None:
        options["stop"] = body["stop"]
    effort = body.get("reasoning_effort")
    if effort == "none":
        native["think"] = False
    elif isinstance(effort, str) and effort:
        native["think"] = effort
    return native


def _native_message(msg: dict[str, Any], messages: list[Any]) -> dict[str, Any]:
    text, images = _content_parts(msg.get("content"))
    out: dict[str, Any] = {"role": msg.get("role") or "user", "content": text}
    thinking = msg.get("reasoning") or msg.get("reasoning_content")
    if isinstance(thinking, str) and thinking:
        out["thinking"] = thinking
    calls = _native_tool_calls(msg.get("tool_calls"))
    if calls:
        out["tool_calls"] = calls
    if images:
        out["images"] = images
    if out["role"] == "tool":
        tool_call_id = msg.get("tool_call_id") or ""
        if tool_call_id:
            out["tool_call_id"] = tool_call_id
        name = msg.get("name") or _tool_name(messages, tool_call_id)
        if name:
            out["tool_name"] = name
    return out


def _content_parts(content: Any) -> tuple[str, list[str]]:
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return ("" if content is None else str(content)), []
    texts: list[str] = []
    images: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
        elif part.get("type") == "image_url":
            image = _image_b64(part.get("image_url"))
            if image:
                images.append(image)
    return "\n".join(texts), images


def _image_b64(image_url: Any) -> str:
    url = image_url.get("url") if isinstance(image_url, dict) else image_url
    if not isinstance(url, str) or not url:
        return ""
    if url.startswith("data:") and "," in url:
        return url.split(",", 1)[1]
    return url


def _native_tool_calls(raw: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for index, call in enumerate(raw or []):
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                arguments = {"_raw": arguments}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(
            {
                "id": call.get("id") or f"call_{index}",
                "function": {
                    "index": index,
                    "name": function.get("name") or "",
                    "arguments": arguments,
                },
            }
        )
    return calls


def _tool_name(messages: list[Any], tool_call_id: str) -> str:
    if not tool_call_id:
        return ""
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        for call in msg.get("tool_calls") or []:
            if isinstance(call, dict) and call.get("id") == tool_call_id:
                return str((call.get("function") or {}).get("name") or "")
    return ""


def to_openai_completion(native: dict[str, Any]) -> dict[str, Any]:
    """One native chat response → an OpenAI chat.completion object."""
    message = native.get("message") or {}
    tool_calls = _openai_tool_calls(message.get("tool_calls"))
    finish = _finish_reason(native.get("done_reason") or "stop", bool(tool_calls))
    prompt, completion = _usage_counts(native)
    body: dict[str, Any] = {
        "id": "chatcmpl-ollama",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": native.get("model") or "",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": message.get("role") or "assistant",
                    "content": message.get("content") or "",
                    "reasoning": message.get("thinking") or None,
                    "tool_calls": tool_calls or None,
                },
                "finish_reason": finish,
            }
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }
    return body


def to_openai_stream_events(
    native: dict[str, Any], *, include_role: bool, saw_tools: bool
) -> list[dict[str, Any]]:
    """One native stream object → zero or more OpenAI chat.completion.chunk objects."""
    message = native.get("message") or {}
    content = message.get("content") or ""
    thinking = message.get("thinking") or ""
    tool_calls = _openai_tool_calls(message.get("tool_calls"))
    events: list[dict[str, Any]] = []
    if content or thinking or tool_calls or include_role:
        delta: dict[str, Any] = {}
        if include_role:
            delta["role"] = "assistant"
        if thinking:
            delta["reasoning"] = thinking
        if content:
            delta["content"] = content
        if tool_calls:
            delta["tool_calls"] = tool_calls
        events.append(_chunk(native, delta))
    if native.get("done"):
        prompt, completion = _usage_counts(native)
        events.append(
            {
                **_chunk(
                    native,
                    {},
                    finish_reason=_finish_reason(
                        native.get("done_reason") or "stop", saw_tools or bool(tool_calls)
                    ),
                ),
                "usage": {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                },
            }
        )
    return events


def _chunk(
    native: dict[str, Any], delta: dict[str, Any], finish_reason: Optional[str] = None
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-ollama",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": native.get("model") or "",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _openai_tool_calls(raw: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for index, call in enumerate(raw or []):
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments if isinstance(arguments, dict) else {})
        calls.append(
            {
                "index": function.get("index", index),
                "id": call.get("id") or f"call_{index}",
                "type": "function",
                "function": {"name": function.get("name") or "", "arguments": arguments},
            }
        )
    return calls


def _finish_reason(reason: str, has_tools: bool) -> str:
    if reason == "stop" and has_tools:
        return "tool_calls"
    return reason or "stop"


def _usage_counts(native: dict[str, Any]) -> tuple[int, int]:
    return int(native.get("prompt_eval_count") or 0), int(native.get("eval_count") or 0)
