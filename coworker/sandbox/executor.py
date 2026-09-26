"""`RunnerExecutor`: the existing `Executor` contract, served by a tool runner.

The shell tool does not change. It gets an executor; whether that executor drives a bash
in this process (`LocalExecutor`) or one inside a sandbox (this class) is decided by the
workspace. Results have exactly the keys `LocalExecutor` returns, so a session looks the
same to the model in both modes.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from .client import RunnerClient
from .runner import protocol as P
from .runner.executor import Executor

_RESULT_KEYS = ("command", "cwd", "exit_code", "output", "timed_out", "truncated", "error")
_DEFAULT_TIMEOUT = 120.0
_GRACE_SECONDS = 20.0  # the runner enforces the command timeout; this only covers the pipe


class RunnerExecutor(Executor):
    def __init__(
        self,
        client: RunnerClient,
        *,
        cwd: str,
        shell: str = "main",
        default_timeout: float = _DEFAULT_TIMEOUT,
        on_output: Optional[Callable[[str], None]] = None,
        before_call: Optional[Callable[[], Optional[str]]] = None,
    ) -> None:
        self._client = client
        # Called before every command. The workspace uses it to restart the sandbox when the
        # session's folders changed; what it returns is told to the agent with the result.
        self._before_call = before_call
        self.shell = shell
        self.cwd = str(cwd)  # the last folder the runner reported; a reopened shell starts here
        self.default_timeout = default_timeout
        self._on_output = on_output

    def _failed(self, command: str, message: str) -> dict[str, Any]:
        return {
            "command": command,
            "cwd": self.cwd,
            "exit_code": None,
            "output": "",
            "timed_out": False,
            "truncated": False,
            "error": message,
        }

    def run(self, command: str, timeout: Optional[float] = None) -> dict[str, Any]:
        timeout = timeout or self.default_timeout
        notice = self._before_call() if self._before_call is not None else None

        def live(method: str, params: dict[str, Any]) -> None:
            if method == "shell.output" and self._on_output is not None:
                self._on_output(str(params.get("text", "")))

        try:
            result = self._client.call(
                "shell.run",
                {"shell": self.shell, "command": command, "timeout_s": timeout, "cwd": self.cwd},
                timeout=timeout + _GRACE_SECONDS,
                on_notify=live,
            )
        except P.RunnerError as exc:
            return self._failed(command, exc.message)
        if isinstance(result.get("cwd"), str) and result["cwd"]:
            self.cwd = result["cwd"]
        answer = {key: result[key] for key in _RESULT_KEYS if key in result}
        if notice:
            answer["sandbox_notice"] = notice
        return answer

    def run_background(self, command: str) -> dict[str, Any]:
        return self._simple("task.start", {"command": command, "cwd": self.cwd})

    def background_output(self, task_id: str) -> dict[str, Any]:
        return self._simple("task.output", {"task_id": task_id})

    def background_kill(self, task_id: str) -> dict[str, Any]:
        return self._simple("task.kill", {"task_id": task_id})

    def _simple(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._client.call(method, {"shell": self.shell, **params}, timeout=30)
        except P.RunnerError as exc:
            return {"error": exc.message}
        result.pop("seq", None)
        return result

    def interrupt_now(self) -> None:
        """User Stop. Must not block the caller, so the request goes out on its own thread."""
        threading.Thread(target=self.interrupt, daemon=True).start()

    def interrupt(self) -> None:
        try:
            self._client.call("shell.interrupt", {"shell": self.shell}, timeout=10)
        except P.RunnerError:
            pass

    def close(self) -> None:
        try:
            self._client.call("shell.close", {"shell": self.shell}, timeout=10)
        except P.RunnerError:
            pass
