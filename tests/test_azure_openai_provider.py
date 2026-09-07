"""Azure OpenAI v1 authentication through a Microsoft Entra service principal."""

from __future__ import annotations

from types import SimpleNamespace

from coworker.providers import OpenAIProvider
from coworker.providers.openai_provider import (
    AZURE_OPENAI_SCOPE,
    build_azure_ad_token_provider,
)
from coworker.providers.registry import (
    build_provider_client,
    descriptor_configured,
    get_descriptor,
    verify_provider_key,
)


AZURE_FIELDS = {
    "auth_method": "azure_ad",
    "base_url": "https://contoso.openai.azure.com/openai/v1",
    "tenant_id": "tenant-1",
    "client_id": "client-1",
    "client_secret": "secret-1",
}


def test_token_provider_uses_client_secret_credential_and_azure_scope(monkeypatch):
    captured: dict = {}
    credential = object()

    def token_provider():
        return "token"

    def fake_credential(**kwargs):
        captured["credential_args"] = kwargs
        return credential

    def fake_provider(received_credential, scope):
        captured["provider_args"] = (received_credential, scope)
        return token_provider

    monkeypatch.setattr("azure.identity.ClientSecretCredential", fake_credential)
    monkeypatch.setattr("azure.identity.get_bearer_token_provider", fake_provider)

    result = build_azure_ad_token_provider("tenant-1", "client-1", "secret-1")

    assert result is token_provider
    assert captured["credential_args"] == {
        "tenant_id": "tenant-1",
        "client_id": "client-1",
        "client_secret": "secret-1",
    }
    assert captured["provider_args"] == (credential, AZURE_OPENAI_SCOPE)


def test_registry_builds_azure_openai_with_refreshable_token_callback(monkeypatch):
    def token_provider():
        return "fresh-token"

    captured: dict = {}

    monkeypatch.setattr(
        "coworker.providers.registry.build_azure_ad_token_provider",
        lambda tenant, client, secret: (
            captured.update(tenant=tenant, client=client, secret=secret)
            or token_provider
        ),
    )

    provider = build_provider_client("openai", AZURE_FIELDS, secrets=None)

    assert isinstance(provider, OpenAIProvider)
    assert provider._api_key is token_provider
    assert provider._base_url == AZURE_FIELDS["base_url"]
    assert captured == {
        "tenant": "tenant-1",
        "client": "client-1",
        "secret": "secret-1",
    }


def test_openai_sdk_receives_token_callback_unchanged(monkeypatch):
    def token_provider():
        return "fresh-token"

    captured: dict = {}
    client = object()

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setattr("openai.OpenAI", fake_openai)

    provider = OpenAIProvider(
        api_key=token_provider,
        base_url=AZURE_FIELDS["base_url"],
    )

    assert provider._ensure_client() is client
    assert captured == {
        "api_key": token_provider,
        "base_url": AZURE_FIELDS["base_url"],
    }


def test_descriptor_configuration_is_auth_method_specific(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    descriptor = get_descriptor("openai")
    assert descriptor is not None

    assert not descriptor_configured(descriptor, {})
    assert descriptor_configured(descriptor, {"api_key": "sk-existing"})
    assert not descriptor_configured(descriptor, {**AZURE_FIELDS, "client_secret": ""})
    assert descriptor_configured(descriptor, AZURE_FIELDS)


def test_verify_azure_openai_uses_bearer_token_and_v1_models(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "coworker.providers.registry.build_azure_ad_token_provider",
        lambda *_args: lambda: "entra-token",
    )

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("httpx.get", fake_get)

    assert verify_provider_key("openai", fields=AZURE_FIELDS) == {"ok": True}
    assert captured["url"] == AZURE_FIELDS["base_url"] + "/models"
    assert captured["headers"] == {"Authorization": "Bearer entra-token"}


def test_verify_azure_openai_reports_missing_fields_without_request(monkeypatch):
    called = False

    def fake_get(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("httpx.get", fake_get)

    result = verify_provider_key(
        "openai", fields={"auth_method": "azure_ad", "tenant_id": "tenant-1"}
    )

    assert result["ok"] is False
    assert "Custom endpoint" in result["error"]
    assert "Client secret" in result["error"]
    assert called is False


def test_verify_azure_openai_maps_rbac_failure(monkeypatch):
    monkeypatch.setattr(
        "coworker.providers.registry.build_azure_ad_token_provider",
        lambda *_args: lambda: "entra-token",
    )
    monkeypatch.setattr(
        "httpx.get", lambda *_args, **_kwargs: SimpleNamespace(status_code=403)
    )

    result = verify_provider_key("openai", fields=AZURE_FIELDS)

    assert result == {
        "ok": False,
        "error": "The service principal lacks access to this Azure OpenAI resource.",
    }


def test_manager_saves_azure_credentials_without_exposing_secrets(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from coworker.server.manager import SessionManager

    manager = SessionManager(data_dir=tmp_path)
    captured: dict = {}

    def fake_verify(name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr("coworker.server.manager.verify_provider_key", fake_verify)

    assert manager.verify_provider("openai", AZURE_FIELDS) == {"ok": True}
    assert captured["api_key"] == ""
    assert captured["fields"] == AZURE_FIELDS
    assert manager.set_provider("openai", AZURE_FIELDS)["ok"] is True

    openai = {p["name"]: p for p in manager.get_providers()}["openai"]
    assert openai["configured"] is True
    assert openai["values"] == {
        "auth_method": "azure_ad",
        "base_url": AZURE_FIELDS["base_url"],
        "tenant_id": "tenant-1",
        "client_id": "client-1",
    }
    assert "client_secret" not in openai["values"]
    assert manager.secrets.get("provider:openai")["client_secret"] == "secret-1"
