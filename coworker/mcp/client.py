"""MCPManager — our own thin async MCP client over the official `mcp` SDK.

Async-native (no `nest_asyncio`, no second event loop): each server runs in a dedicated
asyncio task that opens the transport + `ClientSession`, keeps them alive until shutdown,
then closes them in the *same* task — required because the SDK's transports use anyio cancel
scopes that must be entered and exited on one task. Tool calls are awaited from any task on
the same loop, which is safe.

Tool execution from the (sync) ToolRegistry bridges back here via
`run_coroutine_threadsafe` — see `coworker/mcp/tools.py`.

A connection can die under a live session: a remote server restarts and forgets its
session ids (streamable HTTP then answers 404, which the SDK surfaces as
"Session terminated"), or the transport drops. The MCP spec has the client open a new
session; `call` does that once and retries, but only on errors that prove the request
never ran — a tool call is never repeated after it might have executed.
"""

from __future__ import annotations

import asyncio
import tempfile
from contextlib import AsyncExitStack
from typing import Any, IO, Optional

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError

from .config import MCPServerDef


_STDERR_TAIL_LINES = 20
_STDERR_TAIL_CHARS = 1500

# What the SDK's streamable-HTTP client reports when the server 404s a request
# carrying our session id (mcp/client/streamable_http.py `_send_session_terminated_error`).
_SESSION_TERMINATED = "Session terminated"


def _session_lost(exc: BaseException) -> bool:
    """True when `exc` proves the request never reached a live server session, so
    reconnecting and sending it again cannot run the tool twice: the server rejected
    our session id, or the write side of the transport was already closed."""
    if isinstance(exc, McpError):
        return exc.error.message == _SESSION_TERMINATED
    return isinstance(exc, (anyio.ClosedResourceError, anyio.BrokenResourceError))


def _read_tail(errfile: Optional[IO[str]]) -> Optional[str]:
    """Last few lines of a captured stderr file — the crash evidence, not the log."""
    if errfile is None:
        return None
    try:
        errfile.seek(0)
        text = errfile.read()
    except (OSError, ValueError):
        return None
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    return "\n".join(lines[-_STDERR_TAIL_LINES:])[-_STDERR_TAIL_CHARS:]


class _Conn:
    def __init__(
        self,
        session: ClientSession,
        tools: list[Any],
        server: Optional[MCPServerDef] = None,
    ) -> None:
        self.session = session
        self.tools = tools  # list[mcp.types.Tool]
        self.shutdown = asyncio.Event()
        # The definition this connection was opened from — what a reconnect reopens.
        self.server = server


class MCPManager:
    """Owns persistent MCP connections keyed by server name; lazy-connects on demand."""

    def __init__(self, secrets: Any = None) -> None:
        self._conns: dict[str, _Conn] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._stderr_tails: dict[str, str] = {}
        self._lock = asyncio.Lock()
        # Servers whose connection died on its own (not shut down on purpose), kept so
        # the next tool call reopens them instead of failing "not connected" forever.
        self._lost: dict[str, MCPServerDef] = {}
        self._reopen_locks: dict[str, asyncio.Lock] = {}
        # SecretStore for OAuth servers' token persistence (mcp/oauth.py); lazy default
        # so library/CLI construction without secrets keeps working.
        self._secrets = secrets

    async def ensure(self, server: MCPServerDef, *, interactive: bool = False) -> _Conn:
        """Return a live connection for `server`, connecting (once) if needed.

        `interactive=True` (explicit connect actions only) lets an OAuth server run
        the browser sign-in flow; the default refuses it — stored tokens and silent
        refresh still work, but a server that insists on re-authorization raises
        InteractiveAuthRequired instead of hijacking the user's browser.
        """
        async with self._lock:
            existing = self._conns.get(server.name)
            if existing is not None:
                return existing
            ready: asyncio.Future = asyncio.get_running_loop().create_future()
            self._tasks[server.name] = asyncio.create_task(
                self._serve(server, ready, interactive=interactive)
            )
            conn = await ready  # propagates connection errors
            self._conns[server.name] = conn
            self._lost.pop(server.name, None)
            return conn

    async def tools(self, server: MCPServerDef) -> list[Any]:
        return (await self.ensure(server)).tools

    async def verify(self, server: MCPServerDef, *, interactive: bool = False) -> _Conn:
        """A REAL health check for explicit Test actions. `ensure` returns a cached
        connection untouched, which made Test-on-Live a silent no-op that could not
        detect a dead server (owner-hit 2026-08-21). Here a cached connection is
        round-tripped (tools/list, refreshing the tool set); a dead one is torn
        down and reconnected fresh."""
        conn = self._conns.get(server.name)
        if conn is not None:
            try:
                listed = await asyncio.wait_for(conn.session.list_tools(), timeout=20)
                conn.tools = list(listed.tools)
                return conn
            except Exception:
                await self._drop(server.name, conn)
        return await self.ensure(server, interactive=interactive)

    def last_stderr(self, name: str) -> Optional[str]:
        """Stderr tail from the most recent failed startup of `name`, if any."""
        return self._stderr_tails.get(name)

    def forget(self, name: str) -> None:
        """The user closed `name` on purpose (removed, signed out, disconnected): a
        connection that died earlier must not be reopened by a later tool call.
        A plain dict pop, so it is safe from the server's threadpool routes."""
        self._lost.pop(name, None)

    async def call(
        self, name: str, tool: str, arguments: Optional[dict[str, Any]]
    ) -> Any:
        conn = self._conns.get(name)
        if conn is None:
            lost = self._lost.get(name)
            if lost is None:
                raise RuntimeError(f"MCP server not connected: {name}")
            conn = await self._reopen(lost, None)
            return _result_payload(await conn.session.call_tool(tool, arguments or {}))
        try:
            result = await conn.session.call_tool(tool, arguments or {})
        except Exception as exc:
            if conn.server is None or not _session_lost(exc):
                raise
            # One fresh session, one retry: a second failure surfaces as-is.
            conn = await self._reopen(conn.server, conn)
            result = await conn.session.call_tool(tool, arguments or {})
        return _result_payload(result)

    async def aclose(self) -> None:
        self._lost.clear()
        for conn in self._conns.values():
            conn.shutdown.set()
        for task in list(self._tasks.values()):
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except (asyncio.TimeoutError, Exception):
                task.cancel()
        self._conns.clear()
        self._tasks.clear()

    async def _reopen(self, server: MCPServerDef, dead: Optional[_Conn]) -> _Conn:
        """Replace a dead connection with a fresh session (never an interactive
        sign-in). Serialized per server, so calls that fail together on one dead
        session share a single reconnect instead of each opening their own."""
        lock = self._reopen_locks.setdefault(server.name, asyncio.Lock())
        async with lock:
            current = self._conns.get(server.name)
            if current is not None and current is not dead:
                return current  # another caller already reconnected
            if dead is not None:
                await self._drop(server.name, dead)
            # Reconnectable until a fresh session is up (ensure clears it): if the
            # server is still down, the next call tries again instead of failing
            # "not connected" for good.
            self._lost[server.name] = server
            return await self.ensure(server)

    async def _drop(self, name: str, conn: _Conn) -> None:
        """Shut down `conn` and wait (briefly) for its task to close the transport."""
        conn.shutdown.set()
        task = self._tasks.pop(name, None)
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except Exception:
                task.cancel()
        if self._conns.get(name) is conn:  # _serve pops too; belt and braces
            self._conns.pop(name, None)

    # -- per-server lifecycle (one task owns enter+exit) ------------------------
    async def _serve(
        self, server: MCPServerDef, ready: asyncio.Future, *, interactive: bool = False
    ) -> None:
        errfile = None
        conn: Optional[_Conn] = None
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
                    # Capture the child's stderr so a startup crash leaves evidence
                    # the UI can show (the SDK needs a real file descriptor here).
                    errfile = tempfile.TemporaryFile(
                        mode="w+", encoding="utf-8", errors="replace"
                    )
                    read, write = await stack.enter_async_context(
                        stdio_client(params, errlog=errfile)
                    )
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listed = await session.list_tools()
                conn = _Conn(session, list(listed.tools), server)
                self._stderr_tails.pop(server.name, None)
                if not ready.done():
                    ready.set_result(conn)
                await conn.shutdown.wait()
        except Exception as exc:  # connection / init failure
            tail = _read_tail(errfile)
            if tail:
                self._stderr_tails[server.name] = tail
            if not ready.done():
                ready.set_exception(exc)
        finally:
            if errfile is not None:
                try:
                    errfile.close()
                except OSError:
                    pass
            if conn is not None and not conn.shutdown.is_set():
                # Died under us (transport error, server gone) rather than closed on
                # purpose: the next tool call reopens it instead of failing for good.
                self._lost[server.name] = server
            # Pop only our own entries: a late-cancelled task must not evict the
            # connection a reconnect has already put in its place.
            if conn is not None and self._conns.get(server.name) is conn:
                self._conns.pop(server.name, None)
            if self._tasks.get(server.name) is asyncio.current_task():
                self._tasks.pop(server.name, None)


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
