"""OpenRouter account login: PKCE, isolated credentials, and stale-flow safety."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from coworker.providers import openrouter_auth
from coworker.secrets import SecretStore


def test_manual_login_uses_pkce_and_keeps_credentials_out_of_status(
    tmp_path, monkeypatch
):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("provider:openrouter", {"api_key": "manual-existing"})
    exchanged = []

    async def exchange(code, verifier):
        exchanged.append((code, verifier))
        return "account-secret"

    monkeypatch.setattr(openrouter_auth, "exchange_key", exchange)
    auth = openrouter_auth.OpenRouterAuth(store)

    async def run():
        started = await auth.start(manual=True)
        assert started["authorizing"]
        assert not started["connected"]
        url = urlsplit(started["authorize_url"])
        assert url.scheme == "https" and url.netloc == "openrouter.ai"
        query = parse_qs(url.query)
        assert query["code_challenge_method"] == ["S256"]
        completed = await auth.complete("returned-code", started["attempt_id"])
        assert completed["connected"]
        assert not completed["authorizing"]
        code, verifier = exchanged[0]
        assert code == "returned-code"
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        assert query["code_challenge"] == [challenge]
        assert verifier not in json.dumps(started)
        assert "account-secret" not in json.dumps(completed)
        assert "account-secret" not in json.dumps(auth.status())

    asyncio.run(run())
    assert store.get("provider:openrouter-account")["api_key"] == "account-secret"
    assert store.get("provider:openrouter")["api_key"] == "manual-existing"
    assert openrouter_auth.OpenRouterAuth(store).status()["connected"]


def test_expired_attempt_cannot_complete(tmp_path, monkeypatch):
    auth = openrouter_auth.OpenRouterAuth(SecretStore(tmp_path / "secrets.json"))

    async def exchange(code, verifier):
        raise AssertionError("Expired attempts must not exchange credentials")

    monkeypatch.setattr(openrouter_auth, "exchange_key", exchange)

    async def run():
        started = await auth.start(manual=True)
        auth._expire()
        assert not auth.status()["authorizing"]
        assert "expired" in auth.status()["error"].lower()
        await auth.complete("code", started["attempt_id"])
        assert not auth.status()["connected"]

    asyncio.run(run())


def test_cancel_during_listener_creation_closes_stale_listener(tmp_path, monkeypatch):
    auth = openrouter_auth.OpenRouterAuth(SecretStore(tmp_path / "secrets.json"))

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class Listener:
            closed = False

            def close(self):
                self.closed = True

        listener = Listener()

        async def bind(*args, **kwargs):
            entered.set()
            await release.wait()
            return listener

        monkeypatch.setattr(openrouter_auth.asyncio, "start_server", bind)
        pending = asyncio.create_task(auth.start())
        await entered.wait()
        auth.cancel()
        release.set()
        status = await pending
        assert listener.closed
        assert not status["authorizing"]
        assert status["authorize_url"] is None
        assert auth.server is None
        assert auth.timer is None

    asyncio.run(run())


def test_account_uses_official_endpoint_and_ignores_other_keys(tmp_path, monkeypatch):
    from coworker.providers import registry

    store = SecretStore(tmp_path / "secrets.json")
    store.put("provider:openrouter-account", {"api_key": "account-key"})
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment-key")
    monkeypatch.setattr(registry, "OpenAIProvider", lambda **kwargs: kwargs)
    builder = registry._openai_compat(
        "OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"
    )
    profile = {
        "auth_method": "account",
        "account_connected": True,
        "api_key": "manual-key",
        "base_url": "https://untrusted.example",
    }
    assert builder(profile, store) == {
        "api_key": "account-key",
        "base_url": "https://openrouter.ai/api/v1",
    }
    profile["account_connected"] = False
    with pytest.raises(RuntimeError, match="disconnected"):
        builder(profile, store)


def test_cancel_during_exchange_cannot_restore_account(tmp_path, monkeypatch):
    store = SecretStore(tmp_path / "secrets.json")
    auth = openrouter_auth.OpenRouterAuth(store)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def exchange(code, verifier):
            entered.set()
            await release.wait()
            return "late-secret"

        monkeypatch.setattr(openrouter_auth, "exchange_key", exchange)
        started = await auth.start(manual=True)
        pending = asyncio.create_task(auth.complete("code", started["attempt_id"]))
        await entered.wait()
        assert not auth.cancel()["authorizing"]
        release.set()
        await pending
        assert not auth.status()["connected"]

    asyncio.run(run())
    assert store.get("provider:openrouter-account") is None


def test_replaced_attempt_cannot_complete(tmp_path, monkeypatch):
    store = SecretStore(tmp_path / "secrets.json")
    calls = []

    async def exchange(code, verifier):
        calls.append(code)
        return "new-secret"

    monkeypatch.setattr(openrouter_auth, "exchange_key", exchange)
    auth = openrouter_auth.OpenRouterAuth(store)

    async def run():
        first = await auth.start(manual=True)
        second = await auth.start(manual=True)
        assert first["attempt_id"] != second["attempt_id"]
        await auth.complete("stale-code", first["attempt_id"])
        assert calls == []
        assert store.get("provider:openrouter-account") is None
        await auth.complete("current-code", second["attempt_id"])
        assert calls == ["current-code"]

    asyncio.run(run())


def test_exchange_failure_preserves_previous_account(tmp_path, monkeypatch):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("provider:openrouter-account", {"api_key": "previous-secret"})

    async def exchange(code, verifier):
        raise RuntimeError("remote response included sensitive-provider-data")

    monkeypatch.setattr(openrouter_auth, "exchange_key", exchange)
    auth = openrouter_auth.OpenRouterAuth(store)

    async def run():
        started = await auth.start(manual=True)
        result = await auth.complete("code", started["attempt_id"])
        assert result["connected"]
        assert result["error"]
        assert "sensitive-provider-data" not in json.dumps(result)

    asyncio.run(run())
    assert store.get("provider:openrouter-account")["api_key"] == "previous-secret"


def test_disconnect_only_removes_account_and_rejects_pending_completion(
    tmp_path, monkeypatch
):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("provider:openrouter", {"api_key": "manual-secret"})
    store.put("provider:openrouter-account", {"api_key": "account-secret"})
    auth = openrouter_auth.OpenRouterAuth(store)

    async def exchange(code, verifier):
        raise AssertionError("Disconnected attempts must not exchange credentials")

    monkeypatch.setattr(openrouter_auth, "exchange_key", exchange)

    async def run():
        started = await auth.start(manual=True)
        result = auth.disconnect()
        assert not result["connected"] and not result["authorizing"]
        await auth.complete("old-code", started["attempt_id"])

    asyncio.run(run())
    assert store.get("provider:openrouter-account") is None
    assert store.get("provider:openrouter")["api_key"] == "manual-secret"


def test_exchange_validates_key_with_authenticated_endpoint(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"key": "sk-or-test-secret"})
        return httpx.Response(200, json={"data": {"label": "OpenWorker"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(openrouter_auth.httpx, "AsyncClient", lambda **kwargs: client)
    assert (
        asyncio.run(openrouter_auth.exchange_key("code", "verifier"))
        == "sk-or-test-secret"
    )
    assert len(requests) == 2
    assert str(requests[0].url) == "https://openrouter.ai/api/v1/auth/keys"
    assert json.loads(requests[0].content) == {
        "code": "code",
        "code_verifier": "verifier",
        "code_challenge_method": "S256",
    }
    assert str(requests[1].url) == "https://openrouter.ai/api/v1/auth/key"
    assert requests[1].headers["authorization"] == "Bearer sk-or-test-secret"


def test_loopback_callback_requires_attempt_specific_path(tmp_path, monkeypatch):
    store = SecretStore(tmp_path / "secrets.json")
    auth = openrouter_auth.OpenRouterAuth(store)
    codes = []

    async def exchange(code, verifier):
        codes.append(code)
        return "sk-or-callback-secret"

    monkeypatch.setattr(openrouter_auth, "exchange_key", exchange)

    async def run():
        started = await auth.start()
        callback = parse_qs(urlsplit(started["authorize_url"]).query)["callback_url"][0]
        address = urlsplit(callback)
        assert address.hostname in {"127.0.0.1", "localhost"}
        try:
            async with httpx.AsyncClient() as client:
                wrong = await client.get(f"http://{address.netloc}/wrong?code=bad")
                assert wrong.status_code == 400
                assert codes == []
                correct = await client.get(callback, params={"code": "valid-code"})
                assert correct.status_code == 200
                assert "sk-or-callback-secret" not in correct.text
            assert codes == ["valid-code"]
            assert auth.status()["connected"]
        finally:
            auth.cancel()

    asyncio.run(run())
