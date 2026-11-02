"""Tagged provider views: every model call recorded as a model step (observability E2, E22).

Every model call leaves the app through one provider router, shared by every session's
engine and by the OpenAI-compatible proxy. A wrapper around the router alone cannot know
which session, turn or purpose a call belongs to, so each caller holds its own **tagged
view** (technical design §3.3, TD-2):

    view = provider.tagged(purpose="summary", parent=step_id)
    turn = view.complete(model=..., messages=...)   # recorded as a model step

- `RecordingProvider` wraps the router once, where it is made. A call made through it with
  no tags is recorded as `unattributed`, so a missing tag shows instead of counting as main.
- `tagged()` on a view returns a new view: fields passed replace the old, the rest are kept.
- A view records through a `sink`: the engine's turn recording (`TurnRecording`), which knows
  the turn, or for calls outside any engine (the proxy) a sink on the server's recorder.

Each model step carries the call's purpose, provider, model, tool count, time to the first
streamed piece, finish reason and token counts (§5.2), and, from the HTTP capture
(`capture.py`), the exact exchange, `provider_meta` and `provider_facts` (§5.2.1). Recording
never breaks a call: any recording error is logged and the call goes on.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterator, Optional

from ..providers.base import ProviderClient
from . import capture
from .engine_hooks import CURRENT, TurnRecording
from .recorder import short_hash

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tags:
    purpose: str = "unattributed"
    parent: Optional[str] = None  # the step this call is nested in
    call_id: Optional[str] = None  # for auto-approver checks: the tool call checked
    shadow: bool = False
    turn: Any = CURRENT  # CURRENT = the sink's current turn; a turn id; None = no turn
    sink: Optional[TurnRecording] = None


class RecordingProvider(ProviderClient):
    """The router, wrapped once. Untagged calls are recorded as `unattributed` when a recorder
    is given; everything else (`invalidate`, `_secrets`…) passes through to the router."""

    def __init__(self, inner: ProviderClient, recorder: Any = None) -> None:
        self.inner = inner
        self.recorder = recorder
        self._sink = TurnRecording(recorder, lambda: "") if recorder is not None else None

    def tagged(self, **tags: Any) -> "ProviderView":
        if "sink" not in tags:
            tags["sink"] = self._sink
        return ProviderView(self, Tags(**tags))

    def complete(self, *, model: str, messages: list[dict[str, Any]], tools: Any = None, **settings: Any):
        return self.tagged().complete(model=model, messages=messages, tools=tools, **settings)

    def stream(self, *, model: str, messages: list[dict[str, Any]], tools: Any = None, **settings: Any):
        return self.tagged().stream(model=model, messages=messages, tools=tools, **settings)

    def capabilities(self, model: str):
        return self.inner.capabilities(model)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class ProviderView(ProviderClient):
    """A tagged handle on the shared provider. Tags are explicit; nothing relies on context
    variables crossing threads."""

    def __init__(self, base: RecordingProvider, tags: Tags) -> None:
        self.base, self.tags = base, tags

    def tagged(self, **overrides: Any) -> "ProviderView":
        return ProviderView(self.base, replace(self.tags, **overrides))

    def capabilities(self, model: str):
        return self.base.inner.capabilities(model)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base.inner, name)

    # -- calls --------------------------------------------------------------------------------
    def complete(self, *, model: str, messages: list[dict[str, Any]], tools: Any = None, **settings: Any):
        call = _ModelCall.begin(self.tags, model=model, tools=tools, streamed=False)
        if call is None:
            return self.base.inner.complete(model=model, messages=messages, tools=tools, **settings)
        try:
            with call.http.active():
                turn = self.base.inner.complete(model=model, messages=messages, tools=tools, **settings)
        except BaseException as exc:
            call.failed(exc)
            raise
        call.finished(turn)
        return turn

    def stream(self, *, model: str, messages: list[dict[str, Any]], tools: Any = None, **settings: Any) -> Iterator[Any]:
        call = _ModelCall.begin(self.tags, model=model, tools=tools, streamed=True)
        if call is None:
            yield from self.base.inner.stream(model=model, messages=messages, tools=tools, **settings)
            return
        it = iter(())
        turn = None
        try:
            with call.http.active():
                it = iter(self.base.inner.stream(model=model, messages=messages, tools=tools, **settings))
            while True:
                # Capture is on only while the next piece is read, so a stream left half-read
                # (Stop pressed) never keeps capturing on this thread. Such a step stays open
                # and is closed by the turn's end as `interrupted` (§7.4).
                with call.http.active():
                    try:
                        chunk = next(it)
                    except StopIteration:
                        break
                call.saw(chunk)
                if getattr(chunk, "turn", None) is not None:
                    turn = chunk.turn
                yield chunk
        except GeneratorExit:
            raise
        except BaseException as exc:
            call.failed(exc)
            raise
        if turn is not None:
            call.finished(turn)


# -- one recorded call ----------------------------------------------------------------------------

_tools_hash_cache: dict[int, tuple[Any, Optional[str]]] = {}
_tools_hash_lock = threading.Lock()


def tools_hash(tools: Any) -> Optional[str]:
    """A short code of the tool definitions, to spot tool changes; cached per tool list (P-6)."""
    if not tools:
        return None
    key = id(tools)
    with _tools_hash_lock:
        hit = _tools_hash_cache.get(key)
        if hit is not None and hit[0] is tools:
            return hit[1]
    code = short_hash(json.dumps(tools, sort_keys=True, default=str))
    with _tools_hash_lock:
        if len(_tools_hash_cache) > 64:
            _tools_hash_cache.clear()
        _tools_hash_cache[key] = (tools, code)
    return code


def _provider_of(model: str) -> str:
    return (model or "").split(":", 1)[0] if ":" in (model or "") else "openai"


def _provider_title(provider: str) -> Optional[str]:
    try:
        from ..providers.registry import get_descriptor

        d = get_descriptor(provider)
        return getattr(d, "title", None) if d is not None else None
    except Exception:  # noqa: BLE001
        return None


def _context_window(model: str) -> Optional[int]:
    try:
        from ..providers.matrix import model_context_windows

        return model_context_windows().get(model)
    except Exception:  # noqa: BLE001
        return None


def root_cause(exc: BaseException) -> BaseException:
    seen = set()
    while id(exc) not in seen and (exc.__cause__ is not None or exc.__context__ is not None):
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__  # type: ignore[assignment]
    return exc


def classify_error(exc: BaseException) -> str:
    """`error.type` from the failure's root cause (§5.2, recording-format §5)."""
    try:
        from ..compaction import is_context_overflow

        if is_context_overflow(exc):
            return "context_length"
    except Exception:  # noqa: BLE001
        pass
    root = root_cause(exc)
    text = f"{type(exc).__name__} {exc} {type(root).__name__} {root}".lower()
    status = getattr(exc, "status_code", None) or getattr(root, "status_code", None)
    if status in (529, 503) or "overload" in text:
        return "overloaded"
    if status == 429 or "rate limit" in text or "ratelimit" in text:
        return "rate_limited"
    if status in (401, 403) or "api key" in text or "authentication" in text:
        return "auth"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "cancel" in text:
        return "cancelled"
    if any(k in text for k in ("connect", "dns", "ssl", "tls", "proxy", "name resolution")):
        return "network"
    return "provider_error"


def usage_attrs(usage: Any) -> dict[str, Any]:
    """The step's `usage` from the provider's `TokenUsage` (§5.2, §6.2). A count the provider
    did not send is `null`, never 0; a call with no counts is `{"reported": false}`."""
    if usage is None:
        return {"reported": False}
    return {
        "reported": True,
        "input_fresh": usage.input,
        "cache_read": usage.cache_read if getattr(usage, "cache_read_reported", True) else None,
        "cache_write": usage.cache_write if getattr(usage, "cache_write_reported", True) else None,
        "output": usage.output,
        "reasoning": getattr(usage, "reasoning", None),
    }


class _ModelCall:
    """The state of one recorded model call, from start to end."""

    def __init__(self, tags: Tags, sink: TurnRecording, step_id: str, streamed: bool) -> None:
        self.tags, self.sink, self.step_id, self.streamed = tags, sink, step_id, streamed
        self.http = capture.HttpCapture()
        self.t0 = time.perf_counter()
        self.ttft_ms: Optional[int] = None

    @classmethod
    def begin(cls, tags: Tags, *, model: str, tools: Any, streamed: bool) -> Optional["_ModelCall"]:
        sink = tags.sink
        if sink is None or not sink.enabled:
            return None
        try:
            provider = _provider_of(model)
            step_id = sink.start_step(
                kind="model", name="model", turn_id=tags.turn, shadow=tags.shadow,
                parent_step_id=tags.parent,
                call_id=tags.call_id if tags.purpose == "auto_approver" else None,
                attrs={
                    "purpose": tags.purpose, "provider": provider,
                    "provider_title": _provider_title(provider), "model": model,
                    "stream": streamed, "context_window": _context_window(model),
                    "tools_offered": len(tools or []), "tools_hash": tools_hash(tools),
                },
            )
        except Exception:  # noqa: BLE001 - recording never breaks a call
            log.exception("recording: could not start a model step")
            return None
        if step_id is None:
            return None
        return cls(tags, sink, step_id, streamed)

    def saw(self, chunk: Any) -> None:
        # The first streamed piece of the reply: text or thinking (§7.2).
        if self.ttft_ms is None and (getattr(chunk, "text_delta", None) or getattr(chunk, "reasoning_delta", None)):
            self.ttft_ms = int((time.perf_counter() - self.t0) * 1000)

    def _exchange(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """(attrs, content) from the HTTP capture: provider_meta, provider_facts, the exchange."""
        attrs: dict[str, Any] = {}
        content: dict[str, Any] = {}
        entries = [e for e in self.http.entries() if e.get("method") == "POST"]
        if not entries:
            return attrs, content
        recorder = self.sink.recorder
        content["exchange"] = [capture.store_exchange(e, recorder.put_piece) for e in entries]
        meta = [capture.provider_meta(e) for e in entries]
        attrs["provider_meta"] = meta
        reply = [m for m in meta if not m.get("auxiliary")] or meta
        facts = capture.provider_facts(reply[-1])
        attrs["provider_facts"] = facts
        if facts.get("served_by") is not None:
            attrs["served_by"] = facts["served_by"]
        return attrs, content

    def finished(self, turn: Any) -> None:
        try:
            attrs: dict[str, Any] = {
                "finish_reason": getattr(turn, "finish_reason", None),
                "ttft_ms": self.ttft_ms if self.streamed else None,
                "usage": usage_attrs(getattr(turn, "usage", None)),
            }
            if getattr(turn, "served_by", None):
                attrs["served_by"] = turn.served_by
            more, content = self._exchange()
            attrs.update(more)
            _fill_from_facts(attrs)
            self.sink.end_step(
                self.step_id, status="ok", attrs=attrs, content=content,
                wait_for_reply_message=self.tags.purpose == "main",
            )
            if self.tags.purpose == "main":
                self.sink.note_main_usage(attrs["usage"])
        except Exception:  # noqa: BLE001
            log.exception("recording: could not end a model step")

    def failed(self, exc: BaseException) -> None:
        try:
            if _is_stop(exc):
                return  # Stop: the turn's end closes the step as interrupted
            more, content = self._exchange()
            attrs: dict[str, Any] = {"finish_reason": None, "ttft_ms": self.ttft_ms if self.streamed else None,
                                     "usage": {"reported": False}, **more}
            text = str(root_cause(exc)).strip()[:200] or None
            self.sink.end_step(self.step_id, status="error", attrs=attrs, content=content,
                               error={"type": classify_error(exc), "text": text})
        except Exception:  # noqa: BLE001
            log.exception("recording: could not record a failed model step")


def _is_stop(exc: BaseException) -> bool:
    return isinstance(exc, (GeneratorExit, KeyboardInterrupt))


def _fill_from_facts(attrs: dict[str, Any]) -> None:
    """A count the provider sent is recorded even where the provider code dropped it
    (V-13, V-15), and impossible numbers are flagged, never corrected (V-19)."""
    use, facts = attrs.get("usage") or {}, attrs.get("provider_facts") or {}
    if not use.get("reported"):
        return
    if use.get("reasoning") is None and facts.get("reasoning") is not None:
        use["reasoning"] = facts["reasoning"]
    if use.get("cache_write") is None and facts.get("cache_write") is not None:
        use["cache_write"] = facts["cache_write"]
    flags = []
    if use.get("reasoning") is not None and use.get("output") is not None and use["reasoning"] > use["output"]:
        flags.append("reasoning_exceeds_output")
    total = facts.get("provider_total")
    parts = (use.get("input_fresh") or 0) + (use.get("cache_read") or 0) + (use.get("cache_write") or 0) + (use.get("output") or 0)
    if total is not None and total != parts:
        flags.append("provider_total_differs")
    if flags:
        use["inconsistent"] = flags


def view_for_engine(provider: Any, sink: TurnRecording) -> "ProviderView":
    """The engine's own view (§3.3): tags its creator already set are kept, so a helper's
    engine keeps `helper`; a plain provider is wrapped first."""
    if isinstance(provider, ProviderView):
        tags = provider.tags
        return provider.tagged(
            purpose="main" if tags.purpose == "unattributed" else tags.purpose,
            sink=sink if tags.sink is None or tags.sink is provider.base._sink else tags.sink,
        )
    base = provider if isinstance(provider, RecordingProvider) else RecordingProvider(provider)
    return base.tagged(purpose="main", sink=sink)


def tag(provider: Any, **tags: Any) -> Any:
    """`provider.tagged(**tags)` for a recording provider or view; any other provider as is."""
    tagged = getattr(type(provider), "tagged", None)
    return provider.tagged(**tags) if tagged is not None else provider
