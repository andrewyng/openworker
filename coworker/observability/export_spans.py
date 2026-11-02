"""Recorded turns as OpenTelemetry spans, with the content rules (OB-6, phase-2 design §10.4).

Pure functions, no network: the exporter (`export.py`) reads the store, calls
`build_turn_spans` for a turn's unsent steps and encodes the result with `encode_otlp`.

The mapping is the frozen 1.x one (tracing-spec §9 and §10, attributes.yaml part 2): one trace
per turn, the turn as the root span, each step a span below it, notices as events on the turn.
The Phase 1 lab exporter (`phase-1/lab/export_otel.py`) was the starting point; it was checked
against a local Langfuse. This module writes OTLP/HTTP JSON itself, so the app needs no
OpenTelemetry SDK.

Content (tracing-spec §11). Metadata is always sent. Each content attribute has its switch,
and five rules apply on top:
- R1. Tool parts inside exported messages follow the tool switches, wherever they appear.
- R2. OpenWorker's per-turn `<system-context>` block is cut out of user text unless
  `capture.context` is on.
- R3. The auto-approver's and the summary's calls need the tool switches as well.
- R4. Secrets are masked, then each text part is cut at 8 KB (`Masker`).
- R5. Never exported: reasoning text, provider-reported cost, `error.text`, `questions`,
  `decision_details`, `agents_md`, `blocked`. None of them has a name here.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from typing import Any, Callable, Iterable, Optional

from . import calculator
from .capture import is_auxiliary, parse_events, rebuild_request, wire_of_url

ATTRIBUTES_VERSION = "1.2"
SCHEMA = f"openworker/{ATTRIBUTES_VERSION}"
SERVICE_NAME = "openworker-desktop"
SCOPE_NAME = "openworker.observability"

# Markers (tracing-spec §11).
REASONING = "[reasoning text not exported]"
TOOL_ARGS_OUT = "[tool arguments not exported]"
TOOL_RESULT_OUT = "[tool result not exported]"
CONTEXT_OUT = "[OpenWorker context not exported]"
SECRET = "[secret masked]"
CUT_BYTES = 8192
CUT_MARKER = "[cut: {n} more bytes]"

SWITCHES = ("prompts", "responses", "tool_args", "tool_results", "system_instructions",
            "context", "names")

# attributes.yaml export.value_maps.gen_ai_provider_name
PROVIDER_NAME = {"anthropic": "anthropic", "openai": "openai", "gemini": "gcp.gemini",
                 "bedrock": "aws.bedrock", "openrouter": "openrouter", "ollama": "ollama",
                 "together": "together", "openai_decisions": "openai", "typesafe_jev": "typesafe"}

SPAN_INTERNAL, SPAN_CLIENT = 1, 3  # OTLP SpanKind
STATUS_ERROR = 2


# -- R4: mask, then cut -----------------------------------------------------------------------
# attributes.yaml export.content_switches.masking
MASK_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{16,}",
    r"\b(AKIA|ASIA)[A-Z0-9]{16}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
    r"\bgithub_pat_[A-Za-z0-9_]{30,}\b",
    r"\bxox[abp]-[A-Za-z0-9-]{10,}",
    r"\bAIza[0-9A-Za-z_-]{35}\b",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
]
_PATTERNS = [re.compile(p) for p in MASK_PATTERNS]
MIN_SECRET_LEN = 8
_ENV_SECRET_PARTS = ("_API_KEY", "_TOKEN", "_SECRET", "PASSWORD")


def env_secret_values(environ: Optional[dict[str, str]] = None) -> list[str]:
    """Credential-looking environment variables (masking.values)."""
    env = os.environ if environ is None else environ
    return [v for k, v in env.items()
            if any(p in k.upper() for p in _ENV_SECRET_PARTS) and len(v or "") >= MIN_SECRET_LEN]


def store_secret_values(data: Any) -> list[str]:
    """Every string value in the secret store's profiles."""
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(data)
    return out


def cut(text: str, limit: int = CUT_BYTES) -> str:
    """Cut at `limit` bytes of UTF-8, on a character boundary, and say how much was left out."""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    n = limit
    while n > 0 and (data[n] & 0xC0) == 0x80:  # never split a character
        n -= 1
    return data[:n].decode("utf-8") + CUT_MARKER.format(n=len(data) - n)


class Masker:
    """R4: stored secret values (longest first), then the key patterns; then the cut."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        values = {s for s in secrets if isinstance(s, str) and len(s) >= MIN_SECRET_LEN}
        self.values = sorted(values, key=len, reverse=True)

    def mask(self, text: str) -> str:
        for v in self.values:
            if v in text:
                text = text.replace(v, SECRET)
        for p in _PATTERNS:
            text = p.sub(SECRET, text)
        return text

    def clean(self, value: Any) -> str:
        """One exported text part: as text (JSON for anything else), masked, then cut."""
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        return cut(self.mask(text))


# -- content: the exact request and reply, in the GenAI message shape -------------------------
def _text_parts(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "content": content}] if content else []
    parts: list[dict[str, Any]] = []
    for b in content if isinstance(content, list) else [content]:
        if not isinstance(b, dict):
            parts.append({"type": "text", "content": str(b)})
            continue
        t = b.get("type")
        if t in ("text", "input_text", "output_text"):
            parts.append({"type": "text", "content": b.get("text", "")})
        elif t == "tool_use":
            parts.append({"type": "tool_call", "id": b.get("id"), "name": b.get("name"), "arguments": b.get("input")})
        elif t == "tool_result":
            parts.append({"type": "tool_call_response", "id": b.get("tool_use_id"), "response": b.get("content")})
        elif t in ("thinking", "redacted_thinking", "reasoning"):
            parts.append({"type": "reasoning", "content": REASONING})
        else:
            parts.append({"type": t or "unknown"})  # images and the like: the kind only
    return parts


def _gemini_parts(parts: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        if p.get("thought"):
            out.append({"type": "reasoning", "content": REASONING})
        elif "text" in p:
            out.append({"type": "text", "content": p.get("text") or ""})
        elif "functionCall" in p:
            fc = p["functionCall"] or {}
            out.append({"type": "tool_call", "id": fc.get("id"), "name": fc.get("name"), "arguments": fc.get("args")})
        elif "functionResponse" in p:
            fr = p["functionResponse"] or {}
            out.append({"type": "tool_call_response", "id": fr.get("id") or fr.get("name"), "response": fr.get("response")})
        else:
            out.append({"type": next(iter(p), "unknown")})
    return out


def request_messages(wire: str, req: dict[str, Any]) -> tuple[list, list, list]:
    """(system instructions, input messages, tool definitions) from a rebuilt request."""
    system: list[dict[str, Any]] = []
    msgs: list[dict[str, Any]] = []
    tools = req.get("tools") or []
    if wire == "anthropic":
        system = _text_parts(req.get("system"))
        for m in req.get("messages") or []:
            msgs.append({"role": m.get("role"), "parts": _text_parts(m.get("content"))})
    elif wire == "openai_responses":
        if req.get("instructions"):
            system = [{"type": "text", "content": req["instructions"]}]
        items = req.get("input") or []
        for it in [{"role": "user", "content": items}] if isinstance(items, str) else items:
            t = it.get("type")
            if t == "function_call":
                msgs.append({"role": "assistant", "parts": [{"type": "tool_call", "id": it.get("call_id"),
                                                              "name": it.get("name"), "arguments": it.get("arguments")}]})
            elif t == "function_call_output":
                msgs.append({"role": "tool", "parts": [{"type": "tool_call_response", "id": it.get("call_id"),
                                                         "response": it.get("output")}]})
            elif t == "reasoning":
                msgs.append({"role": "assistant", "parts": [{"type": "reasoning", "content": REASONING}]})
            elif it.get("role") in ("system", "developer"):
                system += _text_parts(it.get("content"))
            else:
                msgs.append({"role": it.get("role", "user"), "parts": _text_parts(it.get("content"))})
    elif wire == "gemini":
        system = _gemini_parts((req.get("systemInstruction") or {}).get("parts"))
        for c in req.get("contents") or []:
            msgs.append({"role": c.get("role", "user"), "parts": _gemini_parts(c.get("parts"))})
    else:  # openai_chat and ollama: a messages list, system first
        for m in req.get("messages") or []:
            if m.get("role") in ("system", "developer"):
                system += _text_parts(m.get("content"))
                continue
            parts = _text_parts(m.get("content"))
            if m.get("reasoning_content") or m.get("reasoning") or m.get("thinking"):
                parts.insert(0, {"type": "reasoning", "content": REASONING})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                parts.append({"type": "tool_call", "id": tc.get("id"), "name": fn.get("name"),
                              "arguments": fn.get("arguments")})
            if m.get("role") == "tool":
                parts = [{"type": "tool_call_response", "id": m.get("tool_call_id") or m.get("tool_name"),
                          "response": m.get("content")}]
            msgs.append({"role": m.get("role"), "parts": parts})
    return system, msgs, tools


def response_message(wire: str, body: str) -> dict[str, Any]:
    """The assistant's reply, put together from the body exactly as received."""
    evs = [e for e in parse_events(body) if isinstance(e, dict)]
    parts: list[dict[str, Any]] = []
    finish = None
    if wire == "anthropic":
        blocks: dict[int, dict[str, Any]] = {}
        for e in evs:
            t = e.get("type")
            if t == "message" and isinstance(e.get("content"), list):  # not streamed
                for i, b in enumerate(e["content"]):
                    blocks[i] = dict(b, _json="")
                finish = e.get("stop_reason")
            elif t == "content_block_start":
                blocks[e.get("index", 0)] = dict(e.get("content_block") or {}, _json="")
            elif t == "content_block_delta":
                b = blocks.setdefault(e.get("index", 0), {"type": "text", "text": "", "_json": ""})
                d = e.get("delta") or {}
                if d.get("type") == "text_delta":
                    b["text"] = b.get("text", "") + d.get("text", "")
                elif d.get("type") == "input_json_delta":
                    b["_json"] += d.get("partial_json", "")
            elif t == "message_delta":
                finish = (e.get("delta") or {}).get("stop_reason")
        for i in sorted(blocks):
            b = blocks[i]
            if b.get("type") == "tool_use":
                try:
                    args = json.loads(b["_json"]) if b["_json"] else b.get("input")
                except ValueError:
                    args = b["_json"]
                parts.append({"type": "tool_call", "id": b.get("id"), "name": b.get("name"), "arguments": args})
            elif b.get("type") == "text":
                parts.append({"type": "text", "content": b.get("text", "")})
            else:
                parts.append({"type": "reasoning", "content": REASONING})
    elif wire == "openai_responses":
        done = [e for e in evs if e.get("type") == "response.completed"]
        resp = done[-1].get("response") if done else (evs[-1] if evs and "output" in evs[-1] else {})
        finish = (resp or {}).get("status")
        for it in (resp or {}).get("output") or []:
            if it.get("type") == "function_call":
                parts.append({"type": "tool_call", "id": it.get("call_id"), "name": it.get("name"),
                              "arguments": it.get("arguments")})
            elif it.get("type") == "message":
                parts += _text_parts(it.get("content"))
            elif it.get("type") == "reasoning":
                parts.append({"type": "reasoning", "content": REASONING})
    elif wire == "gemini":
        for e in evs:
            for c in e.get("candidates") or []:
                parts += _gemini_parts((c.get("content") or {}).get("parts"))
                finish = c.get("finishReason") or finish
    elif wire == "ollama":
        text, reasoning = "", False
        for e in evs:
            m = e.get("message") or {}
            text += m.get("content") or ""
            reasoning = reasoning or bool(m.get("thinking"))
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                parts.append({"type": "tool_call", "id": tc.get("id"), "name": fn.get("name"),
                              "arguments": fn.get("arguments")})
            if e.get("done"):
                finish = e.get("done_reason")
        if text:
            parts.insert(0, {"type": "text", "content": text})
        if reasoning:
            parts.insert(0, {"type": "reasoning", "content": REASONING})
    else:  # openai_chat, streamed or not
        text, calls, reasoning = "", {}, False
        for e in evs:
            for ch in e.get("choices") or []:
                d = ch.get("delta") or ch.get("message") or {}
                text += d.get("content") or ""
                reasoning = reasoning or bool(d.get("reasoning") or d.get("reasoning_content"))
                for n, tc in enumerate(d.get("tool_calls") or []):
                    c = calls.setdefault(tc.get("index", n), {"id": None, "name": None, "arguments": ""})
                    c["id"] = tc.get("id") or c["id"]
                    fn = tc.get("function") or {}
                    c["name"] = fn.get("name") or c["name"]
                    c["arguments"] += fn.get("arguments") or ""
                finish = ch.get("finish_reason") or finish
        if reasoning:
            parts.append({"type": "reasoning", "content": REASONING})
        if text:
            parts.append({"type": "text", "content": text})
        parts += [dict(type="tool_call", **calls[i]) for i in sorted(calls)]
    return {"role": "assistant", "parts": parts, "finish_reason": finish}


def step_exchange(step: dict[str, Any], get_piece: Callable[[str], Optional[bytes]]) -> Optional[tuple[str, dict, str]]:
    """(wire, rebuilt request, response body) of a model step's last real request."""
    for ex in reversed((step.get("content") or {}).get("exchange") or []):
        url = (ex.get("request") or {}).get("url") or ""
        if ex.get("auxiliary") or is_auxiliary(url):
            continue  # a helper lookup (Ollama's /api/show), not the call
        try:
            req = rebuild_request(ex, get_piece)
            if isinstance(req, (bytes, bytearray)):
                req = json.loads(req)
        except Exception:  # noqa: BLE001 - a piece already swept: nothing to rebuild
            return None
        if not isinstance(req, dict):
            return None
        body = get_piece((ex.get("response") or {}).get("body_blob") or "") or b""
        return wire_of_url(url), req, body.decode("utf-8", "replace")
    return None


# -- the content rules -------------------------------------------------------------------------
_CONTEXT_BLOCK = re.compile(r"<system-context>.*?(</system-context>|\Z)", re.S)


def cut_context(text: str) -> str:
    """R2: OpenWorker's per-turn block, replaced by its marker."""
    return _CONTEXT_BLOCK.sub(CONTEXT_OUT, text)


def apply_rules(messages: list[dict[str, Any]], sw: dict[str, bool], masker: Masker) -> list[dict[str, Any]]:
    """R1, R2 and R4 over a list of exported messages (a copy)."""
    out = []
    for m in messages:
        parts = []
        for p in m.get("parts") or []:
            p = dict(p)
            t = p.get("type")
            if t == "text":
                text = p.get("content") or ""
                if m.get("role") == "user" and not sw.get("context"):
                    text = cut_context(text)
                p["content"] = masker.clean(text)
            elif t == "tool_call":
                p["arguments"] = masker.clean(p.get("arguments")) if sw.get("tool_args") else TOOL_ARGS_OUT
            elif t == "tool_call_response":
                p["response"] = masker.clean(p.get("response")) if sw.get("tool_results") else TOOL_RESULT_OUT
            parts.append(p)
        out.append({k: v for k, v in m.items() if k != "parts"} | {"parts": parts})
    return out


def content_allowed(purpose: str, sw: dict[str, bool]) -> tuple[bool, bool]:
    """(input, output) of a model call may go out (R3)."""
    if purpose == "auto_approver":
        return sw["prompts"] and sw["tool_args"], sw["responses"] and sw["tool_args"]
    if purpose == "summary":
        tools = sw["tool_args"] and sw["tool_results"]
        return sw["prompts"] and tools, sw["responses"] and tools
    return sw["prompts"], sw["responses"]


# -- tokens -------------------------------------------------------------------------------------
def _usage(step: dict[str, Any]) -> Optional[dict[str, Any]]:
    u = (step.get("attrs") or {}).get("usage") or {}
    if not u.get("reported"):
        return None
    return {k: u.get(k) for k in ("input_fresh", "cache_read", "cache_write", "output", "reasoning")}


def _semconv_usage(u: dict[str, Any]) -> dict[str, int]:
    a = {"gen_ai.usage.input_tokens": calculator.input_total(u), "gen_ai.usage.output_tokens": u.get("output") or 0}
    for part, name in (("cache_read", "gen_ai.usage.cache_read.input_tokens"),
                       ("cache_write", "gen_ai.usage.cache_creation.input_tokens"),
                       ("reasoning", "gen_ai.usage.reasoning.output_tokens")):
        if u.get(part) is not None:  # a part not reported is left out, never 0
            a[name] = u[part]
    return a


def _langfuse_usage(u: dict[str, Any]) -> dict[str, int]:
    """Buckets that do not overlap: they add up to the call's total."""
    d = {"input": u.get("input_fresh") or 0}
    if u.get("cache_read") is not None:
        d["input_cache_read"] = u["cache_read"]
    if u.get("cache_write") is not None:
        d["input_cache_creation"] = u["cache_write"]
    out = u.get("output") or 0
    if u.get("reasoning") is not None:
        d["output"] = max(out - u["reasoning"], 0)
        d["output_reasoning"] = u["reasoning"]
    else:
        d["output"] = out
    return d


# -- ids -----------------------------------------------------------------------------------------
def _hex(text: str, n: int) -> str:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]
    return h if int(h, 16) else "1".rjust(n, "0")  # an all-zero id is invalid in OTLP


def trace_id(turn_id: str) -> str:
    return _hex(f"trace|{turn_id}", 32)


def root_span_id(turn_id: str) -> str:
    return _hex(f"turn|{turn_id}", 16)


def step_span_id(step_id: str) -> str:
    return _hex(f"step|{step_id}", 16)


def _iso(ms: int) -> str:
    return (_dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def _js(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


# -- one turn -> spans -----------------------------------------------------------------------------
def switches_from(include: dict[str, bool]) -> dict[str, bool]:
    """The page's switches to the shared `capture.*` ones (design §10.4)."""
    return {
        "prompts": bool(include.get("messages")), "responses": bool(include.get("messages")),
        "tool_args": bool(include.get("tool_io")), "tool_results": bool(include.get("tool_io")),
        "system_instructions": bool(include.get("context")), "context": bool(include.get("context")),
        "names": bool(include.get("names")),
    }


def turn_end_ms(turn: dict[str, Any], steps: list[dict[str, Any]]) -> int:
    if turn.get("duration_ms") is not None:
        return turn["started_at"] + turn["duration_ms"]
    ends = [s["started_at"] + (s.get("duration_ms") or 0) for s in steps if s.get("started_at") is not None]
    return max([turn.get("started_at") or 0] + ends)


def build_turn_spans(
    turn: dict[str, Any],
    steps: list[dict[str, Any]],
    *,
    sw: dict[str, bool],
    dialect: str,
    get_piece: Callable[[str], Optional[bytes]],
    masker: Masker,
    include_root: bool = True,
    only: Optional[set[str]] = None,
    session_title: Optional[str] = None,
) -> list[dict[str, Any]]:
    """The spans of one turn, as plain dicts (`encode_otlp` turns them into OTLP JSON).

    `steps` is every step of the turn (parents and content are looked up among them);
    `only` limits the spans built to these step ids (a late `after_turn` step), and
    `include_root` says whether the turn's own span is built. Each span dict carries
    `_mark`: the id the exporter marks as sent (the step id, or the turn id for the root).
    """
    lf = dialect == "langfuse"
    tid = turn["turn_id"]
    by_id = {s["step_id"]: s for s in steps}
    end_ms = turn_end_ms(turn, steps)
    common = {"session.id": turn["session_id"], "gen_ai.conversation.id": turn["session_id"],
              "openworker.turn.id": tid}
    any_content = any(sw.get(k) for k in SWITCHES if k != "names")

    # Content for tool spans comes from the exchanges around them: the arguments from the
    # reply that asked for the call, the result from the next request that carried it back.
    exchanges: dict[str, tuple[str, dict, str]] = {}
    tool_args: dict[str, Any] = {}
    tool_results: dict[str, Any] = {}
    if any_content:
        for s in steps:
            if s.get("kind") != "model":
                continue
            x = step_exchange(s, get_piece)
            if x is None:
                continue
            exchanges[s["step_id"]] = x
            wire, req, body = x
            for m in request_messages(wire, req)[1]:
                for p in m["parts"]:
                    if p.get("type") == "tool_call_response" and p.get("id"):
                        tool_results[p["id"]] = p.get("response")
            for p in response_message(wire, body)["parts"]:
                if p.get("type") == "tool_call" and p.get("id"):
                    tool_args[p["id"]] = p.get("arguments")

    spans: list[dict[str, Any]] = []
    trace = trace_id(tid)
    root_id = root_span_id(tid)

    def depth(s: dict[str, Any]) -> int:
        d, p, seen = 0, s.get("parent_step_id"), set()
        while p and p in by_id and p not in seen:
            seen.add(p)
            d, p = d + 1, by_id[p].get("parent_step_id")
        return d

    first_user_text = last_reply_text = None
    for s in sorted(steps, key=lambda s: (depth(s), s.get("seq") or 0)):
        if s.get("kind") == "notice" or (only is not None and s["step_id"] not in only):
            continue
        a = s.get("attrs") or {}
        attrs: dict[str, Any] = dict(common, **{
            "openworker.step.id": s["step_id"], "openworker.step.kind": s["kind"],
            "openworker.step.status": s.get("status") or "unknown", "openworker.step.seq": s.get("seq") or 0})
        if s.get("after_turn"):
            attrs["openworker.after_turn"] = True
        if s.get("shadow"):
            attrs["openworker.shadow"] = True
        call_id = s.get("call_id") or (a.get("for_call_id") if s["kind"] == "wait" else None)
        if call_id:
            # Only a tool's own span carries gen_ai.tool.call.id: viewers type any span with it
            # as a tool (V-22). Waits, checks and the reviewer's model call use our own name.
            attrs["gen_ai.tool.call.id" if s["kind"] == "tool" else "openworker.tool.call_id"] = call_id
        kind = SPAN_INTERNAL
        events: list[dict[str, Any]] = []
        lf_type = "span"
        usage = None
        if s["kind"] == "model":
            model = a.get("model") or ""
            prov, _, short = model.partition(":")
            short = short or model
            prov = a.get("provider") or prov
            facts = a.get("provider_facts") or {}
            name = f"chat {short}"
            kind, lf_type = SPAN_CLIENT, "generation"
            attrs.update({"gen_ai.operation.name": "chat", "gen_ai.provider.name": PROVIDER_NAME.get(prov, prov),
                          "gen_ai.request.model": short, "openworker.model": model,
                          "openworker.llm.purpose": a.get("purpose") or "unattributed",
                          "openworker.stream": bool(a.get("stream"))})
            if facts.get("served_model"):
                attrs["gen_ai.response.model"] = facts["served_model"]
            if facts.get("provider_response_id"):
                attrs["gen_ai.response.id"] = facts["provider_response_id"]
            if facts.get("provider_request_id"):
                attrs["openworker.provider.request_id"] = facts["provider_request_id"]
            if facts.get("served_by") or a.get("served_by"):
                attrs["openworker.provider.served_by"] = facts.get("served_by") or a.get("served_by")
            if a.get("finish_reason"):
                attrs["gen_ai.response.finish_reasons"] = [a["finish_reason"]]
            if a.get("tools_offered") is not None:
                attrs["openworker.tools_offered"] = a["tools_offered"]
            usage = _usage(s)
            attrs["openworker.usage.reported"] = usage is not None
            if usage:
                attrs.update(_semconv_usage(usage))
                if lf:
                    attrs["langfuse.observation.usage_details"] = _js(_langfuse_usage(usage))
            if a.get("ttft_ms") is not None and s.get("started_at") is not None:
                first = s["started_at"] + a["ttft_ms"]
                attrs["gen_ai.client.operation.time_to_first_chunk"] = a["ttft_ms"] / 1000
                events.append({"name": "gen_ai.first_chunk", "at": first, "attrs": {}})
                if lf:
                    attrs["langfuse.observation.completion_start_time"] = _iso(first)
            err = a.get("error")
            if err:
                attrs["error.type"] = err.get("type") or "error"  # never error.text (R5)
            x = exchanges.get(s["step_id"])
            if x is not None:
                wire, req, body = x
                system, msgs, tools = request_messages(wire, req)
                out = response_message(wire, body)
                purpose = a.get("purpose") or ""
                in_ok, out_ok = content_allowed(purpose, sw)
                if sw.get("system_instructions"):
                    attrs["gen_ai.system_instructions"] = _js(apply_rules([{"role": "system", "parts": system}],
                                                                          {**sw, "context": True}, masker)[0]["parts"])
                    attrs["gen_ai.tool.definitions"] = masker.clean(tools)
                if in_ok:
                    attrs["gen_ai.input.messages"] = _js(apply_rules(msgs, sw, masker))
                if out_ok:
                    attrs["gen_ai.output.messages"] = _js(apply_rules([out], sw, masker))
                if purpose == "main":
                    users = [p["content"] for m in msgs if m.get("role") == "user"
                             for p in m["parts"] if p.get("type") == "text"]
                    first_user_text = first_user_text or (users[-1] if users else None)
                    texts = [p["content"] for p in out["parts"] if p.get("type") == "text"]
                    last_reply_text = texts[-1] if texts else last_reply_text
        elif s["kind"] == "tool":
            name = f"execute_tool {s.get('name')}"
            lf_type = "tool"
            attrs.update({"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": s.get("name") or "",
                          "gen_ai.tool.type": "function"})
            dec = a.get("decision") or {}
            if dec.get("origin"):
                attrs["openworker.decision.origin"] = dec["origin"]
            if dec.get("path"):
                attrs["openworker.decision.path"] = [f"{p.get('stage')}:{p.get('outcome')}" for p in dec["path"]]
            if dec.get("helper"):
                attrs["openworker.decision.helper"] = True
            for k in ("risk_class", "exit_code", "result_bytes"):
                if a.get(k) is not None:
                    attrs[f"openworker.tool.{k}"] = a[k]
            cid = s.get("call_id")
            if sw.get("tool_args") and cid in tool_args:
                attrs["gen_ai.tool.call.arguments"] = masker.clean(tool_args[cid])
            if sw.get("tool_results") and cid in tool_results:
                attrs["gen_ai.tool.call.result"] = masker.clean(tool_results[cid])
        elif s["kind"] == "wait":
            wk = a.get("kind") or "approval"
            name = f"openworker.{wk}.wait"
            for k in ("kind", "where", "outcome", "attended"):
                if a.get(k) is not None:
                    attrs[f"openworker.wait.{k}"] = a[k]
            resolved = (s.get("content") or {}).get("resolved_by")
            if sw.get("names") and resolved:
                attrs["openworker.wait.resolved_by"] = masker.clean(resolved)
        elif s["kind"] == "auto_approver":
            name = f"openworker.policy.review {s.get('name')}"
            lf_type = "guardrail"
            rv = a.get("reviewer") or {}
            attrs.update({"openworker.reviewer.kind": rv.get("kind") or "",
                          "openworker.reviewer.external": bool(rv.get("external")),
                          "openworker.reviewer.mode": a.get("mode") or "",
                          "openworker.reviewer.result": a.get("result") or ""})
            if rv.get("model"):
                attrs["openworker.reviewer.model"] = rv["model"]
            for k in ("score", "cutoff"):
                if a.get(k) is not None:
                    attrs[f"openworker.reviewer.{k}"] = a[k]
            reason = (s.get("content") or {}).get("reason")
            if reason and sw.get("responses") and sw.get("tool_args"):  # R3: it quotes the action
                attrs["openworker.reviewer.reason"] = masker.clean(reason)
        elif s["kind"] == "summary":
            name = "openworker.compaction" if s.get("name") == "summary" else "openworker.compaction.trim"
            for k in ("reason", "tokens_before", "tokens_after"):
                if a.get(k) is not None:
                    attrs[f"openworker.compaction.{k}"] = a[k]
        elif s["kind"] == "context":
            name = "openworker.context.build"
            for k in ("size_chars", "session_prompt_hash", "block_hash"):
                if a.get(k) is not None:
                    attrs[f"openworker.context.{k}"] = a[k]
        elif s["kind"] == "sandbox":
            name = "openworker.sandbox.setup"
        else:
            name = f"openworker.{s['kind']}"

        status = s.get("status") or "unknown"
        has_end = s.get("duration_ms") is not None
        level = message = None
        if status == "error":
            level = "ERROR"
            message = (a.get("error") or {}).get("type") or "error"
            if s["kind"] == "model":
                message += "; usage unknown"
        elif status in ("denied", "interrupted", "unknown") or not has_end:
            level, message = "WARNING", status if has_end else "unknown"
        elif s["kind"] == "model" and usage is None:
            level, message = "WARNING", "usage not reported"
        if lf:
            attrs["langfuse.observation.type"] = lf_type
            if level:
                attrs["langfuse.observation.level"] = level
                attrs["langfuse.observation.status_message"] = message
        parent = s.get("parent_step_id")
        start = s.get("started_at") if s.get("started_at") is not None else turn["started_at"]
        spans.append({
            "_mark": s["step_id"], "trace_id": trace,
            "span_id": step_span_id(s["step_id"]),
            "parent_span_id": step_span_id(parent) if parent and parent in by_id else root_id,
            "name": name, "kind": kind, "start_ms": start,
            "end_ms": start + s["duration_ms"] if has_end else max(end_ms, start),
            "attrs": attrs, "events": events,
            "error": ((a.get("error") or {}).get("type") or "error") if status == "error" else None,
        })

    if include_root:
        usage = calculator.usage({"steps": steps}, turn_id=tid)
        root_attrs = dict(common, **{
            "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "coworker",
            "openworker.turn.seq": turn.get("seq") or 0, "openworker.turn.kind": turn.get("kind") or "",
            "openworker.turn.trigger": turn.get("trigger") or "", "openworker.turn.status": turn.get("status") or "",
            "openworker.approval_mode": turn.get("approval_mode") or "",
            "openworker.attended": bool(turn.get("attended")),
            # Never a gen_ai.usage.* name on a parent span: viewers would count it twice.
            "openworker.usage.total_tokens": usage["pill"],
            "openworker.usage.at_least": usage["at_least"],
            "openworker.usage.unreported_calls": usage["unreported_calls"],
            "openworker.usage.failed_calls": usage["failed_calls"],
            "openworker.usage.context_limit_calls": usage["overflow_calls"]})
        woken = turn.get("woken_by") or {}
        if isinstance(woken, dict):
            if woken.get("team_id"):
                root_attrs["openworker.team.id"] = woken["team_id"]
            if woken.get("lead_session_id"):
                root_attrs["openworker.lead.session.id"] = woken["lead_session_id"]
        if sw.get("names") and session_title:
            root_attrs["openworker.session.title"] = masker.clean(session_title)
        if lf:
            root_attrs.update({"langfuse.observation.type": "agent", "langfuse.trace.name": "invoke_agent coworker"})
            if usage["unreported_calls"] or usage["failed_calls"]:
                root_attrs["langfuse.trace.tags"] = ["usage-incomplete"]
            if sw.get("prompts") and first_user_text:
                text = first_user_text if sw.get("context") else cut_context(first_user_text)
                root_attrs["langfuse.trace.input"] = root_attrs["langfuse.observation.input"] = masker.clean(text)
            if sw.get("responses") and last_reply_text:
                root_attrs["langfuse.trace.output"] = root_attrs["langfuse.observation.output"] = masker.clean(last_reply_text)
        events = [{"name": f"openworker.notice.{s.get('name')}", "at": s.get("started_at") or turn["started_at"],
                   "attrs": {"openworker.step.id": s["step_id"]}}
                  for s in sorted(steps, key=lambda s: s.get("seq") or 0) if s.get("kind") == "notice"]
        spans.insert(0, {
            "_mark": tid, "_notices": [s["step_id"] for s in steps if s.get("kind") == "notice"],
            "trace_id": trace, "span_id": root_id, "parent_span_id": None,
            "name": "invoke_agent coworker", "kind": SPAN_INTERNAL, "start_ms": turn["started_at"],
            "end_ms": end_ms, "attrs": root_attrs, "events": events,
            "error": turn.get("status") if turn.get("status") == "error" else None,
        })
    return spans


# -- the made-up test recording (E9c) ------------------------------------------------------------
TEST_SESSION = "openworker-test"


def sample_spans(now_ms: int, dialect: str) -> list[dict[str, Any]]:
    """One trace with one model span and one tool span, fixed made-up values, tagged
    `openworker-test`. Nothing in it comes from a session."""
    turn_id = f"openworker-test-{now_ms}"
    turn = {"turn_id": turn_id, "session_id": TEST_SESSION, "seq": 1, "kind": "run", "trigger": "user",
            "status": "completed", "approval_mode": "ask", "attended": True,
            "started_at": now_ms - 1500, "duration_ms": 1500}
    steps = [
        {"step_id": f"{turn_id}-model", "turn_id": turn_id, "session_id": TEST_SESSION, "seq": 1,
         "kind": "model", "name": "model", "started_at": now_ms - 1400, "duration_ms": 900, "status": "ok",
         "attrs": {"model": "openworker:test-model", "provider": "openworker", "purpose": "main",
                   "stream": False, "finish_reason": "tool_calls",
                   "usage": {"reported": True, "input_fresh": 100, "cache_read": None, "cache_write": None,
                             "output": 10, "reasoning": None}}},
        {"step_id": f"{turn_id}-tool", "turn_id": turn_id, "session_id": TEST_SESSION, "seq": 2,
         "kind": "tool", "name": "test_tool", "call_id": "test-call", "started_at": now_ms - 400,
         "duration_ms": 200, "status": "ok", "attrs": {"result_bytes": 2}},
    ]
    sw = {k: False for k in SWITCHES}
    spans = build_turn_spans(turn, steps, sw=sw, dialect=dialect, get_piece=lambda _k: None, masker=Masker())
    if dialect == "langfuse":
        spans[0]["attrs"]["langfuse.trace.tags"] = [TEST_SESSION]
    return spans


# -- OTLP/HTTP JSON ------------------------------------------------------------------------------
def _value(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_value(x) for x in v]}}
    return {"stringValue": str(v)}


def _attrs(d: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": k, "value": _value(v)} for k, v in d.items() if v is not None]


def _ns(ms: int) -> str:
    return str(int(ms) * 1_000_000)


def resource_attrs(*, app_version: str, machine_name: Optional[str] = None) -> dict[str, Any]:
    out = {"service.name": SERVICE_NAME, "service.version": app_version or "unknown",
           "openworker.export.schema": SCHEMA}
    if machine_name:  # only with capture.names
        out["host.name"] = machine_name
    return out


def encode_otlp(groups: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> dict[str, Any]:
    """`[(resource attributes, spans)]` -> the OTLP/HTTP JSON body."""
    out = []
    for resource, spans in groups:
        enc = []
        for s in spans:
            span = {
                "traceId": s["trace_id"], "spanId": s["span_id"], "name": s["name"], "kind": s["kind"],
                "startTimeUnixNano": _ns(s["start_ms"]), "endTimeUnixNano": _ns(s["end_ms"]),
                "attributes": _attrs(s["attrs"]),
                "events": [{"timeUnixNano": _ns(e["at"]), "name": e["name"], "attributes": _attrs(e["attrs"])}
                           for e in s.get("events") or []],
            }
            if s.get("parent_span_id"):
                span["parentSpanId"] = s["parent_span_id"]
            if s.get("error"):
                span["status"] = {"code": STATUS_ERROR, "message": s["error"]}
            enc.append(span)
        out.append({"resource": {"attributes": _attrs(resource)},
                    "scopeSpans": [{"scope": {"name": SCOPE_NAME, "version": ATTRIBUTES_VERSION}, "spans": enc}]})
    return {"resourceSpans": out}
