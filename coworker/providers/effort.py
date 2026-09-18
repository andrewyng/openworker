"""Reasoning-effort levels and their per-provider translation (OPE-176).

One setting, `reasoning_effort`, five levels mirroring Anthropic's: low, medium, high,
xhigh, max. Unset means "send nothing" — every provider's request stays byte-identical to
before the setting existed (Anthropic's API default is `high`; Together's default for
Kimi K3 is `max`). Each provider client translates a level into the parameter its wire
accepts, records what it actually sent, and falls back (once, per model, per run) when
the endpoint rejects the parameter.

Level names a model does not accept map to the NEAREST accepted level by rank, ties going
UP (xhigh on Kimi K3 → max; medium on Kimi K3 → high). The mapping is recorded on every
reply as `{requested, effective, param, note}` so a run record states its effort.

Sources (read 2026-09-08): platform.claude.com/docs/en/build-with-claude/effort (supported
models per level; `high` == omitted; hold the level constant within a conversation or
the prompt cache restarts); Moonshot/Together Kimi K3 docs (`reasoning_effort` low/high/max,
default max).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from ..config import EFFORT_LEVELS

_RANK = {level: i for i, level in enumerate(EFFORT_LEVELS)}


@dataclass(frozen=True)
class EffortPlan:
    requested: str
    # The level/value actually sent; None when nothing was sent (unsupported, rejected).
    effective: Optional[str]
    # Provider request parameters to merge (empty when nothing is sent).
    params: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def record(self) -> dict[str, Any]:
        """The sidecar persisted on the assistant message / run record."""
        out: dict[str, Any] = {"requested": self.requested, "effective": self.effective}
        if self.params:
            out["param"] = self.params
        if self.note:
            out["note"] = self.note
        return out

    def without_param(self, note: str) -> "EffortPlan":
        """The plan after the endpoint rejected the parameter and it was dropped."""
        return EffortPlan(self.requested, None, {}, note)


def validate_level(value: Any, source: str = "reasoning_effort") -> Optional[str]:
    """Normalise a configured value to one of EFFORT_LEVELS (case-insensitive) or raise."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text not in _RANK:
        raise ValueError(
            f"{source} must be one of {', '.join(EFFORT_LEVELS)}; got {value!r}"
        )
    return text


def nearest_supported(level: str, supported: Sequence[str]) -> Optional[str]:
    """The accepted level closest in rank to `level`; ties resolve to the higher one."""
    if not supported:
        return None
    if level in supported:
        return level
    target = _RANK[level]
    return min(supported, key=lambda s: (abs(_RANK[s] - target), -_RANK[s]))


# -- Anthropic -----------------------------------------------------------------------

_ALL = tuple(EFFORT_LEVELS)
_NO_XHIGH = ("low", "medium", "high", "max")
# Longest prefix wins. Per the effort doc's "Supported models" + per-level availability.
_ANTHROPIC_EFFORT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("claude-fable-5-1", _ALL),
    ("claude-mythos-5-1", _ALL),
    ("claude-fable-5", _ALL),
    ("claude-mythos-5", _ALL),
    ("claude-mythos-preview", _NO_XHIGH),
    ("claude-opus-5", _ALL),
    ("claude-opus-4-8", _ALL),
    ("claude-opus-4-7", _ALL),
    ("claude-opus-4-6", _NO_XHIGH),
    ("claude-opus-4-5", ("low", "medium", "high")),
    ("claude-sonnet-5", _ALL),
    ("claude-sonnet-4-6", _NO_XHIGH),
)
# Budget-thinking models (older Claude): the level becomes a thinking budget. Anthropic
# requires budget_tokens >= 1,024 and < max_tokens; the provider floors max_tokens.
BUDGET_TOKENS_BY_LEVEL = {
    "low": 2_048,
    "medium": 8_192,
    "high": 16_000,
    "xhigh": 24_000,
    "max": 32_000,
}


def _anthropic_supported(model: str) -> Optional[tuple[str, ...]]:
    best: Optional[tuple[str, ...]] = None
    best_len = -1
    for prefix, levels in _ANTHROPIC_EFFORT:
        if model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = levels, len(prefix)
    return best


def anthropic_effort(model: str, level: str, *, budget_mode: bool) -> EffortPlan:
    """`output_config.effort` on adaptive-thinking models; `thinking.budget_tokens` on
    budget-mode models. Unknown adaptive models get the level as-is (the 400 fallback
    covers a wrong guess); the mapping is recorded either way."""
    if budget_mode:
        budget = BUDGET_TOKENS_BY_LEVEL[level]
        return EffortPlan(
            level,
            level,
            {"thinking": {"type": "enabled", "budget_tokens": budget}},
            f"budget-thinking model: {level} -> budget_tokens {budget}",
        )
    supported = _anthropic_supported(model)
    if supported is None:
        return EffortPlan(
            level,
            level,
            {"output_config": {"effort": level}},
            "model not in the documented effort table; sent unverified",
        )
    effective = nearest_supported(level, supported)
    assert effective is not None
    note = "" if effective == level else f"{level} not accepted by {model}; sent {effective}"
    return EffortPlan(level, effective, {"output_config": {"effort": effective}}, note)


# -- OpenAI-compatible endpoints (Together, Moonshot, DeepSeek, …) -------------------

# Bare model id (as the OpenAI-compatible client receives it) → accepted values.
# Only rows with a verified source; everything else gets the OpenAI vocabulary.
_OPENAI_COMPAT: dict[str, tuple[str, ...]] = {
    # Kimi K3: Moonshot and Together document reasoning_effort low / high / max.
    "moonshotai/Kimi-K3": ("low", "high", "max"),
    "moonshotai/kimi-k3": ("low", "high", "max"),  # OpenRouter's lowercase slug
    "kimi-k3": ("low", "high", "max"),
}
_OPENAI_DEFAULT = ("low", "medium", "high")


def openai_compat_effort(model: str, level: str) -> EffortPlan:
    supported = _OPENAI_COMPAT.get(model) or _OPENAI_COMPAT.get(model.lower()) or _OPENAI_DEFAULT
    effective = nearest_supported(level, supported)
    assert effective is not None
    verified = model in _OPENAI_COMPAT or model.lower() in _OPENAI_COMPAT
    note = ""
    if effective != level:
        note = f"{level} not accepted by {model}; sent {effective}"
    elif not verified:
        note = "model not in the verified table; OpenAI vocabulary assumed"
    return EffortPlan(level, effective, {"reasoning_effort": effective}, note)


def unsupported(level: str, why: str) -> EffortPlan:
    return EffortPlan(level, None, {}, why)


def mentions_effort(exc: BaseException) -> bool:
    """Does an endpoint error name the effort parameter (so dropping it is the fix)?"""
    msg = str(exc).lower()
    return "effort" in msg or "output_config" in msg
