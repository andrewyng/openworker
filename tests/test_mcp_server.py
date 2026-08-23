"""`openworker-mcp` — the SSH-reachable control surface for this install."""

from __future__ import annotations

import asyncio
import json
import types
from dataclasses import asdict
from typing import Any

import pytest

from coworker.engine import ApprovalOutcome, PermissionRequest
from coworker.inbox import VIS_INBOX, InboxStore
from coworker.mcp_server.client import Sidecar, SidecarError
from coworker.mcp_server.server import (
    _fmt_item,
    _latest_report,
    build_server,
    decision,
    wire_resolution,
)
from coworker.server.manager import SessionManager

ITEM = {
    "id": "item-1",
    "kind": "approval",
    "title": "Run shell command",
    "body": "rm -rf build/",
    "created_at": "2026-08-22T04:12:00",
    "session_id": "__run__r9",
    "session_title": "Repo activity brief",
    "session_agent": "repo-ops",
    "session_workspace": "/tmp/ws",
    "options": [],
    "allow_text": True,
}

PLAN_ITEM = {
    **ITEM,
    "id": "plan-1",
    "kind": "plan",
    "title": "Approve the plan?",
    "body": "1. add the budget ledger\n2. wire preflight()",
    "data": {},
}

DIR_ITEM = {
    **ITEM,
    "id": "dir-1",
    "kind": "directory",
    "title": "Grant access to a folder?",
    "body": "I need the raw trial corpus to finish the report.",
    "data": {"path": "/home/u/trials", "writable": True},
}

QUESTION_ITEM = {
    **ITEM,
    "id": "q1",
    "kind": "question",
    "title": "Which bucket?",
    "body": "",
    "options": ["staging", "prod"],
}

TASKS = [
    {
        "id": "task-1", "title": "Repo activity brief", "agent": "repo-ops",
        "schedule": "Every day at ~5:05 AM", "enabled": True, "last_status": "incomplete",
        "run_count": 11, "unseen_runs": 4, "next_run": 200.0, "workspace": "/tmp/ws",
    },
    {
        "id": "task-2", "title": "Morning news briefing", "agent": "research",
        "schedule": "Every day at ~5:20 AM", "enabled": True, "last_status": "ok",
        "run_count": 13, "unseen_runs": 0, "next_run": 100.0, "workspace": "/tmp/ws2",
    },
    {
        "id": "task-3", "title": "Paused thing", "agent": "research", "schedule": "—",
        "enabled": False, "last_status": "ok", "run_count": 3, "unseen_runs": 0,
        "next_run": None, "workspace": "",
    },
]


class FakeSidecar(Sidecar):
    """Stands in for the HTTP sidecar. Records posts so the resume path can be asserted."""

    def __init__(self, items=None, tasks=None, fail: str = "") -> None:
        self.base = "http://127.0.0.1:8765"
        self.token = "t"
        self.items = items if items is not None else [ITEM]
        self.tasks = tasks if tasks is not None else TASKS
        self.posts: list[tuple[str, Any]] = []
        self.fail = fail
        self.resolved_ok = True

    def request(self, method: str, path: str, payload: Any = None) -> Any:
        if self.fail:
            raise SidecarError(self.fail)
        if method == "POST":
            self.posts.append((path, payload))
            return {"ok": self.resolved_ok}
        if path.startswith("/v1/inbox"):
            return {"items": self.items}
        if path == "/v1/automations":
            return {"tasks": self.tasks}
        if path.startswith("/v1/automations/"):
            tid = path.rsplit("/", 1)[-1]
            task = next((t for t in self.tasks if t["id"] == tid), None)
            return {"task": task} if task else {}
        return {}


async def acall(server, name: str, args: dict | None = None) -> Any:
    res = await server.call_tool(name, args or {})
    payload = res[1] if isinstance(res, tuple) else res
    # FastMCP returns the structured result; unwrap the single-value convention.
    if isinstance(payload, dict) and set(payload) == {"result"}:
        return payload["result"]
    return payload


def call(server, name: str, args: dict | None = None) -> Any:
    return asyncio.run(acall(server, name, args))


# -- shaping ------------------------------------------------------------------------------


def test_folder_grants_say_which_folder_and_whether_it_is_writable():
    out = _fmt_item(DIR_ITEM)
    # The one decision-relevant fact of a privilege prompt lives in `data`, not the body — the
    # in-app card reads it from there. Without it the caller approves a write grant blind.
    assert out["data"] == {"path": "/home/u/trials", "writable": True}
    assert "data" not in _fmt_item(ITEM)  # an approval has no folder to name


def test_a_grouped_question_shows_all_of_its_questions():
    grouped = {**QUESTION_ITEM, "questions": [
        {"question": "Which bucket?", "header": "Bucket", "options": ["staging", "prod"]},
        {"question": "Rerun the failed step?", "header": "Rerun"},
        {"question": "Notify the team?", "header": "Notify"},
    ]}
    out = _fmt_item(grouped)
    # title/options carry only the FIRST question by contract. Surfacing just that one made the
    # other two unanswerable — the caller could not know they existed.
    assert [q["header"] for q in out["questions"]] == ["Bucket", "Rerun", "Notify"]


def test_item_carries_enough_to_decide_without_the_app():
    out = _fmt_item(ITEM)
    assert out["kind"] == "approval" and out["detail"] == "rm -rf build/"
    # WHICH automation and persona asked is the difference between an informed approval and a
    # reflexive one.
    assert out["session"]["persona"] == "repo-ops"
    assert out["session"]["title"] == "Repo activity brief"


def test_item_body_is_truncated():
    big = {**ITEM, "body": "x" * 5000}
    assert len(_fmt_item(big)["detail"]) == 2000


def test_latest_report_picks_the_newest_and_skips_focus(tmp_path):
    (tmp_path / "brief-2026-08-20.md").write_text("older")
    newer = tmp_path / "brief-2026-08-22.md"
    newer.write_text("newer")
    import os
    os.utime(tmp_path / "brief-2026-08-20.md", (1, 1))
    (tmp_path / "FOCUS.md").write_text("not a report")

    out = _latest_report(str(tmp_path))
    assert out["text"] == "newer" and out["path"].endswith("brief-2026-08-22.md")
    assert _latest_report(None) is None
    assert _latest_report("/nonexistent") is None


def test_latest_report_reports_truncation(tmp_path):
    (tmp_path / "r-2026-08-22.md").write_text("y" * 30000)
    out = _latest_report(str(tmp_path))
    assert out["truncated"] and len(out["text"]) == 20000


# -- tools --------------------------------------------------------------------------------


def test_status_answers_blocked_and_unhealthy_in_one_call():
    out = call(build_server(FakeSidecar()), "openworker_status")
    assert out["waiting_on_you"] == 1
    assert out["blocked"][0]["id"] == "item-1"
    assert out["automations"]["total"] == 3 and out["automations"]["enabled"] == 2
    unhealthy = [t["title"] for t in out["automations"]["last_run_unhealthy"]]
    assert unhealthy == ["Repo activity brief"]  # "incomplete" counts as unhealthy
    # Soonest first, and a disabled task has no next run to report.
    assert [t["title"] for t in out["automations"]["next_up"]] == [
        "Morning news briefing",
        "Repo activity brief",
    ]


def test_briefing_attaches_the_report_behind_the_prompt(tmp_path):
    (tmp_path / "repo-activity-2026-08-22.md").write_text("# What moved\n- a commit\n")
    item = {**ITEM, "session_workspace": str(tmp_path)}
    task = {**TASKS[0], "workspace": str(tmp_path)}
    out = call(build_server(FakeSidecar(items=[item], tasks=[task])), "inbox_briefing", {"item_id": "item-1"})
    assert out["automation"]["title"] == "Repo activity brief"
    assert "What moved" in out["latest_report"]["text"]
    assert "openworker_resolve" in out["how_to_answer"]["approval"]


def test_the_briefing_documents_every_kind_it_can_show():
    out = call(build_server(FakeSidecar(items=[DIR_ITEM])), "inbox_briefing", {"item_id": "dir-1"})
    # A briefing that only documents approvals and questions leaves the caller to guess at the
    # two kinds whose answer is a JSON object — and guessing wrong denied them.
    assert set(out["how_to_answer"]) == {
        "approval", "plan", "directory", "question", "notification"
    }


def test_a_grouped_question_is_told_how_to_answer_all_of_it():
    grouped = {**QUESTION_ITEM, "questions": [{"question": "A?", "header": "A"},
                                              {"question": "B?", "header": "B"}]}
    out = call(build_server(FakeSidecar(items=[grouped])), "inbox_briefing", {"item_id": "q1"})
    assert "JSON object" in out["how_to_answer"]["question"]


def test_briefing_on_an_already_answered_item_says_so():
    out = call(build_server(FakeSidecar(items=[])), "inbox_briefing", {"item_id": "gone"})
    assert "error" in out and "already be resolved" in out["error"]


def test_resolve_posts_and_reports_the_resume():
    api = FakeSidecar()
    out = call(build_server(api), "openworker_resolve", {"item_id": "item-1", "resolution": "allow"})
    assert api.posts == [("/v1/inbox/item-1/resolve", {"resolution": "allow"})]
    assert out["ok"] and "approved" in out["note"]


def test_resolve_carries_free_text_for_a_question():
    api = FakeSidecar(items=[QUESTION_ITEM])
    call(build_server(api), "openworker_resolve", {"item_id": "q1", "resolution": "use the staging bucket"})
    assert api.posts[0][1] == {"resolution": "use the staging bucket"}


def test_resolve_refuses_an_empty_answer():
    api = FakeSidecar()
    out = call(build_server(api), "openworker_resolve", {"item_id": "item-1", "resolution": "  "})
    # Never guess on the caller's behalf: a blank resolution would post "deny" server-side.
    assert "error" in out and not api.posts


# -- the resolution contract, per kind ----------------------------------------------------
#
# Every one of these covers the same failure: the bridge posting a resolution the approver on
# the worker reads as a REFUSAL, and then reporting "resumed" as if it had been granted. It is
# unrecoverable — the item is spent — and it happens remotely, where nobody can see it.


@pytest.mark.parametrize(
    "word,canonical",
    [("allow", "allow"), ("Allow", "allow"), ("  yes ", "allow"), ("y", "allow"),
     ("approve", "allow"), ("OK", "allow"), ("deny", "deny"), ("no", "deny"),
     ("reject", "deny"), ("always", "always"), ("Always Allow", "always")],
)
def test_a_decision_word_is_read_however_a_model_spells_it(word, canonical):
    # The sidecar's own mapping is case-sensitive and denies anything it does not recognise, so
    # 'yes' and 'Allow' each refused the call while the caller was told it was approved.
    assert decision(word) == canonical


@pytest.mark.parametrize("text", ["ship it", "allow this one call", "looks fine", "sure, go"])
def test_prose_is_not_a_decision(text):
    # Refusing to interpret is the point: a guess that reads as "allow" grants a shell command
    # nobody approved, and a guess that reads as "deny" is the silent inversion again.
    assert decision(text) == ""


def test_a_plan_is_approved_in_the_shape_the_plan_approver_reads():
    # inbox_plan_approver JSON-parses its resolution; the bare word "allow" parses to nothing
    # and falls through to "the user rejected the plan".
    assert json.loads(wire_resolution(PLAN_ITEM, "allow")) == {
        "approved": True,
        "mode": "interactive",  # writes inside the plan still ask — as the in-app button sends
    }
    assert json.loads(wire_resolution(PLAN_ITEM, "deny"))["approved"] is False


def test_a_folder_grant_carries_exactly_what_was_asked_for():
    granted = json.loads(wire_resolution(DIR_ITEM, "allow"))
    # Path and write flag come from the ITEM, never from the caller: a remote "allow" answers
    # the request that was made and cannot widen it.
    assert granted == {"granted": True, "path": "/home/u/trials", "writable": True}
    assert json.loads(wire_resolution(DIR_ITEM, "deny")) == {"granted": False}


def test_an_approval_still_answers_with_the_bare_word():
    assert wire_resolution(ITEM, "allow") == "allow"
    assert wire_resolution(ITEM, "always") == "always"


@pytest.mark.parametrize(
    "item,word,expect",
    [
        (PLAN_ITEM, "allow", {"approved": True, "mode": "interactive"}),
        (PLAN_ITEM, "yes", {"approved": True, "mode": "interactive"}),
        (DIR_ITEM, "allow", {"granted": True, "path": "/home/u/trials", "writable": True}),
        (DIR_ITEM, "deny", {"granted": False}),
    ],
)
def test_the_tool_posts_the_kind_specific_resolution(item, word, expect):
    api = FakeSidecar(items=[item])
    out = call(build_server(api), "openworker_resolve", {"item_id": item["id"], "resolution": word})
    path, payload = api.posts[0]
    assert path == f"/v1/inbox/{item['id']}/resolve"
    assert json.loads(payload["resolution"]) == expect
    assert out["ok"]


@pytest.mark.parametrize("item", [ITEM, PLAN_ITEM, DIR_ITEM])
def test_free_text_on_a_decision_prompt_posts_nothing(item):
    api = FakeSidecar(items=[item])
    out = call(
        build_server(api),
        "openworker_resolve",
        {"item_id": item["id"], "resolution": "yes go ahead, that looks right"},
    )
    # Posting it would spend the item on a refusal. Refusing here leaves it answerable.
    assert not api.posts
    assert "error" in out and "decision" in out["error"]


@pytest.mark.parametrize(
    "item,word,expect",
    [
        (ITEM, "allow", "approved"), (ITEM, "deny", "refused"),
        (ITEM, "always", "every future call"),
        (PLAN_ITEM, "allow", "approved"), (PLAN_ITEM, "deny", "rejected"),
        (DIR_ITEM, "allow", "granted"), (DIR_ITEM, "deny", "NOT granted"),
    ],
)
def test_the_note_says_what_the_worker_was_told(item, word, expect):
    # "resumed" was equally true of a denial, so an inverted answer read exactly like a granted
    # one. The caller relays this line to a human who cannot see the machine.
    out = call(
        build_server(FakeSidecar(items=[item])),
        "openworker_resolve",
        {"item_id": item["id"], "resolution": word},
    )
    assert expect in out["note"]


def test_an_item_that_is_not_pending_is_not_answered_blind():
    api = FakeSidecar(items=[])
    out = call(build_server(api), "openworker_resolve", {"item_id": "gone", "resolution": "allow"})
    # Without the item there is no kind, and without the kind there is no way to know which
    # resolution means yes. Posting anyway is how "allow" became a denial.
    assert not api.posts and "error" in out


def test_second_responder_is_told_nothing_changed():
    api = FakeSidecar()
    api.resolved_ok = False
    out = call(build_server(api), "openworker_resolve", {"item_id": "item-1", "resolution": "deny"})
    assert out["ok"] is False and "already resolved" in out["note"]


def test_a_dead_sidecar_answers_readably_instead_of_crashing():
    api = FakeSidecar(fail="cannot reach OpenWorker at http://127.0.0.1:8765")
    out = call(build_server(api), "openworker_status")
    # The caller is an agent; a transport-level explosion tells it nothing it can act on.
    assert "cannot reach OpenWorker" in out["error"]


def test_automations_list_reports_health_per_task():
    out = call(build_server(FakeSidecar()), "automations_list")
    assert out["count"] == 3
    row = next(a for a in out["automations"] if a["id"] == "task-1")
    assert row["persona"] == "repo-ops" and row["unseen_runs"] == 4


def test_automation_report_returns_the_deliverable(tmp_path):
    (tmp_path / "brief-2026-08-22.md").write_text("the actual report")
    task = {**TASKS[0], "workspace": str(tmp_path)}
    out = call(build_server(FakeSidecar(tasks=[task])), "automation_report", {"task_id": "task-1"})
    assert out["report"]["text"] == "the actual report"


def test_automation_report_without_one_says_so():
    out = call(build_server(FakeSidecar()), "automation_report", {"task_id": "task-3"})
    assert "not written a report" in json.dumps(out["report"])


def test_unknown_automation_is_an_error_not_an_empty_report():
    out = call(build_server(FakeSidecar()), "automation_report", {"task_id": "nope"})
    assert "error" in out


# -- end to end: the bridge and the approver waiting on the worker -------------------------


class _Worker:
    """Just enough SessionManager for its real approver closures to run.

    Those closures are what PARSES the resolution, so a test that stopped at "the bridge posted
    the right JSON" would only restate the assumption that was wrong. Bound to the real unbound
    methods, this fails loudly if the worker side ever changes shape.
    """

    def __init__(self, store) -> None:
        self.inbox = store
        self.inbox_routing = types.SimpleNamespace(route_for=lambda sid, agent: "default")
        self.granted: list[tuple[str, bool]] = []

    def persist_session(self, session_id: str) -> None:
        pass

    async def mirror_inbox_item(self, item) -> None:
        pass

    def approval_prompt_data(self, session_id, request) -> dict:
        return {}

    def add_root(self, session_id, path, writable) -> dict:
        self.granted.append((path, bool(writable)))
        return {"ok": True}

    approval_outcome = SessionManager.approval_outcome


class LiveSidecar(Sidecar):
    """The two routes the bridge uses, over a real InboxStore — same VIS_INBOX filter and the
    same enriched shape app.py returns."""

    def __init__(self, store) -> None:
        self.base = "http://127.0.0.1:8765"
        self.token = "t"
        self.store = store

    def request(self, method: str, path: str, payload: Any = None) -> Any:
        if method == "POST":
            item_id = path.split("/")[3]
            return {"ok": self.store.resolve(item_id, str(payload.get("resolution", "deny")))}
        if path.startswith("/v1/inbox"):
            pending = self.store.list(state="pending", visibility=VIS_INBOX)
            return {
                "items": [
                    {**asdict(i), "session_title": "Overnight run",
                     "session_agent": "research", "session_workspace": ""}
                    for i in pending
                ]
            }
        return {"tasks": []}


async def _park(worker, kind: str):
    """Start the real approver for `kind` and wait until its prompt is in the Inbox."""
    if kind == "plan":
        coro = SessionManager.inbox_plan_approver(worker, "s1", "research")(
            {"plan": "1. budget ledger  2. preflight()"}, "tc-1"
        )
    elif kind == "directory":
        coro = SessionManager.inbox_directory_requester(worker, "s1", "research")(
            {"path": "/data/trials", "writable": False, "reason": "the raw trial corpus"}, "tc-1"
        )
    else:
        coro = SessionManager.inbox_approver(worker, "s1", "research")(
            PermissionRequest(
                tool_name="shell",
                arguments={"command": "pip install -U torch"},
                metadata=None,
                reason="pip install -U torch",
                tool_call_id="tc-1",
            )
        )
    task = asyncio.create_task(coro)
    for _ in range(50):
        if worker.inbox.pending():
            break
        await asyncio.sleep(0)
    return task, worker.inbox.pending()[0].id


def _was_granted(kind: str, result: Any) -> bool:
    if kind == "approval":
        return result is not ApprovalOutcome.DENY
    return bool(result.get("approved" if kind == "plan" else "granted"))


@pytest.mark.parametrize("kind", ["approval", "plan", "directory"])
@pytest.mark.parametrize("word,granted", [("allow", True), ("yes", True), ("deny", False)])
def test_a_decision_reaches_the_approver_as_the_caller_meant_it(kind, word, granted):
    """The whole point of the bridge. Approving from a laptop DENIED a plan and a folder
    request — and reported "resumed" — because both approvers read their resolution as JSON and
    the bridge posted the bare word "allow". The item is resolved once, so the phase was
    unrecoverable and the user was told the opposite of what happened."""
    store = InboxStore()
    worker = _Worker(store)
    server = build_server(LiveSidecar(store))

    async def scenario():
        task, item_id = await _park(worker, kind)
        out = await acall(server, "openworker_resolve", {"item_id": item_id, "resolution": word})
        assert out["ok"], out
        return out, await asyncio.wait_for(task, 2)

    out, seen_by_the_agent = asyncio.run(scenario())
    assert _was_granted(kind, seen_by_the_agent) is granted, seen_by_the_agent
    # …and the caller was told the same thing the agent was.
    assert ("not" not in out["note"].lower() and "refus" not in out["note"]
            and "reject" not in out["note"]) is granted


def test_always_reaches_an_approval_as_a_standing_grant():
    store = InboxStore()
    worker = _Worker(store)
    server = build_server(LiveSidecar(store))

    async def scenario():
        task, item_id = await _park(worker, "approval")
        await acall(server, "openworker_resolve", {"item_id": item_id, "resolution": "always"})
        return await asyncio.wait_for(task, 2)

    assert asyncio.run(scenario()) is ApprovalOutcome.ALWAYS_TOOL


def test_granting_a_folder_adds_exactly_the_root_that_was_asked_for():
    store = InboxStore()
    worker = _Worker(store)
    server = build_server(LiveSidecar(store))

    async def scenario():
        task, item_id = await _park(worker, "directory")
        # The caller never names a path — it cannot widen the request, only answer it.
        await acall(server, "openworker_resolve", {"item_id": item_id, "resolution": "allow"})
        return await asyncio.wait_for(task, 2)

    result = asyncio.run(scenario())
    assert result == {"granted": True, "path": "/data/trials", "writable": False}
    assert worker.granted == [("/data/trials", False)]


def test_the_folder_the_caller_is_asked_about_is_in_the_payload():
    store = InboxStore()
    worker = _Worker(store)
    server = build_server(LiveSidecar(store))

    async def scenario():
        task, _ = await _park(worker, "directory")
        pending = await acall(server, "inbox_pending")
        store.resolve(store.pending()[0].id, json.dumps({"granted": False}))
        await asyncio.wait_for(task, 2)
        return pending

    pending = asyncio.run(scenario())
    assert pending["items"][0]["data"] == {"path": "/data/trials", "writable": False}


# -- surface ------------------------------------------------------------------------------


def test_the_published_tool_surface_is_what_we_intend():
    server = build_server(FakeSidecar())
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert set(tools) == {
        "openworker_status",
        "inbox_pending",
        "inbox_briefing",
        "openworker_resolve",
        "automations_list",
        "automation_report",
        "brain_recall",
    }
    # The error-guard decorator must not leak its *args/**kwargs into the published schema —
    # unwrapped, every tool advertises two bogus required parameters and no client can call it.
    assert tools["openworker_status"].inputSchema.get("properties") == {}
    resolve = tools["openworker_resolve"].inputSchema
    assert set(resolve["required"]) == {"item_id", "resolution"}


@pytest.mark.parametrize("name", ["openworker_status", "openworker_resolve", "inbox_briefing"])
def test_every_tool_documents_itself(name):
    server = build_server(FakeSidecar())
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    # The caller is a model choosing between tools it has never seen; the description is the
    # only thing it has to go on.
    assert (tools[name].description or "").strip()


def test_token_is_read_from_the_state_dir(tmp_path):
    (tmp_path / "sidecar-8765.token").write_text("secret-token\n")
    api = Sidecar(base="http://127.0.0.1:8765", state_dir=tmp_path)
    # Stripped: the file carries a trailing newline and a header with it would 401.
    assert api.token == "secret-token"


def test_a_missing_token_file_is_empty_not_an_exception(tmp_path):
    assert Sidecar(base="http://127.0.0.1:8765", state_dir=tmp_path).token == ""


def test_a_sidecar_that_never_answers_is_a_readable_error(tmp_path, monkeypatch):
    def hang(*_a, **_kw):
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", hang)
    api = Sidecar(base="http://127.0.0.1:8765", state_dir=tmp_path)
    # A socket timeout is an OSError but NOT a URLError, so it slipped past the reachability
    # branch and reached the caller as a bare MCP transport error ("timed out") naming nothing.
    with pytest.raises(SidecarError) as exc:
        api.get("/v1/inbox?state=pending")
    assert "/v1/inbox" in str(exc.value) and "did not answer" in str(exc.value)

    out = call(build_server(api), "openworker_status")
    assert "did not answer" in out["error"]


def test_a_timed_out_resolve_says_the_answer_may_have_landed(tmp_path, monkeypatch):
    def hang(*_a, **_kw):
        raise TimeoutError()

    monkeypatch.setattr("urllib.request.urlopen", hang)
    api = Sidecar(base="http://127.0.0.1:8765", state_dir=tmp_path)
    with pytest.raises(SidecarError) as exc:
        api.post("/v1/inbox/x/resolve", {"resolution": "allow"})
    # The sidecar holds the resolve open for the whole resumed turn, so a timeout does NOT mean
    # the answer was lost. Re-answering a spent item is exactly the mistake to avoid.
    assert "may still have landed" in str(exc.value)
