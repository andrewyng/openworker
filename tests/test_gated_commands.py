"""The gate on irreversible commands.

This machine runs `mode = "custom"` with `auto_allow = ["run_shell"]`, so before this
existed every shell command ran with no prompt — `git push` included. The persona prose
said "never push"; a 35B model following prose is not a gate. These tests are the gate.
"""

from pathlib import Path

import pytest

from coworker.permissions import Mode, PermissionEngine
from coworker.risk import SHELL_TOOL

GATES = ["git push", "docker compose up", "fly deploy"]


def engine(tmp_path: Path, mode: Mode = Mode.CUSTOM) -> PermissionEngine:
    return PermissionEngine(
        workspace_root=tmp_path,
        mode=mode,
        auto_allow_tools={SHELL_TOOL},
        allowed_commands=["git status", "git push"],  # even an explicit allowlist loses
        gated_commands=list(GATES),
    )


def decide(eng: PermissionEngine, command: str):
    return eng.evaluate(SHELL_TOOL, {"command": command})


# --- the rule binds -----------------------------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git push origin main",
        "git push --force",
        "git -C /home/me/repo push origin main",  # flag between command and subcommand
        "/usr/bin/git push",  # absolute path
        "cd repo && git push",  # chained after a benign command
        "git status; git push",
        "true | git push",
        'bash -lc "git push origin main"',  # quoted sub-command
        'ssh box "git push"',  # pushing from somewhere else is still pushing
        "sudo git push",  # wrapper in front of the real command
        "env GIT_DIR=/x git push",
        "docker compose up -d",
        "fly deploy --now",
    ],
)
def test_gated_commands_always_ask(tmp_path, command):
    d = decide(engine(tmp_path), command)
    assert not d.allowed, f"{command!r} was allowed"
    assert d.needs_user, f"{command!r} was denied outright instead of asking"


@pytest.mark.parametrize("mode", [Mode.BYPASS_APPROVALS, Mode.CUSTOM, Mode.INTERACTIVE])
def test_gate_holds_in_every_mode(tmp_path, mode):
    """Including BYPASS_APPROVALS — an unattended automation is exactly when nobody is watching."""
    d = decide(engine(tmp_path, mode), "git push origin main")
    assert not d.allowed and d.needs_user


def test_session_approval_cannot_widen_into_the_gate(tmp_path):
    """Approving `git push` once must not stand for the rest of the run."""
    eng = engine(tmp_path)
    eng.allow_tool_for_session(SHELL_TOOL)
    eng.allow_command_for_session("git push origin main")
    d = decide(eng, "git push origin main")
    assert not d.allowed and d.needs_user


def test_unparseable_command_is_gated_not_waved_through(tmp_path):
    d = decide(engine(tmp_path), 'git push "unbalanced')
    assert not d.allowed and d.needs_user


# --- the rule does NOT bind ---------------------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git commit -m 'wip'",
        "git log --oneline -5",
        "pytest -q",
        "docker ps",  # 'docker' alone is not a gate; 'docker compose up' is
        "docker compose down",
        "echo 'git push'",  # the words as data, not as a command
        "grep -r 'git push' .",
        "cat deploy.md | head -20",
    ],
)
def test_ordinary_commands_still_auto_approve(tmp_path, command):
    """The other half of the check. A gate that stops everything is not a gate."""
    d = decide(engine(tmp_path), command)
    assert d.allowed, f"{command!r} was blocked: {d.reason}"


def test_empty_gate_list_changes_nothing(tmp_path):
    """The shipped default. Existing installs must behave exactly as before."""
    eng = PermissionEngine(
        workspace_root=tmp_path, mode=Mode.CUSTOM, auto_allow_tools={SHELL_TOOL}
    )
    assert decide(eng, "git push origin main").allowed


def test_gate_does_not_leak_into_non_shell_tools(tmp_path):
    d = engine(tmp_path).evaluate("write_file", {"path": str(tmp_path / "git push")})
    assert d.allowed or not d.reason.endswith("always needs approval")
