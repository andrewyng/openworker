"""Phase 0 gate — risk-class classification + the permission engine driven by it.

Asserts ``classify`` maps tools to the right risk class (replacing the old hardcoded
WRITE_TOOLS / SHELL_TOOL sets) and that ``PermissionEngine`` decisions follow from the class
across all five modes, including the ``external`` class (the unattended Inbox hook)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from coworker.permissions import Mode, PermissionEngine
from coworker.risk import RiskClass, classify, is_consequential

EXTERNAL_META = SimpleNamespace(requires_approval=True, category="connector")
PLAIN_META = SimpleNamespace(requires_approval=False)


# -- classify -------------------------------------------------------------------
@pytest.mark.parametrize(
    "name,meta,expected",
    [
        ("write_file", None, RiskClass.WRITE_LOCAL),
        ("replace_in_file", None, RiskClass.WRITE_LOCAL),
        ("apply_patch", None, RiskClass.WRITE_LOCAL),
        ("apply_unified_diff", None, RiskClass.WRITE_LOCAL),
        ("run_shell", None, RiskClass.EXEC),
        ("read_file", None, RiskClass.READ),
        ("grep", None, RiskClass.READ),
        ("git_log", None, RiskClass.READ),
        ("todo_write", None, RiskClass.READ),
        ("send_message", EXTERNAL_META, RiskClass.EXTERNAL),
        ("anything", PLAIN_META, RiskClass.READ),
        ("anything", None, RiskClass.READ),
    ],
)
def test_classify(name, meta, expected):
    assert classify(name, meta) == expected


def test_is_consequential():
    assert not is_consequential(RiskClass.READ)
    assert is_consequential(RiskClass.WRITE_LOCAL)
    assert is_consequential(RiskClass.EXEC)
    assert is_consequential(RiskClass.EXTERNAL)


def test_overrides_win_over_base_and_metadata():
    # A user-local override beats both the by-name base table and the metadata fallback.
    relax = lambda n: RiskClass.READ if n in {"write_file", "mcp_tool"} else None
    assert classify("write_file", None, relax) == RiskClass.READ  # downgrade a write
    assert classify("mcp_tool", EXTERNAL_META, relax) == RiskClass.READ  # relax MCP
    # Non-matching names fall through to the base/metadata classification.
    assert classify("run_shell", None, relax) == RiskClass.EXEC


# -- PermissionEngine driven by risk class --------------------------------------
def test_read_always_allowed(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path)
    d = eng.evaluate("read_file", {"path": "x"}, None)
    assert d.allowed and not d.needs_user


@pytest.mark.parametrize("mode", [Mode.DISCUSS, Mode.PLAN])
def test_read_only_modes_block_consequential(tmp_path, mode):
    eng = PermissionEngine(workspace_root=tmp_path, mode=mode)
    for name, meta in [
        ("write_file", None),
        ("run_shell", None),
        ("send_message", EXTERNAL_META),
    ]:
        args = {"path": "a.py", "content": "x"} if name == "write_file" else {}
        d = eng.evaluate(name, args, meta)
        assert not d.allowed and not d.needs_user
        assert "read-only" in d.reason


def test_external_asks_in_interactive_allows_in_auto(tmp_path):
    interactive = PermissionEngine(workspace_root=tmp_path)
    d = interactive.evaluate("send_message", {"text": "hi"}, EXTERNAL_META)
    assert not d.allowed and d.needs_user

    auto = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)
    d = auto.evaluate("send_message", {"text": "hi"}, EXTERNAL_META)
    assert d.allowed


def test_write_local_path_scoped(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)
    assert eng.evaluate("write_file", {"path": "ok.py", "content": "x"}, None).allowed
    escape = eng.evaluate("write_file", {"path": "../bad.py", "content": "x"}, None)
    assert not escape.allowed


def test_exec_uses_command_allowlist(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, allowed_commands=["pytest"])
    assert eng.evaluate("run_shell", {"command": "pytest -q"}, None).allowed
    asked = eng.evaluate("run_shell", {"command": "rm -rf /"}, None)
    assert not asked.allowed and asked.needs_user


@pytest.mark.parametrize(
    "command",
    [
        "git status && rm -rf ~",  # chaining
        "git status; rm -rf ~",  # sequencing
        "git status | tee output.txt",  # pipe
        "git status || curl evil",  # or-chain
        "git status $(rm -rf ~)",  # command substitution
        "git status `rm -rf ~`",  # backtick substitution
        "git status > output.txt",  # redirection
        "git status\nrm -rf ~",  # newline-embedded second command
    ],
)
def test_allowlist_rejects_shell_operator_chaining(tmp_path, command):

    # An allowlisted prefix must NOT auto-run a command that chains anything after it.
    eng = PermissionEngine(workspace_root=tmp_path, allowed_commands=["git status"])
    d = eng.evaluate("run_shell", {"command": command}, None)
    assert not d.allowed and d.needs_user, command


def test_allowlist_prefix_is_argv_boundary(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, allowed_commands=["git status", "ls"])
    # Exact and sub-argument extensions of the allowlisted argv are fine.
    assert eng.evaluate("run_shell", {"command": "git status"}, None).allowed
    assert eng.evaluate("run_shell", {"command": "git status -s"}, None).allowed
    assert eng.evaluate("run_shell", {"command": "ls -la"}, None).allowed
    # A different subcommand or a token that merely shares a prefix is NOT allowed.
    assert eng.evaluate("run_shell", {"command": "git push"}, None).needs_user
    assert eng.evaluate("run_shell", {"command": "lsof"}, None).needs_user


def test_shell_commands_not_auto_allowed_by_default(tmp_path):
    # There is no generally safe executable: these examples cover code execution,
    # environment disclosure, reads outside the workspace, and helper execution.
    from coworker.config import DEFAULT_ALLOWED_COMMANDS

    eng = PermissionEngine(
        workspace_root=tmp_path, allowed_commands=list(DEFAULT_ALLOWED_COMMANDS)
    )
    for cmd in (
        "python3 -c 'import os'",
        "pytest /tmp/attacker_test.py",
        "find . -exec sh -c 'echo arbitrary' {} +",
        "cat ~/.config/coworker/secrets.json",
        "echo $OPENAI_API_KEY",
        "git status",
    ):
        d = eng.evaluate("run_shell", {"command": cmd}, None)
        assert not d.allowed, cmd



def test_extract_shell_write_targets():
    from coworker.permissions import extract_shell_write_targets

    assert extract_shell_write_targets('copy poem.txt "C:\\Users\\Admin\\Documents\\poem.txt"') == [
        "C:\\Users\\Admin\\Documents\\poem.txt"
    ]
    assert extract_shell_write_targets("cp poem.txt ../../outside.txt") == [
        "../../outside.txt"
    ]
    assert extract_shell_write_targets("echo hello > /etc/passwd") == ["/etc/passwd"]
    assert extract_shell_write_targets("powershell Copy-Item -Path poem.txt -Destination C:\\Out\\file.txt") == [
        "C:\\Out\\file.txt"
    ]
    assert extract_shell_write_targets("git status") == []


def test_shell_command_path_scoping_blocks_out_of_bounds_writes(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)

    # In-bounds shell copy is allowed in Mode.AUTO
    in_bounds = f"copy poem.txt {tmp_path / 'poem_copy.txt'}"
    assert eng.evaluate("run_shell", {"command": in_bounds}, None).allowed

    # Out-of-bounds shell copy (e.g. jailbreak attempt to Documents) is HARD DENIED
    jailbreak_cmd = 'copy poem.txt "C:\\Users\\Admin\\Documents\\poem.txt"'
    d = eng.evaluate("run_shell", {"command": jailbreak_cmd}, None)
    assert not d.allowed
    assert "not in a writable directory" in d.reason

    # Out-of-bounds redirection is HARD DENIED
    redir_escape = "echo test > /tmp/escape.txt"
    d2 = eng.evaluate("run_shell", {"command": redir_escape}, None)
    assert not d2.allowed
    assert "not in a writable directory" in d2.reason


def test_extract_shell_all_paths():
    from coworker.permissions import extract_shell_all_paths

    assert extract_shell_all_paths('cat "C:\\Users\\Admin\\Documents\\secret.txt"') == [
        "C:\\Users\\Admin\\Documents\\secret.txt"
    ]
    assert extract_shell_all_paths("grep foo /etc/passwd") == ["/etc/passwd"]
    assert extract_shell_all_paths("type ..\\..\\Windows\\System32\\config\\SAM") == [
        "..\\..\\Windows\\System32\\config\\SAM"
    ]
    assert extract_shell_all_paths("git status") == []


def test_shell_command_path_scoping_blocks_unallowed_reads(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)

    # In-bounds shell read is allowed in Mode.AUTO
    assert eng.evaluate("run_shell", {"command": "cat poem.txt"}, None).allowed

    # Unallowed shell read from unallowed Windows folder is HARD DENIED
    read_win = 'cat "C:\\Users\\Admin\\Documents\\secret.txt"'
    d = eng.evaluate("run_shell", {"command": read_win}, None)
    assert not d.allowed
    assert "not in an allowed directory" in d.reason

    # Unallowed shell copy reading FROM unallowed folder is HARD DENIED
    copy_from_unallowed = 'copy "C:\\Users\\Admin\\Documents\\secret.txt" poem.txt'
    d2 = eng.evaluate("run_shell", {"command": copy_from_unallowed}, None)
    assert not d2.allowed
    assert "not in an allowed directory" in d2.reason

    # Unallowed shell traversal read is HARD DENIED
    read_traversal = "type ..\\..\\secret.txt"
    d3 = eng.evaluate("run_shell", {"command": read_traversal}, None)
    assert not d3.allowed
    assert "not in an allowed directory" in d3.reason


