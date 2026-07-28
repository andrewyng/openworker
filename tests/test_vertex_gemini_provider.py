"""Tests for Gemini Enterprise (Vertex AI) provider — configuration resolution, client build, and router integration."""

from __future__ import annotations

import os
import pytest

from coworker.providers import GeminiProvider, capabilities_for
from coworker.providers.gemini_provider import resolve_vertex_config
from coworker.providers.registry import build_provider_client, get_descriptor
from coworker.providers.router import ProviderRouter


def test_resolve_vertex_config_from_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project-cloud")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/creds.json")

    project, location, creds = resolve_vertex_config()
    assert project == "test-project-cloud"
    assert location == "us-central1"
    assert creds == "/tmp/creds.json"


def test_resolve_vertex_config_defaults_to_global_location(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_REGION", raising=False)
    monkeypatch.delenv("GOOGLE_LOCATION", raising=False)

    project, location, _ = resolve_vertex_config()
    assert project == "test-project"
    assert location == "global"


def test_resolve_vertex_config_from_secrets(monkeypatch):
    monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_REGION", raising=False)
    monkeypatch.delenv("GOOGLE_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    class _Secrets:
        def get(self, name):
            if name == "provider:vertex-gemini":
                return {
                    "project_id": "secret-project",
                    "location": "europe-west1",
                    "credentials_path": "/path/to/key.json",
                }
            return None

    project, location, creds = resolve_vertex_config(_Secrets())
    assert project == "secret-project"
    assert location == "europe-west1"
    assert creds == "/path/to/key.json"


def test_ensure_client_without_project_raises(monkeypatch):
    monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_REGION", raising=False)
    monkeypatch.delenv("GOOGLE_LOCATION", raising=False)

    provider = GeminiProvider(vertexai=True)
    with pytest.raises(RuntimeError, match="Vertex AI"):
        provider._ensure_client()


def test_vertex_gemini_descriptor_and_build():
    descriptor = get_descriptor("vertex-gemini")
    assert descriptor is not None
    assert descriptor.title == "Gemini Enterprise (Vertex AI)"
    assert not descriptor.needs_key

    provider = build_provider_client(
        "vertex-gemini",
        {"project_id": "proj-123", "location": "global"},
        secrets=None,
    )
    assert isinstance(provider, GeminiProvider)
    assert provider._vertexai is True
    assert provider._project == "proj-123"
    assert provider._location == "global"


@pytest.mark.parametrize(
    "model_id",
    [
        "vertex-gemini:gemini-3.6-flash",
        "vertex-gemini:gemini-3.1-pro-preview",
        "vertex-gemini:gemini-3.5-flash-lite",
    ],
)
def test_vertex_gemini_capabilities(model_id):
    caps = capabilities_for(model_id)
    assert caps.tools and caps.vision and caps.pdf and caps.streaming
    assert caps.parallel_tool_calls is True


def test_router_dispatches_vertex_gemini():
    router = ProviderRouter(secrets=None)
    assert router._provider_name("vertex-gemini:gemini-3.6-flash") == "vertex-gemini"
    assert router._bare("vertex-gemini:gemini-3.6-flash") == "gemini-3.6-flash"
