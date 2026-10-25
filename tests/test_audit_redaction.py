"""Audit-log redaction — nested arguments (#397) and tool-result previews (#525).

The audit row is the record of who ran what against which resource. It is not a place
for the credential the call carried, nor for the content the call returned.
"""

from __future__ import annotations

import json

from coworker.audit import AuditStore


def _row(tmp_path, event: dict) -> dict:
    store = AuditStore(tmp_path / "audit.db")
    try:
        store.append({"session_id": "s1", "tool": "http_request", **event})
        return store.list(limit=1)[0]
    finally:
        store.close()


def test_nested_credentials_are_redacted(tmp_path):
    row = _row(
        tmp_path,
        {
            "arguments": {
                "url": "https://example.com",
                "headers": {"Authorization": "Bearer sk-live-NESTED"},
                "config": {"api_key": "sk-live-NESTED2"},
            }
        },
    )
    assert row["args"]["headers"]["Authorization"] == "[redacted]"
    assert row["args"]["config"]["api_key"] == "[redacted]"
    assert row["args"]["url"] == "https://example.com"  # the resource still reads
    assert "sk-live" not in json.dumps(row["args"])


def test_credential_keys_beyond_the_token_family(tmp_path):
    row = _row(
        tmp_path,
        {
            "arguments": {
                "authorization": "Bearer sk-live-AUTHZ",
                "cookie": "session=sk-live-COOKIE",
                "credential": "sk-live-CRED",
                "private_key": "-----BEGIN...",
            }
        },
    )
    assert set(row["args"].values()) == {"[redacted]"}


def test_nested_bodies_are_redacted(tmp_path):
    row = _row(
        tmp_path,
        {"arguments": {"draft": {"to": "a@example.com", "body": "private text"}}},
    )
    assert row["args"]["draft"]["body"] == "[redacted body]"
    assert row["args"]["draft"]["to"] == "a@example.com"


def test_a_credential_below_the_walk_limit_is_dropped_not_copied(tmp_path):
    deep: dict = {"api_key": "sk-live-DEEP"}
    for _ in range(10):
        deep = {"wrap": deep}
    row = _row(tmp_path, {"arguments": deep})
    assert "sk-live" not in json.dumps(row["args"])


def test_browser_typing_is_still_redacted_input(tmp_path):
    row = _row(
        tmp_path, {"tool": "browser_type", "arguments": {"text": "hunter2"}}
    )
    assert row["args"]["text"] == "[redacted input]"


def test_result_preview_drops_an_email_body_and_keeps_the_envelope(tmp_path):
    row = _row(
        tmp_path,
        {
            "tool": "email_read",
            "arguments": {"uid": "42"},
            "stage": "finished",
            "result": {
                "ok": True,
                "subject": "Q3 numbers",
                "body": "the confidential text of the message",
            },
            "result_preview": "unsanitized preview from the caller",
        },
    )
    assert "confidential" not in row["result_preview"]
    assert "[redacted body]" in row["result_preview"]
    assert "Q3 numbers" in row["result_preview"]  # triage still works


def test_result_preview_drops_shell_output_and_keeps_the_command(tmp_path):
    row = _row(
        tmp_path,
        {
            "tool": "run_shell",
            "arguments": {"command": "printenv"},
            "stage": "finished",
            "result": {
                "command": "printenv",
                "exit_code": 0,
                "output": "AWS_SECRET_ACCESS_KEY=sk-live-ENV",
            },
        },
    )
    assert "sk-live-ENV" not in row["result_preview"]
    assert "printenv" in row["result_preview"]


def test_preview_without_a_raw_result_is_still_stored(tmp_path):
    row = _row(
        tmp_path,
        {"stage": "finished", "result_preview": "{\"ok\": true}"},
    )
    assert row["result_preview"] == '{"ok": true}'
