"""`openworker run --mcp`: the headless runner attaches MCP servers as tools.

End to end with a scripted fake model (no network, no keys) and a REAL stdio MCP server —
a tiny FastMCP `echo` server spawned as a subprocess, so the whole path is exercised:
connect, list tools, register on the live engine, the model calls the tool, the grader-style
result comes back. Run in a subprocess for the same reason as test_headless_run: the runner
redirects OpenWorker's state/scratch folders before the engine is built.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A minimal stdio MCP server with one tool. Only depends on the `mcp` SDK, so it starts fast.
ECHO_SERVER = """\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def ping(text: str) -> str:
    return f"pong:{text}"


if __name__ == "__main__":
    mcp.run()
"""


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("COWORKER_SCRATCH_BASE", None)
    env["COWORKER_STATE_DIR"] = tempfile.mkdtemp(prefix="ow-run-machine-")
    return env


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(tmp_path: Path, script: list[dict], servers: dict) -> tuple[subprocess.CompletedProcess, Path]:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    out = tmp_path / "out"
    script_path = _write(tmp_path / "script.json", json.dumps(script))
    mcp_path = _write(tmp_path / "mcp.json", json.dumps({"mcpServers": servers}))
    cmd = [
        sys.executable, "-m", "coworker.cli", "run",
        "--prompt", "Use the echo tool.",
        "--workspace", str(ws), "--model", "anthropic/claude-sonnet-5",
        "--approval-mode", "bypass-approvals", "--out", str(out),
        "--scripted", str(script_path),
        "--mcp-config", str(mcp_path),
        "--isolated", "--timeout-seconds", "120",
    ]
    proc = subprocess.run(cmd, env=_env(), capture_output=True, text=True, timeout=300)
    return proc, out


def _events(out: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (out / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _summary(out: Path) -> dict:
    return json.loads((out / "summary.json").read_text(encoding="utf-8"))


def test_mcp_tool_attached_and_callable(tmp_path: Path) -> None:
    """A real stdio MCP server's tool is attached and the scripted model calls it; its
    result lands in the record and the run completes."""
    server = _write(tmp_path / "echo_server.py", ECHO_SERVER)
    script = [
        {"text": "Calling echo.", "tool_calls": [{"name": "mcp__echo__ping", "arguments": {"text": "hi"}}]},
        {"text": "Got the result. Done.", "usage": {"input": 100, "output": 20}},
    ]
    proc, out = _run(
        tmp_path,
        script,
        {"echo": {"command": sys.executable, "args": [str(server)], "requires_approval": False}},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    summary = _summary(out)
    assert summary["outcome"] == "completed"
    assert summary["args"]["mcp"] is True
    # The attach is recorded with the server and its tool.
    assert summary["mcp_servers"] == [{"server": "echo", "tools": ["mcp__echo__ping"]}]

    events = _events(out)
    assert any(e["type"] == "mcp_server_attached" and e["data"]["server"] == "echo" for e in events)
    # The tool call ran against the real server and returned the pong.
    finished = [e for e in events if e["type"] == "tool_finished" and e["data"].get("name") == "mcp__echo__ping"]
    assert finished and finished[0]["data"]["status"] == "ok", events
    assert "pong:hi" in (out / "messages.json").read_text(encoding="utf-8")


def test_mcp_server_failure_is_noted_and_run_continues(tmp_path: Path) -> None:
    """A server that cannot start is noted and skipped; the run still completes."""
    script = [{"text": "Nothing to do. Done.", "usage": {"input": 100, "output": 20}}]
    proc, out = _run(
        tmp_path,
        script,
        {"nope": {"command": "ow-no-such-command-xyz", "args": [], "requires_approval": False}},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    summary = _summary(out)
    assert summary["outcome"] == "completed"
    assert summary["mcp_servers"] == []  # nothing attached
    events = _events(out)
    assert any(e["type"] == "mcp_server_failed" and e["data"]["server"] == "nope" for e in events)
