"""Regressions found while integrating the October permissions PR stack."""

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from coworker.permissions import Mode, PermissionEngine
from coworker.plan_steps import PlanStep, match_step
from coworker.provenance import SessionFiles
from coworker.review_facts import DESTINATION_NAMED, DESTINATION_NOT_NAMED, DESTINATION_UNKNOWN, destination_fact


def gate(root, *, auto=True):
    return PermissionEngine(
        workspace_root=root, mode=Mode.AUTO_APPROVE if auto else Mode.INTERACTIVE,
        shell_kind="bash", shell_cwd=lambda: str(root),
        readonly_commands="on", session_readonly=True,
    )


@pytest.mark.parametrize("auto", [False, True])
@pytest.mark.parametrize("command", ["tree *", "sort *", "find *", "awk *"])
def test_wildcard_options_never_receive_a_readonly_grant(tmp_path, auto, command):
    for name in ("-o", "victim.txt", "z-input.txt"):
        (tmp_path / name).write_text("fixture")
    assert not gate(tmp_path, auto=auto).evaluate("run_shell", {"command": command}).allowed


@pytest.mark.parametrize("auto", [False, True])
def test_ordinary_globbed_reads_still_work(tmp_path, auto):
    (tmp_path / "notes.md").write_text("fixture")
    assert gate(tmp_path, auto=auto).evaluate("run_shell", {"command": "cat *.md"}).allowed


@pytest.mark.parametrize("auto", [False, True])
def test_globbed_git_directory_cannot_skip_the_settings_probe(tmp_path, auto):
    (tmp_path / "repo-one").mkdir()
    assert not gate(tmp_path, auto=auto).evaluate("run_shell", {"command": "git -C repo* status"}).allowed


@pytest.mark.parametrize("command", [
    "cd missing || cat ../outside.txt",
    "cd missing; cat ../outside.txt",
    "false && cd child; cat ../outside.txt",
])
def test_cd_failure_cannot_change_the_path_used_for_approval(tmp_path, command):
    (tmp_path / "child").mkdir()
    assert not gate(tmp_path).evaluate("run_shell", {"command": command}).allowed


def test_cd_followed_by_and_is_safe_on_success_or_failure(tmp_path):
    (tmp_path / "child").mkdir()
    assert gate(tmp_path).evaluate("run_shell", {"command": "cd child && cat note.txt"}).allowed
    # If cd fails, the read is never executed.
    assert gate(tmp_path).evaluate("run_shell", {"command": "cd missing && cat note.txt"}).allowed


def matches(root, steps, tool, args):
    return match_step(steps, tool, args, resolve=lambda p: (root / p).resolve(), shell_base=lambda: str(root))


@pytest.mark.parametrize("approved,actual", [
    ('rm "draft  copy.txt"', 'rm "draft copy.txt"'),
    ("rm 'draft\tcopy.txt'", "rm 'draft copy.txt'"),
    ("echo first\necho second", "echo first echo second"),
    ("echo x`  y", "echo x` y"),
])
def test_command_matching_preserves_significant_whitespace(tmp_path, approved, actual):
    assert not matches(tmp_path, [PlanStep("run", approved)], "run_shell", {"command": actual})


@pytest.mark.parametrize("tool,args", [
    ("send_message", {"target": "slack:C123456", "text": "Deployment succeeded"}),
    ("slack_post_message", {"channel": "C123456", "text": "Deployment succeeded"}),
])
def test_message_approval_is_bound_to_the_exact_payload(tmp_path, tool, args):
    steps = [PlanStep("send", "Deployment succeeded", "slack:C123456")]
    assert matches(tmp_path, steps, tool, args)
    assert not matches(tmp_path, steps, tool, {**args, "text": "Unrelated confidential report"})
    assert not matches(tmp_path, steps, tool, {**args, "attachment": "other.txt"})


def test_agent_written_file_grant_expires_after_user_edits(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("agent draft")
    files = SessionFiles(tmp_path)
    files.record("write_file", {"path": "notes.md", "content": "agent draft"}, "ok", step=1)
    engine = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO_APPROVE,
                              inert_writes="on", agent_wrote=files.wrote)
    write = {"path": "notes.md", "content": "next draft", "overwrite": True}
    assert engine.evaluate("write_file", write).allowed
    path.write_text("user edits!")  # also changes an untracked file with no undo in git
    assert not engine.evaluate("write_file", write).allowed
    # A later explicitly approved write may establish a new fingerprint.
    path.write_text("next draft")
    files.record("write_file", write, "ok", step=2)
    assert engine.evaluate("write_file", write).allowed


@pytest.mark.parametrize("url,expected", [
    ("https://trusted.example:443@other.example/upload", DESTINATION_NOT_NAMED),
    ("https://trusted.example@other.example/upload", DESTINATION_NOT_NAMED),
    ("https://trusted.example:443/upload", DESTINATION_NAMED),
    ("https://trusted.example:bad/upload", DESTINATION_UNKNOWN),
    ("https://[broken/upload", DESTINATION_UNKNOWN),
])
def test_destination_facts_use_the_actual_url_host(url, expected):
    assert destination_fact("run_shell", {"command": f"curl {url}"},
                            ["Upload to https://trusted.example"]) == expected


def test_shadow_results_match_the_call_occurrence_even_when_the_review_finishes_late(tmp_path):
    from coworker.audit import AuditStore
    from coworker.engine import TurnEngine
    from coworker.providers import ToolCall

    store = AuditStore(tmp_path / "audit.db")
    first = SimpleNamespace(audit_sink=store.append, audit_context={"session_id": "A"})
    second = SimpleNamespace(audit_sink=store.append, audit_context={"session_id": "B"})
    call1 = ToolCall("call_0", "run_shell", {"command": "first"})
    call2 = ToolCall("call_0", "run_shell", {"command": "second"})
    call3 = ToolCall("call_0", "run_shell", {"command": "third"})
    for engine, call, decision in [(first, call1, "denied"), (first, call2, "approved"), (second, call3, "approved")]:
        TurnEngine._audit(engine, call, stage="proposed")
        TurnEngine._audit(engine, call, stage="approval_resolved", status=decision)
    # Shadow replies need not arrive in call order, or before the user's decision.
    for engine, call in [(first, call2), (second, call3), (first, call1)]:
        TurnEngine._audit(engine, call, stage="reviewer_shadow", status="allow", reviewer="jev")
    assert [r["decision"] for r in store.shadow_rows("jev")] == ["approved", "approved", "denied"]


@pytest.mark.asyncio
async def test_headless_mcp_startup_timeout_closes_the_unresponsive_server(tmp_path, monkeypatch):
    from coworker import mcp
    from coworker.headless import runner
    from coworker.mcp.config import MCPServerDef

    instances = []
    class TrackingManager(mcp.MCPManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            instances.append(self)

    monkeypatch.setattr(mcp, "MCPManager", TrackingManager)
    monkeypatch.setattr(runner, "_MCP_CONNECT_TIMEOUT", 0.5)
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "hung.py"
    script.write_text("import os,time\nfrom pathlib import Path\n"
                      f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
                      "time.sleep(60)\n")
    server = MCPServerDef(name="hung", transport="stdio", command=sys.executable, args=[str(script)])
    notes = []
    started = time.monotonic()
    manager = await asyncio.wait_for(runner._attach_mcp(
        SimpleNamespace(registry=None), [server], None,
        on_note=lambda kind, data: notes.append((kind, data)),
    ), timeout=10)
    assert manager is None
    assert time.monotonic() - started < 10
    assert notes[0][0] == "mcp_server_failed" and "timed out" in notes[0][1]["error"]
    assert not instances[0]._tasks and not instances[0]._conns
    if sys.platform != "win32" and pid_file.exists():
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
