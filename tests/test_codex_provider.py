"""ChatGPT-subscription provider (`openai-codex`): PKCE flow, token storage +
refresh, backend request shape, failure modes, and the REST surface. No live
network — the token endpoint, the SDK client, and the verify probe are all faked;
the loopback callback server is exercised for real on its fixed port."""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from coworker.providers import codex_auth
from coworker.providers.codex_auth import (
    CODEX_BASE_URL,
    CodexAuthError,
    CodexSignInRequired,
    CodexTokenStore,
    account_id_from,
    build_authorize_url,
    create_pkce,
)
from coworker.providers.codex_provider import CodexProvider
from coworker.secrets import SecretStore
from coworker.server.app import create_app
from coworker.server.manager import SessionManager

ACCOUNT_CLAIM = "https://api.openai.com/auth"


def _jwt(claims: dict) -> str:
    def b64(obj) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


def _access(exp_offset: float = 3600, account: str = "acct_1") -> str:
    return _jwt(
        {"exp": time.time() + exp_offset, ACCOUNT_CLAIM: {"chatgpt_account_id": account}}
    )


def _id_token(email: str = "user@example.com", account: str = "acct_1") -> str:
    return _jwt({"email": email, ACCOUNT_CLAIM: {"chatgpt_account_id": account}})


def _token_response(status: int = 200, body: dict | None = None):
    return SimpleNamespace(status_code=status, json=lambda: body or {})


def _seed(secrets, exp_offset: float = 3600) -> None:
    CodexTokenStore(secrets).save(
        {
            "access_token": _access(exp_offset),
            "refresh_token": "rt-1",
            "id_token": _id_token(),
        }
    )


# -- PKCE / authorize URL -----------------------------------------------------------


def test_pkce_challenge_is_s256_of_verifier():
    import hashlib

    verifier, challenge = create_pkce()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == expected
    assert create_pkce()[0] != verifier  # fresh randomness per flow


def test_authorize_url_shape():
    url = build_authorize_url("st4te", "ch4llenge")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == codex_auth.AUTHORIZE_URL
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert q["response_type"] == "code"
    assert q["client_id"] == codex_auth.CLIENT_ID
    assert q["redirect_uri"] == "http://localhost:1455/auth/callback"
    assert q["state"] == "st4te"
    assert q["code_challenge"] == "ch4llenge"
    assert q["code_challenge_method"] == "S256"
    assert q["codex_cli_simplified_flow"] == "true"
    assert q["originator"] == codex_auth.ORIGINATOR


def test_account_id_from_token_claim():
    tokens = {"id_token": _id_token(account="acct_9")}
    assert account_id_from(tokens) == "acct_9"
    # Falls back to the access token, and to the plain account_id key.
    tokens = {"access_token": _jwt({ACCOUNT_CLAIM: {"account_id": "acct_x"}})}
    assert account_id_from(tokens) == "acct_x"
    assert account_id_from({"access_token": "not-a-jwt"}) == ""


# -- sign-in flow (loopback callback → exchange → storage) --------------------------


async def _hit_callback(query: str) -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", codex_auth.CALLBACK_PORT)
    writer.write(
        f"GET /auth/callback?{query} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
    )
    await writer.drain()
    data = await reader.read(-1)
    writer.close()
    return data.decode()


def test_callback_host_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv(codex_auth.CALLBACK_HOST_ENV, raising=False)
    assert codex_auth.callback_host() == "127.0.0.1"
    # Empty is not a bind address — a blank env var must not become "listen
    # everywhere", which is the one mistake here that widens exposure silently.
    monkeypatch.setenv(codex_auth.CALLBACK_HOST_ENV, "")
    assert codex_auth.callback_host() == "127.0.0.1"


class _FakeServer:
    sockets: tuple = ()

    def close(self):
        pass

    async def wait_closed(self):
        pass


async def _bind_host_used(monkeypatch) -> str:
    """The address `_start_callback_server` actually asks asyncio to listen on.

    Asserted through the call rather than by binding for real: the port is fixed at
    1455, so a machine running the Codex CLI (its usual holder) or a container
    publishing that port would otherwise fail this test for unrelated reasons.
    """
    seen: dict = {}

    async def fake_start_server(handler, host, port):
        seen.update(host=host, port=port)
        return _FakeServer()

    monkeypatch.setattr(codex_auth.asyncio, "start_server", fake_start_server)
    server, future = await codex_auth._start_callback_server("state-1")
    future.cancel()
    assert seen["port"] == codex_auth.CALLBACK_PORT
    return seen["host"]


async def test_callback_server_binds_loopback_by_default(monkeypatch):
    monkeypatch.delenv(codex_auth.CALLBACK_HOST_ENV, raising=False)
    assert await _bind_host_used(monkeypatch) == "127.0.0.1"


async def test_callback_server_honours_the_bind_override(monkeypatch):
    """A container publishes a port to its external interface; a server bound to the
    container's own loopback never sees that traffic, so the redirect lands on a closed
    port. The registered redirect URI is unaffected — it says where the BROWSER goes."""
    monkeypatch.setenv(codex_auth.CALLBACK_HOST_ENV, "0.0.0.0")
    assert await _bind_host_used(monkeypatch) == "0.0.0.0"
    assert codex_auth.REDIRECT_URI == "http://localhost:1455/auth/callback"


async def test_sign_in_full_flow(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")
    opened: dict = {}
    exchanged: dict = {}

    monkeypatch.setattr("webbrowser.open", lambda url: opened.update(url=url))

    def fake_token_post(data, timeout=30.0):
        exchanged.update(data)
        return _token_response(
            body={
                "access_token": _access(),
                "refresh_token": "rt-1",
                "id_token": _id_token("user@example.com"),
            }
        )

    monkeypatch.setattr(codex_auth, "_token_post", fake_token_post)

    task = asyncio.create_task(codex_auth.sign_in(secrets))
    while not opened:  # wait for the flow to bind the port and "open" the browser
        await asyncio.sleep(0.01)
    state = parse_qs(urlsplit(opened["url"]).query)["state"][0]

    # A forged local hit with the wrong state is rejected and does NOT consume the flow.
    resp = await _hit_callback("code=evil&state=wrong")
    assert resp.startswith("HTTP/1.1 400")
    assert not task.done()

    resp = await _hit_callback(f"code=c0de&state={state}")
    assert resp.startswith("HTTP/1.1 200") and "close this tab" in resp.lower()
    result = await task

    assert result == {"ok": True, "account": "user@example.com"}
    assert exchanged["grant_type"] == "authorization_code"
    assert exchanged["code"] == "c0de"
    assert exchanged["redirect_uri"] == "http://localhost:1455/auth/callback"
    assert exchanged["code_verifier"]
    profile = secrets.get("provider:openai-codex")
    assert profile["tokens"]["refresh_token"] == "rt-1"
    assert profile["account_id"] == "acct_1"
    assert profile["account_email"] == "user@example.com"
    assert isinstance(profile["tokens_issued_at"], int)


async def test_sign_in_provider_error_from_callback(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")
    opened: dict = {}
    monkeypatch.setattr("webbrowser.open", lambda url: opened.update(url=url))

    task = asyncio.create_task(codex_auth.sign_in(secrets))
    while not opened:
        await asyncio.sleep(0.01)
    resp = await _hit_callback("error=access_denied")
    assert resp.startswith("HTTP/1.1 400")
    with pytest.raises(CodexAuthError, match="access_denied"):
        await task
    assert not CodexTokenStore(secrets).signed_in()


async def test_sign_in_port_busy_names_the_usual_holder(tmp_path):
    secrets = SecretStore(tmp_path / "s.json")
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        blocker.bind(("127.0.0.1", codex_auth.CALLBACK_PORT))
        blocker.listen(1)
        with pytest.raises(CodexAuthError, match="1455"):
            await codex_auth.sign_in(secrets, open_browser=False)
    finally:
        blocker.close()


# -- token store: refresh ------------------------------------------------------------


def test_access_token_fresh_needs_no_refresh(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")
    _seed(secrets)
    monkeypatch.setattr(
        codex_auth, "_token_post", lambda *a, **k: pytest.fail("refresh not needed")
    )
    token, account = CodexTokenStore(secrets).access_token()
    assert token == secrets.get("provider:openai-codex")["tokens"]["access_token"]
    assert account == "acct_1"


def test_access_token_refreshes_near_expiry(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")
    _seed(secrets, exp_offset=30)  # inside the refresh margin
    sent: dict = {}
    fresh = _access(7200)

    def fake_token_post(data, timeout=30.0):
        sent.update(data)
        return _token_response(body={"access_token": fresh})

    monkeypatch.setattr(codex_auth, "_token_post", fake_token_post)
    token, account = CodexTokenStore(secrets).access_token()
    assert token == fresh and account == "acct_1"
    assert sent == {
        "grant_type": "refresh_token",
        "refresh_token": "rt-1",
        "client_id": codex_auth.CLIENT_ID,
    }
    profile = secrets.get("provider:openai-codex")
    # The refresh response omitted refresh/id tokens — prior values survive.
    assert profile["tokens"]["access_token"] == fresh
    assert profile["tokens"]["refresh_token"] == "rt-1"


def test_rejected_refresh_blanks_tokens_to_signed_out(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")
    _seed(secrets, exp_offset=-10)
    monkeypatch.setattr(
        codex_auth, "_token_post", lambda *a, **k: _token_response(status=400)
    )
    store = CodexTokenStore(secrets)
    with pytest.raises(CodexSignInRequired):
        store.access_token()
    assert not store.signed_in()  # clean signed-out state, not a crash loop


def test_access_token_signed_out_raises_typed_error(tmp_path):
    store = CodexTokenStore(SecretStore(tmp_path / "s.json"))
    with pytest.raises(CodexSignInRequired, match="Not signed in"):
        store.access_token()


# -- provider: request shape to the backend -----------------------------------------


class _FakeSDKClient:
    def __init__(self, events=None, errors=None):
        self.kwargs: dict = {}
        errors = list(errors or [])

        def create(**kwargs):
            self.kwargs = kwargs
            if errors:
                raise errors.pop(0)
            return iter(events or [])

        self.responses = SimpleNamespace(create=create)


def _completed_event(text="hello"):
    return SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
            status="completed",
            incomplete_details=None,
        ),
    )


def _status_error(status: int, message: str = ""):
    exc = Exception(message or f"HTTP {status}")
    exc.status_code = status
    return exc


def _provider(tmp_path, monkeypatch, clients):
    """A CodexProvider over seeded tokens whose SDK clients come from `clients`
    (each OpenAI() construction pops the next one and records its init kwargs)."""
    secrets = SecretStore(tmp_path / "s.json")
    _seed(secrets)
    built: list[dict] = []

    def fake_openai(**kwargs):
        built.append(kwargs)
        return clients.pop(0)

    monkeypatch.setattr("openai.OpenAI", fake_openai)
    return CodexProvider(secrets=secrets), secrets, built


def test_stream_request_headers_and_body(tmp_path, monkeypatch):
    fake = _FakeSDKClient(events=[_completed_event()])
    provider, secrets, built = _provider(tmp_path, monkeypatch, [fake])

    out = list(
        provider.stream(
            model="gpt-5.2-codex",
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ],
            tools=[{"type": "function", "function": {"name": "f"}}],
        )
    )

    assert built[0]["api_key"] == secrets.get("provider:openai-codex")["tokens"][
        "access_token"
    ]
    assert built[0]["base_url"] == CODEX_BASE_URL
    headers = built[0]["default_headers"]
    assert headers["chatgpt-account-id"] == "acct_1"
    assert headers["originator"] == codex_auth.ORIGINATOR
    assert headers["OpenAI-Beta"] == "responses=experimental"
    assert headers["session-id"]  # per-conversation uuid
    assert fake.kwargs["model"] == "gpt-5.2-codex"
    assert fake.kwargs["stream"] is True
    assert fake.kwargs["store"] is False
    assert fake.kwargs["include"] == ["reasoning.encrypted_content"]
    assert fake.kwargs["instructions"] == "sys"
    assert fake.kwargs["tools"] == [{"type": "function", "name": "f"}]
    assert out[-1].turn.text == "hello"


def test_complete_goes_through_stream_and_reasoning_effort_rides(tmp_path, monkeypatch):
    fake = _FakeSDKClient(events=[_completed_event("done")])
    provider, _, _ = _provider(tmp_path, monkeypatch, [fake])
    turn = provider.complete(
        model="gpt-5.2-codex",
        messages=[{"role": "user", "content": "hi"}],
        reasoning_effort="high",
    )
    assert turn.text == "done"
    assert fake.kwargs["stream"] is True  # the backend only serves streams
    assert fake.kwargs["reasoning"] == {"summary": "auto", "effort": "high"}
    assert fake.kwargs["instructions"]  # bare-call fallback instructions present


def test_401_refreshes_once_and_retries_with_new_bearer(tmp_path, monkeypatch):
    first = _FakeSDKClient(errors=[_status_error(401, "Unauthorized")])
    second = _FakeSDKClient(events=[_completed_event("after refresh")])
    provider, secrets, built = _provider(tmp_path, monkeypatch, [first, second])
    fresh = _access(7200)
    monkeypatch.setattr(
        codex_auth,
        "_token_post",
        lambda *a, **k: _token_response(body={"access_token": fresh}),
    )
    turn = provider.complete(
        model="gpt-5.2-codex", messages=[{"role": "user", "content": "hi"}]
    )
    assert turn.text == "after refresh"
    assert len(built) == 2 and built[1]["api_key"] == fresh


def test_429_surfaces_plan_limit_message(tmp_path, monkeypatch):
    fake = _FakeSDKClient(errors=[_status_error(429, "Too Many Requests")])
    provider, _, _ = _provider(tmp_path, monkeypatch, [fake])
    with pytest.raises(RuntimeError, match="plan limit"):
        provider.complete(
            model="gpt-5.2-codex", messages=[{"role": "user", "content": "hi"}]
        )


def test_signed_out_provider_raises_typed_error(tmp_path):
    provider = CodexProvider(secrets=SecretStore(tmp_path / "s.json"))
    with pytest.raises(CodexSignInRequired, match="Not signed in"):
        provider.complete(
            model="gpt-5.2-codex", messages=[{"role": "user", "content": "hi"}]
        )


# -- registry / matrix ---------------------------------------------------------------


def test_registry_builds_codex_provider():
    from coworker.providers.registry import build_provider_client, get_descriptor

    assert isinstance(build_provider_client("openai-codex", {}, None), CodexProvider)
    d = get_descriptor("openai-codex")
    assert d.auth == "oauth" and d.fields == []
    assert d.to_dict()["auth"] == "oauth"


def test_descriptor_configured_means_tokens_present():
    from coworker.providers.registry import descriptor_configured, get_descriptor

    d = get_descriptor("openai-codex")
    assert not descriptor_configured(d, {})
    assert descriptor_configured(d, {"tokens": {"access_token": "a"}})


def test_matrix_curates_subscription_models():
    from coworker.providers.capabilities import capabilities_for
    from coworker.providers.matrix import models_for_provider

    assert models_for_provider("openai-codex") == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.2-codex",
        "gpt-5.2",
        "gpt-5.1-codex",
        "gpt-5.1-codex-mini",
    ]
    caps = capabilities_for("openai-codex:gpt-5.6-sol")
    assert caps.tools and caps.vision and caps.streaming


# -- live model catalog --------------------------------------------------------------


def _catalog_response(models, *, status=200, cache_control="max-age=120"):
    body = {"models": models}
    return SimpleNamespace(
        status_code=status,
        headers={"cache-control": cache_control},
        content=json.dumps(body).encode(),
        json=lambda: body,
    )


def test_discover_models_uses_account_catalog_and_filters_picker_rows(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")
    _seed(secrets)
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, headers=headers, timeout=timeout)
        return _catalog_response(
            [
                {
                    "slug": "gpt-6-luna",
                    "display_name": "GPT-6 Luna",
                    "visibility": "list",
                    "priority": 20,
                    "max_context_window": 500_000,
                    "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                    "default_reasoning_level": "high",
                    "input_modalities": ["text", "image"],
                },
                {
                    "slug": "internal-only",
                    "display_name": "Internal",
                    "visibility": "hide",
                    "priority": 0,
                },
                {
                    "slug": "gpt-6.1-sol",
                    "display_name": "GPT-6.1 Sol",
                    "visibility": "list",
                    "priority": 10,
                    "context_window": 900_000,
                },
                {  # duplicate slugs never create duplicate picker rows
                    "slug": "gpt-6.1-sol",
                    "display_name": "duplicate",
                    "visibility": "list",
                    "priority": 1,
                },
            ]
        )

    monkeypatch.setattr("httpx.get", fake_get)
    catalog = codex_auth.discover_models(secrets)
    assert seen["url"] == CODEX_BASE_URL + "/models"
    assert seen["params"] == {"client_version": codex_auth.CODEX_CATALOG_CLIENT_VERSION}
    assert seen["headers"]["chatgpt-account-id"] == "acct_1"
    assert seen["headers"]["originator"] == "openworker"
    assert [row["slug"] for row in catalog["models"]] == ["gpt-6.1-sol", "gpt-6-luna"]
    assert catalog["models"][1]["context_window"] == 500_000
    assert catalog["models"][1]["reasoning_levels"] == ["low", "high"]
    assert catalog["expires_at"] - catalog["fetched_at"] == 120


def test_discover_models_refreshes_once_after_401(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")
    _seed(secrets)
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(headers["Authorization"])
        if len(calls) == 1:
            return _catalog_response([], status=401)
        return _catalog_response(
            [{"slug": "gpt-6-luna", "visibility": "list", "priority": 1}]
        )

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr(CodexTokenStore, "refresh", lambda self: ("fresh-token", "acct_1"))
    catalog = codex_auth.discover_models(secrets)
    assert len(calls) == 2
    assert calls[0].startswith("Bearer ")
    assert calls[1] == "Bearer fresh-token"
    assert catalog["models"][0]["slug"] == "gpt-6-luna"


def test_discover_models_rejects_malformed_catalog(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")
    _seed(secrets)
    response = _catalog_response([])
    response.json = lambda: {"unexpected": []}
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: response)
    with pytest.raises(CodexAuthError, match="invalid model catalog"):
        codex_auth.discover_models(secrets)


@pytest.mark.parametrize("optional", [None, 42, "unexpected", {"future": True}])
def test_catalog_ignores_unrecognized_optional_metadata(optional):
    rows = codex_auth._parse_catalog({"models": [{
        "slug": "gpt-live", "visibility": "list",
        "supported_reasoning_levels": optional, "input_modalities": optional,
        "future_field": {"instructions": "not picker metadata"},
    }]})
    assert rows == [{"slug": "gpt-live", "display_name": "gpt-live", "priority": 0}]


def test_account_change_discards_previous_catalog(tmp_path):
    secrets = SecretStore(tmp_path / "s.json")
    _seed(secrets)
    profile = secrets.get(codex_auth.PROFILE)
    profile["model_catalog"] = {"models": [{"slug": "account-a-only"}]}
    secrets.put(codex_auth.PROFILE, profile)
    store = CodexTokenStore(secrets)

    store.save({"access_token": _access(), "refresh_token": "rt-new"})
    assert secrets.get(codex_auth.PROFILE)["model_catalog"]["models"]
    store.save({
        "access_token": _access(account="acct_2"),
        "id_token": _id_token("other@example.com", "acct_2"),
        "refresh_token": "rt-other",
    })
    assert not secrets.get(codex_auth.PROFILE).get("model_catalog")


@pytest.mark.parametrize("change", ["signout", "rotate", "switch"])
def test_catalog_commit_preserves_concurrent_auth_changes(tmp_path, monkeypatch, change):
    manager, _ = _rest(tmp_path)
    _seed(manager.secrets)
    monkeypatch.setattr(codex_auth, "discover_models", lambda secrets: {
        "models": [{"slug": "gpt-live"}], "expires_at": time.time() + 300,
    })
    committing = threading.Event()
    exclusive = manager.secrets.exclusive
    result = []
    worker = threading.Thread(target=lambda: result.append(manager.refresh_codex_catalog(True)))

    @contextmanager
    def observed_exclusive(*args, **kwargs):
        if threading.current_thread() is worker:
            committing.set()
        with exclusive(*args, **kwargs) as store:
            yield store

    monkeypatch.setattr(manager.secrets, "exclusive", observed_exclusive)
    with exclusive():
        worker.start()
        assert committing.wait(5), "catalog did not reach its commit"
        if change == "signout":
            CodexTokenStore(manager.secrets).clear()
        else:
            account = "acct_2" if change == "switch" else "acct_1"
            CodexTokenStore(manager.secrets).save({
                "access_token": _access(account=account),
                "id_token": _id_token(account=account), "refresh_token": "rt-new",
            })
    worker.join(5)
    assert not worker.is_alive()
    profile = manager.secrets.get(codex_auth.PROFILE)
    if change == "signout":
        assert profile is None
    else:
        assert profile["tokens"]["refresh_token"] == "rt-new"
        assert profile["account_id"] == account
    assert result == [change == "rotate"]


def test_picker_derives_discoveries_without_persisting_them_as_manual_models(tmp_path, monkeypatch):
    manager, _ = _rest(tmp_path)
    _seed(manager.secrets)
    monkeypatch.setattr(codex_auth, "discover_models", lambda secrets: {
        "models": [{"slug": "gpt-live"}], "expires_at": time.time() + 300,
    })
    assert manager.refresh_codex_catalog(True)
    assert "openai-codex:gpt-live" in manager.get_settings()["models"]
    assert "openai-codex:gpt-live" not in manager._prefs.get("models", [])
    manager.add_model("openai-codex:my-manual-model")
    monkeypatch.setattr(codex_auth, "discover_models", lambda secrets: {
        "models": [{"slug": "gpt-next"}], "expires_at": time.time() + 300,
    })
    assert manager.refresh_codex_catalog(True)
    models = manager.get_settings()["models"]
    assert "openai-codex:gpt-live" not in models
    assert "openai-codex:gpt-next" in models
    assert "openai-codex:my-manual-model" in models


def test_catalog_cache_survives_outage_and_skips_fresh_fetches(tmp_path, monkeypatch):
    manager, _ = _rest(tmp_path)
    _seed(manager.secrets)
    catalog = {
        "models": [{"slug": "gpt-live"}],
        "expires_at": time.time() + 300,
    }
    monkeypatch.setattr(codex_auth, "discover_models", lambda secrets: catalog)
    assert manager.refresh_codex_catalog(True)
    calls = []

    def unavailable(secrets):
        calls.append(True)
        raise CodexAuthError("catalog unavailable")

    monkeypatch.setattr(codex_auth, "discover_models", unavailable)
    assert manager.refresh_codex_catalog() is False
    assert not calls
    assert manager.refresh_codex_catalog(True) is False
    assert len(calls) == 1
    assert manager.secrets.get(codex_auth.PROFILE)["model_catalog"] == catalog
    assert "openai-codex:gpt-live" in manager.get_settings()["models"]


# -- verify probe --------------------------------------------------------------------


def _verify_with_backend(tmp_path, monkeypatch, status: int):
    secrets = SecretStore(tmp_path / "s.json")
    _seed(secrets)
    probes: dict = {}

    monkeypatch.setattr(
        "httpx.get",
        lambda *args, **kwargs: _catalog_response(
            [{"slug": "gpt-current", "visibility": "list", "priority": 1}]
        ),
    )

    def fake_post(url, headers=None, json=None, timeout=None):
        probes.update(url=url, headers=headers, body=json)
        return SimpleNamespace(status_code=status)

    monkeypatch.setattr("httpx.post", fake_post)
    return codex_auth.verify(secrets), probes


def test_verify_signed_out(tmp_path):
    result = codex_auth.verify(SecretStore(tmp_path / "s.json"))
    assert result["ok"] is False and result["state"] == "signed_out"


def test_verify_ok_probes_backend(tmp_path, monkeypatch):
    result, probes = _verify_with_backend(tmp_path, monkeypatch, 200)
    assert result == {"ok": True, "account": "user@example.com"}
    assert probes["url"] == CODEX_BASE_URL + "/responses"
    assert probes["headers"]["Authorization"].startswith("Bearer ")
    assert probes["headers"]["chatgpt-account-id"] == "acct_1"
    assert probes["body"]["model"] == "gpt-current"
    assert probes["body"]["store"] is False and probes["body"]["stream"] is True
    assert probes["body"]["instructions"]
    assert isinstance(probes["body"]["input"], list)
    assert "max_output_tokens" not in probes["body"]


def test_verify_expired_and_plan_limited(tmp_path, monkeypatch):
    result, _ = _verify_with_backend(tmp_path, monkeypatch, 401)
    assert result["ok"] is False and result["state"] == "expired"
    result, _ = _verify_with_backend(tmp_path, monkeypatch, 429)
    assert result["ok"] is True and "note" in result  # auth fine, window used up


# -- REST surface --------------------------------------------------------------------


def _rest(tmp_path):
    manager = SessionManager(data_dir=tmp_path / "data")
    return manager, TestClient(create_app(manager))


def test_providers_list_shows_oauth_state(tmp_path):
    manager, client = _rest(tmp_path)
    rows = {p["name"]: p for p in client.get("/v1/providers").json()}
    row = rows["openai-codex"]
    assert row["auth"] == "oauth"
    assert row["signed_in"] is False and row["configured"] is False
    assert "gpt-5.6-sol" in row["suggested_models"]

    manager.secrets.put(
        "provider:openai-codex",
        {
            "tokens": {"access_token": "a", "refresh_token": "r"},
            "account_email": "u@x.com",
            "model_catalog": {
                "models": [{"slug": "gpt-live", "display_name": "GPT Live", "priority": 1}],
                "expires_at": time.time() + 300,
            },
        },
    )
    row = {p["name"]: p for p in client.get("/v1/providers").json()}["openai-codex"]
    assert row["signed_in"] is True and row["configured"] is True
    assert row["account"] == "u@x.com"
    assert "tokens" not in row.get("values", {})  # secrets never leave the store


def test_manager_merges_live_catalog_into_picker_metadata(tmp_path, monkeypatch):
    manager, _ = _rest(tmp_path)
    _seed(manager.secrets)
    catalog = {
        "models": [
            {
                "slug": "gpt-6.1-sol",
                "display_name": "GPT-6.1 Sol",
                "priority": 1,
                "context_window": 900_000,
                "reasoning_levels": ["low", "medium", "high", "ultra"],
                "default_reasoning_level": "medium",
            },
            {"slug": "gpt-6-luna", "display_name": "GPT-6 Luna", "priority": 2},
        ],
        "fetched_at": int(time.time()),
        "expires_at": int(time.time()) + 300,
        "client_version": codex_auth.CODEX_CATALOG_CLIENT_VERSION,
    }
    monkeypatch.setattr(codex_auth, "discover_models", lambda secrets: catalog)

    assert manager.refresh_codex_catalog(force=True) is True
    providers = {row["name"]: row for row in manager.get_providers()}
    assert providers["openai-codex"]["suggested_models"] == ["gpt-6.1-sol", "gpt-6-luna"]
    settings = manager.get_settings()
    assert "openai-codex:gpt-6.1-sol" in settings["models"]
    assert "openai-codex:gpt-5.2-codex" not in settings["models"]
    assert settings["model_labels"]["openai-codex:gpt-6.1-sol"] == (
        "GPT-6.1 Sol · ChatGPT plan"
    )
    assert settings["model_context_windows"]["openai-codex:gpt-6.1-sol"] == 900_000
    controls = manager.model_controls("openai-codex:gpt-6.1-sol")
    assert controls["reasoning"] == {
        "support": "supported",
        "levels": ["low", "medium", "high"],
        "default": "medium",
    }

    # A user's removal remains sticky when the same catalog refreshes again.
    manager.remove_model("openai-codex:gpt-6-luna")
    assert "openai-codex:gpt-6-luna" in manager._prefs["hidden_models"]
    assert manager.refresh_codex_catalog(force=True) is True
    assert "openai-codex:gpt-6-luna" not in manager.get_settings()["models"]


def test_signin_route_starts_background_flow(tmp_path, monkeypatch):
    manager, _ = _rest(tmp_path)
    seen = {}

    async def fake_signin():
        seen["called"] = True
        return {"ok": True}

    monkeypatch.setattr(manager, "codex_signin", fake_signin)
    client = TestClient(create_app(manager))
    assert client.post("/v1/providers/openai-codex/signin").json() == {
        "ok": True,
        "started": True,
    }
    assert seen["called"]
    # begin_codex_signin flagged before the task ran, so the very first status
    # poll after the button press already shows authorizing.
    assert manager._codex_authorizing is True


def test_status_and_signout_routes(tmp_path):
    manager, client = _rest(tmp_path)
    status = client.get("/v1/providers/openai-codex/status").json()
    assert status["signed_in"] is False and status["authorizing"] is False

    manager.secrets.put(
        "provider:openai-codex",
        {"tokens": {"access_token": "a"}, "account_email": "u@x.com"},
    )
    status = client.get("/v1/providers/openai-codex/status").json()
    assert status["signed_in"] is True and status["account"] == "u@x.com"

    assert client.post("/v1/providers/openai-codex/signout").json() == {
        "ok": True,
        "had_tokens": True,
    }
    assert client.get("/v1/providers/openai-codex/status").json()["signed_in"] is False


def test_verify_route_reports_signed_out(tmp_path):
    _, client = _rest(tmp_path)
    result = client.post("/v1/providers/verify", json={"name": "openai-codex"}).json()
    assert result["ok"] is False and result["state"] == "signed_out"


@pytest.mark.parametrize("working_default, hidden", [(False, False), (True, False), (True, True)])
async def test_manager_signin_stores_and_promotes_model(tmp_path, monkeypatch, working_default, hidden):
    manager, _ = _rest(tmp_path)
    previous_default = manager.model
    if working_default:
        manager.secrets.put("provider:openai", {"api_key": "test-key"})
    if hidden:
        manager._prefs["hidden_models"] = ["openai-codex:gpt-6.1-sol"]

    async def fake_sign_in(secrets, **kwargs):
        CodexTokenStore(secrets).save(
            {
                "access_token": _access(),
                "refresh_token": "rt-1",
                "id_token": _id_token(),
            }
        )
        return {"ok": True, "account": "user@example.com"}

    monkeypatch.setattr(codex_auth, "sign_in", fake_sign_in)
    monkeypatch.setattr(
        codex_auth,
        "discover_models",
        lambda secrets: {
            "models": [
                {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol", "priority": 1}
            ],
            "fetched_at": int(time.time()),
            "expires_at": int(time.time()) + 300,
            "client_version": codex_auth.CODEX_CATALOG_CLIENT_VERSION,
        },
    )
    result = await manager.codex_signin()
    assert result["ok"] is True
    assert manager._codex_authorizing is False
    settings = manager.get_settings()
    assert ("openai-codex:gpt-6.1-sol" in settings["models"]) is not hidden
    assert "openai-codex:gpt-6.1-sol" not in manager._prefs.get("models", [])
    if working_default:
        assert manager.model == previous_default


async def test_manager_signin_failure_lands_in_status(tmp_path, monkeypatch):
    manager, client = _rest(tmp_path)

    async def fake_sign_in(secrets, **kwargs):
        raise CodexAuthError("Port 1455 is already in use — quit the other holder.")

    monkeypatch.setattr(codex_auth, "sign_in", fake_sign_in)
    result = await manager.codex_signin()
    assert result["ok"] is False
    status = client.get("/v1/providers/openai-codex/status").json()
    assert "1455" in status["last_error"]
    assert status["authorizing"] is False


def test_request_strips_backend_unsupported_params(tmp_path):
    # The plan backend 400s on standard sampling/cap knobs ("Unsupported parameter:
    # max_output_tokens" / "temperature") — which silently killed every autotitle attempt
    # on plan sessions (owner catch 2026-08-24). The provider strips them; the reasoning
    # effort knob still rides.
    from coworker.secrets import SecretStore

    provider = CodexProvider(secrets=SecretStore(tmp_path / "s.json"))
    kwargs = provider._request_kwargs(
        model="gpt-5.6-sol",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        settings={"max_tokens": 64, "temperature": 0.2, "top_p": 0.9, "reasoning_effort": "none"},
    )
    assert "max_output_tokens" not in kwargs
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
    assert kwargs["reasoning"]["effort"] == "none"
