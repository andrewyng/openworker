"""The read-only session grant reads the session's folders, not the machine (OPE-130).

`readonly.py` vets what a command DOES — carefully, and fail-closed. It said nothing about
what a command READS, so a grant the user reads as "stop asking about my project files"
also covered ~/.aws/credentials, another repository's history, and OpenWorker's own secrets
file. The self-protection floor does not cover that: it guards those files against
modification, not reading.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from coworker.permissions import Mode, PermissionEngine
from coworker.readonly import is_readonly_command, read_targets


@pytest.fixture()
def session(tmp_path):
    """A session whose granted root is `repo/`, with the grant already clicked."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "build.log").write_text("boom", encoding="utf-8")
    engine = PermissionEngine(workspace_root=root, mode=Mode.INTERACTIVE)
    engine.allow_readonly_for_session()
    return engine


def runs(engine, command: str) -> bool:
    return engine.evaluate("run_shell", {"command": command}, None).allowed


# -- the grant still does the job it was built for --------------------------------
# Born of ~15 hand-approvals per security-scan run; scoping must not undo that.
@pytest.mark.parametrize(
    "command",
    [
        "cat build.log",
        "grep AWS_SECRET build.log",
        "head -n 5 build.log",
        "ls src",
        "git status",
        "git log --oneline",
        "pwd",
        "whoami",
        "cat build.log | grep error",
        "find . -name '*.py'",
    ],
)
def test_reads_inside_the_granted_folder_still_run_unprompted(session, command):
    assert runs(session, command), command


# -- and no longer reads the rest of the machine ----------------------------------
@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.aws/credentials",
        "strings ~/.ssh/id_rsa",
        "cat /etc/shadow",
        "grep -r AWS_SECRET /",
        "find / -name id_rsa",
        "head -n 5 ~/.aws/credentials",
    ],
)
def test_reads_outside_every_root_now_ask(session, command):
    assert not runs(session, command), command


def test_openworkers_own_secrets_are_no_longer_readable(session):
    # The floor hard-denies WRITES to this file in every mode and cannot be overridden.
    # Reading was never checked, so one click on a convenience button dumped it into the
    # transcript — and from there to the model provider on the next turn.
    assert not runs(session, "cat ~/.config/coworker/secrets.json")
    assert not runs(session, "jq . ~/.config/coworker/secrets.json")


def test_another_repository_is_not_in_scope(session):
    # `git -C <dir>` is the one accepted way to leave the working directory.
    assert not runs(session, "git -C ~/other-private-repo log -p")


def test_traversal_is_resolved_not_string_matched(session):
    assert not runs(session, "cat ../../../etc/passwd")
    assert not runs(session, "cat ./../../etc/passwd")


def test_a_second_granted_root_is_in_scope(tmp_path):
    from coworker.roots import RootDir

    repo, notes = tmp_path / "repo", tmp_path / "notes"
    repo.mkdir()
    notes.mkdir()
    (notes / "todo.md").write_text("x", encoding="utf-8")
    engine = PermissionEngine(
        workspace_root=repo,
        mode=Mode.INTERACTIVE,
        roots=[RootDir(path=repo, writable=True), RootDir(path=notes, writable=False)],
    )
    engine.allow_readonly_for_session()
    # Reading needs read access, not write access.
    assert runs(engine, f"cat {notes / 'todo.md'}")
    assert not runs(engine, "cat ~/.aws/credentials")


def test_a_pipeline_is_only_as_scoped_as_its_stages(session):
    assert runs(session, "cat build.log | grep -i error")
    # One out-of-scope stage is enough to stop the whole pipeline.
    assert not runs(session, "cat build.log | grep -f ~/.ssh/id_rsa")


def test_scoping_never_loosens_the_verb_rules(session):
    # The classifier's own rejections stand: a path inside the workspace does not make
    # an executing or networking command acceptable.
    for command in ("python -c 'print(1)'", "curl https://x.io", "pytest -q", "rm build.log"):
        assert not runs(session, command), command


def test_without_the_grant_nothing_changes(tmp_path):
    engine = PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE)
    assert not runs(engine, "cat build.log")  # still asks — no grant was made


# -- target extraction ------------------------------------------------------------
@pytest.mark.parametrize(
    "command,expected",
    [
        ("cat a.txt b.txt", ["a.txt", "b.txt"]),
        ("grep PATTERN a.txt", ["a.txt"]),  # the pattern is not a file
        ("grep -f patterns.txt a.txt", ["patterns.txt", "a.txt"]),  # -f supplies it instead
        ("head -n 5 a.txt", ["a.txt"]),  # a count is not a file
        ("cut -f 1 data.csv", ["data.csv"]),  # -f is a FIELD here, not a file
        ("git -C /elsewhere log", ["/elsewhere"]),
        ("git status", []),
        ("echo hello", []),
        ("pwd", []),
        ("jq .foo data.json", ["data.json"]),  # the filter is not a file
        ("find /tmp -name x", ["/tmp"]),  # predicates end the path list
        ("nl a.txt | sed -n 2p", ["a.txt"]),
    ],
)
def test_read_targets_names_the_files_and_not_the_arguments(command, expected):
    assert read_targets(command) == expected


def test_read_targets_is_empty_for_commands_the_classifier_rejects():
    # Nothing to scope when the command never gets that far.
    assert not is_readonly_command("curl https://x.io > out")
    assert read_targets("curl https://x.io > out") == []
