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


def test_managed_bin_dir_is_on_path_from_spawn(tmp_path, monkeypatch):
    """A tool the user approves mid-session must work by name in the ALREADY-running
    shell (owner-hit 2026-08-14: freshly installed trivy needed a full-path retry). The
    stable bin dir goes on PATH at spawn, before anything exists in it."""
    import os as _os

    from coworker import toolchain

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    ex = LocalExecutor(cwd=tmp_path, default_timeout=10)
    try:
        assert str(toolchain.bin_dir()) in ex._env["PATH"].split(_os.pathsep)
        # User's own PATH entries stay ahead — their copies always win.
        assert not ex._env["PATH"].startswith(str(toolchain.bin_dir()))
    finally:
        ex.close()


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


def test_large_output_truncated_keeps_tail(tmp_path):
    ex = LocalExecutor(cwd=tmp_path, max_output_chars=200, default_timeout=10)
    try:
        result = ex.run(PRINT_1000)
        assert result["truncated"] is True
        assert len(result["output"]) <= 200
        # the END survives (where test/build verdicts live), the head is dropped
        assert "line1000" in result["output"]
        assert "line1\n" not in result["output"]
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
            if until_status is not None:
                # Output reads are incremental and the status can flip to its
                # terminal value on the same tick the final chunk is still being
                # drained into the task buffer. Drain the tail: consume anything
                # already buffered greedily (no sleep), and only wait between
                # *empty* reads, stopping once two consecutive reads yield nothing
                # (robust to a one-tick gap between chunks). Bounded and cheap —
                # the common case is a couple of reads, not a tight 2s poll.
                grace_end = time.monotonic() + 0.5
                empty_reads = 0
                while empty_reads < 2 and time.monotonic() < grace_end:
                    extra = reg.execute("shell_task_output", {"task_id": task_id})
                    if extra["output"]:
                        acc += extra["output"]
                        res = extra
                        empty_reads = 0
                        continue  # more available now — keep draining, no sleep
                    empty_reads += 1
                    time.sleep(0.02)
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


def test_is_sensitive_env():
    from coworker.tools.shell import is_sensitive_env

    assert is_sensitive_env("AWS_SECRET_ACCESS_KEY") is True
    assert is_sensitive_env("AWS_ACCESS_KEY_ID") is True
    assert is_sensitive_env("AWS_PROFILE") is True
    assert is_sensitive_env("AZURE_CLIENT_SECRET") is True
    assert is_sensitive_env("OPENAI_API_KEY") is True
    assert is_sensitive_env("GITHUB_TOKEN") is True
    assert is_sensitive_env("MY_SECRET_TOKEN") is True
    assert is_sensitive_env("DATABASE_PASSWORD") is True
    assert is_sensitive_env("ADMIN_PASSWD") is True
    assert is_sensitive_env("GCP_CREDENTIALS") is True
    assert is_sensitive_env("SERVICE_KEY") is True
    assert is_sensitive_env("PROXY_AUTH") is True

    assert is_sensitive_env("PATH") is False
    assert is_sensitive_env("HOME") is False
    assert is_sensitive_env("USER") is False
    assert is_sensitive_env("TERM") is False
    assert is_sensitive_env("SHELL") is False
    assert is_sensitive_env("LANG") is False
    assert is_sensitive_env("TMPDIR") is False
    assert is_sensitive_env("VIRTUAL_ENV") is False
    assert is_sensitive_env("PYTHONPATH") is False


def test_ambient_sensitive_env_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "super-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-12345")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_token999")
    monkeypatch.setenv("DATABASE_PASSWORD", "dbpass")
    monkeypatch.setenv("SAFE_APP_VAR", "visible")

    ex = LocalExecutor(cwd=tmp_path, default_timeout=5)
    try:
        assert "AWS_SECRET_ACCESS_KEY" not in ex._env
        assert "OPENAI_API_KEY" not in ex._env
        assert "GITHUB_TOKEN" not in ex._env
        assert "DATABASE_PASSWORD" not in ex._env
        assert ex._env.get("SAFE_APP_VAR") == "visible"

        # Verify child shell process cannot see the scrubbed variable
        echo_cmd = "echo $OPENAI_API_KEY" if not _WIN else "echo $env:OPENAI_API_KEY"
        res = ex.run(echo_cmd)
        assert "sk-live-12345" not in res["output"]
    finally:
        ex.close()


def test_allowed_env_exempts_sensitive_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "staging")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "top-secret")

    ex = LocalExecutor(
        cwd=tmp_path, allowed_env=["AWS_PROFILE"], default_timeout=5
    )
    try:
        assert ex._env.get("AWS_PROFILE") == "staging"
        assert "AWS_SECRET_ACCESS_KEY" not in ex._env
    finally:
        ex.close()


def test_explicit_env_argument_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-secret")

    ex = LocalExecutor(
        cwd=tmp_path,
        env={"OPENAI_API_KEY": "explicit-provided"},
        default_timeout=5,
    )
    try:
        assert ex._env.get("OPENAI_API_KEY") == "explicit-provided"
    finally:
        ex.close()

