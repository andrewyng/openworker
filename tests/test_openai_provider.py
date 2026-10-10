"""OpenAI provider — coverage for what test_providers.py (complete/stream basics,
param-fix retries) and test_provider_router.py (JSON-form tool-call salvage) don't
reach: API-key resolution, client bootstrap, the Qwen/Hermes XML tool-call template,
salvage gating inside complete()/stream(), and stream edge cases. SDK-free: fakes
mimic the OpenAI SDK's chat.completions surface with SimpleNamespace objects, the
same pattern test_providers.py uses."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from coworker.providers import OpenAIProvider, ToolCall
from coworker.providers.openai_provider import (
    _coerce_param,
    _salvage_tool_calls_from_text,
    resolve_api_key,
)

# -- fakes ------------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.chat = SimpleNamespace(completions=_FakeCompletions(response))


class _StreamClient:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: iter(chunks))
        )


def _response(content=None, tool_calls=None, finish_reason="stop"):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _chunk(content=None, tool_call=None, finish=None):
    delta = SimpleNamespace(
        content=content, tool_calls=[tool_call] if tool_call else None
    )
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


class _Secrets:
    def __init__(self, profile):
        self._profile = profile

    def get(self, key):
        assert key == "provider:openai"
        return self._profile


_FS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "parameters": {
                "type": "object",
                "properties": {"recursive": {"type": "boolean"}},
            },
        },
    },
]


# -- key resolution / client bootstrap ------------------------------------------------


def test_resolve_api_key_env_wins(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    assert resolve_api_key(_Secrets({"api_key": "sk-stored"})) == "sk-env"


def test_resolve_api_key_falls_back_to_secret_store(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert resolve_api_key(_Secrets({"api_key": "sk-stored"})) == "sk-stored"
    assert resolve_api_key(_Secrets(None)) is None  # no stored profile
    assert resolve_api_key(_Secrets({})) is None  # profile without api_key
    assert resolve_api_key(None) is None  # no secret store at all


def test_ensure_client_without_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No model API key"):
        OpenAIProvider()._ensure_client()


def test_ensure_client_passes_key_and_base_url(monkeypatch):
    captured: dict = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)

    OpenAIProvider(api_key="sk-x")._ensure_client()
    assert captured == {"api_key": "sk-x"}  # no base_url kwarg when unset

    captured.clear()
    OpenAIProvider(api_key="sk-x", base_url="http://localhost:11434/v1")._ensure_client()
    assert captured == {"api_key": "sk-x", "base_url": "http://localhost:11434/v1"}


# -- Qwen/Hermes XML tool-call template (salvage path 1b) ------------------------------


def test_salvage_qwen_xml_function_call():
    # qwen3-coder's native template: nested XML, not JSON, wrapped in <tool_call>.
    text = (
        "<tool_call>\n<function=write_file>\n"
        "<parameter=path>\nhello.txt\n</parameter>\n"
        "<parameter=content>\nHello, world!\n</parameter>\n"
        "</function>\n</tool_call>"
    )
    calls = _salvage_tool_calls_from_text(text, _FS_TOOLS)
    assert len(calls) == 1
    assert calls[0].name == "write_file"
    assert calls[0].arguments == {"path": "hello.txt", "content": "Hello, world!"}


def test_salvage_qwen_xml_keeps_free_text_verbatim():
    # File content with embedded whitespace must survive as-is (stripped, not coerced).
    text = (
        "<function=write_file><parameter=path>a.md</parameter>"
        "<parameter=content>line one\nline two</parameter></function>"
    )
    calls = _salvage_tool_calls_from_text(text, _FS_TOOLS)
    assert calls[0].arguments["content"] == "line one\nline two"


def test_salvage_qwen_xml_coerces_json_params():
    text = "<function=list_files><parameter=recursive>true</parameter></function>"
    calls = _salvage_tool_calls_from_text(text, _FS_TOOLS)
    assert calls[0].arguments == {"recursive": True}


def test_salvage_qwen_xml_filters_unknown_function():
    text = "<function=rm_rf><parameter=path>/</parameter></function>"
    assert _salvage_tool_calls_from_text(text, _FS_TOOLS) == []


def test_salvage_qwen_xml_without_schemas_accepts_any_name():
    text = "<function=anything><parameter=x>1</parameter></function>"
    calls = _salvage_tool_calls_from_text(text, None)
    assert calls[0].name == "anything"
    assert calls[0].arguments == {"x": 1}


def test_salvage_qwen_xml_multiple_blocks_renumbered():
    text = (
        "<function=list_files><parameter=recursive>true</parameter></function>"
        "<function=write_file><parameter=path>a</parameter>"
        "<parameter=content>b</parameter></function>"
    )
    calls = _salvage_tool_calls_from_text(text, _FS_TOOLS)
    assert [c.id for c in calls] == ["call_salvaged_0", "call_salvaged_1"]
    assert [c.name for c in calls] == ["list_files", "write_file"]


def test_coerce_param():
    assert _coerce_param(" 42 ") == 42
    assert _coerce_param("true") is True
    assert _coerce_param('{"a":1}') == {"a": 1}
    assert _coerce_param("two words") == "two words"  # whitespace → verbatim
    assert _coerce_param('"quoted"') == '"quoted"'  # JSON strings stay verbatim


# -- salvage gating inside complete() --------------------------------------------------


def test_complete_salvages_text_form_tool_call():
    content = '<tool_call>{"name": "list_files", "arguments": {"recursive": true}}</tool_call>'
    provider = OpenAIProvider(client=_FakeClient(_response(content=content)))
    turn = provider.complete(
        model="qwen3", messages=[{"role": "user", "content": "ls"}], tools=_FS_TOOLS
    )
    assert turn.text is None  # salvaged calls replace the text
    assert [c.name for c in turn.tool_calls] == ["list_files"]
    assert turn.tool_calls[0].arguments == {"recursive": True}


def test_complete_keeps_text_when_no_tools_offered():
    # Salvage is gated on tools being requested — never rewrites a plain chat turn.
    content = '<tool_call>{"name": "list_files", "arguments": {}}</tool_call>'
    provider = OpenAIProvider(client=_FakeClient(_response(content=content)))
    turn = provider.complete(model="qwen3", messages=[{"role": "user", "content": "hi"}])
    assert turn.text == content
    assert turn.tool_calls == []


def test_complete_prefers_structured_tool_calls_over_text():
    # When the server DID populate tool_calls, text is never reinterpreted.
    tc = SimpleNamespace(
        id="call_1", function=SimpleNamespace(name="list_files", arguments="{}")
    )
    content = '{"name": "write_file", "arguments": {"path": "x", "content": "y"}}'
    provider = OpenAIProvider(
        client=_FakeClient(_response(content=content, tool_calls=[tc]))
    )
    turn = provider.complete(
        model="gpt-5.5", messages=[{"role": "user", "content": "go"}], tools=_FS_TOOLS
    )
    assert turn.text == content
    assert [c.name for c in turn.tool_calls] == ["list_files"]


# -- stream edge cases -----------------------------------------------------------------


def test_stream_salvages_text_form_tool_call_at_end():
    parts = ["<tool_call>", '{"name": "list_files",', ' "arguments": {}}', "</tool_call>"]
    chunks = [_chunk(content=p) for p in parts] + [_chunk(finish="stop")]
    provider = OpenAIProvider(client=_StreamClient(chunks))
    out = list(provider.stream(model="qwen3", messages=[], tools=_FS_TOOLS))
    # Deltas still stream out as text (the UI shows them live) …
    assert "".join(c.text_delta for c in out if c.text_delta) == "".join(parts)
    # … but the final turn carries the recovered structured call, not the text.
    turn = out[-1].turn
    assert turn.text is None
    assert [c.name for c in turn.tool_calls] == ["list_files"]


def test_stream_bad_tool_args_surface_raw():
    tc = SimpleNamespace(
        index=0, id="call_1", function=SimpleNamespace(name="f", arguments="{not json")
    )
    chunks = [_chunk(tool_call=tc), _chunk(finish="tool_calls")]
    provider = OpenAIProvider(client=_StreamClient(chunks))
    turn = list(provider.stream(model="gpt-5.5", messages=[]))[-1].turn
    assert turn.tool_calls == [
        ToolCall(id="call_1", name="f", arguments={"_raw": "{not json"})
    ]


def test_stream_orders_parallel_tool_calls_by_index():
    # Chunks may interleave; the final list is ordered by tool-call index.
    second = SimpleNamespace(
        index=1, id="call_b", function=SimpleNamespace(name="b", arguments="{}")
    )
    first = SimpleNamespace(
        index=0, id="call_a", function=SimpleNamespace(name="a", arguments="")
    )
    chunks = [_chunk(tool_call=second), _chunk(tool_call=first), _chunk(finish="tool_calls")]
    provider = OpenAIProvider(client=_StreamClient(chunks))
    turn = list(provider.stream(model="gpt-5.5", messages=[]))[-1].turn
    assert [c.id for c in turn.tool_calls] == ["call_a", "call_b"]
    assert turn.tool_calls[0].arguments == {}  # empty accumulated args → {}


def test_stream_skips_chunks_without_choices():
    # Compat servers emit keep-alive/usage chunks with no choices; they must not crash.
    empty = SimpleNamespace(choices=[])
    chunks = [empty, _chunk(content="ok"), _chunk(finish="stop")]
    provider = OpenAIProvider(client=_StreamClient(chunks))
    out = list(provider.stream(model="gpt-5.5", messages=[]))
    assert out[-1].turn.text == "ok"


def test_stream_reasoning_attr_variant():
    # xAI/OpenRouter put thinking text on `reasoning` (not `reasoning_content`).
    delta = SimpleNamespace(content=None, tool_calls=None, reasoning="thinking…")
    chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=None)]
    )
    chunks = [chunk, _chunk(content="hi"), _chunk(finish="stop")]
    provider = OpenAIProvider(client=_StreamClient(chunks))
    out = list(provider.stream(model="grok-4", messages=[]))
    assert [c.reasoning_delta for c in out if c.reasoning_delta] == ["thinking…"]
    turn = out[-1].turn
    assert turn.reasoning == "thinking…"
    assert turn.text == "hi"


def test_complete_picks_up_reasoning_attr():
    message = SimpleNamespace(content="answer", tool_calls=None, reasoning="because")
    choice = SimpleNamespace(message=message, finish_reason="stop")
    provider = OpenAIProvider(
        client=_FakeClient(SimpleNamespace(choices=[choice]))
    )
    turn = provider.complete(model="grok-4", messages=[{"role": "user", "content": "?"}])
    assert turn.reasoning == "because"
    assert turn.text == "answer"
