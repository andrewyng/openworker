"""Tests for multi-endpoint Ollama configuration: CRUD, selection, migration, validation,
model probing from the selected endpoint, and unreachable hosts."""

from __future__ import annotations

from coworker.providers import ollama_endpoints as ep
from coworker.providers.registry import DEFAULT_OLLAMA_URL, build_provider_client


# -- pure helpers ---------------------------------------------------------------
def test_normalize_and_validate_url():
    assert ep.normalize_endpoint_url("http://h:1/v1/") == "http://h:1"
    assert ep.normalize_endpoint_url(" http://localhost:11434 ") == "http://localhost:11434"
    assert ep.validate_endpoint_url("") == "Endpoint URL is required."
    assert ep.validate_endpoint_url("ftp://x") is not None
    assert ep.validate_endpoint_url("http://") is not None
    assert ep.validate_endpoint_url("http://192.168.1.20:11434") is None
    assert ep.validate_label("  ") is not None
    assert ep.validate_label("My MacBook") is None


def test_migrate_legacy_single_base_url():
    migrated = ep.migrate_profile({"base_url": "http://box:11434"})
    assert len(migrated["endpoints"]) == 1
    assert migrated["endpoints"][0]["base_url"] == "http://box:11434"
    assert migrated["endpoints"][0]["label"] == "Default"
    assert migrated["selected_endpoint_id"] == migrated["endpoints"][0]["id"]
    assert migrated["base_url"] == "http://box:11434"


def test_migrate_empty_profile():
    migrated = ep.migrate_profile({})
    assert migrated["endpoints"] == []
    assert not migrated.get("selected_endpoint_id")
    assert ep.selected_base_url({}) == DEFAULT_OLLAMA_URL


def test_add_edit_delete_select_and_duplicates():
    profile, err = ep.add_endpoint(
        {}, label="MacBook", base_url="http://localhost:11434", select=True
    )
    assert err is None
    assert profile["base_url"] == "http://localhost:11434"
    first = profile["endpoints"][0]["id"]

    profile, err = ep.add_endpoint(
        profile, label="GPU", base_url="http://192.168.1.20:11434", select=False
    )
    assert err is None
    assert len(profile["endpoints"]) == 2
    assert profile["selected_endpoint_id"] == first

    # Duplicate URL rejected (with /v1 suffix normalized away).
    _, err = ep.add_endpoint(
        profile, label="Dup", base_url="http://localhost:11434/v1"
    )
    assert err and "already exists" in err["error"]

    second = profile["endpoints"][1]["id"]
    profile, err = ep.select_endpoint(profile, second)
    assert err is None
    assert profile["base_url"] == "http://192.168.1.20:11434"

    profile, err = ep.update_endpoint(
        profile, second, label="Workstation", base_url="http://192.168.1.20:11434"
    )
    assert err is None
    assert profile["endpoints"][1]["label"] == "Workstation"

    profile, err = ep.update_endpoint(profile, second, enabled=False)
    assert err is None
    # Disabling the selected endpoint moves selection to another enabled one.
    assert profile["selected_endpoint_id"] == first

    _, err = ep.select_endpoint(profile, second)
    assert err and "Enable" in err["error"]

    profile, err = ep.delete_endpoint(profile, first)
    assert err is None
    assert len(profile["endpoints"]) == 1
    assert profile["endpoints"][0]["id"] == second


def test_upsert_from_base_url_legacy_blur_save():
    profile, err = ep.upsert_from_base_url({}, "http://127.0.0.1:9999")
    assert err is None
    assert len(profile["endpoints"]) == 1
    assert profile["base_url"] == "http://127.0.0.1:9999"

    profile, err = ep.upsert_from_base_url(profile, "http://127.0.0.1:8888")
    assert err is None
    assert len(profile["endpoints"]) == 1  # updates selected, doesn't spawn another
    assert profile["base_url"] == "http://127.0.0.1:8888"


def test_build_ollama_uses_selected_endpoint(monkeypatch):
    captured = {}

    class _Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "coworker.providers.registry.OpenAIProvider", _Fake
    )
    profile, _ = ep.add_endpoint(
        {}, label="A", base_url="http://a:11434", select=True
    )
    profile, _ = ep.add_endpoint(
        profile, label="B", base_url="http://b:11434", select=True
    )
    build_provider_client("ollama", profile, secrets=None)
    assert captured["base_url"] == "http://b:11434/v1"
    assert captured["api_key"] == "ollama"


# -- SessionManager integration -------------------------------------------------
def test_manager_endpoint_crud_and_compat(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)

    # Backward compat: single base_url write migrates into endpoints.
    res = mgr.set_provider("ollama", {"base_url": "http://localhost:9999"})
    assert res["ok"]
    provs = {p["name"]: p for p in mgr.get_providers()}
    ollama = provs["ollama"]
    assert ollama["values"]["base_url"] == "http://localhost:9999"
    assert len(ollama["endpoints"]) == 1
    assert ollama["selected_endpoint_id"] == ollama["endpoints"][0]["id"]

    added = mgr.add_ollama_endpoint(
        label="GPU Server", base_url="http://192.168.1.5:11434", select=True
    )
    assert added["ok"]
    assert added["values"]["base_url"] == "http://192.168.1.5:11434"
    assert len(added["endpoints"]) == 2

    first = added["endpoints"][0]["id"]
    selected = mgr.select_ollama_endpoint(first)
    assert selected["ok"]
    assert selected["selected_endpoint_id"] == first
    assert selected["values"]["base_url"] == "http://localhost:9999"

    disabled = mgr.update_ollama_endpoint(first, enabled=False)
    assert disabled["ok"]
    assert disabled["selected_endpoint_id"] != first

    deleted = mgr.delete_ollama_endpoint(added["endpoints"][1]["id"])
    assert deleted["ok"]


def test_manager_models_from_selected_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    mgr.add_ollama_endpoint(label="A", base_url="http://host-a:11434", select=True)
    mgr.add_ollama_endpoint(label="B", base_url="http://host-b:11434", select=False)

    probed: list[str] = []

    class _Resp:
        def __init__(self, models):
            self._models = models

        def json(self):
            return {"models": [{"name": n} for n in self._models]}

        @property
        def status_code(self):
            return 200

    def fake_get(url, timeout=0):
        probed.append(url)
        if "host-a" in url:
            return _Resp(["alpha"])
        if "host-b" in url:
            return _Resp(["beta"])
        raise AssertionError(url)

    monkeypatch.setattr("httpx.get", fake_get)
    # Bypass the 30s alive cache path used by get_settings; test _ollama_models directly.
    models = mgr._ollama_models()
    assert models == ["ollama:alpha"]
    assert any("host-a" in u for u in probed)
    assert not any("host-b" in u for u in probed)

    ollama = next(p for p in mgr.get_providers() if p["name"] == "ollama")
    b_id = next(e["id"] for e in ollama["endpoints"] if e["label"] == "B")
    mgr.select_ollama_endpoint(b_id)
    probed.clear()
    models = mgr._ollama_models()
    assert models == ["ollama:beta"]


def test_manager_unreachable_endpoint_returns_empty_models(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    mgr.add_ollama_endpoint(label="Down", base_url="http://127.0.0.1:9", select=True)

    def boom(*_a, **_k):
        raise ConnectionError("refused")

    monkeypatch.setattr("httpx.get", boom)
    assert mgr._ollama_models() == []
    assert mgr._ollama_alive() is False


def test_manager_validation_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    assert mgr.add_ollama_endpoint(label="", base_url="http://x")["ok"] is False
    assert mgr.add_ollama_endpoint(label="X", base_url="not-a-url")["ok"] is False
    assert mgr.select_ollama_endpoint("missing")["ok"] is False


def test_server_ollama_endpoint_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from fastapi.testclient import TestClient

    from coworker.server import SessionManager, create_app

    mgr = SessionManager(data_dir=tmp_path)
    client = TestClient(create_app(mgr))

    res = client.post(
        "/v1/providers/ollama/endpoints",
        json={"label": "Local", "base_url": "http://127.0.0.1:11434"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] and len(body["endpoints"]) == 1
    eid = body["endpoints"][0]["id"]

    res = client.patch(
        f"/v1/providers/ollama/endpoints/{eid}",
        json={"label": "Renamed", "enabled": True},
    )
    assert res.json()["endpoints"][0]["label"] == "Renamed"

    res = client.post(f"/v1/providers/ollama/endpoints/{eid}/select")
    assert res.json()["selected_endpoint_id"] == eid

    # Invalid add
    bad = client.post(
        "/v1/providers/ollama/endpoints",
        json={"label": "Dup", "base_url": "http://127.0.0.1:11434"},
    )
    assert bad.json()["ok"] is False

    res = client.delete(f"/v1/providers/ollama/endpoints/{eid}")
    assert res.json()["ok"] and res.json()["endpoints"] == []
