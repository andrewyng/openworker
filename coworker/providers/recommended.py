"""Recommended settings per model, from the model makers' own cards.

The table is `recommended_models.json` next to this file, edited in git. Each entry names
patterns a model id can match (without its provider prefix, so `ollama:nemotron-3.5-
lightning:30b-mlx` and vLLM's `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` find
the same entry), the window the model was trained for, the window its maker suggests for
agent work, the longest reply, whether it can think and whether that is on by default,
and the sampling values. Where a card gives separate values for reasoning and tool
calling, the table carries the tool-calling ones: that is OpenWorker's work.

The table is advice. The per-model settings a user saves (coworker/model_config.py) win,
and the machine's memory still caps the context (providers/local_machine.py).
"""

from __future__ import annotations

import fnmatch
import functools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_TABLE = Path(__file__).with_name("recommended_models.json")


@dataclass(frozen=True)
class Recommendation:
    name: str
    context_max: Optional[int] = None
    context_for_agents: Optional[int] = None
    max_output_tokens: Optional[int] = None
    thinking_available: bool = False
    thinking_default: bool = False
    sampling: dict[str, float] = field(default_factory=dict)
    notes: str = ""
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "context_max": self.context_max,
            "context_for_agents": self.context_for_agents,
            "max_output_tokens": self.max_output_tokens,
            "thinking": {"available": self.thinking_available, "default": self.thinking_default},
            "sampling": dict(self.sampling),
            "notes": self.notes,
            "source": self.source,
        }


@functools.lru_cache(maxsize=1)
def _entries() -> tuple[tuple[tuple[str, ...], Recommendation], ...]:
    try:
        raw = json.loads(_TABLE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    out = []
    for item in raw.get("models") or []:
        if not isinstance(item, dict) or not item.get("match"):
            continue
        thinking = item.get("thinking") or {}
        rec = Recommendation(
            name=str(item.get("name") or ""),
            context_max=_int(item.get("context_max")),
            context_for_agents=_int(item.get("context_for_agents")),
            max_output_tokens=_int(item.get("max_output_tokens")),
            thinking_available=bool(thinking.get("available")),
            thinking_default=bool(thinking.get("default")),
            sampling={
                k: float(v)
                for k, v in (item.get("sampling") or {}).items()
                if isinstance(v, (int, float))
            },
            notes=str(item.get("notes") or ""),
            source=str(item.get("source") or ""),
        )
        out.append((tuple(str(m) for m in item["match"]), rec))
    return tuple(out)


def _int(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def bare_name(model: str) -> str:
    """`ollama:qwen3-coder:30b` → `qwen3-coder:30b`; a name with no known prefix is itself.
    Model names carry colons of their own (version tags), so only a known provider
    prefix is stripped."""
    from .registry import provider_names

    vendor, sep, rest = model.partition(":")
    if sep and vendor in provider_names():
        return rest
    return model


def recommendation_for(model: str) -> Optional[Recommendation]:
    """The table's entry for a model id, or None. Case does not matter."""
    name = bare_name(model).lower()
    for patterns, rec in _entries():
        if any(fnmatch.fnmatchcase(name, p.lower()) for p in patterns):
            return rec
    return None
