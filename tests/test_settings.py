"""Tests for the model API-key settings path (Tauri desktop Phase 2).

A Tauri-launched sidecar doesn't inherit the shell env, so the key may live only in the
SecretStore. These cover: the env→store resolver, the status shape (never leaks the key),
and the REST round-trip. No network, no model calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coworker.providers import resolve_api_key
from coworker.secrets import SecretStore


def test_resolve_api_key_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-123")
    secrets = SecretStore(path=tmp_path / "secrets.json")
    secrets.put("provider:openai", {"type": "api_key", "api_key": "sk-store-999"})
    assert resolve_api_key(secrets) == "sk-env-123"


def test_resolve_api_key_falls_back_to_store(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    secrets = SecretStore(path=tmp_path / "secrets.json")
    assert resolve_api_key(secrets) is None
    secrets.put("provider:openai", {"type": "api_key", "api_key": "sk-store-999"})
    assert resolve_api_key(secrets) == "sk-store-999"


def test_settings_rest_roundtrip(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    client = TestClient(create_app(manager))

    before = client.get("/v1/settings").json()
    assert (
        before["has_key"] is False
        and before["source"] is None
        and before["provider"] == "openai"
    )
    assert before["onboarded"] is False and before["model"] in before["models"]

    set_resp = client.post(
        "/v1/settings/model-key", json={"api_key": "sk-secret-xyz"}
    ).json()
    assert (
        set_resp["ok"] is True
        and set_resp["has_key"] is True
        and set_resp["source"] == "store"
    )

    after = client.get("/v1/settings").json()
    assert after["has_key"] is True
    # the key value is never returned by either endpoint
    assert "sk-secret-xyz" not in str(set_resp) and "api_key" not in after

    # empty key is rejected
    assert (
        client.post("/v1/settings/model-key", json={"api_key": "  "}).json()["ok"]
        is False
    )


def test_default_model_and_onboarding_persist(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    client = TestClient(create_app(SessionManager(data_dir=data_dir)))

    # set a default model + mark onboarded
    assert (
        client.post("/v1/settings/default-model", json={"model": "gpt-4o"}).json()[
            "model"
        ]
        == "gpt-4o"
    )
    assert (
        client.post("/v1/settings/onboarded", json={"value": True}).json()["onboarded"]
        is True
    )
    assert (
        client.post("/v1/settings/default-model", json={"model": " "}).json()["ok"]
        is False
    )

    # a fresh manager over the same data dir restores both from prefs.json
    reborn = SessionManager(data_dir=data_dir)
    assert reborn.model == "gpt-4o"
    s = reborn.get_settings()
    assert s["onboarded"] is True and s["model"] == "gpt-4o"


def test_nav_layout_setting_roundtrips(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    client = TestClient(create_app(SessionManager(data_dir=data_dir)))

    # defaults to "flat"
    assert client.get("/v1/settings").json()["nav_layout"] == "flat"

    resp = client.post("/v1/settings/nav-layout", json={"nav_layout": "grouped"}).json()
    assert resp == {"ok": True, "nav_layout": "grouped"}
    assert client.get("/v1/settings").json()["nav_layout"] == "grouped"

    # unknown value falls back to flat; persists across a restart
    assert (
        client.post("/v1/settings/nav-layout", json={"nav_layout": "bogus"}).json()[
            "nav_layout"
        ]
        == "flat"
    )
    client.post("/v1/settings/nav-layout", json={"nav_layout": "grouped"})
    reborn = SessionManager(data_dir=data_dir)
    assert reborn.get_settings()["nav_layout"] == "grouped"


def test_scratch_base_setting_persists_and_drives_provisioning(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    client = TestClient(create_app(SessionManager(data_dir=data_dir)))

    # defaults to ~/OpenWorker
    assert client.get("/v1/settings").json()["scratch_base"] == "~/OpenWorker"

    base = tmp_path / "my coworker files"
    resp = client.post("/v1/settings/scratch-base", json={"path": str(base)}).json()
    assert resp["ok"] is True and resp["scratch_base"] == str(base)
    assert base.is_dir()  # created on set
    assert (
        client.post("/v1/settings/scratch-base", json={"path": " "}).json()["ok"]
        is False
    )

    # persists across a restart and actually drives where scratch dirs are provisioned
    reborn = SessionManager(data_dir=data_dir)
    assert reborn.get_settings()["scratch_base"] == str(base)
    scratch = reborn._provision_scratch("sess-xyz")
    assert Path(scratch) == (base / "sess-xyz").resolve() and Path(scratch).is_dir()


def test_ollama_models_gated_on_liveness(tmp_path, monkeypatch):
    """`ollama:*` entries show only while a local Ollama answers — keyless must not mean
    always-present (a stray ollama:<junk> pref would otherwise render forever)."""
    from coworker.server.manager import SessionManager

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.add_model("ollama:llama3.3")

    monkeypatch.setattr(SessionManager, "_ollama_alive", lambda self: False)
    assert "ollama:llama3.3" not in manager.get_settings()["models"]

    monkeypatch.setattr(SessionManager, "_ollama_alive", lambda self: True)
    assert "ollama:llama3.3" in manager.get_settings()["models"]


def test_lmstudio_models_gated_on_liveness(tmp_path, monkeypatch):
    """Same rule as Ollama: keyless must not mean always-present. LM Studio's server is OFF by
    default, so an unstarted one would otherwise leave dead entries in the picker."""
    from coworker.server.manager import SessionManager

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.add_model("lmstudio:qwen/qwen3.5-9b")

    monkeypatch.setattr(SessionManager, "_lmstudio_alive", lambda self: False)
    assert "lmstudio:qwen/qwen3.5-9b" not in manager.get_settings()["models"]

    monkeypatch.setattr(SessionManager, "_lmstudio_alive", lambda self: True)
    assert "lmstudio:qwen/qwen3.5-9b" in manager.get_settings()["models"]


def _fake_json_get(monkeypatch, routes: dict):
    """Route httpx.get by URL suffix; anything unrouted raises (as an unreachable host would)."""
    from types import SimpleNamespace

    def fake_get(url, **_kw):
        for suffix, payload in routes.items():
            if url.endswith(suffix):
                return SimpleNamespace(status_code=200, json=lambda p=payload: p)
        raise ConnectionError(url)

    monkeypatch.setattr("httpx.get", fake_get)


def test_lmstudio_discovery_drops_embedding_models(tmp_path, monkeypatch):
    """`/v1/models` lists embedders alongside chat models with nothing in the id to tell them
    apart, so discovery prefers LM Studio's typed `/api/v0/models`. Shapes captured from a live
    LM Studio 0.3.x on 2026-07-26."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker.server.manager import SessionManager

    manager = SessionManager(data_dir=tmp_path / "data")
    manager.secrets.put("provider:lmstudio", {"base_url": "http://localhost:1234"})
    _fake_json_get(
        monkeypatch,
        {
            "/api/v0/models": {
                "data": [
                    {"id": "qwen/qwen3.5-9b", "type": "vlm"},
                    {"id": "google/gemma-3-4b", "type": "vlm"},
                    {"id": "text-embedding-nomic-embed-text-v1.5", "type": "embeddings"},
                ]
            }
        },
    )
    assert manager._lmstudio_models() == [
        "lmstudio:qwen/qwen3.5-9b",
        "lmstudio:google/gemma-3-4b",
    ]
    # bare ids for the datalist — the namespacing slash survives, only the prefix goes
    assert manager._suggested_models("lmstudio") == [
        "qwen/qwen3.5-9b",
        "google/gemma-3-4b",
    ]


def test_lmstudio_discovery_falls_back_to_v1(tmp_path, monkeypatch):
    """`/api/v0` is beta. An older build (or a renamed path) must degrade to the stable
    OpenAI-compatible list rather than reporting no models at all."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker.server.manager import SessionManager

    manager = SessionManager(data_dir=tmp_path / "data")
    manager.secrets.put("provider:lmstudio", {"base_url": "http://localhost:1234"})
    _fake_json_get(  # only /v1/models is routed; /api/v0/models raises
        monkeypatch, {"/v1/models": {"data": [{"id": "qwen/qwen3.5-9b"}]}}
    )
    assert manager._lmstudio_models() == ["lmstudio:qwen/qwen3.5-9b"]


def test_lmstudio_discovery_silent_when_unconfigured(tmp_path, monkeypatch):
    """No profile → no probe at all. get_providers() runs on every Settings fetch; a blocking
    HTTP call per unconfigured local provider would tax every user who has neither."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker.server.manager import SessionManager

    manager = SessionManager(data_dir=tmp_path / "data")

    def boom(*_a, **_k):
        raise AssertionError("probed an unconfigured local provider")

    monkeypatch.setattr("httpx.get", boom)
    assert manager._lmstudio_models() == []
    assert manager._ollama_models() == []


@pytest.mark.parametrize(
    "name,default_url",
    [("ollama", "http://localhost:11434"), ("lmstudio", "http://localhost:1234")],
)
def test_connecting_a_local_runtime_on_its_default_endpoint_enables_discovery(
    tmp_path, monkeypatch, name, default_url
):
    """Connecting with the default endpoint stores an EMPTY profile — there is no required
    field to fill in. Discovery must read that as connected (absent vs empty), or the user
    gets a green ✓ and an empty picker."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker.server.manager import SessionManager

    manager = SessionManager(data_dir=tmp_path / "data")
    probed: list[str] = []
    monkeypatch.setattr(
        "httpx.get", lambda url, **_k: probed.append(url) or (_ for _ in ()).throw(ConnectionError())
    )

    assert manager._suggested_models(name) == [] and not probed  # unconnected → no probe

    assert manager.set_provider(name, {})["ok"] is True
    assert manager.secrets.get(f"provider:{name}") == {}  # nothing to store, but present
    manager._suggested_models(name)
    assert probed and probed[0].startswith(default_url), (
        f"connected {name} must be probed at its default endpoint, got {probed}"
    )


def test_connecting_a_local_runtime_selects_a_model_it_actually_has(
    tmp_path, monkeypatch
):
    """Local runtimes serve whatever the user downloaded, so the named recommendation usually
    misses. Connecting one must still leave a usable default — otherwise a perfectly good
    local server still reads as "No model"."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    from coworker.server.manager import SessionManager

    monkeypatch.setattr(SessionManager, "_lmstudio_alive", lambda self: True)
    manager = SessionManager(data_dir=tmp_path / "data")
    assert manager.model == "gpt-5.6-sol"  # fresh install, nothing configured

    # The descriptor recommends qwen3-coder-30b; this machine has neither of those.
    monkeypatch.setattr(
        manager,
        "_suggested_models",
        lambda name: ["qwen/qwen3.5-9b", "google/gemma-3-4b"]
        if name == "lmstudio"
        else [],
    )
    res = manager.set_provider("lmstudio", {"base_url": "http://localhost:1234"})
    assert res["ok"] and res["recommended_model"] == "qwen/qwen3.5-9b"
    assert manager.model == "lmstudio:qwen/qwen3.5-9b"
    assert "lmstudio:qwen/qwen3.5-9b" in manager.get_settings()["models"]
