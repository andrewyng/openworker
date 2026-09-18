"""Configuration — layered TOML: built-in defaults < global < per-workspace.

Global:    <state-dir>/config.toml   (see `secrets.state_dir`; platform-native)
Workspace: <workspace>/.coworker/config.toml   (overrides global)

Workspace command allowances apply only after the user trusts that exact canonical
workspace path. Other permission grants remain global-only.
"""

from __future__ import annotations

import os

try:
    import tomllib  # stdlib since 3.11
except ModuleNotFoundError:  # 3.10, the floor requires-python declares
    import tomli as tomllib  # type: ignore[no-redef]
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .secrets import state_dir

# Commands auto-run WITHOUT an approval prompt. There is no generally safe executable:
# nominally read-only programs can read secrets outside the workspace, expand environment
# variables, load project-controlled config/plugins, or execute helpers (for example
# `find -exec` and pytest collection). Keep the built-in list empty. A user may explicitly
# opt into command prefixes in their user-owned global config, accepting that authority.
DEFAULT_ALLOWED_COMMANDS: list[str] = []


# Reasoning-effort levels (OPE-176), mirroring Anthropic's vocabulary; each provider
# maps a level to what its wire accepts (coworker/providers/effort.py).
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")


@dataclass
class Config:
    model: str = "gpt-5.6-sol"
    mode: str = "interactive"
    max_iterations: int = 150
    # Per-reply output-token ceiling sent to the provider as `max_tokens` (thinking,
    # visible text and tool-call arguments all count against it). Unset = each
    # provider's own default (32,000 for Anthropic and OpenAI-compatible endpoints;
    # Bedrock 4,096). Anthropic recommends ~64,000 at high effort. Environment override:
    # COWORKER_MAX_OUTPUT_TOKENS. Explicit `build_engine(model_settings=...)` wins.
    max_output_tokens: Optional[int] = None
    # How hard the model should think per reply: one of EFFORT_LEVELS. Unset = send no
    # effort parameter at all (Anthropic's API default is high; Together's default for
    # Kimi K3 is max), so existing requests are unchanged. Held constant for a whole
    # session — changing it mid-conversation restarts the prompt cache. Environment
    # override: COWORKER_REASONING_EFFORT.
    reasoning_effort: Optional[str] = None
    # OPE-186: bound every tool result before it enters the conversation. A result whose
    # serialised form exceeds this many bytes is stored as head + marker + tail, with the
    # full text in a spill file the model can read. Unset = 10,000;
    # 0 = off. Environment override: COWORKER_TOOL_RESULT_MAX_BYTES.
    tool_result_max_bytes: Optional[int] = None
    # OPE-186 change 3: cap on the auto-compaction trigger, in tokens. The engine compacts
    # at min(80% of the model's window, this cap); the built-in cap is 250,000, which on a
    # 1M-window model fired once in 445 long sessions. Lower it (e.g. 60000) to
    # summarise long sessions earlier. Environment override: COWORKER_COMPACTION_CAP_TOKENS.
    compaction_cap_tokens: Optional[int] = None
    # OPE-189: output ceiling for the summariser call, in tokens. Unset = 16,000. On a
    # reasoning model the budget is shared with the model's thinking, so a low value means
    # the summary itself never gets written. Environment override:
    # COWORKER_COMPACTION_SUMMARY_MAX_TOKENS.
    compaction_summary_max_tokens: Optional[int] = None
    allowed_commands: list[str] = field(
        default_factory=lambda: list(DEFAULT_ALLOWED_COMMANDS)
    )
    # In "custom" permission mode, these tools are auto-approved (e.g. file edits)
    # while everything else still asks.
    auto_allow: list[str] = field(default_factory=list)
    # Egress destinations `web_fetch` may reach WITHOUT an approval prompt (exact host or
    # subdomain). Empty by default — the first fetch to any host asks. A power-user opt-in,
    # like `allowed_commands`; user-global only, so a repo can't widen the agent's network reach.
    allowed_domains: list[str] = field(default_factory=list)
    # Auto-Approve mode's feature flag (spec §1.5): when true, sessions get an LLM reviewer
    # that judges would-be approval cards in Mode.AUTO_APPROVE. Off by default; user-global
    # only — a cloned repo must not be able to hand itself a looser reviewer.
    auto_approve: bool = False
    # Shadow evaluation (spec Part 6 step 3): the reviewer records what it WOULD have
    # decided on every approval card while the human still decides. Verdicts land in the
    # audit log next to the human's outcome and nothing else changes — this is how the ship
    # gates (zero false-allows; ≥30% fewer prompts) get measured on real sessions. Costs
    # one model call per card while on. Off by default; user-global only.
    auto_approve_shadow: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    # Web search provider: "duckduckgo" (keyless default) | "tavily" | "brave" (need a key).
    web_search_provider: str = "duckduckgo"
    # OpenWorker Cloud (sign-in + managed connectors). Config, never constants:
    # dev/staging/BYO-VPC deployments point these at their own instances.
    cloud_base_url: str = "https://api.openworker.com"
    # Auth0 tenant + API audience are registered identifiers, not branding: the
    # tenant name can never be renamed, and the audience must match the API
    # identifier registered in Auth0 — both keep the legacy value on purpose.
    cloud_auth_domain: str = "opencoworker.us.auth0.com"
    cloud_client_id: str = "g1l4Q1lhYWmyS03qPSf4KEJGrgq02Qam"
    cloud_audience: str = "https://api.opencoworker.app"
    # Managed relay WebSocket endpoint (Slack/GitHub inbound). Defaults to the
    # PRODUCTION relay so a fresh install relays out of the box — an empty
    # default shipped once as "connected but relay OFF" on every machine
    # without a hand-edited config.toml. Empty override ⇒ relay disabled
    # (manual Socket Mode still works); dev/BYO deployments point elsewhere.
    cloud_relay_ws_url: str = (
        "wss://l4z1paxb83.execute-api.us-east-1.amazonaws.com/ocw-connect"
    )


_FIELDS = {
    "model",
    "mode",
    "max_iterations",
    "max_output_tokens",
    "reasoning_effort",
    "tool_result_max_bytes",
    "compaction_cap_tokens",
    "compaction_summary_max_tokens",
    "allowed_commands",
    "auto_allow",
    "allowed_domains",
    "auto_approve",
    "auto_approve_shadow",
    "host",
    "port",
    "web_search_provider",
    "cloud_base_url",
    "cloud_auth_domain",
    "cloud_client_id",
    "cloud_audience",
    "cloud_relay_ws_url",
}

# These fields change what consequential actions can run without a prompt, so the normal
# workspace override pass never applies them. `allowed_commands` is added separately only
# for a canonically trusted workspace; `auto_allow` and `allowed_domains` remain user-global
# only (a repo must not be able to widen the agent's command or network reach).
_GLOBAL_ONLY_FIELDS = {
    "allowed_commands",
    "auto_allow",
    "allowed_domains",
    "auto_approve",
    "auto_approve_shadow",
}
_WORKSPACE_FIELDS = _FIELDS - _GLOBAL_ONLY_FIELDS


def global_config_path() -> Path:
    return state_dir() / "config.toml"


def _read(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def workspace_allowed_commands(workspace: str | Path) -> list[str]:
    """Command prefixes requested by repository config; advisory until workspace trust."""
    path = Path(workspace).expanduser() / ".coworker" / "config.toml"
    value = _read(path).get("allowed_commands", [])
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(v.strip() for v in value if isinstance(v, str) and v.strip()))


MAX_OUTPUT_TOKENS_ENV = "COWORKER_MAX_OUTPUT_TOKENS"
REASONING_EFFORT_ENV = "COWORKER_REASONING_EFFORT"
TOOL_RESULT_MAX_BYTES_ENV = "COWORKER_TOOL_RESULT_MAX_BYTES"
COMPACTION_CAP_TOKENS_ENV = "COWORKER_COMPACTION_CAP_TOKENS"
COMPACTION_SUMMARY_MAX_TOKENS_ENV = "COWORKER_COMPACTION_SUMMARY_MAX_TOKENS"


def _nonnegative_int(value: Any, source: str) -> Optional[int]:
    """`tool_result_max_bytes`: an integer >= 0 (0 turns bounding off); bools rejected."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"tool_result_max_bytes must be an integer >= 0, got {value!r} ({source})"
        )
    return value


def _effort_level(value: Any, source: str) -> Optional[str]:
    """`reasoning_effort` must be one of EFFORT_LEVELS (case-insensitive); empty = unset."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text not in EFFORT_LEVELS:
        raise ValueError(
            f"reasoning_effort must be one of {', '.join(EFFORT_LEVELS)}, got {value!r} ({source})"
        )
    return text


def _positive_int(value: Any, source: str) -> Optional[int]:
    """`max_output_tokens` must be a positive integer (bools are ints in Python and
    TOML `true` would otherwise pass as 1)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(
            f"max_output_tokens must be a positive integer, got {value!r} ({source})"
        )
    return value


def load_config(
    workspace: Optional[str | Path] = None,
    *,
    global_path: Optional[Path] = None,
    workspace_trusted: bool = False,
) -> Config:
    cfg = Config()

    g = Path(global_path) if global_path is not None else global_config_path()
    if g.is_file():
        for key, value in _read(g).items():
            if key in _FIELDS:
                setattr(cfg, key, value)
    if workspace:
        w = Path(workspace).expanduser() / ".coworker" / "config.toml"
        if w.is_file():
            for key, value in _read(w).items():
                if key in _WORKSPACE_FIELDS:
                    setattr(cfg, key, value)
            if workspace_trusted:
                cfg.allowed_commands = list(
                    dict.fromkeys(
                        [*cfg.allowed_commands, *workspace_allowed_commands(workspace)]
                    )
                )
    cfg.max_output_tokens = _positive_int(cfg.max_output_tokens, "config.toml")
    raw = (os.environ.get(MAX_OUTPUT_TOKENS_ENV) or "").strip()
    if raw:
        try:
            parsed: Any = int(raw)
        except ValueError:
            parsed = raw
        cfg.max_output_tokens = _positive_int(parsed, MAX_OUTPUT_TOKENS_ENV)
    cfg.reasoning_effort = _effort_level(cfg.reasoning_effort, "config.toml")
    raw_effort = os.environ.get(REASONING_EFFORT_ENV)
    if raw_effort is not None and raw_effort.strip():
        cfg.reasoning_effort = _effort_level(raw_effort, REASONING_EFFORT_ENV)
    cfg.tool_result_max_bytes = _nonnegative_int(cfg.tool_result_max_bytes, "config.toml")
    raw_cap = (os.environ.get(TOOL_RESULT_MAX_BYTES_ENV) or "").strip()
    if raw_cap:
        try:
            parsed_cap: Any = int(raw_cap)
        except ValueError:
            parsed_cap = raw_cap
        cfg.tool_result_max_bytes = _nonnegative_int(parsed_cap, TOOL_RESULT_MAX_BYTES_ENV)
    if cfg.compaction_cap_tokens is not None and (
        isinstance(cfg.compaction_cap_tokens, bool)
        or not isinstance(cfg.compaction_cap_tokens, int)
        or cfg.compaction_cap_tokens <= 0
    ):
        raise ValueError(
            f"compaction_cap_tokens must be a positive integer, got {cfg.compaction_cap_tokens!r} (config.toml)"
        )
    raw_comp = (os.environ.get(COMPACTION_CAP_TOKENS_ENV) or "").strip()
    if raw_comp:
        try:
            parsed_comp = int(raw_comp)
        except ValueError:
            parsed_comp = 0
        if parsed_comp <= 0:
            raise ValueError(
                f"compaction_cap_tokens must be a positive integer, got {raw_comp!r} ({COMPACTION_CAP_TOKENS_ENV})"
            )
        cfg.compaction_cap_tokens = parsed_comp
    cfg.compaction_summary_max_tokens = _positive_int_setting(
        cfg.compaction_summary_max_tokens,
        "compaction_summary_max_tokens",
        COMPACTION_SUMMARY_MAX_TOKENS_ENV,
    )
    return cfg


def _positive_int_setting(
    value: Any, name: str, env_var: str
) -> Optional[int]:
    """A config.toml value overridden by `env_var`; both must be positive integers."""
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
    ):
        raise ValueError(
            f"{name} must be a positive integer, got {value!r} (config.toml)"
        )
    raw = (os.environ.get(env_var) or "").strip()
    if not raw:
        return value
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 0
    if parsed <= 0:
        raise ValueError(
            f"{name} must be a positive integer, got {raw!r} ({env_var})"
        )
    return parsed
