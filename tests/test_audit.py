"""Audit-log regressions for projected tool results."""

from coworker.audit import AuditStore


def test_explicit_resource_preserves_result_url_without_raw_result(tmp_path):
    store = AuditStore(tmp_path / "audit.db")
    try:
        store.append(
            {
                "session_id": "s",
                "tool": "example",
                "stage": "finished",
                "resource": "https://example.test/item/1",
                "result_preview": "projected",
            }
        )
        event = store.list()[0]
        assert event["resource"] == "https://example.test/item/1"
        assert event["result_preview"] == "projected"
    finally:
        store.close()
