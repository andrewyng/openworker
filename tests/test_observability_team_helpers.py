"""Observability Phase 2, OB-7: helpers and team workers in the timeline (design §13).

- E6b: the `explore` step carries `attrs.helper`, `{index, status, model_calls, tool_calls,
  tokens}`, for display only. The pill still adds up the helper's own model steps.
- E19a: team tool steps carry `attrs.team` (ids only); worker turns carry `woken_by`; a
  `steer_worker` delivery is `trigger: team_wake` with `via: steer`, not `channel`.
- E19b: the read API lists, under each lead turn, the worker turns that lead woke during it.
  Their tokens are display only, never in the lead's pill.
- E19c: TeamView's totals come from the recording, so helper tokens count (V2-29).
- Export: a worker turn's span carries `openworker.team.id` and `openworker.lead.session.id`.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
from pathlib import Path

import jsonschema

from coworker.engine import TurnEngine
from coworker.observability import Recorder, calculator, export_spans, read_api
from coworker.observability.engine_hooks import ToolRun, note_team, set_tool_run
from coworker.observability.provider_view import RecordingProvider
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.providers.base import TokenUsage
from coworker.teams import Actor, Role, TeamStore, board_tools
from coworker.teams.registry import TeamWorker
from coworker.teams.tools import propose_team_tool
from coworker.tools import ToolRegistry
from coworker.tools.subagent import explorer_tools

proposals = importlib.import_module("proposal_fixtures")
SCHEMA = json.loads((Path(__file__).parent / "corpora/observability/recording-format.schema.json").read_text("utf-8"))


def _check(recording):
    validator = jsonschema.validators.validator_for(SCHEMA)(SCHEMA)
    errors = sorted(validator.iter_errors(recording), key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message[:300]}" for e in errors[:8])


# -- E6b: the helper's summary on the explore step ----------------------------------------------

class _ByTask(ProviderClient):
    """Answers each conversation by its first user message, so parallel helpers each get
    their own script whichever thread asks first."""

    def __init__(self, scripts):
        self._scripts = {k: list(v) for k, v in scripts.items()}
        self._lock = threading.Lock()

    def complete(self, *, model, messages, tools=None, **settings):
        first = next(m["content"] for m in messages if m.get("role") == "user")
        key = next(k for k in self._scripts if k in str(first))
        with self._lock:
            turn = self._scripts[key].pop(0)
        return AssistantTurn(text=turn, usage=TokenUsage(input=50, output=5)) if isinstance(turn, str) else turn

    def capabilities(self, model):
        return ModelCapabilities()


def _calls(*calls):
    return AssistantTurn(tool_calls=[ToolCall(id=c, name=n, arguments=a) for c, n, a in calls],
                         finish_reason="tool_calls", usage=TokenUsage(input=100, output=10))


def _explore_engine(tmp_path, scripts):
    (tmp_path / "notes.txt").write_text("Friday release", encoding="utf-8")
    base = RecordingProvider(_ByTask(scripts))
    registry = ToolRegistry()
    registry.register_all(explorer_tools(workspace=tmp_path, provider=base, model="openai:model-x"))
    engine = TurnEngine(provider=base, registry=registry,
                        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS),
                        model="openai:model-x")
    engine.audit_context = {"session_id": "sess_lead"}
    rec = Recorder()
    engine.recorder = rec
    return engine, rec


def _run(engine, text):
    async def go():
        return [ev async for ev in engine.run(text, trigger="user")]

    return asyncio.run(go())


def test_the_explore_step_says_what_its_helper_did(tmp_path):
    engine, rec = _explore_engine(tmp_path, {
        "use explore": [_calls(("c1", "explore", {"task": "alpha: what is in notes.txt?"})),
                        AssistantTurn(text="Friday.", usage=TokenUsage(input=10, output=1))],
        "alpha": [_calls(("h1", "read_file", {"path": "notes.txt"}), ("h2", "list_files", {})),
                  "It says Friday release"],
    })
    _run(engine, "use explore")
    d = rec.as_dict()
    [explore] = [s for s in d["steps"] if s["name"] == "explore"]
    # Two helper model calls: 110 + 55 tokens; two tool calls.
    assert explore["attrs"]["helper"] == {"index": 1, "status": "ok", "model_calls": 2,
                                          "tool_calls": 2, "tokens": 165}
    # The pill counts the helper's own model steps once, never the summary as well:
    # lead 110 + 11, helper 110 + 55.
    assert calculator.usage(d)["pill"] == 110 + 11 + 110 + 55
    _check(d)


def test_two_helpers_in_parallel_are_numbered_in_the_models_order(tmp_path):
    engine, rec = _explore_engine(tmp_path, {
        "use explore": [_calls(("c1", "explore", {"task": "alpha"}), ("c2", "explore", {"task": "beta"})),
                        AssistantTurn(text="done")],
        "alpha": ["alpha report"],
        "beta": [_calls(("b1", "read_file", {"path": "notes.txt"})), "beta report"],
    })
    _run(engine, "use explore")
    d = rec.as_dict()
    by_call = {s["call_id"]: s["attrs"]["helper"] for s in d["steps"] if s["name"] == "explore"}
    assert by_call["c1"]["index"] == 1 and by_call["c1"]["model_calls"] == 1
    assert by_call["c2"]["index"] == 2 and by_call["c2"]["tool_calls"] == 1
    _check(d)


def test_a_helper_that_hits_its_step_limit_says_so(tmp_path):
    loop = [_calls((f"h{i}", "list_files", {})) for i in range(12)]
    engine, rec = _explore_engine(tmp_path, {
        "use explore": [_calls(("c1", "explore", {"task": "alpha"})), AssistantTurn(text="partial")],
        "alpha": loop,
    })
    _run(engine, "use explore")
    d = rec.as_dict()
    [explore] = [s for s in d["steps"] if s["name"] == "explore"]
    helper = explore["attrs"]["helper"]
    assert helper["status"] == "step_limit"
    assert helper["model_calls"] == 10  # the helper's limit
    _check(d)


# -- E19a: ids on the team tool steps -----------------------------------------------------------

class _Script(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        turn = self._turns.pop(0)
        return AssistantTurn(text=turn, usage=TokenUsage(input=10, output=1)) if isinstance(turn, str) else turn

    def capabilities(self, model):
        return ModelCapabilities()


def _team_engine(tmp_path, turns, tools):
    registry = ToolRegistry()
    registry.register_all(tools)
    engine = TurnEngine(provider=_Script(turns), registry=registry,
                        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS),
                        model="openai:model-x")
    engine.audit_context = {"session_id": "sess_lead"}
    rec = Recorder()
    engine.recorder = rec
    return engine, rec


def test_the_leads_board_calls_carry_the_team_and_item_ids(tmp_path):
    store = TeamStore(tmp_path / "teams.db")
    lead = Actor(id="lead", role=Role.LEAD, session_id="sess_lead")
    tools = board_tools(store, space="proj", actor=lead, team_id=lambda: "team-1",
                        worker_session=lambda actor: {"sam": "sess_sam"}.get(actor))
    engine, rec = _team_engine(tmp_path, [
        _calls(("c1", "create_item", {"title": "Invoice PDF", "criteria": "tests pass"})),
        _calls(("c2", "assign", {"item": 1, "assignee": "sam"}),
               ("c3", "comment", {"item": 1, "body": "use the new template"}),
               ("c4", "list_items", {})),
        "assigned",
    ], tools)
    _run(engine, "plan the work")
    store.close()
    d = rec.as_dict()
    team = {s["name"]: s["attrs"].get("team") for s in d["steps"] if s["kind"] == "tool"}
    assert team["create_item"] == {"team_id": "team-1", "item_id": "1"}
    assert team["assign"] == {"team_id": "team-1", "item_id": "1", "worker_session_id": "sess_sam"}
    assert team["comment"] == {"team_id": "team-1", "item_id": "1"}
    assert team["list_items"] is None  # a read, not a team action
    # Ids only: no title, criteria or comment text in the step.
    assert "Invoice" not in json.dumps([s["attrs"] for s in d["steps"]])
    _check(d)


def test_an_approved_propose_team_names_the_worker_sessions_it_created(tmp_path):
    engine, rec = _team_engine(tmp_path, [
        _calls(("p1", "propose_team", proposals.team_proposal())), "staffed",
    ], [propose_team_tool()])

    async def approve(args, call_id):
        return {"approved": True, "team_id": "team-1",
                "workers": [{"actor": "sam", "session_id": "sess_sam"}, {"actor": "kim", "session_id": "sess_kim"}]}

    engine.team_approver = approve
    _run(engine, "staff it")
    d = rec.as_dict()
    [step] = [s for s in d["steps"] if s["kind"] == "tool" and s["name"] == "propose_team"]
    assert step["attrs"]["team"] == {"team_id": "team-1", "worker_session_ids": ["sess_sam", "sess_kim"]}
    _check(d)


class _Steps:
    def __init__(self):
        self.updates = []

    def update_step(self, step_id, attrs=None, content=None):
        self.updates.append((step_id, attrs))


def _in_tool_run(fn):
    rec = _Steps()
    outer = set_tool_run(ToolRun(step_id="step-1", turn_id="turn-1", view=None, recording=rec))
    try:
        result = fn()
    finally:
        set_tool_run(outer)
    return result, [a["team"] for _, a in rec.updates]


def test_note_team_does_nothing_outside_a_recorded_tool_call():
    note_team("team-1", item_id=3)  # no tool run on this thread: nothing to write to
    _, seen = _in_tool_run(lambda: note_team("", item_id=3))
    assert seen == []  # no team: nothing recorded


def _manager(tmp_path, monkeypatch, provider=None):
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "repo").mkdir(exist_ok=True)
    return SessionManager(workspace=str(tmp_path / "repo"), data_dir=tmp_path / "data", provider=provider)


def _team(mgr):
    return mgr.teams.create(space="proj", lead_session="sess_lead", lead_actor="lead",
                            workers=[TeamWorker(actor="sam", persona="swe-worker", session_id="sess_sam"),
                                     TeamWorker(actor="kim", persona="swe-worker", session_id="sess_kim")])


def test_steer_worker_and_decide_worker_call_name_the_worker_session(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    team = _team(mgr)
    steer = mgr._steer_tool("sess_lead")
    _, seen = _in_tool_run(lambda: steer(worker="kim", message="switch to the API docs"))
    assert seen == [{"team_id": team.team_id, "worker_session_id": "sess_kim"}]
    _, seen = _in_tool_run(lambda: steer(worker="nobody", message="x"))
    assert seen == [{"team_id": team.team_id}]  # the team, but no such worker
    result, seen = _in_tool_run(lambda: mgr.decide_worker_call("sess_lead", "sam", "call-9", "allow"))
    assert "error" in result  # no parked call: the ids are still on the step
    assert seen == [{"team_id": team.team_id, "worker_session_id": "sess_sam"}]
    asyncio.run(mgr.aclose())


def test_post_chat_carries_the_team_and_the_item_it_records_on(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    team = _team(mgr)
    post = mgr._post_chat_tool("sess_sam", "worker")
    _, seen = _in_tool_run(lambda: post(text="@lead done?", record_on_item=4))
    assert seen == [{"team_id": team.team_id, "item_id": "4"}]
    asyncio.run(mgr.aclose())


# -- E19a: who woke a worker's turn -------------------------------------------------------------

def _woken(mgr, session_id, message, source, wake=None):
    asyncio.run(mgr.deliver_to_session(session_id, message, source=source, wake=wake))
    turns = mgr.recordings_store.load_recording(session_id)["turns"]
    return turns[-1]


def test_a_worker_turn_records_who_woke_it(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, provider=_Script(["on it", "ok", "seen", "hi"]))
    team = _team(mgr)
    mgr.get_engine("sess_sam", agent="chat")
    steer = _woken(mgr, "sess_sam", "[Lead] switch to the API docs",
                   {"connector": "team", "sender_name": "lead"})
    assert steer["trigger"] == "team_wake"  # V2-30: not `channel`
    assert steer["woken_by"] == {"lead_session_id": "sess_lead", "team_id": team.team_id,
                                 "item_ids": [], "via": "steer"}
    rows = [{"item": 3, "kind": "assigned"}, {"item": 5, "kind": "comment"}, {"item": 3, "kind": "moved"}]
    board = mgr._woken_by("sess_sam", {"connector": "board", "board": {"rows": rows}})
    assert board == {"lead_session_id": "sess_lead", "team_id": team.team_id,
                     "item_ids": ["3", "5"], "via": "board"}  # batched: every item, once
    mention = mgr._woken_by("sess_sam", {"connector": "board", "board": {"rows": [{"kind": "chat"}]}})
    assert mention["via"] == "mention"
    assert mgr._woken_by("sess_sam", None, wake=object())["via"] == "timer"
    # A lead's own turns, and sessions outside a team, have none.
    assert mgr._woken_by("sess_lead", {"connector": "team"}) is None
    mgr.get_engine("sess_solo", agent="chat")
    solo = _woken(mgr, "sess_solo", "hello", {"connector": "slack", "sender_name": "pat"})
    assert solo["trigger"] == "channel" and "woken_by" not in solo
    _check(mgr.recordings_store.load_recording("sess_sam"))
    asyncio.run(mgr.aclose())


# -- E19b: worker rows under the lead's turns ---------------------------------------------------

def _turn(tid, sid, start, dur, *, status="completed", woken_by=None, seq=1):
    t = {"turn_id": tid, "session_id": sid, "seq": seq, "kind": "run", "trigger": "user", "started_at": start,
         "duration_ms": dur, "status": status, "incomplete": False, "approval_mode": "interactive",
         "attended": True, "reviewer": {"active": False, "shadow": False, "kind": None, "model": None},
         "stamp": {}, "first_message_id": None, "last_message_id": None, "schema_version": 1}
    if woken_by:
        t["trigger"], t["woken_by"] = "team_wake", woken_by
    return t


def _model(step_id, tid, sid, start, tokens, purpose="main"):
    return {"step_id": step_id, "turn_id": tid, "session_id": sid, "parent_step_id": None, "seq": 1,
            "kind": "model", "name": "model", "started_at": start, "duration_ms": 10, "status": "ok",
            "call_id": None, "call_index": None, "message_id": None, "shadow": False, "after_turn": False,
            "attrs": {"purpose": purpose, "model": "openai:model-x",
                      "usage": {"reported": True, "input_fresh": tokens - 1, "output": 1}},
            "content": {}}


def _by(team_id="team-1", items=("3",), via="board"):
    return {"lead_session_id": "sess_lead", "team_id": team_id, "item_ids": list(items), "via": via}


def test_worker_turns_are_listed_under_the_lead_turn_they_started_in():
    lead = [_turn("L1", "sess_lead", 1000, 1000, seq=1), _turn("L2", "sess_lead", 5000, 1000, seq=2),
            _turn("L3", "sess_lead", 9000, None, status="running", seq=3)]
    workers = [
        (_turn("W1", "sess_sam", 1500, 400, woken_by=_by(), seq=3), [_model("m1", "W1", "sess_sam", 1500, 12000)]),
        (_turn("W2", "sess_kim", 1600, 400, woken_by=_by(items=("3", "4"))), []),  # one action, two workers
        (_turn("W3", "sess_sam", 3000, 100, woken_by=_by(), seq=4), []),  # after L1 ended: in L2
        (_turn("W4", "sess_sam", 9500, None, status="running", woken_by=_by(via="steer"), seq=5), []),
    ]
    rows = read_api.worker_rows(lead, workers, names={"sess_sam": "sam", "sess_kim": "kim"}.get)
    assert [r["turn_id"] for r in rows["L1"]] == ["W1", "W2"]
    assert [r["turn_id"] for r in rows["L2"]] == ["W3"]
    assert [r["turn_id"] for r in rows["L3"]] == ["W4"]  # the running lead turn is open-ended
    assert rows["L1"][0] == {"session_id": "sess_sam", "turn_id": "W1", "seq": 3, "name": "sam",
                             "started_at": 1500, "duration_ms": 400, "status": "completed", "tokens": 12000,
                             "at_least": False, "team_id": "team-1", "item_ids": ["3"], "via": "board"}
    assert rows["L1"][1]["item_ids"] == ["3", "4"]


def test_a_worker_turn_after_the_last_lead_turn_waits_for_the_next():
    lead = [_turn("L1", "sess_lead", 1000, 1000)]
    rows = read_api.worker_rows(lead, [(_turn("W1", "sess_sam", 2500, 10, woken_by=_by()), [])])
    assert rows == {"L1": []}


def test_the_read_api_lists_worker_rows_and_keeps_their_tokens_out_of_the_leads_pill(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    _team(mgr)
    store = mgr.recordings_store
    store.write_turn(_turn("L1", "sess_lead", 1000, 5000))
    store.write_step(_model("lm", "L1", "sess_lead", 1100, 300))
    store.write_turn(_turn("W1", "sess_sam", 2000, 1000, woken_by=_by()))
    store.write_step(_model("wm", "W1", "sess_sam", 2100, 12000))
    store.write_turn(_turn("X1", "sess_other", 2000, 1000, woken_by={**_by(), "lead_session_id": "sess_x"}))
    [turn] = mgr.recording_turns("sess_lead")["turns"]
    [row] = turn["workers"]
    assert (row["name"], row["session_id"], row["tokens"]) == ("sam", "sess_sam", 12000)
    assert turn["usage"]["pill"] == 300  # the worker's 12000 are display only
    assert mgr.recording_usage("sess_lead")["pill"] == 300
    assert "workers" not in mgr.recording_turns("sess_sam")["turns"][0]
    asyncio.run(mgr.aclose())


# -- E19c: TeamView's totals from the recording (V2-29) -----------------------------------------

def test_team_view_totals_count_helper_tokens_from_the_recording(tmp_path, monkeypatch):
    from coworker.sessions import SessionRecord

    mgr = _manager(tmp_path, monkeypatch)
    mgr.session_store.save(SessionRecord(session_id="sess_lead", workspace=str(tmp_path / "repo"),
                                         model="model-x", mode="interactive", agent="swe-lead"))
    team = mgr.teams.create(space=mgr._board_space("sess_lead"), lead_session="sess_lead", lead_actor="lead",
                            workers=[TeamWorker(actor="sam", persona="swe-worker", session_id="sess_sam")])
    use = {"input": 29, "cache_read": 0, "output": 1, "cache_write": 0}
    mgr.session_store.save(SessionRecord(
        session_id="sess_sam", workspace=str(tmp_path / "repo"), model="model-x", mode="interactive",
        agent="swe-worker", messages=[
            {"role": "assistant", "content": "before recording", "ts": 10, "usage": use},  # 30
            {"role": "assistant", "content": "recorded", "ts": 25, "usage": use},           # 30, in the recording
        ]))
    store = mgr.recordings_store
    store.write_turn(_turn("W1", "sess_sam", 20_000, 10_000, woken_by=_by()))
    store.write_step(_model("wm", "W1", "sess_sam", 25_000, 30))
    store.write_step({**_model("wh", "W1", "sess_sam", 25_500, 100, purpose="helper"), "seq": 2})
    summary = mgr.team_summary(team.team_id)
    sam = next(w for w in summary["workers"] if w["actor"] == "sam")
    # 30 from before the first recorded turn (transcript), 30 main + 100 helper (recording).
    assert sum(sam["tokens"].values()) == 160
    assert sum(sam["tokens"].values()) == mgr.recording_usage("sess_sam")["pill"] + 30
    asyncio.run(mgr.aclose())


# -- export: a worker turn's span joins the lead's trace ----------------------------------------

def _root_span(turn):
    spans = export_spans.build_turn_spans(turn, [], sw=export_spans.switches_from({}), dialect="otlp",
                                          get_piece=lambda k: None, masker=export_spans.Masker())
    return next(s for s in spans if s["parent_span_id"] is None)


def test_a_worker_turns_span_names_its_team_and_lead():
    root = _root_span(_turn("W1", "sess_sam", 2000, 1000, woken_by=_by()))
    assert root["attrs"]["openworker.team.id"] == "team-1"
    assert root["attrs"]["openworker.lead.session.id"] == "sess_lead"
    assert "openworker.team.id" not in _root_span(_turn("L1", "sess_lead", 1000, 5000))["attrs"]
