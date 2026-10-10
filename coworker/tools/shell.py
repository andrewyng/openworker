"""Persistent shell behind an `Executor` boundary.

`LocalExecutor` keeps one long-lived shell process, so `cd`, `export`, activated venvs,
etc. persist across `run_shell` calls (unlike a per-call `subprocess.run`). The `Executor`
interface is the hedge for a future `ContainerExecutor`/`VMExecutor` (sandboxing) without
touching the engine.

The shell is OS-native: `/bin/bash` on POSIX, `powershell.exe` (`-Command -` REPL) on
Windows. Each backend has its own marker/exit-code protocol and interrupt mechanism, but
the `Executor` contract (and the parsed `{marker} {exit_code} {cwd}` trailer) is identical.

Safety here is permission-gating (high-risk tool → approval) + per-command timeout +
best-effort non-interactive enforcement. A timed-out command is interrupted (SIGINT to the
foreground child on POSIX, Ctrl-Break to the child group on Windows); the shell survives so
session state is preserved.

Background tasks (`run_shell` with `run_in_background`) get their own detached process —
NOT the persistent shell — so a dev server can run while the session keeps working. They
are deliberately not killed by `close()` (which the timeout-recovery path calls); they end
when they exit or via `shell_task_kill`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import aisuite as ai

from ..sandbox.runner.executor import (  # noqa: F401  (re-exported: the executor moved)
    _DEFAULT_TIMEOUT,
    _MAX_TIMEOUT,
    Executor,
    LocalExecutor as _StdlibLocalExecutor,
)


class LocalExecutor(_StdlibLocalExecutor):
    """The in-process executor (direct mode). Same behaviour as before the move; the only
    thing added here is the part that needs the rest of OpenWorker."""

    def __init__(self, *, cwd: str | Path, **kwargs) -> None:
        # Managed pinned tools (toolchain.install) land under one stable bin dir; putting
        # it on PATH up front, even before anything is installed there, means a tool the
        # user approves mid-session works in THIS shell immediately, by name, no respawn.
        # Appended last: the user's own copies always win.
        from .. import toolchain

        extra = [*kwargs.pop("extra_path_dirs", ()), str(toolchain.bin_dir())]
        super().__init__(cwd=cwd, extra_path_dirs=extra, **kwargs)


_RUN_SHELL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_shell",
        "description": (
            "Run a shell command in the persistent session (cwd and env persist across "
            "calls). Output longer than the limit keeps the END (where test/build verdicts "
            "are). Set run_in_background for long-running processes like dev servers, then "
            "poll with shell_task_output."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to run.",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Short human-readable summary of what the command does (e.g. "
                        "'Install dependencies'), shown in approval prompts and logs."
                    ),
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": (
                        f"Max seconds to wait (default {int(_DEFAULT_TIMEOUT)}, "
                        f"max {int(_MAX_TIMEOUT)}). Ignored for background tasks."
                    ),
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "Run detached and return a task_id immediately instead of waiting. "
                        "Use for servers, watchers, and very long builds."
                    ),
                },
            },
            "required": ["command"],
        },
    },
}

_TASK_OUTPUT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "shell_task_output",
        "description": (
            "Read NEW output (since the last read) from a background task started with "
            "run_shell run_in_background=true, plus its status and exit code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task_id returned by run_shell.",
                }
            },
            "required": ["task_id"],
        },
    },
}

_TASK_KILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "shell_task_kill",
        "description": "Stop a background task started with run_shell run_in_background=true.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task_id returned by run_shell.",
                }
            },
            "required": ["task_id"],
        },
    },
}


def shell_tools(executor: Executor) -> list:
    """Return the shell tools (`run_shell` + background-task helpers) bound to a
    persistent executor."""

    def run_shell(
        command: str,
        description: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        run_in_background: bool = False,
    ) -> dict:
        # `description` is not used here on purpose: it rides along in the call arguments
        # so approval prompts and the audit log can show intent, not just the raw command.
        if run_in_background:
            return executor.run_background(command)
        timeout = None
        if isinstance(timeout_seconds, (int, float)) and timeout_seconds > 0:
            timeout = min(float(timeout_seconds), _MAX_TIMEOUT)
        return executor.run(command, timeout=timeout)

    def shell_task_output(task_id: str) -> dict:
        return executor.background_output(task_id)

    def shell_task_kill(task_id: str) -> dict:
        return executor.background_kill(task_id)

    wrapped_run = ai.tool(
        run_shell,
        metadata=ai.ToolMetadata(
            category="shell",
            risk_level="high",
            capabilities=["run_command"],
            requires_approval=True,
        ),
    )
    wrapped_run.__coworker_schema__ = _RUN_SHELL_SCHEMA
    wrapped_output = ai.tool(
        shell_task_output,
        metadata=ai.ToolMetadata(
            category="shell",
            risk_level="low",
            capabilities=["run_command"],
            requires_approval=False,
        ),
    )
    wrapped_output.__coworker_schema__ = _TASK_OUTPUT_SCHEMA
    wrapped_kill = ai.tool(
        shell_task_kill,
        metadata=ai.ToolMetadata(
            category="shell",
            risk_level="low",
            capabilities=["run_command"],
            requires_approval=False,
        ),
    )
    wrapped_kill.__coworker_schema__ = _TASK_KILL_SCHEMA
    return [wrapped_run, wrapped_output, wrapped_kill]
