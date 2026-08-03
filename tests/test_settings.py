"""Tests for the model API-key settings path (Tauri desktop Phase 2).

A Tauri-launched sidecar doesn't inherit the shell env, so the key may live only in the
SecretStore. These cover: the env→store resolver, the status shape (never leaks the key),
and the REST round-trip. No network, no model calls.
"""

from __future__ import annotations

from pathlib import Path

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

    monkeypatch.setattr(SessionManager, "_local_alive", lambda self, name: False)
    assert "ollama:llama3.3" not in manager.get_settings()["models"]

    monkeypatch.setattr(SessionManager, "_local_alive", lambda self, name: True)
    assert "ollama:llama3.3" in manager.get_settings()["models"]


def test_lmstudio_models_gated_on_liveness(tmp_path, monkeypatch):
    """Same gate for `lmstudio:*` — and per-provider: only the answering server's models
    render (one local server being up must not surface the other's entries)."""
    from coworker.server.manager import SessionManager

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.add_model("lmstudio:qwen/qwen3-coder-30b")
    manager.add_model("ollama:llama3.3")

    monkeypatch.setattr(SessionManager, "_local_alive", lambda self, name: False)
    assert "lmstudio:qwen/qwen3-coder-30b" not in manager.get_settings()["models"]

    monkeypatch.setattr(
        SessionManager, "_local_alive", lambda self, name: name == "lmstudio"
    )
    models = manager.get_settings()["models"]
    assert "lmstudio:qwen/qwen3-coder-30b" in models
    assert "ollama:llama3.3" not in models  # the other server is still down


class _LocalResp:
    """Minimal httpx.Response stand-in for the local-server probes (json() raises on a
    None body — a 200 that isn't JSON, e.g. some other service's HTML page)."""

    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


def test_local_models_parses_both_server_shapes(tmp_path, monkeypatch):
    """Ollama's native /api/tags and LM Studio's OpenAI-shaped /v1/models both map to
    `<provider>:<id>` picker entries."""
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.set_provider("ollama", {"base_url": "http://localhost:11434"})
    manager.set_provider("lmstudio", {"base_url": "http://localhost:1234"})

    payloads = {
        "http://localhost:11434/api/tags": {"models": [{"name": "llama3.3"}]},
        "http://localhost:1234/v1/models": {
            "data": [{"id": "qwen/qwen3-coder-30b", "object": "model"}]
        },
    }

    import httpx

    monkeypatch.setattr(
        httpx, "get", lambda url, timeout=None: _LocalResp(200, payloads[url])
    )
    assert manager._local_models("ollama") == ["ollama:llama3.3"]
    assert manager._local_models("lmstudio") == ["lmstudio:qwen/qwen3-coder-30b"]


def test_local_alive_requires_model_server_shape(tmp_path, monkeypatch):
    """Liveness needs the provider's expected list container, not just a 200 — another
    service answering on the port must not mark a local provider alive."""
    import httpx

    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")

    seen: list[str] = []

    def openai_shape(url, timeout=None):
        seen.append(url)
        return _LocalResp(200, {"data": []})

    monkeypatch.setattr(httpx, "get", openai_shape)
    assert manager._local_alive("lmstudio") is True  # empty list still counts
    assert seen[-1] == "http://localhost:1234/v1/models"
    assert manager._local_alive("ollama") is False  # /api/tags wants {"models": …}
    assert seen[-1] == "http://localhost:11434/api/tags"

    manager._refresh_provider()  # drop the liveness cache before re-probing
    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: _LocalResp(200, None))
    assert manager._local_alive("lmstudio") is False  # 200 but not JSON


def test_local_liveness_cache_invalidated_on_config_change(tmp_path, monkeypatch):
    """Repointing a local server re-probes immediately instead of serving the previous
    endpoint's liveness for up to 30s."""
    import httpx

    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")

    monkeypatch.setattr(
        httpx, "get", lambda url, timeout=None: _LocalResp(200, {"data": []})
    )
    assert manager._local_alive("lmstudio") is True

    def down(url, timeout=None):
        raise ConnectionError("down")

    monkeypatch.setattr(httpx, "get", down)
    assert manager._local_alive("lmstudio") is True  # cached — no re-probe yet
    manager.set_provider("lmstudio", {"base_url": "http://localhost:4321"})
    assert manager._local_alive("lmstudio") is False  # cache dropped → fresh probe


def test_model_ready_gated_on_local_liveness(tmp_path, monkeypatch):
    """A dead local server must not report the default model as ready — the composer's
    "No model connected" chip relies on model_ready telling the truth."""
    from coworker.server.manager import SessionManager

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.set_default_model("lmstudio:qwen/qwen3-coder-30b")

    monkeypatch.setattr(SessionManager, "_local_alive", lambda self, name: False)
    assert manager.get_settings()["model_ready"] is False

    monkeypatch.setattr(SessionManager, "_local_alive", lambda self, name: True)
    assert manager.get_settings()["model_ready"] is True


def test_first_keyless_detect_stores_profile_and_recommends(tmp_path, monkeypatch):
    """The GUI persists keyless providers on every passing Detect, possibly with all-blank
    fields. The stored EMPTY profile still counts as engaged: the live list probes the
    default endpoint, so the recommended model auto-adds and wins the unset default."""
    import httpx

    from coworker.server.manager import SessionManager

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")

    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, timeout=None: _LocalResp(
            200, {"data": [{"id": "qwen/qwen3-coder-30b"}]}
        ),
    )
    res = manager.set_provider("lmstudio", {"base_url": ""})  # Detect at the default URL
    assert res["ok"] is True
    assert manager.secrets.get("provider:lmstudio") == {}  # engaged, no overrides
    assert "lmstudio:qwen/qwen3-coder-30b" in manager.get_settings()["models"]
    assert manager.model == "lmstudio:qwen/qwen3-coder-30b"  # fresh install → wins default


def test_verify_provider_explicit_blank_endpoint_means_default(tmp_path, monkeypatch):
    """Clearing a stored endpoint in the form verifies the DEFAULT, not the old URL — a
    passing Test must validate the config that would actually be saved."""
    from coworker.server import manager as manager_mod
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.set_provider("lmstudio", {"base_url": "http://old-box:1234"})

    cap: dict = {}

    def fake_verify(name, *, api_key=None, base_url=None, fields=None, timeout=10.0):
        cap.update({"name": name, "base_url": base_url})
        return {"ok": True}

    monkeypatch.setattr(manager_mod, "verify_provider_key", fake_verify)
    manager.verify_provider("lmstudio", {"base_url": ""})  # explicitly cleared
    assert cap["base_url"] == ""  # NOT the stored http://old-box:1234
    manager.verify_provider("lmstudio", {})  # field omitted → stored value stands
    assert cap["base_url"] == "http://old-box:1234"
