"""OPE-232: the read-only session grant knows which shell runs the command and fails closed.

"Allow read-only commands for this session" lets the commands `coworker/readonly.py`
accepts run without a card. The check now:

* takes the shell kind: bash on macOS / Linux, PowerShell on Windows. For PowerShell only a
  plain command with literal arguments is eligible: anything that can evaluate code or
  expand values is rejected;
* treats a command on more than one line as more than one command (rejected);
* accepts only harmless environment prefixes (locale, time zone, colour);
* names the paths carried by flags (`--file=PATH`, `-fPATH`, `-Path:PATH`) as read targets;
* judges paths from the shell's real current folder when the engine knows it (the shell
  persists between calls), and stops trusting it once another shell command has been
  cleared earlier in the same batch;
* expands wildcards and checks every match.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from coworker.permissions import Mode, PermissionEngine
from coworker.readonly import is_readonly_command, read_targets


# -- bash -----------------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["ls\ntouch x", "ls\r\ngit status", "git status\n", "cat a\x0bb"])
def test_more_than_one_line_is_never_read_only(command):
    assert not is_readonly_command(command)
    assert not is_readonly_command(command, shell="bash")


@pytest.mark.parametrize(
    "command, ok",
    [
        ("LC_ALL=C grep foo build.log", True),
        ("LANG=en_US.UTF-8 sort build.log", True),
        ("TZ=UTC date", True),
        ("NO_COLOR=1 ls", True),
        ("GIT_EXTERNAL_DIFF=./tool.sh git diff", False),
        ("PAGER=./x git log", False),
        ("GIT_PAGER=./x git log", False),
        ("LESSOPEN=x cat build.log", False),
        ("GIT_DIR=../other git log", False),
    ],
)
def test_only_harmless_environment_prefixes(command, ok):
    assert is_readonly_command(command) is ok, command


@pytest.mark.parametrize(
    "command, path",
    [
        ("grep --file=/etc/hosts foo src", "/etc/hosts"),
        ("grep -f/etc/hosts foo src", "/etc/hosts"),
        ("rg --ignore-file=../x foo", "../x"),
        ("diff --from-file=../a b", "../a"),
    ],
)
def test_a_path_carried_by_a_flag_is_a_read_target(command, path):
    assert is_readonly_command(command)
    assert path in read_targets(command)


def test_bash_behaviour_is_otherwise_unchanged():
    assert is_readonly_command("git status")
    assert is_readonly_command("nl -ba x.py | sed -n 1,20p | grep foo")
    assert is_readonly_command("awk '{print $1}' data.txt")
    assert not is_readonly_command("ls; ls")
    assert read_targets("grep -n foo src/x.py") == ["src/x.py"]


# -- PowerShell ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git log --oneline -5",
        "ls",
        "ls -Recurse src",
        "cat README.md",
        "cat README.md | sort",
        "git diff --stat",
    ],
)
def test_powershell_plain_commands_stay_eligible(command):
    assert is_readonly_command(command, shell="powershell"), command


@pytest.mark.parametrize(
    "command",
    [
        "cat $env:USERPROFILE\\notes.txt",  # variables
        'cat "$HOME\\notes.txt"',  # expandable string
        "ls (Get-Item .)",  # grouping / sub-expression
        "cat $(Get-Location)\\x",
        "cat @('a.txt')",  # array literal
        "ls @{a=1}",  # hash literal
        "ls | % { $_ }",  # script block
        "cat a.txt,b.txt",  # an array of paths
        "ls `$x",  # backtick escape
        "& git status",  # call operator
        ". .\\x.ps1",  # dot-sourcing
        "ls; ls",
        "cat a.txt > b.txt",
        "cat [ab].txt",
        "ls --% -x",  # stop-parsing token
        "awk '{print $1}' data.txt",
        "ls\ncat x",
    ],
)
def test_powershell_constructs_are_rejected(command):
    assert not is_readonly_command(command, shell="powershell"), command


def test_powershell_named_path_values_are_targets():
    # PowerShell has no backslash escape; paths come back with forward slashes, which
    # name the same file.
    assert "C:/secret.txt" in read_targets("cat -Path C:\\secret.txt", shell="powershell")
    assert "C:/secret.txt" in read_targets("cat C:\\secret.txt", shell="powershell")
    assert ".." in read_targets("ls -Path:..", shell="powershell")


def test_an_unknown_shell_fails_closed():
    assert not is_readonly_command("git status", shell="fish")
    assert read_targets("git status", shell="fish") == []


# -- the session grant --------------------------------------------------------------------------


def _grant(tmp_path: Path, shell: str = "bash") -> PermissionEngine:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "build.log").write_text("boom", encoding="utf-8")
    (root / "notes.md").write_text("n", encoding="utf-8")
    engine = PermissionEngine(workspace_root=root, mode=Mode.INTERACTIVE, shell_kind=shell)
    engine.allow_readonly_for_session()
    return engine


def _runs(engine: PermissionEngine, command: str) -> bool:
    return engine.evaluate("run_shell", {"command": command}, None).allowed


def test_the_default_shell_follows_the_platform(tmp_path):
    engine = PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE)
    assert engine.shell_kind == ("powershell" if sys.platform == "win32" else "bash")


def test_the_grant_asks_for_what_it_must_not_wave_through(tmp_path):
    engine = _grant(tmp_path)
    assert _runs(engine, "cat build.log")
    for command in (
        "ls\ntouch x",
        "GIT_EXTERNAL_DIFF=./tool.sh git diff",
        "grep --file=/etc/hosts foo build.log",
        "grep -f/etc/hosts foo build.log",
    ):
        assert not _runs(engine, command), command
    assert _runs(engine, "grep --file=notes.md foo build.log")  # a flag path inside is fine


def test_the_grant_on_powershell_rejects_its_constructs(tmp_path):
    engine = _grant(tmp_path, shell="powershell")
    assert _runs(engine, "git status")
    assert _runs(engine, "cat build.log")
    assert not _runs(engine, "cat $env:USERPROFILE\\x")
    assert not _runs(engine, "cat build.log,notes.md")
    assert not _runs(engine, "cat -Path ..\\outside.txt")


def test_the_grant_judges_paths_from_the_shells_folder(tmp_path):
    engine = _grant(tmp_path)
    root = engine.workspace_root
    engine.shell_cwd = lambda: str(root / "src")
    assert _runs(engine, "cat ../build.log")  # still inside, seen from src/
    engine.shell_cwd = lambda: str(root.parent)
    assert not _runs(engine, "cat build.log")  # the shell is outside the folders
    assert not _runs(engine, "ls")
    engine.shell_cwd = lambda: None  # known to be unknown: ask
    assert not _runs(engine, "cat build.log")


def test_without_shell_wiring_the_workspace_folder_is_used(tmp_path):
    engine = _grant(tmp_path)
    engine.shell_cwd = None
    assert _runs(engine, "cat build.log")


def test_the_grant_expands_wildcards_and_checks_every_match(tmp_path):
    engine = _grant(tmp_path)
    root = engine.workspace_root
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        os.symlink(outside, root / "notes-link.md")
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create symlinks")
    assert not _runs(engine, "cat notes-link.md")
    assert not _runs(engine, "cat notes*")  # the wildcard reaches the link
    assert _runs(engine, "cat build*")


# -- the engine: a batch is cleared before it runs ----------------------------------------------

from coworker.providers import AssistantTurn, ToolCall  # noqa: E402
from test_auto_approve import _engine, _run  # noqa: E402


def test_after_one_shell_command_clears_the_rest_of_the_batch_asks(tmp_path):
    # Every call in a batch is cleared before any runs, and the shell persists: a `cd`
    # earlier in the batch could change where a later relative path points.
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "build.log").write_text("boom", encoding="utf-8")
    turn = AssistantTurn(
        tool_calls=[
            ToolCall(id="a", name="run_shell", arguments={"command": "git status"}),
            ToolCall(id="b", name="run_shell", arguments={"command": "cat build.log"}),
        ],
        finish_reason="tool_calls",
    )
    engine, _rows, approvals = _engine(root, [turn, AssistantTurn(text="ok", finish_reason="stop")], mode=Mode.INTERACTIVE)
    engine.permissions.shell_kind = "bash"
    engine.permissions.allow_readonly_for_session()
    engine.executor = SimpleNamespace(cwd=str(root))
    engine.permissions.shell_cwd = engine.current_shell_cwd
    _run(engine, "look around")
    assert approvals == ["run_shell"]  # the second command got a card


def test_a_new_batch_trusts_the_shell_folder_again(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "build.log").write_text("boom", encoding="utf-8")
    turns = [
        AssistantTurn(tool_calls=[ToolCall(id="a", name="run_shell", arguments={"command": "git status"})], finish_reason="tool_calls"),
        AssistantTurn(tool_calls=[ToolCall(id="b", name="run_shell", arguments={"command": "cat build.log"})], finish_reason="tool_calls"),
        AssistantTurn(text="ok", finish_reason="stop"),
    ]
    engine, _rows, approvals = _engine(root, turns, mode=Mode.INTERACTIVE)
    engine.permissions.shell_kind = "bash"
    engine.permissions.allow_readonly_for_session()
    engine.executor = SimpleNamespace(cwd=str(root))
    engine.permissions.shell_cwd = engine.current_shell_cwd
    _run(engine, "look around")
    assert approvals == []


# -- the card's button ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, shell, expected",
    [
        ("grep foo notes.txt", "bash", True),
        ("grep foo notes.txt", "powershell", False),  # not a PowerShell command the grant covers
        ("Select-String foo notes.txt", "powershell", True),
        ("Select-String foo notes.txt", "bash", False),
        ("", "bash", False),
    ],
)
def test_the_card_offers_the_grant_only_for_commands_it_covers(command, shell, expected):
    from coworker.engine import _readonly_ok

    assert _readonly_ok({"command": command}, shell) is expected
