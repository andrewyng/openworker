"""Phase 3 through the real server: the 1.3 fields an engine turn records, the usage ledger
written at turn end, the rollup, heaviest-turns and approver endpoints, the turn footer and
ratings (OB-5), the coworker page's rows (OB-15), and a deleted session's counts kept."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from fastapi.testclient import TestClient

from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.providers.base import TokenUsage
from coworker.server import SessionManager, create_app

SCHEMA = json.loads((Path(__file__).parent / "corpora/observability/recording-format.schema.json").read_text("utf-8"))
DAY = 24 * 60 * 60 * 1000


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


def _app(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_Scripted())
    return manager, TestClient(create_app(manager))


def _days(start_ms: int, n: int) -> str:
    return json.dumps([[f"d{i}", start_ms + i * DAY, start_ms + (i + 1) * DAY] for i in range(n)])


def test_a_user_turn_records_wake_and_persona_and_validates_as_1_3(tmp_path):
    manager, client = _app(tmp_path)
    _turn(client, "s1")
    rec = manager.recordings_store.load_recording("s1")
    turn = rec["turns"][0]
    assert turn["wake"]["reason"] == "user"
    assert turn["stamp"]["persona_id"] == manager.session_store.agent_of("s1")
    assert "automation" not in turn and "team" not in turn["stamp"]
    validator = jsonschema.Draft202012Validator({**SCHEMA, "$ref": "#/$defs/recording"})
    errs = list(validator.iter_errors(rec))
    assert not errs, errs[0].message


def test_the_ledger_equals_the_pill_and_the_endpoints_add_up(tmp_path):
    # Two turns back to back in one session: each test-client socket runs on its own event
    # loop, which once left the second turn's model call outside its turn (gate 1 broke).
    manager, client = _app(tmp_path)
    for _ in range(3):
        _turn(client, "s1")
        _turn(client, "s1", "again")
    _turn(client, "s2")
    _turn(client, "s3")
    pills = {sid: client.get(f"/v1/sessions/{sid}/usage").json()["pill"] for sid in ("s1", "s2", "s3")}
    stray = manager.recordings_store._conn.execute(
        "SELECT COUNT(*) FROM steps WHERE kind = 'model' AND turn_id IS NULL").fetchone()[0]
    assert stray == 0
    t0 = min(t["started_at"] for t in manager.recordings_store.usage_turns(0, 2**62)) - 1000
    days = _days(t0, 2)
    r = client.get("/v1/observability/rollup", params={"from": t0, "to": t0 + 2 * DAY, "days": days,
                                                       "group": "session"}).json()
    by_session = {g["key"]["session"]: g["total"] for g in r["groups"]}
    assert by_session == pills
    total = client.get("/v1/observability/rollup", params={"from": t0, "to": t0 + 2 * DAY}).json()
    assert total["groups"][0]["total"] == sum(pills.values())
    assert total["groups"][0]["turns"] == 8 and total["not_checked"] == 0
    assert total["kept_from"] is not None and total["no_turn"] == {"calls": 0, "total": 0}
    # day x purpose adds up to the day, and the days to the total (gate 1)
    dp = client.get("/v1/observability/rollup", params={"days": days, "group": "day,purpose"}).json()
    assert sum(g["total"] for g in dp["groups"]) == sum(pills.values())
    heavy = client.get("/v1/observability/turns", params={"from": t0, "to": t0 + 2 * DAY}).json()["turns"]
    assert [t["total"] for t in heavy] == sorted((t["total"] for t in heavy), reverse=True)
    assert sorted(t["seq"] for t in heavy if t["session_id"] == "s1") == [1, 2, 3, 4, 5, 6]
    assert client.get("/v1/observability/approver", params={"from": t0, "to": t0 + DAY}).json()["shown"] is False
    assert client.get("/v1/observability/rollup", params={"group": "day"}).status_code == 400


def test_feedback_is_stored_replaced_and_counted(tmp_path):
    manager, client = _app(tmp_path)
    _turn(client, "s1")
    tid = manager.recordings_store.load_recording("s1")["turns"][0]["turn_id"]
    url = f"/v1/sessions/s1/turns/{tid}/feedback"
    assert client.get(url).json()["feedback"] is None
    r = client.put(url, json={"rating": "down", "reasons": ["too_slow"], "note": "slow today"})
    assert r.status_code == 200 and r.json()["feedback"]["reasons"] == ["too_slow"]
    assert r.json()["where"] == {"local": True, "export": None, "managed": False, "by": None, "with_note": False}
    r = client.put(url, json={"rating": "up"})
    fb = r.json()["feedback"]
    assert fb["rating"] == "up" and fb["changed_at"] is not None and fb["content"]["note"] is None
    assert client.put(url, json={"rating": "meh"}).status_code == 400
    assert client.put(url, json={"rating": "up", "reasons": ["bogus"]}).status_code == 400
    assert client.put("/v1/sessions/s2/turns/" + tid + "/feedback", json={"rating": "up"}).status_code == 404
    [t] = manager.recordings_store.usage_turns(0, 2**62)
    assert t["rating"] == "up"
    foot = client.get("/v1/sessions/s1/footers").json()
    assert foot["turns"][0]["feedback"]["rating"] == "up"
    assert foot["turns"][0]["total"] == client.get("/v1/sessions/s1/usage").json()["pill"]
    # the recording file carries the ratings (1.3)
    data = json.loads(client.get("/v1/sessions/s1/recording").content)
    assert data["version"] == "1.3" and data["feedback"][0]["turn_id"] == tid


def test_a_deleted_session_keeps_its_counts_and_loses_its_ratings(tmp_path):
    manager, client = _app(tmp_path)
    _turn(client, "s1")
    tid = manager.recordings_store.load_recording("s1")["turns"][0]["turn_id"]
    client.put(f"/v1/sessions/s1/turns/{tid}/feedback", json={"rating": "down", "note": "mine"})
    before = client.get("/v1/observability/rollup").json()["groups"][0]["total"]
    assert client.delete("/v1/sessions/s1").status_code == 200
    after = client.get("/v1/observability/rollup").json()
    assert after["groups"][0]["total"] == before and after["deleted_session_turns"] == 1
    assert manager.recordings_store.get_feedback(tid) is None
    heavy = client.get("/v1/observability/turns", params={"from": 0}).json()["turns"]
    assert heavy[0]["session_deleted"] is True and heavy[0]["title"] is None


def test_the_coworker_page_rows_split_by_fingerprint(tmp_path):
    manager, client = _app(tmp_path)
    _turn(client, "s1")
    _turn(client, "s2")
    pid = manager.session_store.agent_of("s1")
    doing = client.get(f"/v1/personas/{pid}/doing").json()
    assert doing["runs"] == 2 and len(doing["rows"]) == 1
    row = doing["rows"][0]
    assert row["runs"] == 2 and row["too_few"] is True and row["median_tokens"] is None
    # the version was kept with its name and files
    versions = manager.recordings_store.persona_versions(pid)
    assert versions and versions[0]["fp"] == row["fp"] and "manifest.md" in versions[0]["files"]


def test_the_usage_endpoints_narrow_to_one_coworker(tmp_path):
    manager, client = _app(tmp_path)
    _turn(client, "s1")
    pid = manager.session_store.agent_of("s1")
    params = {"from": 0}
    mine = client.get("/v1/observability/rollup", params={**params, "persona_id": pid}).json()
    other = client.get("/v1/observability/rollup", params={**params, "persona_id": "nobody"}).json()
    assert mine["groups"][0]["turns"] == 1 and other["groups"] == []
    assert len(client.get("/v1/observability/turns", params={**params, "persona_id": pid}).json()["turns"]) == 1
    assert client.get("/v1/observability/turns", params={**params, "persona_id": "nobody"}).json()["turns"] == []
    assert client.get("/v1/observability/approver", params={**params, "persona_id": "nobody"}).json()["checks"] == 0
