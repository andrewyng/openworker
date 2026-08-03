"""OpenAI Responses provider — message/tool conversion, complete(), stream(), sidecar
replay, param-fix retries. SDK-free: the fake client mimics the OpenAI SDK's
`responses.create` surface with dicts/SimpleNamespace objects, the same pattern the
Gemini/Anthropic provider tests use."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from coworker.providers.openai_responses import (
    OpenAIResponsesProvider,
    _param_fix_retry,
    convert_messages,
    convert_tools,
)

# -- fakes ------------------------------------------------------------------------


class _FakeClient:
    """Records the kwargs passed to responses.create; raises queued errors first (to
    exercise the param-fix retries), then returns the canned response — or, when the
    request asked for stream=True, an iterator of canned events."""

    def __init__(self, response=None, events=None, errors=None):
        self.kwargs: dict = {}
        self.calls: list[dict] = []
        errors = list(errors or [])

        def create(**kwargs):
            self.kwargs = kwargs
            self.calls.append(kwargs)
            if errors:
                raise errors.pop(0)
            if kwargs.get("stream"):
                return iter(events or [])
            return response

        self.responses = SimpleNamespace(create=create)


def _response(output, status="completed", incomplete_details=None):
    return SimpleNamespace(
        output=output, status=status, incomplete_details=incomplete_details
    )


def _message_item(text):
    return {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def _reasoning_item(summaries, encrypted="enc-blob"):
    item = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [{"type": "summary_text", "text": s} for s in summaries],
    }
    if encrypted:
        item["encrypted_content"] = encrypted
    return item


def _call_item(call_id, name, arguments):
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


# -- message conversion -------------------------------------------------------------


def test_convert_extracts_leading_system_as_instructions():
    instructions, items = convert_messages(
        [
            {"role": "system", "content": "be helpful"},
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert instructions == "be helpful\n\nbe brief"
    assert items == [{"role": "user", "content": "hi"}]


def test_convert_mid_thread_system_stays_a_message():
    _, items = convert_messages(
        [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "steering"},
        ]
    )
    assert items[1] == {"role": "system", "content": "steering"}


def test_convert_user_parts_to_input_parts():
    _, items = convert_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
                    },
                    {
                        "type": "file",
                        "file": {
                            "filename": "report.pdf",
                            "file_data": "data:application/pdf;base64,JVBERi0=",
                        },
                    },
                ],
            }
        ]
    )
    assert items[0]["content"] == [
        {"type": "input_text", "text": "what is this"},
        {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo="},
        {
            "type": "input_file",
            "filename": "report.pdf",
            "file_data": "data:application/pdf;base64,JVBERi0=",
        },
    ]


def test_convert_synthesizes_assistant_and_tool_items():
    # No `_openai` sidecar (history from another provider): items are rebuilt from the
    # canonical fields, and foreign toolu_ ids still pair call → output.
    _, items = convert_messages(
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "on it",
                "tool_calls": [
                    {
                        "id": "toolu_abc",
                        "type": "function",
                        "function": {"name": "f", "arguments": '{"x": 1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "toolu_abc", "content": '{"ok": true}'},
        ]
    )
    assert items[1] == {"role": "assistant", "content": "on it"}
    assert items[2] == {
        "type": "function_call",
        "call_id": "toolu_abc",
        "name": "f",
        "arguments": '{"x": 1}',
    }
    assert items[3] == {
        "type": "function_call_output",
        "call_id": "toolu_abc",
        "output": '{"ok": true}',
    }


def test_convert_replays_openai_sidecar_verbatim():
    sidecar_items = [
        _reasoning_item(["thinking"], encrypted="blob"),
        _message_item("on it"),
        _call_item("call_1", "f", "{}"),
    ]
    _, items = convert_messages(
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "on it",
                "tool_calls": [
                    {"id": "call_1", "function": {"name": "f", "arguments": "{}"}}
                ],
                "_openai": {"items": sidecar_items},
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "done"},
        ]
    )
    # The sidecar items go in verbatim — no synthesized duplicates alongside.
    assert items[1:4] == sidecar_items
    assert items[4]["type"] == "function_call_output"


def test_convert_ignores_foreign_sidecars():
    _, items = convert_messages(
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "hi",
                "_gemini": {"text_sig": "abc"},
            },
        ]
    )
    assert items[1] == {"role": "assistant", "content": "hi"}


def test_convert_empty_assistant_tool_turn_emits_no_message_item():
    _, items = convert_messages(
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "f", "arguments": "{}"}}
                ],
            },
        ]
    )
    assert [i.get("type") for i in items[1:]] == ["function_call"]


# -- tool schema conversion ----------------------------------------------------------


def test_convert_tools_flattens_function_schemas():
    tools = convert_tools(
        [
            {"type": "function", "function": {"name": "bare"}},
            {
                "type": "function",
                "function": {
                    "name": "full",
                    "description": "does things",
                    "parameters": {
                        "type": "object",
                        "properties": {"x": {"type": "integer"}},
                    },
                },
            },
        ]
    )
    assert tools[0] == {"type": "function", "name": "bare"}
    assert tools[1]["name"] == "full" and "function" not in tools[1]
    assert tools[1]["parameters"]["properties"] == {"x": {"type": "integer"}}
    assert convert_tools(None) == []


# -- complete() ----------------------------------------------------------------------


def test_complete_text_turn_and_request_shape():
    fake = _FakeClient(response=_response([_message_item("hello")]))
    provider = OpenAIResponsesProvider(client=fake)
    turn = provider.complete(
        model="gpt-5.6-sol",
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
    )
    assert turn.text == "hello" and turn.finish_reason == "stop"
    assert not turn.has_tool_calls and turn.extras == {}
    assert fake.kwargs["model"] == "gpt-5.6-sol"
    assert fake.kwargs["instructions"] == "sys"
    assert fake.kwargs["store"] is False
    assert fake.kwargs["include"] == ["reasoning.encrypted_content"]
    assert fake.kwargs["reasoning"] == {"summary": "auto"}


def test_complete_parses_function_calls_with_call_ids():
    fake = _FakeClient(
        response=_response(
            [
                _message_item("on it"),
                _call_item("call_a", "write_file", '{"path": "a.txt"}'),
                _call_item("call_b", "read_file", "not json"),
            ]
        )
    )
    provider = OpenAIResponsesProvider(client=fake)
    turn = provider.complete(model="m", messages=[{"role": "user", "content": "go"}])
    assert turn.text == "on it" and turn.finish_reason == "tool_calls"
    assert [(c.id, c.name) for c in turn.tool_calls] == [
        ("call_a", "write_file"),
        ("call_b", "read_file"),
    ]
    assert turn.tool_calls[0].arguments == {"path": "a.txt"}
    assert turn.tool_calls[1].arguments == {"_raw": "not json"}


def test_complete_surfaces_reasoning_summary_and_sidecar():
    items = [
        _reasoning_item(["plan a", " then b"], encrypted="blob"),
        _message_item("answer"),
        _call_item("call_1", "f", "{}"),
    ]
    provider = OpenAIResponsesProvider(client=_FakeClient(response=_response(items)))
    turn = provider.complete(model="m", messages=[{"role": "user", "content": "x"}])
    assert turn.reasoning == "plan a then b"
    assert turn.extras["_openai"]["items"] == items


def test_complete_drops_unresolvable_reasoning_from_sidecar():
    # No encrypted_content (e.g. `include` got param-fix-dropped): replaying the item
    # under store:false would 400, so it must not enter the sidecar.
    items = [
        _reasoning_item(["hmm"], encrypted=None),
        _call_item("call_1", "f", "{}"),
    ]
    provider = OpenAIResponsesProvider(client=_FakeClient(response=_response(items)))
    turn = provider.complete(model="m", messages=[{"role": "user", "content": "x"}])
    assert turn.reasoning == "hmm"  # still displayed…
    kinds = [i["type"] for i in turn.extras["_openai"]["items"]]
    assert kinds == ["function_call"]  # …but never replayed


def test_complete_plain_text_has_no_sidecar():
    provider = OpenAIResponsesProvider(
        client=_FakeClient(response=_response([_message_item("plain")]))
    )
    turn = provider.complete(model="m", messages=[{"role": "user", "content": "x"}])
    assert turn.extras == {}


def test_complete_maps_incomplete_max_tokens_to_length():
    provider = OpenAIResponsesProvider(
        client=_FakeClient(
            response=_response(
                [_message_item("truncat")],
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            )
        )
    )
    turn = provider.complete(model="m", messages=[{"role": "user", "content": "x"}])
    assert turn.finish_reason == "length"


def test_complete_filters_and_aliases_settings():
    fake = _FakeClient(response=_response([_message_item("x")]))
    provider = OpenAIResponsesProvider(client=fake)
    provider.complete(
        model="m",
        messages=[{"role": "user", "content": "x"}],
        temperature=0.2,
        max_tokens=512,  # chat alias → max_output_tokens
        frequency_penalty=0.5,  # not a Responses param → dropped
        reasoning_effort="high",  # no effort knob in v1 → dropped
    )
    assert fake.kwargs["temperature"] == 0.2
    assert fake.kwargs["max_output_tokens"] == 512
    assert "max_tokens" not in fake.kwargs
    assert "frequency_penalty" not in fake.kwargs
    assert "reasoning_effort" not in fake.kwargs


def test_complete_passes_flat_tools():
    fake = _FakeClient(response=_response([_message_item("x")]))
    provider = OpenAIResponsesProvider(client=fake)
    provider.complete(
        model="m",
        messages=[{"role": "user", "content": "x"}],
        tools=[{"type": "function", "function": {"name": "f"}}],
    )
    assert fake.kwargs["tools"] == [{"type": "function", "name": "f"}]


def test_complete_parses_attr_style_sdk_objects():
    # The real SDK returns typed objects, not dicts — the parser must getattr its way in.
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                id="msg_1",
                role="assistant",
                content=[SimpleNamespace(type="output_text", text="hi", annotations=None)],
            ),
            SimpleNamespace(
                type="function_call",
                id="fc_1",
                call_id="call_1",
                name="f",
                arguments='{"a": 1}',
            ),
        ],
        status="completed",
        incomplete_details=None,
    )
    provider = OpenAIResponsesProvider(client=_FakeClient(response=response))
    turn = provider.complete(model="m", messages=[{"role": "user", "content": "x"}])
    assert turn.text == "hi"
    assert turn.tool_calls[0].id == "call_1"
    assert turn.tool_calls[0].arguments == {"a": 1}


# -- param-fix retries ---------------------------------------------------------------


def test_param_fix_drops_named_parameter():
    kwargs = {"model": "m", "input": [], "temperature": 0.2}
    fixed = _param_fix_retry(
        kwargs, Exception("Unsupported parameter: 'temperature' is not supported")
    )
    assert "temperature" not in fixed and kwargs["temperature"] == 0.2  # copy, not mutate


def test_param_fix_dotted_name_drops_top_level():
    fixed = _param_fix_retry(
        {"model": "m", "input": [], "reasoning": {"summary": "auto"}},
        Exception("Unsupported parameter: 'reasoning.summary'"),
    )
    assert "reasoning" not in fixed


def test_param_fix_reraises_unknown_errors():
    with pytest.raises(Exception, match="rate limit"):
        _param_fix_retry({"model": "m", "input": []}, Exception("rate limit exceeded"))


def test_complete_retries_dropping_rejected_params():
    # A non-reasoning model rejecting `reasoning` then `include` — both retried away.
    fake = _FakeClient(
        response=_response([_message_item("ok")]),
        errors=[
            Exception("Unsupported parameter: 'reasoning'"),
            Exception("Unsupported value: 'include[0]'"),
        ],
    )
    provider = OpenAIResponsesProvider(client=fake)
    turn = provider.complete(model="gpt-4.1", messages=[{"role": "user", "content": "x"}])
    assert turn.text == "ok"
    assert len(fake.calls) == 3
    assert "reasoning" not in fake.kwargs and "include" not in fake.kwargs


# -- stream() ------------------------------------------------------------------------


def test_stream_yields_deltas_then_final_turn_from_completed_event():
    final = _response(
        [
            _reasoning_item(["mull it over"], encrypted="blob"),
            _message_item("hello"),
        ]
    )
    events = [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta="mull "),
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta="it over"),
        SimpleNamespace(type="response.output_text.delta", delta="hel"),
        SimpleNamespace(type="response.output_text.delta", delta="lo"),
        SimpleNamespace(type="response.completed", response=final),
    ]
    provider = OpenAIResponsesProvider(client=_FakeClient(events=events))
    out = list(provider.stream(model="m", messages=[{"role": "user", "content": "x"}]))
    assert [c.reasoning_delta for c in out if c.reasoning_delta] == ["mull ", "it over"]
    assert [c.text_delta for c in out if c.text_delta] == ["hel", "lo"]
    turn = out[-1].turn
    assert turn.text == "hello" and turn.reasoning == "mull it over"
    assert turn.finish_reason == "stop"
    # Encrypted reasoning is replay-worthy even without function calls.
    assert [i["type"] for i in turn.extras["_openai"]["items"]] == [
        "reasoning",
        "message",
    ]


def test_stream_final_turn_carries_tool_calls_and_sidecar():
    final = _response(
        [
            _reasoning_item(["plan"], encrypted="blob"),
            _call_item("call_1", "f", '{"x": 1}'),
        ]
    )
    events = [SimpleNamespace(type="response.completed", response=final)]
    provider = OpenAIResponsesProvider(client=_FakeClient(events=events))
    turn = list(
        provider.stream(model="m", messages=[{"role": "user", "content": "x"}])
    )[-1].turn
    assert turn.finish_reason == "tool_calls"
    assert turn.tool_calls[0].arguments == {"x": 1}
    assert [i["type"] for i in turn.extras["_openai"]["items"]] == [
        "reasoning",
        "function_call",
    ]


def test_stream_without_terminal_event_keeps_accumulated_text():
    events = [SimpleNamespace(type="response.output_text.delta", delta="partial")]
    provider = OpenAIResponsesProvider(client=_FakeClient(events=events))
    turn = list(
        provider.stream(model="m", messages=[{"role": "user", "content": "x"}])
    )[-1].turn
    assert turn.text == "partial" and turn.finish_reason is None


def test_stream_requests_stream_flag():
    fake = _FakeClient(events=[])
    provider = OpenAIResponsesProvider(client=fake)
    list(provider.stream(model="m", messages=[{"role": "user", "content": "x"}]))
    assert fake.kwargs["stream"] is True


# -- round trip ----------------------------------------------------------------------


def test_sidecar_round_trip_replays_what_complete_stored():
    """A tool loop: turn 1's sidecar items must be exactly what turn 2's request replays."""
    items = [
        _reasoning_item(["plan"], encrypted="blob"),
        _call_item("call_1", "f", "{}"),
    ]
    provider = OpenAIResponsesProvider(client=_FakeClient(response=_response(items)))
    turn = provider.complete(model="m", messages=[{"role": "user", "content": "go"}])

    # The engine persists canonical fields + extras (engine._assistant_message):
    assistant_message = {
        "role": "assistant",
        "content": turn.text or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in turn.tool_calls
        ],
        **turn.extras,
    }
    fake2 = _FakeClient(response=_response([_message_item("done")]))
    provider2 = OpenAIResponsesProvider(client=fake2)
    provider2.complete(
        model="m",
        messages=[
            {"role": "user", "content": "go"},
            assistant_message,
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ],
    )
    sent = fake2.kwargs["input"]
    assert sent[1:3] == items  # replayed verbatim, reasoning first
    assert sent[3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "ok",
    }


def test_ensure_client_without_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No model API key"):
        OpenAIResponsesProvider()._ensure_client()


# -- registry routing ----------------------------------------------------------------


def test_registry_routes_blank_endpoint_to_responses():
    from coworker.providers import OpenAIProvider
    from coworker.providers.registry import build_provider_client

    assert isinstance(
        build_provider_client("openai", {}, None), OpenAIResponsesProvider
    )
    assert isinstance(
        build_provider_client("openai", {"base_url": "   "}, None),
        OpenAIResponsesProvider,
    )
    # A custom endpoint (Azure, vLLM, any compat gateway) keeps Chat Completions…
    custom = build_provider_client(
        "openai", {"base_url": "https://my.azure.example/openai/v1"}, None
    )
    assert isinstance(custom, OpenAIProvider)
    # …and so do Ollama and every compat vendor (their own descriptors).
    assert isinstance(build_provider_client("ollama", {}, None), OpenAIProvider)
    assert isinstance(
        build_provider_client("deepseek", {"api_key": "sk-x"}, None), OpenAIProvider
    )
