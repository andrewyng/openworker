"""Workers' connectors, approval mode, and the two connector gates (spec §11.6,
owner-ruled 2026-09-05): workers start with no connectors; the staffing card decides
connectors + approval mode per worker within the persona's ceiling; a lead may ask
for a connector later (grant_connector) and any connector-capable coworker may ask
the human to connect a service (request_connector). No network, no LLM."""

from __future__ import annotations

import pytest

from coworker.agents.base import Agent
from coworker.engine import EventType, TurnEngine
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.server.manager import SessionManager
from coworker.sessions import SessionRecord
from coworker.teams.store import WORKER_WAITING
from coworker.tools import ToolRegistry


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    ws = tmp_path / "repo"
    ws.mkdir()
    m = SessionManager(data_dir=tmp_path / "data", workspace=str(ws))
    # GitHub + Slack connected on this box (manual profiles are enough for the gate).
    m.secrets.put("github:default", {"token": "ghp_test", "enabled": True})
    m.secrets.put("slack:default", {"bot_token": "xoxb", "app_token": "xapp", "enabled": True})
    yield m


def _lead(manager, sid="lead-sid", agent="swe-lead"):
    manager.session_store.save(
        SessionRecord(session_id=sid, workspace=manager.default_workspace, model="m", mode="interactive", messages=[], agent=agent)
    )


# -- the ceiling and the default ---------------------------------------------------------


def test_workers_start_with_no_connectors_and_the_offer_is_the_default_set(manager):
    # swe-worker declares [github]: that is its DEFAULT set (worker-connector-grants spec,
    # 2026-09-17), not a ceiling. GitHub is what the lead may suggest unprompted; Slack is
    # connected here but outside the default set, so only a human can add it.
    assert manager.connector_offer("swe-worker") == ["github"]
    # (`browser` is the built-in, always-connected connector.)
    assert manager.connector_lists("swe-worker") == {"ready": ["github"], "connectable": [], "other_connected": ["browser", "slack"]}
    assert manager.connector_offer("design-worker") == []  # declares nothing
    assert manager.connector_lists("design-worker")["other_connected"] == ["browser", "github", "slack"]
    assert manager.other_connected() == ["browser", "github", "slack"]
    _lead(manager)
    result = manager.create_team("lead-sid", [{"persona": "swe-worker", "name": "nia"}])
    assert result["approved"] is True
    sid = result["workers"][0]["session_id"]
    # Nothing on until the human ticks it — even though GitHub is connected and declared.
    assert manager.effective_connectors(sid, "swe-worker") == set()
    assert result["workers"][0]["connectors"] == []
    assert result["workers"][0]["approvals"] == "follow the lead"


def test_staffing_card_ticks_are_the_grant_even_beyond_the_default_set(manager):
    _lead(manager)
    result = manager.create_team(
        "lead-sid",
        [
            # The human ticked GitHub (default set) AND Slack (outside it): both apply — the
            # declaration is the default, not the ceiling. Jira is not connected here, so
            # that tick is dropped. A stray `approval_mode` (older client) is ignored.
            {
                "persona": "swe-worker", "name": "nia", "connectors": ["github", "slack", "jira"],
                "connector_reasons": {"slack": 'you asked: "post the summary in Slack"'},
                "approval_mode": "bypass-approvals",
            },
            {"persona": "test-worker", "name": "checks", "connectors": []},
        ],
        approved_by="rohit@example.com",
    )
    assert result["approved"] is True
    nia, checks = result["workers"]
    assert nia["connectors"] == ["github", "slack"] and checks["connectors"] == []
    # Workers are stored as "always ask"; the lead's regime is mirrored live (below).
    assert manager.session_store.load(nia["session_id"]).mode == "interactive"
    assert manager.effective_connectors(nia["session_id"], "swe-worker") == {"github", "slack"}
    # Going beyond the default set is recorded per session and audited with who approved it.
    assert manager.session_connector_extensions.get(nia["session_id"]) == {"slack": True}
    rows = [r for r in manager.audit_store.list(session_id=nia["session_id"]) if r.get("stage") == "connector_extended"]
    assert len(rows) == 1 and rows[0]["connector"] == "slack" and rows[0]["approved_by"] == "rohit@example.com"
    assert "post the summary in Slack" in rows[0]["reason"]


def test_org_policy_is_the_only_ceiling_on_worker_connectors(manager):
    manager.org_policy = {"worker_connectors": {"deny": ["slack"]}}
    assert manager.connector_lists("swe-worker")["other_connected"] == ["browser"]
    assert manager.other_connected() == ["browser", "github"]
    _lead(manager)
    nia = manager.create_team("lead-sid", [{"persona": "swe-worker", "name": "nia", "connectors": ["github", "slack"]}])["workers"][0]
    assert nia["connectors"] == ["github"]  # a forced tick does not pass policy
    assert "blocked" in manager.grant_worker_connector("lead-sid", "nia", "slack")["error"]


@pytest.mark.asyncio
async def test_lead_suggestions_outside_the_default_set_need_the_users_words(manager):
    import asyncio

    _lead(manager)
    approve = manager.inbox_team_approver("lead-sid", "swe-lead")
    # No reason for Slack (outside swe-worker's default set) and Jira is not connected:
    # the proposal bounces back to the lead with what to do, and nothing is parked.
    from proposal_fixtures import team_proposal
    bounced = await approve(team_proposal([{"persona": "swe-worker", "name": "nia", "connectors": ["slack", "jira"]}]), "t0")
    assert bounced["approved"] is False
    assert any("slack" in p and "quote" in p for p in bounced["problems"])
    assert any("jira" in p and "request_connector" in p for p in bounced["problems"])
    assert not manager.inbox.list(session_id="lead-sid", state="pending")
    # With the user's words as the reason it reaches the card, carrying what the card needs.
    members = [{"persona": "swe-worker", "name": "nia", "connectors": ["GitHub", "slack"],
                "connector_reasons": {"github": "pushes the branch", "slack": 'you asked: "post the summary in Slack"'}}]
    task = asyncio.create_task(approve(team_proposal(members), "t1"))
    for _ in range(50):
        await asyncio.sleep(0.02)
        pending = manager.inbox.list(session_id="lead-sid", state="pending")
        if pending:
            break
    data = pending[0].data
    assert data["members"][0]["connectors"] == ["github", "slack"]  # normalised
    assert data["offer"] == {"swe-worker": ["github"]} and data["other_connected"] == ["browser", "github", "slack"]
    assert "lead_mode" in data
    # The human keeps both ticks.
    manager.inbox.resolve(pending[0].id, '{"approved": true, "members": [{"persona": "swe-worker", "name": "nia", "connectors": ["github", "slack"]}]}')
    result = await task
    assert result["approved"] is True and result["workers"][0]["connectors"] == ["github", "slack"]


# -- approvals follow the lead ---------------------------------------------------------------


def test_worker_engine_mirrors_the_lead_mode_live(manager):
    from coworker.permissions import Mode

    _lead(manager)
    sid = manager.create_team("lead-sid", [{"persona": "swe-worker", "name": "nia"}])["workers"][0]["session_id"]
    engine = manager.get_engine(sid)
    assert engine.permissions.mode is Mode.INTERACTIVE  # the lead is Manual
    assert engine.is_attended is not None and engine.is_attended() is False
    # The lead flips to auto-approve → the worker's next engine use runs the reviewer path
    lead = manager.session_store.load("lead-sid")
    lead.mode = "auto-approve"
    manager.session_store.save(lead)
    engine = manager.get_engine(sid)  # cached engine, re-synced
    assert engine.permissions.mode is Mode.AUTO_APPROVE and engine.is_attended() is True
    # Legacy "auto" on the lead means bypass; a read-only lead still lets workers ask
    lead.mode = "auto"
    manager.session_store.save(lead)
    assert manager.get_engine(sid).permissions.mode is Mode.BYPASS_APPROVALS
    lead.mode = "plan"
    manager.session_store.save(lead)
    assert manager.get_engine(sid).permissions.mode is Mode.INTERACTIVE
    # A non-worker session is never touched
    assert manager.lead_mode_for("lead-sid") is None


def test_decide_worker_call_resolves_the_parked_prompt(manager):
    _lead(manager)
    result = manager.create_team("lead-sid", [{"persona": "swe-worker", "name": "nia"}])
    sid = result["workers"][0]["session_id"]
    item = manager.inbox.add_approval(sid, "Run `run_shell`?", body="git push", visibility="inline")
    manager.note_worker_waiting(sid, "run_shell", prompt_id=item.id, preview="git push")
    # The lead's wake digest names the worker, the call, and the call_id to answer with.
    team = manager.teams.for_lead_session("lead-sid")
    subs = manager.team_store.subscribed_events(team.space, team.lead_actor)
    message, rows = manager._team_digest(team, [], subs, is_lead=True)
    assert "nia is waiting on your decision" in message and "run_shell — git push" in message
    assert f'decide_worker_call(worker="nia", call_id="{item.id}"' in message
    assert any(r["kind"] == "waiting" and r["prompt_id"] == item.id for r in rows)
    # Guards: not a lead, unknown worker, unknown prompt, wrong decision value
    assert "does not lead" in manager.decide_worker_call("nobody", "nia", item.id, "allow")["error"]
    assert "no worker" in manager.decide_worker_call("lead-sid", "webb", item.id, "allow")["error"]
    assert "no such parked call" in manager.decide_worker_call("lead-sid", "nia", "bogus", "allow")["error"]
    # Deny resolves the prompt; a second answer reports it as already answered
    out = manager.decide_worker_call("lead-sid", "nia", item.id, "deny", "outside the item")
    assert out["ok"] is True and out["decision"] == "deny"
    assert manager.inbox.get(item.id).state == "resolved" and manager.inbox.get(item.id).resolution == "deny"
    assert manager.decide_worker_call("lead-sid", "nia", item.id, "allow")["note"] == "already answered"
    # Allow on a fresh prompt
    item2 = manager.inbox.add_approval(sid, "Run `run_shell`?", body="npm test", visibility="inline")
    assert manager.decide_worker_call("lead-sid", "nia", item2.id, "allow")["decision"] == "allow"
    assert manager.inbox.get(item2.id).resolution == "allow"


def test_decide_tool_is_consequential_and_lead_only(tmp_path):
    from coworker.agent import build_engine
    from coworker.secrets import SecretStore

    secrets = SecretStore(tmp_path / "s.json")
    calls = []
    lead = Agent(name="lead", title="L", system_prompt="x", team="lead")
    eng = build_engine(agent=lead, workspace=tmp_path, provider=_Stub(), secrets=secrets, worker_decider=lambda *a: calls.append(a) or {"ok": True})
    assert "decide_worker_call" in eng.registry.names()
    spec = eng.registry.get("decide_worker_call")
    meta = getattr(spec, "metadata", None) or getattr(getattr(spec, "func", None), "__aisuite_tool_metadata__", None)
    assert meta is not None and meta.requires_approval is True  # a Manual lead's decision asks the human
    worker = Agent(name="w", title="W", system_prompt="x", team="worker")
    assert "decide_worker_call" not in build_engine(agent=worker, workspace=tmp_path, provider=_Stub(), secrets=secrets, worker_decider=lambda *a: {}).registry.names()


# -- grant_connector, the lead's later ask ------------------------------------------------


def test_grant_worker_connector_flips_the_session_and_can_extend_with_a_human_yes(manager):
    _lead(manager)
    result = manager.create_team("lead-sid", [{"persona": "swe-worker", "name": "nia"}])
    sid = result["workers"][0]["session_id"]
    assert manager.effective_connectors(sid, "swe-worker") == set()
    out = manager.grant_worker_connector("lead-sid", "nia", "github")
    assert out == {"approved": True, "worker": "nia", "connector": "github"}
    assert manager.effective_connectors(sid, "swe-worker") == {"github"}
    # Slack is outside swe-worker's default set but connected: the human already said yes
    # on the grant card, so it applies — as a recorded extension.
    out = manager.grant_worker_connector("lead-sid", "nia", "slack", reason='you asked: "ping #billing"', approved_by="rohit@example.com")
    assert out["approved"] is True and manager.effective_connectors(sid, "swe-worker") == {"github", "slack"}
    assert manager.session_connector_extensions.get(sid) == {"slack": True}
    # Not connected, unknown worker, not a lead: all refuse with a reason.
    assert "not connected" in manager.grant_worker_connector("lead-sid", "nia", "jira")["error"]
    assert "no worker" in manager.grant_worker_connector("lead-sid", "webb", "github")["error"]
    assert "does not lead" in manager.grant_worker_connector("nobody", "nia", "github")["error"]


def test_grant_rebuilds_the_worker_tools_after_its_turn(manager):
    _lead(manager)
    sid = manager.create_team("lead-sid", [{"persona": "swe-worker", "name": "nia"}])["workers"][0]["session_id"]
    engine = manager.get_engine(sid)
    assert engine is not None and "github_reply" not in engine.registry.names()
    manager.grant_worker_connector("lead-sid", "nia", "github")
    # Idle: the engine was dropped and rebuilds with the connector's tools.
    rebuilt = manager.get_engine(sid)
    assert rebuilt is not engine and "github_reply" in rebuilt.registry.names()
    # Running: kept until the turn ends, then dropped at mark_idle.
    manager._running_sessions.add(sid)
    manager.session_connections.set(sid, "github", False)
    manager._refresh_session_tools(sid)
    assert manager._engines.get(sid) is rebuilt
    manager.mark_idle(sid)
    assert sid not in manager._engines


# -- the lead hears that a worker is waiting ----------------------------------------------


def test_worker_waiting_reaches_the_lead_through_the_board(manager):
    _lead(manager)
    result = manager.create_team("lead-sid", [{"persona": "swe-worker", "name": "nia"}])
    team = manager.teams.for_lead_session("lead-sid")
    sid = result["workers"][0]["session_id"]
    manager.note_worker_waiting(sid, "run_shell", prompt_id="p1", preview="git push")
    events = manager.team_store.subscribed_events(team.space, team.lead_actor)
    waiting = [e for e in events if e["kind"] == WORKER_WAITING]
    assert waiting and waiting[0]["actor"] == "nia" and waiting[0]["payload"]["tool"] == "run_shell"
    assert waiting[0]["payload"]["prompt_id"] == "p1"
    # A session that is not a worker is silently ignored.
    manager.note_worker_waiting("lead-sid", "run_shell")


# -- the engine gate -----------------------------------------------------------------------


class _Scripted(ProviderClient):
    def __init__(self, name, arguments):
        self.calls, self.name, self.arguments = 0, name, arguments

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        if self.calls == 1:
            return AssistantTurn(text="", tool_calls=[ToolCall(id="t1", name=self.name, arguments=self.arguments)])
        return AssistantTurn(text="done", tool_calls=[])

    def capabilities(self, model):
        return ModelCapabilities(tools=True)


def _engine(tmp_path, requester, name, arguments):
    return TurnEngine(
        provider=_Scripted(name, arguments),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE),
        model="m",
        connector_requester=requester,
    )


async def _run(engine):
    return [e async for e in engine.run("go")]


@pytest.mark.asyncio
async def test_grant_connector_emits_the_gate_and_returns_the_verdict(tmp_path):
    seen = {}

    async def requester(args, tool_call_id=None):
        seen.update(args)
        return {"approved": True, "worker": "nia", "connector": "github"}

    events = await _run(_engine(tmp_path, requester, "grant_connector", {"worker": "nia", "connector": "GitHub", "reason": "push the branch"}))
    gate = [e for e in events if e.type is EventType.CONNECTOR_REQUESTED]
    assert gate and gate[0].data == {"request": "grant", "connector": "github", "worker": "nia", "reason": "push the branch"}
    assert seen["worker"] == "nia"
    assert [e for e in events if e.type is EventType.TOOL_FINISHED][0].data["status"] == "ok"


@pytest.mark.asyncio
async def test_request_connector_decline_is_a_plain_outcome(tmp_path):
    async def requester(args, tool_call_id=None):
        return {"approved": False, "reason": "the user declined"}

    engine = _engine(tmp_path, requester, "request_connector", {"connector": "linear", "reason": "keep the tracker in step"})
    events = await _run(engine)
    gate = [e for e in events if e.type is EventType.CONNECTOR_REQUESTED]
    assert gate[0].data["request"] == "connect" and gate[0].data["worker"] == ""
    assert [e for e in events if e.type is EventType.TOOL_FINISHED][0].data["status"] == "denied"
    result = next(m for m in engine.messages if m.get("role") == "tool")
    assert "declined" in str(result.get("content")) and "say plainly" in str(result.get("content"))


@pytest.mark.asyncio
async def test_connector_gate_without_a_requester_or_a_name(tmp_path):
    events = await _run(_engine(tmp_path, None, "request_connector", {"connector": "github", "reason": "x"}))
    assert not [e for e in events if e.type is EventType.CONNECTOR_REQUESTED]
    events = await _run(_engine(tmp_path, lambda a, t=None: None, "grant_connector", {"connector": "github"}))
    assert not [e for e in events if e.type is EventType.CONNECTOR_REQUESTED]  # no worker named


# -- who gets which tool ------------------------------------------------------------------


class _Stub(ProviderClient):
    def complete(self, **_kw):  # pragma: no cover
        return AssistantTurn()

    def capabilities(self, _model):  # pragma: no cover
        return ModelCapabilities()


def test_tools_follow_the_role(tmp_path):
    from coworker.agent import build_engine
    from coworker.secrets import SecretStore

    secrets = SecretStore(tmp_path / "s.json")
    lead = Agent(name="lead", title="L", system_prompt="x", team="lead", connectors=("github",))
    names = build_engine(agent=lead, workspace=tmp_path, provider=_Stub(), secrets=secrets).registry.names()
    assert {"grant_connector", "request_connector"} <= set(names)
    worker = Agent(name="w", title="W", system_prompt="x", team="worker", connectors=("github",))
    names = build_engine(agent=worker, workspace=tmp_path, provider=_Stub(), secrets=secrets).registry.names()
    assert "request_connector" in names and "grant_connector" not in names
    solo = Agent(name="s", title="S", system_prompt="x")  # no connectors declared → nothing to ask for
    names = build_engine(agent=solo, workspace=tmp_path, provider=_Stub(), secrets=secrets).registry.names()
    assert "request_connector" not in names and "grant_connector" not in names


# -- boxes review by default -----------------------------------------------------------------


def test_joined_boxes_have_the_reviewer_on_by_default(manager):
    # A desktop keeps the flag's default (off); a joined box (remote.json present) is on…
    assert manager.auto_approve() is False
    (manager._data_base / "remote.json").write_text('{"controller": "x"}', encoding="utf-8")
    assert manager.is_joined_box() and manager.auto_approve() is True
    # …and an explicit preference wins either way.
    manager.set_auto_approve(False)
    assert manager.auto_approve() is False


# -- the gates work on background turns (headless boxes) ---------------------------------------


@pytest.mark.asyncio
async def test_staffing_gate_works_without_a_socket(manager):
    """Drill finding 2026-09-05: an event-driven lead (background delivery, no socket)
    got "team staffing isn't available in this surface". The queue-backed handlers are
    the default for every turn: the gate parks, the human resolves it from anywhere,
    approval pre-spawns the team with the human's connector decisions."""
    import asyncio

    _lead(manager)
    approve = manager.inbox_team_approver("lead-sid", "swe-lead")
    from proposal_fixtures import team_proposal
    task = asyncio.create_task(approve(team_proposal([{"persona": "swe-worker", "name": "nia", "connectors": ["github"]}, {"persona": "test-worker", "name": "checks"}]), "t1"))
    for _ in range(50):
        await asyncio.sleep(0.02)
        pending = [i for i in manager.inbox.list(session_id="lead-sid", state="pending") if i.title == "Create this team?"]
        if pending:
            break
    assert pending and "swe-worker" in pending[0].body
    # The parked item carries the SAME payload the inline card gets, so the Inbox can render
    # the real staffing card (roster, per-worker connector offer, chat flag) instead of a
    # bare Approve that staffs everyone with nothing (owner-hit 2026-09-16 on a box).
    data = pending[0].data or {}
    assert data["gate"] == "team" and [m["name"] for m in data["members"]] == ["nia", "checks"]
    assert data["enable_chat"] is False and set(data["offer"]) == {"swe-worker", "test-worker"}
    # The human ticks nothing for nia (the lead suggested github) and approves.
    manager.inbox.resolve(pending[0].id, '{"approved": true, "members": [{"persona": "swe-worker", "name": "nia", "connectors": []}, {"persona": "test-worker", "name": "checks", "connectors": []}]}')
    result = await task
    assert result["approved"] is True and [w["actor"] for w in result["workers"]] == ["nia", "checks"]
    assert result["workers"][0]["connectors"] == []
    # An engine built for a background delivery carries all three handlers.
    engine = manager.get_engine("lead-sid")
    assert engine.team_approver is not None and engine.items_approver is not None and engine.connector_requester is not None


@pytest.mark.asyncio
async def test_items_gate_and_connector_ask_work_without_a_socket(manager):
    import asyncio

    _lead(manager)
    approve = manager.inbox_items_approver("lead-sid", "swe-lead")
    from proposal_fixtures import work_proposal
    proposal = work_proposal([{"title": "Cap the backoff", "criteria": "no wait exceeds maxDelayMs"}])
    task = asyncio.create_task(approve(proposal, "t2"))
    for _ in range(50):
        await asyncio.sleep(0.02)
        pending = [i for i in manager.inbox.list(session_id="lead-sid", state="pending") if i.title.startswith("Approve the proposed")]
        if pending:
            break
    assert pending and "Acceptance criteria: no wait exceeds" in pending[0].body
    assert (pending[0].data or {}).get("gate") == "items" and (pending[0].data or {})["items"][0]["title"] == "Cap the backoff"
    # The lead's note reaches the Inbox card too (it used to be inline only).
    assert pending[0].data["summary"] == proposal["summary"]
    manager.inbox.resolve(pending[0].id, '{"approved": true}')
    result = await task
    assert result.get("approved") is True or result.get("ok") is True
    # connect: an already-connected service short-circuits; an unknown one parks and a decline is plain
    ask = manager.inbox_connector_requester("lead-sid", "swe-lead")
    assert (await ask({"connector": "github", "reason": "x"}, "t3"))["connected"] is True
    task = asyncio.create_task(ask({"connector": "linear", "reason": "keep the tracker in step"}, "t4"))
    for _ in range(50):
        await asyncio.sleep(0.02)
        pending = [i for i in manager.inbox.list(session_id="lead-sid", state="pending") if i.title == "Connect linear?"]
        if pending:
            break
    assert pending and pending[0].kind == "connector"
    manager.inbox.resolve(pending[0].id, '{"approved": false}')
    assert (await task)["approved"] is False


@pytest.mark.asyncio
async def test_background_approver_tells_the_lead_a_worker_is_waiting(manager):
    """Workers run on background turns: the manager's own approver must raise the
    worker_waiting board event too (drill finding 2026-09-05 — only the socket
    approver did, so a headless lead never heard its worker had parked)."""
    import asyncio
    from types import SimpleNamespace

    _lead(manager)
    sid = manager.create_team("lead-sid", [{"persona": "swe-worker", "name": "nia"}])["workers"][0]["session_id"]
    approve = manager.inbox_approver(sid, "swe-worker")
    request = SimpleNamespace(tool_name="run_shell", arguments={"command": "git status"}, tool_call_id="c1", metadata=None, reason="")
    task = asyncio.create_task(approve(request))
    for _ in range(50):
        await asyncio.sleep(0.02)
        pending = manager.inbox.list(session_id=sid, state="pending")
        if pending:
            break
    assert pending and pending[0].title == "Run `run_shell`?"
    team = manager.teams.for_lead_session("lead-sid")
    waiting = [e for e in manager.team_store.subscribed_events(team.space, team.lead_actor) if e["kind"] == WORKER_WAITING]
    assert waiting and waiting[0]["payload"]["prompt_id"] == pending[0].id and "git status" in waiting[0]["payload"]["preview"]
    # The lead answers it with decide_worker_call → the worker's prompt resolves.
    out = manager.decide_worker_call("lead-sid", "nia", pending[0].id, "allow", "plain repo inspection")
    assert out["ok"] is True
    outcome = await task
    assert getattr(outcome, "value", outcome) in ("once", "allow") or outcome is not None


# -- worker models: the persona's list is the recommendation, not a binding ----------------


def test_worker_model_prefers_the_human_then_the_recommendation_then_the_lead(manager, monkeypatch):
    opus, sol, sonnet = "anthropic:claude-opus-4-8", "openai:gpt-5.6-sol", "anthropic:claude-sonnet-4-6"
    assert manager.persona_models("swe-worker") == [opus, sol]
    runnable = {opus, sol, sonnet}
    monkeypatch.setattr(manager, "model_selectable", lambda m: m in runnable)
    # The human's choice on the card wins, even outside the recommended list.
    assert manager.resolve_worker_model("swe-worker", human_pick=sonnet, lead_pick=sol, lead_model=sonnet) == (sonnet, "")
    # The lead's pick counts only when it is recommended; otherwise the first recommended runnable.
    assert manager.resolve_worker_model("swe-worker", lead_pick=sol, lead_model=sonnet) == (sol, "")
    assert manager.resolve_worker_model("swe-worker", lead_pick=sonnet, lead_model=sonnet) == (opus, "")
    # A human pick this machine cannot run is ignored, not obeyed into a dead worker.
    assert manager.resolve_worker_model("swe-worker", human_pick="google:gemini-x", lead_model=sonnet) == (opus, "")
    # Nothing recommended can run here: the lead's model, with a warning that says why.
    runnable.clear(); runnable.add(sonnet)
    model, warning = manager.resolve_worker_model("swe-worker", lead_model=sonnet)
    assert model == sonnet
    assert "lead's model" in warning and "Neither is set up on this machine" in warning


def test_team_card_payload_carries_models_for_the_machine_the_workers_run_on(manager, monkeypatch):
    opus, sonnet = "anthropic:claude-opus-4-8", "anthropic:claude-sonnet-4-6"
    monkeypatch.setattr(manager, "model_selectable", lambda m: m == sonnet)
    monkeypatch.setattr(manager, "_curated_models", lambda: [sonnet, opus])
    _lead(manager)
    lead = manager.session_store.load("lead-sid"); lead.model = sonnet; manager.session_store.save(lead)
    extras = manager.team_card_extras("lead-sid", [{"persona": "swe-worker", "name": "nia"}, {"persona": "test-worker", "name": "checks"}])
    assert extras["lead_model"] == sonnet
    assert extras["model_options"] == {"swe-worker": [], "test-worker": []}
    assert [m["id"] for m in extras["runnable_models"]] == [sonnet] and extras["runnable_models"][0]["label"]
    nia = extras["members"][0]
    assert nia["resolved_model"] == sonnet and "lead's model" in nia["model_warning"]
    # The human keeps nia on the fallback and the team is created on a model that runs.
    result = manager.create_team("lead-sid", [{"persona": "swe-worker", "name": "nia", "model_by_human": sonnet}])
    assert manager.session_store.load(result["workers"][0]["session_id"]).model == sonnet


# -- a lead's decision on a worker's waiting call (owner catch 2026-09-17) ----------------


def _lead_engine(tmp_path, mode, arguments, decided):
    from coworker.teams.tools import decide_worker_call_tool

    registry = ToolRegistry()
    registry.register(decide_worker_call_tool(lambda *a: decided.append(a) or {"ok": True}))
    return TurnEngine(
        provider=_Scripted("decide_worker_call", arguments),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=mode),
        model="m",
    )


@pytest.mark.asyncio
async def test_an_auto_approve_leads_denial_asks_nobody(tmp_path):
    """A denial runs nothing, so under Auto-Approve it needs neither the reviewer nor a
    card: the human was being asked to click "Allow" to deny a command."""
    decided = []
    args = {"worker": "nia", "call_id": "c1", "decision": "deny", "note": "wrong worktree"}
    events = await _run(_lead_engine(tmp_path, Mode.AUTO_APPROVE, args, decided))
    assert EventType.PERMISSION_REQUIRED not in [e.type for e in events]
    assert decided == [("nia", "c1", "deny", "wrong worktree")]


@pytest.mark.asyncio
async def test_an_allow_and_a_manual_leads_denial_still_ask(tmp_path):
    for mode, decision in ((Mode.AUTO_APPROVE, "allow"), (Mode.INTERACTIVE, "deny")):
        decided = []
        args = {"worker": "nia", "call_id": "c1", "decision": decision, "note": "n"}
        from coworker.engine import ApprovalOutcome

        async def deny(_request):
            return ApprovalOutcome.DENY

        engine = _lead_engine(tmp_path, mode, args, decided)
        # The harness must resolve a real original action before allowing a proxy.
        engine.delegated_approval = lambda _args: {"tool": "run_shell", "arguments": {"command": "git status"}, "context": {}, "reason": "requires approval"}
        engine.approver = deny  # the human says no: the lead's decision must not run
        events = await _run(engine)
        assert EventType.PERMISSION_REQUIRED in [e.type for e in events], (mode, decision)
        assert decided == [], (mode, decision)


def test_the_leads_approval_card_carries_the_workers_call(manager):
    """The parked approval for `decide_worker_call` shows WHAT the worker wants to run,
    looked up by call_id — not the id."""
    _lead(manager)
    from coworker.teams.registry import TeamWorker
    manager.teams.create(space="acme", lead_session="lead-sid", lead_actor="lead",
        workers=[TeamWorker(actor="nia", persona="swe-worker", session_id="worker-sid")])
    parked = manager.inbox.add_approval(
        "worker-sid", "Run `run_shell`?", body="requires approval\ncommand: npm run build",
        data={"tool": "run_shell", "arguments": {"command": "npm run build"}},
    )

    class Request:
        tool_name = "decide_worker_call"
        arguments = {"worker": "nia", "call_id": parked.id, "decision": "deny", "note": "wrong worktree"}
        metadata = None

    data = manager.approval_prompt_data("lead-sid", Request())
    assert data["worker_call"] == {
        "worker": "nia", "tool": "run_shell", "arguments": {"command": "npm run build"},
        "reason": "requires approval", "state": "pending", "resolution": None,
    }
    # An unknown id (already cleaned up, or invented by the model) attaches nothing.
    Request.arguments = {**Request.arguments, "call_id": "nope"}
    assert "worker_call" not in manager.approval_prompt_data("lead-sid", Request())
