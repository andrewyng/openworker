from __future__ import annotations

import threading
from typing import Any

from coworker.browser_external import ExternalBrowserBridge
from coworker.server.manager import SessionManager


def _respond_once(
    bridge: ExternalBrowserBridge,
    token: str,
    expected_command: str,
    result: dict[str, Any],
    *,
    expected_params: dict[str, Any] | None = None,
) -> threading.Thread:
    def worker() -> None:
        commands = bridge.poll_commands(token, wait_seconds=2, limit=1)
        assert len(commands) == 1
        command = commands[0]
        assert command["command"] == expected_command
        if expected_params is not None:
            assert command["params"] == expected_params
        bridge.submit_result(
            token,
            command["request_id"],
            ok=True,
            result=result,
        )

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def test_manager_routes_shared_chrome_tabs_through_model_tools(tmp_path) -> None:
    bridge = ExternalBrowserBridge()
    manager = SessionManager(
        workspace=tmp_path,
        data_dir=tmp_path / "state",
        external_browser_bridge=bridge,
    )

    paired = manager.browser_extension_native_connect(
        client={
            "browser": "chrome",
            "browser_version": "149",
            "extension_version": "0.1.0",
            "platform": "macOS",
            "client_id": "test-client",
        },
    )
    token = paired["session_token"]
    manager.browser_extension_event(
        token,
        {
            "type": "tab_claimed",
            "tab_id": 42,
            "title": "Signed-in app",
            "url": "https://example.com/account",
        },
    )

    selected = manager.select_browser_surface("task-1", "chrome")
    assert selected["ok"] and selected["claimed_tab_ids"] == [42]

    tabs_worker = _respond_once(
        bridge,
        token,
        "tabs",
        {
            "tabs": [
                {
                    "tab_id": 42,
                    "title": "Signed-in app",
                    "url": "https://example.com/account",
                    "active": True,
                    "attached": True,
                }
            ]
        },
    )
    tabs = manager._invoke_external_browser_tool(
        "task-1", "chrome", "browser_tabs", {}
    )
    tabs_worker.join(timeout=3)
    assert tabs["ok"] and tabs["tabs"][0]["tab_id"] == "42"

    snapshot_worker = _respond_once(
        bridge,
        token,
        "snapshot",
        {
            "tab_id": 42,
            "snapshot_id": "snapshot-1",
            "title": "Signed-in app",
            "url": "https://example.com/account",
            "document_id": "document-1",
            "url_token": "url-1",
            "snapshot": '[ref=e1] button "Publish"',
            "nodes": [{"ref": "e1", "role": "button", "name": "Publish"}],
            "truncated": False,
        },
    )
    snapshot = manager._invoke_external_browser_tool(
        "task-1",
        "chrome",
        "browser_snapshot",
        {"tab_id": "42"},
    )
    snapshot_worker.join(timeout=3)
    assert snapshot["ok"] and snapshot["tab_id"] == "42"
    inspect_worker = _respond_once(
        bridge,
        token,
        "inspect",
        {
            "tab_id": 42,
            "snapshot_id": "snapshot-1",
            "ref": "e1",
            "document_id": "document-1",
            "url": "https://example.com/account",
            "url_token": "url-1",
            "target": {
                "role": "button",
                "accessible_name": "Publish",
                "element_type": "button",
                "inside_form": False,
                "submits_form": False,
                "page_risk_hints": [],
                "data_classification": [],
            },
            "requires_confirmation": True,
            "reasons": ["consequential_control"],
            "confirmation_token": "confirmed-target-1",
        },
    )
    assert manager._external_browser_confirmation(
        "task-1",
        "browser_click",
        {
            "tab_id": "42",
            "snapshot_id": "snapshot-1",
            "ref": "e1",
        },
    )["requires_confirmation"]
    inspect_worker.join(timeout=3)

    click_worker = _respond_once(
        bridge,
        token,
        "click",
        {"clicked": True, "point": {"x": 10, "y": 12}},
        expected_params={
            "tab_id": 42,
            "snapshot_id": "snapshot-1",
            "ref": "e1",
            "confirmation_token": "confirmed-target-1",
        },
    )
    clicked = manager._invoke_external_browser_tool(
        "task-1",
        "chrome",
        "browser_click",
        {
            "tab_id": "42",
            "snapshot_id": "snapshot-1",
            "ref": "e1",
        },
        confirmation={"binding": "confirmed-target-1"},
    )
    click_worker.join(timeout=3)
    assert clicked == {
        "ok": True,
        "clicked": True,
        "point": {"x": 10, "y": 12},
        "surface": "chrome",
    }

    assert "snapshot-1" in manager._external_browser_snapshots["task-1"]
    manager.browser_extension_event(
        token,
        {
            "type": "tab_navigated",
            "tab_id": 42,
            "url": "https://example.com/next",
            "title": "Next",
            "status": "complete",
        },
    )
    assert "snapshot-1" not in manager._external_browser_snapshots["task-1"]

    closed = manager._invoke_external_browser_tool(
        "task-1", "chrome", "browser_close", {}
    )
    assert closed["connection_preserved"] is True
    assert manager.browser_surface("task-1") == "iab"


def test_manager_timeout_expires_ticket_before_late_extension_poll(tmp_path) -> None:
    bridge = ExternalBrowserBridge()
    manager = SessionManager(
        workspace=tmp_path,
        data_dir=tmp_path / "state-timeout",
        external_browser_bridge=bridge,
    )
    connected = manager.browser_extension_native_connect(
        client={"browser": "chrome", "client_id": "timeout-test"}
    )

    result = manager.external_browser_command(
        "chrome", "tabs", {}, timeout_seconds=0
    )

    assert result["error"] == "BROWSER_EXTENSION_TIMEOUT"
    assert bridge.poll_commands(
        connected["session_token"], wait_seconds=0
    ) == []
