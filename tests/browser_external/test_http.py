from __future__ import annotations

from fastapi.testclient import TestClient

from coworker.browser_external import ExternalBrowserBridge, PROTOCOL_VERSION
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app


class _Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path, bridge: ExternalBrowserBridge) -> SessionManager:
    return SessionManager(
        workspace=tmp_path,
        data_dir=tmp_path / "state",
        provider=_Provider(),
        external_browser_bridge=bridge,
    )


def _native_connect(
    client: TestClient,
    *,
    app_token: str,
) -> dict:
    response = client.post(
        "/v1/browser-extension/native/connect",
        headers={"X-OpenWorker-Token": app_token},
        json={
            "protocol_version": PROTOCOL_VERSION,
            "transport": "native_messaging",
            "extension_id": "djnbhkmnbmjobnphflaopcpfkifbgekl",
            "client": {
                "browser": "chrome",
                "browser_version": "149.0.0.0",
                "extension_version": "0.1.0",
                "platform": "macOS",
                "client_id": "test-extension",
            },
        },
    )
    assert response.status_code == 200
    return response.json()


def test_native_connect_requires_sidecar_token_and_creates_chrome_session(
    tmp_path, monkeypatch
) -> None:
    app_token = "a" * 64
    monkeypatch.setenv("COWORKER_API_TOKEN", app_token)
    bridge = ExternalBrowserBridge()
    with TestClient(create_app(_manager(tmp_path, bridge))) as client:
        assert client.post(
            "/v1/browser-extension/native/connect",
            json={
                "protocol_version": PROTOCOL_VERSION,
                "transport": "native_messaging",
                "extension_id": "djnbhkmnbmjobnphflaopcpfkifbgekl",
                "client": {"browser": "chrome"},
            },
        ).status_code == 401
        assert client.get("/v1/browser-extension/status").status_code == 401
        assert client.post(
            "/v1/browser-extension/select",
            json={"session_id": "task-1", "surface": "chrome"},
        ).status_code == 401

        paired = _native_connect(client, app_token=app_token)
        assert paired["browser"] == "chrome"
        assert paired["protocol_version"] == PROTOCOL_VERSION
        assert paired["session_id"]
        assert paired["session_token"]

        status = client.get(
            "/v1/browser-extension/status",
            headers={"X-OpenWorker-Token": app_token},
        )
        assert status.status_code == 200
        chrome = next(
            surface
            for surface in status.json()["surfaces"]
            if surface["surface"] == "chrome"
        )
        assert chrome["connected"] is True
        assert chrome["claimed_tabs"] == 0
        selected = client.post(
            "/v1/browser-extension/select",
            headers={"X-OpenWorker-Token": app_token},
            json={"session_id": "task-1", "surface": "chrome"},
        )
        assert selected.status_code == 200
        assert selected.json() == {
            "ok": True,
            "surface": "chrome",
            "available": True,
            "claimed_tab_ids": [],
        }
        selected_status = client.get(
            "/v1/browser-extension/status?session_id=task-1",
            headers={"X-OpenWorker-Token": app_token},
        )
        assert selected_status.json()["selected_surface"] == "chrome"


def test_native_connect_rejects_wrong_protocol_transport_and_extension(
    tmp_path, monkeypatch
) -> None:
    app_token = "b" * 64
    monkeypatch.setenv("COWORKER_API_TOKEN", app_token)
    bridge = ExternalBrowserBridge()
    with TestClient(create_app(_manager(tmp_path, bridge))) as client:
        headers = {"X-OpenWorker-Token": app_token}
        base = {
            "protocol_version": PROTOCOL_VERSION,
            "transport": "native_messaging",
            "extension_id": "djnbhkmnbmjobnphflaopcpfkifbgekl",
            "client": {"browser": "chrome"},
        }
        wrong_version = client.post(
            "/v1/browser-extension/native/connect",
            headers=headers,
            json={**base, "protocol_version": PROTOCOL_VERSION + 1},
        )
        assert wrong_version.status_code == 400
        assert wrong_version.json()["code"] == "PROTOCOL_VERSION_MISMATCH"

        wrong_transport = client.post(
            "/v1/browser-extension/native/connect",
            headers=headers,
            json={**base, "transport": "http"},
        )
        assert wrong_transport.status_code == 400
        assert wrong_transport.json()["code"] == "INVALID_NATIVE_TRANSPORT"

        wrong_extension = client.post(
            "/v1/browser-extension/native/connect",
            headers=headers,
            json={**base, "extension_id": "a" * 32},
        )
        assert wrong_extension.status_code == 403
        assert wrong_extension.json()["code"] == "INVALID_EXTENSION_ID"


def test_extension_bearer_round_trip_and_failed_command_acknowledgement(
    tmp_path, monkeypatch
) -> None:
    app_token = "c" * 64
    monkeypatch.setenv("COWORKER_API_TOKEN", app_token)
    bridge = ExternalBrowserBridge()
    manager = _manager(tmp_path, bridge)
    with TestClient(create_app(manager)) as client:
        paired = _native_connect(client, app_token=app_token)
        bearer = {"Authorization": f"Bearer {paired['session_token']}"}

        missing = client.post(
            "/v1/browser-extension/poll",
            json={"wait_seconds": 0},
        )
        assert missing.status_code == 401
        assert missing.json()["code"] == "UNAUTHENTICATED"

        claimed = client.post(
            "/v1/browser-extension/events",
            headers=bearer,
            json={
                "event": {
                    "type": "tab_claimed",
                    "tab_id": 17,
                    "url": "https://example.test/",
                    "title": "Fixture",
                }
            },
        )
        assert claimed.status_code == 200
        assert claimed.json()["accepted"] is True

        ticket = bridge.enqueue_command(
            paired["session_id"], "snapshot", {"tab_id": 17}
        )
        poll = client.post(
            "/v1/browser-extension/poll",
            headers=bearer,
            json={"wait_seconds": 0, "limit": 1},
        )
        assert poll.status_code == 200
        assert poll.json()["commands"][0]["request_id"] == ticket.request_id

        # The extension successfully reports that a browser action failed.  The
        # HTTP request itself is still acknowledged with 200 so retries remain
        # idempotent and do not strand the command lease.
        recorded = client.post(
            "/v1/browser-extension/results",
            headers=bearer,
            json={
                "request_id": ticket.request_id,
                "ok": False,
                "error": {
                    "code": "ELEMENT_GONE",
                    "message": "The target was detached",
                },
            },
        )
        assert recorded.status_code == 200
        assert recorded.json()["ok"] is False
        assert recorded.json()["error"]["code"] == "ELEMENT_GONE"

        disconnected = client.post(
            "/v1/browser-extension/disconnect",
            headers=bearer,
            json={"reason": "user_disconnected"},
        )
        assert disconnected.status_code == 200
        stale = client.post(
            "/v1/browser-extension/poll",
            headers=bearer,
            json={"wait_seconds": 0},
        )
        assert stale.status_code == 401
        assert stale.json()["code"] == "UNAUTHENTICATED"


def test_extension_transport_exceptions_are_exact_and_cors_stays_pinned(
    tmp_path, monkeypatch
) -> None:
    app_token = "d" * 64
    monkeypatch.setenv("COWORKER_API_TOKEN", app_token)
    with TestClient(
        create_app(_manager(tmp_path, ExternalBrowserBridge()))
    ) as client:
        # An extension transport endpoint reaches its narrower bearer boundary
        # without the desktop app token.
        transport = client.post(
            "/v1/browser-extension/poll",
            headers={"Origin": "https://evil.example"},
            json={"wait_seconds": 0},
        )
        assert transport.status_code == 401
        assert transport.json()["code"] == "UNAUTHENTICATED"

        # A similarly named route is not accidentally tokenless.
        assert client.post(
            "/v1/browser-extension/poll/extra", json={}
        ).status_code == 401

        # Native Messaging means the MV3 service worker has no loopback host
        # permission. Arbitrary websites and extension origins still receive no
        # CORS access to the wider sidecar API.
        assert "access-control-allow-origin" not in {
            name.lower() for name in transport.headers
        }
        extension_origin = client.options(
            "/v1/browser-extension/poll",
            headers={
                "Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert extension_origin.headers.get("access-control-allow-origin") is None
