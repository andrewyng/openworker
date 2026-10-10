"""What a user can set per session for a model: a thinking switch and reasoning effort.

Two separate controls, each either supported or not (UX-056):

- Thinking: an on/off switch, for models that have one (Qwen3, Nemotron). Current Claude
  models think adaptively and cannot turn it off, so they do not have this control.
- Reasoning: a level from the model's own list, Faster to Smarter (Claude, GPT-5.x,
  Kimi K3, gpt-oss). There is no "off" level.

Cloud models come from `model_controls.json` next to this file, edited in git with a
source per row. Local models are filled in from what their server reports (Ollama's
capabilities, the llama.cpp chat template) and the recommendation table, unless a row in
the JSON says otherwise. Anything not known to support a control gets `not_supported`:
a control that silently does nothing is worse than no control.

The model's default is the user's saved setting for that model (model_config.py) when
there is one, else the table's.
"""

from __future__ import annotations

import fnmatch
import functools
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from ..config import EFFORT_LEVELS

_TABLE = Path(__file__).with_name("model_controls.json")


class Support(str, Enum):
    SUPPORTED = "supported"
    NOT_SUPPORTED = "not_supported"


class EffortLevel(str, Enum):
    """Ordered from faster to smarter."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


assert tuple(e.value for e in EffortLevel) == tuple(EFFORT_LEVELS)


@dataclass(frozen=True)
class ThinkingControl:
    support: Support = Support.NOT_SUPPORTED
    default: Optional[bool] = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"support": self.support.value}
        if self.support is Support.SUPPORTED:
            out["default"] = self.default
        return out


@dataclass(frozen=True)
class ReasoningControl:
    support: Support = Support.NOT_SUPPORTED
    levels: tuple[EffortLevel, ...] = ()
    default: Optional[EffortLevel] = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"support": self.support.value}
        if self.support is Support.SUPPORTED:
            out["levels"] = [lvl.value for lvl in self.levels]
            out["default"] = self.default.value if self.default else None
        return out


@dataclass(frozen=True)
class ModelControls:
    thinking: ThinkingControl = ThinkingControl()
    reasoning: ReasoningControl = ReasoningControl()

    def as_dict(self) -> dict[str, Any]:
        return {"thinking": self.thinking.as_dict(), "reasoning": self.reasoning.as_dict()}


NONE = ModelControls()


@dataclass(frozen=True)
class _Row:
    match: tuple[str, ...]
    providers: tuple[str, ...]
    controls: ModelControls


def _parse_row(raw: dict[str, Any]) -> _Row:
    t = raw.get("thinking") or {}
    r = raw.get("reasoning") or {}
    thinking = ThinkingControl(Support(t.get("support", "not_supported")), t.get("default"))
    levels = tuple(EffortLevel(x) for x in (r.get("levels") or []))
    default = EffortLevel(r["default"]) if r.get("default") else None
    if default is not None and default not in levels:
        raise ValueError(f"default {default.value!r} is not one of the levels {raw.get('match')}")
    reasoning = ReasoningControl(Support(r.get("support", "not_supported")), levels, default)
    if reasoning.support is Support.SUPPORTED and not levels:
        raise ValueError(f"reasoning supported without levels {raw.get('match')}")
    return _Row(
        tuple(p.lower() for p in raw.get("match") or ()),
        tuple(raw.get("providers") or ()),
        ModelControls(thinking, reasoning),
    )


@functools.lru_cache(maxsize=1)
def _rows() -> tuple[_Row, ...]:
    try:
        raw = json.loads(_TABLE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    return tuple(_parse_row(r) for r in raw.get("models") or ())


def table_row(provider: str, bare_model: str) -> Optional[ModelControls]:
    """The JSON row for a model, or None. `bare_model` has no provider prefix."""
    name = bare_model.lower()
    for row in _rows():
        if row.providers and provider not in row.providers:
            continue
        if any(fnmatch.fnmatch(name, pattern) for pattern in row.match):
            return row.controls
    return None


def controls_for(
    provider: str,
    bare_model: str,
    *,
    local: bool = False,
    server_thinking: Optional[bool] = None,
    recommended_thinking: Optional[tuple[bool, bool]] = None,
    saved: Optional[dict[str, Any]] = None,
) -> ModelControls:
    """The controls a model has, with its defaults.

    `local`: the provider is a server on the user's machine. Then `server_thinking` is
    what the server said about thinking (None when it did not say) and
    `recommended_thinking` is the recommendation table's (available, default).
    `saved`: the user's saved settings for this model (model_config.get), whose
    `thinking` / `reasoning_effort` become the defaults."""
    saved = saved or {}
    row = table_row(provider, bare_model)
    if row is not None:
        controls = row
    elif local:
        available = server_thinking
        rec_default = None
        if recommended_thinking is not None:
            rec_available, rec_default = recommended_thinking
            if available is None:
                available = rec_available
        if available:
            # A thinking model thinks unless told not to (Ollama, llama.cpp templates).
            default = True if rec_default is None else bool(rec_default)
            controls = ModelControls(ThinkingControl(Support.SUPPORTED, default), ReasoningControl())
        else:
            controls = NONE
    else:
        controls = NONE
    return _with_saved_defaults(controls, saved)


def _with_saved_defaults(controls: ModelControls, saved: dict[str, Any]) -> ModelControls:
    thinking, reasoning = controls.thinking, controls.reasoning
    if thinking.support is Support.SUPPORTED and isinstance(saved.get("thinking"), bool):
        thinking = ThinkingControl(Support.SUPPORTED, saved["thinking"])
    effort = saved.get("reasoning_effort")
    if reasoning.support is Support.SUPPORTED and effort in {lvl.value for lvl in reasoning.levels}:
        reasoning = ReasoningControl(Support.SUPPORTED, reasoning.levels, EffortLevel(effort))
    return ModelControls(thinking, reasoning)
