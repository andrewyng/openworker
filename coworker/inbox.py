"""The durable wait queue for prompts — NOT the Inbox surface.

NAMING (owner ruling 2026-09-05): this module and ``InboxStore`` are the **durable wait
queue**: every prompt a session parks while it waits for a human — an approval, a
question, a folder/plan/staffing/connector gate — lives here as an item so it survives a
dropped socket or a restart and can be answered from any surface. Think of it as
``DurableWaitQueue``. The *Inbox* the user sees is a different thing: the cross-session
list that shows ONLY items whose ``visibility`` is ``inbox`` — which a session gets only
when the user set it Unattended. An attended session's items are ``inline``: they render
as a card in that session and never appear in the Inbox. When writing about this code,
say "parks the prompt and waits", never "sends it to the Inbox"; the Inbox is the
unattended dial and nothing else. (The class and module names stay for one release —
renaming them touches every surface — but new code should not spread the old name.)

Item state machine (the anti-race contract): each item is ``pending → resolved``, resolved
**once**, idempotent + first-responder-wins — so answering from any surface (in-app, Slack, the
composer after resuming) is safe. ``inbox_approver`` turns a permission request into an item and
suspends the agent until that item is resolved.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

KIND_APPROVAL = "approval"
KIND_QUESTION = "question"
KIND_NOTIFICATION = "notification"
KIND_DIRECTORY = "directory"  # agent asks to be granted a folder
KIND_PLAN = "plan"  # agent presents a plan for approval
KIND_TOOL = "tool"  # agent asks for a missing CLI tool to be installed
KIND_CONNECTOR = "connector"  # §11.6: connect a service / grant a worker a connector

STATE_PENDING = "pending"
STATE_RESOLVED = "resolved"

# Where a pending prompt surfaces. INLINE = an attended session answers it in the composer (parked
# server-side, redelivered on reconnect, never in the cross-session list). INBOX = the user set the
# session Unattended, so it joins the cross-session Inbox queue. Either way it's the same parked,
# awaitable, resolve-from-anywhere record — only the visibility differs.
VIS_INLINE = "inline"
VIS_INBOX = "inbox"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def args_preview(arguments: Optional[dict], *, limit: int = 240) -> str:
    """A compact one-line summary of a tool call's arguments, for an approval card body (so a
    mirrored 'Run `write_file`?' shows *what* — path/content — not just the tool name).
    """
    parts: list[str] = []
    for k, v in (arguments or {}).items():
        s = v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False)
        s = " ".join(str(s).split())  # collapse whitespace/newlines
        if len(s) > 80:
            s = s[:79] + "…"
        parts.append(f"{k}: {s}")
    out = " · ".join(parts)
    return out[: limit - 1] + "…" if len(out) > limit else out


@dataclass
class InboxItem:
    id: str
    session_id: str
    kind: str
    title: str
    body: str = ""
    state: str = STATE_PENDING
    resolution: Optional[str] = (
        None  # approval: "allow"/"deny"/"always"; question: answer text
    )
    inbox: str = "default"  # named inbox / delivery binding (Phase 3 routing)
    created_at: str = field(default_factory=_now)
    resolved_at: Optional[str] = None
    # Who resolved it (controller-verified login when the answer came through the cloud;
    # the session's actor for a live in-app answer; "" when unknown/local).
    resolved_by: str = ""
    visibility: str = VIS_INBOX  # inline (attended) vs inbox (unattended)
    # The tool call this prompt is blocking (durable resume: persisted so a restart can rebuild the
    # suspension and continue the turn). Makes an item idempotent by (session_id, tool_call_id).
    tool_call_id: Optional[str] = None
    # Question metadata (ask_user): optional quick-reply choices + a free-text escape, mirroring
    # the structured-but-always-answerable shape of Claude Code's AskUserQuestion.
    # An option is a plain string OR a rich {label, description, recommended, preview} object
    # (OPE-51); old persisted items hold strings and stay valid.
    options: list = field(default_factory=list)
    allow_text: bool = (
        True  # accept a typed answer even when options exist (the "Other" escape)
    )
    multi: bool = False  # allow choosing more than one option
    header: str = ""  # short chip label for the card ("Region")
    # Grouped form (OPE-51): up to 4 {question, header, options, allow_text, multi} entries
    # rendered as a stepper. When non-empty the singular title/options fields above still hold
    # the FIRST question (so old surfaces and channel mirrors degrade to something sensible),
    # and the resolution is a JSON object string keyed by header-or-question.
    questions: list[dict] = field(default_factory=list)
    # Kind-specific payload (directory: suggested path/writable; plan: the plan text; …).
    data: dict[str, Any] = field(default_factory=dict)


class InboxStore:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._items: dict[str, InboxItem] = {}
        self._waiters: dict[str, asyncio.Event] = {}
        self._waiter_loops: dict[str, asyncio.AbstractEventLoop] = {}
        self._load()

    # -- persistence ------------------------------------------------------------
    def _load(self) -> None:
        if self.path and self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for raw in data.get("items", []):
                item = InboxItem(**raw)
                self._items[item.id] = item

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"items": [asdict(i) for i in self._items.values()]}, indent=2),
            encoding="utf-8",
        )

    # -- adding -----------------------------------------------------------------
    def add(
        self,
        session_id: str,
        kind: str,
        title: str,
        *,
        body: str = "",
        inbox: str = "default",
        visibility: str = VIS_INBOX,
        data: Optional[dict[str, Any]] = None,
        options=None,
        allow_text: bool = True,
        multi: bool = False,
        header: str = "",
        questions=None,
        tool_call_id: Optional[str] = None,
    ) -> InboxItem:
        # Idempotent by (session_id, tool_call_id): a durable resume re-raises the same prompt, and
        # must reuse the existing (possibly already-resolved) item rather than re-prompt.
        if tool_call_id:
            existing = self.for_tool_call(session_id, tool_call_id)
            if existing is not None:
                return existing
        item = InboxItem(
            id=uuid.uuid4().hex,
            session_id=session_id,
            kind=kind,
            title=title,
            body=body,
            inbox=inbox,
            visibility=visibility,
            data=dict(data or {}),
            options=list(options or []),
            allow_text=bool(allow_text),
            multi=bool(multi),
            header=str(header or ""),
            questions=list(questions or []),
            tool_call_id=tool_call_id,
        )
        with self._lock:
            self._items[item.id] = item
            source_id = item.data.get("worker_prompt_id")
            source = self._items.get(source_id) if isinstance(source_id, str) else None
            if source is not None and source.state == STATE_RESOLVED:
                self._supersede_locked(item, source)
            self._save()
        return item

    def for_tool_call(self, session_id: str, tool_call_id: str) -> Optional[InboxItem]:
        for i in self._items.values():
            if i.session_id == session_id and i.tool_call_id == tool_call_id:
                return i
        return None

    def add_approval(
        self,
        session_id,
        title,
        *,
        body="",
        inbox="default",
        visibility=VIS_INBOX,
        data=None,
        tool_call_id=None,
    ) -> InboxItem:
        # `data` carries the automation-run context for standing scoped approvals (§25):
        # {task_id, task_title, standing_target?} — the in-app card's "Allow every time" gate.
        return self.add(
            session_id,
            KIND_APPROVAL,
            title,
            body=body,
            inbox=inbox,
            visibility=visibility,
            data=data,
            tool_call_id=tool_call_id,
        )

    def add_question(
        self,
        session_id,
        title,
        *,
        body="",
        inbox="default",
        visibility=VIS_INBOX,
        options=None,
        allow_text=True,
        multi=False,
        header="",
        questions=None,
        tool_call_id=None,
    ) -> InboxItem:
        return self.add(
            session_id,
            KIND_QUESTION,
            title,
            body=body,
            inbox=inbox,
            visibility=visibility,
            options=options,
            allow_text=allow_text,
            multi=multi,
            header=header,
            questions=questions,
            tool_call_id=tool_call_id,
        )

    def add_directory(
        self,
        session_id,
        title,
        *,
        body="",
        inbox="default",
        visibility=VIS_INBOX,
        data=None,
        tool_call_id=None,
    ) -> InboxItem:
        return self.add(
            session_id,
            KIND_DIRECTORY,
            title,
            body=body,
            inbox=inbox,
            visibility=visibility,
            data=data,
            tool_call_id=tool_call_id,
        )

    def add_plan(
        self,
        session_id,
        title,
        *,
        body="",
        inbox="default",
        visibility=VIS_INBOX,
        data=None,
        tool_call_id=None,
    ) -> InboxItem:
        return self.add(
            session_id,
            KIND_PLAN,
            title,
            body=body,
            inbox=inbox,
            visibility=visibility,
            data=data,
            tool_call_id=tool_call_id,
        )

    def add_tool_request(
        self,
        session_id,
        title,
        *,
        body="",
        inbox="default",
        visibility=VIS_INBOX,
        data=None,
        tool_call_id=None,
    ) -> InboxItem:
        return self.add(
            session_id,
            KIND_TOOL,
            title,
            body=body,
            inbox=inbox,
            visibility=visibility,
            data=data,
            tool_call_id=tool_call_id,
        )

    def add_connector_request(
        self,
        session_id,
        title,
        *,
        body="",
        inbox="default",
        visibility=VIS_INBOX,
        data=None,
        tool_call_id=None,
    ) -> InboxItem:
        return self.add(
            session_id,
            KIND_CONNECTOR,
            title,
            body=body,
            inbox=inbox,
            visibility=visibility,
            data=data,
            tool_call_id=tool_call_id,
        )

    def add_notification(
        self, session_id, title, *, body="", inbox="default", visibility=VIS_INBOX
    ) -> InboxItem:
        return self.add(
            session_id,
            KIND_NOTIFICATION,
            title,
            body=body,
            inbox=inbox,
            visibility=visibility,
        )

    # -- queries ----------------------------------------------------------------
    def get(self, item_id: str) -> Optional[InboxItem]:
        return self._items.get(item_id)

    def resolver_of(self, session_id: str, tool_call_id: str) -> str:
        """Who resolved the item gating this tool call ("" if none/unknown)."""
        if not tool_call_id:
            return ""
        for item in self._items.values():
            if item.session_id == session_id and item.tool_call_id == tool_call_id and item.state == STATE_RESOLVED:
                return item.resolved_by or ""
        return ""

    def list(
        self,
        *,
        session_id: Optional[str] = None,
        state: Optional[str] = None,
        inbox: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> list[InboxItem]:
        out = list(self._items.values())
        if session_id is not None:
            out = [i for i in out if i.session_id == session_id]
        if state is not None:
            out = [i for i in out if i.state == state]
        if inbox is not None:
            out = [i for i in out if i.inbox == inbox]
        if visibility is not None:
            out = [i for i in out if i.visibility == visibility]
        return sorted(out, key=lambda i: i.created_at)

    def pending(self, session_id: Optional[str] = None) -> list[InboxItem]:
        return self.list(session_id=session_id, state=STATE_PENDING)

    # -- the state machine ------------------------------------------------------
    @staticmethod
    def _resolve_locked(item: InboxItem, resolution: str, by: str) -> None:
        item.state = STATE_RESOLVED
        item.resolution = resolution
        item.resolved_at = _now()
        item.resolved_by = (by or "").strip()

    @classmethod
    def _supersede_locked(cls, item: InboxItem, source: InboxItem) -> None:
        cls._resolve_locked(item, "superseded", "system:worker-resolution")
        if isinstance(item.data.get("worker_call"), dict):
            item.data["worker_call"] = {
                **item.data["worker_call"],
                "state": source.state,
                "resolution": source.resolution,
            }

    def resolve(self, item_id: str, resolution: str, by: str = "") -> bool:
        """Resolve an item exactly once. First responder wins; later attempts are no-ops
        (return False). Fires any awaiting agent (the suspended inbox_approver).
        `by` = who decided (spec §Fleet under the org: the audit row of the tool call
        this item gated is stamped `approved_by` from it)."""
        with self._lock:
            item = self._items.get(item_id)
            if item is None or item.state == STATE_RESOLVED:
                return False
            self._resolve_locked(item, resolution, by)
            resolved_ids = [item_id]
            source_id = item.data.get("worker_prompt_id")
            source = self._items.get(source_id) if isinstance(source_id, str) else None
            args = item.data.get("arguments") or {}
            # Server-stamped ownership link only. Denying permission to ALLOW a worker
            # action means deny that action, not leave it parked indefinitely. Never
            # invert a proposed denial into an allow, or propagate stop/delete/errors.
            if (resolution == "deny" and item.kind == KIND_APPROVAL
                    and item.data.get("tool") == "decide_worker_call"
                    and isinstance(args, dict) and args.get("decision") == "allow"
                    and args.get("call_id") == source_id
                    and source is not None and source.kind == KIND_APPROVAL
                    and source.state == STATE_PENDING):
                self._resolve_locked(source, "deny", by)
                resolved_ids.append(source.id)
                if isinstance(item.data.get("worker_call"), dict):
                    item.data["worker_call"] = {**item.data["worker_call"], "state": STATE_RESOLVED, "resolution": "deny"}
            for dependent in self._items.values():
                if dependent.state == STATE_PENDING and dependent.data.get("worker_prompt_id") in resolved_ids:
                    self._supersede_locked(dependent, self._items[dependent.data["worker_prompt_id"]])
                    resolved_ids.append(dependent.id)
            self._save()
        for resolved_id in resolved_ids:
            waiter = self._waiters.get(resolved_id)
            if waiter is not None:
                loop = self._waiter_loops.get(resolved_id)
                if loop is not None and not loop.is_closed():
                    loop.call_soon_threadsafe(waiter.set)
        return True

    def resolve_session(
        self, session_id: str, resolution: str = "session deleted"
    ) -> int:
        """Resolve every still-pending item of a session (called when the session is deleted —
        an orphaned approval/question can never be meaningfully answered). Releases any waiter
        the usual way; returns how many items were closed."""
        closed = 0
        for item in self.pending(session_id):
            if self.resolve(item.id, resolution):
                closed += 1
        return closed

    async def wait(self, item_id: str) -> str:
        """Await an item's resolution; returns the resolution string. Used by the approver to
        suspend the agent until a human answers (from any surface)."""
        with self._lock:
            item = self._items.get(item_id)
            if item is not None and item.state == STATE_RESOLVED:
                return item.resolution or ""
            ev = self._waiters.setdefault(item_id, asyncio.Event())
            self._waiter_loops[item_id] = asyncio.get_running_loop()
        await ev.wait()
        resolved = self._items.get(item_id)
        return (resolved.resolution if resolved else "") or ""

    def promote_to_inbox(self, session_id: str) -> list[InboxItem]:
        """Flip a session's still-pending INLINE prompts to Inbox visibility. Called when
        the last live viewer of an attended session disconnects: an inline item shows only
        inside that session, so with nobody watching it the agent would wait invisibly
        (the 2026-09-01 stall — a question asked over the live path, socket dropped, turn
        parked until an engine rebuild re-raised it). Promoted items appear in the
        cross-session Inbox (and mirror to a bound channel) while staying answerable inline
        when the session is reopened. Returns the items that changed."""
        changed: list[InboxItem] = []
        with self._lock:
            for item in self.pending(session_id):
                if item.visibility == VIS_INLINE:
                    item.visibility = VIS_INBOX
                    changed.append(item)
            if changed:
                self._save()
        return changed

    # -- resume reconciliation --------------------------------------------------
    def reconcile_on_resume(self, session_id: str) -> dict:
        """When a user resumes attended control, surface this session's still-pending items
        inline (one place to answer from now on) plus a recap of what was answered while away.
        Single source of truth: every item already has one authoritative resolution."""
        pending = self.pending(session_id)
        recap = [i for i in self.list(session_id=session_id, state=STATE_RESOLVED)]
        return {
            "pending": [asdict(i) for i in pending],
            "recap": [asdict(i) for i in recap],
        }


# -- approver routing -----------------------------------------------------------
def inbox_approver(store: InboxStore, session_id: str, *, inbox: str = "default"):
    """An Approver that routes a permission request to the Inbox and suspends until resolved.
    Maps the resolution to an ApprovalOutcome (allow → ONCE, always → ALWAYS_TOOL, else DENY).
    """
    from .engine import ApprovalOutcome, PermissionRequest

    async def approve(request: "PermissionRequest") -> "ApprovalOutcome":
        item = store.add_approval(
            session_id,
            title=f"Run `{request.tool_name}`?",
            body=request.reason or "",
            inbox=inbox,
        )
        resolution = await store.wait(item.id)
        if resolution == "always":
            return ApprovalOutcome.ALWAYS_TOOL
        if resolution == "allow":
            return ApprovalOutcome.ONCE
        return ApprovalOutcome.DENY

    return approve
