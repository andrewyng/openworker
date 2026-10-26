"""The stored sign-ins are shared by every OpenWorker process on a computer: the app's
server and each `openworker run`. A change must not be lost when two of them write, and a
sign-in must be renewed once: the first process renews, the others read its result."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from coworker import cloud
from coworker.config import Config
from coworker.providers import codex_auth
from coworker.secrets import SecretStore

ROOT = Path(__file__).resolve().parents[1]


def _python(code: str, *args: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen([sys.executable, "-c", textwrap.dedent(code), *args], env=env, stdout=subprocess.PIPE, text=True)


def test_writes_from_several_processes_are_all_kept(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    writer = """
        import sys
        from coworker.secrets import SecretStore
        store = SecretStore(sys.argv[1])
        for i in range(25):
            store.put(f"{sys.argv[2]}:{i}", {"n": i})
    """
    procs = [_python(writer, str(path), name) for name in ("a", "b", "c", "d")]
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    stored = SecretStore(path)
    missing = [f"{name}:{i}" for name in "abcd" for i in range(25) if stored.get(f"{name}:{i}") is None]
    assert missing == []


def test_a_second_process_waits_for_the_first(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    holder = _python(
        """
        import sys, time
        from coworker.secrets import SecretStore
        with SecretStore(sys.argv[1]).exclusive():
            print("held", flush=True)
            time.sleep(1.5)
        """,
        str(path),
    )
    assert holder.stdout.readline().strip() == "held"
    started = time.monotonic()
    with SecretStore(path).exclusive():
        waited = time.monotonic() - started
    assert holder.wait(timeout=30) == 0
    assert waited > 0.8


def test_a_stuck_holder_does_not_stall_a_turn_for_ever(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    holder = _python(
        """
        import sys, time
        from coworker.secrets import SecretStore
        with SecretStore(sys.argv[1]).exclusive():
            print("held", flush=True)
            time.sleep(20)
        """,
        str(path),
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        store = SecretStore(path)
        with store.exclusive(timeout=0.3):
            store.put("openai", {"api_key": "sk-x"})  # goes ahead without the lock
        assert time.monotonic() - started < 5
        assert store.get("openai") == {"api_key": "sk-x"}
    finally:
        holder.kill()


def test_the_lock_is_re_entrant_and_shared_by_stores_on_the_same_file(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    one, two = SecretStore(path), SecretStore(path)
    with one.exclusive():
        with one.exclusive():
            two.put("a", {"n": 1})  # the same thread, another store on the same file
    assert one.get("a") == {"n": 1}


class _Reply:
    def __init__(self, body: dict, status: int = 200) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> dict:
        return self._body


def test_two_callers_renew_a_connector_sign_in_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "secrets.json"
    SecretStore(path).put(cloud.CLOUD_AUTH_PROFILE, {"access_token": "session", "expires": time.time() + 3600})
    SecretStore(path).put(
        "gmail:default",
        {"managed": True, "provider": "google", "access_token": "old", "refresh_token": "r1", "connection_id": "c1", "expires": time.time() - 10},
    )
    calls: list[str] = []

    def post(url, json=None, headers=None, timeout=None, **_):
        calls.append(json["refresh_token"])
        time.sleep(0.4)  # both callers are inside their renewal before the first finishes
        return _Reply({"access_token": "new", "refresh_token": "r2", "expires_in": 3600})

    monkeypatch.setattr(cloud.httpx, "post", post)
    results: list = []

    def renew() -> None:
        # Each caller has its own store, as two processes would.
        results.append(cloud.refresh_managed_token(SecretStore(path), Config(), "gmail"))

    threads = [threading.Thread(target=renew) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert calls == ["r1"]  # one renewal; the old refresh token was used once
    assert [r["access_token"] for r in results] == ["new", "new"]
    assert SecretStore(path).get("gmail:default")["refresh_token"] == "r2"


def test_two_callers_renew_the_cloud_session_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "secrets.json"
    SecretStore(path).put(cloud.CLOUD_AUTH_PROFILE, {"access_token": "old", "refresh_token": "r1", "expires": time.time() - 10})
    calls: list[str] = []

    def post(url, data=None, timeout=None, **_):
        calls.append(data["refresh_token"])
        time.sleep(0.4)
        return _Reply({"access_token": "new", "refresh_token": "r2", "expires_in": 3600})

    monkeypatch.setattr(cloud.httpx, "post", post)
    results: list = []
    threads = [threading.Thread(target=lambda: results.append(cloud.fresh_access_token(SecretStore(path), Config()))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert calls == ["r1"] and results == ["new", "new"]


def test_two_callers_renew_the_model_sign_in_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "secrets.json"
    SecretStore(path).put(codex_auth.PROFILE, {"tokens": {"access_token": "old", "refresh_token": "r1"}, "account_id": "acct"})
    monkeypatch.setattr(codex_auth, "_jwt_claims", lambda token: {"exp": time.time() + (3600 if token == "new" else -10)})
    calls: list[str] = []

    def token_post(form):
        calls.append(form["refresh_token"])
        time.sleep(0.4)
        return _Reply({"access_token": "new", "refresh_token": "r2"})

    monkeypatch.setattr(codex_auth, "_token_post", token_post)
    results: list = []
    threads = [
        threading.Thread(target=lambda: results.append(codex_auth.CodexTokenStore(SecretStore(path)).access_token())) for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert calls == ["r1"]
    assert [r[0] for r in results] == ["new", "new"]
