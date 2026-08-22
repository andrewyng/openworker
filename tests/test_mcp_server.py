"""`openworker-mcp` — the SSH-reachable control surface for this install."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from coworker.mcp_server.client import Sidecar, SidecarError
from coworker.mcp_server.server import _fmt_item, _latest_report, build_server

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


def call(server, name: str, args: dict | None = None) -> Any:
    async def go():
        res = await server.call_tool(name, args or {})
        payload = res[1] if isinstance(res, tuple) else res
        # FastMCP returns the structured result; unwrap the single-value convention.
        if isinstance(payload, dict) and set(payload) == {"result"}:
            return payload["result"]
        return payload

    return asyncio.run(go())


# -- shaping ------------------------------------------------------------------------------


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


def test_briefing_on_an_already_answered_item_says_so():
    out = call(build_server(FakeSidecar(items=[])), "inbox_briefing", {"item_id": "gone"})
    assert "error" in out and "already be resolved" in out["error"]


def test_resolve_posts_and_reports_the_resume():
    api = FakeSidecar()
    out = call(build_server(api), "openworker_resolve", {"item_id": "item-1", "resolution": "allow"})
    assert api.posts == [("/v1/inbox/item-1/resolve", {"resolution": "allow"})]
    assert out["ok"] and "resumed" in out["note"]


def test_resolve_carries_free_text_for_a_question():
    api = FakeSidecar()
    call(build_server(api), "openworker_resolve", {"item_id": "q1", "resolution": "use the staging bucket"})
    assert api.posts[0][1] == {"resolution": "use the staging bucket"}


def test_resolve_refuses_an_empty_answer():
    api = FakeSidecar()
    out = call(build_server(api), "openworker_resolve", {"item_id": "item-1", "resolution": "  "})
    # Never guess on the caller's behalf: a blank resolution would post "deny" server-side.
    assert "error" in out and not api.posts


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
