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
import tempfile
from contextlib import AsyncExitStack
from typing import Any, IO, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from .config import MCPServerDef


_STDERR_TAIL_LINES = 20
_STDERR_TAIL_CHARS = 1500


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
    def __init__(self, session: ClientSession, tools: list[Any]) -> None:
        self.session = session
        self.tools = tools  # list[mcp.types.Tool]
        self.shutdown = asyncio.Event()


class MCPManager:
    """Owns persistent MCP connections keyed by server name; lazy-connects on demand."""

    def __init__(
        self,
        secrets: Any = None,
        *,
        connect_timeout: float = 30.0,
        interactive_timeout: float = 330.0,
    ) -> None:
        self._conns: dict[str, _Conn] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._stderr_tails: dict[str, str] = {}
        self._ready: dict[str, asyncio.Future] = {}
        self._interactive: set[str] = set()
        self._closing = False
        self._disconnecting: set[str] = set()
        self._connect_timeout = connect_timeout
        self._interactive_timeout = interactive_timeout
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
        if self._closing:
            raise RuntimeError("MCP connections are closing; retry shortly")
        if server.name in self._disconnecting:
            raise RuntimeError("MCP server is reloading; retry shortly")
        existing = self._conns.get(server.name)
        if existing is not None:
            return existing
        # No await between lookup and publication: concurrent callers on this loop
        # share one attempt per server without locking unrelated servers out.
        ready = self._ready.get(server.name)
        if ready is not None and not interactive and server.name in self._interactive:
            raise RuntimeError("MCP sign-in in progress; finish connecting from its page")
        if ready is None:
            ready: asyncio.Future = asyncio.get_running_loop().create_future()
            # The last waiter may disconnect before a failed attempt completes.
            ready.add_done_callback(lambda f: None if f.cancelled() else f.exception())
            self._ready[server.name] = ready
            if interactive:
                self._interactive.add(server.name)
            self._tasks[server.name] = asyncio.create_task(
                self._serve(server, ready, interactive=interactive)
            )
        # Cancelling a page/session must not cancel other callers' readiness signal.
        return await asyncio.shield(ready)

    async def tools(self, server: MCPServerDef) -> list[Any]:
        return (await self.ensure(server)).tools

    @staticmethod
    async def _list_tools(session: ClientSession, name: str) -> list[Any]:
        tools: list[Any] = []
        cursor = None
        seen: set[str] = set()
        while True:
            listed = await session.list_tools(cursor=cursor)
            tools.extend(listed.tools)
            cursor = listed.nextCursor
            if not cursor:
                return tools
            if cursor in seen or len(seen) >= 1000:
                raise RuntimeError(f"MCP server '{name}' returned invalid tools pagination")
            seen.add(cursor)

    async def verify(self, server: MCPServerDef, *, interactive: bool = False) -> _Conn:
        """A REAL health check for explicit Test actions. `ensure` returns a cached
        connection untouched, which made Test-on-Live a silent no-op that could not
        detect a dead server (owner-hit 2026-08-21). Here a cached connection is
        round-tripped (tools/list, refreshing the tool set); a dead one is torn
        down and reconnected fresh."""
        if server.name in self._disconnecting:
            raise RuntimeError("MCP server is reloading; retry shortly")
        conn = self._conns.get(server.name)
        if conn is not None:
            try:
                conn.tools = await asyncio.wait_for(
                    self._list_tools(conn.session, server.name), timeout=20
                )
                return conn
            except Exception:
                if self._conns.get(server.name) is conn:
                    await self.disconnect(server.name)
                else:
                    raise RuntimeError("MCP connection changed during verification")
        return await self.ensure(server, interactive=interactive)

    def last_stderr(self, name: str) -> Optional[str]:
        """Stderr tail from the most recent failed startup of `name`, if any."""
        return self._stderr_tails.get(name)

    async def call(
        self, name: str, tool: str, arguments: Optional[dict[str, Any]]
    ) -> Any:
        conn = self._conns.get(name)
        if conn is None:
            raise RuntimeError(f"MCP server not connected: {name}")
        result = await conn.session.call_tool(tool, arguments or {})
        return _result_payload(result)

    async def disconnect(self, name: str) -> None:
        """Close one server, including a pending handshake, before reconfiguration."""
        self._disconnecting.add(name)
        task = self._tasks.get(name)
        ready = self._ready.get(name)
        try:
            conn = self._conns.pop(name, None)
            if conn is not None:
                conn.shutdown.set()
            elif task is not None:
                task.cancel()
            if task is not None:
                _, pending = await asyncio.wait({task}, timeout=5)
                if pending:
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            # A task cancelled before its first instruction never enters _serve's
            # finally block. Unblock its waiters and clear that attempt here too.
            if ready is not None and not ready.done():
                ready.set_exception(RuntimeError(f"MCP server '{name}' disconnected"))
            if self._tasks.get(name) is task:
                self._tasks.pop(name, None)
                self._ready.pop(name, None)
                self._interactive.discard(name)
            self._disconnecting.discard(name)

    async def aclose(self) -> None:
        self._closing = True
        try:
            tasks = list(self._tasks.values())
            for name, task in self._tasks.items():
                conn = self._conns.get(name)
                if conn is not None:
                    conn.shutdown.set()
                else:
                    task.cancel()
            if tasks:
                _, pending = await asyncio.wait(tasks, timeout=5)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            for ready in self._ready.values():
                if not ready.done():
                    ready.set_exception(RuntimeError("MCP connections closed"))
            self._conns.clear()
            self._tasks.clear()
            self._ready.clear()
            self._interactive.clear()
            self._closing = False

    # -- per-server lifecycle (one task owns enter+exit) ------------------------
    async def _serve(
        self, server: MCPServerDef, ready: asyncio.Future, *, interactive: bool = False
    ) -> None:
        errfile = None
        owner = asyncio.current_task()
        timeout = self._interactive_timeout if interactive else self._connect_timeout
        stage = "transport setup"

        def expired() -> None:
            if not ready.done():
                ready.set_exception(
                    TimeoutError(
                        f"MCP server '{server.name}' timed out during {stage} after {timeout:g}s; "
                        "check the server or reconnect from its page"
                    )
                )
                owner.cancel()

        # A loop timer bounds setup AND discovery without wrapping anyio transport
        # contexts in a cancellation scope that would be exited out of order.
        deadline = asyncio.get_running_loop().call_later(timeout, expired)
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
                stage = "initialize (including authentication)"
                await session.initialize()
                stage = "tools/list"
                conn = _Conn(session, await self._list_tools(session, server.name))
                self._conns[server.name] = conn
                self._stderr_tails.pop(server.name, None)
                if not ready.done():
                    ready.set_result(conn)
                deadline.cancel()
                await conn.shutdown.wait()
        except asyncio.CancelledError:
            if not ready.done():
                ready.set_exception(
                    RuntimeError(f"MCP server '{server.name}' connection cancelled")
                )
            raise
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
            deadline.cancel()
            if self._tasks.get(server.name) is owner:
                self._conns.pop(server.name, None)
                self._tasks.pop(server.name, None)
                self._ready.pop(server.name, None)
                self._interactive.discard(server.name)


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
