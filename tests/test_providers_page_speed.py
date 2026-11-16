"""T3-2: Models & Keys waited 2-15 s on local model servers that are not running. On Windows a
refused loopback connection takes about 2 s, `localhost` tries two addresses, and the servers
were asked one after another. Now the connect wait is short on loopback, the servers are asked
at the same time, and every answer stays in the caches."""

from __future__ import annotations

import threading
import time

from coworker.providers import local_server
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
