"""Durable audit log: storage round-trip, redaction, resource extraction, and
WAL journal mode so per-commit fsync doesn't stall the event loop."""

from coworker.audit import AuditStore, _resource, _sanitize_args


def _pragma(conn, name):
    row = conn.execute(f"PRAGMA {name}").fetchone()
    return row[0] if row else None


def test_audit_store_uses_wal_journal(tmp_path):
    """WAL + synchronous=NORMAL: commit returns without an fsync per tool audit,
    which otherwise blocks the asyncio event loop at every _audit() call site."""
    store = AuditStore(tmp_path / "audit.db")
    try:
        assert _pragma(store._conn, "journal_mode").lower() == "wal"
        assert _pragma(store._conn, "synchronous") == 1  # NORMAL
    finally:
        store.close()


def test_append_and_list_roundtrip(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    try:
        store.append(
            {
                "session_id": "s1",
                "agent": "code",
                "workspace": "/tmp/ws",
                "tool": "shell",
                "stage": "finished",
                "status": "ok",
                "arguments": {"command": "ls"},
                "result_preview": "file.txt",
            }
        )
        rows = store.list()
        assert len(rows) == 1
        row = rows[0]
        assert row["session_id"] == "s1"
        assert row["tool"] == "shell"
        assert row["stage"] == "finished"
        assert row["status"] == "ok"
        assert row["args"] == {"command": "ls"}
    finally:
        store.close()


def test_list_filters(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    try:
        store.append({"session_id": "s1", "tool": "shell", "arguments": {}})
        store.append({"session_id": "s2", "tool": "read_file", "arguments": {}})
        assert len(store.list(session_id="s1")) == 1
        assert len(store.list(tool="read_file")) == 1
        assert len(store.list(session_id="s1", tool="read_file")) == 0
    finally:
        store.close()


def test_sanitize_args_redacts_secrets():
    args = {
        "token": "t",
        "api_key": "k",
        "password": "p",
        "access_token": "a",
        "bot_token": "b",
        "app_token": "ap",
        "secret": "s",
        "raw": "r",
        "body": "hi",
        "content": "x",
        "html": "<p>",
        "safe": "visible",
        "nested_token": "also-redacted",
    }
    out = _sanitize_args("http_get", args)
    for key in (
        "token",
        "api_key",
        "password",
        "access_token",
        "bot_token",
        "app_token",
        "secret",
        "raw",
        "body",
        "content",
        "html",
        "nested_token",
    ):
        assert out[key] == "[redacted]" or out[key] == "[redacted body]", key
    assert out["safe"] == "visible"


def test_sanitize_args_redacts_browser_type_text():
    out = _sanitize_args("browser_type", {"text": "my password is hunter2"})
    assert out["text"] == "[redacted input]"


def test_resource_extracts_well_known_keys():
    assert (
        _resource("http_get", {"url": "https://example.com"}, {})
        == "https://example.com"
    )
    assert _resource("github_issue", {"owner": "o", "repo": "r"}, {}) == "o"
    assert (
        _resource("zendesk_search", {"subdomain": "acme", "query": "foo"}, {})
        == "acme.zendesk.com"
    )
    assert _resource("read_file", {"path": "/etc/hosts"}, {}) == ""
    assert (
        _resource("http_get", {}, {"url": "https://from-result"})
        == "https://from-result"
    )


def test_close_is_idempotent(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    store.close()
    store.close()  # must not raise


def test_summarize_truncates_long_strings(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    try:
        long = "x" * 5000
        store.append({"tool": "echo", "arguments": {"message": long}})
        row = store.list(tool="echo")[0]
        assert len(row["args"]["message"]) <= 500
        assert row["args"]["message"].endswith("...")
    finally:
        store.close()
