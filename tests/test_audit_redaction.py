"""The audit log must not persist tool-result content in plaintext.

Arguments were redacted (secret keys, request bodies) but result_preview stored the raw tool
result with only truncation — leaking email bodies and shell output (cat .env / printenv) into
the durable local audit DB. These tests pin the result-side redaction.
"""

from __future__ import annotations

from coworker.audit import AuditStore


def _row(store: AuditStore, session_id: str) -> dict:
    rows = [r for r in store.list(session_id=session_id) if r["stage"] == "finished"]
    assert rows, "no finished audit row was written"
    return rows[0]


def test_email_body_is_redacted_in_the_preview(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    result = {
        "ok": True,
        "from": "attacker@example.com",
        "subject": "hello",
        "body": "SECRET-EMAIL-CONTENTS reset your password at https://evil",
    }
    store.append(
        {
            "session_id": "s1",
            "tool": "email_read",
            "stage": "finished",
            "status": "ok",
            "result": result,
            "result_preview": str(result),  # what the engine would have flattened
        }
    )
    preview = _row(store, "s1")["result_preview"]
    assert "SECRET-EMAIL-CONTENTS" not in preview
    assert "[redacted body]" in preview
    store.close()


def test_shell_output_is_redacted_in_the_preview(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    result = {
        "command": "printenv",
        "exit_code": 0,
        "output": "AWS_SECRET_ACCESS_KEY=AKIA-TOTALLY-SECRET\nHOME=/root",
    }
    store.append(
        {
            "session_id": "s2",
            "tool": "run_shell",
            "stage": "finished",
            "status": "ok",
            "result": result,
            "result_preview": str(result),
        }
    )
    preview = _row(store, "s2")["result_preview"]
    assert "AKIA-TOTALLY-SECRET" not in preview
    assert "[redacted body]" in preview
    store.close()


def test_secret_valued_result_key_is_redacted(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    store.append(
        {
            "session_id": "s3",
            "tool": "some_connector_auth",
            "stage": "finished",
            "status": "ok",
            "result": {"ok": True, "access_token": "xoxb-super-secret"},
            "result_preview": "{'access_token': 'xoxb-super-secret'}",
        }
    )
    preview = _row(store, "s3")["result_preview"]
    assert "xoxb-super-secret" not in preview
    assert "[redacted]" in preview
    store.close()


def test_non_sensitive_result_still_visible_for_triage(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    store.append(
        {
            "session_id": "s4",
            "tool": "list_issues",
            "stage": "finished",
            "status": "ok",
            "result": {"ok": True, "count": 3, "url": "https://example.com/x"},
            "result_preview": "{'ok': True, 'count': 3}",
        }
    )
    preview = _row(store, "s4")["result_preview"]
    assert "count" in preview and "3" in preview  # metadata survives
    store.close()
