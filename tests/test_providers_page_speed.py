"""T3-2 / OPE-250: Models & Keys waited up to a minute on local model servers that are not
running. On Windows a refused loopback connection takes about 2 s, `localhost` tries two
addresses, the servers were asked one after another, each probe built its own HTTP client
(about 2 s more, for the certificate store a plain http request never uses), and a server
that did not answer was asked a second time for its models. Now the connect wait is short on
loopback, the servers are asked at the same time, every probe shares one client, a dead
server is not asked for its models, the defaults name 127.0.0.1, and every answer stays in
the caches."""

from __future__ import annotations

import threading
import time

import httpx

from coworker.providers import local_server, ollama_facts, probe_http
from coworker.server import SessionManager


def test_a_loopback_server_gets_a_short_connect_wait_and_others_the_whole_budget():
    for url in ("http://localhost:8080/v1", "http://127.0.0.1:11434", "http://[::1]:8000/v1"):
        t = local_server.probe_timeout(url, 4.0)
        assert t.connect == local_server.LOOPBACK_CONNECT_SECONDS and t.read == 4.0, url
    far = local_server.probe_timeout("http://gpu-box.lan:8000/v1", 4.0)
    assert far.connect == 4.0 and far.read == 4.0
    assert local_server.probe_timeout("not a url", 0.8).connect == 0.8


def test_the_local_servers_are_asked_at_once_and_every_answer_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    names = list(local_server.LOCAL_SERVERS)
    running = {"now": 0, "most": 0}
    lock = threading.Lock()

    def slow(*_a, **_k):
        with lock:
            running["now"] += 1
            running["most"] = max(running["most"], running["now"])
        time.sleep(0.2)
        with lock:
            running["now"] -= 1
        return False

    alive_calls: list[str] = []

    def alive(name, base_url, api_key=None):
        alive_calls.append(name)
        return slow()

    monkeypatch.setattr(local_server, "alive", alive)
    monkeypatch.setattr(local_server, "model_facts", lambda *a, **k: slow() or [])
    monkeypatch.setattr(manager, "_ollama_alive", lambda: False)
    monkeypatch.setattr(manager, "_ollama_models", lambda: [])
    started = time.monotonic()
    manager._warm_local_probes(["ollama", *names])
    took = time.monotonic() - started
    assert running["most"] > 1  # asked at the same time
    assert took < 0.2 * 2 * len(names) / 2  # far from one after another
    # every server's answer was kept, so get_providers asks none of them again
    assert sorted(alive_calls) == sorted(names)
    assert set(manager._local_alive_cache) == set(names)
    alive_calls.clear()
    rows = {r["name"]: r for r in manager.get_providers()}
    assert alive_calls == []
    assert all(rows[n]["alive"] is False for n in names if n in rows)
    if hasattr(manager, "observability"):
        manager.observability.close()


def test_every_probe_goes_through_the_one_shared_client(tmp_path, monkeypatch):
    """`httpx.get` builds a client per call (about 2 s on Windows: it loads the certificate
    store). The liveness and model probes of Ollama and the OpenAI-compatible servers all
    use probe_http's shared client, so a fake there answers every one of them and a probe
    that built its own client would fail this test."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        if request.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": "served-model"}]})
        return httpx.Response(404)

    def own_client(*_a, **_k):
        raise AssertionError("a probe built its own HTTP client")

    monkeypatch.setattr(httpx, "get", own_client)
    monkeypatch.setattr(httpx, "post", own_client)
    monkeypatch.setattr(probe_http, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    ollama_facts.forget_all()
    local_server.forget_all()

    assert local_server.alive("vllm", None) is True
    assert [r["name"] for r in local_server.model_facts("vllm", None)] == ["served-model"]
    assert [r["name"] for r in ollama_facts.model_facts(None)] == ["qwen3:8b"]
    manager = SessionManager(workspace=tmp_path)
    assert manager._ollama_alive() is True
    assert "/api/tags" in asked and "/api/show" in asked
    if hasattr(manager, "observability"):
        manager.observability.close()


def test_a_server_that_does_not_answer_is_not_asked_for_its_models(tmp_path, monkeypatch):
    """Only a server that is up is asked what it serves; the others get an empty list
    without a second probe. Both the parallel warm-up and the get_providers rows obey it."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    listed: list[str] = []

    monkeypatch.setattr(local_server, "alive", lambda name, base_url, api_key=None: name == "vllm")

    def facts(name, *_a, **_k):
        listed.append(name)
        return [{"model": f"{name}:served-model", "name": "served-model", "tools": True}]

    monkeypatch.setattr(local_server, "model_facts", facts)
    monkeypatch.setattr(manager, "_ollama_alive", lambda: False)
    monkeypatch.setattr(manager, "_ollama_models", lambda: [])
    rows = {r["name"]: r for r in manager.get_providers()}
    assert set(listed) == {"vllm"}
    assert rows["vllm"]["alive"] is True and rows["vllm"]["suggested_models"] == ["served-model"]
    for name in local_server.LOCAL_SERVERS:
        if name != "vllm":
            assert rows[name]["alive"] is False and rows[name]["suggested_models"] == [], name
    assert rows["ollama"]["alive"] is False and rows["ollama"]["suggested_models"] == []
    if hasattr(manager, "observability"):
        manager.observability.close()


def test_local_server_defaults_name_127_0_0_1_not_localhost():
    """`localhost` resolves to ::1 first on Windows, so a probe of a server that is not
    running waited out two connects; the defaults name the address directly (as OPE-229
    did for Ollama)."""
    for name, spec in local_server.LOCAL_SERVERS.items():
        assert "localhost" not in spec["default_base"], name
