"""Session manager — owns engines (one per session), stores, and the provider.

Each session is bound to a workspace folder (Code requires one). Storage is a single DB
under a data dir (global for the real server, per-workspace for tests), so recents and
sessions span folders.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ..agent import build_engine
from ..agents import get_agent
from ..connections import (
    PersonaConnectionStore,
    SessionConnectionStore,
    effective as effective_connections,
)
from ..inbox import InboxStore, args_preview
from ..inbox_routing import InboxRouting
from ..personas import PersonaRegistry
from ..personas.registry import set_registry as set_persona_registry
from ..selfwake import WakeStore
from ..mentions import MentionSessionStore
from ..subscriptions import ChannelBuffer, SubscriptionStore
from ..unrouted import UnroutedStore
from ..unattended import UnattendedRegistry
from ..audit import AuditStore
from ..config import load_config, workspace_allowed_commands
from ..conversations import ConversationStore, title_from
from ..engine import ApprovalOutcome, Approver, TurnEngine
from ..roots import RootDir
from ..workspace_trust import WorkspaceTrustStore
from ..automation import Schedule, ScheduledTask, Scheduler, TaskRun, TaskStore
from ..connectors import (
    Gateway,
    MessageSource,
    connect_connector,
    connector_list,
    disconnect_connector,
    experimental_enabled,
    load_settings,
    make_adapter,
    set_experimental_enabled,
    slack_split,
    update_connector_tools,
)
from ..connectors.browser_automation import (
    browser_close_session,
    browser_state,
    browser_take_screenshot,
)
from ..connectors.parked import ParkedStore
from ..mcp import (
    MCPManager,
    build_callables,
    delete_global_server,
    load_mcp_servers,
    patch_global_server,
    put_global_server,
    read_global,
)
from ..memory import MemorySettingsStore, MemoryStore, Scope, SQLiteMemoryStore
from ..permissions import Mode
from ..agents import list_agents as _list_agents
from ..providers import (
    ProviderClient,
    ProviderRouter,
    descriptor_configured,
    get_descriptor,
    provider_descriptors,
    verify_provider_key,
)
from ..secrets import SecretStore, state_dir
from ..sessions import SessionRecord
from ..teams import Actor as TeamActor
from ..teams import BoardError as TeamsBoardError
from ..teams import JournalStore, Role as TeamRole, TeamStore, board_tools, journal_tools
from ..projects import (
    project_key,
    project_label,
    project_presence,
    resolve_board_space,
    resolve_memory_key,
)
from ..teams.chat import ChatStore
from ..teams.registry import TeamRegistry, TeamWorker
from ..teams.attachments import AttachmentStore
from ..teams.tokens import BoardTokens
from ..skills import (
    SessionSkillStore,
    SkillLoader,
    SkillStore,
    effective_skills,
)

_SCOPES = {s.value for s in Scope}

logger = logging.getLogger("coworker.manager")


def _grants_of(engine) -> dict[str, Any]:
    """The engine's session-scoped "Always allow" approvals, in persistable shape."""
    tools = sorted(getattr(engine.permissions, "session_allow_tools", None) or ())
    commands = sorted(getattr(engine.permissions, "session_allow_commands", None) or ())
    readonly = bool(getattr(engine.permissions, "session_readonly", False))
    out: dict[str, Any] = {}
    if tools or commands or readonly:
        out = {"tools": tools, "commands": commands}
        if readonly:
            out["readonly"] = True
    return out


def _grant_offered(outcome, request) -> bool:
    """Whether a persistent grant is legitimately offered for this tool — the server-side
    mirror of what the approval card actually renders (`ApprovalCard.tsx`).

    - ALWAYS_TOOL is tool-wide and argument-unbounded, so it is withheld from run_shell (the
      command-scoped grant is the narrower option), from save_skill (every skill proposal
      gets its own review), from anything that reaches off the machine — connectors and
      MCP tools alike, where "always allow send_message" would cover every future recipient —
      and from URL-carrying egress (§1.9): "always allow web_fetch" would cover every future
      destination, and the domain-scoped grant is the one the card offers. Fixed-destination
      egress (web_search: no url argument) keeps it — tool-wide IS provider-wide there.
    - ALWAYS_COMMAND only means anything for the shell tool.
    - ALWAYS_DOMAIN only means anything for a tool carrying a url.
    """
    from ..engine import ApprovalOutcome
    from ..risk import RiskClass, classify

    name = getattr(request, "tool_name", "")
    metadata = getattr(request, "metadata", None)
    args = getattr(request, "arguments", None) or {}
    risk = classify(name, metadata)

    if outcome is ApprovalOutcome.ALWAYS_COMMAND:
        return risk is RiskClass.EXEC
    if outcome is ApprovalOutcome.ALWAYS_DOMAIN:
        return risk is RiskClass.EGRESS and bool(args.get("url"))
    if outcome is ApprovalOutcome.ALWAYS_TOOL:
        if risk in (RiskClass.EXEC, RiskClass.EXTERNAL):
            return False
        if risk is RiskClass.EGRESS and args.get("url"):
            return False
        if getattr(metadata, "category", "") == "connector":
            return False
        return name != "save_skill"
    return True


def _approval_body(request) -> str:
    """Approval card body: the tool's reason (if any) plus a compact preview of its args, so a
    mirrored 'Run `write_file`?' shows the path/content rather than just the tool name.
    """
    reason = (getattr(request, "reason", "") or "").strip()
    preview = args_preview(getattr(request, "arguments", None))
    return "\n".join(p for p in (reason, preview) if p)


def _stable_error(error: str) -> str:
    """An error string with per-process noise removed, for change detection only:
    hex object addresses and long digit runs (pids, ports, timestamps) vary between
    identical failures across relaunches."""
    stable = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", error or "")
    return re.sub(r"\d{4,}", "N", stable)


class SessionManager:
    def __init__(
        self,
        *,
        workspace: Optional[str | Path] = None,  # default/seed workspace (e.g. --cwd)
        data_dir: Optional[str | Path] = None,
        model: str = "gpt-5.6-sol",
        mode: Mode = Mode.INTERACTIVE,
        provider: Optional[ProviderClient] = None,
    ) -> None:
        self.default_workspace = (
            str(Path(workspace).expanduser().resolve()) if workspace else None
        )
        self.model = model
        self.mode = mode
        self.provider = provider

        if data_dir is not None:
            base = Path(data_dir).expanduser()
        elif self.default_workspace is not None:
            base = Path(self.default_workspace) / ".coworker"
        else:
            base = state_dir()
        base.mkdir(parents=True, exist_ok=True)

        self.memory_store: MemoryStore = SQLiteMemoryStore(base / "coworker.db")
        # MEMORY-SPEC §4.3/§6: the on/off switch + the user's standing rules. Settings-
        # level, outside the memory table; read at engine build time.
        self.memory_settings = MemorySettingsStore(base / "memory-settings.json")
        self.audit_store = AuditStore(base / "coworker.db")
        self.session_store = ConversationStore(base)
        self.session_store.canonicalize_workspaces()  # collapse /tmp vs /private/tmp etc.
        if self.default_workspace:
            self.session_store.touch_workspace(self.default_workspace)
        self._engines: dict[str, TurnEngine] = {}
        # Sessions whose workspace was promoted mid-turn (workspace-scratch-design.md §5):
        # evicted from the engine cache at the next mark_idle so the following turn
        # rebuilds fully anchored on the new workspace.
        self._promotion_rebuild: set[str] = set()
        self._running_sessions: set[str] = (
            set()
        )  # sessions with an in-flight turn (busy)
        # Sessions with an auto-title LLM call in flight (FB-010) — one call at a time.
        self._autotitle_inflight: set[str] = set()
        self._autotitle_tasks: set[asyncio.Task] = set()
        self._autotitle_attempts: dict[str, int] = {}
        # Opener-count signature of the last attempt: titling fires at TURN START (owner
        # catch 2026-08-24 — waiting for an agentic turn to COMPLETE left sessions
        # untitled for however long the scan ran), and the completion hook still covers
        # background turns; this guard keeps the two trigger points from burning
        # duplicate attempts on the same openers.
        self._autotitle_sig: dict[str, int] = {}
        self.workspace_trust = WorkspaceTrustStore()
        self.secrets = SecretStore()
        # No explicit provider injected → route by the model's `provider:` prefix (OpenAI default,
        # Ollama, …). Tests inject a provider directly and bypass the router. The same router is
        # shared by every engine and the `/v1/chat/completions` proxy.
        if self.provider is None:
            self.provider = ProviderRouter(
                self.secrets, default_provider="openai", on_use=self._note_provider_use
            )
        self.mcp = MCPManager(secrets=self.secrets)
        # OAuth MCP servers with a sign-in in flight / their last connect error —
        # feeds list_mcp's status so the GUI can show "authorizing…" and failures.
        self._mcp_authorizing: set[str] = set()
        self._mcp_errors: dict[str, str] = {}
        # ChatGPT-subscription provider sign-in in flight / its last error — feeds
        # the providers list + status route so the GUI can show "authorizing…".
        self._codex_authorizing = False
        self._codex_error: Optional[str] = None
        # http servers whose anonymous connect came back 401/403 — the failure is
        # "needs sign-in", so the GUI offers the OAuth switch instead of a raw error.
        self._mcp_auth_hints: set[str] = set()
        # Servers that failed to connect while preparing a session's tools —
        # drained once by the WS handler to append a transcript notice.
        self._mcp_session_failures: dict[str, list[str]] = {}
        self.gateway: Optional[Gateway] = None
        self._data_base = base
        # Desktop/UI prefs (default model, onboarding state) — not secrets; a plain JSON file.
        self._prefs = self._load_prefs()
        if self._prefs.get("default_model"):
            self.model = self._prefs["default_model"]
        # Seed the PDF-fallback module global from prefs so engines see the user's
        # choice from the first turn (set_pdf_settings keeps it in sync after).
        from ..pdf_support import set_fallback_mode

        set_fallback_mode(self.pdf_settings()["pdf_fallback"])
        # Per-session live-view registry: every socket open on a session id gets the turn's events,
        # whoever drives the turn (foreground user_message, channel delivery, self-wake, resume).
        # Delivery itself is socket-independent — this only governs *live visibility*.
        self._session_clients: dict[str, set[Any]] = {}
        # App-wide event sockets (/ws/events): session-independent pushes — today the
        # automation-run-started toast (UX-026); badges could ride it later.
        self._event_clients: set[Any] = set()
        # Automation: scheduled tasks store + the tick scheduler (started in the lifespan).
        # The scheduler also resumes self-wake'd sessions each tick (extra_tick).
        self.task_store = TaskStore(base / "automation.db")
        self.scheduler = Scheduler(
            self.task_store, self._run_scheduled_task, extra_tick=self._scheduler_tick
        )
        # Agent teams: two append-only stores, one record discipline. The journal is
        # case-keyed (knowledge outlives boards/teams); the board log is space-scoped,
        # and assignment feeds journal-case grants. Verbs register per-session behind
        # the persona's `team:` trait; the registry holds rosters (lead/worker
        # sessions per board) that the wake plumbing walks.
        self.journal_store = JournalStore(base / "journal.db")
        self.team_store = TeamStore(base / "teams.db", journal=self.journal_store)
        self.chat_store = ChatStore(base / "chat.db")
        self.teams = TeamRegistry(base / "teams.json")
        # External board clients (OPE-100): join tokens bind actor+role; the
        # `/v1/board` API resolves them and the store enforces authority.
        self.board_tokens = BoardTokens(base / "board-tokens.json")
        # Work-item attachments (OPE-105): content-addressed blobs next to the
        # board; the log carries only `attachment://` refs.
        self.attachment_store = AttachmentStore(base / "attachments")
        self._team_inflight: set[str] = set()
        # Lead-session last-turn timestamps for the check-in backstop (monotonic-ish
        # wall clock; restart resets the clock rather than firing a wake storm).
        self._team_last_alive: dict[str, float] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Personas: registry + lifecycle state under this manager's data dir. Installed as the
        # process singleton so agents.get_agent resolves persona ids (incl. third-party) here.
        self.personas = PersonaRegistry(state_path=base / "personas.json")
        set_persona_registry(self.personas)
        # Inbox (cross-session human-attention queue), routing (named inboxes + Slack/Telegram
        # bindings), the Unattended toggle, and self-wake records.
        self.inbox = InboxStore(base / "inbox.json")
        self.inbox_routing = InboxRouting(base / "inbox_routing.json")
        self.unattended = UnattendedRegistry(base / "unattended.json")
        self.wakes = WakeStore(base / "wakes.json")
        # Channel subscriptions (inbound): persisted (session_id, channel) records + a ring buffer
        # of recently-seen channel messages for get_channel_messages.
        self.subscriptions = SubscriptionStore(base / "subscriptions.json")
        self.channel_buffer = ChannelBuffer(state_path=base / "channels.json")
        # Mention router (§31): thread target → the session that owns that Slack thread.
        # Also the durable source of the thread's standing send_message grant (re-seeded
        # onto the engine in get_engine).
        self.mention_sessions = MentionSessionStore(base / "mention_threads.json")
        # Unauthorized inbound messages, parked instead of dropped (one-step allow-and-deliver).
        self.parked = ParkedStore(base / "parked.json")
        # People directory: "platform:user_id" → display name, noted from every inbound
        # (authorized or parked) so allow-list chips read "Rohit Prsad", not "U07JK…".
        self._people_path = base / "people.json"
        try:
            self._people: dict[str, str] = json.loads(self._people_path.read_text())
        except (OSError, ValueError):
            self._people = {}
        # Seed from already-parked messages (they carry resolved names) so an allow made from
        # an old parked item still gets a named chip.
        for it in self.parked.list():
            if it.get("user_name"):
                self._people.setdefault(
                    f"{it['platform']}:{it['user_id']}", it["user_name"]
                )
        # Connection hierarchy (UI-REFRESH §4): per-persona default connector on/off (seeded from the
        # manifest, then user-editable) + per-session overrides. Resolved into the session's effective
        # connector set, which gates inbound delivery and the engine's connector tools.
        self.persona_connections = PersonaConnectionStore(
            base / "persona_connections.json"
        )
        self.session_connections = SessionConnectionStore(
            base / "session_connections.json"
        )
        # Skills (SKILLS-SPEC §4): folder-backed CRUD + per-session mutes. The effective menu
        # gates the engine's skill catalog the same way effective_connectors gates connector
        # tools — one resolver feeds the catalog injection, the rail, and the composer popup.
        self.skill_store = SkillStore()
        self.session_skills = SessionSkillStore(base / "session_skills.json")
        # Dead-letter: inbound messages with no destination + background-turn failures, so neither
        # vanishes silently (a debugging/visibility surface, not a redelivery queue).
        self.unrouted = UnroutedStore(base / "unrouted.json")

    # -- workspaces -------------------------------------------------------------
    def open_workspace(self, path: str, *, create: bool = False) -> dict[str, Any]:
        resolved = Path(path).expanduser()
        if resolved.exists() and not resolved.is_dir():
            return {"path": str(resolved), "ok": False, "error": "not a directory"}
        if not resolved.exists():
            if not create:
                return {
                    "path": str(resolved),
                    "ok": False,
                    "error": "folder does not exist",
                }
            try:
                resolved.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return {"path": str(resolved), "ok": False, "error": str(exc)}
        resolved = resolved.resolve()
        self.session_store.touch_workspace(str(resolved))
        return {
            "path": str(resolved),
            "ok": True,
            "git_branch": _git_branch(resolved),
            "command_trust": self.workspace_command_trust(resolved),
        }

    def workspace_command_trust(self, path: str | Path) -> dict[str, Any]:
        if not str(path).strip():
            return {
                "workspace": "",
                "requested_commands": [],
                "trusted": False,
                "required": False,
            }
        canonical = WorkspaceTrustStore.canonical(path)
        commands = (
            workspace_allowed_commands(canonical)
            if Path(canonical).is_dir()
            else []
        )
        trusted = self.workspace_trust.is_trusted(canonical)
        return {
            "workspace": canonical,
            "requested_commands": commands,
            "trusted": trusted,
            "required": bool(commands and not trusted),
        }

    def _mcp_workspace_trusted(self, workspace: Optional[str | Path]) -> bool:
        """Whether workspace `.coworker/mcp.json` may be loaded (#213).

        Same consent boundary as repository ``allowed_commands``: an untrusted
        clone must not define stdio processes that spawn at session open.
        """
        return bool(workspace and self.workspace_trust.is_trusted(workspace))

    def set_workspace_trust(
        self, path: str | Path, *, trusted: bool
    ) -> dict[str, Any]:
        if not str(path).strip():
            return {"ok": False, "error": "workspace path is required"}
        candidate = Path(path).expanduser()
        if trusted and not candidate.is_dir():
            return {"ok": False, "error": "workspace is not a directory"}
        canonical = self.workspace_trust.set_trusted(candidate, trusted)
        effective = load_config(
            canonical, workspace_trusted=trusted
        ).allowed_commands
        # Apply trust/revocation immediately to live sessions rooted at this exact path.
        for engine in self._engines.values():
            engine_workspace = str(
                (getattr(engine, "audit_context", {}) or {}).get("workspace", "")
            )
            if engine_workspace and WorkspaceTrustStore.canonical(
                engine_workspace
            ) == canonical:
                engine.permissions.allowed_commands = list(effective)
        return {
            "ok": True,
            **self.workspace_command_trust(canonical),
        }

    def trusted_workspaces(self) -> list[dict[str, Any]]:
        return [
            {
                **self.workspace_command_trust(path),
                "exists": Path(path).is_dir(),
            }
            for path in self.workspace_trust.list()
        ]

    def recent_workspaces(self) -> list[dict[str, Any]]:
        """Recent real projects for the folder gate. Per-conversation scratch dirs are
        excluded — they're workspaces to the session store, but never something a user
        should re-open as a 'project'."""
        scratch = self.scratch_base().resolve()
        out = []
        for path in self.session_store.recent_workspaces():
            p = Path(path)
            try:
                if p.resolve().is_relative_to(scratch):
                    continue
            except OSError:
                pass
            out.append({"path": path, "name": p.name, "exists": p.is_dir()})
        return out

    DEFAULT_SCRATCH_BASE = "~/OpenWorker"

    def scratch_base(self) -> Path:
        """Common area for per-conversation scratch directories. Configurable via prefs;
        the env override keeps tests (and any sandboxed run) out of the real home dir —
        universal scratch means every session provisions here, not just orphan ones."""
        base = (
            self._prefs.get("scratch_base")
            or os.environ.get("COWORKER_SCRATCH_BASE")
            or self.DEFAULT_SCRATCH_BASE
        )
        return Path(base).expanduser()

    def _provision_scratch(self, session_id: str) -> str:
        """Create (idempotently) and return this conversation's scratch directory."""
        d = self.scratch_base() / session_id
        d.mkdir(parents=True, exist_ok=True)
        return str(d.resolve())

    _SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

    def is_temp_workspace(self, path: Optional[str]) -> bool:
        """True when `path` is a per-conversation temporary directory (lives under the
        scratch base). The GUI uses this to label the folder "Temporary folder" instead
        of exposing its raw path."""
        if not path:
            return False
        try:
            return (
                Path(path).expanduser().resolve().is_relative_to(self.scratch_base().resolve())
            )
        except OSError:
            return False

    def provision_temp_workspace(self, session_id: str, *, git: bool = True) -> dict[str, Any]:
        """UX-029 "Start in a temporary folder": create the conversation's temporary
        directory at SEND time (not connect) and, for code-family work, make git ready.
        Idempotent — re-sending against an existing dir is a no-op."""
        if not self._SESSION_ID_RE.match(session_id or "") or session_id in {".", ".."}:
            return {"ok": False, "error": "invalid session id"}
        path = self._provision_scratch(session_id)
        if git and not (Path(path) / ".git").is_dir():
            try:
                subprocess.run(
                    ["git", "init", "-q"],
                    cwd=path,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass  # no git on PATH → still a usable folder, just not a repo
        return {"ok": True, "path": path, "git": (Path(path) / ".git").is_dir()}

    def save_temp_as_project(self, session_id: str, dest: str) -> dict[str, Any]:
        """UX-029 "Save as project…": move a session's temporary folder to a real
        location and rebind the session there. The cached engine is dropped so the next
        connect rebuilds against the new path — callers must reconnect after this."""
        if not dest or not dest.strip():
            return {"ok": False, "error": "no destination folder"}
        record = self.session_store.load(session_id)
        src = record.workspace if record and record.workspace else None
        if not src:
            engine = self._engines.get(session_id)
            executor = getattr(engine, "executor", None) if engine else None
            src = str(executor.cwd) if executor else None
        if not src or not self.is_temp_workspace(src) or not Path(src).is_dir():
            return {"ok": False, "error": "this session is not in a temporary folder"}
        if self.is_running(session_id):
            return {"ok": False, "error": "wait for the current task to finish first"}
        d = Path(dest).expanduser()
        if d.exists():
            if not d.is_dir() or any(d.iterdir()):
                return {"ok": False, "error": "destination must be a new or empty folder"}
            d.rmdir()  # shutil.move into an existing dir would nest src inside it
        try:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, str(d))
        except OSError as e:
            return {"ok": False, "error": f"could not move the folder: {e}"}
        new_path = str(d.resolve())
        if record:
            record.workspace = new_path
            self.session_store.save(record)
        self._engines.pop(session_id, None)
        self.session_store.touch_workspace(new_path)
        return {"ok": True, "path": new_path}

    def resolve_workspace(self, requested: Optional[str]) -> Optional[str]:
        if requested:
            p = Path(requested).expanduser()
            if p.is_dir():
                return str(p.resolve())
            return None
        return self.default_workspace

    # -- engines ----------------------------------------------------------------
    def engine_workspace(
        self, session_id: str, *, workspace: Optional[str] = None, agent: str = "code"
    ) -> Optional[str]:
        """The workspace `get_engine` would bind — for prepping MCP tools beforehand."""
        record = self.session_store.load(session_id)
        if record:
            return record.workspace or None
        return self.resolve_workspace(workspace)

    def get_engine(
        self,
        session_id: str,
        *,
        workspace: Optional[str] = None,
        agent: str = "code",
        approver: Optional[Approver] = None,
        extra_tools: Optional[list[Any]] = None,
        directory_requester: Optional[Any] = None,
        plan_approver: Optional[Any] = None,
        question_asker: Optional[Any] = None,
        tool_requester: Optional[Any] = None,
        team_approver: Optional[Any] = None,
        items_approver: Optional[Any] = None,
    ) -> Optional[TurnEngine]:
        engine = self._engines.get(session_id)
        if engine is not None:
            if approver is not None:
                engine.approver = approver
            if directory_requester is not None:
                engine.directory_requester = directory_requester
            if plan_approver is not None:
                engine.plan_approver = plan_approver
            if question_asker is not None:
                engine.question_asker = question_asker
            if tool_requester is not None:
                engine.tool_requester = tool_requester
            if team_approver is not None:
                engine.team_approver = team_approver
            if items_approver is not None:
                engine.items_approver = items_approver
            return engine

        record = self.session_store.load(session_id)
        is_new_session = record is None
        agent_name = (record.agent if record else agent) or "code"
        ag = get_agent(agent_name)

        if record:
            ws = record.workspace or None
            model, mode, messages = record.model, Mode(record.mode), record.messages
        else:
            ws = self.resolve_workspace(workspace)
            model, mode, messages = self.model, self.mode, None

        if not ws or not Path(ws).is_dir():
            # Sessions without a folder start "orphan": auto-provision a per-conversation
            # scratch directory (generalizes MyHelper's auto-workspace). Folder-gated
            # personas (requires_folder) still demand a real directory picked by the user.
            if not ag.requires_folder:
                ws = self._provision_scratch(session_id)
            else:
                return None

        if ws:
            self.session_store.touch_workspace(ws)
        # Universal scratch (workspace-scratch-design.md §4): EVERY session is multi-root
        # with a per-conversation scratch dir. Orphan sessions run ON their scratch
        # (ws == scratch, primary). Sessions on a real folder — gated personas, or a
        # temp-workspace pick that later became a project — keep that folder primary and
        # gain scratch as a second writable root, so deliverables/temp files have a home
        # that never dirties the user's repo. request_directory rides on roots, so it now
        # registers everywhere.
        roots = None
        if ws:
            extra = [
                r
                for r in ((record.extra_roots if record else []) or [])
                if Path(str(r.get("path", ""))).is_dir()
            ]
            if self.is_temp_workspace(ws):
                roots = [{"path": ws, "writable": True, "label": "scratch"}, *extra]
            elif self._SESSION_ID_RE.match(session_id or "") and session_id not in {".", ".."}:
                roots = [
                    {"path": ws, "writable": True, "label": "workspace"},
                    {
                        "path": self._provision_scratch(session_id),
                        "writable": True,
                        "label": "scratch",
                    },
                    *extra,
                ]
            else:
                # A session id we won't put in a filesystem path: primary root only.
                roots = [{"path": ws, "writable": True, "label": "workspace"}, *extra]
        engine = build_engine(
            agent=ag,
            workspace=ws,
            model=model,
            mode=mode,
            provider=self.provider,
            # Memory off (§4.3) = stop LEARNING, not amnesia: saved facts still inject
            # and stay usable, only the write tools go. Read at build time; running
            # sessions finish under the mode they started with.
            memory_store=self.memory_store,
            memory_workspace=self._memory_key_for(record, ws),
            memory_off=not self.memory_settings.enabled,
            # LIVE, not a snapshot: turning saving off mid-conversation must take
            # effect at once (owner-hit 2026-07-28 — a running session kept saving).
            memory_saving_enabled=lambda: self.memory_settings.enabled,
            # Callable, not a snapshot: editing your instructions in Settings applies
            # to conversations already open (same reason as the saving switch).
            user_rules=lambda: self.memory_settings.user_rules,
            on_memory_saved=self._memory_saved_notifier(session_id),
            messages=messages,
            extra_tools=[
                *(extra_tools or []),
                *self._team_tools_for(session_id, ag, record, ws),
            ]
            or None,
            secrets=self.secrets,
            task_store=self.task_store,
            wake_store=self.wakes,
            session_id=session_id,
            audit_sink=self.audit_store.append,
            roots=roots,
            # WS sessions pass mode-aware callbacks (attended → live prompt, unattended → Inbox).
            # Background / self-wake / durable-resume runs have no live socket → default to the
            # Inbox-based callbacks so a rebuilt engine can still get approvals/answers (and, on
            # resume, the already-resolved item returns immediately).
            approver=approver or self.inbox_approver(session_id, agent),
            directory_requester=directory_requester
            or self.inbox_directory_requester(session_id, agent),
            plan_approver=plan_approver or self.inbox_plan_approver(session_id, agent),
            question_asker=question_asker
            or self.inbox_question_asker(session_id, agent),
            tool_requester=tool_requester,
            team_approver=team_approver,
            items_approver=items_approver,
            subscription_store=self.subscriptions,
            channel_buffer=self.channel_buffer,
            routing_targets=self._routing_targets(session_id, agent),
            # Per-session connection hierarchy: expose only effective-enabled connectors' tools.
            connector_filter=self.effective_connectors(session_id, agent_name),
            # Per-session skill menu, LIVE (SKILLS-SPEC §3): a callable so load_skill sees
            # disables/new skills immediately; the catalog snapshot is taken at build.
            skill_filter=lambda sid=session_id, w=ws, a=agent_name: (
                self.effective_skill_names(sid, w, agent=a)
            ),
            # Persona-carried skills (OPE-58): the bundle's skills/ dir joins the loader
            # so its skills are readable, not just listed.
            extra_skill_dirs=(
                [d] if (d := self.persona_skill_scope(agent_name)[0]) is not None else None
            ),
            # Auto-Approve (spec §1.5): prefs-backed, so the Settings toggle takes effect on
            # the next session build without a config.toml edit.
            auto_approve=self.auto_approve(),
            auto_approve_shadow=self.auto_approve_shadow(),
        )
        # An automation run rebuilt here (manual "Run now" over WS, durable resume) still
        # carries its task's standing allowances — the rules live on the task record.
        owning_task = self.task_store.task_for_run_session(session_id)
        if owning_task is not None:
            self._seed_task_permissions(engine, owning_task)
        # A mention-spawned session (§31) keeps its in-thread reply pre-approved across
        # rebuilds/restarts — the grant is re-derived from the durable thread map.
        for thread_target in self.mention_sessions.targets_for(session_id):
            engine.permissions.task_rules.setdefault("send_message", set()).add(
                thread_target
            )
        if record is not None and record.grants:
            self._apply_grants(engine, record.grants)
        # Auto-compaction (OPE-27): restore the persisted view boundary and wire the live
        # Settings getter — post-construction, so build_engine's signature stays put.
        if record is not None and record.compaction:
            from ..compaction import CompactionState

            engine.compaction_state = CompactionState.from_dict(record.compaction)
        engine.compaction_settings = self.compaction_settings
        self._engines[session_id] = engine
        if is_new_session:
            self._emit_session_created(session_id, agent_name)
        return engine

    def _emit_session_created(self, session_id: str, persona_id: str) -> None:
        """Phase 5 telemetry, fired once per brand-new session on a background thread
        (never blocks session start). cloud.emit_session_created is a hard no-op when
        signed out or opted out, and sends only content-free facts."""
        import threading

        from .. import cloud
        from ..config import load_config

        entry = self.personas.get(persona_id)
        # Wire fields kept stable; both now carry the workspace shape ("folder" = gated
        # primary folder, "scratch" = starts on the per-session scratch dir).
        kind = ("folder" if entry.requires_folder else "scratch") if entry else ""
        family = kind
        workspace_kind = kind

        def _send() -> None:
            try:
                cloud.emit_session_created(
                    self.secrets,
                    load_config(),
                    session_id=session_id,
                    persona_id=persona_id,
                    persona_family=family,
                    workspace_kind=workspace_kind,
                )
            except Exception:
                pass  # telemetry must never surface as a session error

        threading.Thread(target=_send, daemon=True).start()

    def _routing_targets(self, session_id: str, agent: str) -> list[str]:
        """The channel address(es) this session's Inbox routes OUT to — used to warn when a
        subscription (inbound) collides with Inbox routing (outbound) on the same channel.
        """
        binding = self.inbox_routing.binding_for(
            self.inbox_routing.route_for(session_id, agent)
        )
        return [f"{binding.channel}:{binding.target}"] if binding.channel else []

    # -- connection hierarchy (UI-REFRESH §4) -----------------------------------
    def _persona_of(self, session_id: str, persona_id: Optional[str] = None) -> str:
        if persona_id:
            return persona_id
        # The live engine is the freshest truth — a brand-new session has no record row
        # until its first send, but its socket already knows the persona.
        engine = self._engines.get(session_id)
        live = getattr(engine, "agent_name", None) if engine is not None else None
        if live:
            return live
        record = self.session_store.load(session_id)
        return (record.agent if record else None) or self.personas.default_id()

    def _persona_connector_grant(self, persona_id: str) -> Optional[set[str]]:
        """The persona's declared connector allowlist (OPE-93). None = unrestricted (the
        `all` sentinel of general builtins); a set = only these ids can ever be effective
        for its sessions — the empty set means no connector access at all."""
        entry = self.personas.get(persona_id)
        if entry is None or entry.manifest is None:
            # Builder-based builtins (Chat/Code/Cowork/Ops) predate the allowlist: their
            # `connectors` trait gates TOOLS only, while their sessions legitimately use
            # the drawer/inbound path (channel bindings). No manifest → no restriction.
            return None
        declared = entry.manifest.connectors
        if declared is True:
            return None
        return set(declared or ())

    def effective_connectors(
        self, session_id: str, persona_id: Optional[str] = None
    ) -> set[str]:
        """The connectors effectively enabled for this session (§4.1): connected AND not muted by
        the session override / persona default AND within the persona's declared grant (OPE-93).
        Drives the engine's connector-tool gating and the inbound delivery gate; seeds the
        persona defaults from the manifest on first read using the full connected set.
        """
        persona = self._persona_of(session_id, persona_id)
        connected = {c["name"] for c in connector_list(self.secrets) if c["connected"]}
        entry = self.personas.get(persona)
        manifest = entry.manifest if entry else None
        persona_defaults = self.persona_connections.defaults_for(
            persona, manifest, connected=connected
        )
        session_overrides = self.session_connections.get(session_id)
        effective = set(
            effective_connections(
                connected=connected,
                persona_defaults=persona_defaults,
                session_overrides=session_overrides,
            )
        )
        grant = self._persona_connector_grant(persona)
        return effective if grant is None else effective & grant

    def _inbound_connector_allowed(self, session_id: str, connector: str) -> bool:
        """Whether an inbound message on `connector` should be DELIVERED to `session_id` (§4.3).

        Uses the SAME effective set as the engine's connector-tool gating so the inbound gate and the
        tool gate can never disagree (a muted connector is muted both ways, from the first message).
        """
        return connector in self.effective_connectors(session_id)

    # -- persona + session connection surfaces (UI-REFRESH §5/§6) ----------------
    def _connected_connectors(self) -> set[str]:
        """The account-connected connector names (the first layer of the §4 hierarchy)."""
        return {c["name"] for c in connector_list(self.secrets) if c["connected"]}

    def _persona_default_connections(
        self, persona_id: str, manifest, connected: set[str]
    ) -> list[dict[str, Any]]:
        """The persona's default connector map (seeded from the manifest's connector recommends on
        first read, then user-editable) as a list, each annotated with account-connectedness.
        """
        defaults = self.persona_connections.defaults_for(
            persona_id, manifest, connected=connected
        )
        return [
            {"connector": c, "enabled": bool(enabled), "connected": c in connected}
            for c, enabled in defaults.items()
        ]

    def persona_detail(self, persona_id: str) -> Optional[dict[str, Any]]:
        """Identity + capabilities + recommends(+connected) + default connections for one persona
        (UI-REFRESH §5). Returns None for an unknown id (the route maps that to an error).
        """
        entry = self.personas.get(persona_id)
        if entry is None:
            return None
        manifest = entry.manifest
        connected = self._connected_connectors()
        recommends = [
            {
                "kind": rec.kind,
                "ref": rec.ref,
                "reason": rec.reason,
                "tier": rec.tier,
                "connected": rec.ref in connected,
            }
            for rec in (manifest.recommends if manifest else [])
        ]
        media_dir = self.personas.media_dir(persona_id)
        media = (
            sorted(
                f.name
                for f in media_dir.iterdir()
                if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
            )
            if media_dir
            else []
        )
        return {
            "id": entry.id,
            "name": entry.name,
            "icon": entry.icon,
            "tagline": entry.tagline,
            "description": manifest.description if manifest else "",
            "media": media,
            "builtin": entry.builtin,
            "group": entry.group,
            "enabled": self.personas.is_enabled(entry.id),
            "surfaced": self.personas.is_surfaced(entry.id),
            "default": entry.id == self.personas.default_id(),
            "tools": list(entry.tools),
            "recommended_models": list(manifest.recommended_models) if manifest else [],
            "default_permission_mode": (
                manifest.default_permission_mode if manifest else "interactive"
            ),
            "requires_folder": entry.requires_folder,
            "recommends": recommends,
            "default_connections": self._persona_default_connections(
                persona_id, manifest, connected
            ),
        }

    def set_persona_connection(
        self, persona_id: str, connector: str, enabled: bool
    ) -> dict[str, Any]:
        """Set a persona-default connector on/off (UI-REFRESH §5). Seeds the manifest defaults
        first so the stored row stays complete (the edit overlays the full seed rather than
        collapsing the row to this one connector), then returns the refreshed default_connections
        so the client can re-render without a second GET."""
        entry = self.personas.get(persona_id)
        if entry is None:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        manifest = entry.manifest
        connected = self._connected_connectors()
        self.persona_connections.defaults_for(persona_id, manifest, connected=connected)
        self.persona_connections.set(persona_id, connector, bool(enabled))
        return {
            "ok": True,
            "default_connections": self._persona_default_connections(
                persona_id, manifest, connected
            ),
        }

    def set_persona_enabled(self, persona_id: str, enabled: bool) -> dict[str, Any]:
        """Flip a persona's enabled flag. Disabling also archives its real (unarchived,
        non-internal) sessions — disable means "put this coworker and its history away", so
        the persona's sidebar section disappears with it (owner call, 2026-07-04). Re-enabling
        never unarchives: that would overwrite the user's archive state; history returns one
        click at a time via the Show-archived disclosure. Raises KeyError for unknown ids.
        """
        self.personas.set_enabled(persona_id, enabled)
        archived = 0
        if not enabled:
            for r in self.session_store.list():
                if (
                    r.agent == persona_id
                    and not r.archived
                    and not r.session_id.startswith("__")
                ):
                    self.session_store.set_flags(r.session_id, archived=True)
                    archived += 1
        return {"ok": True, "archived_sessions": archived}

    def _connection_detail(
        self, session_id: str, connector: str, info: Optional[dict[str, Any]]
    ) -> str:
        """A short human description of WHY a connector is live for a session: the chat ids it's
        subscribed to on that platform, plus "DMs" if this is the designated DM session. Channel
        *names* would need the live adapter's resolve cache (not cheap here), so we show the chat
        ids; with no subscription/DM tie we fall back to the connector's title."""
        prefix = f"{connector}:"
        parts = [
            s.channel.split(":", 1)[1]
            for s in self.subscriptions.for_session(session_id)
            if s.channel.startswith(prefix)
        ]
        if self.dm_session() == session_id:
            parts.append("DMs")
        if parts:
            return " · ".join(parts)
        return (info or {}).get("title") or connector

    def session_connections_view(
        self, session_id: str, persona_id: Optional[str] = None
    ) -> dict[str, Any]:
        """The per-session connections drawer payload (UI-REFRESH §6): every account-connected
        connector with its effective on/off state (muted ones stay VISIBLE as off — a §4.2 toggle
        must never make a row vanish), the persona's connector recommends that aren't yet
        account-connected, and the attention count (= those unconnected recommends).

        ``persona_id`` is the caller's hint (the GUI knows the active persona). It matters for a
        brand-new session: no SessionRecord exists until the first turn persists, so without the
        hint the view would resolve to the DEFAULT persona and show its defaults/recommends —
        the owner's 2026-07-03 finding (a fresh Project Manager session rendered cowork's view).
        """
        persona = self._persona_of(session_id, persona_id)
        entry = self.personas.get(persona)
        manifest = entry.manifest if entry else None
        connectors = connector_list(self.secrets)
        by_name = {c["name"]: c for c in connectors}
        connected_names = {c["name"] for c in connectors if c["connected"]}
        # OPE-93 (owner-hit 2026-08-15): the drawer must show the persona's world, not the
        # account's. An undeclared connector is not a mutable source of this session — it
        # was rendering as toggled-ON while the engine (correctly) refused its tools.
        grant = self._persona_connector_grant(persona)
        if grant is not None:
            connected_names &= grant
        effective = self.effective_connectors(session_id, persona)
        connected = [
            {
                "connector": name,
                "enabled": name in effective,
                "detail": self._connection_detail(session_id, name, by_name.get(name)),
            }
            for name in sorted(connected_names)
        ]
        recommended = [
            {
                "connector": rec.ref,
                "reason": rec.reason,
                "tier": rec.tier,
                "connected": False,
            }
            for rec in (manifest.recommends if manifest else [])
            if rec.kind == "connector" and rec.ref not in connected_names
        ]
        return {
            "connected": connected,
            "recommended": recommended,
            "attention": sum(1 for r in recommended if not r["connected"]),
        }

    def inbox_question_asker(self, session_id: str, agent: str):
        """The Unattended `ask_user` handler: turn the agent's question into an Inbox item and
        suspend until a human answers it (from the Inbox, or inline when they open the session).
        Also the default for background/self-wake runs (no live socket). Mirrors to a bound channel
        like the approver does."""

        async def ask(
            args: dict[str, Any], tool_call_id: Optional[str] = None
        ) -> dict[str, Any]:
            from ..tools.ask import answer_result, question_item_fields

            fields = question_item_fields(args)
            if fields is None:
                return {"answer": "", "error": "no question"}
            inbox_name = self.inbox_routing.route_for(session_id, agent)
            item = self.inbox.add_question(
                session_id,
                inbox=inbox_name,
                tool_call_id=tool_call_id,
                **fields,
            )
            if (
                item.state != "pending"
            ):  # durable resume re-raised an already-answered prompt
                return answer_result(item.questions, item.resolution)
            self.persist_session(session_id)  # the pending tool call is now on disk
            await self.mirror_inbox_item(item)
            answer = await self.inbox.wait(item.id)
            return answer_result(item.questions, answer)

        return ask

    def inbox_approver(self, session_id: str, agent: str):
        """Inbox-based approver — the default for no-socket runs (background, self-wake, durable
        resume). On resume the item already exists + is resolved, so wait returns at once.
        """

        async def approve(request):
            item = self.inbox.add_approval(
                session_id,
                f"Run `{request.tool_name}`?",
                body=_approval_body(request),
                inbox=self.inbox_routing.route_for(session_id, agent),
                tool_call_id=getattr(request, "tool_call_id", None),
                data=self.approval_prompt_data(session_id, request),
            )
            if item.state == "pending":
                self.persist_session(session_id)
                await self.mirror_inbox_item(item)
            resolution = await self.inbox.wait(item.id)
            return self.approval_outcome(resolution, request, session_id)

        return approve

    def inbox_directory_requester(self, session_id: str, agent: str):
        async def request(args, tool_call_id=None):
            item = self.inbox.add_directory(
                session_id,
                "Grant access to a folder?",
                body=str(args.get("reason", "")),
                inbox=self.inbox_routing.route_for(session_id, agent),
                data={
                    "path": str(args.get("path", "")),
                    "writable": bool(args.get("writable", False)),
                    "primary": bool(args.get("primary", False)),
                },
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                self.persist_session(session_id)
                await self.mirror_inbox_item(item)
            resp = _parse_inbox_json(await self.inbox.wait(item.id))
            if not resp.get("granted"):
                return {"granted": False, "reason": "the user declined the request"}
            path = (resp.get("path") or args.get("path") or "").strip()
            if not path:
                return {"granted": False, "error": "no directory was provided"}
            writable = bool(resp.get("writable", args.get("writable", False)))
            if bool(args.get("primary", False)):
                promo = await asyncio.to_thread(self.promote_workspace, session_id, path)
                if promo.get("ok"):
                    return {
                        "granted": True,
                        "path": promo["path"],
                        "writable": True,
                        "primary": True,
                        "note": (
                            "This folder is now the session's workspace. For the rest "
                            "of this turn, address it by absolute path."
                        ),
                    }
                res = self.add_root(session_id, path, writable)
                if not res.get("ok"):
                    return {
                        "granted": False,
                        "error": promo.get("error", "could not promote"),
                    }
                return {
                    "granted": True,
                    "path": path,
                    "writable": writable,
                    "primary": False,
                    "note": promo.get("error", "")
                    + " — granted as an additional folder instead",
                }
            res = self.add_root(session_id, path, writable)
            if not res.get("ok"):
                return {
                    "granted": False,
                    "error": res.get("error", "could not grant access"),
                }
            return {"granted": True, "path": path, "writable": writable}

        return request

    def inbox_plan_approver(self, session_id: str, agent: str):
        async def approve(args, tool_call_id=None):
            item = self.inbox.add_plan(
                session_id,
                "Approve the plan?",
                body=str(args.get("plan", "")),
                inbox=self.inbox_routing.route_for(session_id, agent),
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                self.persist_session(session_id)
                await self.mirror_inbox_item(item)
            resp = _parse_inbox_json(await self.inbox.wait(item.id))
            if not resp.get("approved"):
                return {
                    "approved": False,
                    "feedback": resp.get("feedback") or "the user rejected the plan",
                }
            return {"approved": True, "mode": resp.get("mode") or "interactive"}

        return approve

    def persist_session(self, session_id: str) -> None:
        """Save the cached engine's thread (so a prompt's pending tool call survives a crash)."""
        engine = self._engines.get(session_id)
        if engine is not None:
            self.save(session_id, engine)

    async def resolve_inbox(self, item_id: str, resolution: str) -> bool:
        """Resolve an Inbox item from any surface (REST / Slack button / channel reply). If the
        asking agent is still suspended live, that await handles it. Otherwise the process restarted
        (or the engine was evicted) while blocked → durably resume: rebuild the engine from the
        saved thread and continue the turn."""
        item = self.inbox.get(item_id)
        ok = self.inbox.resolve(item_id, resolution)
        if not ok or item is None:
            return ok
        if not self.is_running(item.session_id):
            await self._durable_resume(item)
        return ok

    async def _durable_resume(self, item) -> None:
        if not getattr(item, "tool_call_id", None):
            return  # nothing to reconstruct (legacy item) — best-effort: leave it
        engine = self.get_engine(item.session_id)
        if engine is None or not hasattr(engine, "resume"):
            return
        self.mark_running(item.session_id)
        try:
            async for _event in engine.resume():
                pass
            self.save(item.session_id, engine)
        finally:
            self.mark_idle(item.session_id)

    # -- MCP --------------------------------------------------------------------
    async def prepare_mcp_tools(
        self, session_id: str, *, workspace: Optional[str] = None, agent: str = "code"
    ) -> list[Any]:
        """Connect enabled MCP servers (global + workspace) and return their tool callables.

        Called from the async WS handler before `get_engine`; no-op if the engine is already
        built (its MCP tools are attached). Servers that fail to connect are skipped.
        """
        if session_id in self._engines:
            return []
        from ..connectors.descriptors import get_descriptor
        from ..connectors.tool_defs import (
            approval_for_tool,
            mcp_tool_defs,
            tool_enabled,
        )

        from ..mcp import oauth as mcp_oauth

        ws = self.engine_workspace(session_id, workspace=workspace, agent=agent)
        loop = asyncio.get_running_loop()
        effective: Optional[set[str]] = None  # computed lazily, once
        out: list[Any] = []
        # Persona `mcp:` wiring (OPE-58 sibling stub): a persona that declares an `mcp:`
        # list SCOPES its sessions to those servers — the consent screen already presents
        # that list as what the persona uses, so honoring it keeps consent truthful. It
        # only ever shrinks: the user's enabled/configured/authed gates all still apply,
        # and a persona with no list changes nothing. Connector-backed servers keep their
        # own per-persona connector gating instead.
        persona_mcp = self.persona_mcp_scope(agent)
        for server in load_mcp_servers(
            ws,
            secrets=self.secrets,
            workspace_trusted=self._mcp_workspace_trusted(ws),
        ):
            if not server.enabled:
                continue
            if server.auth == "oauth" and not mcp_oauth.has_tokens(
                server.name, self.secrets
            ):
                # NEVER start an interactive OAuth flow from a turn: a token-less
                # server here would open a browser and block every session for the
                # full flow timeout (owner-hit 2026-07-20 — a failed one-click's
                # leftover config froze all new sessions). Flows start only from an
                # explicit connect in Settings/Connectors.
                continue
            descriptor = get_descriptor(server.name)
            backed = descriptor is not None and bool(descriptor.mcp_url)
            if backed:
                # Connector-backed server: obey the same gates as connector tools —
                # the session's effective connector set and the per-tool toggles.
                # The descriptor's PIN is authoritative over whatever the config
                # file says (drift can only ever shrink the surface).
                if effective is None:
                    effective = self.effective_connectors(session_id, agent)
                if server.name not in effective:
                    continue
                prefix = f"mcp__{server.name}__"
                server.include_tools = [
                    t.name.removeprefix(prefix)
                    for t in mcp_tool_defs(server.name)
                    if tool_enabled(self.secrets, server.name, t.name)
                ]
            elif persona_mcp is not None and server.name not in persona_mcp:
                # Raw servers outside the persona's declared scope stay off its sessions.
                continue
            try:
                conn = await self.mcp.ensure(server)
                self._mcp_errors.pop(server.name, None)
                # Recovery resets the notice dedupe: if this server breaks again
                # later, the next session gets a fresh transcript notice.
                self._clear_mcp_notified(server.name)
            except Exception as exc:
                if mcp_oauth.is_auth_required(exc):
                    # Stored tokens no longer refresh (vendor rotated/expired
                    # them) — the non-interactive connect refused to open a
                    # browser. Record it so the MCP page shows WHY the server is
                    # dark; the session just runs without its tools.
                    self._mcp_errors[server.name] = (
                        "sign-in required — reconnect this server from its page"
                    )
                    logger.info(
                        "mcp %s needs re-auth; skipped for this session", server.name
                    )
                else:
                    # Bad command / crashed child / unreachable url — the session
                    # still runs without the tools, but the failure must not be
                    # silent (three-for-three silent failures in the 2026-08-20
                    # drill): record it for the MCP page and the session notice.
                    msg = str(exc) or exc.__class__.__name__
                    tail = self.mcp.last_stderr(server.name)
                    if tail:
                        msg = f"{msg} — {tail}"
                    self._mcp_errors[server.name] = msg[:500]
                    logger.warning(
                        "mcp %s failed to connect: %s", server.name, msg[:500]
                    )
                # Transcript notice on state CHANGE, not state (owner ruling
                # 2026-08-21): a continuously-broken server stamps only the first
                # session after it breaks (or breaks differently) — the Connectors
                # page carries the standing error. Personas that DECLARE the server
                # in their manifest keep the every-session notice: for them the
                # missing tools are material every time (the 2026-08-20 drill case).
                declared = persona_mcp is not None and server.name in persona_mcp
                if declared or self._should_notify_mcp_failure(
                    server.name, self._mcp_errors.get(server.name, "")
                ):
                    self._mcp_session_failures.setdefault(session_id, []).append(
                        server.name
                    )
                continue
            callables = build_callables(
                server,
                conn.tools,
                lambda tool, args, name=server.name: self.mcp.call(name, tool, args),
                loop,
            )
            if backed:
                # Per-tool approval from the pinned read/write classification
                # (server-level requires_approval is off for backed servers);
                # anything unclassified stays approval-gated — fail closed.
                for fn in callables:
                    fn.__aisuite_tool_metadata__.requires_approval = approval_for_tool(
                        fn.__aisuite_tool_metadata__.name, default=True
                    )
            out.extend(callables)
        return out

    def _should_notify_mcp_failure(self, name: str, error: str) -> bool:
        """True once per failure episode: the first session after `name` starts
        failing (or its error text changes) notices; unchanged-broken stays quiet.
        Persisted in prefs so an app relaunch doesn't re-stamp the same complaint.
        Compared on a NORMALIZED error: stderr often embeds per-process values
        (0x… object addresses, pids), which made "the same" failure look new on
        every relaunch and re-stamp every session (owner-hit 2026-08-21)."""
        stable = _stable_error(error)
        notified = self._prefs.setdefault("mcp_notified_errors", {})
        if notified.get(name) == stable:
            return False
        notified[name] = stable
        self._save_prefs()
        return True

    def _clear_mcp_notified(self, name: str) -> None:
        if self._prefs.get("mcp_notified_errors", {}).pop(name, None) is not None:
            self._save_prefs()

    def pop_mcp_failures(self, session_id: str) -> list[tuple[str, Optional[str]]]:
        """Drain (name, error) for servers that failed while preparing this session's
        tools — consumed once by the WS handler to append a transcript notice."""
        names = self._mcp_session_failures.pop(session_id, [])
        return [(n, self._mcp_errors.get(n)) for n in names]

    def list_mcp(self) -> list[dict[str, Any]]:
        """Servers from the global config + connection status (does not connect)."""
        from ..mcp import oauth as mcp_oauth

        from ..connectors.descriptors import get_descriptor

        out = []
        for name, raw in read_global().items():
            d = get_descriptor(name)
            if d is not None and d.mcp_url:
                # Connector-backed server: surfaced on the Connectors page (its
                # connect/disconnect lifecycle lives there), not in the MCP tab.
                continue
            connected = name in self.mcp._conns
            is_oauth = str(raw.get("auth", "")).lower() == "oauth"
            if connected:
                status = "connected"
            elif not raw.get("enabled", True):
                status = "disabled"
            elif name in self._mcp_authorizing:
                status = "authorizing"
            elif is_oauth and not mcp_oauth.has_tokens(name, self.secrets):
                status = "needs_auth"
            elif name in self._mcp_errors and not is_oauth:
                # Startup/connection failure (stdio crash, unreachable url) — the
                # drill class. OAuth servers keep their softer statuses: acquiring
                # tokens supersedes a stale sign-in error (the GUI still prints
                # last_error under the row either way).
                status = "error"
            else:
                status = "configured"
            out.append(
                {
                    "name": name,
                    "enabled": bool(raw.get("enabled", True)),
                    "transport": (
                        "http"
                        if (
                            raw.get("url")
                            or str(raw.get("type", "")).lower()
                            in {"http", "sse", "streamable-http"}
                        )
                        else "stdio"
                    ),
                    "requires_approval": bool(raw.get("requires_approval", True)),
                    "auth": "oauth" if is_oauth else None,
                    "status": status,
                    "auth_hint": name in self._mcp_auth_hints,
                    "last_test_at": self._prefs.get("mcp_last_test", {}).get(name),
                    "last_error": self._mcp_errors.get(name),
                    "tool_count": (
                        len(self.mcp._conns[name].tools) if connected else None
                    ),
                    "config": _redact(raw),
                }
            )
        return out

    def begin_mcp_connect(self, name: str) -> None:
        """Flag `authorizing` BEFORE the background connect task starts. The GUI's
        fast poll keys off this status; the first refresh used to outpace the task,
        so a failing Test showed nothing until the lazy 5s tick (owner-hit
        2026-08-21 — the button looked dead). Known names only, so an unknown
        server can't wedge the flag (connect_mcp only clears it on a match)."""
        if name in read_global():
            self._mcp_authorizing.add(name)

    async def connect_mcp(self, name: str) -> dict[str, Any]:
        """Connect one server NOW — for OAuth servers this may open the browser and wait
        for the loopback callback, so callers run it as a background task and watch
        list_mcp for the status flip."""
        from ..mcp import oauth as mcp_oauth

        for server in load_mcp_servers(
            self.default_workspace,
            secrets=self.secrets,
            workspace_trusted=self._mcp_workspace_trusted(self.default_workspace),
        ):
            if server.name != name:
                continue
            self._mcp_authorizing.add(name)
            self._mcp_errors.pop(name, None)
            self._mcp_auth_hints.discard(name)
            try:
                # The ONE place a browser sign-in may start: an explicit connect.
                # verify (not ensure): an already-live server gets a real round-trip
                # and a refreshed tool list instead of a cached yes.
                conn = await self.mcp.verify(server, interactive=True)
                # The Connectors row says "Ready · tested ⟨when⟩" — the claim must
                # survive an app restart, so it lives in prefs, not memory.
                self._prefs.setdefault("mcp_last_test", {})[name] = int(time.time())
                self._save_prefs()
                self._clear_mcp_notified(name)
                return {"ok": True, "tools": len(conn.tools)}
            except Exception as exc:
                if (
                    server.transport == "http"
                    and server.auth != "oauth"
                    and mcp_oauth.is_http_auth_error(exc)
                ):
                    # Anonymous probe of a guarded server (the add-by-URL flow):
                    # the answer is sign-in, not a raw 401 dump.
                    self._mcp_auth_hints.add(name)
                    msg = "authentication required — sign in to connect"
                else:
                    msg = str(exc) or exc.__class__.__name__
                    tail = self.mcp.last_stderr(name)
                    if tail:
                        msg = f"{msg} — {tail}"
                self._mcp_errors[name] = msg[:500]
                return {"ok": False, "error": self._mcp_errors[name]}
            finally:
                self._mcp_authorizing.discard(name)
        self._mcp_authorizing.discard(name)  # begin_mcp_connect flagged a name we never matched
        return {"ok": False, "error": f"unknown MCP server: {name}"}

    async def mcp_connect_connector(self, name: str) -> dict[str, Any]:
        """One-click connect for an MCP-BACKED connector (descriptor.mcp_url): seed
        the global server entry pinned to the curated allowlist, run the browser
        OAuth flow, and mark the connector profile `mode: "mcp"` on success."""
        from ..connectors.descriptors import get_descriptor
        from ..connectors.tool_defs import mcp_pinned_tools

        d = get_descriptor(name)
        if d is None or not d.mcp_url:
            return {"ok": False, "error": f"{name} has no MCP connect path"}
        put_global_server(
            name,
            {
                "url": d.mcp_url,
                "auth": "oauth",
                # Server-level approval off: writes gate per-tool via the pinned
                # read/write classification (prepare_mcp_tools); unknown vendor
                # tools never load at all (include_tools).
                "requires_approval": False,
                "include_tools": mcp_pinned_tools(name),
                "enabled": True,
            },
        )
        result = await self.connect_mcp(name)
        if result.get("ok"):
            profile = self.secrets.get(f"{name}:default") or {}
            self.secrets.put(
                f"{name}:default", {**profile, "mode": "mcp", "enabled": True}
            )
        else:
            # A failed connect must take its seeded config with it: an enabled
            # oauth entry with no tokens lingers forever (nothing owns it once
            # the descriptor's mcp_url is gone) and re-arms at every session
            # start — the owner-hit asana leftover, 2026-07-20.
            delete_global_server(name)
        return result

    async def signout_mcp(self, name: str) -> dict[str, Any]:
        """Drop the live connection (if any) and forget the stored OAuth tokens."""
        from ..mcp import oauth as mcp_oauth

        conn = self.mcp._conns.get(name)
        if conn is not None:
            conn.shutdown.set()
        self._mcp_errors.pop(name, None)
        removed = mcp_oauth.sign_out(name, self.secrets)
        return {"ok": True, "had_tokens": removed}

    def add_mcp(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        put_global_server(name, config)
        return {"ok": True, "name": name}

    def patch_mcp(self, name: str, changes: dict[str, Any]) -> dict[str, Any]:
        ok = patch_global_server(name, changes)
        return {"ok": ok, "name": name}

    def delete_mcp(self, name: str) -> dict[str, Any]:
        ok = delete_global_server(name)
        if ok:
            # A later re-add under the same name starts clean, not pre-failed —
            # and not pre-trusted (the old entry's test says nothing about the new).
            self._mcp_errors.pop(name, None)
            self._mcp_auth_hints.discard(name)
            if self._prefs.get("mcp_last_test", {}).pop(name, None) is not None:
                self._save_prefs()
            self._clear_mcp_notified(name)
            # Removing a server must not leave its connection running until the next
            # restart, nor its OAuth tokens + DCR registration in the secret store —
            # "Remove" is the user saying this server is GONE (owner review 2026-08-21).
            # The route runs in a threadpool; the shutdown event belongs to the loop.
            conn = self.mcp._conns.get(name)
            if conn is not None:
                if self._loop is not None:
                    self._loop.call_soon_threadsafe(conn.shutdown.set)
                else:
                    conn.shutdown.set()
            from ..mcp import oauth as mcp_oauth

            mcp_oauth.sign_out(name, self.secrets)
        return {"ok": ok, "name": name}

    async def mcp_tools(self, name: str) -> dict[str, Any]:
        """Connect one server and list its tools (name + description)."""
        for server in load_mcp_servers(
            self.default_workspace,
            secrets=self.secrets,
            workspace_trusted=self._mcp_workspace_trusted(self.default_workspace),
        ):
            if server.name == name:
                try:
                    conn = await self.mcp.ensure(server)
                except Exception as exc:
                    return {"name": name, "ok": False, "error": str(exc), "tools": []}
                return {
                    "name": name,
                    "ok": True,
                    "tools": [
                        {"name": t.name, "description": getattr(t, "description", "")}
                        for t in conn.tools
                    ],
                }
        return {"name": name, "ok": False, "error": "unknown server", "tools": []}

    async def reload_mcp(self) -> dict[str, Any]:
        """Drop live MCP connections so new sessions reconnect with fresh config."""
        await self.mcp.aclose()
        return {"ok": True}

    # -- connectors -------------------------------------------------------------
    def list_connectors(self) -> list[dict[str, Any]]:
        # Enrich two-way connectors with the live gateway's recently-seen senders, so the Connectors
        # tab can manage the allow-list inline (each recent sender flagged authorized or not).
        connectors = connector_list(self.secrets)
        for c in connectors:
            if not (c.get("two_way") and c.get("connected")):
                continue
            allowed = set(c.get("allowed_users") or [])
            # Per-workspace allow-lists (managed relay) — a sender is judged against
            # ITS workspace's list; the flat list only governs team-less (socket) events.
            team_allowed = {
                w["team_id"]: set(w.get("allowed_users") or [])
                for w in (c.get("workspaces") or [])
            }
            recent = self.gateway.recent_senders(c["name"]) if self.gateway else []
            for r in recent:
                team = r.get("team_id")
                pool = team_allowed.get(team, set()) if team else allowed
                r["authorized"] = r.get("user_id") in pool
                # Backfill from the people directory (an event may predate name scopes).
                r["user_name"] = r.get("user_name") or self._people.get(
                    f"{c['name']}:{r.get('user_id')}"
                )
            c["recent"] = recent
            # Parked unauthorized messages (§19) — the connector page resolves them inline.
            c["unauthorized"] = self.parked.list(c["name"])
            # Allow-list display names from the people directory (ids stay the source of truth).
            c["allowed_user_names"] = {
                u: self._people.get(f"{c['name']}:{u}")
                for u in (c.get("allowed_users") or [])
            }
            c["approval_owner_names"] = {
                u: self._people.get(f"{c['name']}:{u}")
                for u in (c.get("approval_owner_ids") or [])
            }
            for w in c.get("workspaces") or []:
                w["allowed_user_names"] = {
                    u: self._people.get(f"{c['name']}:{u}")
                    for u in (w.get("allowed_users") or [])
                }
                w["approval_owner_names"] = {
                    u: self._people.get(f"{c['name']}:{u}")
                    for u in (w.get("approval_owner_ids") or [])
                }
        return connectors

    def connect_connector(
        self, name: str, fields: dict[str, Any], *, acknowledged: bool = False
    ) -> dict[str, Any]:
        # validates the token by a live API call (sync httpx) — run off the event loop
        return connect_connector(self.secrets, name, fields, acknowledged=acknowledged)

    def set_experimental_connectors(self, value: bool) -> dict[str, Any]:
        return set_experimental_enabled(self.secrets, value)

    def disconnect_connector(self, name: str) -> dict[str, Any]:
        # MCP-backed profile: drop the live server connection before the tokens go.
        conn = self.mcp._conns.get(name)
        if conn is not None:
            conn.shutdown.set()
        return disconnect_connector(self.secrets, name)

    def update_connector_tools(
        self, name: str, enabled: dict[str, Any]
    ) -> dict[str, Any]:
        return update_connector_tools(self.secrets, name, enabled)

    def list_audit(
        self,
        *,
        limit: int = 100,
        session_id: Optional[str] = None,
        connector: Optional[str] = None,
        tool: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return self.audit_store.list(
            limit=limit, session_id=session_id, connector=connector, tool=tool
        )

    def browser_state(self) -> dict[str, Any]:
        return browser_state()

    def browser_screenshot(self) -> dict[str, Any]:
        return browser_take_screenshot()

    def browser_close(self) -> dict[str, Any]:
        return browser_close_session()

    # ------------------------------------------------------------- agent teams (OPE-96)

    # ------------------------------------------------- project identity (pass 20)

    def _memory_key_for(self, record, ws: Optional[str]) -> Optional[str]:
        """Binding > git > path, with the one-time path→git re-key on the way."""
        binding = ((record.bindings if record else {}) or {}).get("memory")
        return resolve_memory_key(
            ws,
            binding=binding,
            names=self.session_store.names(),
            memory_store=self.memory_store,
        )

    def _space_for(self, record, ws: Optional[str]) -> Optional[str]:
        """Board-space twin of _memory_key_for — same ladder, board collision rule."""
        binding = ((record.bindings if record else {}) or {}).get("board")
        return resolve_board_space(
            ws,
            binding=binding,
            names=self.session_store.names(),
            team_store=self.team_store,
        )

    def _board_space(self, session_id: str) -> Optional[str]:
        record = self.session_store.load(session_id)
        workspace = (record.workspace if record else None) or self.default_workspace
        return self._space_for(record, workspace) if workspace else None

    def _user_actor(self) -> TeamActor:
        return TeamActor(id="user", role=TeamRole.USER)

    def board_item_detail(self, session_id: str, item_id: int) -> dict[str, Any]:
        """One item in full, with its TIMELINE — creations, assignments,
        transitions, and comments merged chronologically (the detail pane renders
        the item's whole story; the store is an event log, so this is just its
        honest projection). Acts as the user."""
        space = self._board_space(session_id)
        if space is None:
            return {"error": "no board for this session"}
        try:
            item = self.team_store.get_item(space, int(item_id))
        except TeamsBoardError as error:
            return {"error": str(error)}
        timeline: list[dict[str, Any]] = []
        for event in self.team_store.events(space, item_id=int(item_id)):
            payload = event.get("payload") or {}
            row: dict[str, Any] = {
                "seq": event["seq"],
                "ts": event["ts"],
                "actor": event["actor"],
            }
            if event["kind"] == "item_created":
                row["kind"] = "created"
            elif event["kind"] == "item_assigned":
                row["kind"] = "claimed" if payload.get("claimed") else "assigned"
                row["assignee"] = payload.get("assignee") or ""
            elif event["kind"] == "item_transitioned":
                row["kind"] = "moved"
                row["to"] = payload.get("to") or ""
                if payload.get("comment"):
                    row["body"] = payload["comment"]
                if payload.get("refs"):
                    row["refs"] = payload["refs"]
            elif event["kind"] == "item_commented":
                row["kind"] = "comment"
                row["body"] = payload.get("body") or ""
                if payload.get("refs"):
                    row["refs"] = payload["refs"]
            else:
                continue
            timeline.append(row)
        item["timeline"] = timeline
        return item

    def board_comment(self, session_id: str, item_id: int, body: str) -> dict[str, Any]:
        """A pure note from the user on an item — never changes state (owner
        doctrine 2026-08-17); the assignee hears it through its feed."""
        space = self._board_space(session_id)
        if space is None:
            return {"error": "no board for this session"}
        try:
            event = self.team_store.comment(
                space, self._user_actor(), int(item_id), body
            )
        except (TeamsBoardError, ValueError) as error:
            return {"error": str(error)}
        self.kick_team_tick()  # the assignee's feed has news
        return {"ok": True, "seq": event["seq"]}

    def session_board(self, session_id: str) -> dict[str, Any]:
        """The session's board: items grouped by the workspace-keyed space. Empty
        (space=None) when the workspace has no items — the rail hides itself."""
        space = self._board_space(session_id)
        if space is None:
            return {"space": None, "name": "", "items": []}
        items = self.team_store.list_items(space, self._user_actor())
        if not items:
            return {"space": None, "name": "", "items": []}
        # Blocked rows carry the blocker as a plain fact ("blocked: need tfvars") —
        # the latest blocked-transition comment, resolved here so the list stays
        # one round-trip.
        for item in items:
            if item["state"] != "blocked":
                continue
            for event in reversed(
                self.team_store.events(space, item_id=item["id"])
            ):
                payload = event.get("payload") or {}
                if event["kind"] == "item_transitioned" and payload.get("to") == "blocked":
                    if payload.get("comment"):
                        item["blocker"] = self._clamp(payload["comment"], 120)
                    break
        return {"space": space, "name": Path(space).name, "items": items}

    def board_transition(
        self, session_id: str, item: int, to: str, comment: str = ""
    ) -> dict[str, Any]:
        space = self._board_space(session_id)
        if space is None:
            return {"error": "this session has no board"}
        try:
            return self.team_store.transition(
                space, self._user_actor(), int(item), to, comment=comment
            )
        except (TeamsBoardError, ValueError) as error:
            return {"error": str(error)}

    def board_create_items(
        self, session_id: str, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """The decomposition gate's approved action: create the proposed items as
        the LEAD (its identity is the creator; the user's approval is the gate that
        let this run). Validates everything up front so a bad batch creates nothing."""
        record = self.session_store.load(session_id)
        if record is None or not record.workspace:
            return {"approved": False, "error": "the session has no workspace"}
        space = self._space_for(record, record.workspace)
        actor = TeamActor(
            id=f"{record.agent}:{session_id[:8]}",
            role=TeamRole.LEAD,
            persona=record.agent,
            session_id=session_id,
        )
        for entry in items:
            if not str((entry or {}).get("title", "")).strip() or not str(
                (entry or {}).get("criteria", "")
            ).strip():
                return {
                    "approved": False,
                    "error": "every item needs a title and acceptance criteria",
                }
        created = []
        for entry in items:
            item = self.team_store.create_item(
                space,
                actor,
                title=str(entry["title"]),
                criteria=str(entry["criteria"]),
                description=str(entry.get("description", "")),
                case=str(entry.get("case", "")) or None,
            )
            created.append({"id": item["id"], "title": item["title"]})
        return {
            "approved": True,
            "items": created,
            "note": "items created on the board — staff and assign to start work",
        }

    def team_chat(self, team_id: str, *, mark_read: bool = True) -> dict[str, Any]:
        """The chat view's payload. Viewing IS reading for the user: the badge
        cursor advances on fetch."""
        team = self.teams.get(team_id)
        if team is None or not team.chat_enabled or not team.chat_group:
            return {"enabled": False, "messages": [], "members": []}
        group = self.chat_store.get_group(team.chat_group) or {"members": []}
        messages = self.chat_store.messages(team.chat_group)
        if mark_read and messages:
            self.chat_store.consume(team.chat_group, "user", messages[-1]["seq"])
        return {
            "enabled": True,
            "team_id": team_id,
            "members": group["members"],
            "messages": messages,
        }

    def post_team_chat(self, team_id: str, text: str) -> dict[str, Any]:
        team = self.teams.get(team_id)
        if team is None or not team.chat_enabled or not team.chat_group:
            return {"error": "chat is not enabled for this team"}
        try:
            message = self.chat_store.post(
                team.chat_group, "user", text, author_role="user"
            )
        except (TeamsBoardError, ValueError) as error:
            return {"error": str(error)}
        # A user post wakes every member — kick the drain rather than waiting a tick.
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.team_tick(), self._loop)
        return message

    def journal_overview(self) -> list[dict[str, Any]]:
        return self.journal_store.overview(self._user_actor())

    TEAM_WAKE_CAP_PER_HOUR = 60  # budget gate at the wake gate: silent server cap

    def _team_tools_for(
        self, session_id: str, agent: Any, record: Any, ws: Optional[str]
    ) -> list[Any]:
        """Board/journal verbs, gated by the persona `team:` trait. Leads get the
        coordination set (+ steer); workers get the worker set bound to their roster
        actor id. OPENWORKER_TEAM_BOARD=1 keeps the phase-1 any-session-as-lead dev
        mode."""
        role = getattr(agent, "team", None)
        if role is None and ws and os.environ.get("OPENWORKER_TEAM_BOARD") == "1":
            role = "lead"
        if role is None or not ws:
            return []
        space = self._space_for(record, ws)
        if role == "worker":
            info = (record.team if record is not None else {}) or {}
            actor = TeamActor(
                id=str(info.get("actor") or f"{agent.name}:{session_id[:8]}"),
                role=TeamRole.WORKER,
                persona=agent.name,
                session_id=session_id,
            )
            space = str(info.get("space") or space)
        else:
            actor = TeamActor(
                id=f"{agent.name}:{session_id[:8]}",
                role=TeamRole.LEAD,
                persona=agent.name,
                session_id=session_id,
            )
        tools = board_tools(
            self.team_store,
            space=space,
            actor=actor,
            attachments=self.attachment_store,
        ) + journal_tools(
            self.journal_store, actor=actor, space=space
        )
        if role == "lead":
            tools.append(self._steer_tool(session_id))
            tools.append(self._team_options_tool())
        # post_chat registers for every team persona; it resolves the group at call
        # time (the team may not exist yet at engine build) and fails gracefully
        # when chat is off.
        tools.append(self._post_chat_tool(session_id, role))
        return tools

    def _post_chat_tool(self, session_id: str, role: str) -> Any:
        import aisuite as ai

        manager = self

        def post_chat(text: str, record_on_item: Optional[int] = None) -> dict:
            """Post to # team chat. Mention teammates with @name to reach them —
            only mentioned members are woken (the user always sees it). Chat is for
            questions and consensus; status lives on the board. If your message
            answers something that matters, pass record_on_item to also record it
            as a comment on that work item."""
            team, handle, actor = manager._chat_identity(session_id, role)
            if team is None:
                return {"error": "this session is not part of a team"}
            if not team.chat_enabled or not team.chat_group:
                return {"error": "team chat is not enabled for this team"}
            try:
                message = manager.chat_store.post(
                    team.chat_group, handle, text, author_role=role
                )
            except (TeamsBoardError, ValueError) as error:
                return {"error": str(error)}
            result: dict[str, Any] = {
                "ok": True,
                "mentioned": message["mentions"],
            }
            if record_on_item is not None and actor is not None:
                try:
                    manager.team_store.comment(
                        team.space, actor, int(record_on_item), text
                    )
                    result["recorded_on"] = int(record_on_item)
                except (TeamsBoardError, ValueError) as error:
                    result["record_error"] = str(error)
            return result

        return ai.tool(
            post_chat,
            metadata=ai.ToolMetadata(
                category="team", risk_level="low", capabilities=["team"]
            ),
        )

    def _chat_identity(self, session_id: str, role: str):
        """(team, chat handle, board actor) for a team session — the lead's chat
        handle is "lead"; a worker's handle IS its board actor (the callname)."""
        if role == "lead":
            team = self.teams.for_lead_session(session_id)
            if team is None:
                return None, "", None
            record = self.session_store.load(session_id)
            actor = TeamActor(
                id=team.lead_actor,
                role=TeamRole.LEAD,
                persona=record.agent if record else "",
                session_id=session_id,
            )
            return team, "lead", actor
        found = self.teams.for_worker_session(session_id)
        if found is None:
            return None, "", None
        team, worker = found
        actor = TeamActor(
            id=worker.actor,
            role=TeamRole.WORKER,
            persona=worker.persona,
            session_id=session_id,
        )
        return team, worker.actor, actor

    def _team_options_tool(self) -> Any:
        """Registry-injected staffing knowledge: the lead's options come from the
        persona registry at call time — installing a worker coworker automatically
        widens what a lead can propose; nothing is hardcoded. Solo personas never
        appear (fail closed at the source AND at create_team)."""
        import aisuite as ai

        manager = self

        def team_options() -> dict:
            """List the worker coworkers available for staffing (call before
            propose_team). Only team-capable workers are listed — solo coworkers
            cannot join a team."""
            out = []
            # Registry entries directly — NOT list_all(), which applies the
            # ships:false visibility filter: a lead that is running (internal
            # build or user-enabled) must be able to staff its workers even
            # when those workers are hidden from the settings page.
            for pid in manager.personas.ids():
                entry = manager.personas.get(pid)
                m = getattr(entry, "manifest", None)
                if m is None or m.team != "worker":
                    continue
                if not manager.personas.is_enabled(pid):
                    continue
                out.append(
                    {
                        "persona": pid,
                        "name": m.name,
                        "tagline": m.tagline,
                        "recommended_models": list(m.recommended_models),
                    }
                )
            return {"workers": out}

        return ai.tool(
            team_options,
            metadata=ai.ToolMetadata(
                category="team", risk_level="low", capabilities=["team"]
            ),
        )

    def _steer_tool(self, lead_session_id: str) -> Any:
        """The lead's downward steering verb. Text lands in the worker's session
        attributed [Lead] — queued into a live turn, or a fresh background turn when
        idle. Strictly downward: no worker ever gets this tool."""
        import aisuite as ai

        manager = self

        def steer_worker(worker: str, message: str) -> dict:
            """Send steering text to one of your workers (by actor id). Use for
            exceptions — changed requirements, stop/redirect, unblock guidance;
            routine status flows through the board, not steering."""
            team = manager.teams.for_lead_session(lead_session_id)
            if team is None:
                return {"error": "no team yet — propose one with propose_team first"}
            match = next((w for w in team.workers if w.actor == worker), None)
            if match is None:
                return {
                    "error": f"no worker '{worker}' on this team",
                    "workers": [w.actor for w in team.workers],
                }
            if manager._loop is None:
                return {"error": "steering is unavailable in this surface"}
            asyncio.run_coroutine_threadsafe(
                manager.deliver_to_session(
                    match.session_id, f"[Lead] {message}".strip()
                ),
                manager._loop,
            )
            return {"ok": True, "delivered_to": worker}

        return ai.tool(
            steer_worker,
            metadata=ai.ToolMetadata(
                category="team", risk_level="medium", capabilities=["team"]
            ),
        )

    def create_team(
        self, session_id: str, members: list[dict[str, Any]], *, enable_chat: bool = False
    ) -> dict[str, Any]:
        """The staffing gate's approved action: PRE-SPAWN worker sessions (state on
        disk, zero tokens — the first model turn fires when the first assignment
        lands) and register the team. Fails closed on personas without `team: worker`."""
        record = self.session_store.load(session_id)
        if record is None or not record.workspace:
            return {"approved": False, "error": "the lead session has no workspace"}
        if self.teams.for_lead_session(session_id) is not None:
            return {"approved": False, "error": "this session already leads a team"}
        space = self._space_for(record, record.workspace)
        workers: list[TeamWorker] = []
        used: set[str] = {"lead", "user", "board"}  # reserved handles
        for member in members:
            pid = str((member or {}).get("persona", "")).strip()
            try:
                ag = get_agent(pid)
            except Exception:
                return {
                    "approved": False,
                    "error": f"unknown coworker '{pid}' — it must be installed and enabled",
                }
            if getattr(ag, "team", None) != "worker":
                # Fail closed: solo personas are not team-eligible — their prompts
                # are written at a human, not a lead.
                return {
                    "approved": False,
                    "error": f"'{pid}' is not team-capable (needs `team: worker`)",
                }
            # The lead-given callname is the HANDLE: board assignee, @mention target,
            # sidebar label. It must be mention-safe and unique on the team.
            name = str(member.get("name", "")).strip().lower()
            if name and not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,23}", name):
                return {
                    "approved": False,
                    "error": f"'{name}' isn't a usable callname — letters/digits/._- only, max 24",
                }
            actor, n = name or pid, 2
            while actor in used:
                actor, n = f"{name or pid}-{n}", n + 1
            used.add(actor)
            worker_sid = uuid.uuid4().hex[:12]
            model = str(member.get("model") or record.model)
            self.session_store.save(
                SessionRecord(
                    session_id=worker_sid,
                    workspace=record.workspace,
                    model=model,
                    mode=record.mode,
                    messages=[],
                    agent=pid,
                )
            )
            # Written via the dedicated setter: the turn-save upsert never touches
            # `team`, so a worker's first turn can't detach it from its lead.
            self.session_store.set_team(
                worker_sid,
                {
                    "team_id": "",  # patched below once the team exists
                    "role": "worker",
                    "actor": actor,
                    "lead_session": session_id,
                    "space": space,
                },
            )
            workers.append(
                TeamWorker(
                    actor=actor,
                    persona=pid,
                    session_id=worker_sid,
                    model=model,
                    reason=str(member.get("reason", "")).strip(),
                )
            )
        chat_group = ""
        if enable_chat:
            group = self.chat_store.create_group(
                "team chat",
                [
                    *(
                        {"name": w.actor, "persona": w.persona, "role": "worker"}
                        for w in workers
                    ),
                    {"name": "lead", "persona": record.agent, "role": "lead"},
                ],
            )
            chat_group = group["group_id"]
        team = self.teams.create(
            space=space,
            lead_session=session_id,
            lead_actor=f"{record.agent}:{session_id[:8]}",
            workers=workers,
            chat_enabled=enable_chat,
            chat_group=chat_group,
        )
        for worker in workers:
            self.session_store.set_team(
                worker.session_id,
                {
                    "team_id": team.team_id,
                    "role": "worker",
                    "actor": worker.actor,
                    "lead_session": session_id,
                    "space": space,
                },
            )
            self._emit_session_created(worker.session_id, worker.persona)
        self.session_store.set_team(
            session_id,
            {
                "team_id": team.team_id,
                "role": "lead",
                "actor": team.lead_actor,
                "space": space,
            },
        )
        return {
            "approved": True,
            "team_id": team.team_id,
            "workers": [
                {"actor": w.actor, "persona": w.persona, "session_id": w.session_id}
                for w in workers
            ],
            "note": (
                "team created — workers are idle until you assign. Create work items"
                " and assign them to the actor ids above; review-state items are"
                " yours to verify."
            ),
        }

    def kick_team_tick(self) -> None:
        """Nudge the wake plumbing from outside the turn loop — e.g. after an
        external board client writes through the `/v1/board` API, so a review or a
        new filing reaches the lead now, not at the next 30s scheduler tick."""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.team_tick(), self._loop)

    async def team_tick(self) -> int:
        """Drain team queues (called each scheduler tick + kicked after team turns).
        One wake consumes a burst as one digest; durable-until-consumed — the cursor
        advances only after the delivery turn is dispatched."""
        delivered = 0
        for team in self.teams.all():
            if team.paused:
                continue
            for worker in team.workers:
                delivered += await self._drain_team_member(
                    team,
                    session_id=worker.session_id,
                    actor=worker.actor,
                    is_lead=False,
                )
            delivered += await self._drain_team_member(
                team,
                session_id=team.lead_session,
                actor=team.lead_actor,
                is_lead=True,
            )
            delivered += await self._maybe_backstop_lead(team)
        return delivered

    # The lead owns its cadence (sleep_until, stretch-when-quiet); this backstop only
    # exists because prompts aren't guarantees. A forgotten timer must never orphan
    # a running team — and it de-facto covers a worker dying without a transition
    # (its item goes stale; the backstop wake surfaces it in the digest).
    TEAM_LEAD_BACKSTOP_SECS = 600

    def _lead_backstop_due(self, team) -> bool:
        sid = team.lead_session
        if self.is_running(sid) or sid in self._team_inflight:
            return False
        if self.wakes.pending(sid):
            return False  # a timer is set — the lead is on cadence, not forgotten
        # Restart-safe: the first observation starts the clock instead of waking.
        last = self._team_last_alive.setdefault(sid, time.time())
        if time.time() - last < self.TEAM_LEAD_BACKSTOP_SECS:
            return False
        try:
            items = self.team_store.list_items(team.space, self._user_actor())
        except Exception:
            return False
        return any(
            i["state"] in ("in_progress", "blocked", "review") for i in items
        )

    async def _maybe_backstop_lead(self, team) -> int:
        if not self._lead_backstop_due(team):
            return 0
        if not self.teams.count_wake(team.team_id, cap=self.TEAM_WAKE_CAP_PER_HOUR):
            return 0
        sid = team.lead_session
        self._team_last_alive[sid] = time.time()
        message = (
            "⏰ Backstop check — work is in flight but you had no check-in timer"
            " set.\n\n"
            + (self.team_staleness_digest(sid) or "Board state unavailable.")
            + "\n\nGlance, act only if something needs you, and set your next"
            " check-in with sleep_until (start 3–5 minutes out; stretch when quiet)."
        )
        self._team_inflight.add(sid)

        async def _deliver() -> None:
            try:
                await self.deliver_to_session(
                    sid, message, source=self._board_source(team, message)
                )
            finally:
                self._team_inflight.discard(sid)

        asyncio.create_task(_deliver())
        return 1

    async def _drain_team_member(
        self, team, *, session_id: str, actor: str, is_lead: bool
    ) -> int:
        # Interest follows the assignment relation: everyone's feed is the events
        # on their slice (assigned ∪ filed) — comments, moves, reassignments. The
        # lead additionally subscribes to the board-wide decision classes.
        directs = self.team_store.feed_for(team.space, actor)
        subs = (
            self.team_store.subscribed_events(team.space, actor) if is_lead else []
        )
        if subs:
            seen = {e["seq"] for e in subs}
            directs = [e for e in directs if e["seq"] not in seen]
        chat_handle = "lead" if is_lead else actor
        chats = (
            self.chat_store.unread_for(team.chat_group, chat_handle)
            if team.chat_enabled and team.chat_group
            else []
        )
        # Cancel is top-priority: an in-flight worker gets interrupted NOW; the
        # queued notice (delivered when the turn dies) tells it why. Only for the
        # item's ASSIGNEE — a filer merely hears about it.
        def _holds(event) -> bool:
            try:
                item = self.team_store.get_item(team.space, int(event["item_id"]))
            except Exception:
                return False
            return item["assignee"] == actor

        cancels = [
            e
            for e in directs
            if e["kind"] == "item_transitioned"
            and (e.get("payload") or {}).get("to") == "canceled"
            and _holds(e)
        ]
        if cancels and self.is_running(session_id):
            engine = self._engines.get(session_id)
            if engine is not None:
                engine.request_interrupt()
        if not directs and not subs and not chats:
            return 0
        if self.is_running(session_id) or session_id in self._team_inflight:
            return 0  # it will drain on its next turn end / next tick
        if not self.teams.count_wake(team.team_id, cap=self.TEAM_WAKE_CAP_PER_HOUR):
            logger.warning("team %s paused for budget this hour", team.team_id)
            return 0
        message, rows = self._team_digest(
            team, directs, subs, chats, is_lead=is_lead, reader=actor
        )
        self._team_inflight.add(session_id)
        source = self._board_source(team, message, rows=rows)

        async def _deliver() -> None:
            try:
                await self.deliver_to_session(session_id, message, source=source)
                # Consume only after the turn dispatched: a crash before this replays
                # the batch next tick (at-least-once, never silently lost).
                # The feed cursor advances past BOTH batches: a subs event deduped
                # out of directs must not replay as a direct next tick.
                delivered = [e["seq"] for e in directs] + [e["seq"] for e in subs]
                if delivered:
                    self.team_store.consume_feed(team.space, actor, max(delivered))
                if subs:
                    self.team_store.consume_subscription(
                        team.space, actor, subs[-1]["seq"]
                    )
                if chats:
                    self.chat_store.consume(
                        team.chat_group, chat_handle, chats[-1]["seq"]
                    )
            finally:
                self._team_inflight.discard(session_id)

        asyncio.create_task(_deliver())
        return 1

    # Long comment/hand-off bodies are already durable on the board — the wake
    # message's job is to say what needs DECISIONS, not to re-carry the evidence
    # into the recipient's context on every wake (owner ruling 2026-08-16). The
    # model text clamps hard; the UI sidecar rows clamp softer (the human gets a
    # bigger excerpt on click without re-inflating the lead's prompt).
    DIGEST_CLAMP_MODEL = 300
    DIGEST_CLAMP_UI = 600

    @staticmethod
    def _clamp(text: str, limit: int, *, suffix: str = "…") -> str:
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + suffix

    def _team_digest(
        self,
        team,
        directs: list[dict],
        subs: list[dict],
        chats: Optional[list[dict]] = None,
        *,
        is_lead: bool,
        reader: str = "",
    ) -> tuple[str, list[dict]]:
        """Coalesce one queue batch into one wake message. Deterministic, computed
        by code — the model does judgment, not arithmetic. Returns (model text,
        structured rows) — the rows ride the display sidecar so the GUI renders a
        collapsed BoardWakeCard instead of re-parsing prose."""
        clamp = lambda text: self._clamp(  # noqa: E731 — two-site local shorthand
            text, self.DIGEST_CLAMP_MODEL, suffix=" … (full text on the board)"
        )
        lines: list[str] = []
        rows: list[dict] = []
        for event in directs + subs:
            item_id = event.get("item_id")
            payload = event.get("payload") or {}
            item = None
            if item_id is not None:
                try:
                    item = self.team_store.get_item(team.space, int(item_id))
                except Exception:
                    item = None
            title = f"#{item_id} {item['title']}" if item else f"#{item_id}"
            row = {
                "item": item_id,
                "title": item["title"] if item else "",
                "actor": event.get("actor", ""),
            }
            if event["kind"] == "item_assigned":
                if item is None:
                    continue
                assignee = payload.get("assignee") or ""
                if payload.get("claimed"):
                    # A self-claim surfacing in the lead's subscription feed —
                    # supervision by exception, not an assignment to the reader.
                    lines.append(
                        f"{event['actor']} claimed {title} — it's theirs now;"
                        " reassign or cancel if that's wrong."
                    )
                    rows.append({**row, "kind": "claimed"})
                    continue
                if reader and payload.get("previous") == reader and assignee != reader:
                    # The reader just LOST this item — its interest ends here.
                    lines.append(
                        f"{title} was reassigned to {assignee} by {event['actor']}"
                        " — stop any work on it; hand off context via a comment"
                        " if useful."
                    )
                    rows.append({**row, "kind": "assigned", "assignee": assignee})
                    continue
                if reader and assignee != reader:
                    # Someone else's assignment surfacing in a broader feed.
                    lines.append(f"{title} assigned to {assignee} by {event['actor']}")
                    rows.append({**row, "kind": "assigned", "assignee": assignee})
                    continue
                lines.append(
                    f"You've been assigned work item {title}.\n"
                    f"  Done when: {item['criteria']}"
                    + (f"\n  Details: {item['description']}" if item["description"] else "")
                )
                rows.append({**row, "kind": "assigned", "assignee": assignee})
            elif event["kind"] == "item_transitioned":
                to = payload.get("to", "?")
                comment = clamp(payload.get("comment") or "")
                note = f" — “{comment}”" if comment else ""
                if to == "canceled" and not is_lead:
                    lines.append(
                        f"{title} was CANCELED by {event['actor']}{note} — stop any"
                        " work on it and pick up your other assignments."
                    )
                else:
                    lines.append(f"{title} moved to {to} by {event['actor']}{note}")
                rows.append(
                    {
                        **row,
                        "kind": "moved",
                        "to": to,
                        "note": self._clamp(
                            payload.get("comment") or "", self.DIGEST_CLAMP_UI
                        ),
                    }
                )
            elif event["kind"] == "item_created":
                lines.append(f"New item filed by {event['actor']}: {title}")
                rows.append({**row, "kind": "filed"})
            elif event["kind"] == "item_commented":
                lines.append(
                    f"Comment on {title} by {event['actor']}:"
                    f" {clamp(payload.get('body', ''))}"
                )
                rows.append(
                    {
                        **row,
                        "kind": "comment",
                        "note": self._clamp(
                            payload.get("body") or "", self.DIGEST_CLAMP_UI
                        ),
                    }
                )
        for chat in chats or []:
            who = chat["author"] if chat["author_role"] != "user" else "[User]"
            lines.append(f"# team chat — {who}: {clamp(chat['text'])}")
            rows.append(
                {
                    "kind": "chat",
                    "actor": who,
                    "note": self._clamp(chat["text"], self.DIGEST_CLAMP_UI),
                }
            )
        body = "\n".join(f"- {line}" for line in lines) or "- (no detail)"
        if is_lead:
            message = (
                "⏰ Board wake — your team needs decisions:\n"
                + body
                + "\n\nFull hand-off comments live on the board (get_item)."
                " Verify review items against their acceptance criteria (then"
                " done, or send back with a comment), unblock or reassign blocked"
                " items, and triage new filings. Steer only where needed."
            )
        else:
            message = (
                "[Lead] Board update:\n"
                + body
                + self._roster_note(team)
                + "\n\nMove your item to in_progress when you start; blocked (with a"
                " comment) if stuck; review with a hand-off comment when finished."
                " Journal evidence as you go."
            )
        return message, rows

    @staticmethod
    def _roster_note(team) -> str:
        """Teammate awareness as a mechanism: every worker digest carries the
        roster, so tagging teammates never depends on the lead remembering to
        introduce them."""
        if not team.workers:
            return ""
        mates = "; ".join(
            f"{w.actor} ({w.persona}" + (f" — {w.reason})" if w.reason else ")")
            for w in team.workers
        )
        reach = (
            " Reach them or the lead with @name in # team chat (post_chat)."
            if team.chat_enabled
            else " Coordinate through item comments; the lead reads the board."
        )
        return f"\n\nYour team: {mates}; lead (coordinator).{reach}"

    @staticmethod
    def _board_source(
        team, message: str, *, rows: Optional[list[dict]] = None
    ) -> dict[str, Any]:
        """Display-only MessageSource sidecar for board deliveries — the same
        mechanism connector messages use, so the GUI renders a structured card
        instead of a fake user bubble (owner ask 2026-08-16). The framed message
        stays the model-facing text; this only shapes presentation. `rows` are
        the digest's structured events — the BoardWakeCard renders those
        (collapsed to one line by default) instead of re-parsing the prose."""
        return {
            "connector": "board",
            "kind": "channel",
            "channel_id": team.space,
            "channel_name": "Team board",
            "sender_id": "board",
            "sender_name": "Board",
            "ts": time.time(),
            "text": message,
            "board": {"rows": rows or []},
        }

    def team_staleness_digest(self, session_id: str) -> str:
        """Attached to a lead's TIMER wakes: pure code over the board — a
        nothing's-wrong wake is one cheap glance, never a re-survey. Scoped by
        role membership: sessions with no team role get nothing."""
        team = self.teams.for_lead_session(session_id)
        if team is None:
            return ""
        try:
            items = self.team_store.list_items(team.space, self._user_actor())
        except Exception:
            return ""
        by_state: dict[str, int] = {}
        for item in items:
            by_state[item["state"]] = by_state.get(item["state"], 0) + 1
        unassigned = sum(
            1 for i in items if i["state"] == "open" and not i["assignee"]
        )
        parts = [f"{n} {state}" for state, n in sorted(by_state.items())]
        lines = [f"Board: {', '.join(parts) or 'empty'}."]
        if unassigned:
            lines.append(f"{unassigned} open item(s) have no assignee.")
        reviews = [i for i in items if i["state"] == "review"]
        if reviews:
            lines.append(
                "Awaiting your review: "
                + ", ".join(f"#{i['id']} {i['title']}" for i in reviews[:5])
            )
        blocked = [i for i in items if i["state"] == "blocked"]
        if blocked:
            lines.append(
                "Blocked: " + ", ".join(f"#{i['id']} {i['title']}" for i in blocked[:5])
            )
        return "\n".join(lines)

    def _artifact_scan_root(self, session_id: str) -> Optional[Path]:
        """The dir the Artifacts panel lists: the session's SCRATCH surface only
        (workspace-scratch-design.md §2.5). For orphan sessions that's the workspace
        itself; for folder-gated sessions it's the side scratch root — never the user's
        repo, which would list the whole codebase as 'artifacts'."""
        record = self.session_store.load(session_id)
        workspace = record.workspace if record else self.default_workspace
        if workspace and self.is_temp_workspace(workspace):
            return Path(workspace).expanduser().resolve()
        if self._SESSION_ID_RE.match(session_id or "") and session_id not in {".", ".."}:
            d = (self.scratch_base() / session_id).resolve()
            if d.is_dir():
                return d
        # Legacy fallback (pre-universal-scratch sessions on a custom scratch base):
        # a workspace that is itself disposable still scans.
        if workspace and not record:
            return Path(workspace).expanduser().resolve()
        return None

    def list_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        root = self._artifact_scan_root(session_id)
        if root is None or not root.is_dir():
            return []
        out: list[dict[str, Any]] = []
        suffixes = {
            ".md",
            ".markdown",
            ".html",
            ".htm",
            ".txt",
            ".json",
            ".csv",
            ".tsv",
            ".py",
            ".js",
            ".ts",
            ".tsx",
            ".css",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".pdf",
            ".xlsx",
            ".xls",
            ".pptx",
            ".ppt",
            ".pptm",
            ".docx",
            ".doc",
            ".docm",
        }
        # os.walk with in-place pruning, NOT rglob: rglob descends first and filters after,
        # so a home-directory workspace walked into ~/Library and tripped the macOS App Data
        # TCC prompt ("OpenWorker would like to access data from other apps") on every turn.
        # Pruning here means those directories are never entered at all.
        from ..tools.search import OS_DATA_DIRS

        skip = {"node_modules", "target", "dist", "__pycache__"} | OS_DATA_DIRS
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in skip]
            for name in files:
                if name.startswith("."):
                    continue
                path = Path(dirpath) / name
                if path.suffix.lower() not in suffixes:
                    continue
                try:
                    st = path.stat()
                    if not path.is_file():
                        continue
                    out.append(
                        {
                            "path": str(path.relative_to(root)),
                            # Absolute path for "Copy path" — the relative one is useless
                            # outside the app (tester catch 2026-07-12: it copied just the
                            # filename).
                            "abs_path": str(path),
                            "name": path.name,
                            "kind": _artifact_kind(path),
                            "size": st.st_size,
                            "modified_at": st.st_mtime,
                        }
                    )
                except OSError:
                    continue
        out.sort(key=lambda a: a["modified_at"], reverse=True)
        return out[:80]

    MAX_BINARY_PREVIEW = 25 * 1024 * 1024  # base64-over-JSON gets heavy past this

    def _artifact_target(
        self, session_id: str, path: str, *, allow_dir: bool = False
    ) -> tuple[Optional[Path], Optional[str]]:
        """Resolve an artifact path under one of the session's roots — workspace first,
        then the scratch dir, then user-granted extra roots. Universal scratch means a
        gated session's artifacts live BESIDE its workspace, so single-root resolution
        would orphan every transcript chip pointing at scratch."""
        record = self.session_store.load(session_id)
        workspace = record.workspace if record else self.default_workspace
        candidates: list[Path] = []
        if workspace:
            candidates.append(Path(workspace).expanduser().resolve())
        if self._SESSION_ID_RE.match(session_id or "") and session_id not in {".", ".."}:
            scratch = (self.scratch_base() / session_id).resolve()
            if scratch.is_dir() and scratch not in candidates:
                candidates.append(scratch)
        for r in (record.extra_roots if record else []) or []:
            p = Path(str(r.get("path", ""))).expanduser()
            if p.is_dir():
                rp = p.resolve()
                if rp not in candidates:
                    candidates.append(rp)
        if not candidates:
            return None, "no workspace"
        found_missing = False
        for root in candidates:
            target = (root / path).expanduser().resolve()
            try:
                target.relative_to(root)
            except ValueError:
                continue
            if allow_dir and target.is_dir():
                return target, None
            if target.is_file():
                return target, None
            found_missing = True
        if found_missing:
            return None, (
                "This isn't in the conversation's folder anymore — it may have been "
                "moved or deleted."
            )
        return None, "path escapes workspace"

    def read_artifact(self, session_id: str, path: str) -> dict[str, Any]:
        # Folders are readable too (a model sometimes links a whole package, e.g. a skill
        # build dir): return a listing the viewer can render instead of a dead end.
        target, err = self._artifact_target(session_id, path, allow_dir=True)
        if target is None:
            return {"ok": False, "error": err}
        if target.is_dir():
            entries: list[dict[str, Any]] = []
            try:
                children = sorted(
                    target.iterdir(), key=lambda c: (c.is_file(), c.name.lower())
                )
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
            for child in children[:500]:
                try:
                    size = 0 if child.is_dir() else child.stat().st_size
                except OSError:
                    continue
                entries.append({"name": child.name, "dir": child.is_dir(), "size": size})
            return {"ok": True, "path": path, "kind": "folder", "entries": entries}
        kind = _artifact_kind(target)
        if kind == "office":
            # PowerPoint/Word binaries can't be previewed inline; the UI offers
            # "Open in default app" instead of trying to render them.
            return {"ok": True, "path": path, "kind": "office"}
        if kind in ("image", "pdf", "sheet"):
            import base64

            if target.stat().st_size > self.MAX_BINARY_PREVIEW:
                return {
                    "ok": False,
                    "error": "file too large to preview — use Reveal to open it",
                }
            mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
                ".pdf": "application/pdf",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".xls": "application/vnd.ms-excel",
            }.get(target.suffix.lower(), "application/octet-stream")
            data = base64.b64encode(target.read_bytes()).decode("ascii")
            return {
                "ok": True,
                "path": path,
                "kind": kind,
                "data_url": f"data:{mime};base64,{data}",
            }
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"ok": False, "error": "binary file cannot be previewed"}
        return {
            "ok": True,
            "path": path,
            "kind": kind,
            "content": text[:500000],
            "truncated": len(text) > 500000,
        }

    def reveal_artifact(
        self, session_id: str, path: str, mode: str = "reveal"
    ) -> dict[str, Any]:
        """Show the file in the OS file manager (`reveal`) or open it with its default app
        (`open`). The server runs on the user's machine in both desktop and browser builds, so
        this is local. Cross-platform: macOS `open`, Windows Explorer/ShellExecute, Linux
        `xdg-open`."""
        import os
        import subprocess
        import sys

        target, err = self._artifact_target(session_id, path, allow_dir=True)
        if target is None:
            return {"ok": False, "error": err}
        # A folder "opens" as itself in the file manager, whatever the mode.
        is_dir = target.is_dir()
        try:
            if sys.platform == "darwin":
                args = (
                    ["open", "-R", str(target)]
                    if mode == "reveal" and not is_dir
                    else ["open", str(target)]
                )
                subprocess.Popen(
                    args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            elif sys.platform == "win32":
                if mode == "reveal" and not is_dir:
                    # Explorer wants the path glued to the switch: /select,<path>
                    subprocess.Popen(["explorer", f"/select,{target}"])
                else:
                    os.startfile(str(target))  # type: ignore[attr-defined]  # open in default app
            else:  # Linux/BSD
                tgt = str(target.parent) if mode == "reveal" and not is_dir else str(target)
                subprocess.Popen(
                    ["xdg-open", tgt],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    # -- web search -------------------------------------------------------------
    def get_web_search(self) -> dict[str, Any]:
        from ..config import load_config
        from ..web import provider_names

        profile = self.secrets.get("web_search:default") or {}
        provider = (
            profile.get("provider") or load_config().web_search_provider or "duckduckgo"
        )
        return {
            "provider": provider,
            "has_key": bool(profile.get("api_key")),
            "providers": provider_names(),
        }

    def set_web_search(
        self, provider: str, api_key: Optional[str] = None
    ) -> dict[str, Any]:
        from ..web import provider_names

        if provider not in provider_names():
            return {"ok": False, "error": f"unknown provider: {provider}"}
        before = self.get_web_search()["provider"]
        profile: dict[str, Any] = {"provider": provider}
        if api_key:
            profile["api_key"] = api_key
        self.secrets.put("web_search:default", profile)
        # §1.9: "Always allow searches this session" is consent to a NAMED destination —
        # the card says which provider the queries go to. A new provider is a new
        # destination, so every live session's grant dies with the old one. (Scheduled
        # tasks that name-allow web_search are unaffected: their approver re-allows.)
        if provider != before:
            for engine in self._engines.values():
                engine.permissions.session_allow_tools.discard("web_search")
        return {"ok": True, "provider": provider}

    # -- model providers (OpenAI, Ollama, …) ------------------------------------
    def get_providers(self) -> list[dict[str, Any]]:
        """Descriptor + per-provider status for the Settings UI. Never returns secret values;
        non-secret field values (e.g. the Ollama base URL) ARE returned so the form can prefill.
        """
        out: list[dict[str, Any]] = []
        for d in provider_descriptors():
            profile = self.secrets.get(f"provider:{d.name}") or {}
            configured = descriptor_configured(d, profile)
            values = {
                f.key: profile.get(f.key)
                for f in d.fields
                if not f.secret and profile.get(f.key)
            }
            row = {
                **d.to_dict(),
                "configured": configured,
                "values": values,
                "suggested_models": self._suggested_models(d.name),
                # Key hygiene for the Settings pane: when the key was saved (date, stamped
                # by set_provider) and when the provider last served a completion (epoch,
                # stamped by the router's on_use hook). Absent for env-only config.
                "key_set_at": profile.get("key_set_at"),
                "last_used_at": (self._prefs.get("provider_last_used") or {}).get(
                    d.name
                ),
            }
            if d.auth == "oauth":
                # Sign-in state instead of key state; the token values themselves
                # never leave the SecretStore.
                row["signed_in"] = configured
                row["account"] = profile.get("account_email") or profile.get(
                    "account_id"
                )
                if d.name == "openai-codex":
                    row["authorizing"] = self._codex_authorizing
                    row["last_error"] = self._codex_error
            out.append(row)
        return out

    def pick_native_folder(self) -> dict[str, Any]:
        """Open the OS folder picker FROM THE SIDECAR — the browser GUI can't obtain absolute
        paths from web file dialogs, but the sidecar is local and can (the desktop shell uses
        Tauri's own picker instead). Blocking until pick/cancel; callers run it off-thread.
        """
        import os
        import subprocess
        import sys

        prompt = "Give the coworker access to a folder"
        if sys.platform == "darwin":
            candidates = [
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to activate',
                    "-e",
                    f'POSIX path of (choose folder with prompt "{prompt}")',
                ]
            ]
        elif sys.platform == "win32":
            # WinForms folder dialog via PowerShell — no extra deps. -STA is required
            # (the dialog silently fails in the default MTA apartment).
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                f"$f.Description = '{prompt}'; "
                "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
                "{ [Console]::Out.Write($f.SelectedPath) }"
            )
            candidates = [["powershell.exe", "-NoProfile", "-STA", "-Command", ps]]
        else:
            # Linux/BSD: no single blessed dialog, and none is guaranteed present — a minimal
            # desktop (ChromeOS Crostini's container ships neither) may have none at all. Try
            # the two that come with the big desktops, then zenity's Qt clone; the first one
            # actually installed wins. All missing → the GUI's paste-a-path input remains the
            # way in, which is why this returns an error rather than raising.
            home = os.path.expanduser("~")
            candidates = [
                ["zenity", "--file-selection", "--directory", f"--title={prompt}"],
                ["kdialog", "--getexistingdirectory", home, "--title", prompt],
                ["qarma", "--file-selection", "--directory", f"--title={prompt}"],
            ]
        for cmd in candidates:
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            except FileNotFoundError:
                continue  # dialog not installed — try the next one
            except (OSError, subprocess.TimeoutExpired):
                break
            path = (out.stdout or "").strip()
            if out.returncode != 0 or not path:
                # The dialog ran and the user dismissed it. Never fall through to another
                # picker here — a second window popping up after a cancel is worse than none.
                return {"ok": False, "canceled": True}
            return {"ok": True, "path": path}
        return {"ok": False, "error": "no native folder picker available"}

    def _note_provider_use(self, name: str) -> None:
        """Router on_use hook: remember when a provider last served a completion. Persisted
        THROTTLED (once per provider per minute) — this fires on every model call, from engine
        threads, and prefs.json isn't a place for a write-per-token-of-work."""
        import time

        now = time.time()
        used = self._prefs.setdefault("provider_last_used", {})
        if now - float(used.get(name) or 0) < 60:
            return
        used[name] = now
        try:
            self._save_prefs()
        except OSError:
            pass

    # Suggestions for the OpenAI-compatible vendor providers (checked against vendor docs
    # 2026-07-04; refresh alongside `recommended_model` in providers/registry.py).
    COMPAT_MODELS = {
        "zai": ["glm-5.2", "glm-4.6"],
        "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "kimi": ["kimi-k2.6", "kimi-k2.5"],
        "minimax": ["MiniMax-M2.5", "MiniMax-M2.5-highspeed", "MiniMax-M3"],
        "qwen": ["qwen3-max", "qwen3-coder-plus", "qwen-plus"],
        "xai": ["grok-4.3", "grok-4"],
        "mistral": ["mistral-large-latest", "mistral-small-latest"],
    }

    def _suggested_models(self, name: str) -> list[str]:
        """Bare model-name suggestions for the 'add model' form (datalist), per provider.
        Ollama → live `/api/tags` (best-effort); everyone else → the curated matrix,
        topped up with the compat-vendor extras the matrix doesn't vouch for."""
        if name == "ollama":
            return [m.split(":", 1)[-1] for m in self._ollama_models()]
        from ..providers.matrix import models_for_provider

        return list(
            dict.fromkeys(
                [*models_for_provider(name), *self.COMPAT_MODELS.get(name, [])]
            )
        )

    def set_provider(
        self, name: str, fields: Optional[dict[str, Any]]
    ) -> dict[str, Any]:
        """Store a provider's config in its `provider:<name>` SecretStore profile and rebuild
        its cached client. Merges provided fields into any existing profile."""
        d = get_descriptor(name)
        if d is None:
            return {"ok": False, "error": f"unknown provider: {name}"}
        fields = fields or {}
        profile = dict(self.secrets.get(f"provider:{name}") or {})
        for f in d.fields:
            if f.key not in fields:
                continue
            val = fields.get(f.key)
            if isinstance(val, str):
                val = val.strip()
            if val:
                profile[f.key] = val
            elif not f.required:
                profile.pop(f.key, None)
        missing = [f.label for f in d.fields if f.required and not profile.get(f.key)]
        if missing:
            return {"ok": False, "error": "missing: " + ", ".join(missing)}
        # A (re)pasted key stamps its save date — Settings shows "key added <date>" so stale
        # keys are visible. Endpoint-only saves keep the original stamp.
        if isinstance(fields.get("api_key"), str) and fields["api_key"].strip():
            from datetime import date

            profile["key_set_at"] = date.today().isoformat()
        self.secrets.put(f"provider:{name}", profile)
        self._refresh_provider(name)
        # Convenience: if the provider recommends a model and it's actually available, add it to
        # the curated list so it shows up in the composer right after configuring the provider.
        rec = d.recommended_model
        added: Optional[str] = None
        if rec and rec in self._suggested_models(name):
            # OpenAI models stay bare (the router's default); others carry their prefix.
            added = rec if name == "openai" else f"{name}:{rec}"
            self.add_model(added)
        # First working provider wins the default: if the current default model belongs to a
        # provider with no usable config (the fresh-install gpt-5.6-sol case), switch the default to
        # this provider's model. A default that already works is never stolen.
        if added and not self._provider_configured(self._model_provider(self.model)):
            self.set_default_model(added)
        return {"ok": True, "provider": name, "recommended_model": rec}

    def remove_provider(self, name: str) -> dict[str, Any]:
        """Forget a provider's stored config (Settings ▸ Models "Remove key"). The whole
        `provider:<name>` profile goes — key, endpoint, key_set_at — so the provider reads
        as never configured. Curated models stay; they just gray out until a new key."""
        d = get_descriptor(name)
        if d is None:
            return {"ok": False, "error": f"unknown provider: {name}"}
        self.secrets.delete(f"provider:{name}")
        self._refresh_provider(name)
        return {"ok": True, "provider": name}

    # -- ChatGPT-subscription provider (OAuth, no key) ---------------------------
    def begin_codex_signin(self) -> None:
        """Flag `authorizing` BEFORE the background sign-in task starts, so the GUI's
        first poll after the button press already shows it (same reasoning as
        begin_mcp_connect)."""
        self._codex_authorizing = True
        self._codex_error = None

    async def codex_signin(self) -> dict[str, Any]:
        """Run the interactive browser sign-in and store the tokens. Long-running
        (the user completes it in the browser) — routes run it as a background task
        and the GUI polls codex_status for the flip."""
        from ..providers import codex_auth

        self._codex_authorizing = True
        self._codex_error = None
        try:
            result = await codex_auth.sign_in(self.secrets)
        except Exception as exc:
            self._codex_error = str(exc)
            return {"ok": False, "error": str(exc)}
        finally:
            self._codex_authorizing = False
        self._refresh_provider("openai-codex")
        # Same convenience as set_provider: surface the recommended model right away,
        # and win the default when the current default's provider isn't usable.
        added = "openai-codex:gpt-5.6-sol"
        self.add_model(added)
        if not self._provider_configured(self._model_provider(self.model)):
            self.set_default_model(added)
        return result

    def codex_status(self) -> dict[str, Any]:
        from ..providers import codex_auth

        store = codex_auth.CodexTokenStore(self.secrets)
        return {
            "signed_in": store.signed_in(),
            "account": store.account_label(),
            "authorizing": self._codex_authorizing,
            "last_error": self._codex_error,
            "authorize_url": codex_auth.last_authorize_url,
        }

    def codex_signout(self) -> dict[str, Any]:
        from ..providers import codex_auth

        had_tokens = codex_auth.CodexTokenStore(self.secrets).clear()
        self._codex_error = None
        self._refresh_provider("openai-codex")
        return {"ok": True, "had_tokens": had_tokens}

    def verify_provider(
        self, name: str, fields: Optional[dict[str, Any]]
    ) -> dict[str, Any]:
        """Test a provider's credentials with a live read-only call, WITHOUT persisting them, so
        onboarding can offer a "Test" button. Falls back to stored/env values when the form left
        a field blank (e.g. testing an already-configured provider)."""
        import os

        d = get_descriptor(name)
        if d is None:
            return {"ok": False, "error": f"unknown provider: {name}"}
        if d.auth == "oauth":
            # No key form — verify from the stored token set (signed-out / expired / OK).
            from ..providers import codex_auth

            return codex_auth.verify(self.secrets)
        fields = fields or {}
        profile = self.secrets.get(f"provider:{name}") or {}
        merged = {}
        for f in d.fields:
            val = fields.get(f.key) or profile.get(f.key) or ""
            if isinstance(val, str):
                val = val.strip()
            if val:
                merged[f.key] = val
        api_key = merged.get("api_key", "")
        if not api_key and d.env_key:
            api_key = os.environ.get(d.env_key, "").strip()
        has_key_field = any(f.key == "api_key" for f in d.fields)
        if d.needs_key and has_key_field and not api_key:
            return {"ok": False, "error": "Enter an API key to test."}
        if d.needs_key and not has_key_field:
            # Multi-field cloud providers (Bedrock): required fields must be present;
            # actual credentials may be ambient (~/.aws, env) and are checked by the call.
            missing = [f.label for f in d.fields if f.required and not merged.get(f.key)]
            if missing:
                return {"ok": False, "error": "missing: " + ", ".join(missing)}
        return verify_provider_key(
            name, api_key=api_key, base_url=merged.get("base_url", ""), fields=merged
        )

    def _model_provider(self, model: str) -> str:
        """The provider a model string routes to (known `prefix:` or the OpenAI default)."""
        if ":" in (model or ""):
            prefix = model.split(":", 1)[0]
            if get_descriptor(prefix) is not None:
                return prefix
        return "openai"

    def _provider_configured(self, name: str) -> bool:
        d = get_descriptor(name)
        if d is None:
            return False
        return descriptor_configured(d, self.secrets.get(f"provider:{name}") or {})

    # -- settings / prefs (model API key, default model, onboarding) -------------
    def _prefs_path(self) -> Path:
        return self._data_base / "prefs.json"

    def _load_prefs(self) -> dict[str, Any]:
        try:
            return json.loads(self._prefs_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_prefs(self) -> None:
        self._prefs_path().write_text(
            json.dumps(self._prefs, indent=2), encoding="utf-8"
        )

    # -- direct-message routing -------------------------------------------------
    def dm_session(self) -> Optional[str]:
        """The session a DM to the bot is routed to (user-designated). None → DMs are parked."""
        sid = self._prefs.get("dm_session")
        return sid or None

    def set_dm_session(self, session_id: Optional[str]) -> dict[str, Any]:
        """Designate (or clear, with a falsy id) the session that handles incoming DMs."""
        sid = (session_id or "").strip()
        if sid:
            self._prefs["dm_session"] = sid
        else:
            self._prefs.pop("dm_session", None)
        self._save_prefs()
        return {"ok": True, "dm_session": self.dm_session()}

    def _ollama_alive(self) -> bool:
        """Best-effort local-Ollama liveness, cached 30s (get_settings runs on every GUI
        fetch — no 2s probe inline). Keyless is not the same as PRESENT: `ollama:*` picker
        entries render only when an Ollama actually answers, so a machine with no Ollama
        never shows phantom local models (e.g. a stray pasted string saved as a model id,
        caught 2026-07-21)."""
        import time

        now = time.monotonic()
        cached = getattr(self, "_ollama_alive_cache", None)
        if cached and now - cached[0] < 30:
            return cached[1]
        profile = self.secrets.get("provider:ollama") or {}
        base = (profile.get("base_url") or "http://localhost:11434").strip().rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        try:
            import httpx

            alive = httpx.get(base + "/api/tags", timeout=0.8).status_code == 200
        except Exception:
            alive = False
        self._ollama_alive_cache = (now, alive)
        return alive

    def _ollama_models(self) -> list[str]:
        """Live list of models pulled into the configured Ollama server (via its native
        `/api/tags`), as `ollama:<name>` so they're directly selectable. Empty if Ollama isn't
        configured or unreachable — best-effort, never raises."""
        profile = self.secrets.get("provider:ollama")
        if not profile:
            return []
        base = (profile.get("base_url") or "http://localhost:11434").strip().rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        try:
            import httpx

            data = httpx.get(base + "/api/tags", timeout=2.0).json()
            return [
                f"ollama:{m['name']}" for m in data.get("models", []) if m.get("name")
            ]
        except Exception:
            return []

    def _curated_models(self) -> list[str]:
        """The models offered in the composer's selector: every curated-matrix model
        (`get_settings` culls the ones whose provider has no key) plus custom ids the user
        added, minus matrix models they removed. Deliberately NO built-in seed list — a
        fresh install offers nothing until a provider key exists, and then exactly that
        provider's matrix models appear. The active default is always kept selectable.
        """
        from ..providers.matrix import MATRIX

        user = self._prefs.get("models")
        user = user if isinstance(user, list) else []
        hidden = set(self._prefs.get("hidden_models") or [])
        models = [m for m in [*MATRIX, *user] if m not in hidden]
        return list(dict.fromkeys([self.model, *models]))

    def add_model(self, model: str) -> dict[str, Any]:
        """Add a model id (e.g. `gpt-4o`, `ollama:qwen2.5-coder:32b`) to the picker.
        Custom ids persist in prefs; a previously removed matrix model is just unhidden
        (storing it too would shadow future matrix updates)."""
        from ..providers.matrix import MATRIX

        model = (model or "").strip()
        if not model:
            return {"ok": False, "error": "empty model"}
        hidden = [m for m in self._prefs.get("hidden_models") or [] if m != model]
        if hidden:
            self._prefs["hidden_models"] = hidden
        else:
            self._prefs.pop("hidden_models", None)
        models = self._prefs.get("models")
        models = models if isinstance(models, list) else []
        if model not in models and model not in MATRIX:
            models.append(model)
        self._prefs["models"] = models
        self._save_prefs()
        return {"ok": True, **self.get_settings()}

    def remove_model(self, model: str) -> dict[str, Any]:
        """Remove a model id from the picker. Custom ids are dropped; matrix models are
        hidden by id (the matrix is derived, not stored, so a bare drop would resurrect
        them on the next read)."""
        from ..providers.matrix import MATRIX

        models = self._prefs.get("models")
        models = models if isinstance(models, list) else []
        self._prefs["models"] = [m for m in models if m != model]
        if model in MATRIX:
            hidden = self._prefs.get("hidden_models") or []
            if model not in hidden:
                self._prefs["hidden_models"] = [*hidden, model]
        self._save_prefs()
        return {"ok": True, **self.get_settings()}

    def get_settings(self) -> dict[str, Any]:
        """Model-access + UI status. Never returns the key; `source` says where it comes from."""
        import os

        env_key = bool(os.environ.get("OPENAI_API_KEY"))
        stored = bool((self.secrets.get("provider:openai") or {}).get("api_key"))
        # Only surface models whose provider is actually configured — the composer picker
        # reflects exactly what's connected. The active default is always kept selectable
        # (it's hidden behind the "No model" state until a provider is connected anyway).
        # Ollama is keyless, so "configured" is meaningless there — its models show only
        # while a local Ollama answers (cached liveness probe).
        def _selectable(m: str) -> bool:
            provider = self._model_provider(m)
            if provider == "ollama":
                return self._ollama_alive()
            return self._provider_configured(provider)

        selectable = [m for m in self._curated_models() if _selectable(m)]
        if self.model not in selectable:
            selectable.insert(0, self.model)
        from ..providers.matrix import model_context_windows, model_labels

        return {
            "provider": "openai",
            "model": self.model,
            "models": selectable,
            # Curated-matrix display names ({full id → "GLM-5.2 · via Together"}) so every
            # picker shows human labels; custom models absent here render their raw id.
            "model_labels": model_labels(),
            # {full id → context window in tokens}, verified matrix entries only —
            # drives the composer's context-fill meter (absent id → meter hides).
            "model_context_windows": model_context_windows(),
            "has_key": env_key or stored,
            # Provider-agnostic "can this default model actually run?" — true when the default
            # model's provider is configured (any provider, not just OpenAI). Drives the GUI's
            # "No model connected" composer chip and the onboarding Skip warning.
            "model_ready": self._provider_configured(self._model_provider(self.model)),
            "source": "env" if env_key else ("store" if stored else None),
            "onboarded": bool(self._prefs.get("onboarded")),
            "experimental_connectors": experimental_enabled(self.secrets),
            "surfaces": self._surfaces(),
            "nav_layout": self._nav_layout(),
            "sessions_peek": self.sessions_peek(),
            "context_bar": self.context_bar(),
            # Auto-Approve feature flag + its shadow-eval sibling (spec §1.5). Drive the
            # Settings toggles and gate the composer's Auto-Approve mode entry.
            "auto_approve": self.auto_approve(),
            "auto_approve_shadow": self.auto_approve_shadow(),
            "scratch_base": self._prefs.get("scratch_base")
            or self.DEFAULT_SCRATCH_BASE,
            # Real on-disk secrets location, so the UI shows the OS-native path instead of a
            # hardcoded POSIX one (Windows -> %APPDATA%\coworker, macOS/Linux -> ~/.config).
            "secrets_path": str(self.secrets.path),
            **self.pdf_settings(),
            **self.compaction_settings_payload(),
        }

    def _surfaces(self) -> dict[str, bool]:
        """Which session surfaces are shown in the sidebar. Cowork is always on; Chat and Code
        are opt-in (default off) so a new user sees Cowork only."""
        return {
            "cowork": True,
            "chat": bool(self._prefs.get("show_chat", False)),
            "code": bool(self._prefs.get("show_code", False)),
        }

    def set_surfaces(
        self, chat: Optional[bool] = None, code: Optional[bool] = None
    ) -> dict[str, Any]:
        """Toggle Chat/Code visibility (Cowork is always shown). Persisted in prefs."""
        if chat is not None:
            self._prefs["show_chat"] = bool(chat)
        if code is not None:
            self._prefs["show_code"] = bool(code)
        self._save_prefs()
        return {"ok": True, "surfaces": self._surfaces()}

    def _nav_layout(self) -> str:
        """Sidebar layout: ``"flat"`` (default) or ``"grouped"`` (by persona). Persisted in
        prefs (UI-REFRESH §7)."""
        return "grouped" if self._prefs.get("nav_layout") == "grouped" else "flat"

    def set_nav_layout(self, nav_layout: str) -> dict[str, Any]:
        """Set + persist the sidebar layout. Unknown values fall back to ``"flat"``."""
        value = "grouped" if (nav_layout or "").strip() == "grouped" else "flat"
        self._prefs["nav_layout"] = value
        self._save_prefs()
        return {"ok": True, "nav_layout": value}

    DEFAULT_SESSIONS_PEEK = 5

    def sessions_peek(self) -> int:
        """How many sessions a sidebar group shows before "Show more" (owner ask, 2026-07-03)."""
        try:
            n = int(self._prefs.get("sessions_peek", self.DEFAULT_SESSIONS_PEEK))
        except (TypeError, ValueError):
            n = self.DEFAULT_SESSIONS_PEEK
        return max(1, min(n, 50))

    def set_sessions_peek(self, n: int) -> dict[str, Any]:
        try:
            self._prefs["sessions_peek"] = max(1, min(int(n), 50))
        except (TypeError, ValueError):
            return {"ok": False, "error": "sessions_peek must be a number"}
        self._save_prefs()
        return {"ok": True, "sessions_peek": self.sessions_peek()}

    def context_bar(self) -> bool:
        """Whether the composer shows the context-window fill bar. OFF by default (owner
        ask): the chip then states the session total, and the popover keeps both numbers."""
        return bool(self._prefs.get("context_bar", False))

    def set_context_bar(self, shown: Any) -> dict[str, Any]:
        self._prefs["context_bar"] = bool(shown)
        self._save_prefs()
        return {"ok": True, "context_bar": self.context_bar()}

    # -- Auto-Approve (spec §1.5, Part 6 step 3) --------------------------------
    # The feature flag and its shadow-eval sibling live in prefs (GUI-writable), falling
    # back to the config.toml value a power user may have hand-set. Prefs is user-global,
    # so a cloned repo still can't enable either — same guarantee as the config path.
    def auto_approve(self) -> bool:
        from ..config import load_config

        if "auto_approve" in self._prefs:
            return bool(self._prefs["auto_approve"])
        return bool(load_config().auto_approve)

    def auto_approve_shadow(self) -> bool:
        from ..config import load_config

        if "auto_approve_shadow" in self._prefs:
            return bool(self._prefs["auto_approve_shadow"])
        return bool(load_config().auto_approve_shadow)

    def set_auto_approve(self, on: Any) -> dict[str, Any]:
        self._prefs["auto_approve"] = bool(on)
        self._save_prefs()
        return {
            "ok": True,
            "auto_approve": self.auto_approve(),
            "auto_approve_shadow": self.auto_approve_shadow(),
        }

    def set_auto_approve_shadow(self, on: Any) -> dict[str, Any]:
        self._prefs["auto_approve_shadow"] = bool(on)
        self._save_prefs()
        return {
            "ok": True,
            "auto_approve": self.auto_approve(),
            "auto_approve_shadow": self.auto_approve_shadow(),
        }

    # -- PDF attachments / token savings (owner ask, 2026-07-17) ----------------
    DEFAULT_PDF_MAX_PAGES = 20
    DEFAULT_PDF_MAX_MB = 10

    def pdf_settings(self) -> dict[str, Any]:
        """Fallback mode for models without native PDF support + the attach-time
        thresholds (Settings → Token savings: big PDFs quietly eat tokens)."""
        from ..pdf_support import FALLBACK_MODES

        mode = self._prefs.get("pdf_fallback")
        try:
            pages = int(self._prefs.get("pdf_max_pages", self.DEFAULT_PDF_MAX_PAGES))
        except (TypeError, ValueError):
            pages = self.DEFAULT_PDF_MAX_PAGES
        try:
            mb = int(self._prefs.get("pdf_max_mb", self.DEFAULT_PDF_MAX_MB))
        except (TypeError, ValueError):
            mb = self.DEFAULT_PDF_MAX_MB
        return {
            "pdf_fallback": mode if mode in FALLBACK_MODES else "text",
            "pdf_max_pages": max(1, min(pages, 100)),
            "pdf_max_mb": max(1, min(mb, 10)),
        }

    def compaction_settings(self) -> dict[str, Any]:
        """The live auto-compaction knobs (OPE-27) — read by every engine per check, so a
        Settings change applies without a rebuild. Only the two spec'd overrides plus the
        summarizer-model pin; absent keys fall back to compaction.py defaults."""
        from ..compaction import DEFAULT_CAP_TOKENS, DEFAULT_THRESHOLD_PCT

        return {
            "threshold_pct": float(
                self._prefs.get("compaction_threshold_pct") or DEFAULT_THRESHOLD_PCT
            ),
            "cap_tokens": int(
                self._prefs.get("compaction_cap_tokens") or DEFAULT_CAP_TOKENS
            ),
            # "" → the session's own model (engine falls back to self.model).
            "model": str(self._prefs.get("compaction_model") or ""),
        }

    def compaction_settings_payload(self) -> dict[str, Any]:
        """The same knobs under REST-facing names (prefixed to keep /v1/settings flat)."""
        settings = self.compaction_settings()
        return {
            "compaction_threshold_pct": settings["threshold_pct"],
            "compaction_cap_tokens": settings["cap_tokens"],
            "compaction_model": settings["model"],
        }

    def set_compaction_settings(
        self,
        threshold_pct: Any = None,
        cap_tokens: Any = None,
        model: Any = None,
    ) -> dict[str, Any]:
        """Persist the auto-compaction overrides (OPE-27). Threshold is a percentage of
        the model's context window (10–95); the cap is an absolute token ceiling; model
        pins the summarizer ('' → the session's own model). Engines read these live via
        `compaction_settings()`, so changes apply to running sessions immediately."""
        if threshold_pct is not None:
            try:
                pct = float(threshold_pct)
            except (TypeError, ValueError):
                return {"ok": False, "error": "compaction_threshold_pct must be a number"}
            if not 0.10 <= pct <= 0.95:
                return {
                    "ok": False,
                    "error": "compaction_threshold_pct must be between 0.10 and 0.95",
                }
            self._prefs["compaction_threshold_pct"] = pct
        if cap_tokens is not None:
            try:
                self._prefs["compaction_cap_tokens"] = max(
                    10_000, min(int(cap_tokens), 2_000_000)
                )
            except (TypeError, ValueError):
                return {"ok": False, "error": "compaction_cap_tokens must be a number"}
        if model is not None:
            self._prefs["compaction_model"] = str(model)
        self._save_prefs()
        return {"ok": True, **self.compaction_settings()}

    def set_pdf_settings(
        self,
        fallback: Any = None,
        max_pages: Any = None,
        max_mb: Any = None,
    ) -> dict[str, Any]:
        from ..pdf_support import FALLBACK_MODES, set_fallback_mode

        if fallback is not None:
            if fallback not in FALLBACK_MODES:
                return {"ok": False, "error": "pdf_fallback must be 'text' or 'images'"}
            self._prefs["pdf_fallback"] = fallback
        for key, value, ceiling in (
            ("pdf_max_pages", max_pages, 100),
            ("pdf_max_mb", max_mb, 10),
        ):
            if value is None:
                continue
            try:
                self._prefs[key] = max(1, min(int(value), ceiling))
            except (TypeError, ValueError):
                return {"ok": False, "error": f"{key} must be a number"}
        self._save_prefs()
        settings = self.pdf_settings()
        set_fallback_mode(settings["pdf_fallback"])  # engines read the module global
        return {"ok": True, **settings}

    def set_model_key(self, api_key: str) -> dict[str, Any]:
        """Persist the model API key to the SecretStore (0600). The new provider client is
        built lazily on the next turn, so it picks the key up without a restart."""
        api_key = (api_key or "").strip()
        if not api_key:
            return {"ok": False, "error": "empty api key"}
        # Merge, don't replace: the profile may also hold a custom endpoint (base_url).
        profile = dict(self.secrets.get("provider:openai") or {})
        profile.update({"type": "api_key", "api_key": api_key})
        self.secrets.put("provider:openai", profile)
        self._refresh_provider("openai")  # rebuild the OpenAI client with the new key
        return {"ok": True, **self.get_settings()}

    def set_default_model(self, model: str) -> dict[str, Any]:
        """Set + persist the default model for new sessions (the UI pre-selects it)."""
        model = (model or "").strip()
        if not model:
            return {"ok": False, "error": "empty model"}
        self.model = model
        self._prefs["default_model"] = model
        self._save_prefs()
        return {"ok": True, **self.get_settings()}

    def set_onboarded(self, value: bool = True) -> dict[str, Any]:
        """Record that first-run setup is complete (so it isn't shown again)."""
        self._prefs["onboarded"] = bool(value)
        self._save_prefs()
        return {"ok": True, "onboarded": bool(value)}

    def set_scratch_base(self, path: str) -> dict[str, Any]:
        """Set + persist the common area where each Cowork conversation's scratch directory is
        created (default ~/OpenWorker). The raw value is stored so the UI shows it as entered;
        new conversations use it immediately (existing ones keep their provisioned dir).
        """
        path = (path or "").strip()
        if not path:
            return {"ok": False, "error": "empty path"}
        try:
            Path(path).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        self._prefs["scratch_base"] = path
        self._save_prefs()
        return {"ok": True, **self.get_settings()}

    # -- gateway + connector allow-list (inbound messaging) ---------------------
    def allow_user(
        self,
        name: str,
        user_id: str,
        team_id: Optional[str] = None,
        *,
        display_name: str = "",
    ) -> dict[str, Any]:
        out = self._set_allowed(name, user_id, team_id=team_id, add=True)
        # Directory picks arrive with the name in hand — record it so the chip
        # is readable immediately (message-driven allows learn it on arrival).
        if out.get("ok") and display_name:
            self._note_person(name, user_id, display_name)
        return out

    def disallow_user(
        self, name: str, user_id: str, team_id: Optional[str] = None
    ) -> dict[str, Any]:
        if name == "slack" and user_id in self.slack_approval_owner_ids(team_id):
            return {
                "ok": False,
                "error": "Remove this person as an approval owner first.",
            }
        return self._set_allowed(name, user_id, team_id=team_id, add=False)

    def slack_approval_owner_ids(self, team_id: Optional[str] = None) -> set[str]:
        """Stable Slack user ids allowed to resolve consequential Inbox prompts.

        Managed relay installs are installer-owned. Manual Socket Mode has no
        human OAuth identity, so its owners are selected explicitly.
        """
        key = f"slack:team:{team_id}" if team_id else "slack:default"
        profile = self.secrets.get(key) or {}
        if team_id:
            installer = str(profile.get("slack_user_id") or "").strip()
            return {installer} if installer else set()
        if profile.get("mode") == "relay":
            return set()
        return {
            str(user_id).strip()
            for user_id in (profile.get("approval_owner_ids") or [])
            if str(user_id).strip()
        }

    def set_slack_approval_owner(
        self, user_id: str, *, add: bool, display_name: str = ""
    ) -> dict[str, Any]:
        """Edit Manual Socket Mode approval owners.

        Owner status implies inbound permission. Relay ownership is derived from
        the OAuth installer and is intentionally not editable here.
        """
        user_id = str(user_id).strip()
        if not user_id:
            return {"ok": False, "error": "user_id required"}
        profile = self.secrets.get("slack:default")
        if not profile:
            return {"ok": False, "error": "Slack is not connected in Manual mode."}
        if profile.get("mode") == "relay" or profile.get("managed"):
            return {
                "ok": False,
                "error": "Relay approval ownership is set by the Slack installer.",
            }

        owners = self.slack_approval_owner_ids()
        if add:
            owners.add(user_id)
        else:
            owners.discard(user_id)
            if not owners and self._has_manual_slack_inbox_binding():
                return {
                    "ok": False,
                    "error": (
                        "Choose another approval owner before removing the last one "
                        "while Slack Inbox routing is active."
                    ),
                }
        profile["approval_owner_ids"] = sorted(owners)
        if add:
            allowed = set(profile.get("allowed_users") or [])
            allowed.add(user_id)
            profile["allowed_users"] = sorted(allowed)
        self.secrets.put("slack:default", profile)
        if display_name:
            self._note_person("slack", user_id, display_name)
        if self.gateway is not None and "slack" in self.gateway.settings:
            self.gateway.settings["slack"].allowed_users = set(
                profile.get("allowed_users") or []
            )
        return {
            "ok": True,
            "approval_owner_ids": sorted(owners),
            "allowed_users": list(profile.get("allowed_users") or []),
        }

    def _has_manual_slack_inbox_binding(self) -> bool:
        for raw in self.inbox_routing.bindings():
            if raw.get("channel") != "slack":
                continue
            team_id, _ = slack_split(str(raw.get("target") or ""))
            if team_id is None:
                return True
        return False

    def _slack_actor_owns_item(
        self,
        item,
        *,
        actor_id: str,
        chat_id: str,
        team_id: Optional[str],
    ) -> bool:
        """Authorize a Slack resolution against both its owner and delivery binding."""
        event_team, event_channel = slack_split(chat_id)
        event_team = team_id or event_team
        binding = self.inbox_routing.binding_for(item.inbox)
        owner_team = event_team
        if binding.channel == "slack":
            owner_team, bound_channel = slack_split(binding.target)
            if owner_team != event_team or bound_channel != event_channel:
                return False
        return bool(actor_id) and actor_id in self.slack_approval_owner_ids(owner_team)

    def set_inbox_binding(
        self, name: str, *, channel: Optional[str], target: str
    ) -> dict[str, Any]:
        """Persist an Inbox transport after validating its approval identity."""
        channel = str(channel or "").strip() or None
        target = str(target or "").strip()
        if channel and not target:
            return {"ok": False, "error": "Choose a destination channel."}
        if channel == "slack":
            settings = load_settings(self.secrets).get("slack")
            if settings is None or not settings.enabled:
                return {"ok": False, "error": "Slack is not connected."}
            team_id, destination = slack_split(target)
            if not destination:
                return {"ok": False, "error": "Choose a destination channel."}
            key = f"slack:team:{team_id}" if team_id else "slack:default"
            if not self.secrets.get(key):
                return {
                    "ok": False,
                    "error": "That Slack workspace is not connected.",
                }
            if not self.slack_approval_owner_ids(team_id):
                return {
                    "ok": False,
                    "error": (
                        "Choose at least one approval owner in Slack settings before "
                        "routing Inbox requests there."
                    ),
                }
        self.inbox_routing.set_binding(name, channel=channel, target=target)
        return {"ok": True, "bindings": self.inbox_routing.bindings()}

    def _set_allowed(
        self, name: str, user_id: str, *, team_id: Optional[str] = None, add: bool
    ) -> dict[str, Any]:
        """Add/remove a sender on the allow-list. With `team_id` the edit targets that
        scope's profile — a workspace's `slack:team:<id>`, or a GitHub App
        installation's `github:install:<id>` (the same per-tenant pattern);
        without, the flat `<name>:default` list (manual single-workspace mode)."""
        user_id = str(user_id).strip()
        if not user_id:
            return {"ok": False, "error": "user_id required"}
        scope = "install" if name == "github" else "team"
        profile_key = f"{name}:{scope}:{team_id}" if team_id else f"{name}:default"
        profile = self.secrets.get(profile_key)
        if not profile:
            return {
                "ok": False,
                "error": (
                    "workspace not connected" if team_id else "connector not connected"
                ),
            }
        allowed = set(profile.get("allowed_users") or [])
        allowed.add(user_id) if add else allowed.discard(user_id)
        profile["allowed_users"] = sorted(allowed)
        self.secrets.put(profile_key, profile)
        # reflect into the live gateway so it takes effect without a restart
        if self.gateway is not None and name in self.gateway.settings:
            if team_id:
                from ..connectors import TeamAuth

                teams = self.gateway.settings[name].teams
                team = teams.setdefault(team_id, TeamAuth())
                team.allowed_users = set(allowed)
            else:
                self.gateway.settings[name].allowed_users = set(allowed)
        return {"ok": True, "allowed_users": sorted(allowed), "team_id": team_id}

    async def disconnect_slack_workspace(self, team_id: str) -> dict[str, Any]:
        """Stop relaying ONE workspace: delete the cloud routing row (best-effort),
        drop the local per-team token, and hot-reload the gateway. Removing the last
        workspace also clears relay mode on slack:default so the connector reads
        disconnected (the manual Socket Mode fields, if any, are left untouched)."""
        team_id = str(team_id).strip()
        profile_key = f"slack:team:{team_id}"
        if not team_id or not self.secrets.get(profile_key):
            return {"ok": False, "error": "workspace not connected"}
        from .. import cloud
        from ..config import load_config

        await asyncio.to_thread(
            lambda: cloud.slack_disconnect_workspace(
                self.secrets, load_config(), team_id
            )
        )
        self.secrets.delete(profile_key)
        remaining = [
            m["profile"]
            for m in self.secrets.status()
            if m.get("profile", "").startswith("slack:team:")
        ]
        if not remaining:
            default = self.secrets.get("slack:default") or {}
            if default.get("mode") == "relay":
                default.pop("mode", None)
                default.pop("managed", None)
                if default.get("bot_token"):
                    # Manual Socket Mode creds predating the relay switch: keep them
                    # stored but DISABLED — removing the last workspace must never
                    # silently start listening with old tokens.
                    default["type"] = "token"
                    default["enabled"] = False
                    self.secrets.put("slack:default", default)
                else:
                    default.pop("type", None)
                    default.pop("enabled", None)
                    if default:  # e.g. a flat allow-list worth keeping
                        self.secrets.put("slack:default", default)
                    else:
                        self.secrets.delete("slack:default")
        await self.refresh_gateway()
        return {"ok": True, "remaining_workspaces": len(remaining)}

    def slack_status(self) -> dict[str, Any]:
        """Slack connection health in three honest layers (UX-DECISIONS §21):
        the desktop↔relay socket, the cloud sign-in that authorizes it, and each
        workspace's bot token. The desktop can't see the Slack↔cloud leg, so no
        layer here ever claims it — event silence ≠ outage."""
        from .. import cloud

        default = self.secrets.get("slack:default") or {}
        mode = default.get("mode") or ""
        signin = cloud.status(self.secrets)

        relay: dict[str, Any] = {
            "state": "offline",
            "reconnects": 0,
            "last_event_at": None,
            "last_error": "",
        }
        teams: dict[str, Any] = {}
        adapter = (
            self.gateway._adapters.get("slack") if self.gateway is not None else None
        )
        snapshot = getattr(
            adapter, "status", None
        )  # relay adapter only; Socket Mode has none
        if callable(snapshot):
            relay = snapshot()
            teams = relay.pop("teams", {})
        return {
            "ok": True,
            "mode": mode,
            "relay": relay,
            "signed_in": bool(signin.get("signed_in")),
            "teams": teams,
        }

    async def disconnect_github_installation(
        self, installation_id: str
    ) -> dict[str, Any]:
        """Stop relaying ONE GitHub installation: delete the cloud routing rows
        (best-effort), drop the local profile, hot-reload the gateway. The Slack
        per-workspace disconnect, GitHub flavour — a manual PAT stays untouched."""
        installation_id = str(installation_id).strip()
        from .. import cloud
        from ..config import load_config
        from ..connectors import github_installs

        if not installation_id or not self.secrets.get(
            github_installs.PREFIX + installation_id
        ):
            return {"ok": False, "error": "installation not connected"}
        await asyncio.to_thread(
            lambda: cloud.github_disconnect_installation(
                self.secrets, load_config(), installation_id
            )
        )
        result = github_installs.disconnect_install(self.secrets, installation_id)
        await self.refresh_gateway()
        return result

    def github_status(self) -> dict[str, Any]:
        """GitHub relay health, same three honest layers as Slack: the shared
        relay socket, the cloud sign-in, and per-installation token health."""
        from .. import cloud

        default = self.secrets.get("github:default") or {}
        signin = cloud.status(self.secrets)
        relay: dict[str, Any] = {
            "state": "offline",
            "reconnects": 0,
            "last_event_at": None,
            "last_error": "",
        }
        installs: dict[str, Any] = {}
        missed: dict[str, Any] = {}
        adapter = (
            self.gateway._adapters.get("github") if self.gateway is not None else None
        )
        snapshot = getattr(adapter, "status", None)
        if callable(snapshot):
            relay = snapshot()
            installs = relay.pop("installs", {})
            missed = relay.pop("missed", {})
        return {
            "ok": True,
            "mode": default.get("mode") or "",
            "relay": relay,
            "signed_in": bool(signin.get("signed_in")),
            "installs": installs,
            "missed": missed,
        }

    async def start_gateway(self) -> list[str]:
        """Build the messaging gateway and start enabled listeners. Inbound messages route to
        durable sessions: a channel message to its subscribers, a DM to the designated DM session
        (else parked). Returns the platforms whose listeners came up."""
        # Team steering/kicks are dispatched from tool threads; they need the app loop.
        self._loop = asyncio.get_running_loop()
        self.scheduler.start()  # tick scheduler for automations (independent of connectors)
        return await self._build_and_start_gateway()

    async def refresh_gateway(self) -> list[str]:
        """Hot-reload the messaging listeners with fresh secrets — called after a connector
        connect/disconnect so pasting new tokens takes effect immediately. A platform socket
        (Slack Socket Mode) authenticates at connect time, so new creds mean reopening that
        socket; this replaces the adapters in-process — the sidecar never restarts."""
        await self.stop_gateway()
        started = await self._build_and_start_gateway()
        print(f"[coworker] messaging gateway reloaded: {', '.join(started) or 'none'}")
        return started

    async def _build_and_start_gateway(self) -> list[str]:
        settings = load_settings(self.secrets)
        self.gateway = Gateway(
            secrets=self.secrets,
            settings=settings,
            handler=self._dispatch_inbound,
            reply_resolver=self._resolve_inbox_reply,
            interaction_handler=self._on_interaction,
            on_unauthorized=self._park_unauthorized,
        )
        # Managed Slack relay wiring (only used when a connector picks relay mode):
        # the cloud sign-in JWT authorizes the relay WebSocket, and the relay
        # endpoint comes from config. Both are lazy — Socket Mode needs neither.
        from ..cloud import fresh_access_token
        from ..config import load_config

        cloud_config = load_config()

        def _relay_token() -> str:
            return fresh_access_token(self.secrets, cloud_config) or ""

        # Every relay-mode platform shares ONE cloud socket; the hub fans frames
        # out by provider tag. Built lazily on the first relay adapter.
        relay_ws_url = getattr(cloud_config, "cloud_relay_ws_url", "") or None
        relay_hub = None
        if relay_ws_url:
            from ..connectors.relay_client import RelayHub

            relay_hub = RelayHub(relay_ws_url, _relay_token)

        async def _github_token(installation_id: str) -> str:
            from ..cloud import github_installation_token

            return await asyncio.to_thread(
                github_installation_token, self.secrets, cloud_config, installation_id
            )

        for platform, st in settings.items():
            if not st.enabled:
                continue
            profile = self.secrets.get(f"{platform}:default") or {}
            adapter = make_adapter(
                platform,
                profile,
                secrets=self.secrets,
                token_provider=_relay_token,
                relay_url=relay_ws_url,
                relay_hub=relay_hub,
                github_token_client=_github_token,
            )
            if adapter is not None:
                self.gateway.register(adapter)
        return await self.gateway.start()

    async def stop_gateway(self) -> None:
        if self.gateway is not None:
            await self.gateway.stop()
            self.gateway = None

    # -- unauthorized inbound (parked, §19) --------------------------------------
    def _note_person(
        self, platform: str, user_id: Optional[str], name: Optional[str]
    ) -> None:
        """Remember a sender's display name (persisted) so ID-keyed surfaces — the allow-list
        chips above all — can show who a U07JK… actually is. Best-effort, newest name wins.
        """
        if not user_id or not name:
            return
        key = f"{platform}:{user_id}"
        if self._people.get(key) != name:
            self._people[key] = name
            try:
                self._people_path.write_text(json.dumps(self._people))
            except OSError:
                pass

    async def _park_unauthorized(self, event) -> None:
        """Gateway callback: keep what an unallowed sender said (names already resolved by the
        adapter, best-effort) so the owner can allow-and-deliver without a re-send."""
        s = event.source
        self._note_person(s.platform, s.user_id, s.user_name)
        self.parked.park(
            platform=s.platform,
            chat_id=s.chat_id,
            chat_name=s.chat_name,
            user_id=s.user_id or "?",
            user_name=s.user_name,
            chat_type=s.chat_type,
            thread_id=s.thread_id,
            team_id=s.team_id,
            text=event.text or "",
        )

    async def resolve_unauthorized(
        self, name: str, item_id: str, action: str
    ) -> dict[str, Any]:
        """Resolve one parked message: "dismiss" throws it away; "allow" adds the sender to the
        allow-list (future messages flow); "allow_deliver" also re-injects the parked message
        through the NORMAL inbound path — buffer + subscriptions — as if it just arrived.
        """
        item = self.parked.pop(item_id)
        if item is None or item.platform != name:
            return {"ok": False, "error": "unknown item"}
        if action == "dismiss":
            return {"ok": True}
        if action not in ("allow", "allow_deliver"):
            return {"ok": False, "error": f"unknown action: {action}"}
        allowed = self._set_allowed(name, item.user_id, team_id=item.team_id, add=True)
        if not allowed.get("ok"):
            return allowed
        if action == "allow_deliver":
            from ..connectors import MessageEvent, SessionSource

            event = MessageEvent(
                text=item.text,
                source=SessionSource(
                    platform=item.platform,
                    chat_id=item.chat_id,
                    user_id=item.user_id,
                    user_name=item.user_name,
                    chat_name=item.chat_name,
                    chat_type=item.chat_type,
                    thread_id=item.thread_id,
                    team_id=item.team_id,
                ),
            )
            await self._dispatch_inbound(event)
        return {"ok": True}

    # -- per-session live view --------------------------------------------------
    def register_event_client(self, send_cb: Any) -> None:
        self._event_clients.add(send_cb)

    def unregister_event_client(self, send_cb: Any) -> None:
        self._event_clients.discard(send_cb)

    async def broadcast_event(self, message: dict) -> None:
        """Fan an app-wide event out to every /ws/events socket. Best-effort: a dead
        socket is dropped, never fatal to the caller."""
        for cb in list(self._event_clients):
            try:
                await cb(message)
            except Exception:
                self.unregister_event_client(cb)

    def register_session_client(self, session_id: str, send_cb: Any) -> None:
        self._session_clients.setdefault(session_id, set()).add(send_cb)

    def unregister_session_client(self, session_id: str, send_cb: Any) -> None:
        clients = self._session_clients.get(session_id)
        if clients is not None:
            clients.discard(send_cb)
            if not clients:
                self._session_clients.pop(session_id, None)

    async def broadcast_session(self, session_id: str, message: dict) -> None:
        """Fan a turn event out to every socket viewing this session. Best-effort: a dead socket
        is dropped, never fatal to the turn (delivery is socket-independent)."""
        for cb in list(self._session_clients.get(session_id, ())):
            try:
                await cb(message)
            except Exception:
                self.unregister_session_client(session_id, cb)

    async def aclose(self) -> None:
        await self.scheduler.stop()
        await self.stop_gateway()
        await self.mcp.aclose()
        self.audit_store.close()

    # -- automation (scheduled tasks) -------------------------------------------
    def approval_prompt_data(self, session_id: str, request) -> dict[str, Any]:
        """Extra Inbox-item payload for a parked approval. Always carries the tool name +
        arguments so the GUI can render the same humanized card (§35) it shows live —
        without them a reopened session fell back to the raw 'Run `tool`?' treatment.
        Automation runs additionally carry the owning task + (when the call is eligible)
        the exact target a standing rule would pin: the GUI offers "Allow every time" only
        when both are present — in-app only, never on Slack-mirrored buttons (§25)."""
        from ..permissions import standing_rule_candidate

        data: dict[str, Any] = {
            "tool": request.tool_name,
            "arguments": getattr(request, "arguments", None) or {},
        }
        task = self.task_store.task_for_run_session(session_id)
        if task is None:
            return data
        data.update({"task_id": task.id, "task_title": task.title})
        target = standing_rule_candidate(
            request.tool_name,
            getattr(request, "arguments", None) or {},
            getattr(request, "metadata", None),
        )
        if target:
            data["standing_target"] = target
        return data

    def mint_task_rule(
        self, session_id: str, tool_name: str, arguments: Any, metadata: Any = None
    ) -> bool:
        """Persist a standing rule a human minted via "Allow every time" on a run's
        approval card (§25's retrofit path). Server-side validation, not trust in the
        card: the session must be an automation run and the call must be rule-eligible
        (external risk, declared target argument, non-empty target). Also applies the
        rule to the live engine so the run's next call auto-allows."""
        from ..permissions import standing_rule_candidate

        task = self.task_store.task_for_run_session(session_id)
        if task is None:
            return False
        target = standing_rule_candidate(tool_name, arguments or {}, metadata)
        if not target or not task.add_rule(tool_name, target):
            return False
        self.task_store.save(task)
        engine = self._engines.get(session_id)
        if engine is not None:
            engine.permissions.task_rules.setdefault(tool_name, set()).add(target)
        try:
            self.audit_store.append(
                {
                    "session_id": session_id,
                    "tool": tool_name,
                    "arguments": arguments or {},
                    "stage": "standing_rule_minted",
                    "status": "granted",
                    "reason": f"allow every time: {tool_name} → {target} (task {task.id})",
                }
            )
        except Exception:
            pass
        return True

    def approval_outcome(self, resolution: str, request, session_id: str):
        """Map an approval resolution (from any surface) to an ApprovalOutcome, handling
        the task-persistent "always_task" vocabulary alongside the session-scoped ones.

        Server-side validated, not trusted from the caller: a grant that no UI offers for
        this tool is downgraded to a one-time approval rather than honoured. The GUI already
        hides the broad "always allow" for run_shell / connectors / save_skill, and Slack
        mirrors only ever render approve/deny — but `POST /v1/inbox/{id}/resolve` takes a raw
        string, so without this check any local API caller could mint a session-wide
        any-argument shell grant. Same philosophy as mint_task_rule: validate here, don't
        trust the card.
        """
        from ..engine import ApprovalOutcome

        if resolution == "always_task":
            minted = self.mint_task_rule(
                session_id,
                request.tool_name,
                getattr(request, "arguments", None),
                getattr(request, "metadata", None),
            )
            if not minted:
                self._audit_grant_refused(session_id, request, resolution)
            return ApprovalOutcome.ONCE
        try:
            outcome = ApprovalOutcome(resolution)
        except ValueError:
            if resolution == "allow":
                return ApprovalOutcome.ONCE
            if resolution == "always":
                outcome = ApprovalOutcome.ALWAYS_TOOL
            else:
                return ApprovalOutcome.DENY
        if outcome in (
            ApprovalOutcome.ALWAYS_TOOL,
            ApprovalOutcome.ALWAYS_COMMAND,
            ApprovalOutcome.ALWAYS_DOMAIN,
        ) and not _grant_offered(outcome, request):
            self._audit_grant_refused(session_id, request, resolution)
            return ApprovalOutcome.ONCE
        return outcome

    def audit_autonomy_change(
        self, session_id: str, kind: str, before: Any, after: Any
    ) -> None:
        """Record a change to how much the agent may do unsupervised — the permission mode,
        or the attended/unattended toggle. Without this, "who turned on auto mode, and when"
        is unanswerable from the audit store, which is at odds with the per-call trail the
        rest of the engine keeps. Raising autonomy is flagged so it can be filtered."""
        # AUTO_APPROVE sits above interactive (turning the reviewer on means fewer human
        # checks — that IS raising autonomy) and below bypass, which removes checks
        # entirely. "auto" is the legacy spelling of "bypass-approvals".
        order = {
            "discuss": 0,
            "plan": 1,
            "interactive": 2,
            "custom": 2,
            "auto-approve": 3,
            "auto": 4,
            "bypass-approvals": 4,
        }
        raised = (
            order.get(str(after), 0) > order.get(str(before), 0)
            if kind == "mode"
            else bool(after) and not bool(before)
        )
        try:
            self.audit_store.append(
                {
                    "session_id": session_id,
                    "tool": "",
                    "arguments": {},
                    "stage": f"{kind}_changed",
                    "status": "raised" if raised else "lowered",
                    "reason": f"{kind}: {before} → {after}",
                }
            )
        except Exception:
            pass

    def set_unattended(self, session_id: str, on: bool) -> dict[str, Any]:
        """Flip the attended/unattended toggle, with an audit row. Note this changes only
        WHERE the human is reached, never the autonomy ceiling (that's the mode) — but it is
        still worth recording, since an unattended session routes prompts away from the
        screen the user is looking at."""
        before = self.unattended.is_unattended(session_id)
        self.unattended.set(session_id, on)
        if before != on:
            self.audit_autonomy_change(session_id, "unattended", before, on)
        return {"ok": True, "session_id": session_id, "unattended": on}

    def _audit_grant_refused(self, session_id: str, request, resolution: str) -> None:
        try:
            self.audit_store.append(
                {
                    "session_id": session_id,
                    "tool": getattr(request, "tool_name", ""),
                    "arguments": getattr(request, "arguments", None) or {},
                    "stage": "grant_refused",
                    "status": "downgraded",
                    "reason": (
                        f"resolution {resolution!r} is not offered for this tool — "
                        "applied as a one-time approval"
                    ),
                }
            )
        except Exception:
            pass

    def _scheduled_approver(self, task, session_id: str):
        from ..engine import ApprovalOutcome
        from ..permissions import WRITE_TOOLS

        name_allowed = task.name_allowed_tools()

        async def approver(request):
            # Unattended: auto-allow the deliverable writes (path-scoped to the task
            # workspace) + tools the task allows BY NAME (legacy entries). Target-bound
            # rules never reach here — the permission engine matched them already.
            if request.tool_name in WRITE_TOOLS or request.tool_name in name_allowed:
                return ApprovalOutcome.ONCE
            # Anything else parks in the Inbox and suspends the run (§25 graceful
            # degradation — an ungranted automation still works, it just asks). The item
            # carries the task binding so the in-app card can offer "Allow every time";
            # the Slack mirror renders only Approve/Deny buttons.
            item = self.inbox.add_approval(
                session_id,
                f"Run `{request.tool_name}`?",
                body=_approval_body(request),
                inbox=self.inbox_routing.route_for(session_id, task.agent),
                tool_call_id=getattr(request, "tool_call_id", None),
                data=self.approval_prompt_data(session_id, request),
            )
            if item.state == "pending":
                self.persist_session(session_id)
                await self.mirror_inbox_item(item)
            resolution = await self.inbox.wait(item.id)
            return self.approval_outcome(resolution, request, session_id)

        return approver

    def _seed_task_permissions(self, engine: TurnEngine, task) -> None:
        """Apply a task's standing allowances to an engine: target-bound rules feed the
        permission engine's matcher (connector tools included — the target binding is the
        safety); name-only legacy entries keep their session-allowlist behavior."""
        engine.permissions.task_rules = task.standing_rules()
        for tool in task.name_allowed_tools():
            engine.permissions.allow_tool_for_session(tool)

    def _build_task_engine(self, task, *, session_id: str) -> TurnEngine:
        ag = get_agent(task.agent)
        Path(task.workspace).mkdir(parents=True, exist_ok=True)
        engine = build_engine(
            agent=ag,
            workspace=task.workspace,
            model=task.model or self.model,
            mode=Mode.INTERACTIVE,
            approver=self._scheduled_approver(task, session_id),
            provider=self.provider,
            memory_store=self.memory_store,
            memory_workspace=self._memory_key_for(None, task.workspace),
            memory_off=not self.memory_settings.enabled,
            memory_saving_enabled=lambda: self.memory_settings.enabled,
            # Callable, not a snapshot: editing your instructions in Settings applies
            # to conversations already open (same reason as the saving switch).
            user_rules=lambda: self.memory_settings.user_rules,
            on_memory_saved=self._memory_saved_notifier(session_id),
            secrets=self.secrets,
            # No scheduling tools inside a scheduled run: the executing agent's job is to DO the
            # task, and instructions that mention timing ("every day at 5:32pm…") otherwise tempt
            # it to create another automation instead of running this one.
            task_store=None,
            session_id=session_id,
            audit_sink=self.audit_store.append,
            # Scheduled runs respect the same per-session connection hierarchy as live sessions:
            # expose only the persona's effective-enabled connectors' tools (§4.3).
            connector_filter=self.effective_connectors(session_id, task.agent),
            skill_filter=lambda sid=session_id, w=task.workspace, a=task.agent: (
                self.effective_skill_names(sid, w, agent=a)
            ),
            extra_skill_dirs=(
                [d] if (d := self.persona_skill_scope(task.agent)[0]) is not None else None
            ),
        )
        self._seed_task_permissions(engine, task)
        return engine

    # -- mirroring inbox items to a bound channel -------------------------------
    async def mirror_inbox_item(self, item) -> None:
        """Mirror an Inbox item to its bound channel. Discrete choices (approve/deny, ask_user
        options) render as BUTTONS — the item id rides in each, so a click resolves it
        unambiguously. Free-text answers aren't offered over messaging (open the app).
        """
        from ..interactions import buttons_for

        binding = self.inbox_routing.binding_for(item.inbox)
        if not (binding.channel and self.gateway is not None):
            return
        if binding.channel == "slack":
            team_id, _ = slack_split(binding.target)
            # Legacy bindings may predate approval ownership. Keep the item
            # available in-app, but never mirror it to an ownerless channel.
            if not self.slack_approval_owner_ids(team_id):
                return
        target = f"{binding.channel}:{binding.target}"
        body = "\n".join(p for p in (item.title, item.body) if p).strip()
        buttons = buttons_for(item)
        try:
            if buttons:
                await self.gateway.deliver_interactive(target, body, buttons)
            else:
                await self.gateway.deliver(
                    target,
                    f"{body}\n(Open the app to respond.)\n[ow:{item.id}]".strip(),
                )
        except Exception:
            pass

    # -- interactive prompt buttons (Slack/Telegram) ----------------------------
    async def _on_interaction(self, event) -> None:
        """A button click on a mirrored Inbox prompt. The button value carries the item id + the
        resolution, so this is unambiguous — resolve the item, then swap the buttons for the
        outcome. Resolving releases any agent suspended on it (first-responder-wins)."""
        from ..interactions import decode

        decoded = decode(getattr(event, "value", "") or "")
        if decoded is None:
            return
        item_id, resolution = decoded
        item = self.inbox.get(item_id)
        if item is None:
            return
        protected_kinds = {"approval", "directory", "plan"}
        if (
            getattr(event, "platform", "") == "slack"
            and item.kind in protected_kinds
        ):
            actor_id = str(getattr(event, "user_id", "") or "")
            if not self._slack_actor_owns_item(
                item,
                actor_id=actor_id,
                chat_id=getattr(event, "chat_id", "") or "",
                team_id=getattr(event, "team_id", None),
            ):
                if self.gateway is not None:
                    await self.gateway.reject_interaction(event)
                return
        already = item is not None and item.state != "pending"
        resolved = await self.resolve_inbox(item_id, resolution)
        if not resolved and not already:
            return
        who = getattr(event, "user_name", None) or "someone"
        title = item.title
        outcome = "already resolved" if already else f"“{resolution}” — by {who}"
        if self.gateway is not None and getattr(event, "message_id", None):
            try:
                await self.gateway.update_message(
                    getattr(event, "platform", "slack"),
                    getattr(event, "chat_id", ""),
                    event.message_id,
                    f"{title}\n✅ {outcome}",
                )
            except Exception:
                pass

    # -- inbox replies over messaging connectors --------------------------------
    def _resolve_inbox_reply(self, event) -> bool:
        """Try to handle an inbound Slack/Telegram message as an Inbox reply. Returns True if the
        message carried an `[ow:<id>]` token (so it's consumed here, not routed as a new turn) —
        resolving the item also releases any agent suspended on it."""
        from ..inbox_routing import resolve_from_reply

        text = getattr(event, "text", "") or ""

        def _resolve(item_id: str, resolution: str) -> bool:
            item = self.inbox.get(item_id)
            if item is None:
                return False
            if (
                getattr(event.source, "platform", "") == "slack"
                and item.kind in {"approval", "directory", "plan"}
            ):
                actor_id = str(getattr(event.source, "user_id", "") or "")
                if not self._slack_actor_owns_item(
                    item,
                    actor_id=actor_id,
                    chat_id=getattr(event.source, "chat_id", "") or "",
                    team_id=getattr(event.source, "team_id", None),
                ):
                    return False
            return self.inbox.resolve(item_id, resolution)

        return resolve_from_reply(text, _resolve) is not None

    # -- self-wake resumption ---------------------------------------------------
    async def _scheduler_tick(self) -> None:
        """The shared per-tick work: resume due self-wakes, then drain team queues.
        Team deliveries dispatch as tasks (a long worker turn must not stall the
        scheduler)."""
        await self.resume_due_wakes()
        try:
            await self.team_tick()
        except Exception:
            logger.exception("team tick failed")

    async def resume_due_wakes(self) -> int:
        """Resume sessions whose self-wakes are due (called each scheduler tick). A suspended
        agent (it called sleep_until / wake_on / wake_on_event and ended its turn) is re-invoked on
        its own session with a wake message so it continues where it left off. Returns the count.
        """
        resumed = 0
        for wake in self.wakes.due():
            try:
                await self._resume_wake(wake)
                resumed += 1
            except Exception:
                pass
            finally:
                self.wakes.mark_fired(wake.id)
        return resumed

    def mark_running(self, session_id: str) -> None:
        self._running_sessions.add(session_id)

    def try_mark_running(self, session_id: str) -> bool:
        """Atomically claim an idle session for one turn on the server event loop."""
        if session_id in self._running_sessions:
            return False
        self._running_sessions.add(session_id)
        return True

    def mark_idle(self, session_id: str) -> None:
        self._running_sessions.discard(session_id)
        # Every turn path (WS, background delivery, durable resume) marks idle when it
        # finishes — the one shared post-turn moment, so auto-titling hooks in here and
        # can never add latency to the response itself.
        self._maybe_autotitle(session_id)
        # Team sessions: a finished turn is the moment new board events exist (an
        # assign, a review transition) — kick the queue drain now instead of waiting
        # for the next scheduler tick. Cheap no-op for teamless sessions.
        if self.teams.for_lead_session(session_id):
            self._team_last_alive[session_id] = time.time()
        if self._loop is not None and (
            self.teams.for_lead_session(session_id)
            or self.teams.for_worker_session(session_id)
        ):
            asyncio.run_coroutine_threadsafe(self.team_tick(), self._loop)
        if session_id in self._promotion_rebuild:
            # Promotion happened this turn: drop the cached engine so the next turn
            # rebuilds with the new primary (relative anchoring, env snapshot, git).
            self._promotion_rebuild.discard(session_id)
            self._engines.pop(session_id, None)

    def is_running(self, session_id: str) -> bool:
        return session_id in self._running_sessions

    async def _resume_wake(self, wake) -> None:
        message = self._wake_message(wake)
        # A lead's timer wake carries the staleness digest — pure code over the
        # board, scoped by role membership (teamless sessions get a bare wake).
        digest = self.team_staleness_digest(wake.session_id)
        if digest:
            message = f"{message}\n\n{digest}"
        await self.deliver_to_session(wake.session_id, message)

    async def deliver_to_session(
        self, session_id: str, message: str, *, source: Optional[dict[str, Any]] = None
    ) -> None:
        """Deliver an out-of-band message to a (durable) session — the agent stays resumable
        forever, so this works with no live socket. Busy (mid tool-loop): steer it into the live
        turn at its next step (don't start a colliding run). Idle: run a fresh background turn
        (results persist; if the session is Unattended, any approvals route to the Inbox). Shared
        by self-wake and channel-subscription delivery. `source` is the display-only MessageSource
        sidecar for connector messages (framed `message` stays the model-facing text).
        """
        engine = self.get_engine(session_id)
        if engine is None:
            return
        if not self.try_mark_running(session_id):
            engine.queue_steering(message, source)
            return
        try:
            async for event in engine.run(message, source=source):
                # Stream every event to any socket viewing this session, so a background turn
                # (channel delivery, self-wake, durable resume) is seen live — not just on reselect.
                await self.broadcast_session(
                    session_id, {"type": event.type.value, "data": event.data}
                )
                # A background turn has no user watching to read an inline error: a dead model or
                # tool failure would otherwise vanish. Log it and park it in the dead-letter store.
                if event.type.value == "error":
                    reason = (event.data or {}).get("error", "unknown error")
                    logger.warning(
                        "background turn failed for %s: %s", session_id, reason
                    )
                    self.unrouted.record(session_id, "-", message, reason=reason)
            self.save(session_id, engine)
        except (
            Exception
        ) as exc:  # an unexpected raise out of the turn must not be swallowed
            logger.warning("background turn crashed for %s: %s", session_id, exc)
            self.unrouted.record(session_id, "-", message, reason=str(exc))
            await self.broadcast_session(
                session_id, {"type": "error", "data": {"error": str(exc)}}
            )
        finally:
            self.mark_idle(session_id)
            await self.broadcast_session(session_id, {"type": "turn_done", "data": {}})

    # -- channel subscriptions (inbound messaging) ------------------------------
    async def _dispatch_inbound(self, event) -> None:
        """Route a non-token inbound message. Channel messages are buffered (for catch-up) and
        fanned out to every subscribed session; a DM (or any non-channel) goes to the user-designated
        DM session (delivered like any background turn) or, if none is set, is parked as unrouted.
        """
        src = event.source
        text = getattr(event, "text", "") or ""
        who = src.user_name or src.user_id or "?"
        channel = f"{src.platform}:{src.chat_id}"  # thread-agnostic channel address
        self._note_person(src.platform, src.user_id, src.user_name)
        # Structured sidecar (display-only) built from the resolved identities on the event — the
        # framed text below stays the model-facing `content`; `ms.text` carries the RAW message.
        ms = MessageSource(
            connector=src.platform,
            kind="channel" if src.chat_type in ("channel", "group") else "dm",
            channel_id=src.chat_id,
            channel_name=src.chat_name or src.chat_id,
            sender_id=src.user_id or "",
            sender_name=src.user_name or src.user_id or "?",
            ts=_inbound_epoch(getattr(event, "message_id", None)),
            text=text,
        )
        if src.chat_type in ("channel", "group"):
            self.channel_buffer.record(
                channel, who, text, name=src.chat_name
            )  # buffer all, even unsubscribed
            subs = self.subscriptions.for_channel(channel)
            # §31 mention router: a direct @-mention of the bot outranks the passive fan-out —
            # subscribed sessions must answer it; an unsubscribed channel spawns (or steers)
            # the per-thread coworker session.
            if getattr(event, "mentions_me", False):
                await self._route_mention(event, ms, subs)
                return
            if subs:
                # Chattiness tiers (§31): untagged channel traffic is judgement-only —
                # silence is the default; the must-respond framing is the mention path's.
                msg = (
                    f"💬 New message on {src.chat_name or channel} from {who}: {text}\n"
                    f"(You're subscribed to this channel but were NOT mentioned. Use your "
                    f"judgement: stay silent unless the message clearly concerns your job and "
                    f"a reply adds real value — most channel chatter needs no response from "
                    f'you. If you do reply, use the send_message tool with target "{channel}".)'
                )
                for sub in subs:
                    # Per-session connection hierarchy (§4.3): a session that has muted this
                    # connector skips delivery — the message is still buffered (above) for catch-up.
                    if not self._inbound_connector_allowed(
                        sub.session_id, src.platform
                    ):
                        continue
                    try:
                        await self.deliver_to_session(
                            sub.session_id, msg, source=ms.to_dict()
                        )
                    except Exception:
                        pass
                return
            return  # channel with no subscribers — nobody is listening
        # DM (or any non-channel): route to the designated session, else park it for visibility.
        dm = self.dm_session()
        if dm and self._inbound_connector_allowed(dm, src.platform):
            await self.deliver_to_session(dm, event.tagged_text(), source=ms.to_dict())
        elif dm:
            # Designated, but this session has muted the connector → park rather than deliver.
            self.unrouted.record(
                src.target, who, text, reason="connector muted for DM session"
            )
        else:
            self.unrouted.record(
                src.target, who, text, reason="no DM session designated"
            )

    # -- mention router (§31) ----------------------------------------------------
    async def _route_mention(self, event, ms: MessageSource, subs) -> None:
        """@OpenWorker tagged in a channel. A subscribed (user-connected) coworker owns the channel
        and must answer; otherwise the per-thread coworker session handles it — spawned on the
        first tag, steered by follow-ups (deduped on the thread target)."""
        from ..connectors.base import format_target

        src = event.source
        # Slack semantics: replying to a top-level message threads on THAT message's ts, so a
        # top-level tag (no thread_ts) keys — and is answered — on its own ts.
        thread_key = src.thread_id or getattr(event, "message_id", None)
        thread_target = format_target(src.platform, src.chat_id, thread_key)
        who = src.user_name or src.user_id or "?"
        chan = f"#{src.chat_name}" if src.chat_name else src.chat_id
        if subs:
            # The user connected a coworker to this channel — it answers tags; no spawn.
            msg = (
                f"🔔 You were tagged by {who} in {chan}: {event.text}\n"
                f"(You are subscribed to this channel and were mentioned directly — you must "
                f"respond. Reply in the thread with the send_message tool, target "
                f'"{thread_target}".)'
            )
            for sub in subs:
                if not self._inbound_connector_allowed(sub.session_id, src.platform):
                    continue
                try:
                    await self.deliver_to_session(
                        sub.session_id, msg, source=ms.to_dict()
                    )
                except Exception:
                    pass
            return
        sid = self.mention_sessions.get(thread_target)
        if sid and self.session_store.load(sid) is not None:
            # Follow-up tag in a thread we already own → steer the same session.
            msg = (
                f"💬 Follow-up in your Slack thread ({chan}) from {who}: {event.text}\n"
                f'(Reply in the thread with the send_message tool, target "{thread_target}" '
                f"— replies there are pre-approved.)"
            )
            await self.deliver_to_session(sid, msg, source=ms.to_dict())
            return
        await self._spawn_mention_session(event, ms, thread_target)

    async def _spawn_mention_session(
        self, event, ms: MessageSource, thread_target: str
    ) -> None:
        """First tag in a thread: a NEW visible coworker session that owns the thread. Its
        in-thread replies carry a standing grant (§25 shape, exact-target match) so the
        conversation never stalls on an approval nobody in Slack can see; everything else
        asks as usual (approvals park to the Inbox)."""
        import uuid

        src = event.source
        who = src.user_name or src.user_id or "?"
        chan = f"#{src.chat_name}" if src.chat_name else src.chat_id
        sid = uuid.uuid4().hex
        engine = self.get_engine(sid, agent=self.personas.default_id())
        if engine is None:
            self.unrouted.record(
                src.target, who, event.text, reason="could not spawn mention session"
            )
            return
        # Durable mapping FIRST (a fast follow-up tag mid-turn dedupes into steering),
        # then the live grant; get_engine re-derives it from the store on any rebuild.
        self.mention_sessions.set(
            thread_target, sid, channel=f"{src.platform}:{src.chat_id}"
        )
        engine.permissions.task_rules.setdefault("send_message", set()).add(
            thread_target
        )
        self.save(sid, engine)  # the sessions row must exist before rename/set_origin
        # Title = the ASK first, channel last (owner call 2026-07-14): the text is what
        # varies between sessions, so it gets the truncation budget; the mention token is
        # noise (origin is already told by the From Slack group + icon + origin_label).
        ask = re.sub(r"<@[^>]+>", "", event.text or "")
        ask = " ".join(ask.split())[:48]
        self.session_store.rename(sid, f"{ask} — {chan}" if ask else chan)
        label = chan + (f" · {src.team_id}" if src.team_id else "")
        self.session_store.set_origin(sid, src.platform, label)
        # Up to 6 lines of channel context, minus the tag itself (it's the opening line).
        recent = self.channel_buffer.recent(f"{src.platform}:{src.chat_id}", 7)[:-1]
        context = "\n".join(f"- {m['from']}: {m['text']}" for m in recent)
        opening = (
            f"🔔 You were mentioned on Slack in {chan} by {who}: {event.text}\n\n"
            f"You own this Slack thread. Reply in the thread using the send_message tool "
            f'with target "{thread_target}" — replies to this thread are pre-approved and '
            f"never prompt the user. Anything else (other channels, files, external "
            f"actions) asks for approval as usual. Keep replies concise and "
            f"Slack-appropriate."
            + (f"\n\nRecent channel context:\n{context}" if context else "")
        )
        try:
            await self.deliver_to_session(sid, opening, source=ms.to_dict())
        except Exception:
            logger.exception("mention session %s opening turn failed", sid)

    @staticmethod
    def _wake_message(wake) -> str:
        note = f" (note: {wake.note})" if getattr(wake, "note", "") else ""
        if wake.kind == "completion":
            return (
                f"⏰ Wake — the job `{wake.job_id}` you were waiting on has completed{note}. "
                "Continue where you left off."
            )
        if wake.kind == "event":
            return (
                f"⏰ Wake — the event `{wake.event_key}` you were waiting on has fired{note}. "
                "Continue where you left off."
            )
        return (
            f"⏰ Wake — the timer you set has fired{note}. Continue where you left off."
        )

    async def _run_scheduled_task(self, task, trigger: str) -> TaskRun:
        run = TaskRun(
            task_id=task.id, trigger=trigger
        )  # __post_init__ sets run.session_id
        self.task_store.add_run(run)  # mark "running"
        # UX-026: tell every open app window a SCHEDULED run just started (the 5s
        # top-right toast). Manual runs never come through here — the user is
        # already watching those live.
        await self.broadcast_event(
            {
                "type": "automation_run_started",
                "data": {
                    "task_id": task.id,
                    "task_title": task.title,
                    "session_id": run.session_id,
                    "workspace": task.workspace,
                    "agent": task.agent,
                    "trigger": trigger,
                },
            }
        )
        # Each run is a real, persisted conversation thread: it runs the instructions under its
        # own session id, then saves the transcript. The user can reopen that session and ask a
        # follow-up — the scheduled agent is no longer fire-and-forget.
        engine = self._build_task_engine(task, session_id=run.session_id)
        # Register the live engine up-front: a parked approval persists the session
        # mid-run (durable suspend), and resolving from the Inbox must find this engine.
        self._engines[run.session_id] = engine
        # The first turn is the task itself. The framing matters: instructions often restate the
        # schedule ("every day at 5:32pm…"), so make explicit that the schedule already fired and
        # the job now is to execute, not to (re)schedule.
        opening = (
            f"⏰ Scheduled run — {task.title}\n\n"
            "This automation is due now: carry out the task below immediately and produce the "
            "result. The schedule already exists — do not create or modify any scheduled tasks.\n\n"
            f"{task.instructions}"
        )
        try:
            async for _event in engine.run(opening):
                pass
            run.result_text = _last_assistant_text(engine.messages)
            run.artifacts = _recent_files(task.workspace, since=run.started_at)
            run.status = "ok"
            if task.notify_on_completion:
                await self._notify_task_done(task, run)
        except Exception as exc:
            run.status, run.error = "error", str(exc)
        finally:
            run.finished_at = _epoch()
            # Persist the run as a continuable session + keep the live engine for an immediate
            # follow-up; record the run (now carrying its session_id).
            try:
                self.save(run.session_id, engine)
                self._engines[run.session_id] = engine
            except Exception:
                pass
            self.task_store.add_run(run)
        return run

    async def _notify_task_done(self, task, run: TaskRun) -> None:
        summary = (run.result_text or "").strip()[:280]
        # Notify any socket viewing this scheduled run's session (it's a durable session of its own).
        await self.broadcast_session(
            run.session_id,
            {
                "type": "task_done",
                "data": {
                    "task": task.title,
                    "id": task.id,
                    "text": summary,
                    "run_id": run.run_id,
                },
            },
        )
        if task.notify_target:
            from ..connectors.base import parse_target
            from ..connectors.senders import DEFAULT_SENDERS

            try:
                platform, chat_id, thread = parse_target(task.notify_target)
                sender = DEFAULT_SENDERS.get(platform)
                creds = self.secrets.get(f"{platform}:default") or {}
                if sender and creds.get("bot_token"):
                    await asyncio.to_thread(
                        sender,
                        creds["bot_token"],
                        chat_id,
                        f"✓ {task.title}\n\n{summary}",
                        thread,
                    )
            except Exception:
                pass

    # -- automation REST --------------------------------------------------------
    def list_automations(self) -> dict[str, Any]:
        # Unseen = runs started after the task's seen mark (UX-023 sidebar badges).
        # `unseen_failed` tints the badge when the NEWEST unseen run errored.
        tasks = []
        for t in self.task_store.list():
            unseen = [
                r for r in self.task_store.runs(t.id) if r.started_at > t.seen_runs_at
            ]
            tasks.append(
                {
                    **t.public(),
                    "unseen_runs": len(unseen),
                    "unseen_failed": bool(unseen) and unseen[0].status == "error",
                }
            )
        return {"tasks": tasks}

    def mark_automation_seen(self, task_id: str) -> dict[str, Any]:
        task = self.task_store.get(task_id)
        if task is None:
            return {"ok": False, "error": "not found"}
        task.seen_runs_at = time.time()
        self.task_store.save(task)
        return {"ok": True}

    def get_automation(self, task_id: str) -> dict[str, Any]:
        task = self.task_store.get(task_id)
        if task is None:
            return {"error": "not found"}
        return {
            "task": task.public(),
            "runs": [r.to_dict() for r in self.task_store.runs(task_id)],
        }

    def create_automation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an automation directly from the GUI (the "New automation" / template flow).
        Mirrors the agent-facing `create_scheduled_task` validation, but binds the task to a
        fresh per-task scratch workspace instead of an origin conversation's folder."""
        from croniter import croniter

        title = (payload.get("title") or "").strip()
        instructions = (payload.get("instructions") or "").strip()
        cron = (payload.get("cron") or "").strip() or None
        fire_at = (payload.get("fire_at") or "").strip() or None
        timezone = (payload.get("timezone") or "").strip() or "local"

        if not title:
            return {"ok": False, "error": "title is required"}
        if not instructions:
            return {"ok": False, "error": "instructions are required"}
        if not cron and not fire_at:
            return {
                "ok": False,
                "error": "provide a cron (recurring) or a fire_at ISO datetime (one-time)",
            }
        if cron and not croniter.is_valid(cron):
            return {"ok": False, "error": f"invalid cron expression: {cron}"}

        schedule = Schedule(
            kind="once" if (fire_at and not cron) else "cron",
            cron=cron,
            fire_at=fire_at,
            timezone=timezone,
        )
        from ..automation.models import grant_entries

        task = ScheduledTask(
            title=title,
            instructions=instructions,
            schedule=schedule,
            workspace="",
            origin_surface="cowork",
            agent="cowork",
            # Human-driven path (GUI form / onboarding recipes): the creating surface
            # rendered the grants, the submit IS the consent. Same validation as the
            # agent tool — only target-bound write grants survive.
            always_allowed_tools=grant_entries(payload.get("permissions")),
        )
        task.workspace = self._provision_scratch(task.task_session_id)
        self.task_store.save(task)
        return {"ok": True, "task": task.public()}

    def update_automation(
        self, task_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        task = self.task_store.get(task_id)
        if task is None:
            return {"ok": False, "error": "not found"}
        if "enabled" in changes:
            task.enabled = bool(changes["enabled"])
        if changes.get("instructions") is not None:
            task.instructions = changes["instructions"]
        if changes.get("title") is not None:
            task.title = changes["title"]
        if changes.get("cron") is not None:
            from croniter import croniter

            if not croniter.is_valid(changes["cron"]):
                return {"ok": False, "error": "invalid cron"}
            task.schedule.cron, task.schedule.kind = changes["cron"], "cron"
        if changes.get("revoke"):
            # Revocation from the task detail page ("Allowed without asking … · Revoke").
            # Human-only, like minting; the agent-facing update tool has no such field.
            task.revoke_rule(str(changes["revoke"]))
        self.task_store.save(task)
        if changes.get("revoke"):
            # A live run engine may still hold the revoked rule — reseed from the record.
            for sid, engine in self._engines.items():
                owner = self.task_store.task_for_run_session(sid)
                if owner is not None and owner.id == task.id:
                    engine.permissions.task_rules = task.standing_rules()
        return {"ok": True, "task": task.public()}

    def delete_automation(self, task_id: str) -> dict[str, Any]:
        return {"ok": self.task_store.delete(task_id), "id": task_id}

    def prepare_manual_run(self, task_id: str) -> dict[str, Any]:
        """Create a 'running' manual run and return its session, so the GUI can open it and
        drive the task LIVE over the normal session WS (you watch the agent + follow up). The
        automatic scheduler path stays headless (`_run_scheduled_task`)."""
        task = self.task_store.get(task_id)
        if task is None:
            return {"ok": False, "error": "not found"}
        Path(task.workspace).mkdir(parents=True, exist_ok=True)
        run = TaskRun(
            task_id=task.id, trigger="manual"
        )  # status "running", session_id auto
        self.task_store.add_run(run)
        return {
            "ok": True,
            "run_id": run.run_id,
            "session_id": run.session_id,
            "workspace": task.workspace,
            "agent": task.agent,
            # Same execute-now framing as the headless path — manual runs ride a normal live
            # session whose engine DOES have scheduling tools, so be explicit.
            "prompt": (
                f"⏰ Running automation '{task.title}' now. Carry out these instructions "
                "immediately and produce the result. The schedule already exists — do not create "
                f"or modify any scheduled tasks.\n\n{task.instructions}"
            ),
        }

    def finalize_manual_run(self, task_id: str, run_id: str) -> dict[str, Any]:
        """Mark a manual run complete once its first turn finished (the WS already saved the
        session). Pulls result text + artifacts from the persisted transcript/workspace.
        """
        run = next(
            (r for r in self.task_store.runs(task_id) if r.run_id == run_id), None
        )
        task = self.task_store.get(task_id)
        if run is None or task is None:
            return {"ok": False, "error": "not found"}
        if run.status == "running":
            record = self.session_store.load(run.session_id)
            run.result_text = _last_assistant_text(record.messages) if record else None
            run.artifacts = _recent_files(task.workspace, since=run.started_at)
            run.status = "ok"
            run.finished_at = _epoch()
            self.task_store.add_run(run)
            task.last_run, task.last_status = run.finished_at, "ok"
            task.run_count += 1
            self.task_store.save(task)
        return {"ok": True, "run": run.to_dict()}

    def save(self, session_id: str, engine: TurnEngine, touch: bool = True) -> None:
        executor = getattr(engine, "executor", None)
        workspace = os.path.realpath(str(executor.cwd)) if executor else ""
        self.session_store.save(
            SessionRecord(
                session_id=session_id,
                workspace=workspace,
                model=engine.model,
                mode=engine.permissions.mode.value,
                messages=engine.messages,
                title=title_from(engine.messages),
                agent=getattr(engine, "agent_name", "code"),
                extra_roots=self._extra_roots_of(engine, session_id),
                grants=_grants_of(engine),
                compaction=(
                    engine.compaction_state.as_dict()
                    if getattr(engine, "compaction_state", None)
                    else {}
                ),
            ),
            touch=touch,
        )

    @staticmethod
    def _apply_grants(engine: TurnEngine, grants: dict[str, Any]) -> None:
        """Re-apply a reloaded session's persisted "Always allow" approvals — they're
        session-scoped, and the session outlives the process (owner-hit 2026-07-22)."""
        for tool in grants.get("tools") or []:
            engine.permissions.allow_tool_for_session(str(tool))
        for command in grants.get("commands") or []:
            engine.permissions.allow_command_for_session(str(command))
        if grants.get("readonly"):
            engine.permissions.allow_readonly_for_session()

    def _extra_roots_of(
        self, engine: TurnEngine, session_id: str
    ) -> list[dict[str, Any]]:
        """User/agent-added folders = the engine's roots minus the primary (index 0) AND
        the session's provisioned scratch root. Persisting the scratch as an "extra"
        would re-add it as a plain folder on every rebuild (universal scratch made
        index-0-only slicing wrong for dual-root sessions)."""
        roots = getattr(engine, "roots", None) or []
        scratch = (self.scratch_base() / session_id).expanduser()
        try:
            scratch = scratch.resolve()
        except OSError:
            pass
        return [
            {"path": str(r.path), "writable": bool(r.writable), "label": r.label}
            for r in roots[1:]
            if r.path != scratch
        ]

    # -- LLM auto-titles (FB-010) -------------------------------------------------
    _AUTOTITLE_PROMPT = (
        "You title chat sessions. Given the user's opening message(s) — and, when "
        "present, the assistant's first reply for context — reply with ONLY a 4-5 word "
        "title for the session, named after what the session is actually about — no "
        "quotes or punctuation wrapping it. If there is no topic at all ("
        '"hey", "how are you", "hi there" and a generic reply), reply with exactly: '
        "small-talk"
    )

    def _maybe_autotitle(self, session_id: str) -> None:
        """Kick off title generation after a turn completes, fire-and-forget. Only while
        the session has neither a manual rename nor a generated title, at most twice:
        attempt 1 rides turn 1, and the second window exists solely for the small-talk
        retry (with both openers). Attempts are counted in memory rather than derived
        from the user-message count — steering injections also land as role "user", and
        counting them would silently suppress titling on a steered first turn. A restart
        forgetting the counter is harmless: renamed/auto_title still gate re-titling."""
        if session_id.startswith("__"):
            return
        engine = self._engines.get(session_id)
        if engine is None or session_id in self._autotitle_inflight:
            return
        if self.task_store.task_for_run_session(session_id) is not None:
            return  # automation runs are titled by their task
        # Three windows, not two (owner ruling 2026-08-24): opener-only at turn 1 start,
        # opener+assistant-reply at turn 1 end (titles a "hey"-then-real-work session),
        # and both-openers at turn 2 start. The signature guard makes each fire at most
        # once; sessions with a meaty first message still title on attempt 1.
        if self._autotitle_attempts.get(session_id, 0) >= 3:
            return
        users = [m for m in engine.messages if m.get("role") == "user"]
        if not users:
            return
        state = self.session_store.title_state(session_id)
        if state is None or state["renamed"] or state["auto_title"]:
            return
        from ..attachments import content_to_text

        openers = [
            text
            for m in users
            if (text := content_to_text(m.get("content"), image_placeholder="").strip())
        ][:2]
        if not openers:
            return
        # The agent's first reply is fair evidence for a TITLE (unlike the reviewer,
        # naming a session is not a security boundary — owner ruling 2026-08-24): it is
        # what turns "hey" + a generic ask into "Semgrep security review".
        assistant = next(
            (
                text
                for m in engine.messages
                if m.get("role") == "assistant"
                and (
                    text := content_to_text(
                        m.get("content"), image_placeholder=""
                    ).strip()
                )
            ),
            "",
        )[:400]
        # Same evidence as the last attempt → nothing new to say; skip WITHOUT burning an
        # attempt (this is how the turn-start and turn-end triggers coexist).
        sig = (len(openers), bool(assistant))
        if self._autotitle_sig.get(session_id) == sig:
            return
        self._autotitle_sig[session_id] = sig
        self._autotitle_attempts[session_id] = (
            self._autotitle_attempts.get(session_id, 0) + 1
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop to ride (sync caller) — skip, never block
        self._autotitle_inflight.add(session_id)
        # Retain the task: the loop holds only a weak ref, and a GC'd task would both
        # kill the title mid-flight and strand the inflight guard.
        task = loop.create_task(
            self._generate_autotitle(session_id, engine, openers, assistant)
        )
        self._autotitle_tasks.add(task)
        task.add_done_callback(self._autotitle_tasks.discard)

    async def _generate_autotitle(
        self,
        session_id: str,
        engine: TurnEngine,
        openers: list[str],
        assistant: str = "",
    ) -> None:
        """One cheap non-streaming completion on the session's own provider/model. Every
        failure (provider error, empty, absurdly long) is swallowed — the title_from
        fallback stays; the small-talk sentinel leaves auto_title unset so the turn-2
        retry can run."""
        try:
            turn = await asyncio.to_thread(
                engine.provider.complete,
                model=engine.model,
                messages=[
                    {"role": "system", "content": self._AUTOTITLE_PROMPT},
                    {
                        "role": "user",
                        "content": "\n\n".join(openers)
                        + (
                            f"\n\n[the assistant's first reply]\n{assistant}"
                            if assistant
                            else ""
                        ),
                    },
                ],
                temperature=0.2,
                # Reasoning-routed models spend hidden tokens BEFORE emitting text; a
                # tight cap plus default effort yields an empty completion and a silent
                # no-op. Effort "none" reaches only the OpenAI-compat path (the native
                # providers whitelist their settings), and 64 leaves headroom either way.
                max_tokens=64,
                reasoning_effort="none",
            )
            raw = (getattr(turn, "text", None) or "").strip()
            # Sanitize: surrounding quotes off, whitespace collapsed, capped at 60.
            title = " ".join(raw.strip("\"'“”‘’`").split())
            # Sentinel tolerance: models riff on the exact token ("Small talk.", quoted,
            # trailing period) — normalize before comparing, else the riff becomes the title.
            if title.lower().strip(".!,;:'\"").replace(" ", "-").replace("_", "-") in (
                "small-talk",
                "smalltalk",
            ):
                return
            if not title or len(title) > 80:
                return
            if self.session_store.set_auto_title(session_id, title[:60]):
                # Best-effort nudge for any live viewer; the sidebar's poll and
                # post-turn refresh pick the new title up regardless.
                await self.broadcast_session(
                    session_id,
                    {
                        "type": "session_title",
                        "data": {"session_id": session_id, "title": title[:60]},
                    },
                )
        except Exception:
            # A failed title must never surface as a session error — but it must
            # not be invisible either (a silent provider 400 hid the max_tokens
            # rejection for a whole owner test pass, 2026-07-20).
            # warning, not debug: debug was invisible in packaged builds, which re-hid
            # exactly the class of failure this comment warns about (2026-08-24: the plan
            # backend 400-ing on max_output_tokens went unseen for a whole test pass).
            logger.warning("autotitle failed for %s", session_id, exc_info=True)
        finally:
            self._autotitle_inflight.discard(session_id)

    # -- session roots (orphan Cowork: scratch + added folders) ------------------
    def get_roots(self, session_id: str) -> list[dict[str, Any]]:
        """The directories this session can touch: primary scratch first, then added folders.
        Reads the live engine when one is running; otherwise reconstructs from persisted state.
        """
        engine = self._engines.get(session_id)
        if engine is not None and getattr(engine, "roots", None):
            return [
                {
                    "path": str(r.path),
                    "writable": bool(r.writable),
                    "label": r.label,
                    "primary": i == 0,
                    "exists": r.path.is_dir(),
                }
                for i, r in enumerate(engine.roots)
            ]
        record = self.session_store.load(session_id)
        primary = (
            record.workspace
            if record and record.workspace
            else self._provision_scratch(session_id)
        )
        extra = (record.extra_roots if record else []) or []
        primary_is_scratch = self.is_temp_workspace(primary)
        out = [
            {
                "path": primary,
                "writable": True,
                "label": "scratch" if primary_is_scratch else "workspace",
                "primary": True,
                "exists": Path(primary).is_dir(),
            }
        ]
        # Universal scratch: a real-folder session also carries its provisioned scratch
        # root (mirrors the engine-side shape so a cold read matches a live one).
        if not primary_is_scratch and self._SESSION_ID_RE.match(session_id or ""):
            scratch = self.scratch_base() / session_id
            out.append(
                {
                    "path": str(scratch.expanduser().resolve()),
                    "writable": True,
                    "label": "scratch",
                    "primary": False,
                    "exists": scratch.is_dir(),
                }
            )
        for r in extra:
            p = str(r.get("path", ""))
            out.append(
                {
                    "path": p,
                    "writable": bool(r.get("writable", False)),
                    "label": r.get("label") or Path(p).name,
                    "primary": False,
                    "exists": Path(p).is_dir(),
                }
            )
        return out

    def promote_workspace(self, session_id: str, path: str) -> dict[str, Any]:
        """Root promotion (workspace-scratch-design.md §5): adopt `path` as the session's
        primary workspace. One-way and once — only while the primary is still the
        provisioned scratch; a session that already has a real workspace is never
        re-pointed. Mutates the live session (roots + shell cwd), persists, and marks
        the engine for a post-turn rebuild."""
        p = Path(path).expanduser()
        if not p.is_dir():
            return {"ok": False, "error": f"not a directory: {path}"}
        resolved = p.resolve()
        engine = self._engines.get(session_id)
        if engine is None:
            return {"ok": False, "error": "no live session to promote"}
        executor = getattr(engine, "executor", None)
        current = str(executor.cwd) if executor is not None else None
        if not current or not self.is_temp_workspace(current):
            return {"ok": False, "error": "this session already has a workspace"}
        roots = getattr(engine, "roots", None)
        if roots is None:
            return {"ok": False, "error": "this session has no directory list"}
        # Shared list: permissions, file tools, and the context injector see the new
        # primary immediately. The old scratch primary stays as the scratch root.
        roots[:] = [
            RootDir(path=resolved, writable=True, label="workspace"),
            *[r for r in roots if r.path != resolved],
        ]
        try:
            # Move the live shell too — save() derives the persisted workspace from the
            # executor's cwd, so this is also what makes the promotion durable.
            res = executor.run(f"cd {shlex.quote(str(resolved))}", timeout=15)
            if res.get("exit_code") != 0:
                executor.cwd = str(resolved)
        except Exception:
            executor.cwd = str(resolved)  # a respawned shell starts there
        self.save(session_id, engine)
        self.session_store.touch_workspace(str(resolved))
        self._promotion_rebuild.add(session_id)
        return {"ok": True, "path": str(resolved), "roots": self.get_roots(session_id)}

    def add_root(
        self, session_id: str, path: str, writable: bool = False
    ) -> dict[str, Any]:
        """Grant the session access to another folder (read-only or read-write). Mutates the live
        engine in place when running (file tools + permissions + context see it immediately) and
        persists it so a later resume still has it."""
        p = Path(path).expanduser()
        if not p.is_dir():
            return {"ok": False, "error": f"not a directory: {path}"}
        resolved = p.resolve()
        engine = self._engines.get(session_id)
        if engine is not None and getattr(engine, "roots", None) is not None:
            if any(r.path == resolved for r in engine.roots):
                # already present: just update its access level
                for r in engine.roots:
                    if r.path == resolved:
                        r.writable = bool(writable)
            else:
                engine.roots.append(RootDir(path=resolved, writable=bool(writable)))
            self.session_store.set_extra_roots(
                session_id, self._extra_roots_of(engine, session_id)
            )
        else:
            # A brand-new conversation has no record yet (it's only saved after the first turn) —
            # create one now so set_extra_roots has a row to update and the folder survives.
            if self.session_store.load(session_id) is None:
                self.session_store.save(
                    SessionRecord(
                        session_id=session_id,
                        workspace=self._provision_scratch(session_id),
                        model=self.model,
                        mode=self.mode.value,
                        messages=[],
                        agent="cowork",  # folder access is a Cowork affordance
                    )
                )
            session_scratch = str((self.scratch_base() / session_id).expanduser().resolve())
            extra = [
                r
                for r in self.get_roots(session_id)
                if not r["primary"] and r["path"] != session_scratch
            ]
            extra = [r for r in extra if Path(r["path"]).resolve() != resolved]
            extra.append(
                {
                    "path": str(resolved),
                    "writable": bool(writable),
                    "label": resolved.name,
                }
            )
            self.session_store.set_extra_roots(
                session_id,
                [
                    {
                        "path": r["path"],
                        "writable": r["writable"],
                        "label": r.get("label", ""),
                    }
                    for r in extra
                ],
            )
        self.session_store.touch_workspace(str(resolved))
        # Grant-time notice (pass 20): if this directory's project already has
        # memory or a board, say so — one line, pointer only, to agent + user.
        notice = self._project_notice(str(resolved))
        engine = self._engines.get(session_id)
        if notice and engine is not None:
            engine._append_notice("project_presence", notice)
        return {"ok": True, "roots": self.get_roots(session_id), "notice": notice}

    def _project_notice(self, path: str) -> Optional[str]:
        """One-line presence pointer for a newly granted directory, or None."""
        try:
            key = project_key(path)
            pres = project_presence(
                key, memory_store=self.memory_store, team_store=self.team_store
            )
        except Exception:
            return None
        parts = []
        if pres["memories"]:
            parts.append(f"project memory ({pres['memories']} entries)")
        if pres["board_items"]:
            parts.append("a board")
        if not parts:
            return None
        label = project_label(key)["label"]
        return f"“{label}” already has {' and '.join(parts)} — bind it by name or start a session there to use it."

    def remove_root(self, session_id: str, path: str) -> dict[str, Any]:
        """Revoke a previously-added folder. The primary scratch cannot be removed."""
        resolved = Path(path).expanduser().resolve()
        engine = self._engines.get(session_id)
        if engine is not None and getattr(engine, "roots", None):
            if engine.roots and engine.roots[0].path == resolved:
                return {
                    "ok": False,
                    "error": "cannot remove the primary scratch directory",
                }
            engine.roots[:] = [r for r in engine.roots if r.path != resolved]
            self.session_store.set_extra_roots(
                session_id, self._extra_roots_of(engine, session_id)
            )
        else:
            current = self.get_roots(session_id)
            if (
                current
                and current[0]["primary"]
                and Path(current[0]["path"]).resolve() == resolved
            ):
                return {
                    "ok": False,
                    "error": "cannot remove the primary scratch directory",
                }
            session_scratch = (self.scratch_base() / session_id).expanduser().resolve()
            extra = [
                r
                for r in current
                if not r["primary"]
                and Path(r["path"]).resolve() not in (resolved, session_scratch)
            ]
            self.session_store.set_extra_roots(
                session_id,
                [
                    {
                        "path": r["path"],
                        "writable": r["writable"],
                        "label": r.get("label", ""),
                    }
                    for r in extra
                ],
            )
        return {"ok": True, "roots": self.get_roots(session_id)}

    def session_messages(self, session_id: str) -> list[dict[str, Any]]:
        # A live engine's in-memory thread is authoritative: mid-turn it's ahead of the
        # persisted record — which may not even exist yet for a scheduled run's first turn
        # (opening a "running" automation showed a blank session; owner report 2026-07-04).
        engine = self._engines.get(session_id)
        if engine is not None:
            return list(engine.messages)
        record = self.session_store.load(session_id)
        return record.messages if record else []

    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        if session_id.startswith("__"):
            return {"ok": False, "error": "internal sessions cannot be renamed"}
        ok = self.session_store.rename(session_id, title)
        return {
            "ok": ok,
            "session_id": session_id,
            "title": " ".join((title or "").split())[:120],
        }

    def set_session_flags(
        self,
        session_id: str,
        *,
        pinned: Optional[bool] = None,
        archived: Optional[bool] = None,
    ) -> dict[str, Any]:
        if session_id.startswith("__"):
            return {"ok": False, "error": "internal sessions cannot be modified here"}
        ok = self.session_store.set_flags(session_id, pinned=pinned, archived=archived)
        return {"ok": ok, "session_id": session_id}

    def delete_session(self, session_id: str) -> dict[str, Any]:
        if session_id.startswith("__"):
            return {"ok": False, "error": "internal sessions cannot be deleted here"}
        engine = self._engines.pop(session_id, None)
        if engine is not None:
            try:
                # (was engine.interrupt() — a method that never existed; the AttributeError
                # was silently swallowed, so deleting a running session never stopped it.)
                engine.request_interrupt()
            except Exception:
                pass
        record = self.session_store.load(session_id)
        ok = self.session_store.delete(session_id)
        # Deleting a session is the one implicit unsubscribe (otherwise subscriptions are permanent).
        self.subscriptions.remove_session(session_id)
        # ...and releases any Slack threads it owned (§31): the next tag there spawns fresh.
        self.mention_sessions.remove_session(session_id)
        # ...and drops its per-session connector overrides (§4.2, like subscriptions).
        self.session_connections.remove_session(session_id)
        # ...and its per-session skill mutes (SKILLS-SPEC §3 — mutes die with the session).
        self.session_skills.remove_session(session_id)
        # ...and closes its pending Inbox items — an orphaned approval/question can never be
        # meaningfully answered (owner call, 2026-07-03).
        self.inbox.resolve_session(session_id)
        # ...and its scratch dir. STRICTLY scoped: only a directory inside scratch_base is
        # removed — a real project folder the user picked is never touched.
        if ok and record and record.workspace:
            scratch = self.scratch_base().resolve()
            ws = Path(record.workspace)
            try:
                resolved = ws.resolve()
                if (
                    resolved.is_relative_to(scratch)
                    and resolved != scratch
                    and resolved.is_dir()
                ):
                    shutil.rmtree(resolved)
            except OSError:
                pass  # a stale/foreign path must not fail the delete
        return {"ok": ok, "session_id": session_id}

    # -- provider proxy ---------------------------------------------------------
    def provider_complete(self, model, messages, tools=None):
        return self.provider.complete(model=model, messages=messages, tools=tools)

    def _refresh_provider(self, name: Optional[str] = None) -> None:
        """Drop the router's cached client(s) so the next turn rebuilds with fresh config.
        No-op for an injected non-router provider (tests)."""
        invalidate = getattr(self.provider, "invalidate", None)
        if callable(invalidate):
            invalidate(name)

    # -- read models ------------------------------------------------------------
    def list_sessions(self, workspace: Optional[str] = None) -> list[dict[str, Any]]:
        ws = self.resolve_workspace(workspace) if workspace else None
        return [
            {
                "session_id": r.session_id,
                "title": r.title or "New session",
                "workspace": r.workspace,
                "agent": r.agent,
                "model": r.model,
                "mode": r.mode,
                "updated_at": r.updated_at,
                "messages": r.message_count,
                "pinned": r.pinned,
                "archived": r.archived,
                # §31: non-user origin ("slack") + display label — drives the sidebar's
                # "From Slack" group and the row's platform icon.
                "origin": r.origin,
                "origin_label": r.origin_label,
                # Attention = Inbox items awaiting this session (the amber count that bubbles
                # session → persona → footer Inbox). Liveness = working (in-flight turn) /
                # sleeping (a self-wake is pending) / idle — a count-less dot that never bubbles.
                "attention": len(self.inbox.pending(session_id=r.session_id)),
                "liveness": self._session_liveness(r.session_id),
                # When sleeping: the next timer fire (ISO) — drives the "sleeping
                # until…" strip so a scheduled agent never reads as a dead one.
                "sleeping_until": self._sleeping_until(r.session_id),
                # Channels this session listens to (inbound subscriptions) — drives the per-session
                # "connections" indicator.
                "subscriptions": [
                    s.channel for s in self.subscriptions.for_session(r.session_id)
                ],
                # Agent teams: {} for plain sessions. Workers carry role/lead_session
                # (+ a computed current-item line); leads carry role/team_id — drives
                # the sidebar's ONE expandable team entry.
                "team": self._session_team_row(r),
            }
            for r in self.session_store.list(workspace=ws)
            if not r.session_id.startswith("__")  # hide internal threads
        ]

    def _session_team_row(self, record: SessionRecord) -> dict[str, Any]:
        info = record.team or {}
        if not info:
            return {}
        row = {
            "role": info.get("role", ""),
            "team_id": info.get("team_id", ""),
            "lead_session": info.get("lead_session", ""),
        }
        if info.get("role") == "lead":
            team = self.teams.get(str(info.get("team_id", "")))
            if team is not None and team.chat_enabled and team.chat_group:
                row["chat_enabled"] = True
                row["chat_unread"] = self.chat_store.unread_count(
                    team.chat_group, "user"
                )
        if info.get("role") == "worker" and info.get("space") and info.get("actor"):
            try:
                items = self.team_store.list_items(
                    str(info["space"]), self._user_actor(), assignee=str(info["actor"])
                )
            except Exception:
                items = []
            active = next(
                (
                    i
                    for state in ("blocked", "review", "in_progress", "open")
                    for i in items
                    if i["state"] == state
                ),
                None,
            )
            row["actor"] = info["actor"]
            row["current_item"] = (
                f"#{active['id']} {active['state'].replace('_', ' ')}" if active else "idle"
            )
            row["status"] = active["state"] if active else "idle"
        return row

    def _sleeping_until(self, session_id: str) -> Optional[str]:
        fires = [
            w.fire_at
            for w in self.wakes.pending(session_id)
            if w.kind == "timer" and w.fire_at
        ]
        return min(fires) if fires else None

    def _session_liveness(self, session_id: str) -> str:
        if self.is_running(session_id):
            return "working"
        if self.wakes.pending(session_id):
            return "sleeping"
        return "idle"

    def list_agents(self) -> list[dict[str, Any]]:
        return _list_agents()

    # -- skills (SKILLS-SPEC §4.4) ------------------------------------------------
    def list_skills(self, workspace: Optional[str] = None) -> list[dict[str, Any]]:
        """Enriched rows for the Settings screen (scope/source/enabled). Optional workspace
        adds that project's skills, with project copies shadowing same-named global ones."""
        return self.skill_store.rows(workspace or None)

    def reveal_skill(
        self, name: str, workspace: Optional[str] = None
    ) -> dict[str, Any]:
        """Open the skill's folder in the OS file manager (§6 "Show folder" — the power-user
        window into folder-is-truth). Same local-machine rationale as reveal_artifact."""
        import subprocess
        import sys

        try:
            folder, _scope = self.skill_store.find(name, workspace or None)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            if sys.platform == "darwin":
                subprocess.Popen(
                    ["open", str(folder)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "win32":
                import os

                os.startfile(str(folder))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(
                    ["xdg-open", str(folder)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def persona_mcp_scope(self, persona_id: str) -> Optional[set[str]]:
        """The persona's declared MCP-server scope (OPE-58 sibling stub): the manifest's
        `mcp:` names, or None when it declares none (= no scoping). Only ever narrows —
        the user's enabled/configured/authed gates apply regardless."""
        entry = self.personas.get(persona_id)
        names = list((entry.manifest.mcp if entry and entry.manifest else []) or [])
        return {n for n in names if n} or None

    def persona_skill_scope(
        self, persona_id: str
    ) -> tuple[Optional[Path], Optional[set[str]]]:
        """The persona's own skill folder + optional allowlist (OPE-58).

        A manifest-backed persona carries skills as a `skills/` dir next to its manifest —
        the sharing bundle shape (manifest + skill folders). The manifest's `skills:` list,
        when non-empty, narrows which of those activate. Additive on top of global/project
        scopes: the persona SHIPS skills; it never hides the user's own."""
        entry = self.personas.get(persona_id)
        manifest = entry.manifest if entry else None
        if manifest is None or not manifest.source:
            return None, None
        d = Path(manifest.source).parent / "skills"
        if not d.is_dir():
            return None, None
        allow = {s for s in manifest.skills if s} or None
        return d, allow

    def effective_skill_names(
        self,
        session_id: str,
        workspace: Optional[str | Path] = None,
        agent: Optional[str] = None,
    ) -> set[str]:
        """The session's skill menu (§3): merged scopes − Settings disables − session mutes.
        The single resolver behind the engine catalog, the rail list, and the composer popup.
        Persona-carried skills (OPE-58) join the merge for the session's persona — user
        disables and mutes still win over them."""
        dirs = [self.skill_store.global_dir]
        if workspace:
            dirs.append(self.skill_store.project_dir(workspace))
        loader = SkillLoader(dirs)
        names = set(loader.names())
        persona_dir, allow = self.persona_skill_scope(self._persona_of(session_id, agent))
        if persona_dir is not None:
            persona_names = set(SkillLoader([persona_dir]).names())
            if allow is not None:
                persona_names &= allow
            names |= persona_names
        return effective_skills(
            names=names,
            disabled=self.skill_store.disabled_names(),
            session_overrides=self.session_skills.get(session_id),
        )

    def session_skills_view(
        self, session_id: str, workspace: Optional[str] = None
    ) -> dict[str, Any]:
        """The rail payload: every in-scope, Settings-enabled skill with its mute state.
        Persona-carried skills (OPE-58) appear with scope "coworker" — mutable per session
        like any other, but owned by the persona bundle, not the Settings store."""
        disabled = self.skill_store.disabled_names()
        overrides = self.session_skills.get(session_id)
        rows = [
            {
                "name": r["name"],
                "description": r["description"],
                "scope": r["scope"],
                "enabled": overrides.get(r["name"], True),
            }
            for r in self.skill_store.rows(workspace or None)
            if r["name"] not in disabled
        ]
        seen = {r["name"] for r in rows}
        persona_dir, allow = self.persona_skill_scope(self._persona_of(session_id))
        if persona_dir is not None:
            for entry in SkillLoader([persona_dir]).catalog():
                name = entry["name"]
                if name in seen or name in disabled:
                    continue  # a global/project copy shadows the bundle's
                if allow is not None and name not in allow:
                    continue
                rows.append(
                    {
                        "name": name,
                        "description": entry["description"],
                        "scope": "coworker",
                        "enabled": overrides.get(name, True),
                    }
                )
        return {"skills": rows}

    def _scratch_workspace_error(self, workspace: Any) -> Optional[dict[str, Any]]:
        """Refuse skill WRITES into a per-conversation scratch dir — a skill saved there is
        stranded in a throwaway folder. Backend chokepoint: guards every entry path (UI,
        REST, future import), not just the flows the GUI happens to gate."""
        if not workspace:
            return None
        try:
            ws = Path(str(workspace)).expanduser().resolve()
            if ws.is_relative_to(self.scratch_base().resolve()):
                return {
                    "ok": False,
                    "error": (
                        "That folder is a temporary session space — skills saved there "
                        "would be lost. Save it globally or pick a real project."
                    ),
                }
        except OSError:
            pass
        return None

    def create_skill(self, body: dict[str, Any]) -> dict[str, Any]:
        blocked = self._scratch_workspace_error(body.get("workspace"))
        if blocked:
            return blocked
        try:
            created = self.skill_store.create(
                name=str(body.get("name", "")),
                description=str(body.get("description", "")),
                instructions=str(body.get("instructions", "")),
                scope=str(body.get("scope", "global") or "global"),
                workspace=body.get("workspace") or None,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "skill": created}

    def update_skill(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            if "enabled" in body:
                self.skill_store.set_enabled(name, bool(body["enabled"]))
            if body.get("description") is not None or body.get("instructions") is not None:
                self.skill_store.update(
                    name,
                    description=body.get("description"),
                    instructions=body.get("instructions"),
                    workspace=body.get("workspace") or None,
                )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def delete_skill(self, name: str, workspace: Optional[str] = None) -> dict[str, Any]:
        try:
            self.skill_store.delete(name, workspace or None)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def move_skill(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        # Moving INTO project scope must not target a scratch dir (moving OUT is fine —
        # that's the rescue path for already-stranded skills).
        if str(body.get("scope", "")) == "project":
            blocked = self._scratch_workspace_error(body.get("workspace"))
            if blocked:
                return blocked
        try:
            moved = self.skill_store.move(
                name,
                to_scope=str(body.get("scope", "")),
                workspace=body.get("workspace") or None,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "skill": moved}

    def stage_skill_upload(self, data: bytes, filename: str = "") -> dict[str, Any]:
        try:
            preview = self.skill_store.stage_upload(data, filename)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **preview}

    def confirm_skill_upload(self, body: dict[str, Any]) -> dict[str, Any]:
        blocked = self._scratch_workspace_error(body.get("workspace"))
        if blocked:
            return blocked
        try:
            saved = self.skill_store.confirm_upload(
                str(body.get("token", "")),
                scope=str(body.get("scope", "global") or "global"),
                workspace=body.get("workspace") or None,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "skill": saved}

    def _memory_saved_notifier(self, session_id: str):
        """MEMORY-SPEC §5.1: push the memory_saved event that powers the GUI's save
        toast ("I'll remember that — … [Undo]"). Best-effort by design: `remember` may
        run with no socket attached (background runs) or off the loop thread — a lost
        toast never fails the save."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        def notify(item, previous=None) -> None:
            if loop is None or not loop.is_running():
                return
            payload = {
                "type": "memory_saved",
                "data": {
                    "id": item.id,
                    "scope": item.scope.value,
                    "summary": item.summary or "",
                    "content": item.content,
                    # Set when this was an EDIT of an existing memory: the surface says
                    # "I've updated what I remember" and Undo restores this text.
                    "previous": previous or "",
                },
            }
            try:
                asyncio.run_coroutine_threadsafe(
                    self.broadcast_session(session_id, payload), loop
                )
            except RuntimeError:
                pass

        return notify

    # -- project bindings (pass 20 / UX-044) -------------------------------------

    def project_menu(self, session_id: str, kind: str) -> dict[str, Any]:
        """The submenu payload: the session's derived project (pinned, labeled per
        the UX-044 rules) + named entries MRU-ordered. The GUI shows 5 and grows a
        filter at 6+; the full named list ships so the filter reaches everything."""
        record = self.session_store.load(session_id)
        ws = (record.workspace if record else None) or self.default_workspace
        derived_key = project_key(ws) if ws else None
        names = self.session_store.names()
        bound = ((record.bindings if record else {}) or {}).get(kind)
        named = names.list(kind)
        return {
            "kind": kind,
            "bound": bound,
            "derived": (
                {**project_label(derived_key), "key": derived_key}
                if derived_key
                else None
            ),
            "named": [{"name": n["name"], "key": n["key"]} for n in named],
        }

    def set_binding(
        self, session_id: str, kind: str, name: Optional[str]
    ) -> dict[str, Any]:
        """Bind (or unbind, name=None) a named project for this session. Takes
        effect at the next engine build — the running engine keeps the knowledge
        it started with (same doctrine as memory deletions)."""
        if kind not in ("memory", "board"):
            return {"ok": False, "error": f"unknown kind {kind!r}"}
        if self.is_running(session_id):
            return {"ok": False, "error": "wait for the current task to finish first"}
        if name and self.session_store.names().resolve(kind, name) is None:
            return {"ok": False, "error": f"no {kind} named {name!r}"}
        record = self.session_store.load(session_id)
        bindings = dict((record.bindings if record else {}) or {})
        if name:
            bindings[kind] = name
        else:
            bindings.pop(kind, None)
        if record is None:
            return {"ok": False, "error": "unknown session"}
        self.session_store.set_bindings(session_id, bindings)
        # Rebind applies from the next engine build; drop the cached engine so the
        # next turn rebuilds with the new key (messages persist via the record).
        self._engines.pop(session_id, None)
        return {"ok": True, "bindings": bindings}

    def name_current_project(
        self, session_id: str, kind: str, name: str
    ) -> dict[str, Any]:
        """Give the session's derived project a user name (UX-044 'Name current…')."""
        record = self.session_store.load(session_id)
        ws = (record.workspace if record else None) or self.default_workspace
        if not ws:
            return {"ok": False, "error": "session has no workspace"}
        try:
            entry = self.session_store.names().name_current(
                kind, name, project_key(ws)
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, **entry}

    def list_memory(self) -> list[dict[str, Any]]:
        return [
            {
                "id": m.id,
                "scope": m.scope.value,
                "content": m.content,
                "summary": m.summary or "",
                "created_at": m.created_at or "",
            }
            for m in self.memory_store.list()
        ]

    def add_memory(
        self, content: str, scope: str = "workspace", workspace: Optional[str] = None
    ) -> dict[str, Any]:
        content = (content or "").strip()
        if not content:
            return {"ok": False, "error": "content required"}
        chosen = Scope(scope) if scope in _SCOPES else Scope.WORKSPACE
        ws = self.resolve_workspace(workspace) if chosen is Scope.WORKSPACE else None
        item = self.memory_store.add(content, scope=chosen, workspace=ws)
        return {"id": item.id, "scope": item.scope.value, "content": item.content}

    def update_memory(self, item_id: int, content: str) -> dict[str, Any]:
        """Edit-in-place from the memory screen (§5.3). The user rewrote the fact, so
        the stale one-line summary is cleared rather than left contradicting it."""
        content = (content or "").strip()
        if not content:
            return {"ok": False, "error": "content required"}
        item = self.memory_store.update(item_id, content, summary="")
        if item is None:
            return {"ok": False, "error": f"no memory with id {item_id}"}
        return {"ok": True, "id": item.id, "content": item.content}

    def delete_memory(self, item_id: int) -> dict[str, Any]:
        """Row delete on the memory screen — and the toast's Undo (§5.1)."""
        if self.memory_store.delete(item_id):
            return {"ok": True, "id": item_id}
        return {"ok": False, "error": f"no memory with id {item_id}"}

    def delete_all_memory(self) -> dict[str, Any]:
        return {"ok": True, "deleted": self.memory_store.delete_all()}

    def get_memory_settings(self) -> dict[str, Any]:
        return self.memory_settings.snapshot()

    def set_memory_settings(
        self, enabled: Optional[bool] = None, user_rules: Optional[str] = None
    ) -> dict[str, Any]:
        return self.memory_settings.set(enabled=enabled, user_rules=user_rules)


def _parse_inbox_json(s: str) -> dict[str, Any]:
    """Parse a structured Inbox resolution (directory/plan carry their reply as a JSON string)."""
    import json as _json

    try:
        v = _json.loads(s) if s else {}
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _epoch() -> float:
    import time

    return time.time()


# A Slack message ts looks like "1700000001.000001" (epoch seconds + microseconds). Other
# platforms use opaque/incrementing ids (e.g. a Telegram integer), so only parse the Slack shape.
_SLACK_TS_RE = re.compile(r"^\d+\.\d+$")


def _inbound_epoch(message_id: Optional[str]) -> float:
    """Best-effort epoch-seconds for a MessageSource: a Slack-style ts, else wall-clock now."""
    if message_id and _SLACK_TS_RE.match(str(message_id)):
        try:
            return float(message_id)
        except ValueError:
            pass
    return time.time()


def _last_assistant_text(messages: list[dict[str, Any]]) -> Optional[str]:
    for msg in reversed(messages or []):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"]
    return None


def _recent_files(workspace: str, *, since: float, limit: int = 20) -> list[str]:
    """Files in the task workspace modified during the run — the run's artifacts."""
    out: list[str] = []
    root = Path(workspace)
    if not root.is_dir():
        return out
    for path in root.rglob("*"):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            if path.is_file() and path.stat().st_mtime >= since - 1:
                out.append(str(path.relative_to(root)))
        except OSError:
            continue
        if len(out) >= limit:
            break
    return out


def _artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".xlsx", ".xls"}:
        return "sheet"
    if suffix in {".pptx", ".ppt", ".pptm", ".docx", ".doc", ".docm"}:
        return "office"
    if suffix in {".csv", ".tsv"}:
        return "csv"
    if suffix in {".py", ".js", ".ts", ".tsx", ".css", ".json"}:
        return "code"
    return "text"


def _redact(raw: dict[str, Any]) -> dict[str, Any]:
    """Copy of a server config safe to return over REST — env/header values masked."""
    out = dict(raw)
    for key in ("env", "headers"):
        if isinstance(out.get(key), dict):
            out[key] = {k: ("***" if v else v) for k, v in out[key].items()}
    return out


def _git_branch(path: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=3,
        )
        branch = result.stdout.strip()
        return branch or None
    except (OSError, subprocess.SubprocessError):
        return None
