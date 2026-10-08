"""Regression coverage for audit-log redaction."""

from __future__ import annotations

from coworker.audit import AuditStore


def test_audit_store_redacts_nested_credentials_and_bodies(tmp_path):
    authorization = "Bearer top-secret"
    cookie = "session=very-secret"
    api_key = "api-key-secret"
    credential = "credential-secret"
    private_key = "-----BEGIN PRIVATE KEY-----"
    body = "confidential request body"
    store = AuditStore(tmp_path / "coworker.db")
    try:
        store.append(
            {
                "tool": "http_request",
                "arguments": {
                    "url": "https://example.test/api",
                    "headers": {"Authorization": authorization, "Cookie": cookie},
                    "config": {"api_key": api_key, "credential": credential},
                    "requests": [{"private_key": private_key, "request_body": body}],
                },
            }
        )

        event = store.list()[0]
        assert event["args"] == {
            "url": "https://example.test/api",
            "headers": {"Authorization": "[redacted]", "Cookie": "[redacted]"},
            "config": {"api_key": "[redacted]", "credential": "[redacted]"},
            "requests": [
                {"private_key": "[redacted]", "request_body": "[redacted body]"}
            ],
        }

        persisted_args = store._conn.execute("SELECT args FROM audit_events").fetchone()[0]
        for secret in (authorization, cookie, api_key, credential, private_key, body):
            assert secret not in persisted_args
    finally:
        store.close()
