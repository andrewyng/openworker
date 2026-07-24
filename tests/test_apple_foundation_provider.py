"""Production tests for the optional Apple provider, using an SDK-shaped fake."""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest

from coworker.providers.apple_foundation_provider import (
    AppleFoundationProvider,
    argument_schema,
    canonical_prompt,
)
from coworker.providers.capabilities import capabilities_for
from coworker.providers.registry import get_descriptor


class _Generated:
    def __init__(self, value):
        self.value = value

    def to_dict(self):
        return self.value


class _FakeModel:
    context_size = 4096
    available = True
    reason = None

    def is_available(self):
        return self.available, self.reason


class _FakeSession:
    scripted: ClassVar[list] = []
    instances: ClassVar[list] = []

    def __init__(self, *, model, instructions=None):
        self.model = model
        self.instructions = instructions
        self.calls = []
        self.__class__.instances.append(self)

    async def respond(self, prompt, json_schema=None, options=None):
        self.calls.append((prompt, json_schema, options))
        value = self.__class__.scripted.pop(0)
        if isinstance(value, Exception):
            raise value
        return _Generated(value) if isinstance(value, dict) else value

    async def stream_response(self, prompt, options=None):
        self.calls.append((prompt, None, options))
        for value in self.__class__.scripted.pop(0):
            yield value


class _FakeGenerationOptions:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


@pytest.fixture
def sdk():
    _FakeModel.available = True
    _FakeModel.reason = None
    _FakeSession.scripted = []
    _FakeSession.instances = []
    return SimpleNamespace(
        SystemLanguageModel=_FakeModel,
        LanguageModelSession=_FakeSession,
        GenerationOptions=_FakeGenerationOptions,
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read one file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
]


def test_descriptor_is_keyless_and_experimental():
    descriptor = get_descriptor("apple")
    assert descriptor is not None
    assert descriptor.needs_key is False
    assert descriptor.recommended_model == "system"
    assert "Experimental" in descriptor.blurb


def test_availability_reports_runtime_capacity(sdk):
    status = AppleFoundationProvider(sdk).availability()
    assert status.available and status.code == "available"
    assert status.context_size == 4096


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("Apple Intelligence is not enabled", "apple_intelligence_disabled"),
        ("Model assets are not ready", "model_assets_unavailable"),
        ("Locale is unsupported", "locale_unavailable"),
        ("Device is not eligible", "unsupported_hardware"),
        ("Requires macOS 26", "unsupported_os"),
    ],
)
def test_availability_maps_known_reasons(sdk, reason, code):
    _FakeModel.available = False
    _FakeModel.reason = reason
    status = AppleFoundationProvider(sdk).availability()
    assert status.available is False
    assert status.code == code
    assert status.detail == reason


def test_missing_sdk_is_actionable(monkeypatch):
    provider = AppleFoundationProvider()

    def missing():
        raise ModuleNotFoundError("apple_fm_sdk")

    monkeypatch.setattr(provider, "_module", missing)
    status = provider.availability()
    assert status.code == "sdk_not_installed"
    assert "does not include" in (status.detail or "")


def test_canonical_prompt_separates_system_and_strips_sidecars():
    instructions, prompt = canonical_prompt(
        [
            {"role": "system", "content": "Be exact"},
            {"role": "user", "content": "hello", "_gemini": {"signature": "secret"}},
            {"role": "tool", "name": "read_file", "content": "untrusted"},
        ]
    )
    assert instructions.startswith("Be exact")
    assert "untrusted data" in instructions
    assert "_gemini" not in prompt
    assert "[user]" in prompt
    assert "[tool:read_file]" in prompt


def test_canonical_prompt_flattens_text_parts_without_wire_json():
    _, prompt = canonical_prompt(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extracted PDF text"},
                    {"type": "text", "text": "More text"},
                ],
            }
        ]
    )
    assert "Extracted PDF text\nMore text" in prompt
    assert '"type":"text"' not in prompt


def test_schema_adds_ordering_metadata_recursively():
    schema = argument_schema(TOOLS[0])
    assert schema["x-order"] == ["path"]
    assert schema["additionalProperties"] is False


def test_incompatible_schema_is_omitted_and_plain_response_is_used(sdk):
    _FakeSession.scripted = ["plain"]
    incompatible = [
        {
            "type": "function",
            "function": {
                "name": "choose",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "value": {"anyOf": [{"type": "string"}, {"type": "number"}]}
                    },
                },
            },
        }
    ]
    turn = AppleFoundationProvider(sdk).complete(
        model="system",
        messages=[{"role": "user", "content": "hello"}],
        tools=incompatible,
    )
    assert turn.text == "plain"
    assert _FakeSession.instances[0].calls[0][1] is None


def test_complete_text_uses_provider_boundary(sdk):
    _FakeSession.scripted = ["hello"]
    turn = AppleFoundationProvider(sdk).complete(
        model="system", messages=[{"role": "user", "content": "hi"}]
    )
    assert turn.text == "hello"
    assert turn.finish_reason == "stop"


def test_complete_maps_supported_generation_settings(sdk):
    _FakeSession.scripted = ["hello"]
    AppleFoundationProvider(sdk).complete(
        model="system",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.2,
        max_tokens=64,
        reasoning_effort="none",
    )
    options = _FakeSession.instances[0].calls[0][2]
    assert options.temperature == 0.2
    assert options.maximum_response_tokens == 64
    assert not hasattr(options, "reasoning_effort")


def test_context_limit_error_is_actionable(sdk):
    class ExceededContextWindowSizeError(Exception):
        pass

    _FakeSession.scripted = [ExceededContextWindowSizeError()]
    with pytest.raises(RuntimeError, match="4,096-token context"):
        AppleFoundationProvider(sdk).complete(
            model="system", messages=[{"role": "user", "content": "too long"}]
        )


def test_two_stage_tool_proposal_maps_to_tool_call(sdk):
    _FakeSession.scripted = [
        {"kind": "tool", "text": "I need the file.", "tool_name": "read_file"},
        {"path": "README.md"},
    ]
    turn = AppleFoundationProvider(sdk).complete(
        model="system",
        messages=[{"role": "user", "content": "read the README"}],
        tools=TOOLS,
    )
    assert turn.finish_reason == "tool_calls"
    assert turn.tool_calls[0].name == "read_file"
    assert turn.tool_calls[0].arguments == {"path": "README.md"}
    assert turn.tool_calls[0].id.startswith("apple_")
    assert len(_FakeSession.instances[0].calls) == 2


def test_stream_converts_snapshots_to_deltas(sdk):
    _FakeSession.scripted = [["H", "Hel", "Hello"]]
    chunks = list(
        AppleFoundationProvider(sdk).stream(
            model="system", messages=[{"role": "user", "content": "hi"}]
        )
    )
    assert [chunk.text_delta for chunk in chunks[:-1]] == ["H", "el", "lo"]
    assert chunks[-1].turn.text == "Hello"


def test_capabilities_are_conservative():
    caps = capabilities_for("apple:system")
    assert caps.tools and caps.streaming
    assert not caps.vision and not caps.pdf and not caps.parallel_tool_calls
