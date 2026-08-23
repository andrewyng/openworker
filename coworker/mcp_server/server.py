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

    {"mcpServers": {"openworker": {"command": "ssh",
                                   "args": ["evo-x2", "~/.local/bin/openworker-mcp"]}}}

The absolute path is not fussiness: `ssh host <command>` is non-interactive, so it runs with
sshd's compiled-in default PATH and the shell's rc file returns early before it can add
~/.local/bin. The bare name is not found there. GET /v1/remote-access prints the resolved path
for this install, which is what the desktop app shows.

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

from ..inbox import (
    KIND_APPROVAL,
    KIND_DIRECTORY,
    KIND_NOTIFICATION,
    KIND_PLAN,
    KIND_QUESTION,
)
from .client import Sidecar, SidecarError

# An approval that the caller did not ask for is the one thing this must never do, so the
# resolution vocabulary is closed and "allow for every future call" is spelled out in full.
RESOLUTIONS = {
    "allow": "allow this one call",
    "deny": "refuse this one call",
    "always": "allow this call AND every future call like it (a standing grant)",
}

# The kinds that take a DECISION rather than an answer. Each one is read back by a different
# approver on the worker, and each reads a different shape (see `wire_resolution`).
DECISION_KINDS = {KIND_APPROVAL, KIND_PLAN, KIND_DIRECTORY}

# The words a model actually reaches for, mapped onto the closed vocabulary. An explicit table,
# not a "does it look positive?" heuristic: everything absent from it is REFUSED rather than
# guessed at, because the cost of guessing wrong is a shell command nobody approved. It exists
# because the sidecar's own mapping (manager.approval_outcome) is case-sensitive and treats
# every unrecognised word as DENY — so 'yes', 'Allow' and 'approve' each denied a call while
# this tool answered "resumed".
_DECISION_WORDS = {
    "allow": "allow", "allowed": "allow", "approve": "allow", "approved": "allow",
    "accept": "allow", "grant": "allow", "granted": "allow", "yes": "allow", "y": "allow",
    "ok": "allow", "okay": "allow", "once": "allow",
    "deny": "deny", "denied": "deny", "decline": "deny", "declined": "deny",
    "refuse": "deny", "reject": "deny", "rejected": "deny", "no": "deny", "n": "deny",
    "always": "always", "always allow": "always", "allow always": "always",
    "always_tool": "always", "every time": "always",
}


def decision(resolution: str) -> str:
    """The canonical 'allow' / 'deny' / 'always' behind a caller's word, or '' if it is not a
    decision at all. Unknown text is not a quiet denial — the caller is told."""
    return _DECISION_WORDS.get(" ".join((resolution or "").lower().split()), "")


def wire_resolution(item: dict[str, Any], word: str) -> str:
    """The resolution string THIS item's kind needs, from the one verb the caller typed.

    Only `approval` answers with a bare word. The plan approver and the directory requester
    (manager.inbox_plan_approver / inbox_directory_requester) parse their resolution as JSON
    and treat anything that is not `{"approved": true}` / `{"granted": true}` as a refusal — so
    posting the literal "allow" that this tool's own docstring asks for silently DENIED the
    request while the caller was told the run had resumed, and the item was already resolved so
    nothing could re-answer it. The shape is the server's business; the caller keeps one verb.
    """
    granted = word in {"allow", "always"}
    kind = item.get("kind")
    if kind == KIND_PLAN:
        if not granted:
            return json.dumps({"approved": False, "feedback": ""})
        # "interactive" is what the in-app Approve button sends: the plan is approved and the
        # writes inside it still ask. Approving from a laptop must not also hand over auto-run.
        return json.dumps({"approved": True, "mode": "interactive"})
    if kind == KIND_DIRECTORY:
        if not granted:
            return json.dumps({"granted": False})
        data = item.get("data") or {}
        # Grant exactly what was asked for, never more: the path and the write flag come from
        # the item, not from the caller, so a remote "allow" cannot widen the request.
        return json.dumps(
            {
                "granted": True,
                "path": str(data.get("path") or ""),
                "writable": bool(data.get("writable")),
            }
        )
    return word


def _decision_facts(item: dict[str, Any]) -> dict[str, Any]:
    """The kind-specific facts the decision actually turns on.

    A folder grant's path and write flag live in `data`, never in the body — the in-app card
    reads them straight from there. Projecting the body alone left an MCP caller approving
    "Grant access to a folder?" with no way to see WHICH folder, or whether it was writable.
    """
    data = item.get("data") or {}
    if item.get("kind") == KIND_DIRECTORY:
        return {"path": str(data.get("path") or ""), "writable": bool(data.get("writable"))}
    return {}


def _resume_note(kind: str, word: str) -> str:
    """What the worker was told, in the caller's words. "resumed" alone was true of a denial
    too, so an inverted answer read exactly like a granted one."""
    if not word:
        return "answer delivered — the run continues on the worker"
    if word == "deny":
        refused = {
            KIND_PLAN: "the plan was rejected",
            KIND_DIRECTORY: "the folder was NOT granted",
        }.get(kind, "the call was refused")
        return f"{refused} — the run continues on the worker and the agent is told no"
    granted = {
        KIND_PLAN: "the plan is approved and execution starts",
        KIND_DIRECTORY: "the folder is granted, exactly as requested",
    }.get(kind, "the call is approved")
    if word == "always" and kind == KIND_APPROVAL:
        granted += ", and so is every future call like it (standing grant)"
    return f"{granted} — the run continues on the worker"


def _fmt_item(item: dict[str, Any]) -> dict[str, Any]:
    """One pending prompt, with enough context to decide without opening the app."""
    out = {
        "id": item.get("id"),
        "kind": item.get("kind"),  # approval | question | notification | directory | plan
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
    facts = _decision_facts(item)
    if facts:
        out["data"] = facts
    # A grouped ask_user (OPE-51) holds up to four questions; `title`/`options` above are only
    # the FIRST. Surfacing just that one made the other answers unaskable — the caller could not
    # know they existed, let alone send the JSON object that answers them.
    questions = item.get("questions") or []
    if questions:
        out["questions"] = questions
    return out


def _how_to_answer(item: dict[str, Any]) -> dict[str, str]:
    """Per kind, the exact call that answers this prompt. All five are listed rather than the
    one that applies, so a caller reading a briefing for one item learns the surface."""
    grouped = bool(item.get("questions"))
    return {
        "approval": "openworker_resolve(item_id, 'allow' | 'deny' | 'always')",
        "plan": "openworker_resolve(item_id, 'allow' | 'deny') — 'allow' starts execution",
        "directory": (
            "openworker_resolve(item_id, 'allow' | 'deny') — 'allow' grants exactly the path "
            "and write flag in item.data, nothing wider"
        ),
        "question": (
            "openworker_resolve(item_id, '{\"<header>\": \"<answer>\", ...}') — a JSON object "
            "answering EVERY entry in item.questions; a bare string answers only the first"
            if grouped
            else "openworker_resolve(item_id, '<your answer as text>')"
        ),
        "notification": "openworker_resolve(item_id, 'seen')",
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
            "how_to_answer": _how_to_answer(item),
            "session_id": sid,
        }

    @mcp.tool()
    @_guard
    def openworker_resolve(item_id: str, resolution: str) -> dict[str, Any]:
        """Answer a pending prompt and let the run continue.

        For anything that asks for a DECISION — an approval, a plan, a folder grant —
        `resolution` is 'allow', 'deny', or 'always' ('always' is a standing grant for every
        future call like it; say so to the user before choosing it). For a question it is the
        answer text. The suspended turn resumes on the worker immediately.

        A decision prompt takes only those words. Anything else is refused and nothing is
        posted, because an unrecognised word reaches the worker as a REFUSAL — being told
        "approved" while the run was denied, with the item spent and unanswerable, is the one
        failure this tool must not have.

        Resolving is first-responder-wins and idempotent: ok=false means someone already
        answered it, not that anything broke.
        """
        text = (resolution or "").strip()
        if not text:
            return {"error": "resolution is required: 'allow', 'deny', 'always', or answer text"}
        # Look the item up first: what has to go on the wire depends on its kind, and posting
        # blind is how "allow" became a denial for the plan and directory kinds.
        items = api.get("/v1/inbox?state=pending").get("items", [])
        item = next((i for i in items if i.get("id") == item_id), None)
        if item is None:
            return {
                "error": (
                    f"no pending item {item_id!r} — it may already be resolved. "
                    "Call inbox_pending for what is still open."
                )
            }
        kind = item.get("kind") or ""
        word = decision(text)
        if kind in DECISION_KINDS:
            if not word:
                return {
                    "error": (
                        f"{item.get('title') or item_id!r} is a {kind} prompt: it takes a "
                        "decision, not free text. Use "
                        + ", ".join(f"{k!r} ({v})" for k, v in RESOLUTIONS.items())
                        + f". Nothing was posted — {text!r} would have reached the worker as a "
                        "refusal."
                    ),
                    "item_id": item_id,
                    "kind": kind,
                }
            posted = wire_resolution(item, word)
        else:
            posted = text
        out = api.post(f"/v1/inbox/{item_id}/resolve", {"resolution": posted})
        ok = bool(out.get("ok"))
        return {
            "ok": ok,
            "item_id": item_id,
            "kind": kind,
            "resolution": word or text,
            # Say what the worker was actually told, not just that something was sent: the
            # caller relays this to a human who cannot see the machine.
            "note": _resume_note(kind, word) if ok else (
                "already resolved elsewhere (first responder wins); nothing changed"
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
