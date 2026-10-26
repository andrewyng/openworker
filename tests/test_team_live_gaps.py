"""Regressions found by the real lead + two-worker Team View run."""

import asyncio
from types import SimpleNamespace

import pytest
from coworker.engine import ApprovalOutcome, PermissionRequest
from coworker.inbox import InboxStore
from coworker.server.manager import SessionManager
from coworker.sessions import SessionRecord
from coworker.teams.registry import TeamWorker
from coworker.teams.store import WORKER_WAITING
from test_engine import ScriptedProvider, _collect, _engine, _text_turn, _tool_turn


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "repo").mkdir()
    m = SessionManager(data_dir=tmp_path / "data", workspace=tmp_path / "repo")
    m.session_store.save(
        SessionRecord(
            session_id="lead",
            workspace=m.default_workspace,
            model="sample",
            mode="interactive",
            agent="swe-lead",
        )
    )
    m.teams.create(
        space=m._board_space("lead"),
        lead_session="lead",
        lead_actor="lead",
        workers=[TeamWorker(actor="sam", persona="swe-worker", session_id="worker")],
    )
    yield m
    asyncio.run(m.aclose())


def request_for(manager):
    original = manager.inbox.add_approval(
        "worker",
        "Run tests",
        data={"tool": "run_shell", "arguments": {"command": "python -m unittest -v"}},
    )
    request = PermissionRequest(
        tool_name="decide_worker_call",
        arguments={"worker": "sam", "call_id": original.id, "decision": "allow"},
        metadata=None,
        reason="requires approval",
        tool_call_id="lead-call",
    )
    return original, request


def test_reviewer_settings_hot_apply_to_cached_lead_and_worker(manager):
    from coworker.permissions import Mode

    manager.provider = ScriptedProvider([])
    lead = manager.get_engine("lead", agent="swe-lead")
    worker = manager.get_engine("worker", agent="swe-worker")
    assert lead.reviewer is None and worker.reviewer is None
    original, _ = request_for(manager)
    worker._reviewer_denials = 1
    lead.permissions.mode = Mode.AUTO_APPROVE
    manager.save("lead", lead, touch=False)

    manager.set_auto_approve(True)
    assert manager.get_engine("worker") is worker
    assert lead.reviewer is not None and worker.reviewer is not None
    assert worker.permissions.mode is Mode.AUTO_APPROVE
    assert worker._reviewer_active()
    assert worker._reviewer_denials == 1
    assert original.state == "pending"  # never retroactively clear a parked action
    reviewer = worker.reviewer
    manager.sync_cached_reviewers()
    assert worker.reviewer is reviewer

    manager.set_auto_approve_shadow(True)
    manager.set_auto_approve(False)
    assert worker.reviewer is reviewer and worker.reviewer_shadow
    assert not worker._reviewer_active()  # shadow does not grant live approval
    manager.set_auto_approve_shadow(False)
    assert lead.reviewer is None and worker.reviewer is None
    manager.set_auto_approve(True)
    lead.permissions.mode = Mode.INTERACTIVE
    manager.save("lead", lead, touch=False)
    manager.sync_cached_reviewers()
    assert worker.permissions.mode is Mode.INTERACTIVE
    assert not worker._reviewer_active()


@pytest.mark.parametrize("resolve_first", [False, True])
@pytest.mark.parametrize("answer", ["allow", "deny"])
def test_worker_resolution_retires_linked_gate_before_or_after_creation(
    manager, resolve_first, answer
):
    original, request = request_for(manager)
    data = manager.approval_prompt_data("lead", request)
    if resolve_first:
        manager.inbox.resolve(original.id, answer, by="human")

    async def run():
        task = asyncio.create_task(manager.inbox_approver("lead", "swe-lead")(request))
        await asyncio.sleep(0)
        if not resolve_first:
            manager.inbox.resolve(original.id, answer, by="human")
        assert await asyncio.wait_for(task, 1) is ApprovalOutcome.SUPERSEDED

    asyncio.run(run())
    parent = manager.inbox.for_tool_call("lead", "lead-call")
    assert parent.state == "resolved" and parent.resolution == "superseded"
    assert parent.resolved_by == "system:worker-resolution"
    assert parent.data["worker_call"]["state"] == "resolved"
    assert parent.data["worker_call"]["resolution"] == answer
    assert not manager.inbox.pending()
    assert not manager.inbox.resolve(original.id, "opposite")
    assert original.resolution == answer
    loaded = InboxStore(manager.inbox.path)
    assert loaded.get(parent.id).resolution == "superseded"
    assert data["worker_prompt_id"] == original.id


def test_cross_team_or_mismatched_worker_cannot_link_or_disclose_prompt(manager):
    original, request = request_for(manager)
    assert manager.approval_outcome("superseded", request, "lead") is ApprovalOutcome.DENY
    assert "worker_call" not in manager.approval_prompt_data("not-the-lead", request)
    request.arguments["worker"] = "maya"
    data = manager.approval_prompt_data("lead", request)
    assert "worker_prompt_id" not in data and "worker_call" not in data
    assert original.state == "pending"


def test_denied_lead_allow_atomically_denies_worker_and_retires_other_proxies(manager):
    original, request = request_for(manager)
    data = manager.approval_prompt_data("lead", request)
    parent = manager.inbox.add_approval("lead", "Decision", data=data)
    sibling = manager.inbox.add_approval("lead", "Other window", data=data)

    async def run():
        waits = [asyncio.create_task(manager.inbox.wait(i.id)) for i in (original, parent, sibling)]
        await asyncio.sleep(0)
        assert manager.inbox.resolve(parent.id, "deny", by="human")
        assert await asyncio.wait_for(asyncio.gather(*waits), 1) == ["deny", "deny", "superseded"]

    asyncio.run(run())
    assert original.resolved_by == "human"
    assert not manager.inbox.pending()
    assert not manager.inbox.resolve(original.id, "allow")
    loaded = InboxStore(manager.inbox.path)
    assert loaded.get(original.id).resolution == "deny"
    assert loaded.get(parent.id).resolution == "deny"


@pytest.mark.parametrize("answer", ["allow", "interrupted", "session deleted"])
def test_non_denial_of_proxy_does_not_resolve_worker(manager, answer):
    original, request = request_for(manager)
    parent = manager.inbox.add_approval("lead", "Decision", data=manager.approval_prompt_data("lead", request))
    manager.inbox.resolve(parent.id, answer)
    assert original.state == "pending"


def test_rejecting_a_lead_denial_never_implicitly_allows_worker(manager):
    original, request = request_for(manager)
    request.arguments["decision"] = "deny"
    parent = manager.inbox.add_approval("lead", "Decision", data=manager.approval_prompt_data("lead", request))
    manager.inbox.resolve(parent.id, "deny")
    assert original.state == "pending"


def test_denial_after_restart_resumes_both_parked_sessions(manager, monkeypatch):
    original, request = request_for(manager)
    parent = manager.inbox.add_approval("lead", "Decision", data=manager.approval_prompt_data("lead", request))
    manager.inbox = InboxStore(manager.inbox.path)
    resumed = []

    async def resume(item):
        resumed.append((item.session_id, item.resolution))

    monkeypatch.setattr(manager, "_durable_resume", resume)
    monkeypatch.setattr(manager, "reconcile_obsolete_prompts", lambda sid: None)
    asyncio.run(manager.resolve_inbox(parent.id, "deny", by="human"))
    assert sorted(resumed) == [("lead", "deny"), ("worker", "deny")]
    assert manager.inbox.get(original.id).resolution == "deny"


def test_rest_proxy_denial_is_visible_to_another_client_and_survives_reload(manager, monkeypatch):
    from fastapi.testclient import TestClient
    from coworker.server import create_app

    original, request = request_for(manager)
    parent = manager.inbox.add_approval("lead", "Decision", data=manager.approval_prompt_data("lead", request))
    monkeypatch.setattr(manager, "reconcile_obsolete_prompts", lambda sid: None)
    first = TestClient(create_app(manager))
    other = TestClient(create_app(manager))
    assert first.post(f"/v1/inbox/{parent.id}/resolve", json={"resolution": "deny"}).json()["ok"]
    rows = other.get("/v1/inbox", params={"session_id": "worker"}).json()["items"]
    assert next(i for i in rows if i["id"] == original.id)["resolution"] == "deny"
    assert not other.post(f"/v1/inbox/{original.id}/resolve", json={"resolution": "allow"}).json()["ok"]
    assert InboxStore(manager.inbox.path).get(original.id).resolution == "deny"


def test_lead_proxy_denial_releases_live_engine_without_executing_decision(manager, monkeypatch):
    original, request = request_for(manager)
    manager.provider = ScriptedProvider([
        _tool_turn("decide_worker_call", request.arguments), _text_turn("The user denied it.")
    ])
    executed = []
    monkeypatch.setattr(manager, "decide_worker_call", lambda *a: executed.append(a))

    async def run():
        delivery = asyncio.create_task(manager.deliver_to_session("lead", "Review the waiting call."))
        for _ in range(200):
            pending = manager.inbox.pending("lead")
            if pending:
                break
            await asyncio.sleep(0.01)
        assert pending
        manager.inbox.resolve(pending[0].id, "deny", by="human")
        await asyncio.wait_for(delivery, 3)

    asyncio.run(run())
    assert original.resolution == "deny"
    assert not executed and not manager.inbox.pending()


def test_successful_self_wake_ends_turn_without_another_model_call(manager):
    manager.provider = ScriptedProvider([_tool_turn("sleep_for", {"seconds": 2})])
    engine = manager.get_engine("lead", agent="swe-lead")
    events = _collect(engine, "Wait for the worker.")
    assert manager.provider.calls == 1
    assert events[-1].data["status"] == "sleeping"
    assert len(manager.wakes.pending("lead")) == 1


def test_superseded_gate_never_executes_or_grants_permission(tmp_path):
    async def approve(_):
        return ApprovalOutcome.SUPERSEDED

    engine, _ = _engine(
        tmp_path,
        [
            _tool_turn("write_file", {"path": "must-not-exist.txt", "content": "no"}),
            _text_turn("The original request is already resolved."),
        ],
        approver=approve,
    )
    events = _collect(engine, "write")
    assert not (tmp_path / "must-not-exist.txt").exists()
    finished = [e for e in events if e.type.value == "tool_finished"]
    assert finished[0].data["status"] == "ok"
    assert "already resolved" in finished[0].data["result_preview"]


@pytest.mark.parametrize("answer", [ApprovalOutcome.ONCE, ApprovalOutcome.DENY, ApprovalOutcome.SUPERSEDED])
def test_ordinary_approval_and_completion_carry_the_same_exact_call_id(tmp_path, answer):
    async def approve(request):
        assert request.tool_call_id == "unique-write-call"
        return answer

    engine, _ = _engine(tmp_path, [
        _tool_turn("write_file", {"path": "result.txt", "content": "sample"}, call_id="unique-write-call"),
        _text_turn("Finished"),
    ], approver=approve)
    events = _collect(engine, "Write the sample file.")
    for kind in ("permission_required", "tool_finished"):
        event = next(e for e in events if e.type.value == kind)
        assert event.data["tool_call_id"] == "unique-write-call"
    assert (tmp_path / "result.txt").exists() == (answer is ApprovalOutcome.ONCE)


def test_background_delivery_enriches_actual_permission_event_and_retires_gate(
    manager, monkeypatch
):
    original, request = request_for(manager)
    manager.provider = ScriptedProvider(
        [
            _tool_turn("decide_worker_call", request.arguments),
            _text_turn("The human already answered the worker."),
        ]
    )
    events = []
    executed = []
    monkeypatch.setattr(manager, "decide_worker_call", lambda *a: executed.append(a))

    async def receive(event):
        events.append(event)

    async def run():
        manager.register_session_client("lead", receive)
        delivery = asyncio.create_task(
            manager.deliver_to_session("lead", "Decide the worker call.")
        )
        for _ in range(200):
            if manager.inbox.pending("lead"):
                break
            await asyncio.sleep(0.01)
        assert manager.inbox.pending("lead")
        manager.inbox.resolve(original.id, "deny", by="human")
        await asyncio.wait_for(delivery, 3)

    asyncio.run(run())
    permission = next(e["data"] for e in events if e["type"] == "permission_required")
    assert permission["worker_call"]["arguments"]["command"] == "python -m unittest -v"
    assert not executed
    assert not manager.inbox.pending()
    assert any(e["type"] == "tool_finished" for e in events)


def test_lead_self_wake_does_not_enable_general_scheduling(manager):
    lead = manager.get_engine("lead", agent="swe-lead")
    names = set(lead.registry.names())
    assert {"sleep_for", "sleep_until"} <= names
    assert (
        not {"create_scheduled_task", "update_scheduled_task", "delete_scheduled_task"}
        & names
    )
    worker = manager.get_engine(
        "worker", workspace=manager.default_workspace, agent="swe-worker"
    )
    assert "sleep_for" not in worker.registry.names()


def test_worker_wait_digest_is_informational_and_wait_only_batch_does_not_wake(
    manager, monkeypatch
):
    team = manager.teams.for_lead_session("lead")
    events = [
        {
            "seq": 10,
            "item_id": None,
            "kind": WORKER_WAITING,
            "actor": "maya",
            "payload": {"tool": "run_shell", "prompt_id": "sample-prompt"},
        }
    ]
    body, rows = manager._team_digest(team, events, [], is_lead=False, reader="sam")
    assert "decide_worker_call" not in body and "your decision" not in body
    assert "lead or user" in body and rows[0]["kind"] == "waiting"
    consumed = []
    monkeypatch.setattr(manager.team_store, "delivery_page", lambda *a, **kw: {
        "directs": events, "subs": [], "through_seq": 10, "has_more": False})
    monkeypatch.setattr(
        manager.team_store, "consume_feed", lambda *a: consumed.append(a)
    )
    assert (
        asyncio.run(
            manager._drain_team_member(
                team, session_id="worker", actor="sam", is_lead=False
            )
        )
        == 0
    )
    assert consumed == [(team.space, "sam", 10)]
