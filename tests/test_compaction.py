"""Manual and automatic conversation compaction through the public session API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from coworker.compaction import (
    RECENT_USER_MESSAGE_MAX_TOKENS,
    approx_token_count,
    recent_user_messages,
)
from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
)
from coworker.providers.base import TokenUsage
from coworker.server import SessionManager, create_app


class RecordingProvider(ProviderClient):
    def __init__(self, turns: list[AssistantTurn]) -> None:
        self.turns = list(turns)
        self.requests: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.requests.append(messages)
        return self.turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _turn(text: str, *, context_tokens: int = 0) -> AssistantTurn:
    usage = TokenUsage(input=context_tokens) if context_tokens else None
    return AssistantTurn(text=text, finish_reason="stop", usage=usage)


def _drain(ws) -> list[dict]:
    events = []
    while True:
        event = ws.receive_json()
        events.append(event)
        if event["type"] == "turn_done":
            return events


def _messages_contain(messages: list[dict], text: str) -> bool:
    return any(text in str(message.get("content", "")) for message in messages)


def _receive_turn_start(ws) -> dict:
    while True:
        event = ws.receive_json()
        assert event["type"] != "input_rejected", event
        if event["type"] == "turn_start":
            return event


def test_slash_compact_is_an_operation_and_replaces_provider_history(tmp_path):
    provider = RecordingProvider(
        [
            _turn("The launch code is ORCHID."),
            _turn("Remember the launch code ORCHID and the user's request."),
            _turn("ORCHID"),
        ]
    )
    manager = SessionManager(workspace=tmp_path, provider=provider)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/__manual") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "Remember launch code ORCHID."})
        _drain(ws)

        ws.send_json({"type": "compact"})
        first_compact_event = _receive_turn_start(ws)
        compact_events = [first_compact_event, *_drain(ws)]

        assert [e["type"] for e in compact_events] == [
            "turn_start",
            "context_compacted",
            "turn_end",
            "turn_done",
        ]
        assert compact_events[1]["data"]["automatic"] is False
        assert compact_events[1]["data"]["context_tokens"] > 0

        ws.send_json({"type": "user_message", "text": "What is the launch code?"})
        _drain(ws)

    assert len(provider.requests) == 3
    assert _messages_contain(
        provider.requests[1], "CONTEXT CHECKPOINT COMPACTION"
    )
    assert not _messages_contain(provider.requests[2], "The launch code is ORCHID.")
    assert _messages_contain(
        provider.requests[2],
        "Remember the launch code ORCHID and the user's request.",
    )
    assert _messages_contain(provider.requests[2], "What is the launch code?")

    saved = manager.session_store.load("__manual")
    assert saved is not None
    assert all(m.get("content") != "/compact" for m in saved.messages)
    marker = next(
        m
        for m in saved.messages
        if m.get("role") == "notice" and m.get("kind") == "context_compaction"
    )
    assert marker["automatic"] is False
    assert marker["summary"].startswith(
        "Another language model started to solve this problem"
    )


def test_automatic_compaction_runs_before_next_model_sample_at_ninety_percent(
    tmp_path,
):
    provider = RecordingProvider(
        [
            _turn("first reply", context_tokens=360_000),
            _turn("summary with the durable facts"),
            _turn("second reply", context_tokens=120),
        ]
    )
    manager = SessionManager(workspace=tmp_path, provider=provider)
    client = TestClient(create_app(manager))

    with client.websocket_connect("/ws/session/__automatic") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(
            {
                "type": "user_message",
                "text": "first",
                "model": "companion:gpt-5.5",
            }
        )
        _drain(ws)

        ws.send_json(
            {
                "type": "user_message",
                "text": "second",
                "model": "companion:gpt-5.5",
            }
        )
        events = _drain(ws)

    assert len(provider.requests) == 3
    assert _messages_contain(provider.requests[1], "CONTEXT CHECKPOINT COMPACTION")
    assert _messages_contain(provider.requests[2], "summary with the durable facts")
    assert _messages_contain(provider.requests[2], "second")
    compacted = [e for e in events if e["type"] == "context_compacted"]
    assert len(compacted) == 1
    assert compacted[0]["data"]["automatic"] is True
    assert 0 < compacted[0]["data"]["context_tokens"] < 360_000


def test_compaction_checkpoint_survives_engine_restart(tmp_path):
    first_provider = RecordingProvider(
        [
            _turn("The deployment region is ap-south-1."),
            _turn("Keep the deployment region ap-south-1."),
        ]
    )
    first_manager = SessionManager(workspace=tmp_path, provider=first_provider)
    first_client = TestClient(create_app(first_manager))
    with first_client.websocket_connect("/ws/session/__restart") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(
            {"type": "user_message", "text": "Deployment region is ap-south-1."}
        )
        _drain(ws)
        ws.send_json({"type": "compact"})
        _drain(ws)

    second_provider = RecordingProvider([_turn("ap-south-1")])
    second_manager = SessionManager(workspace=tmp_path, provider=second_provider)
    second_client = TestClient(create_app(second_manager))
    with second_client.websocket_connect("/ws/session/__restart") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(
            {"type": "user_message", "text": "Which deployment region?"}
        )
        _drain(ws)

    assert len(second_provider.requests) == 1
    assert _messages_contain(
        second_provider.requests[0], "Keep the deployment region ap-south-1."
    )
    assert not _messages_contain(
        second_provider.requests[0], "The deployment region is ap-south-1."
    )


def test_automatic_compaction_waits_below_ninety_percent(tmp_path):
    provider = RecordingProvider(
        [
            _turn("first reply", context_tokens=359_999),
            _turn("second reply", context_tokens=100),
        ]
    )
    manager = SessionManager(workspace=tmp_path, provider=provider)
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/__below") as ws:
        assert ws.receive_json()["type"] == "ready"
        for text in ("first", "second"):
            ws.send_json(
                {"type": "user_message", "text": text, "model": "gpt-5.5"}
            )
            events = _drain(ws)
            assert all(e["type"] != "context_compacted" for e in events)

    assert len(provider.requests) == 2
    assert not any(
        _messages_contain(request, "CONTEXT CHECKPOINT COMPACTION")
        for request in provider.requests
    )


def test_recent_user_message_budget_matches_codex_and_keeps_utf8_valid():
    messages = [
        {"role": "user", "content": "α" * 45_000},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": "latest-" + "🚀" * 5_000},
    ]

    selected = recent_user_messages(messages)

    assert selected[-1]["content"].startswith("latest-")
    assert selected[0]["content"].startswith("α")
    assert "chars truncated" in selected[0]["content"]
    assert (
        sum(approx_token_count(m["content"]) for m in selected)
        <= RECENT_USER_MESSAGE_MAX_TOKENS + 16
    )
