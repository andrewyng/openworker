"""Connector tool catalog and local enablement policy."""

from __future__ import annotations

from typing import Any, Optional

from ..secrets import SecretStore
from .tool_def_model import ConnectorToolDef
from .tool_def_catalog_1 import TOOL_DEFS as _TOOL_DEFS_1
from .tool_def_catalog_2 import TOOL_DEFS as _TOOL_DEFS_2
from .tool_def_catalog_3 import TOOL_DEFS as _TOOL_DEFS_3

TOOL_DEFS: tuple[ConnectorToolDef, ...] = (
    *_TOOL_DEFS_1,
    *_TOOL_DEFS_2,
    *_TOOL_DEFS_3,
)
_KIND_BY_NAME = {d.name: d.kind for d in TOOL_DEFS}


# §36: the registry's read/write kind is the SINGLE source of truth for whether a
# connector tool gates. Reads on a service the user explicitly connected never ask
# (the §25 design note — "reads never gate" — made law); writes always do. Tools
# without a registry entry keep their call-site default (MCP/experimental stay
# conservative).
def approval_for_tool(name: str, default: bool = True) -> bool:
    kind = _KIND_BY_NAME.get(name)
    if kind is None:
        return default
    return kind != "read"


TOOL_TO_CONNECTOR = {d.name: d.connector for d in TOOL_DEFS}
TOOLS_BY_CONNECTOR: dict[str, list[ConnectorToolDef]] = {}
for _def in TOOL_DEFS:
    TOOLS_BY_CONNECTOR.setdefault(_def.connector, []).append(_def)

# Standing-rule target arguments (§25). Declared on connector tool defs above, plus the
# always-available messaging tool `send_message` (its `target` is the reply handle
# "platform:chat_id" — exactly the address a rule pins). This dict is the single source of
# which tools can EVER carry a standing rule: exec/destructive tools must never appear here.
TARGET_ARGS: dict[str, str] = {d.name: d.target_arg for d in TOOL_DEFS if d.target_arg}
TARGET_ARGS["send_message"] = "target"


def target_arg_for(tool_name: str) -> Optional[str]:
    """The argument that names this tool's standing-rule target, or None if the tool
    isn't eligible for standing rules."""
    return TARGET_ARGS.get(tool_name)


def connector_for_tool(tool_name: str) -> str | None:
    return TOOL_TO_CONNECTOR.get(tool_name)


def load_tool_settings(secrets: SecretStore, connector: str) -> dict[str, bool]:
    raw = secrets.get(f"{connector}:tools") or {}
    enabled = raw.get("enabled") if isinstance(raw, dict) else None
    return {str(k): bool(v) for k, v in (enabled or {}).items()}


def tool_enabled(secrets: SecretStore, connector: str, tool_name: str) -> bool:
    overrides = load_tool_settings(secrets, connector)
    if tool_name in overrides:
        return overrides[tool_name]
    for tool in TOOLS_BY_CONNECTOR.get(connector, []):
        if tool.name == tool_name:
            return tool.default_enabled
    return False


def patch_tool_settings(
    secrets: SecretStore, connector: str, enabled: dict[str, Any]
) -> dict[str, Any]:
    known = {t.name for t in TOOLS_BY_CONNECTOR.get(connector, [])}
    if not known:
        return {"ok": False, "error": "unknown connector or no tools"}
    current = load_tool_settings(secrets, connector)
    for name, value in enabled.items():
        if name in known:
            current[name] = bool(value)
    secrets.put(f"{connector}:tools", {"enabled": current})
    return {"ok": True, "tools": current}


def mcp_tool_defs(connector: str) -> list[ConnectorToolDef]:
    """The connector's PINNED MCP tools (names `mcp__<connector>__<vendor tool>`)."""
    return [
        t for t in TOOLS_BY_CONNECTOR.get(connector, []) if t.name.startswith("mcp__")
    ]


def mcp_pinned_tools(connector: str) -> list[str]:
    """Vendor-side tool names of the pinned allowlist (prefix stripped) — what goes
    into the seeded server config's `include_tools`."""
    prefix = f"mcp__{connector}__"
    return [t.name.removeprefix(prefix) for t in mcp_tool_defs(connector)]


def active_tool_defs(secrets: SecretStore, connector: str) -> list[ConnectorToolDef]:
    """The defs live for this connector's CURRENT profile. A connector with both an
    API tool set and a pinned MCP set (jira) exposes exactly one of them, following
    the profile's mode; single-set connectors are unaffected."""
    defs = TOOLS_BY_CONNECTOR.get(connector, [])
    mcp = [t for t in defs if t.name.startswith("mcp__")]
    api = [t for t in defs if not t.name.startswith("mcp__")]
    if not mcp or not api:
        return defs
    profile = secrets.get(f"{connector}:default") or {}
    return mcp if profile.get("mode") == "mcp" else api


def tool_dicts(secrets: SecretStore, connector: str) -> list[dict[str, Any]]:
    overrides = load_tool_settings(secrets, connector)
    out = []
    for tool in active_tool_defs(secrets, connector):
        out.append(
            {
                "name": tool.name,
                "label": tool.label,
                "kind": tool.kind,
                "description": tool.description,
                "enabled": bool(overrides.get(tool.name, tool.default_enabled)),
                "requires_approval": True,
            }
        )
    return out
