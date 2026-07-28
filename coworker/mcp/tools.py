"""Turn MCP tools into ToolRegistry-ready callables.

Each MCP tool becomes a sync callable (so it fits the registry's `execute` contract, which
the engine already runs via `asyncio.to_thread`). The callable bridges back to the live
async session on the server loop via `run_coroutine_threadsafe`. We attach `ToolMetadata`
(category="mcp", `requires_approval` per config) so the PermissionEngine gates it, and an
explicit OpenAI schema built straight from the MCP `inputSchema` for fidelity.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

import aisuite as ai

from ..integrations.kordoc import KORDOC_MCP_TOOL_ALLOWLIST
from .config import MCPServerDef

CallAsync = Callable[[str, dict[str, Any]], Awaitable[Any]]
ArgumentValidator = Callable[[str, dict[str, Any]], str | None]

_NAME_OK = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_NAME = 64  # OpenAI function-name limit

# The pinned Kordoc 4.2.3 RAG surface is read-only. Every allowed operation
# accepts one source document via ``file_path``; it does not accept output,
# render, profile, index, or embedding paths.
_KORDOC_FILE_PATH_TOOLS = frozenset(KORDOC_MCP_TOOL_ALLOWLIST)


def tool_name(server: str, tool: str) -> str:
    """`mcp__<server>__<tool>`, sanitized to OpenAI's `[A-Za-z0-9_-]{1,64}` rule."""
    base = f"mcp__{_NAME_OK.sub('_', server)}__{_NAME_OK.sub('_', tool)}"
    if len(base) > _MAX_NAME:
        base = base[:_MAX_NAME]
    return base


def _openai_schema(name: str, mcp_tool: Any) -> dict[str, Any]:
    params = getattr(mcp_tool, "inputSchema", None) or {
        "type": "object",
        "properties": {},
    }
    description = (getattr(mcp_tool, "description", None) or "")[:1024]
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": params},
    }


def _filtered(mcp_tools: list[Any], server: MCPServerDef) -> list[Any]:
    out = mcp_tools
    if server.include_tools is not None:
        allow = set(server.include_tools)
        out = [t for t in out if t.name in allow]
    if server.exclude_tools:
        block = set(server.exclude_tools)
        out = [t for t in out if t.name not in block]
    return out


def validate_kordoc_workspace_arguments(
    tool: str, arguments: dict[str, Any], workspace: str | Path | None
) -> str | None:
    """Fail closed and canonicalize a pinned Kordoc RAG source path.

    The result is intentionally a path-free tool error. It is returned by the
    sync wrapper before any MCP coroutine is created, so rejected requests never
    reach the Kordoc process. A validated relative path is replaced in-place with
    its canonical absolute form so the MCP process cannot resolve it against a
    different working directory.
    """
    if tool not in _KORDOC_FILE_PATH_TOOLS:
        return "Kordoc blocked this call: the requested tool is outside the pinned RAG surface."
    try:
        root = Path(workspace).expanduser().resolve(strict=True) if workspace else None
    except (OSError, RuntimeError, ValueError):
        root = None
    if root is None or not root.is_dir():
        return "Kordoc blocked this call: the session workspace is unavailable."

    canonical, error = _canonical_kordoc_input_path(arguments.get("file_path"), root)
    if error:
        return _kordoc_path_error("file_path", error)
    arguments["file_path"] = str(canonical)
    return None


def _canonical_kordoc_input_path(value: Any, root: Path) -> tuple[Path | None, str | None]:
    candidate, error = _kordoc_path_candidate(value, root)
    if error:
        return None, error
    try:
        canonical = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None, "it must be an existing regular file"
    if not _inside_workspace(canonical, root):
        return None, "it resolves outside the session workspace"
    if not canonical.is_file():
        return None, "it must be an existing regular file"
    return canonical, None


def _kordoc_path_candidate(value: Any, root: Path) -> tuple[Path, str | None]:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return root, "it must be a non-empty path string"
    try:
        requested = Path(value).expanduser()
    except (OSError, RuntimeError, ValueError):
        return root, "it must be a valid path string"
    if ".." in requested.parts:
        return root, "path traversal is not allowed"
    if requested.is_absolute():
        # Do this lexical check before resolve(strict=True). On Windows, resolving
        # an outside UNC path can contact an SMB server before we have decided the
        # path is trusted.
        try:
            requested.relative_to(root)
        except ValueError:
            return root, "absolute paths outside the session workspace are not allowed"
        return requested, None
    if requested.drive or requested.root:
        # Drive-relative (C:foo) and rooted-without-drive (\foo) paths can discard
        # parts of ``root`` when joined, so they are not ordinary relative paths.
        return root, "rooted paths are not allowed"
    return root / requested, None


def _inside_workspace(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _kordoc_path_error(field: str, detail: str) -> str:
    return f"Kordoc blocked {field}: {detail}. Use a path inside the session workspace."


def build_callables(
    server: MCPServerDef,
    mcp_tools: list[Any],
    call_async: CallAsync,
    loop: asyncio.AbstractEventLoop,
    *,
    timeout: float = 120.0,
    argument_validator: ArgumentValidator | None = None,
) -> list[Callable[..., Any]]:
    """Wrap a server's (filtered) MCP tools as registry-ready callables."""
    callables: list[Callable[..., Any]] = []
    for mcp_tool in _filtered(mcp_tools, server):
        name = tool_name(server.name, mcp_tool.name)
        remote = mcp_tool.name

        def _invoke(_remote: str = remote, **kwargs: Any) -> Any:
            if argument_validator is not None:
                error = argument_validator(_remote, kwargs)
                if error:
                    return {"error": error}
            future = asyncio.run_coroutine_threadsafe(call_async(_remote, kwargs), loop)
            return future.result(timeout)

        # We attach the schema + metadata explicitly (rather than via `ai.tool`, which would
        # try to derive a schema from this `**kwargs` wrapper): the registry reads both attrs.
        _invoke.__name__ = name
        _invoke.__doc__ = (
            getattr(mcp_tool, "description", None)
            or f"MCP tool {remote} from {server.name}"
        )
        _invoke.__aisuite_tool_metadata__ = ai.ToolMetadata(
            name=name,
            category="mcp",
            risk_level="medium",
            capabilities=[server.name],
            requires_approval=server.requires_approval,
        )
        _invoke.__coworker_schema__ = _openai_schema(name, mcp_tool)
        callables.append(_invoke)
    return callables
