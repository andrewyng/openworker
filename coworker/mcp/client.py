"""MCPManager — our own thin async MCP client over the official `mcp` SDK.

Async-native (no `nest_asyncio`, no second event loop): each server runs in a dedicated
asyncio task that opens the transport + `ClientSession`, keeps them alive until shutdown,
then closes them in the *same* task — required because the SDK's transports use anyio cancel
scopes that must be entered and exited on one task. Tool calls are awaited from any task on
the same loop, which is safe.

Tool execution from the (sync) ToolRegistry bridges back here via
`run_coroutine_threadsafe` — see `coworker/mcp/tools.py`.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from typing import Any, Hashable, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from .config import MCPServerDef


class _Conn:
    def __init__(self, session: ClientSession, tools: list[Any]) -> None:
        self.session = session
        self.tools = tools  # list[mcp.types.Tool]
        self.shutdown = asyncio.Event()
        self.connection_key: Optional[Hashable] = None
        self.definition: Optional[str] = None
        self.retired = False


class MCPManager:
    """Owns definition-aware persistent MCP connections, keyed independently of display name."""

    def __init__(self, secrets: Any = None) -> None:
        self._conns: dict[Hashable, _Conn] = {}
        self._tasks: dict[Hashable, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        # SecretStore for OAuth servers' token persistence (mcp/oauth.py); lazy default
        # so library/CLI construction without secrets keeps working.
        self._secrets = secrets

    async def ensure(
        self,
        server: MCPServerDef,
        *,
        interactive: bool = False,
        connection_key: Optional[Hashable] = None,
    ) -> _Conn:
        """Return a live connection for this exact `server` definition.

        `interactive=True` (explicit connect actions only) lets an OAuth server run
        the browser sign-in flow; the default refuses it — stored tokens and silent
        refresh still work, but a server that insists on re-authorization raises
        InteractiveAuthRequired instead of hijacking the user's browser.

        A public server name is not enough to isolate a trusted connection. Internal
        callers may supply an opaque ``connection_key`` that user configuration cannot
        represent; configured servers fall back to their public name. Within that key,
        a changed command, transport, endpoint, environment, or credentials
        configuration replaces the older connection. Same-definition callers share
        one live connection, while callers holding an older connection cannot be
        routed to its replacement through :meth:`call_on_connection`.
        """
        key = server.name if connection_key is None else connection_key
        definition = _definition_fingerprint(server)
        async with self._lock:
            existing = self._conns.get(key)
            if existing is not None and existing.definition == definition:
                return existing
            if existing is not None:
                await self._retire_locked(key, existing)
            ready: asyncio.Future = asyncio.get_running_loop().create_future()
            task = asyncio.create_task(
                self._serve(
                    server,
                    ready,
                    interactive=interactive,
                    connection_key=key,
                )
            )
            self._tasks[key] = task
            try:
                conn = await ready  # propagates connection errors
            except BaseException:
                # Cancellation while startup is pending must not leave _serve
                # blocked forever on its private shutdown event. The task is still
                # ours here because the definition lock serializes replacements.
                if self._tasks.get(key) is task:
                    self._tasks.pop(key, None)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
            conn.connection_key = key
            conn.definition = definition
            conn.retired = False
            self._conns[key] = conn
            return conn

    async def tools(self, server: MCPServerDef) -> list[Any]:
        return (await self.ensure(server)).tools

    async def call(
        self, name: str, tool: str, arguments: Optional[dict[str, Any]]
    ) -> Any:
        conn = self._conns.get(name)
        if conn is None:
            raise RuntimeError(f"MCP server not connected: {name}")
        return await self.call_on_connection(conn, tool, arguments)

    async def call_on_connection(
        self, conn: _Conn, tool: str, arguments: Optional[dict[str, Any]]
    ) -> Any:
        """Call through one exact connection instead of looking it up by name.

        This is the safe callable binding for isolated integrations. If another
        definition replaced the name since a tool was prepared, the old callable
        fails closed rather than being redirected to the new server.
        """
        if (
            conn.retired
            or conn.connection_key is None
            or self._conns.get(conn.connection_key) is not conn
        ):
            raise RuntimeError("MCP connection was replaced; prepare tools again")
        result = await conn.session.call_tool(tool, arguments or {})
        return _result_payload(result)

    async def aclose(self) -> None:
        async with self._lock:
            conns = list(self._conns.values())
            tasks = list(self._tasks.values())
            for conn in conns:
                conn.retired = True
                conn.shutdown.set()
            self._conns.clear()
            self._tasks.clear()
        for task in tasks:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except (asyncio.TimeoutError, Exception):
                task.cancel()

    async def _retire_locked(self, key: Hashable, conn: _Conn) -> None:
        """Retire one replaced connection while the definition lock is held."""
        conn.retired = True
        conn.shutdown.set()
        task = self._tasks.get(key)
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except (asyncio.TimeoutError, Exception):
                task.cancel()
        if self._conns.get(key) is conn:
            self._conns.pop(key, None)
        if task is not None and self._tasks.get(key) is task and task.done():
            self._tasks.pop(key, None)

    # -- per-server lifecycle (one task owns enter+exit) ------------------------
    async def _serve(
        self,
        server: MCPServerDef,
        ready: asyncio.Future,
        *,
        interactive: bool = False,
        connection_key: Hashable,
    ) -> None:
        conn: Optional[_Conn] = None
        task = asyncio.current_task()
        try:
            async with AsyncExitStack() as stack:
                if server.transport == "http":
                    if not server.url:
                        raise ValueError(
                            f"MCP server '{server.name}' is http but has no url"
                        )
                    auth = None
                    if server.auth == "oauth":
                        from ..secrets import SecretStore
                        from .oauth import build_auth

                        if self._secrets is None:
                            self._secrets = SecretStore()
                        auth = build_auth(
                            server.name,
                            server.url,
                            self._secrets,
                            interactive=interactive,
                        )
                    read, write, *_ = await stack.enter_async_context(
                        streamablehttp_client(
                            server.url, headers=server.headers or None, auth=auth
                        )
                    )
                else:
                    if not server.command:
                        raise ValueError(
                            f"MCP server '{server.name}' is stdio but has no command"
                        )
                    params = StdioServerParameters(
                        command=server.command,
                        args=server.args,
                        env=server.env or None,
                        cwd=server.cwd,
                    )
                    read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listed = await session.list_tools()
                conn = _Conn(session, list(listed.tools))
                if not ready.done():
                    ready.set_result(conn)
                await conn.shutdown.wait()
        except Exception as exc:  # connection / init failure
            if not ready.done():
                ready.set_exception(exc)
        finally:
            # A replacement task may already own this key. Never let a retiring
            # task erase its successor's registration.
            if self._tasks.get(connection_key) is task:
                self._tasks.pop(connection_key, None)
            if conn is not None and self._conns.get(connection_key) is conn:
                self._conns.pop(connection_key, None)


def _definition_fingerprint(server: MCPServerDef) -> str:
    """Stable identity for fields that select or authenticate an MCP endpoint."""
    return json.dumps(
        {
            "name": server.name,
            "transport": server.transport,
            "command": server.command,
            "args": server.args,
            "env": server.env,
            "cwd": server.cwd,
            "url": server.url,
            "headers": server.headers,
            "enabled": server.enabled,
            "include_tools": server.include_tools,
            "exclude_tools": server.exclude_tools,
            "requires_approval": server.requires_approval,
            "auth": server.auth,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _result_payload(result: Any) -> Any:
    """Flatten a CallToolResult into something the engine can serialize for the model."""
    texts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            texts.append(text)
        else:  # non-text content (image/resource) — describe it
            texts.append(f"[{getattr(block, 'type', 'content')}]")
    body = "\n".join(texts)
    if getattr(result, "isError", False):
        return {"error": body or "MCP tool error"}
    structured = getattr(result, "structuredContent", None)
    if structured is not None and not body:
        return structured
    return body
