import copy
import json
from pathlib import Path

import pytest

from coworker.teams.model import Actor, Role
from coworker.teams.proposals import validate_work_proposal, validate_team_proposal
from coworker.teams.store import TeamStore
from proposal_fixtures import work_proposal, team_proposal
from test_gallery_contract import manager, _parked


def test_domain_examples_are_valid_tool_arguments():
    contract = json.loads((Path(__file__).parents[1] / "surfaces/gui/src/gallery/states.contract.json").read_text())
    for name in ("launch-plan", "security-plan", "marketing-plan"):
        proposal = contract["cards"]["work-items"]["states"][name]
        assert validate_work_proposal(proposal) == proposal


@pytest.mark.parametrize("field", ["title", "summary", "targets", "external_actions", "activities", "workstreams", "final_acceptance"])
def test_new_work_contract_requires_explicit_information(field):
    proposal = work_proposal()
    del proposal[field]
    with pytest.raises(ValueError, match=field):
        validate_work_proposal(proposal)


@pytest.mark.parametrize("status,actions,valid", [("none", [], True), ("planned", ["Open a PR"], True),
    ("undetermined", [], True), ("none", ["Deploy"], False), ("planned", [], False), ("undetermined", ["Scan"], False)])
def test_external_intent_is_not_inferred(status, actions, valid):
    proposal = work_proposal()
    proposal["external_actions"].update(status=status, actions=actions)
    if valid:
        assert validate_work_proposal(proposal)["external_actions"]["status"] == status
    else:
        with pytest.raises(ValueError):
            validate_work_proposal(proposal)


def test_references_and_cycles_are_rejected_before_writing():
    proposal = work_proposal([{"title": "Assess", "criteria": "Findings have evidence"},
                              {"title": "Verify", "criteria": "Findings are rechecked"}])
    proposal["items"][0]["depends_on"] = ["task-1"]
    proposal["items"][1]["depends_on"] = ["task-0"]
    with pytest.raises(ValueError, match="cycle"):
        validate_work_proposal(proposal)
    proposal["items"][0]["depends_on"] = ["missing"]
    with pytest.raises(ValueError, match="existing"):
        validate_work_proposal(proposal)


def test_accepted_metadata_and_dependencies_survive_rebuild(tmp_path):
    store = TeamStore(tmp_path / "board.db")
    actor = Actor("lead", Role.LEAD)
    proposal = work_proposal([{"title": "Assess", "criteria": "Findings have evidence"},
                              {"title": "Verify", "criteria": "Findings are rechecked"}])
    proposal["items"][1].update(depends_on=["task-0"], verifies=["task-0"])
    result = store.create_proposal("acme", actor, proposal)
    assert [i["key"] for i in result["items"]] == ["task-0", "task-1"]
    before = store.get_item("acme", 2, actor=actor)
    assert before["state"] == "open" and before["assignee"] == "lead"
    assert before["proposal"]["external_actions"] == proposal["external_actions"]
    assert before["proposal"]["verifies"] == [1]
    assert store.get_item("acme", 1, actor=actor)["assignee"] == ""
    store.rebuild("acme")
    assert store.get_item("acme", 2, actor=actor) == before
    assert store.verify_chain("acme")
    store.close()


def test_failed_batch_does_not_leave_partial_items_or_events(tmp_path, monkeypatch):
    store = TeamStore(tmp_path / "board.db")
    proposal = work_proposal([{"title": "First", "criteria": "Verified"}, {"title": "Second", "criteria": "Verified"}])
    original = store._apply
    def fail_second(space, seq, ts, kind, actor, item_id, payload):
        if item_id == 2:
            raise RuntimeError("write failure")
        return original(space, seq, ts, kind, actor, item_id, payload)
    monkeypatch.setattr(store, "_apply", fail_second)
    with pytest.raises(RuntimeError):
        store.create_proposal("acme", Actor("lead", Role.LEAD), proposal)
    assert store.event_count("acme") == 0
    assert store.list_items("acme", Actor("lead", Role.LEAD)) == []


def test_staffing_requires_declared_groups_and_unique_names():
    proposal = team_proposal()
    assert validate_team_proposal(proposal) == proposal
    proposal["members"][0]["group"] = "unknown"
    with pytest.raises(ValueError, match="group"):
        validate_team_proposal(proposal)
    proposal = team_proposal()
    proposal["members"].append(copy.deepcopy(proposal["members"][0]))
    with pytest.raises(ValueError, match="unique"):
        validate_team_proposal(proposal)


@pytest.mark.asyncio
async def test_server_resolves_responsibilities_and_preserves_human_name_without_assigning(manager):
    plan = work_proposal([{"title": "Assess", "criteria": "Evidence exists"}, {"title": "Accept", "criteria": "Evidence checked"}])
    created = manager.board_create_items("lead-sid", plan["items"], proposal=plan)
    item_id = created["items"][0]["id"]
    proposal = team_proposal([{"persona": "test-worker", "name": "maya", "item_ids": [item_id]}])
    approve = manager.inbox_team_approver("lead-sid", "swe-lead")
    task, prompt = await _parked(manager, approve(proposal, "staff"), "Create this team?")
    assert prompt.data["planned_items"][0]["title"] == "Assess"
    assert prompt.data["planned_items"][0]["final_acceptance"]["owner"] == "lead"
    manager.inbox.resolve(prompt.id, json.dumps({"approved": True, "members": [{"name": "maya-custom", "connectors": []}]}))
    result = await task
    assert result["approved"] is True
    assert result["workers"][0]["actor"] == "maya-custom"
    record = manager.session_store.load("lead-sid")
    space = manager._space_for(record, record.workspace)
    item = manager.team_store.get_item(space, item_id, actor=Actor("lead", Role.LEAD))
    assert item["assignee"] == "" and item["state"] == "open"


@pytest.mark.asyncio
async def test_unknown_board_responsibilities_do_not_park_a_card(manager):
    proposal = team_proposal([{"persona": "test-worker", "name": "maya", "item_ids": [999]}])
    result = await manager.inbox_team_approver("lead-sid", "swe-lead")(proposal, "invalid")
    assert not result["approved"]
    assert not manager.inbox.pending("lead-sid")


@pytest.mark.asyncio
@pytest.mark.parametrize("human", [None, "", "Run local tests.\n No pushes."])
async def test_only_human_returned_guidance_is_saved(manager, human):
    proposal = team_proposal([{"persona": "test-worker", "name": "maya", "approval_guidance": "Lead suggestion"}])
    task, prompt = await _parked(manager, manager.inbox_team_approver("lead-sid", "swe-lead")(proposal, "staff"), "Create this team?")
    assert prompt.data["members"][0]["approval_guidance"] == "Lead suggestion"
    decision = {"name": "maya", "connectors": []}
    if human is not None:
        decision["approval_guidance"] = human
    manager.inbox.resolve(prompt.id, json.dumps({"approved": True, "members": [decision]}))
    assert (await task)["approved"]
    worker = manager.teams.for_lead_session("lead-sid").workers[0]
    assert worker.approval_guidance == (human or "")
