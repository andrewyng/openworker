"""TurnEngine — the owned agent loop.

Async, but with blocking provider/tool calls wrapped in `asyncio.to_thread` so the loop
(and any UI consuming its events) stays responsive. One user turn spans many model↔tool
iterations until the model stops requesting tools, a rail trips, or it's interrupted.
When the model requests several tool calls in one turn, low-risk ones (reads, searches)
execute concurrently; writes/shell stay strictly ordered.

Approvals are handled out-of-band via an injected async `approver`: when the permission
engine says `needs_user`, the engine emits `PERMISSION_REQUIRED` and awaits the approver.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from . import compaction as _compaction
from . import provenance
from . import session_facts
from . import toolchain as _toolchain
from .events import Event, EventType
from .permissions import Mode, PermissionEngine
from .providers import AssistantTurn, ProviderClient, ToolCall
from .providers.errors import friendly_model_error
from .tools import ToolRegistry


class ApprovalOutcome(str, Enum):
    ONCE = "once"
    ALWAYS_TOOL = "always_tool"
    ALWAYS_COMMAND = "always_command"
    ALWAYS_DOMAIN = "always_domain"
    # Session-wide grant for classifier-approved read-only shell commands (readonly.py).
    READONLY_SESSION = "readonly_session"
    DENY = "deny"


def _readonly_ok(arguments: dict) -> bool:
    command = str((arguments or {}).get("command", "") or "")
    if not command:
        return False
    from .readonly import is_readonly_command

    return is_readonly_command(command)


@dataclass
class PermissionRequest:
    tool_name: str
    arguments: dict[str, Any]
    metadata: Any
    reason: str
    tool_call_id: Optional[str] = None  # for durable resume (idempotent inbox item)


Approver = Callable[[PermissionRequest], Awaitable[ApprovalOutcome]]


async def _deny_all(_request: PermissionRequest) -> ApprovalOutcome:
    return ApprovalOutcome.DENY


class TurnEngine:
    def __init__(
        self,
        *,
        provider: ProviderClient,
        registry: ToolRegistry,
        permissions: PermissionEngine,
        model: str,
        instructions: Optional[str] = None,
        approver: Optional[Approver] = None,
        max_iterations: int = 12,
        model_settings: Optional[dict[str, Any]] = None,
        messages: Optional[list[dict[str, Any]]] = None,
        audit_sink: Optional[Callable[[dict[str, Any]], None]] = None,
        context_provider: Optional[Callable[[], str]] = None,
        directory_requester: Optional[
            Callable[[dict[str, Any]], "Awaitable[dict[str, Any]]"]
        ] = None,
        plan_approver: Optional[
            Callable[[dict[str, Any]], "Awaitable[dict[str, Any]]"]
        ] = None,
        question_asker: Optional[
            Callable[[dict[str, Any]], "Awaitable[dict[str, Any]]"]
        ] = None,
        tool_requester: Optional[
            Callable[[dict[str, Any]], "Awaitable[dict[str, Any]]"]
        ] = None,
        team_approver: Optional[
            Callable[[dict[str, Any]], "Awaitable[dict[str, Any]]"]
        ] = None,
        items_approver: Optional[
            Callable[[dict[str, Any]], "Awaitable[dict[str, Any]]"]
        ] = None,
        # Called (thread-safe, best-effort) when the user stops the turn — e.g. the
        # executor's kill for a running shell command.
        interrupt_hooks: Optional[list[Callable[[], None]]] = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.permissions = permissions
        self.model = model
        self.approver = approver or _deny_all
        self.max_iterations = max_iterations
        self.model_settings = dict(model_settings or {})
        self.messages: list[dict[str, Any]] = list(messages or [])
        self.audit_sink = audit_sink
        # Returns an ephemeral `<system-context>` block appended to the LAST user message at
        # send-time only (never persisted). We can't reliably inject system messages mid-thread
        # across providers, so dynamic per-turn context (e.g. the live directory list) rides on
        # the latest user turn. Returns "" when there's nothing to add.
        self.context_provider = context_provider
        # Handles the `request_directory` tool: emits a DIRECTORY_REQUESTED prompt, waits for the
        # user to grant/decline a folder out-of-band, applies the grant to this live session, and
        # returns the outcome. None on surfaces that can't prompt (the tool then no-ops).
        self.directory_requester = directory_requester
        # Handles the `request_tool` tool: emits TOOL_REQUESTED, waits for the user to install
        # the pinned build or decline. None on surfaces that can't prompt (the tool then
        # no-ops, and the agent is told so it can fall back openly rather than skip silently).
        self.tool_requester = tool_requester
        # Handles the `propose_plan` tool: emits PLAN_PROPOSED, waits for the user's decision.
        # An approving result flips the live PermissionEngine out of plan mode (same session,
        # context kept). None on surfaces that can't prompt (the tool then no-ops).
        self.plan_approver = plan_approver
        # Handles the `propose_team` tool (the staffing gate): emits TEAM_PROPOSED, waits
        # for the user's decision; approval pre-spawns the worker sessions and the result
        # carries the roster (actor ids). None on surfaces that can't prompt.
        self.team_approver = team_approver
        # Handles `propose_work_items` (the decomposition gate): emits ITEMS_PROPOSED,
        # waits; approval creates the items on the board. Mode-independent by design —
        # unlike propose_plan it carries no permission-mode semantics: propose_plan is
        # an IMPLEMENTATION plan (steps/files, plan-mode exit); this is a team
        # decomposition onto the board.
        self.items_approver = items_approver
        # Handles the `ask_user` tool: turns a question into an Inbox item and waits for the answer
        # (answerable inline in a live session or from the Inbox when unattended). None on surfaces
        # that can't ask (the tool then no-ops).
        self.question_asker = question_asker
        # Auto-compaction (OPE-27) — set post-construction by the surface/manager so the
        # constructor footprint stays put. `compaction_settings` is a live getter (Settings
        # changes apply without a rebuild); `is_attended` gates the failure prompt (None →
        # treat as unattended: never park a background run on internal bookkeeping).
        self.compaction_state: Optional[_compaction.CompactionState] = None
        self.compaction_settings: Optional[Callable[[], dict[str, Any]]] = None
        self.is_attended: Optional[Callable[[], bool]] = None
        # Session facts (spec Part 0 / §2.4) — the known world frozen at session start, plus
        # the per-turn ingestion record. Set post-construction by the surface, same as
        # compaction above, so the constructor footprint stays put. None ⇒ nothing recorded
        # and behaviour is byte-identical; NOTHING consumes it in v1 either way.
        self.session_facts: Optional[session_facts.SessionFacts] = None
        # Auto-Approve reviewer (spec Part 8). Set post-construction; None ⇒ Mode.AUTO_APPROVE
        # behaves exactly like INTERACTIVE. Consulted only on decisions the gate marked
        # needs_user, only in AUTO_APPROVE mode, only when the session is attended (an
        # unset is_attended counts as NOT attended here — automations never set it), and
        # only until two denials in a turn (§8.4 retry guard).
        self.reviewer: Optional[Any] = None
        self._reviewer_denials = 0
        self._reviewer_verdicts: dict[str, Any] = {}
        # Shadow evaluation (spec Part 6 step 3): when True and a reviewer is attached, the
        # reviewer records what it WOULD have decided on each approval card while the human
        # still decides. Fire-and-forget — the card is never delayed, no decision is ever
        # touched, and the verdict lands in the audit log (stage="reviewer_shadow", joined
        # to the human's approval_resolved row by call_id).
        self.reviewer_shadow = False
        self._shadow_tasks: set[asyncio.Task] = set()
        # One-shot "Allow anyway" grants (§8.4): minted ONLY by a human clicking the deny
        # card, keyed on the exact tool + canonical arguments, consumed on first match. A
        # re-proposal with even slightly different arguments does not match and goes back
        # through the reviewer/card — deliberately narrow, deliberately not standing.
        self._allow_anyway: set[tuple[str, str]] = set()
        # ask_user answers for the reviewer's history (§8.2 — the missing third of the
        # reply-tag feature: render_history prints the tag and the §8.3 instructions say to
        # weigh it lower; this is the extractor that finally delivers the data). Captured at
        # the moment the asker returns — the one point where the engine KNOWS the text came
        # from the human, whichever authenticated surface answered (inline card, Inbox, or a
        # bound channel; the same trust approval clicks already carry). ANSWERS ONLY, never
        # the agent's question: agent-authored text stays out of the judge's view — showing
        # the question too is step 2, evidence-gated on shadow data. Each entry is
        # (anchor, text) where anchor = how many user messages existed at capture, so the
        # merge in `_user_history` stays chronological. Runtime-only on purpose: a restart
        # costs the reviewer context (more cards), never correctness.
        self._ask_replies: list[tuple[int, str]] = []
        # Extra user-facing fields for a tool's approval card, merged into the
        # PERMISSION_REQUIRED payload — e.g. web_search's live provider name, so the card
        # can say where queries actually go (§1.9). Set post-construction by the surface
        # (the engine itself knows nothing about providers); None ⇒ no extras. Called at
        # card time, not session start, so a mid-session Settings change shows through.
        self.approval_extras: Optional[
            Callable[[str, dict[str, Any]], dict[str, Any]]
        ] = None
        # What the agent itself created this session (OPE-114 §1). The reviewer never sees
        # file contents, so `python scripts/setup.py` is unjudgeable from its text — but the
        # engine knows whether it wrote or downloaded that file moments ago, and says so on
        # the card and in the reviewer's request. Runtime-only, like `_ask_replies`: a
        # restart costs context (more cards), never correctness.
        self._agent_files = provenance.SessionFiles(permissions.workspace_root)
        # Completed tool calls so far, so a fact can say how many steps back the write was.
        self._step = 0
        self._last_context_tokens: Optional[int] = None
        self.audit_context: dict[str, Any] = {}
        if instructions and not (
            self.messages and self.messages[0].get("role") == "system"
        ):
            self.messages.insert(0, {"role": "system", "content": instructions})
        self._cancel = asyncio.Event()
        # Whether the latest assistant turn hit the output-token limit — decides which
        # diagnosis a mangled (unparseable-args) tool call gets answered with.
        self._turn_truncated = False
        # Each pending steering message: (text, optional MessageSource sidecar dict).
        self._steering: list[tuple[str, Optional[dict[str, Any]]]] = []
        # tool_call.id → the standing rule that auto-allowed it ("tool → target"), so the
        # TOOL_FINISHED event can carry the note to the tool card (§25).
        self._standing_notes: dict[str, str] = {}
        self._interrupt_hooks: list[Callable[[], None]] = list(interrupt_hooks or [])

    # -- external controls ------------------------------------------------------
    def request_interrupt(self) -> None:
        """Stop the turn as soon as possible, from ANY state: mid-stream (the producer
        thread drops the stream between chunks), mid-tool (interrupt hooks kill the
        running command), awaiting an approval/question/plan (the await resolves as
        interrupted), or between iterations (the loop checkpoint). Every pending
        tool_call still gets a tool-error result so the history never carries orphans
        (hosted templates reject them, and durable-resume would re-prompt them)."""
        self._cancel.set()
        for hook in self._interrupt_hooks:
            try:
                hook()
            except Exception:
                pass  # best-effort: a dead executor must not block the stop

    async def _interruptible(self, coro: Any, interrupted: Any) -> Any:
        """Await `coro`, but resolve early with `interrupted` if the user stops the
        turn. The pending task is cancelled so an answered-later Inbox card no-ops."""
        task = asyncio.ensure_future(coro)
        cancel_wait = asyncio.ensure_future(self._cancel.wait())
        try:
            done, _ = await asyncio.wait(
                {task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                return task.result()
            task.cancel()
            return interrupted
        finally:
            cancel_wait.cancel()

    def queue_steering(
        self, text: str, source: Optional[dict[str, Any]] = None
    ) -> None:
        self._steering.append((text, source))

    # -- main loop --------------------------------------------------------------
    async def run(
        self,
        user_input: "str | list",
        *,
        source: Optional[dict[str, Any]] = None,
        display: Optional[str] = None,
    ) -> AsyncIterator[Event]:
        # `user_input` is a string, or OpenAI content-parts (text + image_url) for attachments.
        # `source` (a MessageSource dict) is a display-only sidecar for connector messages: it
        # rides on the persisted user message + the TURN_START event, but is stripped before the
        # message reaches a provider (see `_outbound_messages`). `content` stays the framed text.
        # `display` is the same split for force-run skills (SKILLS-SPEC §4.1 #3): the user's
        # literal "/skill …" line for the transcript, while `content` carries the model-facing
        # framing. `ts` (unix seconds, stamped on every appended message) is the same kind of
        # sidecar.
        message: dict[str, Any] = {
            "role": "user",
            "content": user_input,
            "ts": time.time(),
        }
        if source is not None:
            message["source"] = source
        if display is not None:
            message["_display"] = display
        self.messages.append(message)
        self._cancel.clear()
        if self.session_facts is not None:
            self.session_facts.begin_turn()
        # §8.4 retry guard resets per user turn: two reviewer denials in one turn route
        # everything else that turn to the human. A fresh user message is a fresh brief.
        self._reviewer_denials = 0
        self._reviewer_verdicts.clear()
        data: dict[str, Any] = {"input": user_input}
        if source is not None:
            data["source"] = source
        if display is not None:
            data["display"] = display
        yield Event(EventType.TURN_START, data)
        async for event in self._loop():
            yield event

    def switch_model(self, model: str) -> Optional[str]:
        """Rebind the session's model mid-conversation (roadmap item 3). History is
        canonical OpenAI shape and every provider converts per call, so the switch is just
        the field write — plus a persisted notice marking WHERE it happened, with a
        degradation warning when history carries images the new model can't see (those are
        sent as placeholders — see `_outbound_messages`). Returns the notice text, or None
        when nothing changed (same model, or first bind on a fresh session)."""
        if not model or model == self.model:
            return None
        had_history = any(m.get("role") != "system" for m in self.messages)
        self.model = model
        # The reviewer judges with the session's own model (§1.5: "if it's trusted to
        # drive the agent, it's strong enough to review it"). Bound once at session build,
        # it would otherwise keep the OLD model for the rest of the session after a
        # switch — silently reviewing with a model the user moved away from.
        if self.reviewer is not None:
            self.reviewer.model = model
        if not had_history:
            return None
        from .providers.matrix import model_labels

        text = f"Model switched to {model_labels().get(model, model)}"
        try:
            caps = self.provider.capabilities(model)
        except Exception:
            caps = None
        if (
            caps is not None
            and not getattr(caps, "vision", False)
            and self._history_has_images()
        ):
            text += " — earlier images can't be read by this model"
        self._append_notice("model_switch", text)
        return text

    def _history_has_images(self) -> bool:
        return any(
            isinstance(p, dict) and p.get("type") == "image_url"
            for msg in self.messages
            if isinstance(msg.get("content"), list)
            for p in msg["content"]
        )

    def _tail_is_retriable_error(self) -> bool:
        """True when the history tail is an error notice, looking through any model_switch
        notices appended after it (a switch must not consume the retry)."""
        for message in reversed(self.messages):
            if message.get("role") != "notice":
                return False
            if message.get("kind") == "model_switch":
                continue
            return message.get("kind") == "error"
        return False

    def _append_notice(self, kind: str, text: Optional[str] = None, **fields: Any) -> None:
        """Persist a turn-ending marker (error/interrupted) as a display-only `notice`
        message: it survives reload like the transcript does, but `_outbound_messages`
        drops the role so no provider ever sees it. Extra `fields` (e.g. the failing
        MCP server's name) persist on the message for structured rendering."""
        notice: dict[str, Any] = {"role": "notice", "kind": kind, "ts": time.time()}
        if text:
            notice["text"] = text
        notice.update({k: v for k, v in fields.items() if v is not None})
        self.messages.append(notice)

    async def retry(self) -> AsyncIterator[Event]:
        """Re-run the model loop after a provider error — no new user message; the failed
        turn's input is already the tail of history. Guarded on the tail being an error
        notice so a stray retry frame can't re-answer a completed turn. Trailing
        model_switch notices don't break the guard — switching models and THEN retrying
        is the intended recovery path (owner-hit 2026-07-23)."""
        if not self._tail_is_retriable_error():
            return
        self._cancel.clear()
        yield Event(EventType.TURN_START, {"input": ""})
        async for event in self._loop():
            yield event

    async def resume(self) -> AsyncIterator[Event]:
        """Continue a turn that was suspended at a prompt and persisted — durable resume after a
        restart (or engine eviction). Re-process the trailing assistant message's UNANSWERED
        tool-calls (the prompt callbacks find the already-resolved Inbox item and return without
        re-prompting; answered calls are skipped, so nothing double-executes), then run the model
        loop to finish the turn."""
        pending = self._unanswered_trailing_tool_calls()
        if not pending:
            return
        self._cancel.clear()
        yield Event(EventType.TURN_START, {"input": "(resumed)"})
        async for event in self._handle_tool_calls(pending):
            yield event
        yield Event(EventType.ITERATION_END, {"iteration": 0})
        if not self._cancel.is_set():
            async for event in self._loop():
                yield event

    def _unanswered_trailing_tool_calls(self) -> list[ToolCall]:
        """The tool-calls of the last assistant message that don't yet have a tool result —
        i.e. the prompt we suspended on (+ any after it). Reconstructed from the persisted thread.
        """
        answered = {
            m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"
        }
        for msg in reversed(self.messages):
            if msg.get("role") == "user":
                return []
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                out: list[ToolCall] = []
                for tc in msg["tool_calls"]:
                    if tc.get("id") in answered:
                        continue
                    fn = tc.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    out.append(
                        ToolCall(id=tc.get("id"), name=fn.get("name"), arguments=args)
                    )
                return out
        return []

    async def _loop(self) -> AsyncIterator[Event]:
        iterations = 0
        while True:
            if iterations >= self.max_iterations:
                yield Event(
                    EventType.TURN_END,
                    {"status": "max_iterations_exceeded", "iterations": iterations},
                )
                return
            iterations += 1

            # Auto-compaction checkpoint (OPE-27): between tool turns and before a new
            # turn's first call. Deliberately no "wrap up" warning to the model. The
            # COMPACTING signal precedes the (multi-second) summarizer call so surfaces
            # can show progress instead of a silent stall.
            notice = None
            if self._compaction_due():
                yield Event(EventType.COMPACTING, {})
                notice = await self._compact_now()
            if notice:
                self._append_notice("compacted", notice)
                yield Event(EventType.COMPACTED, {"text": notice})

            turn: Optional[AssistantTurn] = None
            streamed: list[str] = []
            streamed_reasoning: list[str] = []

            def _partial_turn() -> AssistantTurn:
                # What the user watched arrive — text and thinking, NO tool calls (any
                # half-formed calls would either orphan or execute against the stop).
                return AssistantTurn(
                    text="".join(streamed) or None,
                    reasoning="".join(streamed_reasoning) or None,
                )

            try:
                async for chunk in self._astream():
                    if chunk.reasoning_delta:
                        streamed_reasoning.append(chunk.reasoning_delta)
                        yield Event(
                            EventType.REASONING_DELTA, {"text": chunk.reasoning_delta}
                        )
                    if chunk.text_delta:
                        streamed.append(chunk.text_delta)
                        yield Event(
                            EventType.ASSISTANT_DELTA, {"text": chunk.text_delta}
                        )
                    if chunk.turn is not None:
                        turn = chunk.turn
            except Exception as exc:  # provider failure
                # A raw context-overflow 400 (compaction mispredicted, e.g. the estimate
                # path) routes into the compaction policy instead of surfacing. The retry
                # is progress-guarded: each pass moves the boundary forward or gives up,
                # so a model that keeps overflowing still terminates in the error path.
                if _compaction.is_context_overflow(exc) and not self._cancel.is_set():
                    yield Event(EventType.COMPACTING, {})
                    notice = await self._compact_now(force=True)
                    if notice:
                        self._append_notice("compacted", notice)
                        yield Event(EventType.COMPACTED, {"text": notice})
                        continue
                # Same contract as the stop path below: the partial the user watched
                # arrive survives the failure.
                if streamed or streamed_reasoning:
                    self.messages.append(_assistant_message(_partial_turn()))
                friendly = friendly_model_error(self.model, exc)
                payload = {
                    "error": friendly or str(exc),
                    "error_type": type(exc).__name__,
                }
                if friendly:
                    payload["raw"] = str(exc)
                self._append_notice("error", friendly or str(exc))
                yield Event(EventType.ERROR, payload)
                return
            if self._cancel.is_set() and turn is None:
                # Stopped mid-stream: persist exactly what the user watched arrive.
                if streamed or streamed_reasoning:
                    self.messages.append(_assistant_message(_partial_turn()))
                self._append_notice("interrupted")
                yield Event(EventType.INTERRUPTED, {"iterations": iterations})
                return
            if turn is None:
                turn = AssistantTurn()
            if turn.usage is not None:
                # The trigger signal: the prompt-side total that actually occupied the
                # window on this round-trip (estimate fallback when never reported).
                self._last_context_tokens = turn.usage.context_tokens

            self._turn_truncated = turn.finish_reason == "length"
            _sanitize_mangled_calls(turn)
            self.messages.append(_assistant_message(turn, model=self.model))
            payload: dict[str, Any] = {
                "text": turn.text,
                "tool_calls": [tc.name for tc in turn.tool_calls],
            }
            if turn.reasoning:
                payload["reasoning"] = turn.reasoning
            if turn.usage is not None:
                payload["usage"] = {"model": self.model, **turn.usage.as_dict()}
            yield Event(EventType.ASSISTANT_MESSAGE, payload)

            if not turn.tool_calls:
                if self._steering:
                    self._inject_steering()
                    continue
                yield Event(
                    EventType.TURN_END,
                    {"status": "completed", "iterations": iterations},
                )
                return

            async for event in self._handle_tool_calls(turn.tool_calls):
                yield event

            yield Event(EventType.ITERATION_END, {"iteration": iterations})

            if self._cancel.is_set():
                self._append_notice("interrupted")
                yield Event(EventType.INTERRUPTED, {"iterations": iterations})
                return
            if self._steering:
                self._inject_steering()

    # -- auto-compaction (OPE-27) ------------------------------------------------
    def _compaction_config(self) -> dict[str, Any]:
        cfg = dict(self.compaction_settings() or {}) if self.compaction_settings else {}
        if not cfg.get("context_window"):
            from .providers.matrix import model_context_windows

            cfg["context_window"] = model_context_windows().get(self.model)
        cfg.setdefault("threshold_pct", _compaction.DEFAULT_THRESHOLD_PCT)
        cfg.setdefault("cap_tokens", _compaction.DEFAULT_CAP_TOKENS)
        return cfg

    def _compaction_due(self) -> bool:
        """The trigger check alone — cheap and side-effect free, so the loop can emit
        the COMPACTING signal before committing to the (slow) summarizer call."""
        cfg = self._compaction_config()
        if cfg.get("enabled") is False:
            return False
        signal = self._last_context_tokens or _compaction.estimate_tokens(
            self._outbound_messages()
        )
        return _compaction.should_compact(
            signal,
            cfg.get("context_window"),
            threshold_pct=float(cfg["threshold_pct"]),
            cap_tokens=int(cfg["cap_tokens"]),
        )

    async def _compact_now(self, *, force: bool = False) -> Optional[str]:
        """Run the compaction policy. Callers gate on `_compaction_due()` (or `force`,
        the overflow path). Returns the user-facing notice text when the outbound view
        changed, else None. Failure policy per spec: retry once (both modes); attended →
        Retry / Trim prompt; unattended → auto-trim and continue (never park a run on
        bookkeeping)."""
        cfg = self._compaction_config()
        pct = float(cfg["threshold_pct"])
        cap = int(cfg["cap_tokens"])
        window = cfg.get("context_window")
        keep = int(
            _compaction.KEEP_RECENT_FRACTION
            * _compaction.trigger_tokens(window, threshold_pct=pct, cap_tokens=cap)
        )
        model = str(cfg.get("model") or "") or self.model

        def _build() -> Optional[_compaction.CompactionState]:
            return _compaction.build_state(
                self.messages,
                provider=self.provider,
                model=model,
                keep_tokens=keep,
                prior=self.compaction_state,
            )

        state: Optional[_compaction.CompactionState] = None
        failed = False
        for _attempt in range(2):  # first try + the unconditional single retry
            try:
                state = await asyncio.to_thread(_build)
                failed = False
                break
            except Exception:
                failed = True
        if failed and self.question_asker is not None and self.is_attended and self.is_attended():
            while True:
                answer = await self._interruptible(
                    self.question_asker(
                        {
                            "question": (
                                "Context compaction failed — the summarizer couldn't "
                                "condense this session's history. How should I proceed?"
                            ),
                            "options": ["Retry", "Trim oldest 10%"],
                            "allow_text": False,
                            "header": "Compaction",
                        },
                        None,
                    ),
                    interrupted=None,
                )
                if not answer or answer.get("answer") != "Retry":
                    break
                try:
                    state = await asyncio.to_thread(_build)
                    failed = False
                    break
                except Exception:
                    continue
        if state is not None:
            self.compaction_state = state
            self._last_context_tokens = None  # stale once the outbound view shrank
            return "Context compacted — earlier turns were summarized"
        if failed or force:
            trimmed = _compaction.trim_state(self.messages, prior=self.compaction_state)
            if trimmed is not None:
                self.compaction_state = trimmed
                self._last_context_tokens = None
                return "Context trimmed — oldest turns dropped (summary unavailable)"
        return None

    # -- helpers ----------------------------------------------------------------
    async def _astream(self):
        """Bridge the provider's blocking stream generator to the async loop via a
        thread + queue, so text deltas surface live without blocking the event loop."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        tools = self.registry.schemas() or None
        model, messages, settings = (
            self.model,
            self._outbound_messages(),
            self.model_settings,
        )
        provider = self.provider

        def produce():
            try:
                for chunk in provider.stream(
                    model=model, messages=messages, tools=tools, **settings
                ):
                    # User pressed Stop: drop the stream between chunks (reading the
                    # asyncio.Event's flag from a thread is safe; we only read).
                    if self._cancel.is_set():
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
            except Exception as exc:  # surfaced to the awaiting consumer
                loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        loop.run_in_executor(None, produce)
        while True:
            # Race the queue against Stop so a stalled stream (no chunks arriving —
            # the pre-first-token wait, a wedged connection) can't hold the turn.
            get_task = asyncio.ensure_future(queue.get())
            cancel_task = asyncio.ensure_future(self._cancel.wait())
            done, _ = await asyncio.wait(
                {get_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
            )
            cancel_task.cancel()
            if get_task not in done:
                get_task.cancel()
                return  # interrupted — the producer exits on its own next chunk
            kind, payload = get_task.result()
            if kind == "chunk":
                yield payload
            elif kind == "error":
                raise payload
            else:
                return

    async def _handle_tool_calls(
        self, tool_calls: list[ToolCall]
    ) -> AsyncIterator[Event]:
        """Run one assistant turn's tool calls: authorize all of them first (sequentially —
        approval prompts are interactive), then execute. Low-risk calls (reads, searches)
        run concurrently; everything else runs one at a time in call order."""
        # Auto-Approve: fire the reviewer for every call that will need it, all at once,
        # BEFORE the sequential authorize loop (spec §8.6 — one action per request, sent
        # concurrently; the wall-clock cost of reviewing N calls is one round-trip, and a
        # verdict physically cannot land on the wrong action). The loop below stays
        # sequential because approval cards are interactive and must reach the human one
        # at a time, in call order.
        await self._preconsult_reviewer(tool_calls)
        cleared: list[ToolCall] = []
        for tool_call in tool_calls:
            if self._cancel.is_set():
                # Stopped: every remaining call still gets an answer (no orphans).
                yield self._interrupted_tool(tool_call)
                continue
            yield Event(
                EventType.TOOL_PROPOSED,
                {"name": tool_call.name, "arguments": tool_call.arguments},
            )
            self._audit(tool_call, stage="proposed")
            if _is_mangled(tool_call):
                # The arguments never parsed as JSON (a `{"_raw": …}` fallback from the
                # provider). Executing would produce a bare parameter error the model
                # misreads — seen in the field as an endless "wrong parameter" retry
                # loop. Answer with the ACTUAL diagnosis instead.
                yield self._mangled_tool(tool_call)
                continue
            # `request_directory` and `propose_plan` are interactive: the user decides
            # out-of-band and that decision IS the consent, so they skip the
            # permission/registry path.
            if tool_call.name == "request_directory":
                async for event in self._handle_directory_request(tool_call):
                    yield event
                continue
            if tool_call.name == "request_tool":
                async for event in self._handle_tool_request(tool_call):
                    yield event
                continue
            if tool_call.name == "propose_plan":
                async for event in self._handle_plan_proposal(tool_call):
                    yield event
                continue
            if tool_call.name == "propose_team":
                async for event in self._handle_team_proposal(tool_call):
                    yield event
                continue
            if tool_call.name == "propose_work_items":
                async for event in self._handle_items_proposal(tool_call):
                    yield event
                continue
            if tool_call.name == "ask_user":
                async for event in self._handle_ask_user(tool_call):
                    yield event
                continue
            allowed = False
            async for item in self._authorize(tool_call):
                if isinstance(item, Event):
                    yield item
                else:
                    allowed = item
            if allowed:
                cleared.append(tool_call)

        concurrent = (
            [tc for tc in cleared if self._parallel_safe(tc)]
            if len(cleared) > 1
            else []
        )
        serial = [tc for tc in cleared if tc not in concurrent]

        if concurrent:
            for tool_call in concurrent:
                yield Event(EventType.TOOL_STARTED, {"name": tool_call.name})
                self._audit(tool_call, stage="started")
            outcomes = await asyncio.gather(
                *[asyncio.to_thread(self._execute_sync, tc) for tc in concurrent]
            )
            for tool_call, (result, status) in zip(concurrent, outcomes):
                yield self._record_result(tool_call, result, status)

        for tool_call in serial:
            if self._cancel.is_set():
                yield self._interrupted_tool(tool_call)
                continue
            yield Event(EventType.TOOL_STARTED, {"name": tool_call.name})
            self._audit(tool_call, stage="started")
            result, status = await asyncio.to_thread(self._execute_sync, tool_call)
            yield self._record_result(tool_call, result, status)

    def _mangled_tool(self, tool_call: ToolCall) -> Event:
        """Answer a tool call whose arguments never parsed, with the real diagnosis.

        Two causes, two different cures — and the model can only pick the right one if
        the error says which happened. Truncation (`finish_reason == "length"`) means
        "same content, smaller pieces"; plain bad JSON means "re-send with the declared
        parameters". Either way the raw text is NOT replayed into history: a stored
        `{"_raw": …}` call reads as a worked example and teaches the model to emit
        `_raw` on purpose (observed 2026-08-15), on top of re-sending the junk tokens
        every turn."""
        if self._turn_truncated:
            reason = (
                "your tool-call arguments were cut off by the output-token limit before "
                "they finished streaming — the tool never received them. Produce the same "
                "content in smaller pieces: several calls that each write or append a "
                "section, keeping each call's content well under the limit. Do not retry "
                "the identical oversized call."
            )
        else:
            reason = (
                "your tool-call arguments did not parse as a JSON object, so the tool "
                "received nothing. `_raw` is not a parameter — it is the unparsed text of "
                "the failed call. Re-issue the call using the tool's declared parameters."
            )
        self.messages.append(_tool_error_message(tool_call, reason))
        self._audit(tool_call, stage="finished", status="error", reason=reason)
        return Event(
            EventType.TOOL_FINISHED,
            {"name": tool_call.name, "status": "error", "reason": reason},
        )

    def _interrupted_tool(self, tool_call: ToolCall) -> Event:
        """The stop-path answer for a call that will not run: a tool-error result in the
        history (hosted chat templates reject orphaned tool_calls, and durable-resume
        would otherwise re-prompt it) + the finished event for the tool card."""
        self.messages.append(_tool_error_message(tool_call, "interrupted by user"))
        self._audit(
            tool_call, stage="finished", status="interrupted", reason="user stop"
        )
        return Event(
            EventType.TOOL_FINISHED,
            {"name": tool_call.name, "status": "interrupted", "reason": "stopped"},
        )

    def _parallel_safe(self, tool_call: ToolCall) -> bool:
        # Only metadata-declared low-risk tools (reads, searches, git queries) run
        # concurrently; writes, shell, and anything unannotated stay strictly ordered.
        spec = self.registry.get(tool_call.name)
        metadata = spec.metadata if spec else None
        return getattr(metadata, "risk_level", "") == "low" and not getattr(
            metadata, "requires_approval", False
        )

    # -- Auto-Approve reviewer (spec Part 8) ----------------------------------------

    def _reviewer_active(self) -> bool:
        """The reviewer is consulted only when ALL of these hold. Any miss ⇒ today's
        behaviour (the card). Attended is required explicitly: `is_attended` unset counts
        as NOT attended, so automations — which never set it — can never be reviewed
        (§1.5: the mode is attended-only)."""
        from .permissions import Mode

        return (
            self.reviewer is not None
            and self.permissions.mode is Mode.AUTO_APPROVE
            and self.is_attended is not None
            and self.is_attended()
            and self._reviewer_denials < 2
        )

    def _user_history(self) -> tuple[str, list[dict[str, Any]]]:
        """(current request, earlier user messages) — the user's own words only, extracted
        mechanically (§8.2). Never agent output, never tool results, never a summary.

        `ask_user` answers are merged in from `_ask_replies` (captured as they arrived, not
        parsed out of tool envelopes), tagged `is_reply` so `render_history` prints the
        "[reply to a question the agent asked]" marker the §8.3 instructions already know
        how to weigh. A reply is always HISTORY, never the current request — "ok proceed"
        must not become the headline the action is judged against.

        Attachments collapse to neutral markers via `reviewer_text` (§4.4): the reviewer
        learns a file was attached, never what it says — an attachment body is
        outside-authored text riding a user turn."""
        from .attachments import reviewer_text

        texts: list[str] = []
        for msg in self.messages:
            if msg.get("role") != "user":
                continue
            text = reviewer_text(msg.get("content"))
            if text:
                texts.append(text)
        if not texts:
            return "", [{"text": t, "is_reply": True} for _, t in self._ask_replies]
        history: list[dict[str, Any]] = []
        for i, t in enumerate(texts[:-1], start=1):
            history.append({"text": t})
            history.extend(
                {"text": r, "is_reply": True} for a, r in self._ask_replies if a == i
            )
        # Replies captured during the current turn (anchor == len(texts)) — or after an
        # anchor message that was itself empty/skipped — land at the tail, so a same-turn
        # consent is already visible to the reviewer for the very next action.
        history.extend(
            {"text": r, "is_reply": True}
            for a, r in self._ask_replies
            if a >= len(texts)
        )
        return texts[-1], history

    def _downloaded_target(self, tool_call: ToolCall) -> Optional[Any]:
        """A file this call would run that the agent DOWNLOADED this session, or None.
        Fetch-then-execute has no quiet legitimate form, so it reaches a person over both
        the reviewer and any command allowlist (OPE-114 §1)."""
        match = self._agent_files.match(
            tool_call.name, tool_call.arguments, step=self._step
        )
        return match if match is not None and match.downloaded else None

    def _provenance(self, tool_call: ToolCall) -> str:
        """One line naming a file this call would run that the agent itself created, or ""
        (§8.2). Fixed vocabulary — never file contents, never outside-authored text, so the
        no-untrusted-content rule holds."""
        match = self._agent_files.match(
            tool_call.name, tool_call.arguments, step=self._step
        )
        return match.render() if match else ""

    async def _preconsult_reviewer(self, tool_calls: list[ToolCall]) -> None:
        """Fire one reviewer request per call that will escalate, all concurrently, and
        park the verdicts for `_authorize` to consume. One action per request — there is
        no verdict list to pair back, so a verdict cannot land on the wrong action (§8.6).
        Skips calls the gate already decides (allow or hard-deny): the reviewer only ever
        sees what would otherwise become an approval card (§1.2)."""
        if not self._reviewer_active() or not tool_calls:
            return
        interactive = {"request_directory", "propose_plan", "ask_user"}
        pending: list[ToolCall] = []
        for tool_call in tool_calls:
            if tool_call.name in interactive or tool_call.id in self._reviewer_verdicts:
                continue
            spec = self.registry.get(tool_call.name)
            if spec is None:
                continue
            decision = self.permissions.evaluate(
                tool_call.name, tool_call.arguments, spec.metadata
            )
            # human_only asks never reach the reviewer — same rule as `_authorize`.
            if (
                not decision.allowed
                and decision.needs_user
                and not decision.human_only
                and self._downloaded_target(tool_call) is None
            ):
                pending.append(tool_call)
        if not pending:
            return
        request, history = self._user_history()
        verdicts = await asyncio.gather(
            *[
                self.reviewer.review(
                    request=request,
                    history=history,
                    tool_name=tc.name,
                    arguments=tc.arguments,
                    provenance=self._provenance(tc),
                )
                for tc in pending
            ]
        )
        for tc, verdict in zip(pending, verdicts):
            self._reviewer_verdicts[tc.id] = verdict

    async def _consult_reviewer(self, tool_call: ToolCall) -> Any:
        """The parked verdict from `_preconsult_reviewer`, or a fresh single call."""
        verdict = self._reviewer_verdicts.pop(tool_call.id, None)
        if verdict is not None:
            return verdict
        request, history = self._user_history()
        return await self.reviewer.review(
            request=request,
            history=history,
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
            provenance=self._provenance(tool_call),
        )

    @staticmethod
    def _action_key(tool_name: str, arguments: dict[str, Any] | None) -> tuple[str, str]:
        try:
            canon = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            canon = str(arguments)
        return (tool_name, canon)

    def approve_action_once(self, tool_name: str, arguments: dict[str, Any] | None) -> None:
        """Register a one-shot human approval for this EXACT action (§8.4 "Allow anyway").

        Called by the server when the user clicks the deny card — a human decision made
        with the full reviewer reason in front of them. The next proposal of the identical
        action (same tool, byte-identical canonical arguments) runs without the reviewer or
        a card; anything that differs at all still goes through the normal flow. Never
        standing: consumed on first use."""
        self._allow_anyway.add(self._action_key(tool_name, arguments))
        if self.audit_sink is not None:
            try:
                self.audit_sink(
                    {
                        **self.audit_context,
                        "tool": tool_name,
                        "arguments": arguments or {},
                        "stage": "allow_anyway_granted",
                        "status": "granted",
                        "reason": "user approved via the deny card (one-shot, exact action)",
                    }
                )
            except Exception:
                pass

    def _consume_allow_anyway(self, tool_call: ToolCall) -> bool:
        key = self._action_key(tool_call.name, tool_call.arguments)
        if key in self._allow_anyway:
            self._allow_anyway.discard(key)
            return True
        return False

    def _spawn_shadow_review(self, tool_call: ToolCall) -> None:
        """Shadow evaluation (spec Part 6 step 3): record what the reviewer WOULD have
        decided about this card, without touching anything. Fire-and-forget — the card
        renders immediately; the verdict lands in the audit log when the call returns,
        joined to the human's `approval_resolved` row by `call_id`. There is deliberately
        no code path from a shadow verdict to a decision."""
        if self.reviewer is None or not self.reviewer_shadow:
            return
        request, history = self._user_history()
        prov = self._provenance(tool_call)

        async def _shadow() -> None:
            try:
                verdict = await self.reviewer.review(
                    request=request,
                    history=history,
                    provenance=prov,
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                )
                self._audit(
                    tool_call,
                    stage="reviewer_shadow",
                    status=verdict.verdict,
                    reason=verdict.reason,
                    call_id=tool_call.id,
                    tokens_in=verdict.tokens_in,
                    tokens_out=verdict.tokens_out,
                    cache_read=verdict.cache_read,
                    cache_write=verdict.cache_write,
                )
            except Exception:
                pass  # shadow must never surface a failure

        task = asyncio.create_task(_shadow())
        self._shadow_tasks.add(task)
        task.add_done_callback(self._shadow_tasks.discard)

    async def drain_shadow_reviews(self) -> None:
        """Await in-flight shadow verdicts (tests and orderly shutdown; never the hot path)."""
        if self._shadow_tasks:
            await asyncio.gather(*list(self._shadow_tasks), return_exceptions=True)

    async def _authorize(self, tool_call: ToolCall) -> "AsyncIterator[Event | bool]":
        """Permission flow for one call (TOOL_PROPOSED is emitted by the caller). Yields
        its events, then True/False (allowed) last. Denied/unknown calls get their
        tool-error message appended here."""
        from .permissions import standing_rule_candidate

        spec = self.registry.get(tool_call.name)
        metadata = spec.metadata if spec else None

        decision = self.permissions.evaluate(
            tool_call.name, tool_call.arguments, metadata
        )
        allowed = decision.allowed
        reason = decision.reason

        # OPE-114 §1: running something the agent DOWNLOADED this session is the classic
        # fetch-then-execute chain, and there is no quiet legitimate version of it — so it
        # goes to a person, over both the reviewer and any command allowlist that would
        # otherwise wave it through (a `python` prefix rule must not vouch for a script
        # pulled off the internet a moment ago). A hard deny is left untouched: this floor
        # only ever tightens an allow, never loosens a block. Agent-WRITTEN files are not
        # floored — "write this script and run it" is ordinary work — they travel as a fact
        # for the reviewer to weigh instead.
        provenance_note = self._provenance(tool_call)
        if self._downloaded_target(tool_call) is not None and (
            decision.needs_user or allowed
        ):
            allowed = False
            reason = f"this file was downloaded by the agent this session — {provenance_note}"
            decision = replace(
                decision,
                allowed=False,
                reason=reason,
                needs_user=True,
                human_only=True,
            )

        if allowed and decision.rule:
            # A task-scoped standing rule auto-allowed this call: audit the exact rule
            # (§25 invariant — every auto-allowed call cites its rule) and remember it so
            # the tool card can say "allowed by standing rule".
            self._standing_notes[tool_call.id] = decision.rule
            self._audit(
                tool_call, stage="auto_allowed", status="allowed", reason=reason
            )

        if not allowed and decision.needs_user and self._consume_allow_anyway(tool_call):
            # §8.4 "Allow anyway": the human already approved this exact action from the
            # deny card. One-shot — consumed above; a different action never matches.
            allowed = True
            reason = "approved by user (allow anyway)"
            self._audit(tool_call, stage="auto_allowed", status="allowed", reason=reason)

        consulted_live = False
        if (
            not allowed
            and decision.needs_user
            and not decision.human_only
            and self._reviewer_active()
        ):
            # The one thing the reviewer may do: turn "ask the human" into "go ahead" —
            # never "blocked" into "go ahead" (§1.2; hard denies never reach this branch
            # because needs_user is False on them). `human_only` asks (git hooks, CI
            # configs, unscopable writes) skip the reviewer entirely: their floor is that
            # a PERSON sees them, and a verdict here would be that floor's bypass.
            consulted_live = True
            verdict = await self._consult_reviewer(tool_call)
            self._audit(
                tool_call,
                stage="reviewer_verdict",
                status=verdict.verdict,
                reason=verdict.reason,
                tokens_in=verdict.tokens_in,
                tokens_out=verdict.tokens_out,
                cache_read=verdict.cache_read,
                cache_write=verdict.cache_write,
            )
            if verdict.verdict == "allow":
                allowed = True
                reason = f"allowed by reviewer: {verdict.reason}"
            elif verdict.verdict == "deny":
                # §8.4 deny asymmetry — full reason to the USER (event + audit above),
                # terse non-diagnostic refusal to the AGENT. The sanctioned way around a
                # deny is ask the human, never reshape the request.
                from .reviewer import AGENT_DENY_MESSAGE

                self._reviewer_denials += 1
                yield Event(
                    EventType.TOOL_FINISHED,
                    {
                        "name": tool_call.name,
                        "status": "denied",
                        "reason": "blocked by the safety reviewer",
                        "reviewer_reason": verdict.reason,
                        "allow_anyway": True,
                    },
                )
                self.messages.append(
                    _tool_error_message(tool_call, AGENT_DENY_MESSAGE)
                )
                self._audit(
                    tool_call,
                    stage="finished",
                    status="denied",
                    reason=f"denied by reviewer: {verdict.reason}",
                )
                yield False
                return
            # "unsure" falls through to today's card — the human decides.

        if not allowed and decision.needs_user:
            # Shadow evaluation: record what the reviewer would have said about this card.
            # Skipped when the live path already consulted it (an `unsure` falling through
            # to the card is already audited as reviewer_verdict — no double spend).
            if not consulted_live:
                self._spawn_shadow_review(tool_call)
            yield Event(
                EventType.PERMISSION_REQUIRED,
                {
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                    "reason": decision.reason,
                    "category": getattr(metadata, "category", ""),
                    # The exact target a standing rule could pin, or None when the call
                    # isn't eligible (no declared target arg / exec risk). Surfaces use it
                    # to offer "Allow every time" on automation-run approval cards only.
                    # OPE-114 §1: the fact neither the reviewer nor the human could get
                    # from the command text alone.
                    "provenance": provenance_note,
                    "standing_target": standing_rule_candidate(
                        tool_call.name,
                        tool_call.arguments,
                        metadata,
                        self.permissions.risk_overrides,
                    ),
                    # True when this shell command classifies as read-only — the card
                    # offers "Allow read-only commands for this session" only then.
                    "readonly_ok": _readonly_ok(tool_call.arguments),
                    **(
                        self.approval_extras(tool_call.name, tool_call.arguments)
                        if self.approval_extras
                        else {}
                    ),
                },
            )
            self._audit(
                tool_call,
                stage="approval_requested",
                reason=decision.reason,
                call_id=tool_call.id,
            )
            outcome = await self._interruptible(
                self.approver(
                    PermissionRequest(
                        tool_name=tool_call.name,
                        arguments=tool_call.arguments,
                        metadata=metadata,
                        reason=decision.reason,
                        tool_call_id=tool_call.id,
                    )
                ),
                interrupted=ApprovalOutcome.DENY,
            )
            if outcome is ApprovalOutcome.DENY:
                allowed, reason = (
                    False,
                    "interrupted by user" if self._cancel.is_set() else "denied by user",
                )
                self._audit(
                    tool_call,
                    stage="approval_resolved",
                    call_id=tool_call.id,
                    status="denied",
                    approval=outcome.value,
                    reason=reason,
                )
            else:
                if outcome is ApprovalOutcome.ALWAYS_TOOL:
                    self.permissions.allow_tool_for_session(tool_call.name)
                elif outcome is ApprovalOutcome.ALWAYS_COMMAND:
                    self.permissions.allow_command_for_session(
                        str(tool_call.arguments.get("command", ""))
                    )
                elif outcome is ApprovalOutcome.ALWAYS_DOMAIN:
                    self.permissions.allow_domain_for_session(
                        str(tool_call.arguments.get("url", ""))
                    )
                elif outcome is ApprovalOutcome.READONLY_SESSION:
                    self.permissions.allow_readonly_for_session()
                allowed, reason = True, "approved by user"
                self._audit(
                    tool_call,
                    stage="approval_resolved",
                    call_id=tool_call.id,
                    status="approved",
                    approval=outcome.value,
                    reason=reason,
                )

        if not allowed:
            if spec is None:
                reason = f"unknown tool: {tool_call.name}"
            self.messages.append(_tool_error_message(tool_call, reason))
            yield Event(
                EventType.TOOL_FINISHED,
                {"name": tool_call.name, "status": "denied", "reason": reason},
            )
            self._audit(tool_call, stage="finished", status="denied", reason=reason)
            yield False
            return

        if spec is None:
            self.messages.append(
                _tool_error_message(tool_call, f"unknown tool: {tool_call.name}")
            )
            yield Event(
                EventType.TOOL_FINISHED,
                {"name": tool_call.name, "status": "error", "reason": "unknown tool"},
            )
            yield False
            return

        yield True

    def _execute_sync(self, tool_call: ToolCall) -> tuple[Any, str]:
        """Execute one authorized call (runs in a worker thread)."""
        try:
            return self.registry.execute(tool_call.name, tool_call.arguments), "ok"
        except Exception as exc:
            return {"error": str(exc), "error_type": type(exc).__name__}, "error"

    def _record_result(self, tool_call: ToolCall, result: Any, status: str) -> Event:
        self._step += 1
        if status == "ok":
            # Only successful calls: a write that raised left nothing on disk to run.
            self._agent_files.record(
                tool_call.name, tool_call.arguments, result, step=self._step
            )
        # A `_display` key on a tool result is user-facing metadata the AGENT must
        # never see (e.g. how many gmail hits the privacy filters hid — a count
        # the model could probe around). Lift it onto the message as a sidecar
        # (like `source`), stripped from every provider feed in
        # `_outbound_messages` but persisted for the GUI's tool card.
        display: Optional[dict[str, Any]] = None
        if isinstance(result, dict) and "_display" in result:
            display = result.get("_display") or None
            result = {k: v for k, v in result.items() if k != "_display"}
        message = _tool_result_message(tool_call, result)
        if display:
            message["_display"] = display
        self.messages.append(message)
        hidden = int((display or {}).get("hidden_by_filters") or 0)
        stripped = int((display or {}).get("hidden_fields") or 0)
        if hidden or stripped:
            # The out-of-band trace the user CAN see: rule class + count, never content.
            parts = []
            if hidden:
                parts.append(f"{hidden} result(s) hidden")
            if stripped:
                parts.append(f"{stripped} field value(s) stripped")
            self._audit(
                tool_call,
                stage="filtered",
                status="hidden",
                reason=" · ".join(parts) + " by privacy filters",
            )
        self._audit(
            tool_call,
            stage="finished",
            status=status,
            result=result,
            result_preview=_preview(result),
        )
        self._note_ingestion(tool_call, status)
        rule = self._standing_notes.pop(tool_call.id, "")
        return Event(
            EventType.TOOL_FINISHED,
            {
                "name": tool_call.name,
                "status": status,
                "result_preview": _preview(result),
                **({"display": display} if display else {}),
                **({"standing_rule": rule} if rule else {}),
            },
        )

    def _note_ingestion(self, tool_call: ToolCall, status: str) -> None:
        """Record that outside content entered this session, and from where. The fact and
        the source only — never the content, not even truncated.

        **Nothing consumes this in v1.** It exists so that when the reviewer is eventually
        offered the fact (v2, `PRV-1`), the question "would it have changed a verdict?" can
        be answered by replaying a shadow run instead of re-argued. See
        `session_facts.py` and the spec's Part 0.

        Failed calls are skipped: a fetch that errored brought nothing in.
        """
        if self.session_facts is None or status != "ok":
            return
        spec = self.registry.get(tool_call.name)
        if not session_facts.is_ingesting(spec.metadata if spec else None):
            return
        record = self.session_facts.note(tool_call.name, tool_call.arguments)
        self._audit(tool_call, **record.to_audit())

    def _audit(self, tool_call: ToolCall, **event: Any) -> None:
        if self.audit_sink is None:
            return
        payload = {
            **self.audit_context,
            "tool": tool_call.name,
            "arguments": tool_call.arguments,
            **event,
        }
        try:
            self.audit_sink(payload)
        except Exception:
            pass

    async def _handle_items_proposal(self, tool_call: ToolCall) -> AsyncIterator[Event]:
        """The decomposition gate: emit the proposed items, await the user's decision.
        Approval creates them on the board (server-side, inside the approver) and the
        result carries their ids; rejection returns feedback for a revised split."""
        args = tool_call.arguments or {}
        items = args.get("items") or []
        valid = [
            i
            for i in items
            if isinstance(i, dict)
            and str(i.get("title", "")).strip()
            and str(i.get("criteria", "")).strip()
        ]
        if not valid or len(valid) != len(items):
            result: dict[str, Any] = {
                "approved": False,
                "error": "every proposed item needs a title and acceptance criteria",
            }
        elif self.items_approver is None:
            result = {
                "approved": False,
                "error": "item proposals aren't available in this surface",
            }
        else:
            yield Event(
                EventType.ITEMS_PROPOSED,
                {"items": valid, "note": str(args.get("note", ""))},
            )
            self._audit(tool_call, stage="items_proposed")
            result = await self._interruptible(
                self.items_approver(dict(args), tool_call.id),
                interrupted={"approved": False, "error": "interrupted by user"},
            ) or {"approved": False, "error": "no response"}

        status = "ok" if result.get("approved") else "denied"
        self.messages.append(_tool_result_message(tool_call, result))
        self._audit(
            tool_call,
            stage="finished",
            status=status,
            result=result,
            result_preview=_preview(result),
        )
        yield Event(
            EventType.TOOL_FINISHED,
            {
                "name": tool_call.name,
                "status": status,
                "result_preview": _preview(result),
            },
        )

    async def _handle_team_proposal(self, tool_call: ToolCall) -> AsyncIterator[Event]:
        """The staffing gate: emit the proposed roster, await the user's out-of-band
        decision. Approval PRE-SPAWNS the worker sessions (server-side, inside the
        approver) and the result carries the roster with actor ids so the lead can
        assign; rejection returns the user's feedback for a revised proposal."""
        args = tool_call.arguments or {}
        members = args.get("members") or []
        if not isinstance(members, list) or not members:
            result: dict[str, Any] = {
                "approved": False,
                "error": "propose at least one member ({persona, model?, reason?})",
            }
        elif self.team_approver is None:
            result = {
                "approved": False,
                "error": "team staffing isn't available in this surface",
            }
        else:
            yield Event(
                EventType.TEAM_PROPOSED,
                {
                    "members": members,
                    "enable_chat": bool(args.get("enable_chat", False)),
                    "note": str(args.get("note", "")),
                },
            )
            self._audit(tool_call, stage="team_proposed")
            result = await self._interruptible(
                self.team_approver(dict(args), tool_call.id),
                interrupted={"approved": False, "error": "interrupted by user"},
            ) or {"approved": False, "error": "no response"}

        status = "ok" if result.get("approved") else "denied"
        self.messages.append(_tool_result_message(tool_call, result))
        self._audit(
            tool_call,
            stage="finished",
            status=status,
            result=result,
            result_preview=_preview(result),
        )
        yield Event(
            EventType.TOOL_FINISHED,
            {
                "name": tool_call.name,
                "status": status,
                "result_preview": _preview(result),
            },
        )

    async def _handle_plan_proposal(self, tool_call: ToolCall) -> AsyncIterator[Event]:
        """Emit the plan for review, await the user's out-of-band decision, and apply it:
        approval flips the live PermissionEngine out of plan mode (the same session keeps
        going, with all its exploration context); rejection keeps plan mode and returns
        the user's feedback so the agent can revise."""
        args = tool_call.arguments or {}
        plan = str(args.get("plan", ""))
        if self.permissions.mode is not Mode.PLAN:
            # The tool is always registered (mode can flip mid-session), but proposing a
            # plan only means something while the session is actually in plan mode. The
            # right next step differs by mode: discuss stays read-only, so the agent
            # should talk through the change; write-capable modes should just do it.
            if self.permissions.mode is Mode.DISCUSS:
                error = (
                    "not in plan mode — this is discuss mode (read-only), so describe "
                    "the proposed changes in chat instead"
                )
            else:
                error = "not in plan mode — proceed with the work directly"
            result: dict[str, Any] = {"approved": False, "error": error}
        elif self.plan_approver is None:
            result = {
                "approved": False,
                "error": "plan approval isn't available here",
            }
        else:
            yield Event(EventType.PLAN_PROPOSED, {"plan": plan})
            self._audit(tool_call, stage="plan_proposed")
            result = await self._interruptible(
                self.plan_approver(dict(args), tool_call.id),
                interrupted={"approved": False, "error": "interrupted by user"},
            ) or {
                "approved": False,
                "error": "no response",
            }

        if result.get("approved"):
            # The approver may pick the post-plan mode ("interactive" asks per write,
            # "auto" executes the approved plan without further prompts).
            try:
                self.permissions.mode = Mode(str(result.get("mode", "interactive")))
            except ValueError:
                self.permissions.mode = Mode.INTERACTIVE
            result = {
                **result,
                "mode": self.permissions.mode.value,
                "note": "plan approved — implement it now",
            }

        status = "ok" if result.get("approved") else "denied"
        self.messages.append(_tool_result_message(tool_call, result))
        self._audit(
            tool_call,
            stage="finished",
            status=status,
            result=result,
            result_preview=_preview(result),
        )
        yield Event(
            EventType.TOOL_FINISHED,
            {
                "name": tool_call.name,
                "status": status,
                "result_preview": _preview(result),
            },
        )

    async def _handle_tool_request(self, tool_call: ToolCall) -> AsyncIterator[Event]:
        """Emit the install prompt, await the user's decision, hand the outcome back.

        Declining is a normal outcome, not an error: the result tells the agent to fall back
        and disclose the gap, because a security report that quietly loses a check is worse
        than one that says which checks it couldn't run.
        """
        args = tool_call.arguments or {}
        name = str(args.get("name", "")).strip()
        reason = str(args.get("reason", ""))

        if self.tool_requester is None or not name:
            result: dict[str, Any] = {
                "installed": False,
                "error": "tool requests aren't available here",
                "guidance": (
                    "Continue without it: use a fallback check if you have one, and say in "
                    "your report which checks were degraded."
                ),
            }
        elif _toolchain.describe(name) is None:
            # Not in the pinned catalog: no card at all (owner-hit 2026-08-20 — agents
            # routed ordinary brew/pip installs through the install card, which could
            # only fail after approval). The agent has a shell with its own approval
            # flow; steer it there instead of at the user.
            catalog = ", ".join(sorted(_toolchain.MANAGED))
            result = {
                "installed": False,
                "error": (
                    f"'{name}' is not in the pinned tool catalog ({catalog})."
                ),
                "guidance": (
                    "Install it yourself with the shell (brew/pip/…, subject to the "
                    "normal command approval), or continue without it and say in your "
                    "report which checks were degraded."
                ),
            }
        else:
            # The prompt must say up front whether WE can install this (pinned build for
            # this platform) — a card that offers Install for a tool we can't fetch turns
            # the user's approval into a guaranteed error. Absence of metadata means NO.
            info = _toolchain.describe(name)
            yield Event(
                EventType.TOOL_REQUESTED,
                {
                    "name": name,
                    "reason": reason,
                    "installable": info is not None,
                    "version": (info or {}).get("version", ""),
                    "summary": (info or {}).get("summary", ""),
                    "source": (info or {}).get("source", ""),
                },
            )
            self._audit(tool_call, stage="tool_requested", reason=reason)
            result = await self._interruptible(
                self.tool_requester(dict(args), tool_call.id),
                interrupted={"installed": False, "error": "interrupted by user"},
            ) or {"installed": False, "error": "no response"}
            if not result.get("installed"):
                # The card says "or install it yourself and continue" — honor it. A user
                # who brewed the tool mid-prompt and clicked Continue has PROVIDED it,
                # not declined it; find their copy before treating this as a refusal.
                found = _toolchain.resolve(name)
                if found:
                    result = {
                        "installed": True,
                        "path": found,
                        "note": (
                            "the user provided their own copy instead of the managed "
                            "install — use it from this path"
                        ),
                    }
            if not result.get("installed"):
                result.setdefault(
                    "guidance",
                    "Continue without it: use a fallback check if you have one, and say in "
                    "your report which checks were degraded.",
                )

        status = "ok" if result.get("installed") else "denied"
        self.messages.append(_tool_result_message(tool_call, result))
        self._audit(
            tool_call,
            stage="finished",
            status=status,
            result=result,
            result_preview=_preview(result),
        )
        yield Event(
            EventType.TOOL_FINISHED,
            {
                "name": tool_call.name,
                "status": status,
                "result_preview": _preview(result),
            },
        )

    async def _handle_directory_request(
        self, tool_call: ToolCall
    ) -> AsyncIterator[Event]:
        """Emit the grant prompt, await the user's out-of-band decision (which the requester also
        applies to this session's roots), and return the outcome as the tool result."""
        args = tool_call.arguments or {}
        if self.directory_requester is None:
            result: dict[str, Any] = {
                "granted": False,
                "error": "directory requests aren't available here",
            }
        else:
            yield Event(
                EventType.DIRECTORY_REQUESTED,
                {
                    "reason": str(args.get("reason", "")),
                    "path": str(args.get("path", "")),
                    "writable": bool(args.get("writable", False)),
                    # Root promotion (workspace-scratch-design.md §5): the agent asks for
                    # the folder to become the session's primary workspace — the consent
                    # card must say so, it's a different grant than a plain extra root.
                    "primary": bool(args.get("primary", False)),
                },
            )
            self._audit(
                tool_call,
                stage="directory_requested",
                reason=str(args.get("reason", "")),
            )
            result = await self._interruptible(
                self.directory_requester(dict(args), tool_call.id),
                interrupted={"granted": False, "error": "interrupted by user"},
            ) or {
                "granted": False,
                "error": "no response",
            }

        status = "ok" if result.get("granted") else "denied"
        self.messages.append(_tool_result_message(tool_call, result))
        self._audit(
            tool_call,
            stage="finished",
            status=status,
            result=result,
            result_preview=_preview(result),
        )
        yield Event(
            EventType.TOOL_FINISHED,
            {
                "name": tool_call.name,
                "status": status,
                "result_preview": _preview(result),
            },
        )

    async def _handle_ask_user(self, tool_call: ToolCall) -> AsyncIterator[Event]:
        """Emit the question, await the user's out-of-band answer (inline in the live session or
        from the Inbox when unattended), and return it as the tool result."""
        args = tool_call.arguments or {}
        question = str(args.get("question", "")).strip()
        # Grouped form (OPE-51): `questions` alone is a valid call — the singular field may be
        # empty. The asker normalizes/validates the entries; here only "is anything asked?".
        if not question:
            for entry in args.get("questions") or []:
                if isinstance(entry, dict) and str(entry.get("question", "")).strip():
                    question = str(entry["question"]).strip()
                    break
        if self.question_asker is None or not question:
            result: dict[str, Any] = {
                "answer": "",
                "error": (
                    "no question was asked"
                    if not question
                    else "asking isn't available here"
                ),
            }
        else:
            # The asker is mode-aware (attended → live inline prompt; unattended → Inbox), so it
            # owns surfacing the question. The engine just awaits the answer.
            self._audit(tool_call, stage="question_requested", reason=question)
            result = await self._interruptible(
                self.question_asker(dict(args), tool_call.id),
                interrupted={"answer": "", "error": "interrupted by user"},
            ) or {
                "answer": "",
                "error": "no response",
            }

        status = "ok" if (result.get("answer") or result.get("answers")) else "denied"
        if status == "ok":
            self._note_ask_replies(result)
        self.messages.append(_tool_result_message(tool_call, result))
        self._audit(
            tool_call,
            stage="finished",
            status=status,
            result=result,
            result_preview=_preview(result),
        )
        yield Event(
            EventType.TOOL_FINISHED,
            {
                "name": tool_call.name,
                "status": status,
                "result_preview": _preview(result),
            },
        )

    def _note_ask_replies(self, result: dict[str, Any]) -> None:
        """Record the user's ask_user answer(s) for the reviewer's history (§8.2). Values
        only — a grouped form's keys are the agent's own question headers, and agent text
        never enters the judge's view. Anchored to the number of user messages present now,
        so the merge stays chronological however the session continues."""
        anchor = sum(1 for m in self.messages if m.get("role") == "user")
        answers = result.get("answers")
        values = (
            [str(v) for v in answers.values()]
            if isinstance(answers, dict)
            else [str(result.get("answer") or "")]
        )
        for text in values:
            text = text.strip()
            if text:
                self._ask_replies.append((anchor, text))

    def _inject_steering(self) -> None:
        for text, source in self._steering:
            message: dict[str, Any] = {
                "role": "user",
                "content": text,
                "ts": time.time(),
            }
            if source is not None:
                message["source"] = source
            self.messages.append(message)
        self._steering = []

    def _outbound_messages(self) -> list[dict[str, Any]]:
        """`self.messages` prepared for the provider. The SOLE provider feed (see `_astream`).

        Every message is stripped of the display-only sidecars — `source`, `_display`, and
        `ts` — (providers reject unknown keys), unconditionally — whether or not a
        `<system-context>` block is added. When a context
        provider yields a non-empty string, an ephemeral `<system-context>` block is appended to the
        last user message. Never mutates `self.messages`, so neither the strip nor the block is
        persisted/replayed.
        """
        # Strip the display-only sidecars — `source` (connector cards), `_display`
        # (e.g. filter-hidden counts), `ts` (append-time timestamps), `reasoning`
        # (thinking text), and `usage` (token counts) — copying only messages that carry
        # one. Whole `notice` messages (error/interrupted/model-switch markers) are
        # display-only too: dropped entirely.
        _SIDECARS = ("source", "_display", "ts", "reasoning", "usage")
        # Auto-compaction (OPE-27): everything before the boundary is represented by the
        # compacted block. Outbound-only — the canonical history stays intact — and the
        # block+tail are byte-stable between turns, so prompt caching keeps working.
        source_messages = _compaction.apply_to_outbound(
            self.messages, self.compaction_state
        )
        out = [
            (
                {k: v for k, v in msg.items() if k not in _SIDECARS}
                if any(s in msg for s in _SIDECARS)
                else msg
            )
            for msg in source_messages
            if msg.get("role") != "notice"
        ]
        # PDF attachments (stored as `file` parts) are adapted to the ACTIVE model right
        # here — never in the persisted history — so a mid-session model switch always
        # re-decides: native PDF models get the real document, the rest get the local
        # text-extract/page-image fallback (pdf_support.py).
        if any(
            isinstance(p, dict) and p.get("type") == "file"
            for msg in out
            if isinstance(msg.get("content"), list)
            for p in msg["content"]
        ):
            caps = self.provider.capabilities(self.model)
            if not getattr(caps, "pdf", False):
                from . import pdf_support

                out = [
                    (
                        {
                            **msg,
                            "content": pdf_support.adapt_content(msg["content"], caps),
                        }
                        if isinstance(msg.get("content"), list)
                        else msg
                    )
                    for msg in out
                ]

        # Images get the same per-turn treatment: a model without vision receives a visible
        # placeholder instead of a payload it would reject. Like the PDF path, this re-decides
        # per call, so a mid-session switch to/from a vision model always does the right thing.
        if any(
            isinstance(p, dict) and p.get("type") == "image_url"
            for msg in out
            if isinstance(msg.get("content"), list)
            for p in msg["content"]
        ):
            caps = self.provider.capabilities(self.model)
            if not getattr(caps, "vision", False):
                placeholder = {
                    "type": "text",
                    "text": "[image attachment — not viewable by this model]",
                }
                out = [
                    (
                        {
                            **msg,
                            "content": [
                                (
                                    placeholder
                                    if isinstance(p, dict)
                                    and p.get("type") == "image_url"
                                    else p
                                )
                                for p in msg["content"]
                            ],
                        }
                        if isinstance(msg.get("content"), list)
                        else msg
                    )
                    for msg in out
                ]

        context = (
            self.context_provider() if self.context_provider is not None else ""
        ) or ""
        if not context:
            return out
        block = f"\n\n<system-context>\n{context}\n</system-context>"
        for i in range(len(out) - 1, -1, -1):
            if out[i].get("role") != "user":
                continue
            msg = dict(out[i])
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = content + block
            elif isinstance(content, list):  # content-parts (text + images)
                msg["content"] = [*content, {"type": "text", "text": block}]
            else:
                msg["content"] = block
            out[i] = msg
            break
        return out


def _assistant_message(turn: AssistantTurn, model: Optional[str] = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": turn.text or "",
        "ts": time.time(),
    }
    if turn.usage is not None:
        # Display/aggregation sidecar (like `reasoning`): persisted with the message,
        # stripped before provider calls. Tagged with the model that produced it so
        # per-model rollups survive mid-session model switches.
        message["usage"] = {"model": model, **turn.usage.as_dict()}
    if turn.reasoning:
        # Display-only thinking text — rendered by the GUI, stripped for every provider
        # (`_outbound_messages`); provider-private replay blocks go via `extras` instead.
        message["reasoning"] = turn.reasoning
    if turn.extras:
        # Provider-private sidecars (e.g. `_gemini` thought signatures) persist with the
        # message; the owning provider reattaches them, the rest strip them (base.py).
        message.update(turn.extras)
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in turn.tool_calls
        ]
    return message


_MANGLED_PREVIEW_CHARS = 200


def _is_mangled(tool_call: ToolCall) -> bool:
    """Provider arg-parsers fall back to `{"_raw": <unparsed text>}` when a tool call's
    arguments aren't a JSON object (typically a stream truncated mid-arguments)."""
    return set(tool_call.arguments or {}) == {"_raw"}


def _sanitize_mangled_calls(turn: AssistantTurn) -> None:
    """Shrink each mangled call's stored raw text to a short preview BEFORE the turn
    enters history. The full text is junk (half a JSON document): replaying it costs
    thousands of tokens per turn and, worse, teaches the model that `_raw` is a real
    parameter shape it should imitate."""
    for tc in turn.tool_calls:
        if _is_mangled(tc):
            raw = str(tc.arguments.get("_raw") or "")
            if len(raw) > _MANGLED_PREVIEW_CHARS:
                tc.arguments = {
                    "_raw": raw[:_MANGLED_PREVIEW_CHARS]
                    + f"… [unparsed tool-call text, {len(raw)} chars, truncated in history]"
                }


def _tool_result_message(tool_call: ToolCall, result: Any) -> dict[str, Any]:
    content = result if isinstance(result, str) else json.dumps(result, default=str)
    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": content,
        "ts": time.time(),
    }


def _tool_error_message(tool_call: ToolCall, reason: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": json.dumps({"error": "tool call not executed", "reason": reason}),
        "ts": time.time(),
    }


def _preview(value: Any, max_chars: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = text.replace("\n", "\\n")
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."
