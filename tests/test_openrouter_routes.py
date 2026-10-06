"""Provider account login through the real sidecar routes and credential resolver."""

from fastapi.testclient import TestClient

from coworker.providers import openrouter_auth
from coworker.server import SessionManager, create_app


def test_account_login_switching_and_disconnect(tmp_path, monkeypatch):
    async def exchange(code, verifier):
        assert code == "valid-code"
        return "sk-or-account-secret"

    monkeypatch.setattr(openrouter_auth, "exchange_key", exchange)
    manager = SessionManager(workspace=tmp_path)
    manager.secrets.put("provider:openrouter", {"api_key": "sk-or-manual"})
    client = TestClient(create_app(manager))
    with client:
        start = client.post(
            "/v1/providers/openrouter/signin", json={"manual": True}
        ).json()
        done = client.post(
            "/v1/providers/openrouter/complete",
            json={
                "code": "valid-code",
                "attempt_id": start["attempt_id"],
            },
        ).json()
        assert done["active"] and done["connected"]
        rows = client.get("/v1/providers").json()
        row = next(row for row in rows if row["name"] == "openrouter")
        assert row["configured"] and row["api_key_configured"]
        assert "sk-or-" not in str(rows)
        assert manager._provider_configured("openrouter")

        # Explicitly saving the existing manual key switches billing methods.
        saved = client.post(
            "/v1/providers", json={"name": "openrouter", "fields": {"api_key": ""}}
        ).json()
        assert saved["ok"]
        status = client.get("/v1/providers/openrouter/status").json()
        assert status["connected"] and not status["active"]

        start = client.post(
            "/v1/providers/openrouter/signin", json={"manual": True}
        ).json()
        client.post(
            "/v1/providers/openrouter/complete",
            json={"code": "valid-code", "attempt_id": start["attempt_id"]},
        )
        client.delete("/v1/providers/openrouter")
        assert client.get("/v1/providers/openrouter/status").json()["active"]
        assert not (manager.secrets.get("provider:openrouter") or {}).get("api_key")
        disconnected = client.post("/v1/providers/openrouter/disconnect").json()
        assert not disconnected["connected"]
        assert not manager._provider_configured("openrouter")


def test_routes_reject_invalid_input_and_cancel_pending_login(tmp_path):
    manager = SessionManager(workspace=tmp_path)
    with TestClient(create_app(manager)) as client:
        assert (
            client.post("/v1/providers/openrouter/signin", json=[]).status_code == 422
        )
        started = client.post(
            "/v1/providers/openrouter/signin", json={"manual": True}
        ).json()
        assert started["authorizing"]
        assert not client.post("/v1/providers/openrouter/cancel").json()["authorizing"]
        result = client.post(
            "/v1/providers/openrouter/complete",
            json={"code": "old", "attempt_id": started["attempt_id"]},
        ).json()
        assert result["error"] and not result["connected"]
        assert (
            client.post(
                "/v1/providers/openrouter/signin",
                json={},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
