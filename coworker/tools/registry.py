"""Tool registry — wraps callables (incl. aisuite toolkit tools) into a registry the
runtime owns: JSON schemas for the model, plus execution. Permission checks live in the
PermissionEngine and are applied by the turn engine, not here.

Schema generation is reused from aisuite (`Tools`) so we don't reimplement
docstring/type-hint → JSON-schema extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from aisuite.utils.tools import Tools


@dataclass
class ToolSpec:
    name: str
    schema: dict[str, Any]  # OpenAI-format function tool schema
    func: Callable[..., Any]
    metadata: Any = None  # aisuite ToolMetadata or None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        # Alternate dispatch names (e.g. the untruncated `mcp__…` name a model emits
        # even though the schema showed a <=64-char one, #746). Aliases resolve to the
        # canonical spec — they never add schemas or bypass the permission gates, which
        # key on the spec the alias points at.
        self._aliases: dict[str, str] = {}

    def register(
        self,
        func: Callable[..., Any],
        *,
        metadata: Any = None,
        schema: Optional[dict[str, Any]] = None,
    ) -> ToolSpec:
        name = getattr(func, "__name__", None)
        if not name:
            raise ValueError("Tool function must have a __name__.")
        meta = metadata or getattr(func, "__aisuite_tool_metadata__", None)
        # Allow an explicit schema override (param or a `__coworker_schema__` attribute)
        # for tools whose signature can't be auto-converted to a valid JSON schema.
        resolved_schema = (
            schema or getattr(func, "__coworker_schema__", None) or _schema_for(func)
        )
        spec = ToolSpec(name=name, schema=resolved_schema, func=func, metadata=meta)
        self._tools[name] = spec
        for alias in getattr(func, "__coworker_aliases__", None) or ():
            self._aliases[alias] = name
        return spec

    def register_all(self, funcs: list[Callable[..., Any]]) -> None:
        for func in funcs:
            self.register(func)

    def names(self) -> list[str]:
        return list(self._tools)

    def clone(self, *, exclude: Optional[set[str]] = None) -> "ToolRegistry":
        """A new registry over the same ToolSpecs, minus `exclude` — how a subagent
        inherits its parent's tools without the spawn tools themselves (no recursion)."""
        out = ToolRegistry()
        for name, spec in self._tools.items():
            if exclude and name in exclude:
                continue
            out._tools[name] = spec
        out._aliases = {
            alias: target
            for alias, target in self._aliases.items()
            if target in out._tools
        }
        return out

    def _resolve(self, name: str) -> str:
        return self._aliases.get(name, name)

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(self._resolve(name))

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.schema for spec in self._tools.values()]

    def execute(self, name: str, arguments: Optional[dict[str, Any]] = None) -> Any:
        spec = self._tools.get(self._resolve(name))
        if spec is None:
            raise KeyError(f"Tool not registered: {name}")
        return spec.func(**(arguments or {}))


def _schema_for(func: Callable[..., Any]) -> dict[str, Any]:
    """Generate one OpenAI-format tool schema via aisuite's schema generator."""
    return Tools([func]).tools(format="openai")[0]
