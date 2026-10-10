"""Send a workspace tool's execution to the tool runner, leaving its definition alone.

A proxy has the same name, signature, description, schema and approval metadata as the tool
it stands for, so the registry, the permission engine and the model cannot tell the
difference. Only the call itself goes to the runner, where the same code runs on the other
side of the sandbox wall.
"""

from __future__ import annotations

import builtins
import functools
import inspect
from typing import Any, Callable, Optional

from .runner import protocol as P
from .runner.toolcalls import TOOLS

_TOOL_TIMEOUT = 180.0


def _reraise(exc: P.RunnerError) -> None:
    """The tool raised inside the runner: raise the same kind of error with the same text."""
    kind = getattr(builtins, str(exc.data.get("type", "")), None)
    if not (isinstance(kind, type) and issubclass(kind, Exception)):
        kind = RuntimeError
    message = exc.message
    if kind is KeyError and len(message) >= 2 and message[0] == message[-1] == "'":
        message = message[1:-1]  # str(KeyError("x")) is "'x'"
    raise kind(message) from None


def proxy_tool(tool: Callable[..., Any], workspace: Any, *, root: str, roots: Optional[list] = None) -> Callable[..., Any]:
    name = getattr(tool, "__name__", "")
    signature = inspect.signature(tool)

    @functools.wraps(tool)
    def call(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(*args, **kwargs)
        sync = getattr(workspace, "sync_roots", None)
        if sync is not None:
            sync()  # a folder granted since the sandbox started: restart it with the new walls
        # The session's roots list is live (folders are granted while a session runs), so
        # it is read on every call, as the in-process tools do.
        live = [{"path": str(r.path), "writable": bool(r.writable)} for r in (roots or [])]
        try:
            result = workspace.client.call(
                "tool.call",
                {"name": name, "args": dict(bound.arguments), "workspace": root, "roots": live or None},
                timeout=_TOOL_TIMEOUT,
            )
        except P.RunnerError as exc:
            if exc.code == P.TOOL_RAISED:
                _reraise(exc)
            raise RuntimeError(f"{name} could not run in the sandbox: {exc.message}") from None
        return result.get("value")

    return call


def proxy_tools(tools: list, workspace: Any, *, root: str, roots: Optional[list] = None) -> list:
    """Proxies for the tools the runner can execute; any other tool is returned as it is."""
    return [
        proxy_tool(t, workspace, root=root, roots=roots) if getattr(t, "__name__", "") in TOOLS else t
        for t in tools
    ]
