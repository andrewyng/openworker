"""Phase 3 manual testing (5 Nov), the server side of rows T3-6 and T3-10: the Auto-approve table
counts "overridden" per reviewer, and the Ratings card's numbers."""

from __future__ import annotations

import time

from coworker.observability import RecordingStore, usage_api

# Turns an hour old: a store with the background writer sweeps on its first tick.
RECENT = int(time.time() * 1000) - 3_600_000


def _turn(tid, sid, seq, at, reviewer_kind=None):
    return {"turn_id": tid, "session_id": sid, "seq": seq, "kind": "run", "trigger": "user", "started_at": at,
            "duration_ms": 1000, "status": "completed", "incomplete": False, "approval_mode": "auto_approve",
            "attended": True, "reviewer": {"kind": reviewer_kind, "active": True, "shadow": False} if reviewer_kind else None,
            "stamp": {"machine": {"kind": "this_machine", "id": None}}, "first_message_id": None,
            "last_message_id": None, "schema_version": 1, "wake": {"reason": "user"}}


def _step(tid, sid, n, at, kind, attrs):
    return {"step_id": f"{tid}-s{n}", "turn_id": tid, "session_id": sid, "parent_step_id": None, "seq": n,
            "kind": kind, "name": kind, "started_at": at, "duration_ms": 100, "status": "ok", "call_id": None,
            "call_index": None, "message_id": None, "shadow": False, "after_turn": False, "attrs": attrs,
            "content": {}}


def _check(result, kind):
    return {"result": result, "reviewer": {"kind": kind}}


OVERRIDE = {"decision": {"path": [{"stage": "auto_approver", "outcome": "deny"}, {"stage": "grant", "outcome": "allow_anyway"}]}}


def test_overridden_is_counted_per_reviewer(tmp_path):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    turns = [_turn("A", "s1", 1, RECENT, "session_model"), _turn("B", "s2", 1, RECENT + 10, "typesafe_jev"),
             _turn("C", "s2", 2, RECENT + 20, "typesafe_jev"), _turn("D", "s3", 1, RECENT + 30, "openai_decisions")]
    steps = [
        _step("A", "s1", 1, RECENT, "auto_approver", _check("allow", "session_model")),
        _step("A", "s1", 2, RECENT + 1, "auto_approver", _check("allow", "session_model")),
        _step("B", "s2", 1, RECENT + 10, "auto_approver", _check("deny", "typesafe_jev")),
        # the person let Jev's block through, twice
        _step("C", "s2", 1, RECENT + 20, "tool", OVERRIDE),
        _step("C", "s2", 2, RECENT + 21, "tool", OVERRIDE),
        _step("D", "s3", 1, RECENT + 30, "auto_approver", _check("unsure", "openai_decisions")),
        # the reviewers' own model calls: tokens per reviewer
        _step("A", "s1", 3, RECENT + 2, "model", {"purpose": "auto_approver", "model": "model-x", "usage": {
            "reported": True, "input_fresh": 300, "cache_read": 0, "cache_write": 0, "output": 20}}),
        _step("D", "s3", 2, RECENT + 31, "model", {"purpose": "auto_approver", "model": "model-y", "usage": {
            "reported": True, "input_fresh": 50, "cache_read": 0, "cache_write": 0, "output": 5}}),
        _step("A", "s1", 4, RECENT + 3, "model", {"purpose": "main", "model": "model-x", "usage": {
            "reported": True, "input_fresh": 9000, "cache_read": 0, "cache_write": 0, "output": 100}}),
    ]
    for t in turns:
        store.write_turn(t)
    for s in steps:
        store.write_step(s)
    store.flush()
    got = usage_api.approver(store, start=RECENT - 1, end=RECENT + 1000)
    assert got["overridden"] == 2  # the total, as before
    rows = {r["kind"]: r for r in got["by_reviewer"]}
    assert set(rows) == {"session_model", "typesafe_jev", "openai_decisions"}
    assert rows["typesafe_jev"]["overridden"] == 2
    assert rows["session_model"]["overridden"] == 0 and rows["openai_decisions"]["overridden"] == 0
    assert rows["session_model"]["checks"] == 2 and rows["openai_decisions"]["asked"] == 1
    # each reviewer's own tokens; main work is not in them
    assert rows["session_model"]["tokens"] == 320 and rows["openai_decisions"]["tokens"] == 55
    assert rows["typesafe_jev"]["tokens"] == 0
    store.close()


# -- T3-12 ----------------------------------------------------------------------------------------
def test_the_default_coworkers_page_and_history_answer(tmp_path, monkeypatch):
    """Settings ▸ Coworkers now opens the default coworker's page too: its detail and its "How
    it's been doing" must answer for the default id, with its turns counted."""
    from fastapi.testclient import TestClient

    from coworker.personas.registry import DEFAULT_PERSONA_ID
    from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from coworker.providers.base import TokenUsage
    from coworker.server import SessionManager, create_app
    from coworker.sessions import SessionRecord

    class _Scripted(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return AssistantTurn(text="Done.", finish_reason="stop", usage=TokenUsage(input=20, output=5))

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path, provider=_Scripted())
    client = TestClient(create_app(manager))
    manager.session_store.save(SessionRecord(session_id="s1", workspace=str(tmp_path), model="sample",
                                             mode="interactive", agent=DEFAULT_PERSONA_ID))
    with client.websocket_connect("/ws/session/s1") as ws:
        assert ws.receive_json()["type"] in ("ready", "export_notice")
        ws.send_json({"type": "user_message", "text": "hello"})
        while ws.receive_json()["type"] != "turn_done":
            pass
    detail = client.get(f"/v1/personas/{DEFAULT_PERSONA_ID}")
    assert detail.status_code == 200 and detail.json().get("default") is True
    doing = client.get(f"/v1/personas/{DEFAULT_PERSONA_ID}/doing", params={"days": 30})
    assert doing.status_code == 200, doing.text
    body = doing.json()
    assert body["persona_id"] == DEFAULT_PERSONA_ID and body["runs"] == 1, body
    assert sum(r["turns"] for r in body["rows"]) == 1
    # T3-15 (d): the default coworker is built in code, not from a manifest; its only version
    # is still the current one. (b, f): a first version, numbered v1, with only its own models.
    [only] = body["rows"]
    assert only["current"] is True and only["version_n"] == 1
    assert only["changed"] is None and only["model_line"]["kind"] == "first"
    assert only["models"] == only["model_line"]["models"]  # the session's own model(s), listed once
    # T3-10's route answers with the card's fields
    now = int(time.time() * 1000)
    got = client.get("/v1/observability/ratings", params={"from": now - 86_400_000, "to": now + 60_000}).json()
    assert (got["up"], got["down"], got["down_turns"], got["more"]) == (0, 0, [], 0)
    manager.observability.close()


# -- T3-10 ----------------------------------------------------------------------------------------
def test_the_ratings_card_counts_reasons_and_lists_the_down_turns(tmp_path):
    from coworker.observability import signals

    store = RecordingStore(tmp_path / "recordings.db", background=False)
    main = {"purpose": "main", "model": "model-x",
            "usage": {"reported": True, "input_fresh": 10, "cache_read": 0, "cache_write": 0, "output": 1}}
    for i, (sid, persona) in enumerate([("s1", "cowork"), ("s1", "cowork"), ("s2", "sec"), ("s2", "sec"), ("s3", "cowork")]):
        t = _turn(f"T{i}", sid, i + 1, RECENT + i * 1000)
        t["stamp"]["persona_id"] = persona
        store.write_turn(t)
        store.write_step(_step(f"T{i}", sid, 1, RECENT + i * 1000, "model", main))
        store.write_issues(f"T{i}", {"signals_version": signals.version(), "warn": 0, "info": 0, "issues": [], "fine": []},
                           seen=True)
    store.flush()
    fb = lambda tid, sid, rating, reasons=(), note=None, auto=(): store.put_feedback(  # noqa: E731
        {"turn_id": tid, "session_id": sid, "rating": rating, "reasons": list(reasons), "note": note,
         "auto_approved": list(auto)})
    fb("T0", "s1", "up")
    fb("T1", "s1", "down", ["wrong_answer", "too_slow"], "it guessed")
    fb("T2", "s2", "down", ["did_unasked"], None, ["T2-s4", "T2-s5"])
    fb("T3", "s2", "up")
    # T4 is not rated
    got = usage_api.ratings(store, start=RECENT - 1, end=RECENT + 60_000, title_of=lambda sid: f"title {sid}")
    assert (got["up"], got["down"]) == (2, 2)
    assert got["reasons"] == {"wrong_answer": 1, "too_slow": 1, "did_unasked": 1}
    assert [t["turn_id"] for t in got["down_turns"]] == ["T2", "T1"]  # newest first
    t2, t1 = got["down_turns"]
    assert t1["note"] == "it guessed" and t1["title"] == "title s1" and t1["reasons"] == ["wrong_answer", "too_slow"]
    assert t2["auto_approved"] == 2 and t2["note"] is None and t2["seq"] == 3
    assert got["more"] == 0
    # one coworker narrows it, as the rest of the page
    one = usage_api.ratings(store, start=RECENT - 1, end=RECENT + 60_000, persona_id="sec")
    assert (one["up"], one["down"]) == (1, 1) and one["down_turns"][0]["turn_id"] == "T2"
    store.close()


# -- T3-3 -----------------------------------------------------------------------------------------
def test_stopping_the_app_drops_queued_scope_checks_and_stops_the_start_up_checks(tmp_path, monkeypatch):
    import asyncio
    import threading as _threading

    from coworker.server import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    gate, ran = _threading.Event(), []

    def check(sid, tid):
        ran.append(tid)
        gate.wait(5)

    monkeypatch.setattr(manager, "run_scope_checks", check)
    for i in range(20):
        manager._scopes_on_turn_end({"type": "turn_ended", "turn_id": f"T{i}", "session_id": "s"})
    asyncio.run(manager.aclose())
    gate.set()
    manager._scope_pool.shutdown(wait=True)
    assert len(ran) <= 1  # the one running finishes; the 19 queued are dropped
    # a turn that ends after the stop is not queued
    manager._scopes_on_turn_end({"type": "turn_ended", "turn_id": "late", "session_id": "s"})
    assert "late" not in ran
    # the start-up checks stop between sessions
    monkeypatch.setattr(manager.recordings_store, "sessions_needing_check", lambda v: ["a", "b"])
    loaded = []
    monkeypatch.setattr(manager.recordings_store, "load_recording", lambda sid: loaded.append(sid))
    assert manager.recheck_issues() == 2 and loaded == []


# -- T3-15 (b) ------------------------------------------------------------------------------------
def test_a_versions_models_are_its_own_work_only(tmp_path):
    from coworker.observability import signals

    store = RecordingStore(tmp_path / "recordings.db", background=False)
    t = _turn("T1", "s1", 1, RECENT)
    t["stamp"].update({"persona_id": "cowork", "persona_fp": "fp1"})
    store.write_turn(t)
    usage = {"reported": True, "input_fresh": 10, "cache_read": 0, "cache_write": 0, "output": 1}
    for n, (purpose, model) in enumerate([("main", "model-x"), ("helper", "model-h"), ("main", "openai:model-h"),
                                          ("main", "together:model-h"), ("auto_approver", "reviewer-model"),
                                          ("title", "title-model"), ("summary", "summary-model")], start=1):
        store.write_step(_step("T1", "s1", n, RECENT + n, "model", {"purpose": purpose, "model": model, "usage": usage}))
    store.write_issues("T1", {"signals_version": signals.version(), "warn": 0, "info": 0, "issues": [], "fine": []}, seen=True)
    store.flush()
    got = usage_api.persona_doing(store, "cowork", start=RECENT - 1, end=RECENT + 60_000)
    [row] = got["rows"]
    # T3-16 (d): openai:model-h is model-h (only the openai: prefix goes); together: keeps its own
    assert row["models"] == ["model-h", "model-x", "together:model-h"]
    assert row["model_line"] == {"kind": "first", "models": ["model-h", "model-x", "together:model-h"]}
    assert row["version_n"] == 1  # T3-16: numbered even when its files were not kept
    assert usage_api.canonical_model("openai:model-x") == "model-x"
    assert usage_api.canonical_model("openrouter:openai/model-x") == "openrouter:openai/model-x"
    store.close()


# -- T3-4 -----------------------------------------------------------------------------------------
def test_one_model_is_read_and_grouped_under_one_name(tmp_path):
    """Turns saved before the model-prefix fix (#837) recorded one OpenAI model as both
    `openai:model-x` and `model-x`. Read and grouped, it is one model; other prefixes stay."""
    from coworker.observability import calculator, read_api, signals
    from coworker.observability.model_names import canonical_model

    assert canonical_model("openai:model-x") == "model-x"
    assert canonical_model(" model-x ") == "model-x"
    assert canonical_model("anthropic:model-y") == "anthropic:model-y"
    assert canonical_model("openai:") == "openai:"  # nothing after the prefix: kept as is
    assert canonical_model("OpenAI:model-x") == "OpenAI:model-x"  # the exact prefix only

    store = RecordingStore(tmp_path / "recordings.db", background=False)
    usage = {"reported": True, "input_fresh": 100, "cache_read": 0, "cache_write": 0, "output": 10}
    for i, model in enumerate(["openai:model-x", "model-x", "anthropic:model-y"]):
        tid = f"T{i}"
        store.write_turn(_turn(tid, "s1", i + 1, RECENT + i * 1000))
        store.write_step(_step(tid, "s1", 1, RECENT + i * 1000, "model", {"purpose": "main", "model": model, "usage": usage}))
        store.write_issues(tid, {"signals_version": signals.version(), "warn": 0, "info": 0, "issues": [], "fine": []}, seen=True)
    store.flush()
    span = (RECENT - 1, RECENT + 60_000)
    groups = {g["key"]["model"]: g["total"] for g in store.usage_rollup(["model"], *span)}
    assert groups == {"model-x": 220, "anthropic:model-y": 110}
    assert [t["models"] for t in store.usage_turns(*span)] == [["model-x"], ["model-x"], ["anthropic:model-y"]]
    heavy = usage_api.heaviest(store, start=span[0], end=span[1])["turns"]
    assert sorted(m for t in heavy for m in t["model_ids"]) == ["anthropic:model-y", "model-x", "model-x"]
    rec = store.load_recording("s1")
    # the per-turn usage keeps the recorded spelling: the golden corpus defines it
    assert calculator.usage(rec)["by_model"] == {"openai:model-x": 110, "model-x": 110, "anthropic:model-y": 110}
    assert set(read_api.usage(rec, "s1")["by_model_parts"]) == {"model-x", "anthropic:model-y"}
    store.close()
