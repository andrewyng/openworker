"""Phase 3 engine items, one test each: the scanner facts on a real shell step (E17b), the
1.3 turn facts an entry point passes (E18a, E18b, E26a, E25a), the wake reason of a team
delivery (TD3-18), the journal's evidence links (E17a), the `verifies` link (E17d), the
hand-off coverage report (E17c), stored series and task results, the ledger's 90 days and
the feedback export (E27b)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from coworker.engine import TurnEngine
from coworker.observability import Recorder, RecordingStore, scopes, signals
from coworker.observability import export as ex
from coworker.observability import export_spans as spans
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.providers.base import TokenUsage
from coworker.server.manager import SessionManager
from coworker.teams.journal import JournalStore
from coworker.teams.model import Actor, Role
from coworker.teams.store import BoardError, TeamStore
from coworker.tools import ToolRegistry

SCHEMA = json.loads((Path(__file__).parent / "corpora/observability/recording-format.schema.json").read_text("utf-8"))


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


class _Meta:
    category: str = ""
    risk_level: str = "low"
    requires_approval: bool = False


def run_shell(command: str) -> dict:
    """Run a shell command.

    Args:
        command: the command
    """
    return {"exit_code": 1 if "gitleaks" in command else 0, "stdout": "found 2 leaks"}


def _call(cid, name, args):
    return AssistantTurn(tool_calls=[ToolCall(id=cid, name=name, arguments=args)], finish_reason="tool_calls",
                         usage=TokenUsage(input=10, output=2))


def _engine(tmp_path, turns):
    registry = ToolRegistry()
    registry.register(run_shell, metadata=_Meta())
    engine = TurnEngine(
        provider=_Scripted(turns), registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS),
        model="openai:model-x", instructions="You are a test coworker.", max_iterations=12,
    )
    engine.audit_context = {"session_id": "sess_p3"}
    engine.persona_id, engine.persona_fp, engine.persona_coverage = "security", "a1b2c3", ["secrets"]
    rec = Recorder()
    engine.recorder = rec
    return engine, rec


def test_a_shell_step_records_its_scanner_and_result_hash(tmp_path):
    engine, rec = _engine(tmp_path, [
        _call("c1", "run_shell", {"command": "npx gitleaks detect --source ."}),
        _call("c2", "run_shell", {"command": "grep -r semgrep docs"}),
        AssistantTurn(text="done", usage=TokenUsage(input=10, output=2)),
    ])

    async def go():
        return [ev async for ev in engine.run("scan", trigger="team_wake", team={"team_id": "t1", "role": "worker"},
                                              wake={"reason": "assigned", "item_ids": [7], "board_seq": 41})]

    asyncio.run(go())
    d = rec.as_dict()
    tools = {s["call_id"]: s["attrs"] for s in d["steps"] if s["kind"] == "tool"}
    assert tools["c1"]["scanner"] == {"name": "gitleaks", "action": "scan"} and tools["c1"]["exit_code"] == 1
    assert tools["c2"]["scanner"] is None
    raw = json.dumps({"exit_code": 1, "stdout": "found 2 leaks"}, ensure_ascii=False).encode("utf-8")
    assert tools["c1"]["result_sha256"] == hashlib.sha256(raw).hexdigest()
    turn = d["turns"][0]
    assert turn["stamp"]["persona_id"] == "security" and turn["stamp"]["persona_coverage"] == ["secrets"]
    assert turn["stamp"]["team"] == {"team_id": "t1", "role": "worker"}
    assert turn["wake"] == {"reason": "assigned", "kinds": [], "item_ids": ["7"], "board_seq": 41,
                            "batched": None, "deferred": None, "by": None}
    errs = list(jsonschema.Draft202012Validator({**SCHEMA, "$ref": "#/$defs/recording"}).iter_errors(d))
    assert not errs, errs[0].message


def test_an_automation_turn_carries_its_run_and_a_bad_wake_reason_reads_unknown(tmp_path):
    engine, rec = _engine(tmp_path, [AssistantTurn(text="brief", usage=TokenUsage(input=10, output=2))])

    async def go():
        return [ev async for ev in engine.run("go", trigger="schedule", wake={"reason": "bogus"},
                                              automation={"task_id": "task-1", "run_id": "run-1", "trigger": "catch_up"})]

    asyncio.run(go())
    turn = rec.as_dict()["turns"][0]
    assert turn["automation"] == {"task_id": "task-1", "run_id": "run-1", "trigger": "catch_up"}
    assert turn["wake"]["reason"] == "unknown" and turn["trigger"] == "schedule"


@pytest.mark.parametrize("rows,lead,want", [
    ([{"kind": "moved", "to": "review", "item": 3}, {"kind": "assigned", "item": 4}], True, "review"),
    ([{"kind": "moved", "to": "blocked", "item": 3}, {"kind": "waiting", "item": 4}], True, "blocker"),
    ([{"kind": "chat"}, {"kind": "chat"}], False, "mention"),
    ([{"kind": "moved", "to": "done", "item": 3}], False, "prereq_done"),
    ([{"kind": "moved", "to": "done", "item": 3}], True, "board"),
    ([{"kind": "claimed", "item": 5}, {"kind": "chat"}], True, "assigned"),
])
def test_one_wake_reason_by_fixed_priority(rows, lead, want):
    reason, kinds, items = SessionManager._wake_from_rows(rows, is_lead=lead)
    assert reason == want
    assert kinds == list(dict.fromkeys(r["kind"] for r in rows))


def _team(tmp_path):
    journal = JournalStore(tmp_path / "journal.db")
    store = TeamStore(tmp_path / "board.db", journal=journal)
    lead = Actor(id="lead:1", role=Role.LEAD, persona="devsecops-lead", session_id="lead-s")
    worker = Actor(id="appsec:1", role=Role.WORKER, persona="appsec-worker", session_id="w-s")
    return store, journal, lead, worker


def test_verifies_is_a_board_link_both_ways(tmp_path):
    store, _j, lead, _w = _team(tmp_path)
    fix = store.create_item("space", lead, title="Fix SQLi", criteria="query is parameterised")
    check = store.create_item("space", lead, title="Verify the fix", criteria="rerun semgrep", verifies=fix["id"])
    assert {"kind": "verifies", "item": fix["id"]} in check["links"]
    again = store.get_item("space", fix["id"], actor=lead)
    assert {"kind": "verified_by", "item": check["id"]} in again["links"]
    with pytest.raises(BoardError):
        store.create_item("space", lead, title="x", criteria="y", verifies=999)


def test_a_hand_off_carries_a_checked_coverage_report(tmp_path):
    store, _j, lead, worker = _team(tmp_path)
    item = store.create_item("space", lead, title="Scan", criteria="all areas")
    store.assign("space", lead, item["id"], worker.id)
    store.transition("space", worker, item["id"], "in_progress")
    store.transition("space", worker, item["id"], "review", comment="done",
                     coverage={"secrets": {"ran": False, "findings": 0, "reason": "gitleaks missing"},
                               "code": {"ran": True, "findings": 3}})
    ev = [e for e in store.events("space", item_id=item["id"]) if e["kind"] == "item_transitioned"][-1]
    assert ev["payload"]["coverage"] == {"secrets": {"ran": False, "findings": 0, "reason": "gitleaks missing"},
                                         "code": {"ran": True, "findings": 3}}
    for bad in ({"network": {"ran": True}}, {"code": {"ran": "yes"}}, {"code": {"ran": True, "findings": -1}}):
        with pytest.raises(BoardError):
            store.transition("space", worker, item["id"], "in_progress", coverage=bad)


def test_journal_evidence_is_outside_the_hash_chain(tmp_path):
    _s, journal, lead, worker = _team(tmp_path)
    plain = journal.append(worker, "case-1", "an old-style note")
    found = journal.append(worker, "case-1", "SQLi in login", kind="finding", severity="high",
                           step_call_id="call_9", evidence=[{"session_id": "w-s", "call_id": "call_4"}])
    assert found["evidence"] == [{"session_id": "w-s", "call_id": "call_4"}] and found["step_call_id"] == "call_9"
    rows = journal.read(worker, "case-1")
    assert rows[0]["evidence"] is None and rows[0]["severity"] is None
    assert rows[1]["evidence"] == [{"session_id": "w-s", "call_id": "call_4"}] and rows[1]["severity"] == "high"
    # changing the pointer does not touch the chain
    with journal._lock:
        journal._conn.execute("UPDATE journal_entries SET evidence = '[]' WHERE seq = ?", (found["seq"],))
    assert journal.read(worker, "case-1")[1]["hash"] == found["hash"]
    assert plain["hash"] != found["hash"]


def test_journal_append_links_the_sessions_scans_by_default(tmp_path):
    from coworker.teams.tools import journal_tools

    _s, journal, _lead, worker = _team(tmp_path)
    tools = {t.__name__: t for t in journal_tools(
        journal, actor=worker, space="space",
        scan_evidence=lambda item: [{"session_id": "w-s", "call_id": "call_2"}])}
    out = tools["journal_append"]("case-2", "a leaked key", kind="finding", severity="critical")
    assert out["evidence"] == [{"session_id": "w-s", "call_id": "call_2"}]
    named = tools["journal_append"]("case-2", "another", kind="finding", evidence=["call_7"])
    assert named["evidence"] == [{"session_id": "w-s", "call_id": "call_7"}]
    note = tools["journal_append"]("case-2", "just a note")
    assert note["evidence"] is None
    assert "error" in tools["journal_append"]("case-2", "x", kind="finding", severity="severe")


def _write(store, turns, steps, issues=None):
    for t in turns:
        store.write_turn(t)
    for s in steps:
        store.write_step(s)
    for t in turns:
        store.write_issues(t["turn_id"], (issues or {}).get(t["turn_id"]) or
                           {"signals_version": signals.version(), "warn": 0, "info": 0, "issues": [], "fine": []}, seen=True)
    store.flush()


def _turn(tid, sid, seq, at, wake, team=None, status="completed"):
    stamp = {"machine": {"kind": "this_machine", "id": None}, "persona_id": "devsecops-lead"}
    if team:
        stamp["team"] = team
    return {"turn_id": tid, "session_id": sid, "seq": seq, "kind": "run", "trigger": "team_wake", "started_at": at,
            "duration_ms": 1000, "status": status, "incomplete": False, "approval_mode": "interactive",
            "attended": True, "reviewer": None, "stamp": stamp, "first_message_id": None,
            "last_message_id": None, "schema_version": 1, "wake": wake}


def _step(tid, sid, n, at):
    return {"step_id": f"{tid}-s{n}", "turn_id": tid, "session_id": sid, "parent_step_id": None, "seq": n,
            "kind": "model", "name": "model", "started_at": at, "duration_ms": 100, "status": "ok",
            "call_id": None, "call_index": None, "message_id": None, "shadow": False, "after_turn": False,
            "attrs": {"purpose": "main", "model": "model-x", "usage": {"reported": True, "input_fresh": 10,
                                                                         "cache_read": 0, "cache_write": 0, "output": 1}},
            "content": {}}


def test_lead_polling_is_stored_once_on_the_last_turn_of_the_run(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_Scripted([]))
    store = manager.recordings_store
    team = {"team_id": "t1", "role": "lead"}
    turns = [_turn(f"L{i}", "lead-s", i, 1_000_000 + i * 60_000, {"reason": "timer", "board_seq": 5}, team)
             for i in range(1, 5)]
    _write(store, turns, [_step(t["turn_id"], "lead-s", 1, t["started_at"]) for t in turns])
    manager._scope_lead_polling("lead-s")
    got = {tid: [i["check"] for i in (store.read_turn_issues(tid) or {}).get("issues", [])] for tid in ("L3", "L4")}
    assert got == {"L3": [], "L4": ["lead_polling"]}
    issue = store.read_turn_issues("L4")
    assert issue["warn"] == 1 and issue["seen"] is False
    assert issue["issues"][0]["text"] == "The lead woke on its timer 4 times with no board change."
    # the ledger follows the stored result
    [t4] = [t for t in store.usage_turns(0, 2**62) if t["turn_id"] == "L4"]
    assert t4["warn"] == 1 and "lead_polling" in t4["warn_checks"]
    # a re-check of the turn alone keeps the series result
    rec = store.load_recording("lead-s")
    store.write_issues("L4", {**issue, "signals_version": "old"})
    store.flush()
    out = manager._recording_issues("lead-s", rec)
    assert [i["check"] for i in out["L4"]["issues"]] == ["lead_polling"]
    manager.observability.close()


def test_the_ledger_is_kept_90_days(tmp_path):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    now = 200 * 24 * 3600 * 1000
    old = _turn("old", "s", 1, now - 91 * 24 * 3600 * 1000, {"reason": "user"})
    new = _turn("new", "s", 2, now - 40 * 24 * 3600 * 1000, {"reason": "user"})
    _write(store, [old, new], [_step("old", "s", 1, old["started_at"]), _step("new", "s", 1, new["started_at"])])
    store.sweep(now_ms=now)  # recordings: 30 days
    assert store.load_recording("s")["turns"] == []
    store.sweep_ledger(now_ms=now)  # the ledger: 90 days
    assert [t["turn_id"] for t in store.usage_turns(0, 2**62)] == ["new"]
    store.close()


def test_ratings_go_out_as_a_langfuse_score_and_an_otlp_span(tmp_path):
    store = RecordingStore(tmp_path / "recordings.db", background=False)
    t = _turn("T1", "s", 1, 5_000, {"reason": "user"})
    _write(store, [t], [_step("T1", "s", 1, 5_000)])
    store.put_feedback({"turn_id": "T1", "session_id": "s", "rating": "down", "reasons": ["did_unasked"],
                        "note": "used my key sk-PLANT", "auto_approved": []})
    posts = []

    def post(url, body, headers, timeout):
        posts.append((url, json.loads(body)))
        return 200, ""

    for kind, prompts in (("langfuse", False), ("otlp", True)):
        posts.clear()
        dest = ex.Destination(kind="langfuse", host="http://lf.test") if kind == "langfuse" else \
            ex.Destination(kind="otlp", url="http://otel.test/v1/traces")
        cfg = ex.ExportConfig(destination=dest, headers={}, include={"messages": prompts}, since_ms=0)
        exp = ex.Exporter(store, cfg, post=post, secret_values=lambda: ["sk-PLANT"])
        exp.flush_once()
        fb = [p for p in posts if "scores" in p[0] or "feedback" in json.dumps(p[1])]
        assert len(fb) == 1
        if kind == "langfuse":
            body = fb[0][1]
            assert fb[0][0] == "http://lf.test/api/public/scores"
            assert body["name"] == "user_feedback" and body["value"] == -1
            assert body["id"] == spans.feedback_id("T1") and body["traceId"] == spans.trace_id("T1")
            assert body["metadata"] == {"reasons": ["did_unasked"]} and "comment" not in body
        else:
            span = fb[0][1]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
            attrs = {a["key"]: a["value"] for a in span["attributes"]}
            assert span["name"] == "openworker.feedback" and span["parentSpanId"] == spans.root_span_id("T1")
            assert attrs["openworker.feedback.rating"] == {"stringValue": "down"}
            assert "PLANT" not in json.dumps(attrs) and "openworker.feedback.note" in attrs
        # sent once; a changed rating goes again, under the same fixed id
        posts.clear()
        exp.flush_once()
        assert not [p for p in posts if "scores" in p[0] or "feedback" in json.dumps(p[1])]
    store.close()


def test_the_turn_span_names_the_coworker_and_keeps_agent_name(tmp_path):
    t = _turn("T1", "s", 1, 5_000, {"reason": "review"}, {"team_id": "t1", "role": "lead"})
    t["stamp"]["persona_fp"] = "a1b2c3"
    t["automation"] = {"task_id": "task-1", "run_id": "run-1", "trigger": "manual"}
    built = spans.build_turn_spans(t, [_step("T1", "s", 1, 5_000)], sw=spans.switches_from({}), dialect="semconv",
                                   get_piece=lambda k: None, masker=spans.Masker(), include_root=True,
                                   only=None, session_title=None)
    root = built[0]["attrs"]
    assert root["gen_ai.agent.name"] == "coworker"  # TD3-25: the 1.0 value stays
    assert root["openworker.persona.id"] == "devsecops-lead" and root["openworker.persona.fp"] == "a1b2c3"
    assert root["openworker.wake.reason"] == "review" and root["openworker.team.role"] == "lead"
    assert root["openworker.automation.run_id"] == "run-1" and root["openworker.automation.trigger"] == "manual"


def test_run_health_counts_a_stuck_run_as_failed():
    turn = {"status": "completed", "warn_checks": [], "ended_at": 1_000}
    assert scopes.run_health({"status": "running"}, turn, now_ms=1_000 + 600_001) == "failed"
    assert scopes.run_health({"status": "running"}, turn, now_ms=1_000 + 60_000) == "ok"
    assert scopes.run_health({"status": "ok"}, {**turn, "warn_checks": ["model_failed"]}) == "failed"
    assert scopes.run_health({"status": "ok"}, None) == "unknown"


def test_turns_recorded_before_the_ledger_are_filled_in(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_Scripted([]))
    store = manager.recordings_store
    t = _turn("T0", "s-old", 1, 1_000, {"reason": "user"})
    _write(store, [t], [_step("T0", "s-old", 1, 1_000)])
    with store._lock, store._conn:
        store._conn.execute("DELETE FROM turn_usage")  # as a Phase 2 store has none
    assert store.turns_without_usage() == ["T0"]
    assert manager.backfill_usage() == 1
    [row] = store.usage_turns(0, 2**62)
    assert row["total"] == 11 and store.turns_without_usage() == []
    manager.observability.close()


def test_a_wake_that_was_not_delivered_is_given_back_to_the_cap(tmp_path):
    from coworker.teams.registry import TeamRegistry, TeamWorker

    reg = TeamRegistry(tmp_path / "teams.json")
    team = reg.create(space="sp", lead_session="lead-s", lead_actor="lead-1",
                      workers=[TeamWorker(actor="w", persona="appsec-worker", session_id="w-s")])
    assert all(reg.count_wake(team.team_id, cap=2) for _ in range(2))
    assert reg.count_wake(team.team_id, cap=2) is False
    reg.refund_wake(team.team_id)  # TD3-22: counted only once delivered
    assert reg.count_wake(team.team_id, cap=2) is True


def test_the_wake_cap_event_wakes_nobody_and_deferrals_are_recorded(tmp_path):
    from coworker.teams.registry import TeamWorker

    manager = SessionManager(workspace=tmp_path, provider=_Scripted([]))
    team = manager.teams.create(space=str(tmp_path), lead_session="lead-s", lead_actor="lead-1",
                                workers=[TeamWorker(actor="w", persona="appsec-worker", session_id="w-s")])
    manager._note_held("lead-s", "cap")
    manager._note_wake_cap(team)
    manager._note_wake_cap(team)  # once per hour
    caps = [e for e in manager.team_store.events(team.space) if e["kind"] == "wake_cap_reached"]
    assert len(caps) == 1 and caps[0]["payload"]["held"] == ["lead-s"]
    assert manager._actionable_team_events(team, caps, actor="lead-1", is_lead=True) == []
    wake = manager._turn_wake("lead-s", source={"connector": "board"}, trigger="team_wake",
                              rows=[{"kind": "moved", "to": "review", "item": 3}])
    assert wake["reason"] == "review" and wake["deferred"]["by"] == "cap" and wake["deferred"]["ms"] >= 0
    assert wake["board_seq"] == caps[0]["seq"]
    assert manager._team_stamp("w-s") == {"team_id": team.team_id, "role": "worker"}
    manager.observability.close()


def test_a_turn_on_a_new_event_loop_is_not_read_as_stopped(tmp_path):
    """Two turns of one engine on two event loops (each test-client socket has its own): the
    second turn's answer and its model call stay in that turn (gate 1)."""
    engine, rec = _engine(tmp_path, [AssistantTurn(text="one", usage=TokenUsage(input=10, output=2)),
                                     AssistantTurn(text="two", usage=TokenUsage(input=10, output=3))])

    async def go(text):
        return [ev async for ev in engine.run(text, trigger="user")]

    asyncio.run(go("first"))
    asyncio.run(go("second"))
    d = rec.as_dict()
    assert [m.get("content") for m in engine.messages if m.get("role") == "assistant"] == ["one", "two"]
    models = [s for s in d["steps"] if s["kind"] == "model"]
    assert len(models) == 2 and all(s["turn_id"] for s in models)
    assert {s["turn_id"] for s in models} == {t["turn_id"] for t in d["turns"]}
