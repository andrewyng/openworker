"""ChatGPT-subscription provider (`openai-codex`): PKCE flow, token storage +
refresh, backend request shape, failure modes, and the REST surface. No live
network — the token endpoint, the SDK client, and the verify probe are all faked;
the loopback callback server is exercised for real on its fixed port."""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import time
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


# -- verify probe --------------------------------------------------------------------


def _verify_with_backend(tmp_path, monkeypatch, status: int):
    secrets = SecretStore(tmp_path / "s.json")
    _seed(secrets)
    probes: dict = {}

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
    assert probes["body"]["store"] is False and probes["body"]["stream"] is True


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
        {"tokens": {"access_token": "a", "refresh_token": "r"}, "account_email": "u@x.com"},
    )
    row = {p["name"]: p for p in client.get("/v1/providers").json()}["openai-codex"]
    assert row["signed_in"] is True and row["configured"] is True
    assert row["account"] == "u@x.com"
    assert "tokens" not in row.get("values", {})  # secrets never leave the store


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


async def test_manager_signin_stores_and_promotes_model(tmp_path, monkeypatch):
    manager, _ = _rest(tmp_path)

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
    result = await manager.codex_signin()
    assert result["ok"] is True
    assert manager._codex_authorizing is False
    settings = manager.get_settings()
    assert "openai-codex:gpt-5.6-sol" in settings["models"]


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
