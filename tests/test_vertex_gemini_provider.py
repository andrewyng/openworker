"""Tests for Vertex AI Gemini provider — configuration resolution, client build, and router integration."""

from __future__ import annotations

import os
from types import SimpleNamespace
import pytest

from coworker.providers import GeminiProvider, capabilities_for
from coworker.providers.gemini_provider import resolve_vertex_config
from coworker.providers.registry import build_provider_client, get_descriptor
from coworker.providers.router import ProviderRouter


def test_resolve_vertex_config_from_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_PROJECT_ID", "test-project-env")
    monkeypatch.setenv("GOOGLE_REGION", "europe-west1")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/creds.json")

    project, location, creds = resolve_vertex_config()
    assert project == "test-project-env"
    assert location == "europe-west1"
    assert creds == "/tmp/creds.json"


def test_resolve_vertex_config_from_secrets(monkeypatch):
    monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_REGION", raising=False)
    monkeypatch.delenv("GOOGLE_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    class _Secrets:
        def get(self, name):
            if name == "provider:vertex-gemini":
                return {
                    "project_id": "secret-project",
                    "location": "us-east1",
                    "credentials_path": "/path/to/key.json",
                }
            return None

    project, location, creds = resolve_vertex_config(_Secrets())
    assert project == "secret-project"
    assert location == "us-east1"
    assert creds == "/path/to/key.json"


def test_ensure_client_without_project_raises(monkeypatch):
    monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_REGION", raising=False)
    monkeypatch.delenv("GOOGLE_LOCATION", raising=False)

    provider = GeminiProvider(vertexai=True)
    with pytest.raises(RuntimeError, match="Vertex AI"):
        provider._ensure_client()


def test_vertex_gemini_descriptor_and_build():
    descriptor = get_descriptor("vertex-gemini")
    assert descriptor is not None
    assert descriptor.title == "Vertex AI Gemini (Google Cloud)"
    assert not descriptor.needs_key

    provider = build_provider_client(
        "vertex-gemini",
        {"project_id": "proj-123", "location": "us-central1"},
        secrets=None,
    )
    assert isinstance(provider, GeminiProvider)
    assert provider._vertexai is True
    assert provider._project == "proj-123"
    assert provider._location == "us-central1"


def test_vertex_gemini_capabilities():
    caps = capabilities_for("vertex-gemini:gemini-3.6-flash")
    assert caps.tools and caps.vision and caps.pdf and caps.streaming
    assert caps.parallel_tool_calls is True


def test_router_dispatches_vertex_gemini():
    router = ProviderRouter(secrets=None)
    assert router._provider_name("vertex-gemini:gemini-3.6-flash") == "vertex-gemini"
    assert router._bare("vertex-gemini:gemini-3.6-flash") == "gemini-3.6-flash"
