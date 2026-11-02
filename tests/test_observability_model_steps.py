"""Observability E2, E22, E3: every model call is a model step, with its exact exchange and
correct token counts.

- E3: the shared usage vectors (`golden-phase1/vectors/usage.json`, recording format 1.0
  section 5) go through each provider's own usage code and come out as the step's `usage`.
- E2: a real engine turn records one model step per call, purpose `main`, pointing at the
  reply message it produced; untagged and proxy calls are told apart.
- E22: a call to a local fake server is captured below the SDK: the request rebuilds
  exactly from its pieces, no credential is stored, and `provider_facts` are read.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import aisuite as ai
import pytest

from coworker.engine import TurnEngine
from coworker.observability import Recorder
from coworker.observability import capture
from coworker.observability.engine_hooks import TurnRecording
from coworker.observability.provider_view import RecordingProvider, usage_attrs
from coworker.permissions import PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.providers import anthropic_provider, bedrock_provider, gemini_provider
from coworker.providers import openai_provider, openai_responses
from coworker.providers.base import TokenUsage
from coworker.providers.ollama_context import to_openai_completion
from coworker.tools import ToolRegistry

VECTORS = json.loads(
    (Path(__file__).parent / "corpora/observability/golden-phase1/vectors/usage.json").read_text("utf-8")
)


def _ns(v):
    if isinstance(v, dict):
        return SimpleNamespace(**{k: _ns(x) for k, x in v.items()})
    return v


def _provider_usage(family, raw):
    if family == "anthropic":
        return anthropic_provider._usage_from(_ns(raw))
    if family == "openai_chat":
        return openai_provider._usage_from(_ns(raw))
    if family == "openai_responses":
        return openai_responses._usage_from(_ns(raw))
    if family == "gemini":
        return gemini_provider._usage_from(_ns(raw))
    if family == "bedrock_converse":
        return bedrock_provider._usage_from(raw)
    if family == "ollama":
        # The app turns Ollama's native reply into an OpenAI-shaped one (ollama_context.py).
        return openai_provider._usage_from(_ns(to_openai_completion({"message": {}, **raw})["usage"]))
    raise AssertionError(family)


# The decision reviewers call their own service directly; their usage comes with E23.
PROVIDER_VECTORS = [v for v in VECTORS if v["family"] not in ("openai_decisions", "typesafe_jev")]


@pytest.mark.parametrize("vector", PROVIDER_VECTORS, ids=[v["id"] for v in PROVIDER_VECTORS])
def test_usage_vectors(vector):
    got = usage_attrs(_provider_usage(vector["family"], vector["raw"]))
    assert got == {"reported": True, **vector["expected"]}


def test_a_call_with_no_usage_is_not_reported():
    assert usage_attrs(None) == {"reported": False}
    assert usage_attrs(TokenUsage(input=5, output=1, cache_write_reported=False))["cache_write"] is None


def test_anthropic_stream_totals_come_from_the_final_event():
    start = _ns({"input_tokens": 5, "output_tokens": 1, "cache_read_input_tokens": 5194,
                 "cache_creation_input_tokens": 130})
    final = _ns({"output_tokens": 23, "output_tokens_details": {"thinking_tokens": 0}})
    u = anthropic_provider._merge_final_usage(anthropic_provider._usage_from(start), final)
    assert usage_attrs(u) == {"reported": True, "input_fresh": 5, "cache_read": 5194,
                              "cache_write": 130, "output": 23, "reasoning": 0}


# -- E2: model steps from a real engine turn -------------------------------------------------

class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, turns):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    engine = TurnEngine(
        provider=ScriptedProvider(turns),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="openai:model-a",
    )
    engine.audit_context = {"session_id": "sess_1"}
    rec = Recorder()
    engine.recorder = rec
    return engine, rec


def _run(engine, text):
    async def go():
        return [ev async for ev in engine.run(text, trigger="user")]

    return asyncio.run(go())


def test_every_model_call_is_a_main_model_step(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    engine, rec = _engine(tmp_path, [
        AssistantTurn(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"})],
                      finish_reason="tool_calls",
                      usage=TokenUsage(input=100, output=10, cache_read=50, cache_write_reported=False)),
        AssistantTurn(text="it says hello", finish_reason="stop", usage=None),
    ])
    _run(engine, "read it")
    d = rec.as_dict()
    models = [s for s in d["steps"] if s["kind"] == "model"]
    assert [s["attrs"]["purpose"] for s in models] == ["main", "main"]
    assistants = [m for m in engine.messages if m["role"] == "assistant"]
    assert [s["message_id"] for s in models] == [m["mid"] for m in assistants]
    first, second = models
    assert first["attrs"]["finish_reason"] == "tool_calls"
    assert first["attrs"]["usage"] == {"reported": True, "input_fresh": 100, "cache_read": 50,
                                       "cache_write": None, "output": 10, "reasoning": None}
    assert second["attrs"]["usage"] == {"reported": False}
    assert first["attrs"]["model"] == "openai:model-a" and first["attrs"]["provider"] == "openai"
    assert first["attrs"]["tools_offered"] > 0 and first["attrs"]["tools_hash"]
    assert all(s["turn_id"] == d["turns"][0]["turn_id"] for s in models)
    # canonical order: the context taken at the first model call, then model, tool, model
    assert [s["kind"] for s in d["steps"]] == ["context", "model", "tool", "model"]


def test_a_failed_model_call_is_an_error_step(tmp_path):
    engine, rec = _engine(tmp_path, [RuntimeError("upstream 529 overloaded")])
    _run(engine, "hi")
    [step] = [s for s in rec.as_dict()["steps"] if s["kind"] == "model"]
    assert step["status"] == "error"
    assert step["attrs"]["error"]["type"] == "overloaded"


def test_untagged_and_proxy_calls_are_told_apart():
    rec = Recorder()
    base = RecordingProvider(ScriptedProvider([AssistantTurn(text="a"), AssistantTurn(text="b")]), recorder=rec)
    base.complete(model="openai:model-a", messages=[{"role": "user", "content": "x"}])
    base.tagged(purpose="proxy", turn=None).complete(model="openai:model-a", messages=[])
    purposes = sorted(s["attrs"]["purpose"] for s in rec.as_dict()["steps"])
    assert purposes == ["proxy", "unattributed"]
    assert all(s["turn_id"] in ("", None) for s in rec.as_dict()["steps"])


def test_a_title_tagged_with_an_ended_turn_is_after_turn(tmp_path):
    engine, rec = _engine(tmp_path, [AssistantTurn(text="done"), AssistantTurn(text="Title")])
    _run(engine, "hello")
    turn_id = engine._obs.turn_id
    engine.provider.tagged(purpose="title", turn=turn_id).complete(model=engine.model, messages=[])
    title = next(s for s in rec.as_dict()["steps"] if s["attrs"].get("purpose") == "title")
    assert title["turn_id"] == turn_id and title["after_turn"] is True


# -- E22: the exact exchange, captured below the SDK ------------------------------------------

_REPLY = {
    "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "model-a-2026",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi there"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 30, "completion_tokens": 3, "total_tokens": 33,
              "prompt_tokens_details": {"cached_tokens": 20}},
}


class _Handler(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        _Handler.seen.append((dict(self.headers), body))
        data = json.dumps(_REPLY).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("x-request-id", "req_abc")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def test_the_exact_exchange_is_kept_and_no_key_is_stored(fake_server):
    rec = Recorder()
    sink = TurnRecording(rec, lambda: "sess_x")
    inner = openai_provider.OpenAIProvider(api_key="sk-test-not-real", base_url=fake_server)
    view = RecordingProvider(inner).tagged(purpose="main", sink=sink)
    messages = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "say hi"}]
    turn = view.complete(model="model-a", messages=messages)
    assert turn.text == "hi there"

    [step] = rec.as_dict()["steps"]
    [exchange] = step["content"]["exchange"]
    sent_headers, sent_body = _Handler.seen[-1]
    # Rebuilds exactly, and the stored hash is the hash of the bytes sent.
    assert capture.rebuild_request(exchange, rec.get_piece) == json.loads(sent_body)
    import hashlib
    assert exchange["request"]["body_sha256"] == hashlib.sha256(sent_body).hexdigest()
    assert json.loads(rec.get_piece(exchange["response"]["body_blob"])) == _REPLY
    # No credential anywhere in what is stored.
    stored = json.dumps(step)
    assert "sk-test-not-real" not in stored
    assert "authorization" not in {k.lower() for k in exchange["request"]["headers"]}
    # Metadata without content; facts with fixed names.
    meta = step["attrs"]["provider_meta"][0]
    assert meta["wire"] == "openai_chat" and "messages" not in meta["request"]
    assert "hi there" not in json.dumps(step["attrs"])
    facts = step["attrs"]["provider_facts"]
    assert facts["served_model"] == "model-a-2026"
    assert facts["provider_request_id"] == "req_abc"
    assert facts["provider_total"] == 33
    assert step["attrs"]["usage"] == {"reported": True, "input_fresh": 10, "cache_read": 20,
                                      "cache_write": None, "output": 3, "reasoning": None}


def test_capture_is_off_outside_a_recorded_call(fake_server):
    inner = openai_provider.OpenAIProvider(api_key="sk-test-not-real", base_url=fake_server)
    capture.install()
    assert getattr(capture._active, "log", None) is None
    inner.complete(model="model-a", messages=[{"role": "user", "content": "x"}])
    assert getattr(capture._active, "log", None) is None


DECISION_VECTORS = [v for v in VECTORS if v["family"] in ("openai_decisions", "typesafe_jev")]


@pytest.mark.parametrize("vector", DECISION_VECTORS, ids=[v["id"] for v in DECISION_VECTORS])
def test_decision_reviewer_usage_vectors(vector):
    from coworker.observability.provider_view import _reply_usage

    entries = [{"body": json.dumps({"usage": vector["raw"]})}]
    assert usage_attrs(_reply_usage(entries)) == {"reported": True, **vector["expected"]}
