"""Turn MCP tools into ToolRegistry-ready callables.

Each MCP tool becomes a sync callable (so it fits the registry's `execute` contract, which
the engine already runs via `asyncio.to_thread`). The callable bridges back to the live
async session on the server loop via `run_coroutine_threadsafe`. We attach `ToolMetadata`
(category="mcp", `requires_approval` per config) so the PermissionEngine gates it, and an
explicit OpenAI schema built straight from the MCP `inputSchema` for fidelity.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any, Awaitable, Callable

import aisuite as ai

from .config import MCPServerDef

CallAsync = Callable[[str, dict[str, Any]], Awaitable[Any]]

logger = logging.getLogger(__name__)

_NAME_OK = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_NAME = 64  # OpenAI function-name limit
_HASH_CHARS = 7  # digest suffix on truncated names; 56 + 1 + 7 = 64


def full_tool_name(server: str, tool: str) -> str:
    """The untruncated `mcp__<server>__<tool>`, sanitized — what a model that reads
    the server's own tool list (or skill text naming the tool) naturally calls."""
    return f"mcp__{_NAME_OK.sub('_', server)}__{_NAME_OK.sub('_', tool)}"


def tool_name(server: str, tool: str) -> str:
    """`mcp__<server>__<tool>`, sanitized to OpenAI's `[A-Za-z0-9_-]{1,64}` rule.

    A plain cut at 64 made every long tool's registry key its first 64 characters,
    so tools behind an aggregator namespace collided and became unreachable, while
    the model's full-name call failed as `unknown tool` (#746). The truncated name
    instead keeps a 56-char prefix plus a digest of the full name: two long tools
    never collapse onto the same key. The full name stays dispatchable as an alias
    (see `build_callables`)."""
    base = full_tool_name(server, tool)
    if len(base) > _MAX_NAME:
        digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:_HASH_CHARS]
        base = f"{base[: _MAX_NAME - _HASH_CHARS - 1]}_{digest}"
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


def build_callables(
    server: MCPServerDef,
    mcp_tools: list[Any],
    call_async: CallAsync,
    loop: asyncio.AbstractEventLoop,
    *,
    timeout: float = 120.0,
) -> list[Callable[..., Any]]:
    """Wrap a server's (filtered) MCP tools as registry-ready callables."""
    callables: list[Callable[..., Any]] = []
    truncated: list[str] = []
    for mcp_tool in _filtered(mcp_tools, server):
        name = tool_name(server.name, mcp_tool.name)
        remote = mcp_tool.name

        # Bind `remote` in a dedicated closure rather than a default argument. The
        # default-argument form (`_remote=remote`) also defeats late binding, but it
        # exposes the routing target as a caller-overridable keyword: a model-supplied
        # `_remote` in the tool-call arguments would then re-route an approved, filtered
        # call to any other tool on this server, since the approval gate and the
        # include/exclude filter only ever see the visible tool name.
        def _make_invoke(remote: str) -> Callable[..., Any]:
            def _invoke(**kwargs: Any) -> Any:
                future = asyncio.run_coroutine_threadsafe(
                    call_async(remote, kwargs), loop
                )
                return future.result(timeout)

            return _invoke

        _invoke = _make_invoke(remote)

        # We attach the schema + metadata explicitly (rather than via `ai.tool`, which would
        # try to derive a schema from this `**kwargs` wrapper): the registry reads both attrs.
        _invoke.__name__ = name
        # #746: the schema shows the truncated name (the only one within the 64-char
        # limit), but a model that emits the full name — from skill text or the
        # server's own listing — still dispatches to this same callable, instead of
        # dying as `unknown tool`. The registry resolves the alias; schemas(), the
        # approval gate and the audit trail keep using the canonical name.
        full = full_tool_name(server.name, mcp_tool.name)
        if full != name:
            _invoke.__coworker_aliases__ = (full,)
            truncated.append(remote)
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
        # OPE-136 finding 4: where this call actually goes, for the approval card's
        # scope chip. From the server DEF (user-authored config), never from anything
        # the server itself claims. http → the remote host; stdio → a local process.
        _invoke.__coworker_mcp_destination__ = {
            "transport": server.transport,
            "host": _server_host(server),
        }
        callables.append(_invoke)
    if truncated:
        # Connect-time visibility (#746): the rename is silent otherwise, and a user
        # with per-tool rules keyed on the old names needs to know they changed.
        logger.warning(
            "mcp %s: %d tool name(s) exceed %d characters and are registered under "
            "hashed names (e.g. %s); a shorter server key keeps names intact",
            server.name,
            len(truncated),
            _MAX_NAME,
            truncated[0],
        )
    return callables


def _server_host(server: MCPServerDef) -> str:
    """The hostname an HTTP server's calls reach (lowercased), "" for stdio/unparseable."""
    if not server.url:
        return ""
    try:
        from urllib.parse import urlparse

        return (urlparse(server.url).hostname or "").lower()
    except ValueError:  # pragma: no cover - urlparse rarely raises, but fail to ""
        return ""
