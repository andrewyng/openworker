"""End-to-end verification for Issue #455 — multi-endpoint Ollama.

Exercises persistence across manager restart, endpoint switching with recorded probes,
disabled-endpoint isolation, invalid inputs, HTTPS/local-IP URLs, client rebuild, and
legacy single-URL secrets on disk. Uses httpx mocks only (no paid APIs; no live Ollama).
"""

from __future__ import annotations

from pathlib import Path

from coworker.providers import ollama_endpoints as ep
from coworker.providers.registry import build_provider_client
from coworker.providers.router import ProviderRouter


def _mgr(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker.server.manager import SessionManager

    return SessionManager(data_dir=tmp_path)


class _TagsResp:
    def __init__(self, names: list[str], status: int = 200):
        self._names = names
        self.status_code = status

    def json(self):
        return {"models": [{"name": n} for n in self._names]}


# -- 9. Full endpoint management lifecycle + persistence -----------------------
def test_lifecycle_add_edit_disable_enable_delete_persists(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)

    added = mgr.add_ollama_endpoint(
        label="My MacBook", base_url="http://192.168.1.10:11434", select=True
    )
    assert added["ok"]
    eid = added["endpoints"][0]["id"]
    assert added["endpoints"][0]["label"] == "My MacBook"
    assert added["selected_endpoint_id"] == eid

    # Edit nickname + URL (HTTPS API-style path host).
    edited = mgr.update_ollama_endpoint(
        eid, label="Workstation", base_url="https://ollama.example.com"
    )
    assert edited["ok"]
    assert edited["endpoints"][0]["label"] == "Workstation"
    assert edited["endpoints"][0]["base_url"] == "https://ollama.example.com"
    assert edited["values"]["base_url"] == "https://ollama.example.com"

    # Disable — must not remain the active probe target when another enabled exists.
    other = mgr.add_ollama_endpoint(
        label="GPU", base_url="http://10.0.0.5:11434", select=False
    )
    oid = next(e["id"] for e in other["endpoints"] if e["label"] == "GPU")
    mgr.select_ollama_endpoint(eid)
    disabled = mgr.update_ollama_endpoint(eid, enabled=False)
    assert disabled["ok"]
    row = next(e for e in disabled["endpoints"] if e["id"] == eid)
    assert row["enabled"] is False
    assert disabled["selected_endpoint_id"] == oid  # jumped to enabled peer
    assert disabled["values"]["base_url"] == "http://10.0.0.5:11434"

    # Re-enable + select.
    mgr.update_ollama_endpoint(eid, enabled=True)
    sel = mgr.select_ollama_endpoint(eid)
    assert sel["selected_endpoint_id"] == eid

    # Delete and confirm gone.
    deleted = mgr.delete_ollama_endpoint(eid)
    assert deleted["ok"]
    assert all(e["id"] != eid for e in deleted["endpoints"])

    # Restart: new SessionManager on same data_dir must reload remaining endpoint.
    mgr2 = _mgr(tmp_path, monkeypatch)
    ollama = next(p for p in mgr2.get_providers() if p["name"] == "ollama")
    assert len(ollama["endpoints"]) == 1
    assert ollama["endpoints"][0]["label"] == "GPU"
    assert ollama["endpoints"][0]["base_url"] == "http://10.0.0.5:11434"
    assert ollama["selected_endpoint_id"] == oid
    assert ollama["values"]["base_url"] == "http://10.0.0.5:11434"

    # Secrets file itself contains the multi-endpoint shape (not only mirrored base_url).
    secrets_files = list(Path(tmp_path).rglob("*.json"))
    blob = "\n".join(p.read_text(encoding="utf-8") for p in secrets_files)
    assert "10.0.0.5:11434" in blob
    assert "endpoints" in blob


# -- 10. Endpoint switching + model isolation ----------------------------------
def test_switch_endpoints_probes_only_selected_and_rebuilds_client(
    tmp_path, monkeypatch
):
    mgr = _mgr(tmp_path, monkeypatch)
    mgr.add_ollama_endpoint(
        label="Endpoint A", base_url="http://192.168.1.20:11434", select=True
    )
    mgr.add_ollama_endpoint(
        label="Endpoint B", base_url="http://10.0.0.8:11434", select=False
    )
    ollama = next(p for p in mgr.get_providers() if p["name"] == "ollama")
    a_id = next(e["id"] for e in ollama["endpoints"] if e["label"] == "Endpoint A")
    b_id = next(e["id"] for e in ollama["endpoints"] if e["label"] == "Endpoint B")
    assert ollama["selected_endpoint_id"] == a_id

    probed: list[str] = []

    def fake_get(url, timeout=0):
        probed.append(url)
        if "192.168.1.20" in url:
            return _TagsResp(["alpha-model"])
        if "10.0.0.8" in url:
            return _TagsResp(["beta-model"])
        raise AssertionError(f"unexpected probe: {url}")

    monkeypatch.setattr("httpx.get", fake_get)

    models_a = mgr._ollama_models()
    assert models_a == ["ollama:alpha-model"]
    assert any("192.168.1.20" in u for u in probed)
    assert not any("10.0.0.8" in u for u in probed)
    sugg = mgr._suggested_models("ollama")
    assert sugg == ["alpha-model"]
    assert "beta-model" not in sugg

    # Switch to B — probes and suggestions must flip; no A models mixed in.
    mgr.select_ollama_endpoint(b_id)
    probed.clear()
    models_b = mgr._ollama_models()
    assert models_b == ["ollama:beta-model"]
    assert any("10.0.0.8" in u for u in probed)
    assert not any("192.168.1.20" in u for u in probed)
    assert mgr._suggested_models("ollama") == ["beta-model"]

    # ProviderRouter must rebuild the cached client against B's /v1 URL.
    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def complete(self, **kwargs):
            return None

    monkeypatch.setattr(
        "coworker.providers.registry.OpenAIProvider", _FakeClient
    )
    # Invalidate so next route rebuilds (select already did; force again after patch).
    assert isinstance(mgr.provider, ProviderRouter)
    mgr.provider.invalidate("ollama")
    mgr.provider._client_for("ollama:beta-model")
    assert captured["base_url"] == "http://10.0.0.8:11434/v1"


# -- 11. Unavailable / invalid endpoints ---------------------------------------
def test_unreachable_and_disabled_never_crash(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    mgr.add_ollama_endpoint(
        label="Down", base_url="http://203.0.113.9:11434", select=True
    )

    def boom(*_a, **_k):
        raise OSError("network unreachable")

    monkeypatch.setattr("httpx.get", boom)
    assert mgr._ollama_models() == []
    assert mgr._ollama_alive() is False
    # verify_provider must not raise either.
    res = mgr.verify_provider("ollama", {"base_url": "http://203.0.113.9:11434"})
    assert res["ok"] is False
    assert "error" in res

    # Disabled selected (sole endpoint): no probes, empty models.
    ollama = next(p for p in mgr.get_providers() if p["name"] == "ollama")
    eid = ollama["endpoints"][0]["id"]
    mgr.update_ollama_endpoint(eid, enabled=False)
    probed: list[str] = []

    def track(url, timeout=0):
        probed.append(url)
        return _TagsResp(["should-not-see"])

    monkeypatch.setattr("httpx.get", track)
    assert mgr._ollama_models() == []
    assert mgr._ollama_alive() is False
    assert probed == []


def test_all_disabled_does_not_probe_localhost(tmp_path, monkeypatch):
    """Regression: disabling every endpoint must not fall back to DEFAULT localhost."""
    mgr = _mgr(tmp_path, monkeypatch)
    added = mgr.add_ollama_endpoint(
        label="Only", base_url="http://203.0.113.50:11434", select=True
    )
    eid = added["endpoints"][0]["id"]
    disabled = mgr.update_ollama_endpoint(eid, enabled=False)
    assert disabled["selected_endpoint_id"] in (None, "")
    assert "base_url" not in (disabled.get("values") or {})

    probed: list[str] = []

    def track(url, timeout=0):
        probed.append(url)
        return _TagsResp(["leak"])

    monkeypatch.setattr("httpx.get", track)
    assert mgr._ollama_models() == []
    assert mgr._ollama_alive() is False
    assert probed == []
    assert not any("localhost" in u or "127.0.0.1" in u for u in probed)


# -- 12. Invalid inputs --------------------------------------------------------
def test_invalid_inputs_matrix():
    cases = [
        ("", "http://localhost:11434", "Nickname"),
        ("   ", "http://localhost:11434", "Nickname"),
        ("Box", "", "URL"),
        ("Box", "not-a-url", "http"),
        ("Box", "ftp://localhost:11434", "http"),
        ("Box", "localhost:11434", "http"),  # missing scheme
        ("Box", "http://", "host"),
        ("Box", "https://user:secret@host:11434", "username"),
    ]
    for label, url, needle in cases:
        _, err = ep.add_endpoint({}, label=label, base_url=url)
        assert err and err["ok"] is False, (label, url)
        assert needle.lower() in err["error"].lower() or (
            needle == "URL" and "required" in err["error"].lower()
        ), err

    # Duplicate URL (incl. /v1 variant).
    profile, err = ep.add_endpoint(
        {}, label="A", base_url="http://127.0.0.1:11434"
    )
    assert err is None
    _, err = ep.add_endpoint(
        profile, label="B", base_url="http://127.0.0.1:11434/v1/"
    )
    assert err and "already exists" in err["error"]

    # Valid local IP + port, HTTPS API URL, and host without explicit port are accepted.
    for label, url in [
        ("LAN", "http://192.168.1.20:11434"),
        ("Remote", "https://example.com/ollama"),
        ("DefaultPort", "http://ollama.local"),
    ]:
        profile, err = ep.add_endpoint({}, label=label, base_url=url)
        assert err is None, (label, url, err)
        assert profile["endpoints"][0]["base_url"] == ep.normalize_endpoint_url(url)


# -- 13. Backward compatibility ------------------------------------------------
def test_legacy_single_url_secret_survives_restart(tmp_path, monkeypatch):
    """Simulate a pre-#455 secrets profile with only base_url — no endpoints key."""
    mgr = _mgr(tmp_path, monkeypatch)
    # Write legacy shape directly into the secret store (bypass new APIs).
    mgr.secrets.put("provider:ollama", {"base_url": "http://127.0.0.1:11434"})

    # Fresh manager reads legacy profile without losing the URL.
    mgr2 = _mgr(tmp_path, monkeypatch)
    ollama = next(p for p in mgr2.get_providers() if p["name"] == "ollama")
    assert ollama["values"]["base_url"] == "http://127.0.0.1:11434"
    assert len(ollama["endpoints"]) == 1
    assert ollama["endpoints"][0]["label"] == "Default"
    assert ollama["endpoints"][0]["base_url"] == "http://127.0.0.1:11434"

    # Client still builds against the legacy URL.
    captured: dict = {}

    class _Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("coworker.providers.registry.OpenAIProvider", _Fake)
    build_provider_client(
        "ollama", mgr2.secrets.get("provider:ollama") or {}, None
    )
    assert captured["base_url"] == "http://127.0.0.1:11434/v1"

    # Legacy set_provider({base_url}) still works and keeps a single endpoint.
    assert mgr2.set_provider("ollama", {"base_url": "http://10.1.1.1:11434"})["ok"]
    ollama = next(p for p in mgr2.get_providers() if p["name"] == "ollama")
    assert ollama["values"]["base_url"] == "http://10.1.1.1:11434"
    assert len(ollama["endpoints"]) == 1


# -- HTTP API surface (connected, not dead) ------------------------------------
def test_http_api_full_flow(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server import SessionManager, create_app

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    mgr = SessionManager(data_dir=tmp_path)
    client = TestClient(create_app(mgr))

    r = client.post(
        "/v1/providers/ollama/endpoints",
        json={"label": "LAN", "base_url": "http://192.168.0.2:11434"},
    ).json()
    assert r["ok"]
    eid = r["endpoints"][0]["id"]

    r = client.post(
        "/v1/providers/ollama/endpoints",
        json={"label": "HTTPS", "base_url": "https://example.com/ollama", "select": False},
    ).json()
    assert r["ok"] and len(r["endpoints"]) == 2
    other = next(e["id"] for e in r["endpoints"] if e["id"] != eid)

    r = client.post(f"/v1/providers/ollama/endpoints/{other}/select").json()
    assert r["selected_endpoint_id"] == other
    assert r["values"]["base_url"] == "https://example.com/ollama"

    r = client.patch(
        f"/v1/providers/ollama/endpoints/{eid}",
        json={"enabled": False},
    ).json()
    assert next(e for e in r["endpoints"] if e["id"] == eid)["enabled"] is False

    # Providers list exposes endpoints to the GUI.
    providers = {p["name"]: p for p in client.get("/v1/providers").json()}
    assert "endpoints" in providers["ollama"]
    assert providers["ollama"]["selected_endpoint_id"] == other

    r = client.delete(f"/v1/providers/ollama/endpoints/{eid}").json()
    assert all(e["id"] != eid for e in r["endpoints"])

    # Invalid inputs via API.
    bad = client.post(
        "/v1/providers/ollama/endpoints",
        json={"label": "", "base_url": "http://x"},
    ).json()
    assert bad["ok"] is False
