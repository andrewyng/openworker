"""GitHub OAuth Device Flow: local protocol, persistence, and sidecar API."""

from __future__ import annotations

import json
import threading

import httpx
from fastapi.testclient import TestClient

from coworker.connectors.github import auth as github_auth
from coworker.connectors.github.auth import GitHubDeviceAuth
from coworker.secrets import SecretStore
from coworker.server import SessionManager, create_app


def _response(data: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=data)


def test_start_requires_configured_public_client_id(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    auth = GitHubDeviceAuth(SecretStore())
    called = False

    def unexpected_post(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(github_auth.httpx, "post", unexpected_post)
    result = auth.start("")

    assert result == {
        "ok": False,
        "error": (
            "GitHub device sign-in is not configured. "
            "Set github_oauth_client_id in config.toml."
        ),
    }
    assert called is False


def test_start_returns_only_public_device_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return _response(
            {
                "device_code": "server-only-secret",
                "user_code": "WDJB-MJHT",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }
        )

    monkeypatch.setattr(github_auth.httpx, "post", post)
    result = GitHubDeviceAuth(SecretStore()).start("public-client", "repo read:user")

    assert result["ok"] is True
    assert result["user_code"] == "WDJB-MJHT"
    assert result["verification_uri"] == "https://github.com/login/device"
    assert result["flow_id"]
    assert "device_code" not in result
    assert "server-only-secret" not in json.dumps(result)
    assert calls[0][1]["data"] == {
        "client_id": "public-client",
        "scope": "repo read:user",
    }


def test_poll_enforces_interval_handles_slow_down_and_stores_token(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    clock = [100.0]
    monkeypatch.setattr(github_auth.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(github_auth.time, "time", lambda: 1_000.0 + clock[0])
    token_polls = []

    def post(url, **kwargs):
        if url == github_auth.DEVICE_CODE_URL:
            return _response(
                {
                    "device_code": "private-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://github.com/login/device",
                    "expires_in": 900,
                    "interval": 5,
                }
            )
        token_polls.append(kwargs["data"])
        if len(token_polls) == 1:
            return _response({"error": "authorization_pending"})
        if len(token_polls) == 2:
            return _response({"error": "slow_down"})
        return _response(
            {
                "access_token": "gho_local-token",
                "token_type": "bearer",
                "scope": "repo",
            }
        )

    def get(url, **_kwargs):
        assert url == "https://api.github.com/user"
        return _response({"id": 42, "login": "octocat"})

    monkeypatch.setattr(github_auth.httpx, "post", post)
    monkeypatch.setattr(github_auth.httpx, "get", get)
    store = SecretStore()
    store.put(
        "github:default",
        {"type": "oauth", "managed": True, "mode": "relay", "enabled": True},
    )
    auth = GitHubDeviceAuth(store)
    flow_id = auth.start("public-client")["flow_id"]

    # The first GUI poll is answered locally until GitHub's interval elapses.
    assert auth.poll(flow_id) == {
        "ok": True,
        "state": "pending",
        "retry_after": 5,
    }
    assert len(token_polls) == 0
    clock[0] = 105.0
    assert auth.poll(flow_id) == {
        "ok": True,
        "state": "pending",
        "retry_after": 5,
    }
    # A fast duplicate GUI poll is answered locally.
    assert auth.poll(flow_id)["state"] == "pending"
    assert len(token_polls) == 1

    clock[0] = 110.0
    slowed = auth.poll(flow_id)
    assert slowed == {"ok": True, "state": "pending", "retry_after": 10}
    clock[0] = 119.0
    assert auth.poll(flow_id)["state"] == "pending"
    assert len(token_polls) == 2

    clock[0] = 120.0
    complete = auth.poll(flow_id)
    assert complete == {"ok": True, "state": "complete", "account": "octocat"}
    assert "gho_local-token" not in json.dumps(complete)
    profile = store.get("github:default")
    assert profile["token"] == "gho_local-token"
    assert profile["auth_method"] == "device"
    assert profile["account"] == "octocat"
    assert profile["account_id"] == "42"
    # A user credential can coexist with the App installation relay.
    assert profile["mode"] == "relay"
    assert profile["managed"] is True
    assert auth.poll(flow_id)["state"] == "error"


def test_denial_removes_pending_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    clock = [0.0]
    monkeypatch.setattr(github_auth.time, "monotonic", lambda: clock[0])

    def post(url, **_kwargs):
        if url == github_auth.DEVICE_CODE_URL:
            return _response(
                {
                    "device_code": "device",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://github.com/login/device",
                    "expires_in": 900,
                    "interval": 5,
                }
            )
        return _response({"error": "access_denied"})

    monkeypatch.setattr(github_auth.httpx, "post", post)
    auth = GitHubDeviceAuth(SecretStore())
    flow_id = auth.start("client")["flow_id"]

    clock[0] = 5.0
    denied = auth.poll(flow_id)
    assert denied["state"] == "denied"
    assert auth.poll(flow_id)["error"] == "Device sign-in flow not found."
    assert SecretStore().get("github:default") is None


def test_local_expiry_has_distinct_state(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    clock = [10.0]
    monkeypatch.setattr(github_auth.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        github_auth.httpx,
        "post",
        lambda *_args, **_kwargs: _response(
            {
                "device_code": "device",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 1,
                "interval": 5,
            }
        ),
    )
    auth = GitHubDeviceAuth(SecretStore())
    flow_id = auth.start("client")["flow_id"]

    clock[0] = 11.0
    expired = auth.poll(flow_id)
    assert expired == {
        "ok": False,
        "state": "expired",
        "error": "The GitHub device code expired. Start again.",
    }


def test_cancel_wins_against_inflight_success(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    clock = [0.0]
    monkeypatch.setattr(github_auth.time, "monotonic", lambda: clock[0])
    entered = threading.Event()
    release = threading.Event()

    def post(url, **_kwargs):
        if url == github_auth.DEVICE_CODE_URL:
            return _response(
                {
                    "device_code": "device",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://github.com/login/device",
                    "expires_in": 900,
                    "interval": 1,
                }
            )
        entered.set()
        assert release.wait(timeout=2)
        return _response({"access_token": "gho_must-not-persist"})

    monkeypatch.setattr(github_auth.httpx, "post", post)
    monkeypatch.setattr(
        github_auth.httpx,
        "get",
        lambda *_args, **_kwargs: _response({"id": 42, "login": "octocat"}),
    )
    store = SecretStore()
    auth = GitHubDeviceAuth(store)
    flow_id = auth.start("client")["flow_id"]
    clock[0] = 1.0
    result = {}

    thread = threading.Thread(target=lambda: result.update(auth.poll(flow_id)))
    thread.start()
    assert entered.wait(timeout=2)
    assert auth.cancel(flow_id) == {"ok": True, "cancelled": True}
    release.set()
    thread.join(timeout=2)

    assert result["state"] == "error"
    assert result["error"] == "Device sign-in was cancelled."
    assert store.get("github:default") is None


def test_sidecar_routes_use_config_and_never_expose_device_code(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("COWORKER_STATE_DIR", str(state))
    (state / "config.toml").write_text(
        'github_oauth_client_id = "configured-client"\n'
        'github_oauth_scopes = "repo"\n',
        encoding="utf-8",
    )

    def post(url, **kwargs):
        assert url == github_auth.DEVICE_CODE_URL
        assert kwargs["data"]["client_id"] == "configured-client"
        return _response(
            {
                "device_code": "route-private-code",
                "user_code": "ROUT-CODE",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }
        )

    monkeypatch.setattr(github_auth.httpx, "post", post)
    manager = SessionManager(workspace=tmp_path)
    with TestClient(create_app(manager)) as client:
        started = client.post("/v1/connectors/github/device/start")
        assert started.status_code == 200
        payload = started.json()
        assert payload["ok"] is True
        assert payload["user_code"] == "ROUT-CODE"
        assert "device_code" not in payload
        assert "route-private-code" not in started.text

        cancelled = client.delete(
            f"/v1/connectors/github/device/{payload['flow_id']}"
        )
        assert cancelled.json() == {"ok": True, "cancelled": True}
