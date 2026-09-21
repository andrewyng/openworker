"""`openworker run` end to end with a scripted fake model: no network, no keys.

Each test runs the command in a subprocess because the runner points OpenWorker's state
and scratch folders under --out BEFORE the engine is built, which cannot be undone inside
one process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = [
    {"text": "I will create the file.", "tool_calls": [{"name": "write_file", "arguments": {"path": "hello.txt", "content": "hello"}}]},
    {"text": "Now reading it back.", "tool_calls": [{"name": "read_file", "arguments": {"path": "hello.txt"}}]},
    {"text": "The file says: hello. Done.", "usage": {"input": 300, "output": 40, "cache_read": 100}},
]

TASK = "Create hello.txt containing the word hello, then read it back."


def _env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("COWORKER_STATE_DIR", None)
    env.pop("COWORKER_SCRATCH_BASE", None)
    env.update(extra or {})
    return env


def _run(
    tmp_path: Path,
    mode: str,
    script: list[dict] | None = None,
    *,
    model: str = "anthropic/claude-sonnet-5",
    prompt: str = TASK,
    extra: list[str] | None = None,
    env: dict | None = None,
) -> tuple[subprocess.CompletedProcess, Path, Path]:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    out = tmp_path / "out"
    path = tmp_path / "script.json"
    path.write_text(json.dumps(script if script is not None else SCRIPT), encoding="utf-8")
    cmd = [
        sys.executable, "-m", "coworker.cli", "run",
        "--prompt", prompt,
        "--workspace", str(ws), "--model", model,
        "--mode", mode, "--out", str(out),
        "--trajectory-atif", str(tmp_path / "logs" / "trajectory.json"),
        "--scripted", str(path), "--timeout-seconds", "120",
        *(extra or []),
    ]
    proc = subprocess.run(cmd, env=_env(env), capture_output=True, text=True, timeout=300)
    return proc, ws, out


def _summary(out: Path) -> dict:
    return json.loads((out / "summary.json").read_text(encoding="utf-8"))


def _messages(out: Path) -> list[dict]:
    return json.loads((out / "messages.json").read_text(encoding="utf-8"))


def _events(out: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (out / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _calls(out: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (out / "model_calls.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# -- the record ----------------------------------------------------------------------------


def test_bypass_writes_the_file_and_leaves_a_full_record(tmp_path: Path) -> None:
    proc, ws, out = _run(tmp_path, "bypass-approvals")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (ws / "hello.txt").read_text(encoding="utf-8") == "hello"

    summary = _summary(out)
    assert summary["outcome"] == "completed"
    assert summary["args"]["model"] == "anthropic:claude-sonnet-5"  # provider/model converted
    assert summary["args"]["attendance"] == "auto"
    assert summary["tool_calls"] == 2 and summary["tool_calls_denied"] == 0
    assert summary["answers"] == {
        "cards_refused": 0,
        "floors_cleared": 0,
        "questions_answered": 0,
        "directory_requests_declined": 0,
        "tool_installs": 0,
    }
    assert summary["tokens"]["input"] > 0 and summary["tokens"]["output"] > 0
    assert summary["cost_usd"] is not None and summary["cost_usd"] > 0  # Sonnet 5 is priced
    for name in ("events.jsonl", "messages.json", "answers.json", "audit.db", "trajectory.json", "model_calls.jsonl"):
        assert (out / name).exists(), name
    # The run never touched the machine's own state: both folders live under --out.
    assert Path(summary["isolation"]["COWORKER_STATE_DIR"]).is_relative_to(out)

    traj = json.loads((tmp_path / "logs" / "trajectory.json").read_text(encoding="utf-8"))
    assert traj["schema_version"] == "ATIF-v1.7"
    assert traj["agent"]["name"] == "openworker" and traj["agent"]["version"].startswith("openworker")
    assert traj["agent"]["extra"]["attendance"] == "auto"
    assert [s["step_id"] for s in traj["steps"]] == list(range(1, len(traj["steps"]) + 1))
    agent_steps = [s for s in traj["steps"] if s["source"] == "agent"]
    assert agent_steps[0]["tool_calls"][0]["function_name"] == "write_file"
    obs = agent_steps[0]["observation"]["results"]
    assert obs[0]["source_call_id"] == agent_steps[0]["tool_calls"][0]["tool_call_id"]
    assert traj["final_metrics"]["total_completion_tokens"] > 0
    assert traj["final_metrics"]["total_cost_usd"] > 0
    for s in traj["steps"]:
        if s["source"] != "agent":
            assert "tool_calls" not in s and "metrics" not in s and "model_name" not in s


def test_interactive_refuses_the_card_nobody_can_answer_and_records_it(tmp_path: Path) -> None:
    # Also an unpriced model (cost must be null, never guessed) in the two-slash form,
    # which must split on the first slash only.
    proc, ws, out = _run(tmp_path, "interactive", model="together/deepseek-ai/DeepSeek-V4-Pro")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (ws / "hello.txt").exists()
    summary = _summary(out)
    assert summary["args"]["model"] == "together:deepseek-ai/DeepSeek-V4-Pro"
    assert summary["tool_calls_denied"] >= 1
    assert summary["answers"]["cards_refused"] >= 1
    assert summary["cost_usd"] is None
    answers = json.loads((out / "answers.json").read_text(encoding="utf-8"))
    assert answers["rows"][0]["tool"] == "write_file" and answers["rows"][0]["stage"] == "approval_resolved"
    traj = json.loads((out / "trajectory.json").read_text(encoding="utf-8"))
    assert "total_cost_usd" not in traj["final_metrics"]


def test_dangerous_mode_warns_and_clears_the_downloaded_file_floor(tmp_path: Path) -> None:
    script = [
        {"text": "fetching", "tool_calls": [{"name": "run_shell", "arguments": {"command": "echo 'echo hi' > setup.sh"}}]},
        {"text": "running", "tool_calls": [{"name": "run_shell", "arguments": {"command": "sh setup.sh"}}]},
        {"text": "Done."},
    ]
    proc, _ws, out = _run(tmp_path, "dangerously-bypass-approvals", script)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Dangerously bypass approvals is on" in proc.stderr
    messages = _messages(out)
    assert any(m.get("role") == "notice" and m.get("kind") == "dangerous_mode" for m in messages)
    assert _summary(out)["args"]["mode"] == "dangerously-bypass-approvals"


def test_a_question_is_answered_by_the_engine_and_counted(tmp_path: Path) -> None:
    script = [
        {"text": "asking", "tool_calls": [{"name": "ask_user", "arguments": {"question": "Rewrite history?"}}]},
        {"text": "ok", "tool_calls": [{"name": "write_file", "arguments": {"path": "hello.txt", "content": "hello"}}]},
        {"text": "Done."},
    ]
    proc, ws, out = _run(tmp_path, "bypass-approvals", script)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (ws / "hello.txt").exists()
    summary = _summary(out)
    assert summary["answers"]["questions_answered"] == 1
    tool_msgs = [m for m in _messages(out) if m["role"] == "tool"]
    assert "least destructive" in tool_msgs[0]["content"]


def test_finish_reason_is_persisted_on_messages_events_and_the_call_log(tmp_path: Path) -> None:
    proc, _ws, out = _run(tmp_path, "bypass-approvals")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assistant = [m for m in _messages(out) if m.get("role") == "assistant"]
    assert [m.get("finish_reason") for m in assistant] == ["tool_calls", "tool_calls", "stop"]
    replies = [e for e in _events(out) if e.get("type") == "assistant_message"]
    assert [e["data"].get("finish_reason") for e in replies] == ["tool_calls", "tool_calls", "stop"]
    assert [c["finish_reason"] for c in _calls(out)] == [m["finish_reason"] for m in assistant]


def test_a_cut_off_reply_is_continued_and_repeated_cut_offs_end_as_truncated(tmp_path: Path) -> None:
    cut = {"text": None, "finish_reason": "length"}
    write = {"text": "ok", "tool_calls": [{"name": "write_file", "arguments": {"path": "hello.txt", "content": "hello"}}]}
    proc, ws, out = _run(tmp_path, "bypass-approvals", [cut, write, {"text": "Done."}], prompt="Do something hard.")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = _summary(out)
    assert summary["outcome"] == "completed" and summary["continuations"] == 1
    assert (ws / "hello.txt").read_text(encoding="utf-8") == "hello"

    second = tmp_path / "again"
    second.mkdir()
    proc, _ws, out = _run(second, "bypass-approvals", [cut, cut, cut], prompt="Do something hard.")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = _summary(out)
    assert summary["outcome"] == "truncated" and summary["continuations"] == 2
    assert _messages(out)[-1]["kind"] == "truncated"


def test_max_output_tokens_and_reasoning_effort_reach_every_call_and_the_record(tmp_path: Path) -> None:
    proc, _ws, out = _run(
        tmp_path, "bypass-approvals", extra=["--max-output-tokens", "64000", "--reasoning-effort", "xhigh"]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = _summary(out)
    assert summary["args"]["max_output_tokens"] == 64000
    assert summary["reasoning_effort"] == "xhigh"
    assert summary["reasoning_effort_detail"]["replies_recorded"] == 3
    calls = _calls(out)
    assert [c["max_output_tokens"] for c in calls] == [64000, 64000, 64000]
    assert [c["effort"]["effective"] for c in calls] == ["xhigh", "xhigh", "xhigh"]


def test_without_the_flags_the_provider_defaults_are_described(tmp_path: Path) -> None:
    proc, _ws, out = _run(tmp_path, "bypass-approvals")  # claude-sonnet-5
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = _summary(out)
    assert summary["args"]["max_output_tokens"] is None
    assert summary["reasoning_effort"].startswith("high (Anthropic adaptive thinking")
    assert summary["context"] == {
        "window": 1_000_000,
        "window_source": "matrix",
        "compaction_trigger_tokens": 250_000,
    }


# -- transient provider errors ------------------------------------------------------------


def test_transient_provider_error_is_waited_out_and_the_run_completes(tmp_path: Path) -> None:
    fail = {"error": "Service unavailable", "error_type": "APIError"}
    write = {"text": "ok", "tool_calls": [{"name": "write_file", "arguments": {"path": "hello.txt", "content": "hello"}}]}
    proc, ws, out = _run(
        tmp_path, "bypass-approvals", [fail, write, {"text": "Done."}],
        prompt="Do something hard.", env={"OPENWORKER_RUN_RETRY_DELAYS": "0,0"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = _summary(out)
    assert summary["outcome"] == "completed" and summary["provider_retries"] == 1
    assert (ws / "hello.txt").read_text(encoding="utf-8") == "hello"
    kinds = [e["type"] for e in _events(out)]
    assert kinds.index("error") < kinds.index("provider_retry") < kinds.index("turn_end")
    users = [m["content"] for m in _messages(out) if m["role"] == "user"]
    assert users[0] == "Do something hard." and "temporary provider error" in users[1]


def test_empty_reply_is_nudged_and_a_permanent_error_is_not_retried(tmp_path: Path) -> None:
    empty = {"text": None}
    write = {"text": "ok", "tool_calls": [{"name": "write_file", "arguments": {"path": "hello.txt", "content": "hello"}}]}
    proc, ws, out = _run(
        tmp_path, "bypass-approvals", [empty, write, {"text": "Done."}],
        prompt="Do something hard.", env={"OPENWORKER_RUN_RETRY_DELAYS": "0,0"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _summary(out)["provider_retries"] == 1 and (ws / "hello.txt").exists()
    retry = next(e for e in _events(out) if e["type"] == "provider_retry")
    assert retry["data"]["error_type"] == "EmptyReply"

    second = tmp_path / "permanent"
    second.mkdir()
    fail = {"error": "Invalid API key provided", "error_type": "AuthenticationError"}
    proc, _ws, out = _run(
        second, "bypass-approvals", [fail, {"text": "never reached"}],
        prompt="Do something hard.", env={"OPENWORKER_RUN_RETRY_DELAYS": "0,0"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr  # a model error is a recorded outcome
    summary = _summary(out)
    assert summary["outcome"] == "model_error" and summary["provider_retries"] == 0


# -- persona and tool results -------------------------------------------------------------


def test_code_persona_runs_headless_and_is_recorded(tmp_path: Path) -> None:
    proc, ws, out = _run(tmp_path, "bypass-approvals", extra=["--persona", "code"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = _summary(out)
    assert summary["outcome"] == "completed" and summary["args"]["persona"] == "code"
    assert (ws / "hello.txt").read_text(encoding="utf-8") == "hello"
    system = next(m for m in _messages(out) if m["role"] == "system")["content"]
    assert "coding agent" in system


def test_large_tool_result_is_bounded_and_spilled(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    big = "\n".join(f"{i:04d} " + "x" * 55 for i in range(1500))  # ~90 KB, inside read_file's window
    (ws / "big.txt").write_text(big, encoding="utf-8")
    script = [
        {"text": "reading", "tool_calls": [{"name": "read_file", "arguments": {"path": "big.txt", "max_lines": 5000}}]},
        {"text": "Done."},
    ]
    proc, _ws, out = _run(tmp_path, "bypass-approvals", script, prompt="Read big.txt.")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    tool = [m for m in _messages(out) if m["role"] == "tool"]
    content = tool[0]["content"]
    assert len(content.encode("utf-8")) <= 10_000 and "bytes omitted here" in content
    spilled = list((out / "tool-output").iterdir())
    assert len(spilled) == 1 and "1499 xxxxx" in spilled[0].read_text(encoding="utf-8")
    assert _summary(out)["tool_results_bounded"] == 1


# -- extra roots (--root) -------------------------------------------------------------------


def _patch_script(target: Path) -> list[dict]:
    return [
        {"text": "Writing the patch.", "tool_calls": [{"name": "write_file", "arguments": {"path": str(target), "content": "--- a/x\n+++ b/x\n"}}]},
        {"text": "Done.", "usage": {"input": 10, "output": 5}},
    ]


def test_root_lets_the_file_tools_write_outside_the_workspace(tmp_path: Path) -> None:
    # A harness whose output contract lives outside the workspace (CyberGym: /output).
    outdir = tmp_path / "output"
    outdir.mkdir()
    target = outdir / "fix.patch"
    proc, _ws, out = _run(
        tmp_path, "bypass-approvals", _patch_script(target), extra=["--root", str(outdir)]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert target.read_text(encoding="utf-8").startswith("--- a/x")
    summary = _summary(out)
    assert summary["outcome"] == "completed" and summary["tool_calls_denied"] == 0
    assert summary["args"]["roots"] == [str(outdir.resolve())]
    # The model is told about the folder in the session's directories block (a per-turn
    # context, not the cached system prompt), the same way the workspace is announced.
    everything = "\n".join(str(m.get("content") or "") for m in _messages(out))
    assert str(outdir.resolve()) in everything


def test_without_root_the_same_write_stays_inside_the_workspace(tmp_path: Path) -> None:
    outdir = tmp_path / "output"
    outdir.mkdir()
    target = outdir / "fix.patch"
    proc, _ws, out = _run(tmp_path, "bypass-approvals", _patch_script(target))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not target.exists()
    assert _summary(out)["args"]["roots"] == []


def test_run_help_and_the_top_level_help_mention_the_command() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "coworker.cli", "run", "--help"],
        env=_env(), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0 and "usage: openworker run" in proc.stdout
    assert "--attendance" in proc.stdout and "--scripted" not in proc.stdout  # test-only flag stays hidden
    top = subprocess.run(
        [sys.executable, "-m", "coworker.cli"], env=_env(), capture_output=True, text=True, timeout=120
    )
    assert "run <task>" in top.stdout
