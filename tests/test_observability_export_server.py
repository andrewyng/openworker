"""OB-6 through the real server: the settings, status and test endpoints, the managed state,
the session notice, Record steps off, the recording file, and Off meaning no exporter and no
connection at all (phase-2 design §10.3, §10.6 to §10.8)."""

from __future__ import annotations

import json
import threading

from fastapi.testclient import TestClient

from coworker.observability import export as ex
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.providers.base import TokenUsage
from coworker.remote import joiner
from coworker.server import SessionManager, create_app


class _Scripted(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        if str(messages[0].get("content") or "").startswith("You title"):
            return AssistantTurn(text="Notes", usage=TokenUsage(input=40, output=2))
        return AssistantTurn(text="Done.", finish_reason="stop", usage=TokenUsage(input=20, output=5))

    def capabilities(self, model):
        return ModelCapabilities()


def _turn(client, sid: str, text: str = "hello") -> None:
    with client.websocket_connect(f"/ws/session/{sid}") as ws:
        assert ws.receive_json()["type"] in ("ready", "export_notice")
        ws.send_json({"type": "user_message", "text": text})
        while ws.receive_json()["type"] != "turn_done":
            pass


def _app(tmp_path, monkeypatch, posts: list):
    def post(url, body, headers, timeout):
        posts.append(url)
        return 200, ""

    # Every send the exporter or the test button could make goes through `post`; with Off it
    # must never be called.
    manager = SessionManager(workspace=tmp_path, provider=_Scripted())
    manager.observability.post = post
    return manager, TestClient(create_app(manager))


def test_off_creates_no_exporter_and_sends_nothing(tmp_path, monkeypatch):
    posts: list = []
    manager, client = _app(tmp_path, monkeypatch, posts)
    _turn(client, "s1")
    assert client.get("/v1/sessions/s1/turns").json()["turns"]  # recording works
    assert manager.observability.exporter is None
    assert not any(t.name == "observability-export" for t in threading.enumerate())
    assert client.get("/v1/observability/status").json()["state"] == "off"
    test = client.post("/v1/observability/test").json()
    assert test["ok"] is False and posts == []


def test_settings_endpoints_are_write_only_for_keys(tmp_path, monkeypatch):
    posts: list = []
    manager, client = _app(tmp_path, monkeypatch, posts)
    got = client.get("/v1/observability/settings").json()
    assert got == {"record": True, "retention_days": 30, "destination": {"kind": "off"},
                   "include": {"messages": False, "tool_io": False, "context": False, "names": False},
                   "secrets": {}, "managed": None, "notice": None}
    r = client.put("/v1/observability/settings", json={
        "destination": {"kind": "otlp", "url": "http://127.0.0.1:9/v1/traces", "header_names": ["x-honeycomb-team"]},
        "secrets": {"headers": {"x-honeycomb-team": "PLANT-header-value"}}, "retention_days": 7})
    assert r.status_code == 200
    body = r.json()
    assert body["secrets"] == {"headers": {"x-honeycomb-team": "••••••••"}}
    for answer in (body, client.get("/v1/observability/settings").json(), client.get("/v1/observability/status").json()):
        assert "PLANT" not in json.dumps(answer)
    state = manager.secrets.path.parent
    assert "PLANT" not in (state / ex.SETTINGS_FILE).read_text(encoding="utf-8")
    assert manager.secrets.get(ex.PROFILE)["user"]["headers"] == {"x-honeycomb-team": "PLANT-header-value"}
    assert manager.recordings_store.retention_days == 7
    assert manager.observability.exporter is not None
    # A turn after the change is sent; the status counts it.
    _turn(client, "s2")
    manager.observability.exporter.flush_once()
    status = client.get("/v1/observability/status").json()
    assert status["state"] == "sending" and status["sent"] > 0 and status["last_sent_at"]
    assert set(status) >= {"state", "last_sent_at", "sent", "failed", "dropped", "last_error"}
    assert posts and all(u == "http://127.0.0.1:9/v1/traces" for u in posts)
    manager.observability.close()


def test_a_company_policy_makes_the_page_read_only_and_tells_new_sessions(tmp_path, monkeypatch):
    posts: list = []
    manager, client = _app(tmp_path, monkeypatch, posts)
    policy = {"org_id": "org_1", "org_name": "Acme Corp", "version": 2, "observability_export": {
        "enabled": True, "name": "Security team default", "set_at": "2026-10-12",
        "destination": {"kind": "langfuse", "host": "https://langfuse.acme.internal",
                        "public_key": "pk-lf-PLANT-0001", "secret_key": "sk-lf-PLANT"},
        "include": {"messages": True}}}

    class _App:
        class state:  # noqa: N801
            pass
    _App.state.manager = manager
    state = manager.secrets.path.parent
    applied, _ = joiner.apply_policy(_App, policy, state=state)
    assert applied
    assert "PLANT" not in (state / "policy.json").read_text(encoding="utf-8")
    got = client.get("/v1/observability/settings").json()
    assert got["managed"]["by"] == "Acme Corp" and got["destination"]["host"] == "https://langfuse.acme.internal"
    assert "PLANT" not in json.dumps(got)
    r = client.put("/v1/observability/settings", json={"include": {"tool_io": True}})
    assert r.status_code == 409 and r.json()["error"] == "Managed by Acme Corp"
    # Each new session says what goes where, once.
    with client.websocket_connect("/ws/session/fresh") as ws:
        msgs = [ws.receive_json() for _ in range(2)]
        notice = next(m for m in msgs if m["type"] == "export_notice")
    assert notice["data"]["text"] == "Acme Corp receives this session's messages (Langfuse, langfuse.acme.internal)."
    kinds = [m.get("kind") for m in client.get("/v1/sessions/fresh/messages").json()["messages"]]
    assert kinds.count("export_notice") == 1
    manager.observability.close()


def test_record_steps_off_records_nothing_for_new_sessions(tmp_path, monkeypatch):
    manager, client = _app(tmp_path, monkeypatch, [])
    assert client.put("/v1/observability/settings", json={"record": False}).status_code == 200
    _turn(client, "quiet")
    assert client.get("/v1/sessions/quiet/turns").json()["turns"] == []
    assert client.get("/v1/observability/settings").json()["record"] is False


def test_the_recording_file_holds_the_whole_session(tmp_path, monkeypatch):
    manager, client = _app(tmp_path, monkeypatch, [])
    _turn(client, "s9", "what is in notes?")
    r = client.get("/v1/sessions/s9/recording")
    assert r.status_code == 200
    assert 'attachment; filename="recording-s9.json"' == r.headers["content-disposition"]
    data = r.json()
    assert data["session_id"] == "s9" and data["version"] == "1.2"
    [turn] = data["turns"]
    steps = data["steps"][turn["turn_id"]]
    assert steps and all(s["step_id"] in data["details"] for s in steps)
