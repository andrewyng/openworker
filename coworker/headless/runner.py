"""`openworker run`: run ONE task with no human present, and record it.

    openworker run --prompt "..." --workspace /path/to/project --model anthropic/claude-sonnet-5 \
        --mode bypass-approvals --out ./run-record --trajectory-atif ./run-record/trajectory.json

The model id is accepted in OpenWorker's `provider:model` form or the `provider/model`
form other tools use. Nothing waits for a person: attendance is `auto`
(coworker/unattended.py), so the engine answers questions with the least-destructive
default, declines folder requests with guidance, installs pinned catalog tools itself,
and refuses any approval card only a person could clear unless the mode grants it.

Writes into --out:
    events.jsonl        every engine event, with a timestamp
    messages.json       the final conversation (OpenWorker's own persisted shape)
    summary.json        outcome, counts, tokens, cost, wall-clock, arguments
    answers.json        every answer the engine gave on the absent user's behalf
    audit.db            OpenWorker's own audit log for the session
    model_calls.jsonl   one line per model call: stop reason, usage, host, effort
    provider_errors.log full cause chains of failed model calls (credentials redacted)
    trajectory.json     the ATIF trajectory (also copied to --trajectory-atif when given)

The trajectory and the exit code are the stable contract; the other files are internal
and may change. Exit code 0 whenever a trajectory was written (completed, iteration cap,
timeout, model error); 1 only when the run crashed before producing one.

OpenWorker's state and scratch folders are pointed under --out before the engine is
built, so a run never touches the machine's real conversations, keys or settings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Optional

MODES = [
    "bypass-approvals",
    "dangerously-bypass-approvals",
    "auto-approve",
    "interactive",
    "custom",
    "auto",  # legacy spelling of bypass-approvals
]
ATTENDANCE = ["auto"]  # `inbox` needs the server's Inbox; `attended` needs a screen
RETRY_DELAYS_ENV = "OPENWORKER_RUN_RETRY_DELAYS"


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="openworker run",
        description="Run one task with no human present, and record it.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--prompt", "--prompt-text", dest="prompt_text", help="the task text")
    src.add_argument("--prompt-file", help="file containing the task text (UTF-8)")
    p.add_argument("--workspace", required=True, help="folder the agent works in")
    p.add_argument(
        "--add-dir",
        dest="add_dir",
        action="append",
        default=None,
        metavar="DIR",
        help="an extra folder the agent may read and write, beside the workspace "
        "(repeatable), for a harness whose output contract lives outside the workspace, "
        "e.g. --add-dir /output. The file tools only write inside the session's folders, in "
        "every mode; the shell is not scoped, so without this a delivery would depend on "
        "which tool the model happened to pick.",
    )
    p.add_argument(
        "--model", required=True, help="provider:model (OpenWorker) or provider/model"
    )
    p.add_argument("--persona", default="cowork", help="OpenWorker persona id (default: cowork)")
    p.add_argument("--mode", default="bypass-approvals", choices=MODES)
    p.add_argument(
        "--attendance",
        default="auto",
        choices=ATTENDANCE,
        help="who answers when the agent asks; `auto` = the engine, by fixed rule (default)",
    )
    p.add_argument(
        "--max-iterations", type=int, default=None, help="default: OpenWorker config (150)"
    )
    p.add_argument(
        "--provider-order",
        default=None,
        help="OpenRouter only: comma-separated upstream hosts to use, in order, with no "
        "fallback (e.g. 'Together')",
    )
    p.add_argument(
        "--compaction-cap-tokens",
        type=int,
        default=None,
        help="compact once the context reaches this many tokens (default: OpenWorker's own "
        "trigger, min(0.8 x window, 250000))",
    )
    p.add_argument(
        "--compaction-summary-max-tokens",
        type=int,
        default=None,
        help="output ceiling for the summariser call (default: OpenWorker's own 16,000)",
    )
    p.add_argument(
        "--tool-result-max-bytes",
        type=int,
        default=None,
        help="bound every tool result to this many bytes (head + marker + tail; full text "
        "spilled under <out>/tool-output). Default: OpenWorker's config, then 10000. 0 = off.",
    )
    p.add_argument(
        "--max-output-tokens",
        type=int,
        default=None,
        help="per-reply output-token cap sent as max_tokens (default: OpenWorker config, "
        "else the provider's own default)",
    )
    p.add_argument(
        "--timeout-seconds",
        type=float,
        default=1800.0,
        help="the run's own limit; 0 disables it (use 0 when an outer runner enforces one)",
    )
    p.add_argument("--out", required=True, help="folder for the run record")
    p.add_argument("--trajectory-atif", default=None, help="also write the ATIF trajectory here")
    p.add_argument("--agent-version", default=None, help="label for the trajectory's agent.version")
    p.add_argument(
        "--reasoning-effort",
        default=None,
        choices=["low", "medium", "high", "xhigh", "max"],
        help="reasoning-effort level sent to the provider (Anthropic output_config.effort, "
        "Together/Moonshot reasoning_effort). Default: nothing sent; the provider default is recorded",
    )
    p.add_argument("--scripted", default=None, help=argparse.SUPPRESS)  # tests: a fake model
    return p.parse_args(argv)


# -- isolation ----------------------------------------------------------------------------


def isolate_environment(out: Path) -> dict[str, str]:
    """Point OpenWorker's state and scratch folders under --out BEFORE the engine is
    built, exactly as the test suite does (tests/conftest.py)."""
    state = out / "state"
    scratch = out / "scratch"
    state.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["COWORKER_STATE_DIR"] = str(state)
    os.environ["COWORKER_SCRATCH_BASE"] = str(scratch)
    os.environ.pop("COWORKER_API_TOKEN", None)
    return {"COWORKER_STATE_DIR": str(state), "COWORKER_SCRATCH_BASE": str(scratch)}


# -- model name ---------------------------------------------------------------------------


def normalize_model(model: str) -> str:
    """Accept `provider:model` or `provider/model`; return OpenWorker's form. Splits on
    the FIRST slash only, so `together/moonshotai/Kimi-K3` becomes
    `together:moonshotai/Kimi-K3`."""
    from ..providers.registry import get_descriptor

    if ":" in model and get_descriptor(model.split(":", 1)[0]) is not None:
        return model
    if "/" in model:
        prefix, rest = model.split("/", 1)
        if get_descriptor(prefix) is not None:
            return f"{prefix}:{rest}"
    return model


# -- transient provider errors ------------------------------------------------------------
# The provider clients retry a failed request a couple of times within seconds. A provider
# outage lasting minutes outlives that, and the engine then ends the run with an ERROR
# event; in an unattended run that is a lost run. So the runner waits with backoff and
# re-enters the conversation with a nudge, up to ~17 minutes in total. Permanent errors
# (bad key, unknown model, context overflow) are not retried.
PROVIDER_RETRY_DELAYS: tuple[int, ...] = (30, 60, 120, 240, 300, 300)
PROVIDER_RETRY_NUDGE = (
    "The previous model request failed with a temporary provider error and has been "
    "retried. Continue the task from where you left off."
)
# An empty reply — no text, no tool call, finish_reason "stop" — ends the engine's turn as
# "completed". Under a degraded provider that is how a lost reply looks. The runner treats
# it like a provider hiccup: wait, then re-enter the conversation with this nudge. A model
# that really is finished answers it in one line.
EMPTY_REPLY_NUDGE = (
    "Your previous reply was empty: no text and no tool call. If the task is complete, "
    "say so in one line; otherwise continue the task from where you left off."
)


def ended_on_empty_reply(turn_events: list[dict[str, Any]]) -> bool:
    """True when the pass's last assistant message carried neither text nor a tool call."""
    last = next((e for e in reversed(turn_events) if e.get("type") == "assistant_message"), None)
    if last is None:
        return False
    data = last.get("data") or {}
    return not str(data.get("text") or "").strip() and not data.get("tool_calls")


_TRANSIENT_ERROR_TYPES = {
    # The engine's own "reply carried tool-call markup the endpoint couldn't parse" stop;
    # its docstring calls it drift that a retry usually clears.
    "UnparsedToolCall",
    "APIError",
    "APIConnectionError",
    "APITimeoutError",
    "APIStatusError",
    "InternalServerError",
    "RateLimitError",
    "ServiceUnavailableError",
    "OverloadedError",
    "ConnectionError",
    "TimeoutError",
    "ReadTimeout",
    "ConnectTimeout",
    "RemoteProtocolError",
}
_TRANSIENT_ERROR_TEXT = (
    "service unavailable",
    "temporarily unavailable",
    "overloaded",
    "rate limit",
    "too many requests",
    "capacity",
    "try again",
    "timed out",
    "timeout",
    "connection",
    "bad gateway",
    "gateway time",
    "internal server error",
    "empty response",
    "no response",
    " 429",
    " 500",
    " 502",
    " 503",
    " 504",
    " 529",
)
_PERMANENT_ERROR_TEXT = (
    "api key",
    "authentication",
    "unauthorized",
    "not found",
    "context length",
    "maximum context",
    "too long",
    "insufficient",
    "quota",
)


def is_transient_provider_error(payload: dict[str, Any]) -> bool:
    """True for engine ERROR payloads that describe a provider hiccup worth waiting out.

    Anything the provider client raised (the SDK error types above) counts as transient
    unless its text names a permanent condition; the text patterns then also catch
    hiccups surfaced under other exception types ("Provider returned an empty response"
    is the case that taught this: it matched no pattern and cost a long run).
    """
    text = " ".join(str(payload.get(k) or "") for k in ("error", "raw")).lower()
    if any(p in text for p in _PERMANENT_ERROR_TEXT):
        return False
    if str(payload.get("error_type") or "") in _TRANSIENT_ERROR_TYPES:
        return True
    return any(p in text for p in _TRANSIENT_ERROR_TEXT)


def provider_retry_delays() -> tuple[int, ...]:
    """OPENWORKER_RUN_RETRY_DELAYS="30,60,120" overrides the schedule (tests use "0")."""
    raw = os.environ.get(RETRY_DELAYS_ENV)
    if raw is None:
        return PROVIDER_RETRY_DELAYS
    raw = raw.strip()
    if not raw:
        return ()
    return tuple(max(0, int(x)) for x in raw.split(",") if x.strip())


# -- offline provider (tests) -------------------------------------------------------------


def _scripted_effort(settings: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The fake model echoes the level as a real provider's record would."""
    level = settings.get("reasoning_effort")
    if not level:
        return None
    return {"requested": level, "effective": level, "param": {"scripted": level}}


def scripted_provider(path: Path):
    """A fake model that replays turns from a JSON file: [{"text": ..., "tool_calls":
    [{"name": ..., "arguments": {...}}], "usage": {...}, "finish_reason": ...}, ...].
    For the offline tests only (`--scripted`)."""
    from ..providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
    from ..providers.base import TokenUsage

    turns = json.loads(path.read_text(encoding="utf-8"))

    class Scripted(ProviderClient):
        def __init__(self) -> None:
            self._turns = list(turns)
            self.calls = 0

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls += 1
            if not self._turns:
                return AssistantTurn(
                    text="(script exhausted)",
                    finish_reason="stop",
                    usage=TokenUsage(input=1, output=1),
                    output_limit=settings.get("max_tokens"),
                    effort=_scripted_effort(settings),
                )
            t = self._turns.pop(0)
            if t.get("error"):
                # A scripted provider failure: {"error": "Service unavailable",
                # "error_type": "APIError"} raises an exception of that name, so the
                # transient-error retry can be tested offline.
                exc_cls = type(str(t.get("error_type") or "APIError"), (Exception,), {})
                raise exc_cls(str(t["error"]))
            calls = [
                ToolCall(
                    id=tc.get("id") or f"call_{self.calls}_{i}",
                    name=tc["name"],
                    arguments=tc.get("arguments", {}),
                )
                for i, tc in enumerate(t.get("tool_calls", []))
            ]
            usage = t.get("usage") or {"input": 100, "output": 20}
            # An explicit "finish_reason" in the script (e.g. "length") overrides the
            # derived one, so truncation handling can be tested offline.
            return AssistantTurn(
                text=t.get("text"),
                tool_calls=calls,
                finish_reason=t.get("finish_reason") or ("tool_calls" if calls else "stop"),
                usage=TokenUsage(**usage),
                # Echo the ceiling the engine asked for, as a real provider would.
                output_limit=settings.get("max_tokens"),
                effort=_scripted_effort(settings),
            )

        def capabilities(self, model):
            return ModelCapabilities()

    return Scripted()


# -- reasoning effort ---------------------------------------------------------------------


def describe_reasoning_effort(model: str) -> dict[str, Any]:
    """What OpenWorker actually sends for reasoning/thinking with this model when no
    level was requested, read from its provider code. Run records state an effort per
    run, so this reports the effective behaviour instead of a label.

    Returns {"label": short string for metadata, "detail": {...}}."""
    provider = model.split(":", 1)[0] if ":" in model else "openai"
    bare = model.split(":", 1)[1] if ":" in model else model
    detail: dict[str, Any] = {
        "provider": provider,
        "model": bare,
        "source": "OpenWorker defaults, no user setting",
    }
    if provider in ("anthropic", "bedrock", "vertex"):
        try:
            from ..providers.anthropic_provider import (
                DEFAULT_MAX_TOKENS,
                DEFAULT_THINKING_BUDGET,
                _uses_budget_thinking,
            )

            detail["max_tokens"] = DEFAULT_MAX_TOKENS
            if _uses_budget_thinking(bare):
                detail["thinking"] = {"type": "enabled", "budget_tokens": DEFAULT_THINKING_BUDGET}
                label = f"extended thinking, budget {DEFAULT_THINKING_BUDGET} tokens (OpenWorker default)"
            else:
                detail["thinking"] = {"type": "adaptive", "display": "summarized"}
                # No output_config.effort is sent; Anthropic documents that as identical
                # to effort "high".
                detail["effort"] = "high (API default; no output_config sent)"
                label = "high (Anthropic adaptive thinking, effort not set = API default high)"
        except Exception:  # noqa: BLE001 - never fail a run over a metadata lookup
            label = "extended thinking on (OpenWorker default; details unavailable)"
        return {"label": label, "detail": detail}
    if provider == "openai":
        detail["reasoning_effort"] = "not sent; OpenAI server default applies (Responses API)"
        return {
            "label": "provider default (OpenAI Responses API, no effort parameter sent)",
            "detail": detail,
        }
    detail["reasoning_effort"] = "not sent; provider default applies"
    return {"label": "provider default (no reasoning-effort parameter sent)", "detail": detail}


# -- provider error recording -------------------------------------------------------------


_SECRET_ENV_MARKERS = ("_API_KEY", "_TOKEN", "_SECRET", "PASSWORD")


def _secret_values() -> list[str]:
    """Values of environment variables that look like credentials (for redaction)."""
    values = []
    for name, value in os.environ.items():
        upper = name.upper()
        if value and len(value) >= 8 and any(m in upper for m in _SECRET_ENV_MARKERS):
            values.append(value.strip())
    return sorted(set(values), key=len, reverse=True)


def redact(text: str) -> str:
    """Mask credential values and common key shapes in free text before it is written."""
    import re

    for value in _secret_values():
        text = text.replace(value, "[REDACTED]")
    # Common provider key shapes, in case a value arrived by a path other than the env.
    text = re.sub(r"sk-[A-Za-z0-9_-]{16,}", "[REDACTED]", text)
    text = re.sub(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b", "[REDACTED]", text)
    return text


def sanitize_key_env() -> list[str]:
    """Strip stray whitespace from credential variables. A key pasted with a trailing
    space or newline is an illegal HTTP header value and every request fails before
    leaving the machine. Returns the names that were cleaned."""
    cleaned = []
    for name, value in list(os.environ.items()):
        upper = name.upper()
        if value and any(m in upper for m in _SECRET_ENV_MARKERS):
            stripped = value.strip()
            if stripped != value:
                os.environ[name] = stripped
                cleaned.append(name)
    return cleaned


def recording_provider(inner, log_path: Path):
    """Wrap a ProviderClient so every model call leaves one line in model_calls.jsonl
    (stop reason, usage, ceiling, effort, serving host) and every exception is written to
    `log_path` with its full cause chain (credentials redacted), then re-raised unchanged.
    The engine reports only the top-level message; the chain underneath is what says
    whether it was DNS, TLS, a proxy, a refused socket, or a malformed header."""
    from ..providers import ProviderClient

    class Recording(ProviderClient):
        def __init__(self) -> None:
            self.inner = inner

        def _record(self, exc: BaseException, method: str) -> None:
            try:
                lines = [f"=== {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {method} ==="]
                lines.append("".join(traceback.format_exception(exc)))
                cause = exc.__cause__ or exc.__context__
                depth = 0
                while cause is not None and depth < 8:
                    lines.append(
                        f"--- caused by ({type(cause).__module__}.{type(cause).__name__}): {cause}"
                    )
                    cause = cause.__cause__ or cause.__context__
                    depth += 1
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(redact("\n".join(lines)) + "\n")
            except Exception:  # noqa: BLE001 - diagnostics must never mask the real error
                pass

        def _note_turn(self, turn, method: str) -> None:
            try:
                usage = turn.usage.as_dict() if getattr(turn, "usage", None) else None
                rec = {
                    "ts": time.time(),
                    "method": method,
                    "finish_reason": getattr(turn, "finish_reason", None),
                    "max_output_tokens": getattr(turn, "output_limit", None),
                    "effort": getattr(turn, "effort", None),
                    "served_by": getattr(turn, "served_by", None),
                    "n_tool_calls": len(getattr(turn, "tool_calls", None) or []),
                    "text_chars": len(getattr(turn, "text", None) or ""),
                    "reasoning_chars": len(getattr(turn, "reasoning", None) or ""),
                    "usage": usage,
                }
                calls_path = log_path.with_name("model_calls.jsonl")
                with calls_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec) + "\n")
            except Exception:  # noqa: BLE001
                pass

        def complete(self, *, model, messages, tools=None, **settings):
            try:
                turn = self.inner.complete(model=model, messages=messages, tools=tools, **settings)
            except BaseException as exc:
                self._record(exc, "complete")
                raise
            self._note_turn(turn, "complete")
            return turn

        def stream(self, *, model, messages, tools=None, **settings):
            try:
                for chunk in self.inner.stream(model=model, messages=messages, tools=tools, **settings):
                    if getattr(chunk, "turn", None) is not None:
                        self._note_turn(chunk.turn, "stream")
                    yield chunk
            except BaseException as exc:
                self._record(exc, "stream")
                raise

        def capabilities(self, model):
            return self.inner.capabilities(model)

        def __getattr__(self, name):  # anything else the engine may probe on the provider
            return getattr(self.inner, name)

    return Recording()


# -- the answers the engine gave for the absent user --------------------------------------


class Answers:
    """Every answer the engine gave on the absent user's behalf, taken from the audit
    rows the engine writes: refused cards, floors the mode cleared, auto-answered
    questions, declined folder requests, pinned installs. Written as answers.json."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def sink(self, row: dict[str, Any]) -> None:
        stage = str(row.get("stage") or "")
        keep = (
            (stage == "approval_resolved" and row.get("approval") == "auto_refused")
            or (stage == "auto_allowed" and str(row.get("reason") or "").startswith("cleared by mode"))
            or stage in ("question_auto_answered", "directory_auto_refused", "tool_auto_install")
        )
        if keep:
            self.rows.append(
                {
                    "ts": time.time(),
                    "stage": stage,
                    "tool": row.get("tool"),
                    "arguments": _preview(row.get("arguments")),
                    "reason": row.get("reason"),
                }
            )

    def counts(self) -> dict[str, int]:
        def n(stage: str) -> int:
            return sum(1 for r in self.rows if r["stage"] == stage)

        return {
            "cards_refused": n("approval_resolved"),
            "floors_cleared": n("auto_allowed"),
            "questions_answered": n("question_auto_answered"),
            "directory_requests_declined": n("directory_auto_refused"),
            "tool_installs": n("tool_auto_install"),
        }


def _preview(value: Any, limit: int = 400) -> str:
    text = json.dumps(value, default=str) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[:limit] + "…"


# -- the run ------------------------------------------------------------------------------


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_text is not None:
        return args.prompt_text
    return Path(args.prompt_file).read_text(encoding="utf-8")


def _agent_version(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    from importlib.metadata import PackageNotFoundError, version

    for dist in ("openworker", "coworker"):
        try:
            return f"openworker {version(dist)}"
        except PackageNotFoundError:
            continue
        except Exception:  # noqa: BLE001
            break
    return "openworker unknown"


def _extra_dirs(args: argparse.Namespace, workspace: Path) -> list:
    """The `--add-dir DIR` folders as writable roots: created if missing, deduplicated, and
    without the workspace itself (always the first root)."""
    from ..roots import RootDir

    found: list = []
    for raw in args.add_dir or []:
        path = Path(raw).expanduser().resolve()
        if path == workspace or any(path == r.path for r in found):
            continue
        path.mkdir(parents=True, exist_ok=True)
        found.append(RootDir(path=path, writable=True))
    return found


def run(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    isolation = isolate_environment(out)
    cleaned_keys = sanitize_key_env()
    if cleaned_keys:
        print(f"openworker run: stripped stray whitespace from {', '.join(cleaned_keys)}")

    # Build the engine only now, after the isolation variables are set.
    from ..agent import build_engine
    from ..agents.registry import get_agent
    from ..audit import AuditStore
    from ..events import EventType
    from ..permissions import Mode
    from ..providers import ProviderRouter
    from ..roots import RootDir
    from ..secrets import SecretStore, state_dir
    from ..unattended import DANGEROUS_MODE_WARNING

    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    # `--add-dir DIR`: extra writable folders. An explicit list replaces the engine's
    # default single-workspace root, so the workspace goes first and stays the folder
    # that relative paths resolve against.
    extra_dirs = _extra_dirs(args, workspace)
    roots = [RootDir(path=workspace, writable=True), *extra_dirs] if extra_dirs else None
    model = normalize_model(args.model)
    mode = Mode(args.mode)
    run_id = uuid.uuid4().hex[:12]
    prompt = _read_prompt(args)
    answers = Answers()

    secrets = SecretStore(state_dir() / "secrets.json")
    if args.scripted:
        provider = scripted_provider(Path(args.scripted))
    else:
        default_provider = model.split(":", 1)[0] if ":" in model else "openai"
        provider = ProviderRouter(secrets, default_provider=default_provider)
    provider = recording_provider(provider, out / "provider_errors.log")

    audit = AuditStore(out / "audit.db")

    def audit_sink(row: dict[str, Any]) -> None:
        audit.append(row)
        answers.sink(row)

    engine = build_engine(
        agent=get_agent(args.persona),
        workspace=workspace,
        roots=roots,
        model=model,
        mode=mode,
        provider=provider,
        audit_sink=audit_sink,
        max_iterations=args.max_iterations,
        # An explicit ceiling rides model_settings into every provider call.
        model_settings=_model_settings(args),
        # Bounded tool results; the full texts stay with the record under tool-output/.
        tool_result_max_bytes=args.tool_result_max_bytes,
        tool_result_spill_dir=out / "tool-output",
        session_id=run_id,
        secrets=secrets,
        # The reviewer only exists in auto-approve mode; elsewhere leave the config default.
        auto_approve=True if mode is Mode.AUTO_APPROVE else None,
    )
    # Nobody is present: the engine answers by rule (coworker/unattended.py).
    engine.attendance = lambda: args.attendance
    if mode is Mode.DANGEROUSLY_BYPASS_APPROVALS:
        print(f"openworker run: {DANGEROUS_MODE_WARNING}", file=sys.stderr, flush=True)

    if args.compaction_cap_tokens or args.compaction_summary_max_tokens:
        # Compact early so a cheap task exercises the summariser and the persisted
        # compaction record. Window comes from the model matrix.
        _base_cfg = engine._compaction_config()
        _forced = {
            "cap_tokens": int(args.compaction_cap_tokens or _base_cfg["cap_tokens"]),
            "threshold_pct": float(_base_cfg["threshold_pct"]),
            "context_window": _base_cfg.get("context_window"),
            "summary_max_tokens": int(
                args.compaction_summary_max_tokens or _base_cfg["summary_max_tokens"]
            ),
        }
        engine.compaction_settings = lambda: dict(_forced)
    events: list[dict[str, Any]] = []
    outcome = "unknown"
    error_text: Optional[str] = None
    started = time.time()

    # Records are written INCREMENTALLY. An outer time limit may end the process with a
    # signal, and a runner that only writes at the end leaves nothing behind. So: every
    # event is appended to events.jsonl as it happens, and after every iteration a
    # provisional summary, messages, and trajectory are rewritten. SIGTERM also flushes.
    events_fh = (out / "events.jsonl").open("w", encoding="utf-8")

    def flush_provisional(status: str) -> None:
        try:
            events_fh.flush()
            _write_records(
                out=out,
                engine=engine,
                events=events,
                answers=answers,
                outcome=status,
                error_text=None,
                wall=time.time() - started,
                model=model,
                args=args,
                run_id=run_id,
                isolation=isolation,
                trajectory_extra_path=None,  # final copy to --trajectory-atif happens at the end
            )
        except Exception:  # noqa: BLE001 - a provisional write must never break the run
            pass

    import signal

    def _on_term(signum, frame):  # noqa: ARG001
        try:
            engine.request_interrupt()
        finally:
            flush_provisional("killed")

    for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            try:
                signal.signal(sig, _on_term)
            except (ValueError, OSError):
                pass

    async def consume() -> None:
        delays = provider_retry_delays()
        retries = 0
        message: Any = prompt
        while True:
            last_type = None
            last_data: dict[str, Any] = {}
            pass_start = len(events)
            async for ev in engine.run(message):
                rec = {"ts": time.time(), "type": ev.type.value, "data": ev.data}
                events.append(rec)
                events_fh.write(json.dumps(rec, default=str) + "\n")
                last_type, last_data = ev.type, ev.data
                if ev.type == EventType.ITERATION_END:
                    flush_provisional("in_progress")
            # A transient provider failure ended the turn: wait, then re-enter the same
            # conversation with a nudge (the engine keeps every message so far).
            if (
                last_type == EventType.ERROR
                and retries < len(delays)
                and is_transient_provider_error(last_data)
            ):
                delay = delays[retries]
                retries += 1
                rec = {
                    "ts": time.time(),
                    "type": "provider_retry",
                    "data": {
                        "attempt": retries,
                        "of": len(delays),
                        "delay_seconds": delay,
                        "error": last_data.get("error"),
                        "error_type": last_data.get("error_type"),
                    },
                }
                events.append(rec)
                events_fh.write(json.dumps(rec, default=str) + "\n")
                events_fh.flush()
                print(
                    f"openworker run: provider error ({last_data.get('error_type')}: "
                    f"{str(last_data.get('error'))[:120]}); retry {retries}/{len(delays)} "
                    f"in {delay}s",
                    file=sys.stderr,
                    flush=True,
                )
                flush_provisional("in_progress")
                await asyncio.sleep(delay)
                message = PROVIDER_RETRY_NUDGE
                continue
            # An empty reply ended the turn as "completed": same wait-and-nudge, same budget.
            if (
                last_type == EventType.TURN_END
                and str(last_data.get("status") or "") == "completed"
                and retries < len(delays)
                and ended_on_empty_reply(events[pass_start:])
            ):
                delay = delays[retries]
                retries += 1
                rec = {
                    "ts": time.time(),
                    "type": "provider_retry",
                    "data": {
                        "attempt": retries,
                        "of": len(delays),
                        "delay_seconds": delay,
                        "error": "empty reply (no text, no tool call)",
                        "error_type": "EmptyReply",
                    },
                }
                events.append(rec)
                events_fh.write(json.dumps(rec, default=str) + "\n")
                events_fh.flush()
                print(
                    f"openworker run: empty reply; retry {retries}/{len(delays)} in {delay}s",
                    file=sys.stderr,
                    flush=True,
                )
                flush_provisional("in_progress")
                await asyncio.sleep(delay)
                message = EMPTY_REPLY_NUDGE
                continue
            break

    async def guarded() -> None:
        # timeout_seconds <= 0 disables the run's own limit (an outer runner may enforce
        # its own; a second, shorter limit here would cut long tasks early).
        if args.timeout_seconds and args.timeout_seconds > 0:
            await asyncio.wait_for(consume(), timeout=args.timeout_seconds)
        else:
            await consume()

    try:
        asyncio.run(guarded())
    except asyncio.TimeoutError:
        outcome = "timeout"
        try:
            engine.request_interrupt()
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001 - recorded, then reported by exit code
        outcome = "crash"
        error_text = "".join(traceback.format_exception(exc))
    finally:
        executor = getattr(engine, "executor", None)
        if executor is not None:
            try:
                executor.close()
            except Exception:  # noqa: BLE001
                pass
    wall = time.time() - started
    try:
        events_fh.close()
    except Exception:  # noqa: BLE001
        pass

    # Outcome from the events when the loop ended on its own.
    if outcome == "unknown":
        outcome = "ended_without_turn_end"
        for ev in reversed(events):
            if ev["type"] == EventType.TURN_END.value:
                outcome = str(ev["data"].get("status", "completed"))
                break
            if ev["type"] == EventType.ERROR.value:
                outcome = "model_error"
                error_text = str(ev["data"].get("error", ""))
                break
            if ev["type"] == EventType.INTERRUPTED.value:
                outcome = "interrupted"
                break

    summary = _write_records(
        out=out,
        engine=engine,
        events=events,
        answers=answers,
        outcome=outcome,
        error_text=error_text,
        wall=wall,
        model=model,
        args=args,
        run_id=run_id,
        isolation=isolation,
        trajectory_extra_path=args.trajectory_atif,
    )
    tokens = summary["tokens"]
    print(
        f"openworker run: outcome={outcome} iterations={summary['iterations']} "
        f"tool_calls={summary['tool_calls']} refused={summary['answers']['cards_refused']} "
        f"tokens_in={tokens['input']} tokens_out={tokens['output']} "
        f"cost={summary['cost_usd']} wall={wall:.0f}s out={out}"
    )
    return 1 if outcome == "crash" else 0


def _model_settings(args: argparse.Namespace) -> Optional[dict[str, Any]]:
    """Explicit per-run settings that ride model_settings into every provider call:
    the output ceiling and the reasoning-effort level."""
    settings: dict[str, Any] = {}
    if args.max_output_tokens:
        settings["max_tokens"] = args.max_output_tokens
    if args.reasoning_effort:
        settings["reasoning_effort"] = args.reasoning_effort
    if args.provider_order:
        # OpenRouter's routing field rides the OpenAI SDK's `extra_body`, which
        # OpenWorker's OpenAI-compatible client passes through untouched.
        order = [x.strip() for x in args.provider_order.split(",") if x.strip()]
        settings["extra_body"] = {"provider": {"order": order, "allow_fallbacks": False}}
    return settings or None


def _effort_summary(args: argparse.Namespace, model: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """What the run record says about effort. Set: the level requested plus what the
    provider reported sending on each reply. Unset: the provider default described from
    OpenWorker's code."""
    if not args.reasoning_effort:
        described = describe_reasoning_effort(model)
        return {"label": described["label"], "detail": described["detail"]}
    records = [
        e["data"].get("reasoning_effort")
        for e in events
        if e["type"] == "assistant_message" and e["data"].get("reasoning_effort")
    ]
    effective = sorted({str(r.get("effective")) for r in records}) if records else []
    detail: dict[str, Any] = {
        "requested": args.reasoning_effort,
        "effective": effective[0] if len(effective) == 1 else effective,
        "replies_recorded": len(records),
        "first_record": records[0] if records else None,
        "notes": sorted({r["note"] for r in records if r.get("note")}),
    }
    label = args.reasoning_effort
    if len(effective) == 1 and effective[0] not in (args.reasoning_effort, "None"):
        label = f"{args.reasoning_effort} (sent as {effective[0]})"
    elif effective == ["None"]:
        label = f"{args.reasoning_effort} (requested; provider sent no effort parameter)"
    return {"label": label, "detail": detail}


def _context_record(engine: Any) -> dict[str, Any]:
    try:
        from .. import compaction as _compaction

        cfg = engine._compaction_config()
        window = cfg.get("context_window")
        return {
            "window": window,
            "window_source": "matrix" if window else "fallback",
            "compaction_trigger_tokens": _compaction.trigger_tokens(
                window,
                threshold_pct=float(cfg["threshold_pct"]),
                cap_tokens=int(cfg["cap_tokens"]),
            ),
        }
    except Exception:  # noqa: BLE001 - a record field must never break the run
        return {"window": None, "window_source": "unknown", "compaction_trigger_tokens": None}


def _write_records(
    *,
    out: Path,
    engine: Any,
    events: list[dict[str, Any]],
    answers: Answers,
    outcome: str,
    error_text: Optional[str],
    wall: float,
    model: str,
    args: argparse.Namespace,
    run_id: str,
    isolation: dict[str, str],
    trajectory_extra_path: Optional[str],
) -> dict[str, Any]:
    """Compute the summary from the events so far and (re)write summary.json,
    messages.json, answers.json, and trajectory.json. Called after every iteration with a
    provisional outcome and once more at the end with the final one, so a process killed
    by an outer time limit still leaves a usable record."""
    from ..events import EventType
    from .pricing import estimate_cost
    from .trajectory import build_trajectory, write_trajectory

    tool_finished = [e for e in events if e["type"] == EventType.TOOL_FINISHED.value]
    assistant = [e for e in events if e["type"] == EventType.ASSISTANT_MESSAGE.value]
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    model_calls_with_usage = 0
    for e in assistant:
        usage = e["data"].get("usage")
        if usage:
            model_calls_with_usage += 1
            for k in tokens:
                tokens[k] += int(usage.get(k, 0) or 0)
    iterations = 0
    for e in events:
        if e["type"] == EventType.ITERATION_END.value:
            iterations = max(iterations, int(e["data"].get("iteration", 0) or 0))
        if e["type"] == EventType.TURN_END.value:
            iterations = max(iterations, int(e["data"].get("iterations", 0) or 0))

    cost = estimate_cost(model, tokens)
    effort = _effort_summary(args, model, events)

    summary = {
        "run_id": run_id,
        "outcome": outcome,
        "reasoning_effort": effort["label"],
        "reasoning_effort_detail": effort["detail"],
        "error": error_text,
        "wall_seconds": round(wall, 1),
        "iterations": iterations,
        "model_calls": len(assistant),
        "model_calls_with_usage": model_calls_with_usage,
        "tool_calls": len(tool_finished),
        # Tool results the engine bounded (head + marker + tail; full text under
        # <out>/tool-output). Counted from the marker the bounder writes.
        "tool_results_bounded": sum(
            1
            for m in getattr(engine, "messages", [])
            if m.get("role") == "tool" and "bytes omitted here" in str(m.get("content") or "")
        ),
        "tool_calls_denied": sum(1 for e in tool_finished if e["data"].get("status") == "denied"),
        "tool_calls_error": sum(1 for e in tool_finished if e["data"].get("status") == "error"),
        # Replies cut off at the output limit with no action that the engine nudged past;
        # outcome "truncated" means the cap was hit repeatedly.
        "continuations": sum(1 for e in events if e["type"] == "continuation"),
        # Transient provider failures the runner waited out and resumed from; outcome
        # "model_error" means the schedule was exhausted (or the error was permanent).
        "provider_retries": sum(1 for e in events if e["type"] == "provider_retry"),
        # What the engine answered on the absent user's behalf (coworker/unattended.py).
        "answers": answers.counts(),
        "tokens": tokens,
        "cost_usd": cost,
        "args": {
            "model": model,
            "model_as_given": args.model,
            "persona": args.persona,
            "mode": args.mode,
            "attendance": args.attendance,
            "max_iterations": args.max_iterations,
            "max_output_tokens": args.max_output_tokens,
            "reasoning_effort": args.reasoning_effort,
            "compaction_cap_tokens": args.compaction_cap_tokens,
            "compaction_summary_max_tokens": args.compaction_summary_max_tokens,
            "tool_result_max_bytes": args.tool_result_max_bytes,
            "provider_order": args.provider_order,
            "timeout_seconds": args.timeout_seconds,
            "workspace": str(Path(args.workspace).resolve()),
            "extra_dirs": [str(Path(r).expanduser().resolve()) for r in (args.add_dir or [])],
            "scripted": bool(args.scripted),
        },
        "isolation": isolation,
        # The window and trigger the engine actually used: an unlisted model shows
        # window null and the fallback trigger.
        "context": _context_record(engine),
        # Distinct upstream hosts reported by a router across the run (proof of a pin).
        "served_by": sorted(
            {
                str(e["data"]["served_by"])
                for e in events
                if e["type"] == "assistant_message" and e["data"].get("served_by")
            }
        ),
        # Every compaction that happened, as persisted on the notices.
        "compactions": [
            {
                "trimmed": bool((m.get("compaction") or {}).get("trimmed")),
                "boundary_index": (m.get("compaction") or {}).get("boundary_index"),
                "model_used": (m.get("compaction") or {}).get("model_used"),
                "summary_chars": len(str((m.get("compaction") or {}).get("summary_text") or "")),
            }
            for m in engine.messages
            if m.get("role") == "notice" and m.get("kind") == "compacted"
        ],
    }

    (out / "messages.json").write_text(
        json.dumps(engine.messages, indent=1, default=str), encoding="utf-8"
    )
    (out / "answers.json").write_text(
        json.dumps({"counts": answers.counts(), "rows": answers.rows}, indent=1),
        encoding="utf-8",
    )
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")

    trajectory = build_trajectory(
        engine.messages,
        agent_name="openworker",
        agent_version=_agent_version(args.agent_version),
        model_name=model,
        session_id=run_id,
        outcome=outcome,
        reasoning_effort=effort["label"],
        agent_extra={
            "persona": args.persona,
            "mode": args.mode,
            "attendance": args.attendance,
            "reasoning_effort": effort["label"],
            "reasoning_effort_detail": effort["detail"],
        },
    )
    write_trajectory(out / "trajectory.json", trajectory)
    if trajectory_extra_path:
        write_trajectory(trajectory_extra_path, trajectory)
    return summary


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    try:
        code = run(args)
    except Exception:  # noqa: BLE001 - last resort: say what happened, exit non-zero
        traceback.print_exc()
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
