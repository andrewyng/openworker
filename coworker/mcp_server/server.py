"""`openworker-mcp` — drive this OpenWorker install from an agent on another machine.

The problem it solves: automations run unattended at 04:00 and stop dead the moment one needs a
human — an approval, an answer, a plan to accept. Until someone opens the desktop app, the run
is parked and the phase after it never starts. That is fine when you are at the machine and
useless when you are not.

This exposes the Inbox and the schedule as MCP tools, so any agent you already talk to (Claude
Code on a laptop, another OpenWorker, anything MCP-speaking) can read the briefing, approve or
deny, and let the run continue.

TRANSPORT IS SSH, deliberately. The server speaks stdio and talks to 127.0.0.1 — it opens no
port. From the host machine:

    {"mcpServers": {"openworker": {"command": "ssh", "args": ["evo-x2", "openworker-mcp"]}}}

SSH is the authentication, the encryption and the audit trail; revoking access is removing a
key. The alternative — exposing the sidecar's HTTP port to the network — would mean a second
credential system guarding an API that can approve shell commands, which is not a trade worth
making for a convenience feature.

Resolving an item resumes the suspended turn through the sidecar's normal durable-resume path
(manager.resolve_inbox), so a phase approved from a laptop continues on the worker exactly as
if it had been approved in the app.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any, Optional

from .client import Sidecar, SidecarError

# An approval that the caller did not ask for is the one thing this must never do, so the
# resolution vocabulary is closed and "allow for every future call" is spelled out in full.
RESOLUTIONS = {
    "allow": "allow this one call",
    "deny": "refuse this one call",
    "always": "allow this call AND every future call like it (a standing grant)",
}


def _fmt_item(item: dict[str, Any]) -> dict[str, Any]:
    """One pending prompt, with enough context to decide without opening the app."""
    return {
        "id": item.get("id"),
        "kind": item.get("kind"),  # approval | question | planreq | dirreq
        "title": item.get("title"),
        "detail": (item.get("body") or "")[:2000],
        "asked_at": item.get("created_at"),
        "session": {
            "id": item.get("session_id"),
            "title": item.get("session_title"),
            "persona": item.get("session_agent"),
            "workspace": item.get("session_workspace"),
        },
        # ask_user prompts carry their own choices; an approval does not.
        "options": item.get("options") or [],
        "free_text_allowed": item.get("allow_text", True),
    }


def _latest_report(workspace: Optional[str]) -> Optional[dict[str, Any]]:
    """The newest markdown a task left behind — the briefing the caller is being asked about."""
    if not workspace:
        return None
    d = Path(workspace)
    if not d.is_dir():
        return None
    files = [f for f in d.glob("*.md") if f.name != "FOCUS.md"]
    if not files:
        return None
    newest = max(files, key=lambda f: f.stat().st_mtime)
    try:
        text = newest.read_text(encoding="utf-8")
    except OSError:
        return None
    return {"path": str(newest), "text": text[:20000], "truncated": len(text) > 20000}


def build_server(sidecar: Optional[Sidecar] = None):
    """The MCP server. Built lazily so importing this module does not require the SDK."""
    from mcp.server.fastmcp import FastMCP

    api = sidecar or Sidecar()
    mcp = FastMCP("openworker")

    def _guard(fn):
        """Report a sidecar failure as a readable answer rather than an MCP transport error —
        the caller is an agent, and "cannot reach OpenWorker" is something it can act on.

        `functools.wraps` matters here beyond tidiness: FastMCP derives each tool's argument
        schema from the callable's signature, and an unwrapped `*args, **kwargs` shim publishes
        two required parameters named `args` and `kwargs`. `wraps` sets `__wrapped__`, which
        `inspect.signature` follows to the real parameters.
        """

        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except SidecarError as exc:
                return {"error": str(exc)}

        return wrapped

    @mcp.tool()
    @_guard
    def openworker_status() -> dict[str, Any]:
        """What needs a human right now, and what the schedule is doing. Start here: it answers
        'is anything blocked?' and 'did anything fail overnight?' in one call."""
        items = api.get("/v1/inbox?state=pending").get("items", [])
        tasks = api.get("/v1/automations").get("tasks", [])
        failed = [
            t for t in tasks if t.get("last_status") in {"error", "incomplete"}
        ]
        upcoming = sorted(
            (t for t in tasks if t.get("enabled") and t.get("next_run")),
            key=lambda t: t["next_run"],
        )[:5]
        return {
            "waiting_on_you": len(items),
            "blocked": [_fmt_item(i) for i in items[:10]],
            "automations": {
                "total": len(tasks),
                "enabled": sum(1 for t in tasks if t.get("enabled")),
                "last_run_unhealthy": [
                    {"id": t["id"], "title": t["title"], "status": t.get("last_status")}
                    for t in failed
                ],
                "next_up": [
                    {"title": t["title"], "at": t.get("next_run"), "schedule": t.get("schedule")}
                    for t in upcoming
                ],
            },
        }

    @mcp.tool()
    @_guard
    def inbox_pending() -> dict[str, Any]:
        """Every prompt an unattended run is currently parked on, with the session and persona
        that raised it. Each one is blocking a turn until it is resolved."""
        items = api.get("/v1/inbox?state=pending").get("items", [])
        return {"count": len(items), "items": [_fmt_item(i) for i in items]}

    @mcp.tool()
    @_guard
    def inbox_briefing(item_id: str) -> dict[str, Any]:
        """The full context behind one pending prompt: what is being asked, which automation
        asked it, and the most recent report that automation wrote. Read this before approving
        anything you did not watch happen."""
        items = api.get("/v1/inbox?state=pending").get("items", [])
        item = next((i for i in items if i.get("id") == item_id), None)
        if item is None:
            return {"error": f"no pending item {item_id!r} — it may already be resolved"}
        tasks = api.get("/v1/automations").get("tasks", [])
        sid = item.get("session_id") or ""
        # A run session is `__run__<id>`; its task owns the workspace the report lives in.
        task = next(
            (t for t in tasks if t.get("workspace") and t["workspace"] == item.get("session_workspace")),
            None,
        )
        return {
            "item": _fmt_item(item),
            "automation": {"id": task["id"], "title": task["title"]} if task else None,
            "latest_report": _latest_report(item.get("session_workspace")),
            "how_to_answer": {
                "approval": "openworker_resolve(item_id, 'allow' | 'deny' | 'always')",
                "question": "openworker_resolve(item_id, '<your answer as text>')",
            },
            "session_id": sid,
        }

    @mcp.tool()
    @_guard
    def openworker_resolve(item_id: str, resolution: str) -> dict[str, Any]:
        """Answer a pending prompt and let the run continue.

        For an approval, `resolution` is 'allow', 'deny', or 'always' (a standing grant for
        every future call like it — say so to the user before choosing it). For a question, it
        is the answer text. The suspended turn resumes on the worker immediately.

        Resolving is first-responder-wins and idempotent: ok=false means someone already
        answered it, not that anything broke.
        """
        text = (resolution or "").strip()
        if not text:
            return {"error": "resolution is required: 'allow', 'deny', 'always', or answer text"}
        out = api.post(f"/v1/inbox/{item_id}/resolve", {"resolution": text})
        return {
            "ok": bool(out.get("ok")),
            "item_id": item_id,
            "resolution": text,
            "note": (
                "resumed — the run continues on the worker"
                if out.get("ok")
                else "already resolved elsewhere (first responder wins); nothing changed"
            ),
        }

    @mcp.tool()
    @_guard
    def automations_list() -> dict[str, Any]:
        """Every scheduled automation with its cadence, last outcome and next run."""
        tasks = api.get("/v1/automations").get("tasks", [])
        return {
            "count": len(tasks),
            "automations": [
                {
                    "id": t["id"],
                    "title": t["title"],
                    "persona": t.get("agent"),
                    "schedule": t.get("schedule"),
                    "enabled": t.get("enabled"),
                    "last_status": t.get("last_status"),
                    "runs": t.get("run_count"),
                    "unseen_runs": t.get("unseen_runs", 0),
                }
                for t in tasks
            ],
        }

    @mcp.tool()
    @_guard
    def automation_report(task_id: str) -> dict[str, Any]:
        """The most recent report an automation wrote — the deliverable, not a status line."""
        detail = api.get(f"/v1/automations/{task_id}")
        task = detail.get("task")
        if not task:
            return {"error": f"no automation {task_id!r}"}
        report = _latest_report(task.get("workspace"))
        return {
            "automation": {"id": task["id"], "title": task["title"]},
            "last_status": task.get("last_status"),
            "report": report or {"note": "this automation has not written a report yet"},
        }

    @mcp.tool()
    @_guard
    def brain_recall(query: str, limit: int = 6) -> dict[str, Any]:
        """Search what this machine has learned before — durable subject threads and the dated
        reports behind them. The same memory the local personas read."""
        from ..brain import recall as do_recall

        n = limit if isinstance(limit, int) and 0 < limit <= 25 else 6
        return do_recall(query, limit=n).as_dict()

    return mcp


def main(argv: Optional[list[str]] = None) -> int:
    """stdio entry point. Prints a readable line to stderr on a failed handshake, because the
    caller is usually an MCP client that shows nothing but 'server exited'."""
    import sys

    try:
        server = build_server()
    except Exception as exc:  # pragma: no cover - import/environment failure
        print(f"openworker-mcp: cannot start: {exc}", file=sys.stderr)
        return 1
    server.run()
    return 0
