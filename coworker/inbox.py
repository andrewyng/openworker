"""The Inbox — the canonical, cross-session human-attention queue.

While a user works in one session (or is away with a session running Unattended), the Inbox
holds what other agents need from them: an **approval**, a **question**, or a **notification**.
It is the store of record; messaging connectors / mobile (Phase 3) are transports of the same
items.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

KIND_APPROVAL = "approval"
KIND_QUESTION = "question"
KIND_NOTIFICATION = "notification"
KIND_DIRECTORY = "directory"  # agent asks to be granted a folder
KIND_PLAN = "plan"  # agent presents a plan for approval
KIND_TOOL = "tool"  # agent asks for a missing CLI tool to be installed

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


def is_expired(item: InboxItem, now: Optional[datetime] = None) -> bool:
    """True if the item has an expires_at timestamp that is in the past."""
    if not item.expires_at:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        exp = datetime.fromisoformat(item.expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return now >= exp
    except (ValueError, TypeError):
        return False


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
        None  # approval: "allow"/"deny"/"always"/"expired"; question: answer text
    )
    inbox: str = "default"  # named inbox / delivery binding (Phase 3 routing)
    created_at: str = field(default_factory=_now)
    resolved_at: Optional[str] = None
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
    # Optional expiry timestamp (ISO-8601 UTC). When elapsed, item auto-resolves as "expired".
    expires_at: Optional[str] = None


class InboxStore:
    def __init__(
        self,
        path: Optional[str | Path] = None,
        *,
        default_ttl_seconds: Optional[float] = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.default_ttl_seconds = default_ttl_seconds
        self._lock = threading.Lock()
        self._items: dict[str, InboxItem] = {}
        self._waiters: dict[str, asyncio.Event] = {}
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

    def _compute_expires_at(
        self,
        expires_at: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> Optional[str]:
        if expires_at:
            return expires_at
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        if ttl is not None and ttl > 0:
            return (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
        return None

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
        expires_at: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> InboxItem:
        # Idempotent by (session_id, tool_call_id): a durable resume re-raises the same prompt, and
        # must reuse the existing (possibly already-resolved) item rather than re-prompt.
        if tool_call_id:
            existing = self.for_tool_call(session_id, tool_call_id)
            if existing is not None:
                return existing
        computed_expires_at = self._compute_expires_at(expires_at, ttl_seconds)
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
            expires_at=computed_expires_at,
        )
        with self._lock:
            self._items[item.id] = item
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
        expires_at=None,
        ttl_seconds=None,
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
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
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
        expires_at=None,
        ttl_seconds=None,
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
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
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
        expires_at=None,
        ttl_seconds=None,
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
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
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
        expires_at=None,
        ttl_seconds=None,
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
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
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
        expires_at=None,
        ttl_seconds=None,
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
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
        )

    def add_notification(
        self,
        session_id,
        title,
        *,
        body="",
        inbox="default",
        visibility=VIS_INBOX,
        expires_at=None,
        ttl_seconds=None,
    ) -> InboxItem:
        return self.add(
            session_id,
            KIND_NOTIFICATION,
            title,
            body=body,
            inbox=inbox,
            visibility=visibility,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
        )

    # -- queries ----------------------------------------------------------------
    def _check_expirations_locked(self) -> list[InboxItem]:
        """Check all pending items and auto-resolve any whose TTL has elapsed."""
        expired: list[InboxItem] = []
        now = datetime.now(timezone.utc)
        for item in self._items.values():
            if item.state == STATE_PENDING and is_expired(item, now=now):
                item.state = STATE_RESOLVED
                item.resolution = "expired"
                item.resolved_at = now.isoformat()
                expired.append(item)
                ev = self._waiters.get(item.id)
                if ev is not None:
                    ev.set()
        if expired:
            self._save()
        return expired

    def check_expirations(self) -> list[InboxItem]:
        """Check all pending items and auto-resolve any whose TTL has elapsed."""
        with self._lock:
            return self._check_expirations_locked()

    def get(self, item_id: str) -> Optional[InboxItem]:
        with self._lock:
            self._check_expirations_locked()
            return self._items.get(item_id)

    def list(
        self,
        *,
        session_id: Optional[str] = None,
        state: Optional[str] = None,
        inbox: Optional[str] = None,
        visibility: Optional[str] = None,
    ) -> list[InboxItem]:
        with self._lock:
            self._check_expirations_locked()
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
    def resolve(self, item_id: str, resolution: str, *, force: bool = False) -> bool:
        """Resolve an item exactly once. First responder wins; later attempts are no-ops
        (return False). If the item's TTL has elapsed, resolving with a user decision is
        rejected: the item auto-resolves as 'expired' to prevent acting on stale consent."""
        with self._lock:
            item = self._items.get(item_id)
            if item is None or item.state == STATE_RESOLVED:
                return False
            if not force and is_expired(item):
                item.state = STATE_RESOLVED
                item.resolution = "expired"
                item.resolved_at = _now()
                self._save()
                waiter = self._waiters.get(item_id)
                if waiter is not None:
                    waiter.set()
                return False
            item.state = STATE_RESOLVED
            item.resolution = resolution
            item.resolved_at = _now()
            self._save()
        waiter = self._waiters.get(item_id)
        if waiter is not None:
            waiter.set()
        return True

    def resolve_session(
        self, session_id: str, resolution: str = "session deleted"
    ) -> int:
        """Resolve every still-pending item of a session (called when the session is deleted —
        an orphaned approval/question can never be meaningfully answered). Releases any waiter
        the usual way; returns how many items were closed."""
        closed = 0
        for item in self.pending(session_id):
            if self.resolve(item.id, resolution, force=True):
                closed += 1
        return closed

    async def wait(self, item_id: str) -> str:
        """Await an item's resolution; returns the resolution string. Used by the approver to
        suspend the agent until a human answers (from any surface). If the item has an expiry
        and elapses before resolution, it auto-resolves as 'expired'."""
        with self._lock:
            self._check_expirations_locked()
            item = self._items.get(item_id)
            if item is not None and item.state == STATE_RESOLVED:
                return item.resolution or ""
            timeout: Optional[float] = None
            if item is not None and item.expires_at:
                try:
                    exp = datetime.fromisoformat(item.expires_at)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    timeout = max(0.0, (exp - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError):
                    pass
            ev = self._waiters.setdefault(item_id, asyncio.Event())

        try:
            if timeout is not None:
                await asyncio.wait_for(ev.wait(), timeout=timeout)
            else:
                await ev.wait()
        except asyncio.TimeoutError:
            # Resolve outside the lock and preserve a concurrent first response.
            self.resolve(item_id, "expired", force=True)

        with self._lock:
            resolved = self._items.get(item_id)
            return (resolved.resolution if resolved else "") or ""

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
    Maps the resolution to an ApprovalOutcome (allow → ONCE, always → ALWAYS_TOOL, expired → EXPIRED, else DENY).
    """
    from .engine import ApprovalOutcome, PermissionRequest

    async def approve(request: "PermissionRequest") -> "ApprovalOutcome":
        item = store.add_approval(
            session_id,
            title=f"Run `{request.tool_name}`?",
            body=request.reason or "",
            inbox=inbox,
            expires_at=getattr(request, "expires_at", None),
            ttl_seconds=getattr(request, "ttl_seconds", None),
        )
        resolution = await store.wait(item.id)
        if resolution == "always":
            return ApprovalOutcome.ALWAYS_TOOL
        if resolution == "allow":
            return ApprovalOutcome.ONCE
        if resolution == "expired":
            return ApprovalOutcome.EXPIRED
        return ApprovalOutcome.DENY

    return approve
