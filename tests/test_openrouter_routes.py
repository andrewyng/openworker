"""Provider account login through the real sidecar routes and credential resolver."""

from fastapi.testclient import TestClient

from coworker.providers import openrouter_auth
from coworker.server import SessionManager, create_app


def test_account_login_is_its_own_provider_and_disconnects(tmp_path, monkeypatch):
    async def exchange(code, verifier):
        assert code == "valid-code"
        return "sk-or-account-secret"

    monkeypatch.setattr(openrouter_auth, "exchange_key", exchange)
    manager = SessionManager(workspace=tmp_path)
    manager.secrets.put("provider:openrouter", {"api_key": "sk-or-manual"})
    client = TestClient(create_app(manager))
    with client:
        start = client.post(
            "/v1/providers/openrouter-account/signin", json={"manual": True}
        ).json()
        done = client.post(
            "/v1/providers/openrouter-account/complete",
            json={"code": "valid-code", "attempt_id": start["attempt_id"]},
        ).json()
        assert done["connected"] and not done["authorizing"]
        rows = client.get("/v1/providers").json()
        by_name = {row["name"]: row for row in rows}
        # Two cards: the key under API keys, the account under Subscriptions.
        assert by_name["openrouter"]["configured"] and by_name["openrouter"]["kind"] == "api_key"
        account = by_name["openrouter-account"]
        assert account["configured"] and account["signed_in"] and account["kind"] == "subscription"
        assert "sk-or-" not in str(rows)
        assert manager._provider_configured("openrouter-account")
        assert manager.secrets.get("provider:openrouter") == {"api_key": "sk-or-manual"}

        # Signing out leaves the key-based provider alone.
        disconnected = client.post("/v1/providers/openrouter-account/disconnect").json()
        assert not disconnected["connected"]
        assert not manager._provider_configured("openrouter-account")
        assert manager._provider_configured("openrouter")


def test_routes_reject_invalid_input_and_cancel_pending_login(tmp_path):
    manager = SessionManager(workspace=tmp_path)
    with TestClient(create_app(manager)) as client:
        assert (
            client.post("/v1/providers/openrouter-account/signin", json=[]).status_code == 422
        )
        started = client.post(
            "/v1/providers/openrouter-account/signin", json={"manual": True}
        ).json()
        assert started["authorizing"]
        assert not client.post("/v1/providers/openrouter-account/cancel").json()["authorizing"]
        result = client.post(
            "/v1/providers/openrouter-account/complete",
            json={"code": "old", "attempt_id": started["attempt_id"]},
        ).json()
        assert result["error"] and not result["connected"]
        assert (
            client.post(
                "/v1/providers/openrouter-account/signin",
                json={},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
