"""The GUI's card gallery and the server must agree on what a card's payload looks like.

`surfaces/gui/src/gallery/states.contract.json` is generated from the gallery's state files
(`npm run gallery:contract`). Here the same items are built by the REAL server code against
a fixed fake machine, then compared with that file by field name and JSON type:

  * a field the server sends that no gallery state has  → the card has never been looked
    at with that field (the bug class of 2026-09-16: a payload the card did not expect);
  * a field the card's common-case state relies on that the server does not send
    → the card would render wrong in the app while looking right in the gallery.

No network, no LLM."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from coworker.server.manager import SessionManager
from coworker.sessions import SessionRecord
from proposal_fixtures import work_proposal, team_proposal

CONTRACT = Path(__file__).resolve().parents[1] / "surfaces/gui/src/gallery/states.contract.json"
OPUS, SONNET = "anthropic:claude-opus-4-8", "anthropic:claude-sonnet-4-8"


@pytest.fixture(scope="module")
def contract():
    return json.loads(CONTRACT.read_text())["cards"]


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    ws = tmp_path / "repo"
    ws.mkdir()
    m = SessionManager(data_dir=tmp_path / "data", workspace=str(ws))
    m.secrets.put("github:default", {"token": "ghp_test", "enabled": True})
    m.secrets.put("linear:default", {"token": "lin_test", "enabled": True})
    # A machine that can run two models, so the payload carries real model information.
    monkeypatch.setattr(m, "model_selectable", lambda model: model in (OPUS, SONNET))
    monkeypatch.setattr(m, "_curated_models", lambda: [OPUS, SONNET])
    m.session_store.save(
        SessionRecord(session_id="lead-sid", workspace=m.default_workspace, model=SONNET, mode="auto-approve", messages=[], agent="swe-lead")
    )
    yield m


def _kind(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return {str: "string", list: "array", dict: "object", type(None): "null"}[type(value)]


def _compare(label: str, server: dict, canonical: dict, all_states: list[dict]) -> list[str]:
    problems = []
    known = {k for s in all_states for k in s}
    for key in sorted(set(server) - known):
        problems.append(f"{label}: the server sends `{key}` but no gallery state has it")
    for key in sorted(set(canonical) - set(server)):
        problems.append(f"{label}: the gallery's common case relies on `{key}` but the server does not send it")
    for key in sorted(set(server) & set(canonical)):
        a, b = _kind(server[key]), _kind(canonical[key])
        if "null" not in (a, b) and a != b:
            problems.append(f"{label}: `{key}` is {a} from the server but {b} in the gallery")
    return problems


async def _parked(manager, start, title_prefix):
    task = asyncio.create_task(start)
    for _ in range(100):
        await asyncio.sleep(0.02)
        items = [i for i in manager.inbox.list(session_id="lead-sid", state="pending") if i.title.startswith(title_prefix)]
        if items:
            return task, items[0]
    raise AssertionError(f"nothing parked for {title_prefix!r}")


def _inbox_data(card: dict) -> tuple[dict, list[dict]]:
    items = card["inbox"]
    return items[card["canonical"]].get("data") or {}, [i.get("data") or {} for i in items.values()]


@pytest.mark.asyncio
async def test_staffing_card_payload_matches_the_gallery(manager, contract):
    card = contract["team-request"]
    approve = manager.inbox_team_approver("lead-sid", "swe-lead")
    args = {
        "members": [
            {"persona": "swe-worker", "name": "nia", "reason": "builds it", "connectors": ["github"], "connector_reasons": {"github": "pushes the branch"}},
            {"persona": "test-worker", "name": "checks", "reason": "verifies it"},
        ],
        "note": "Two workers.",
    }
    args = team_proposal(args["members"])
    task, item = await _parked(manager, approve(args, "t1"), "Create this team?")
    data = item.data or {}
    canonical, states = _inbox_data(card)
    problems = _compare("team gate", data, canonical, states)
    # Same check one level down: the roster rows.
    gallery_members = [m for s in states for m in s.get("members", [])]
    canonical_member = {k: v for k, v in canonical["members"][0].items()}
    for member in data["members"]:
        problems += _compare("team member", member, {k: canonical_member[k] for k in ("persona", "name", "resolved_model")}, gallery_members)
    # The inline event carries the same fields minus the gate marker.
    event_canonical = card["states"][card["canonical"]]
    problems += _compare("team_proposed", {k: v for k, v in data.items() if k != "gate"}, event_canonical, list(card["states"].values()))
    manager.inbox.resolve(item.id, '{"approved": false}')
    await task
    assert not problems, "\n".join(problems)


@pytest.mark.asyncio
async def test_work_items_payload_matches_the_gallery(manager, contract):
    card = contract["work-items"]
    approve = manager.inbox_items_approver("lead-sid", "swe-lead")
    args = {"items": [{"title": "Cap the backoff", "criteria": "no wait exceeds maxDelayMs", "description": "in retry.ts"}], "note": "one item"}
    args = work_proposal(args["items"])
    task, item = await _parked(manager, approve(args, "t2"), "Approve the proposed")
    canonical, states = _inbox_data(card)
    problems = _compare("items gate", item.data or {}, canonical, states)
    gallery_items = [i for s in card["states"].values() for i in s.get("items", [])]
    problems += _compare("work item", (item.data or {})["items"][0], {"title": "", "criteria": ""}, gallery_items)
    manager.inbox.resolve(item.id, '{"approved": false}')
    await task
    assert not problems, "\n".join(problems)


@pytest.mark.asyncio
async def test_connector_request_payload_matches_the_gallery(manager, contract):
    card = contract["connector-request"]
    ask = manager.inbox_connector_requester("lead-sid", "swe-lead")
    task, item = await _parked(manager, ask({"connector": "jira", "reason": "track the work"}, "t3"), "Connect jira?")
    canonical, states = _inbox_data(card)
    problems = _compare("connector request", item.data or {}, canonical, states)
    # The gallery writes the same title the server does.
    assert item.title == card["inbox"][card["canonical"]]["title"].replace("jira", "jira")
    manager.inbox.resolve(item.id, '{"approved": false}')
    await task
    assert not problems, "\n".join(problems)


def test_approval_prompt_data_matches_the_gallery(manager, contract):
    card = contract["approval"]

    class Request:
        tool_name = "run_shell"
        arguments = {"command": "ls"}
        metadata = None

    canonical, states = _inbox_data(card)
    problems = _compare("parked approval", manager.approval_prompt_data("lead-sid", Request()), canonical, states)
    assert not problems, "\n".join(problems)


def test_question_fields_match_the_gallery(contract):
    from coworker.tools.ask import question_item_fields

    card = contract["question"]
    for state_id, payload in card["states"].items():
        fields = question_item_fields(payload)
        assert fields is not None, state_id
        parked = card["inbox"][state_id]
        for key in ("title", "options", "allow_text", "multi", "header"):
            assert _kind(fields[key]) == _kind(parked[key]), f"question/{state_id}: `{key}`"
        assert fields["title"] == parked["title"], f"question/{state_id}: title"
        assert len(fields["questions"]) == len(parked["questions"]), f"question/{state_id}: grouped questions"


def test_lead_decision_payload_matches_the_gallery(manager, contract):
    """`decide_worker_call`: the parked approval carries the worker's call the card shows."""
    card = contract["worker-decision"]
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
    canonical, states = _inbox_data(card)
    problems = _compare("lead decision", data, canonical, states)
    problems += _compare(
        "worker call", data["worker_call"], canonical["worker_call"],
        [s["worker_call"] for s in states if "worker_call" in s],
    )
    problems += _compare("decision arguments", data["arguments"], canonical["arguments"], [s["arguments"] for s in states])
    assert not problems, "\n".join(problems)
