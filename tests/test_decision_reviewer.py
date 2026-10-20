"""Decision-model reviewers for Auto-Approve (OPE-224): OpenAI Decisions and TypeSafe Jev.

Everything here is offline. Transports are fakes; no test calls a vendor."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from coworker import reviewer as reviewer_mod
from coworker.config import _GLOBAL_ONLY_FIELDS
from coworker.decision_reviewer import (
    AGENT_FILE_FLAG,
    ASK_SIGNALS,
    BACKENDS,
    COMBINED_NAMES,
    DECISIONS_QUESTIONS,
    JEV_QUESTIONS,
    JEV_SPLITS,
    STOP_SIGNALS,
    DecisionReviewer,
    JevBackend,
    OpenAIDecisionsBackend,
    build_decision_reviewer,
    combine_answers,
    derive_jev,
    evidence_state,
    make_auto_reviewer,
    normalize_reviewer_choice,
)
from coworker.events import EventType
from coworker.server.app import create_app
from coworker.server.manager import SessionManager
from test_auto_approve import _engine, _run, _tool_turn
from coworker.providers import AssistantTurn

DECISIONS = OpenAIDecisionsBackend
JEV = JevBackend


class _Recorder:
    def __init__(self, reply):
        self.reply = reply
        self.bodies: list[tuple[str, dict, dict]] = []

    def __call__(self, url, headers, body):
        self.bodies.append((url, headers, body))
        return self.reply(url, body)


def _answers(values: dict[str, float]):
    """A reply in whichever vendor shape the URL names, giving `values` (default 0.02)."""

    def reply(url, body):
        qs = body["questions"]
        names = list(qs) if isinstance(qs, dict) else [q["name"] for q in qs]
        p = {n: values.get(n, 0.02) for n in names}
        if url.endswith("/v1/decisions"):
            return 200, {"model": "m", "answers": [
                {"name": n, "type": "predicate", "probability": v} for n, v in p.items()
            ], "usage": {"input_tokens": 7}}
        return 200, {"model": "m", "answers": {n: {"type": "noul", "noul": v} for n, v in p.items()},
                     "usage": {"input_tokens": 7}}

    return reply


def _review(rv, **kw):
    args = dict(request="run the test suite", history=[], tool_name="run_shell", arguments={"command": "pytest -q"})
    args.update(kw)
    return asyncio.run(rv.review(**args))


def _rv(backend_cls, values=None, reply=None):
    rec = _Recorder(reply or _answers({"requested": 0.97, **(values or {})}))
    return DecisionReviewer(backend_cls(api_key="k", transport=rec)), rec


# -- what each vendor is sent --------------------------------------------------------------


def test_decisions_request_is_plain_predicates_over_json_text():
    rv, rec = _rv(DECISIONS)
    _review(rv)
    url, headers, body = rec.bodies[0]
    assert url.endswith("/v1/decisions") and body["model"] == "gpt-6-luna"
    assert headers["Authorization"] == "Bearer k"
    assert isinstance(body["input"], str) and json.loads(body["input"])["current_request"] == "run the test suite"
    assert [q["name"] for q in body["questions"]] == [q.name for q in DECISIONS_QUESTIONS]
    for q in body["questions"]:
        assert set(q) == {"name", "type", "instructions"} and q["type"] == "predicate"
        assert "`" not in q["instructions"]


def test_jev_request_is_structured_nouls_with_criteria():
    rv, rec = _rv(JEV)
    _review(rv)
    url, _, body = rec.bodies[0]
    assert url.endswith("/v1/systemone") and body["model"] == "jev-1.13.0"
    assert isinstance(body["state"], dict)
    assert set(body["questions"]) == {q.name for q in JEV_QUESTIONS}
    for name, spec in body["questions"].items():
        assert spec["type"] == "noul"
        assert spec["instructions"]["question"]
        assert {"what", "examples"} <= set(spec["criteria"]["true"]), name
        assert {"what", "examples"} <= set(spec["criteria"]["false"]), name


def test_evidence_state_carries_facts_not_provenance():
    state = evidence_state(
        known_world="KNOWN WORLD\n  folder /repo [read-write]", history=[{"text": "set up the repo"}],
        request="run it", tool_name="run_shell", arguments={"command": "make"},
        provenance="scripts/x.sh was written by the agent this session",
    )
    assert state["environment"]["working_folders"] == [{"path": "/repo", "access": "read-write"}]
    assert "provenance" not in json.dumps(state) and "written by the agent" not in json.dumps(state)


# -- frozen settings ------------------------------------------------------------------------


def test_cut_offs_and_models_are_frozen_per_backend():
    assert (DECISIONS.run_threshold, DECISIONS.risk_threshold, DECISIONS.stop_threshold) == (0.80, 0.40, 0.60)
    assert (JEV.run_threshold, JEV.risk_threshold, JEV.stop_threshold) == (0.90, 0.40, 0.60)
    assert DECISIONS.default_model == "gpt-6-luna"
    assert JEV.default_model == "jev-1.13.0"  # pinned, not jev-latest (the docs: versioned ids carry the patch number)


# -- combining answers ---------------------------------------------------------------------


def _p(**over):
    p = {n: 0.02 for n in COMBINED_NAMES}
    p["requested"] = 0.97
    p.update(over)
    return p


def _combine(p, run=0.80):
    return combine_answers(p, run_threshold=run, stop_threshold=0.60, risk_threshold=0.40)[0]


def test_plain_requested_action_is_allowed():
    assert _combine(_p()) == "allow"


@pytest.mark.parametrize("signal", STOP_SIGNALS)
def test_every_stop_signal_stops(signal):
    assert _combine(_p(**{signal: 0.9})) == "deny"


@pytest.mark.parametrize("signal", ASK_SIGNALS)
def test_every_ask_signal_asks(signal):
    assert _combine(_p(**{signal: 0.5})) == "unsure"


def test_not_requested_enough_asks():
    assert _combine(_p(requested=0.85), run=0.90) == "unsure"


@pytest.mark.parametrize("outside, asked, verdict", [(0.9, 0.9, "allow"), (0.9, 0.1, "unsure"), (0.1, 0.1, "allow")])
def test_outside_write_counts_only_when_not_asked_for(outside, asked, verdict):
    assert _combine(_p(outside_folders=outside, outside_change_requested=asked)) == verdict


def test_agent_made_file_asks():
    assert _combine({**_p(), AGENT_FILE_FLAG: 1.0}) == "unsure"


def test_missing_or_bad_answers_ask():
    p = _p()
    del p["moves_money"]
    assert _combine(p) == "unsure"
    assert _combine(_p(broader=1.5)) == "unsure"


@pytest.mark.parametrize("parts, derived, expected", [
    ({"shared_item": 0.9, "item_written": 0.1, "one_meaning": 0.1}, "target_unpinned", 0.9 * 0.9 * 0.9),
    ({"named_target": 0.95, "acts_on_named": 0.1}, "target_mismatch", 0.95 * 0.9),
    ({"has_recipient": 0.9, "recipient_written": 0.95}, "outside_recipient", 0.9 * 0.05),
    ({"sets_hour": 0.98, "hour_given": 0.02}, "agent_chose_time", 0.98 * 0.98),
    ({"named_place": 0.9, "acts_in_named_place": 0.1}, "place_mismatch", 0.9 * 0.9),
])
def test_jev_split_parts_combine_by_product(parts, derived, expected):
    base = {q.name: 0.02 for q in JEV_QUESTIONS}
    base.update(parts)
    assert derive_jev(base)[derived] == pytest.approx(expected, abs=0.02)


def test_jev_missing_part_fails_closed():
    base = {q.name: 0.02 for q in JEV_QUESTIONS}
    base["requested"] = 0.97
    del base["acts_in_named_place"]
    assert _combine(derive_jev(base), run=0.90) == "unsure"
    for cond, nots in JEV_SPLITS.values():
        assert {cond, *nots} <= {q.name for q in JEV_QUESTIONS}


def test_jev_examples_are_not_eval_rows():
    from scripts import eval_reviewer as ev

    requests = {r.user_request.strip().lower() for n in ev.CORPORA for r in ev.load_corpus(n)}
    for q in JEV_QUESTIONS:
        for side in (q.true, q.false):
            for ex in side.get("examples", []):
                assert ex.strip().lower() not in requests, (q.name, ex)


# -- end to end through a backend ----------------------------------------------------------


@pytest.mark.parametrize("backend", [DECISIONS, JEV])
def test_end_to_end_allow_ask_stop(backend):
    assert _review(_rv(backend)[0]).verdict == "allow"
    assert _review(_rv(backend, {"moves_money": 0.9})[0]).verdict == "unsure"
    assert _review(_rv(backend, {"contrary": 0.95})[0]).verdict == "deny"


def test_decisions_place_mismatch_stops():
    assert _review(_rv(DECISIONS, {"place_mismatch": 0.9})[0]).verdict == "deny"


def test_jev_wrong_place_stops():
    assert _review(_rv(JEV, {"named_place": 0.95, "acts_in_named_place": 0.05})[0]).verdict == "deny"


@pytest.mark.parametrize("backend", [DECISIONS, JEV])
def test_no_reason_for_the_card_but_a_full_one_for_the_audit(backend):
    v = _review(_rv(backend, {"broader": 0.9})[0])
    assert v.verdict == "unsure" and v.card_reason == ""
    assert "broader" in v.reason and v.probabilities["broader"] == pytest.approx(0.9)


# -- failing closed -------------------------------------------------------------------------


@pytest.mark.parametrize("reply", [
    lambda url, body: (500, {"error": "boom"}),
    lambda url, body: (200, "not json"),
    lambda url, body: (200, {"answers": {}}),
])
@pytest.mark.parametrize("backend", [DECISIONS, JEV])
def test_machinery_failures_are_unsure_with_error(backend, reply):
    v = _review(_rv(backend, reply=reply)[0])
    assert v.verdict == "unsure" and v.error and v.card_reason == ""


def test_missing_key_is_unsure_with_error(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    v = _review(DecisionReviewer(JEV(transport=_Recorder(_answers({})))))
    assert v.verdict == "unsure" and v.error and "no API key" in v.reason


def test_refusal_is_unsure_and_counted_apart():
    def refuse(url, body):
        return 200, {"model": "m", "answers": [
            {"name": q["name"], "type": "refusal"} if q["name"] == "exposes_secret"
            else {"name": q["name"], "type": "predicate", "probability": 0.02}
            for q in body["questions"]
        ]}

    v = _review(_rv(DECISIONS, reply=refuse)[0])
    assert v.verdict == "unsure" and v.refused and not v.error


def test_key_saved_later_is_used_without_a_rebuild(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    class _Secrets:
        store: dict = {}

        def get(self, name):
            return self.store.get(name)

    secrets = _Secrets()
    rec = _Recorder(_answers({"requested": 0.97}))
    rv = DecisionReviewer(JEV(secrets=secrets, transport=rec))
    assert _review(rv).error  # no key yet
    secrets.store["provider:typesafe"] = {"api_key": "later"}
    assert _review(rv).verdict == "allow"
    assert rec.bodies[-1][1]["Authorization"] == "Bearer later"


# -- choosing the reviewer ------------------------------------------------------------------


def test_factory_builds_the_chosen_reviewer():
    session = make_auto_reviewer("session", provider=None, model="m", known_world="")
    assert isinstance(session, reviewer_mod.Reviewer)
    for name, cls in BACKENDS.items():
        rv = make_auto_reviewer(name, provider=None, model="m", known_world="KW", secrets=object())
        assert isinstance(rv, DecisionReviewer) and isinstance(rv.backend, cls) and rv.known_world == "KW"
    assert normalize_reviewer_choice("JEV") == "jev"
    assert normalize_reviewer_choice("nonsense") == "session"
    assert normalize_reviewer_choice(None) == "session"


def test_a_repository_cannot_choose_the_reviewer():
    assert "auto_approve_reviewer" in _GLOBAL_ONLY_FIELDS


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))


def test_setting_defaults_to_the_session_model_and_round_trips(client):
    assert client.get("/v1/settings").json()["auto_approve_reviewer"] == "session"
    r = client.post("/v1/settings/auto-approve-reviewer", json={"auto_approve_reviewer": "openai-decisions"}).json()
    assert r["ok"] and r["auto_approve_reviewer"] == "openai-decisions"
    assert client.get("/v1/settings").json()["auto_approve_reviewer"] == "openai-decisions"
    bad = client.post("/v1/settings/auto-approve-reviewer", json={"auto_approve_reviewer": "gpt"}).json()
    assert not bad["ok"]


def test_reviewer_key_is_saved_and_never_echoed(client):
    s = client.get("/v1/settings").json()
    assert s["auto_approve_reviewer_keys"] == {"jev": False, "openai-decisions": False}
    r = client.post("/v1/settings/auto-approve-reviewer-key", json={"reviewer": "jev", "api_key": "ts-secret"}).json()
    assert r["ok"] and r["auto_approve_reviewer_keys"]["jev"] is True
    assert "ts-secret" not in json.dumps(r) and "ts-secret" not in json.dumps(client.get("/v1/settings").json())
    assert not client.post("/v1/settings/auto-approve-reviewer-key", json={"reviewer": "jev", "api_key": ""}).json()["ok"]


# -- what the approval card shows ------------------------------------------------------------


class _DecisionLike:
    """A reviewer returning DecisionVerdicts, scripted."""

    def __init__(self, verdict: str, *, error: bool = False):
        self.verdict, self.error = verdict, error

    async def review(self, **_kw):
        from coworker.decision_reviewer import DecisionVerdict

        return DecisionVerdict(self.verdict, "requested 0.97; broader 0.90", error=self.error)


def _card(tmp_path, reviewer):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "git push"})), AssistantTurn(text="ok", finish_reason="stop")],
    )
    engine.reviewer = reviewer
    events = _run(engine)
    cards = [ev for ev in events if ev.type == EventType.PERMISSION_REQUIRED]
    return cards[0].data, rows


def test_decision_reviewer_ask_shows_no_reason_on_the_card(tmp_path):
    data, rows = _card(tmp_path, _DecisionLike("unsure"))
    assert "reviewer_unsure" not in data
    assert data["escalation"] == {"kind": "reviewer_asks", "reason": ""}
    # The audit still records what fired.
    assert any(r.get("stage") == "reviewer_verdict" and "broader" in r.get("reason", "") for r in rows)


def test_decision_reviewer_failure_says_it_could_not_review(tmp_path):
    data, _ = _card(tmp_path, _DecisionLike("unsure", error=True))
    assert data["escalation"] == {"kind": "reviewer_unavailable", "reason": ""}


def test_session_reviewer_reason_still_shows(tmp_path):
    from test_auto_approve import _FakeReviewer

    data, _ = _card(tmp_path, _FakeReviewer({"run_shell": "unsure"}))
    assert data["reviewer_unsure"] == "scripted unsure"


# -- the eval script ------------------------------------------------------------------------


@pytest.mark.parametrize("backend", sorted(BACKENDS))
def test_eval_stub_runs_each_decision_reviewer(backend, capsys):
    from scripts import eval_reviewer as ev

    rc = ev.main(["--reviewer", backend, "--stub", "--corpus", "reviewer_actions"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert f"{backend}:" in out


# -- shadow results: summary and CSV export ---------------------------------------------------


def _shadow(store, call, verdict, reviewer="jev", decision=None, approval="once"):
    store.append({"session_id": "s1", "tool": "run_shell", "arguments": {"command": f"cmd {call}"},
                  "stage": "reviewer_shadow", "status": verdict, "reason": f"r {call}",
                  "call_id": call, "reviewer": reviewer})
    if decision:
        store.append({"session_id": "s1", "tool": "run_shell", "arguments": {}, "stage": "approval_resolved",
                      "status": decision, "approval": approval, "call_id": call})


def test_shadow_summary_counts_and_matches(tmp_path):
    from coworker.audit import AuditStore

    store = AuditStore(tmp_path / "a.db")
    _shadow(store, "c1", "allow", decision="approved")   # run, you allowed: match
    _shadow(store, "c2", "allow", decision="denied")     # run, you denied: no match
    _shadow(store, "c3", "deny", decision="denied")      # stop, you denied: match
    _shadow(store, "c4", "unsure", decision="approved")  # asked: not counted either way
    _shadow(store, "c5", "allow")                        # never answered: not counted
    _shadow(store, "c6", "deny", decision="denied", approval="auto_refused")  # nobody decided
    _shadow(store, "c7", "allow", reviewer="openai-decisions", decision="approved")  # other reviewer
    s = store.shadow_summary("jev")
    assert (s["checks"], s["run"], s["asked"], s["stopped"]) == (6, 3, 1, 2)
    assert (s["decided"], s["matched"]) == (3, 2)
    assert store.shadow_summary("openai-decisions")["checks"] == 1


def test_shadow_export_writes_a_readable_csv(client, tmp_path):
    import csv

    client.post("/v1/settings/auto-approve-reviewer", json={"auto_approve_reviewer": "jev"})
    mgr = client.app.state.manager if hasattr(client.app.state, "manager") else None
    store = mgr.audit_store if mgr else None
    if store is None:
        pytest.skip("manager not reachable from the test client")
    _shadow(store, "c1", "allow", decision="denied")
    summary = client.get("/v1/settings/auto-approve-shadow-summary").json()
    assert summary["reviewer"] == "jev" and summary["checks"] == 1 and summary["matched"] == 0
    r = client.post("/v1/settings/auto-approve-shadow-export").json()
    assert r["ok"] and r["rows"] == 1 and r["path"].endswith(".csv")
    with open(r["path"], encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["reviewer would"] == "run" and rows[0]["your decision"] == "denied" and rows[0]["matched"] == "no"
    assert "cmd c1" in rows[0]["action"]


def test_engine_tags_shadow_rows_with_the_reviewer(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": "git push"})), AssistantTurn(text="ok", finish_reason="stop")],
        mode=__import__("coworker.permissions", fromlist=["Mode"]).Mode.INTERACTIVE,
    )
    engine.reviewer = _DecisionLike("allow")
    engine.reviewer_shadow = True
    engine.reviewer_choice = "jev"
    _run(engine)
    asyncio.run(engine.drain_shadow_reviews())
    shadow = [r for r in rows if r.get("stage") == "reviewer_shadow"]
    assert shadow and shadow[0]["reviewer"] == "jev"
