"""Adapters that expose OpenWork ToolSpecs to LangChain without bypassing permissions."""

from __future__ import annotations

from typing import Any, Callable

from pydantic import Field, create_model

from ..tools import ToolRegistry, ToolSpec


class OpenWorkToolAdapter:
    """Build LangChain tools from the OpenWork registry through a guarded executor.

    The caller must supply the same authorization/execution function used by TurnEngine.
    This intentionally prevents a Deep Agent from calling ``ToolSpec.func`` directly.
    """

    def __init__(self, registry: ToolRegistry, execute: Callable[[str, dict[str, Any]], Any]) -> None:
        self.registry = registry
        self.execute = execute

    def schemas(self) -> list[dict[str, Any]]:
        return self.registry.schemas()

    def as_langchain(self) -> list[Any]:
        try:
            from langchain_core.tools import StructuredTool
        except ImportError as exc:  # pragma: no cover - dependency gate is tested by available runtime
            raise RuntimeError("langchain-core is required for LangChain tool adaptation") from exc
        out: list[Any] = []
        for spec in (self.registry.get(name) for name in self.registry.names()):
            if spec is not None:
                out.append(self._tool(spec, StructuredTool))
        return out

    def _tool(self, spec: ToolSpec, structured_tool: Any) -> Any:
        schema = (spec.schema.get("function") or {}).get("parameters") or {"type": "object", "properties": {}}
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or ())
        fields = {
            str(name): (Any, Field(...) if name in required else Field(default=None))
            for name in properties
        }
        args_schema = create_model(f"OpenWork_{spec.name}_Args", **fields)

        def invoke(**arguments: Any) -> Any:
            return self.execute(spec.name, arguments)

        return structured_tool.from_function(
            func=invoke,
            name=spec.name,
            description=(spec.schema.get("function") or {}).get("description") or spec.name,
            args_schema=args_schema,
        )
