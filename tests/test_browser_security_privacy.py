from __future__ import annotations

import json

from coworker.browser_security.privacy import (
    BROWSER_INPUT_REDACTED,
    BROWSER_OBSERVATION_OMITTED,
    durable_browser_event,
    redact_browser_error,
    scrub_browser_audit_event,
    scrub_browser_messages_for_storage,
)


def _serialized(value):
    return json.dumps(value, sort_keys=True)


def test_persisted_message_scrub_removes_observation_inputs_and_provider_sidecars():
    messages = [
        {"role": "user", "content": "Log me in"},
        {
            "role": "assistant",
            "content": "",
            "extras": {"raw": "provider-secret"},
            "thinking": "page says bearer-secret",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "browser_fill",
                        "arguments": json.dumps(
                            {
                                "tab_id": "tab-1",
                                "snapshot_id": "snap-1",
                                "ref": "e4",
                                "text": "bearer-secret",
                                "cookies": {"sid": "cookie-secret"},
                                "future_page_field": "private-page-text",
                            }
                        ),
                    },
                    "provider_private": "raw-secret",
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": '{"snapshot":"private-page-text","url":"https://example.com/private?q=secret"}',
            "screenshot": "base64-secret",
        },
    ]
    scrubbed = scrub_browser_messages_for_storage(messages)
    blob = _serialized(scrubbed)
    for secret in (
        "bearer-secret",
        "cookie-secret",
        "private-page-text",
        "provider-secret",
        "raw-secret",
        "base64-secret",
        "/private",
        "q=secret",
    ):
        assert secret not in blob
    assert scrubbed[2]["content"] == BROWSER_OBSERVATION_OMITTED
    args = json.loads(scrubbed[1]["tool_calls"][0]["function"]["arguments"])
    assert args["text"] == BROWSER_INPUT_REDACTED
    assert args["tab_id"] == "tab-1"
    # The active in-memory list was not mutated.
    assert "bearer-secret" in _serialized(messages)


def test_non_browser_messages_are_unchanged():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "c",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"notes.txt"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c", "content": "contents"},
    ]
    assert scrub_browser_messages_for_storage(messages) == messages


def test_browser_audit_scrub_has_strict_allowlist_behavior():
    event = {
        "session_id": "s",
        "tool": "browser_open_url",
        "status": "failed",
        "arguments": {
            "url": "https://example.com/private/path?token=bearer-secret",
            "headers": {"Authorization": "Bearer bearer-secret"},
            "text": "typed-secret",
        },
        "result": {"snapshot": "private-page-text"},
        "result_preview": "private-page-text",
        "reason": "ACTION_TIMEOUT at https://example.com/private?token=secret",
        "screenshot": "base64-secret",
        "cookies": {"sid": "cookie-secret"},
    }
    scrubbed = scrub_browser_audit_event(event)
    blob = _serialized(scrubbed)
    for secret in (
        "bearer-secret",
        "typed-secret",
        "private-page-text",
        "/private",
        "base64-secret",
        "cookie-secret",
    ):
        assert secret not in blob
    assert scrubbed["origin"] == "https://example.com"
    assert scrubbed["resource"] == "https://example.com"
    assert scrubbed["result"] == BROWSER_OBSERVATION_OMITTED
    assert scrubbed["reason"]["code"] == "ACTION_TIMEOUT"


def test_durable_event_and_errors_never_preserve_raw_failure_text():
    event = durable_browser_event(
        tool="browser_click",
        status="FAILED",
        origin="https://example.com/private?q=token",
        title="  Account settings\n ",
        error={"code": "UNKNOWN", "message": "Bearer secret at /private"},
        timestamp="2026-07-30T00:00:00+00:00",
    )
    assert event == {
        "tool": "browser_click",
        "origin": "https://example.com",
        "title": "Account settings",
        "status": "failed",
        "timestamp": "2026-07-30T00:00:00+00:00",
        "error": {"code": "BROWSER_ERROR", "message": "Browser action failed"},
    }
    assert redact_browser_error("STALE_SNAPSHOT page said secret") == {
        "code": "STALE_SNAPSHOT",
        "message": "Browser snapshot is stale",
    }
