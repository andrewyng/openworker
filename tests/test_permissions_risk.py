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
        "git status | tee /tmp/x",  # pipe
        "git status || curl evil",  # or-chain
        "git status $(rm -rf ~)",  # command substitution
        "git status `rm -rf ~`",  # backtick substitution
        "git status > /etc/passwd",  # redirection
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


@pytest.mark.parametrize(
    "command",
    [
        # Interpreters: the argument is a program the user never saw.
        "python3 /tmp/evil.py",
        "python3 -c 'import shutil'",
        "python3 -m http.server",
        "python3 -m pip install attacker-pkg",
        "node /tmp/evil.js",
        "perl /tmp/evil.pl",
        # Package managers: install/exec fetch remote code and run its hooks.
        "npm install attacker-pkg",
        "npm run anything",
        "npm exec -- attacker-pkg",
        "pip install attacker-pkg",
        # Exec wrappers: the argument IS the command to run.
        "env FOO=1 /tmp/evil.sh",
        "xargs rm",
        "sudo rm -rf /",
        # find's helper-exec family. The classic `-exec ... ;` form is already caught by
        # operator rejection; `{} +` carries no shell metacharacter at all.
        "find . -exec touch /tmp/pwned {} +",
        "find . -execdir /tmp/evil.sh {} +",
        "find . -ok rm {} +",
    ],
)
def test_bare_allowlist_entry_does_not_auto_run_delegated_execution(tmp_path, command):
    # A bare program name auto-runs the program, not everything the program can be
    # pointed at. Every one of these used to auto-run under the allowlist that
    # docs/config.example.toml recommended.
    eng = PermissionEngine(
        workspace_root=tmp_path,
        allowed_commands=["python3", "node", "perl", "npm", "pip", "env", "xargs", "sudo", "find"],
    )
    d = eng.evaluate("run_shell", {"command": command}, None)
    assert not d.allowed and d.needs_user, command


@pytest.mark.parametrize(
    "command",
    [
        "find . -name '*.py'",
        "find . -type f -newer setup.py",
        "find . -maxdepth 2 -size +1k",
    ],
)
def test_runner_escape_check_leaves_ordinary_use_alone(tmp_path, command):
    # Only the helper-exec flags are held back; `find` itself stays useful.
    eng = PermissionEngine(workspace_root=tmp_path, allowed_commands=["find"])
    assert eng.evaluate("run_shell", {"command": command}, None).allowed, command


def test_entry_that_pins_the_operation_still_auto_runs(tmp_path):
    # Naming the operation in the entry is the opt-in: the user chose that authority, and
    # further arguments may be appended to it as with any other allowlist entry.
    eng = PermissionEngine(
        workspace_root=tmp_path,
        allowed_commands=["npm test", "python3 -m pytest", "find . -exec"],
    )
    assert eng.evaluate("run_shell", {"command": "npm test"}, None).allowed
    assert eng.evaluate("run_shell", {"command": "npm test --silent"}, None).allowed
    assert eng.evaluate("run_shell", {"command": "python3 -m pytest -q"}, None).allowed
    assert eng.evaluate(
        "run_shell", {"command": "find . -exec grep -l TODO {} +"}, None
    ).allowed
    # ...but only that operation, not a sibling the entry never named.
    assert eng.evaluate("run_shell", {"command": "npm install pkg"}, None).needs_user


def test_runner_check_matches_on_the_program_not_the_path(tmp_path):
    # An absolute path to the same interpreter must not slip past the check.
    eng = PermissionEngine(workspace_root=tmp_path, allowed_commands=["/usr/bin/python3"])
    assert eng.evaluate("run_shell", {"command": "/usr/bin/python3"}, None).allowed
    d = eng.evaluate("run_shell", {"command": "/usr/bin/python3 /tmp/evil.py"}, None)
    assert not d.allowed and d.needs_user


@pytest.mark.parametrize(
    "entry,command",
    [
        ("python.exe", "python.exe C:/tmp/evil.py"),
        ("python3.exe", "python3.exe evil.py"),
        ("node.exe", "node.exe evil.js"),
        ("npm.cmd", "npm.cmd install attacker-pkg"),
        ("Python3", "Python3 evil.py"),
        ("PYTHON3", "PYTHON3 evil.py"),
        (r"C:\\Python\\python.exe", r"C:\\Python\\python.exe evil.py"),
    ],
)
def test_runner_check_survives_windows_spelling(tmp_path, entry, command):
    # Windows names the same interpreters python.exe / npm.cmd, and paths use backslashes.
    # Matching on the bare lowercased stem keeps the rule from being spelled around.
    eng = PermissionEngine(workspace_root=tmp_path, allowed_commands=[entry])
    d = eng.evaluate("run_shell", {"command": command}, None)
    assert not d.allowed and d.needs_user, command


def test_positional_script_interpreters_are_held_back(tmp_path):
    # `sed`/`awk` carry their program as a POSITIONAL argument (`sed 'e ls'` shells out on
    # GNU sed), so there is no flag to key on and they belong with the interpreters.
    eng = PermissionEngine(workspace_root=tmp_path, allowed_commands=["sed", "awk"])
    assert eng.evaluate("run_shell", {"command": "sed"}, None).allowed
    assert eng.evaluate("run_shell", {"command": "sed e_ls README.md"}, None).needs_user
    assert eng.evaluate("run_shell", {"command": "awk BEGIN_system README.md"}, None).needs_user


def test_project_local_task_runners_are_unaffected(tmp_path):
    # `pytest`/`make` run the workspace's own code, which the agent can already edit, so
    # they stay on the ordinary prefix rule. Pointing them outside the workspace is a
    # path-scoping concern, not something the command allowlist can express.
    eng = PermissionEngine(workspace_root=tmp_path, allowed_commands=["pytest", "make"])
    assert eng.evaluate("run_shell", {"command": "pytest -q"}, None).allowed
    assert eng.evaluate("run_shell", {"command": "make build"}, None).allowed


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
        assert not d.allowed and d.needs_user, cmd
