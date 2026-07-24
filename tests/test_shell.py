"""P3 gate tests — persistent shell executor.

The executor drives the OS-native shell (bash on POSIX, PowerShell on Windows), so the
command strings here are parameterized per-OS. The behavior under test (cwd/env persistence,
exit codes, timeout-and-recover, truncation) is identical across both.
"""

from __future__ import annotations

import sys
import time

import pytest

from coworker.permissions import PermissionEngine
from coworker.tools import ToolRegistry
from coworker.tools.shell import LocalExecutor, shell_tools

_WIN = sys.platform == "win32"

# Per-OS command snippets exercising the same behavior in the native shell.
SET_ENV = "$env:GREETING='hello_world'" if _WIN else "export GREETING=hello_world"
ECHO_ENV = "echo $env:GREETING" if _WIN else "echo $GREETING"
EXIT_OK = "cmd /c exit 0" if _WIN else "true"
EXIT_FAIL = "cmd /c exit 1" if _WIN else "false"
SLEEP_5 = "Start-Sleep -Seconds 5" if _WIN else "sleep 5"
PRINT_1000 = (
    'foreach ($i in 1..1000) { "line$i" }'
    if _WIN
    else "for i in $(seq 1 1000); do echo line$i; done"
)


@pytest.fixture
def executor(tmp_path):
    ex = LocalExecutor(cwd=tmp_path, default_timeout=10)
    yield ex
    ex.close()


def test_cwd_persists_across_calls(executor, tmp_path):
    (tmp_path / "sub").mkdir()
    executor.run("cd sub")
    result = executor.run("pwd")
    assert result["exit_code"] == 0
    assert "sub" in result["output"]
    assert executor.cwd.endswith("sub")


def test_env_persists_across_calls(executor):
    executor.run(SET_ENV)
    result = executor.run(ECHO_ENV)
    assert "hello_world" in result["output"]


def test_exit_code_captured(executor):
    assert executor.run(EXIT_OK)["exit_code"] == 0
    assert executor.run(EXIT_FAIL)["exit_code"] == 1


def test_timeout_kills_command(executor):
    start = time.monotonic()
    result = executor.run(SLEEP_5, timeout=1)
    elapsed = time.monotonic() - start
    assert result["timed_out"] is True
    assert elapsed < 4.0  # did not block for the full sleep
    # session survives the timeout — still usable (POSIX keeps the shell; Windows respawns)
    assert executor.run("echo alive")["output"].strip().endswith("alive")


def test_large_output_is_preserved_for_engine_projection(tmp_path):
    ex = LocalExecutor(cwd=tmp_path, max_output_chars=200, default_timeout=10)
    try:
        result = ex.run(PRINT_1000)
        assert result["truncated"] is False
        assert len(result["output"]) > 200
        # Both ends survive; the engine projects large output for model context.
        assert "line1000" in result["output"]
        assert "line1\n" in result["output"]
    finally:
        ex.close()


def test_shell_tool_integration(executor, tmp_path):
    reg = ToolRegistry()
    reg.register_all(shell_tools(executor))
    assert {"run_shell", "shell_task_output", "shell_task_kill"} <= set(reg.names())

    spec = reg.get("run_shell")
    assert spec.metadata.requires_approval is True
    # polling/killing the agent's own background tasks doesn't need approval
    assert reg.get("shell_task_output").metadata.requires_approval is False
    assert reg.get("shell_task_kill").metadata.requires_approval is False

    eng = PermissionEngine(workspace_root=tmp_path)
    decision = eng.evaluate("run_shell", {"command": "echo hi"}, spec.metadata)
    assert not decision.allowed and decision.needs_user  # high-risk → asks

    out = reg.execute("run_shell", {"command": "echo hi"})
    assert "hi" in out["output"]


def test_run_shell_accepts_description_and_clamped_timeout(executor):
    reg = ToolRegistry()
    reg.register_all(shell_tools(executor))
    # `description` rides along for approval prompts/audit; it must not break execution.
    out = reg.execute(
        "run_shell",
        {"command": "echo ok", "description": "Say ok", "timeout_seconds": 99999},
    )
    assert out["exit_code"] == 0 and "ok" in out["output"]


# -- background tasks ------------------------------------------------------------

ECHO_THEN_SLEEP = (
    "Write-Output started; Start-Sleep -Seconds 30"
    if _WIN
    else "echo started; sleep 30"
)
QUICK_ECHO = "Write-Output quick_done" if _WIN else "echo quick_done"


def _poll_output(reg, task_id, *, until_status=None, deadline=10.0):
    """Poll shell_task_output, accumulating output until a status is reached."""
    acc = ""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        res = reg.execute("shell_task_output", {"task_id": task_id})
        acc += res["output"]
        if until_status is None or res["status"] == until_status:
            if until_status is None and not acc:
                time.sleep(0.1)
                continue
            return acc, res
        time.sleep(0.1)
    return acc, res


def test_background_task_runs_and_exits(executor):
    reg = ToolRegistry()
    reg.register_all(shell_tools(executor))
    started = reg.execute(
        "run_shell", {"command": QUICK_ECHO, "run_in_background": True}
    )
    assert started["status"] == "running" and started["task_id"]

    acc, res = _poll_output(reg, started["task_id"], until_status="exited")
    assert res["status"] == "exited"
    assert res["exit_code"] == 0
    assert "quick_done" in acc

    # output reads are incremental: a second read returns nothing new
    again = reg.execute("shell_task_output", {"task_id": started["task_id"]})
    assert again["output"] == ""


def test_background_task_kill(executor):
    reg = ToolRegistry()
    reg.register_all(shell_tools(executor))
    started = reg.execute(
        "run_shell", {"command": ECHO_THEN_SLEEP, "run_in_background": True}
    )
    acc, _ = _poll_output(reg, started["task_id"])
    assert "started" in acc  # it's alive and producing output

    killed = reg.execute("shell_task_kill", {"task_id": started["task_id"]})
    assert killed["status"] == "killed"

    res = reg.execute("shell_task_output", {"task_id": started["task_id"]})
    assert res["status"] == "exited"


def test_background_unknown_task_errors(executor):
    reg = ToolRegistry()
    reg.register_all(shell_tools(executor))
    assert (
        "unknown task"
        in reg.execute("shell_task_output", {"task_id": "bg-99"})["error"]
    )
    assert (
        "unknown task" in reg.execute("shell_task_kill", {"task_id": "bg-99"})["error"]
    )


def test_background_large_output_is_recoverable(tmp_path):
    # Emit more than the old foreground cap; capture must retain head/middle/tail.
    if _WIN:
        cmd = (
            '$s = ("A" * 5000) + "MIDDLE_BG_SENTINEL" + ("Z" * 5000); '
            "Write-Output $s"
        )
    else:
        cmd = "python3 -c \"print('A'*5000 + 'MIDDLE_BG_SENTINEL' + 'Z'*5000)\""
    ex = LocalExecutor(cwd=tmp_path, capture_dir=tmp_path / "caps", default_timeout=15)
    try:
        reg = ToolRegistry()
        reg.register_all(shell_tools(ex))
        started = reg.execute("run_shell", {"command": cmd, "run_in_background": True})
        acc, res = _poll_output(reg, started["task_id"], until_status="exited", deadline=15)
        assert res["status"] == "exited"
        assert "MIDDLE_BG_SENTINEL" in acc
        task = ex._bg_tasks[started["task_id"]]
        retained = task.read_retained(0, 20_000).decode("utf-8", errors="replace")
        assert "MIDDLE_BG_SENTINEL" in retained
        assert retained.startswith("A") or "AAAA" in retained
        # Kill must not delete retained capture.
        reg.execute("shell_task_kill", {"task_id": started["task_id"]})
        assert task.capture_path.is_file()
        again = reg.execute("shell_task_output", {"task_id": started["task_id"]})
        assert again["output"] == ""
    finally:
        ex.close()


def test_background_polls_are_incremental(tmp_path):
    if _WIN:
        cmd = "Write-Output one; Start-Sleep -Milliseconds 200; Write-Output two"
    else:
        cmd = "echo one; sleep 0.2; echo two"
    ex = LocalExecutor(cwd=tmp_path, capture_dir=tmp_path / "caps", default_timeout=10)
    try:
        reg = ToolRegistry()
        reg.register_all(shell_tools(ex))
        started = reg.execute("run_shell", {"command": cmd, "run_in_background": True})
        seen = []
        end = time.monotonic() + 8
        while time.monotonic() < end:
            res = reg.execute("shell_task_output", {"task_id": started["task_id"]})
            if res["output"]:
                seen.append(res["output"])
            if res["status"] == "exited":
                break
            time.sleep(0.05)
        joined = "".join(seen)
        assert "one" in joined and "two" in joined
        # No duplicate whole stream across polls.
        assert joined.count("one") == 1
    finally:
        ex.close()


def test_rebuilt_executor_uses_a_fresh_capture_file(tmp_path):
    capture_dir = tmp_path / "caps"
    first = LocalExecutor(cwd=tmp_path, capture_dir=capture_dir)
    second = None
    try:
        first_task = first.run_background(QUICK_ECHO)
        task_one = first._bg_tasks[first_task["task_id"]]
        task_one.proc.wait(timeout=10)
        second = LocalExecutor(cwd=tmp_path, capture_dir=capture_dir)
        second_task = second.run_background(QUICK_ECHO)
        task_two = second._bg_tasks[second_task["task_id"]]
        task_two.proc.wait(timeout=10)
        assert first_task["task_id"] == second_task["task_id"] == "bg-1"
        assert task_one.capture_path != task_two.capture_path
    finally:
        first.close()
        if second is not None:
            second.close()


def test_close_background_tasks_stops_detached_process(tmp_path):
    ex = LocalExecutor(cwd=tmp_path, capture_dir=tmp_path / "caps")
    try:
        started = ex.run_background(ECHO_THEN_SLEEP)
        task = ex._bg_tasks[started["task_id"]]
        ex.close_background_tasks()
        assert task.proc.poll() is not None
    finally:
        ex.close()
