"""Tests for workspace turn checkpointing via git shadow refs (Issue #614)."""

import subprocess
from pathlib import Path

import pytest

from coworker.engine import TurnEngine
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import AssistantTurn, ProviderClient, ToolCall
from coworker.tools import ToolRegistry
from coworker.tools.git import (
    _git_env,
    create_checkpoint,
    git_tools,
    is_git_repo,
    list_checkpoints,
    restore_checkpoint,
)


def _init_git_repo(path: Path) -> None:
    env = _git_env()
    subprocess.run(
        ["git", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@test.local",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        ],
        cwd=path,
        check=True,
        capture_output=True,
        env=env,
    )


def test_is_git_repo(tmp_path):
    assert not is_git_repo(tmp_path)
    _init_git_repo(tmp_path)
    assert is_git_repo(tmp_path)


def test_create_checkpoint_non_git_returns_none(tmp_path):
    assert create_checkpoint(tmp_path, "sess-1", 1) is None
    assert list_checkpoints(tmp_path) == []
    res = restore_checkpoint(tmp_path, "sess-1", 1)
    assert not res["ok"]
    assert "not a git repository" in res["error"]


def test_create_and_list_checkpoints(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "hello.txt").write_text("v1")

    ref1 = create_checkpoint(tmp_path, "session-a", 1)
    assert ref1 == "refs/openworker/checkpoints/session-a/1"

    (tmp_path / "hello.txt").write_text("v2")
    ref2 = create_checkpoint(tmp_path, "session-a", 2)
    assert ref2 == "refs/openworker/checkpoints/session-a/2"

    ckpts = list_checkpoints(tmp_path, session_id="session-a")
    assert len(ckpts) == 2
    assert ckpts[0]["turn"] == 1
    assert ckpts[1]["turn"] == 2
    assert ckpts[0]["session_id"] == "session-a"


def test_create_and_restore_checkpoint(tmp_path):
    _init_git_repo(tmp_path)

    # Setup tracked file
    (tmp_path / "app.py").write_text("print('original')")
    env = _git_env()
    subprocess.run(
        ["git", "add", "app.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@test.local",
            "commit",
            "-m",
            "add app.py",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=env,
    )

    # Pre-existing untracked file and gitignored file
    (tmp_path / "untracked_pre.txt").write_text("pre-existing untracked")
    (tmp_path / ".gitignore").write_text("*.log\n")
    (tmp_path / "build.log").write_text("log line 1")

    # Capture checkpoint before turn 1
    ref = create_checkpoint(tmp_path, "sess-1", 1)
    assert ref is not None

    # Agent executes turn: modifies app.py, deletes untracked_pre.txt, creates new file
    (tmp_path / "app.py").write_text("print('corrupted by agent')")
    (tmp_path / "untracked_pre.txt").unlink()
    sub = tmp_path / "new_dir"
    sub.mkdir()
    (sub / "generated.py").write_text("bad code")
    (tmp_path / "build.log").write_text("log line 2")

    # Restore checkpoint
    res = restore_checkpoint(tmp_path, "sess-1", 1)
    assert res["ok"]
    assert "new_dir/generated.py" in res["removed_files"]

    # Assertions
    assert (tmp_path / "app.py").read_text() == "print('original')"
    assert (tmp_path / "untracked_pre.txt").read_text() == "pre-existing untracked"
    assert not (sub / "generated.py").exists()
    assert not sub.exists()
    # gitignored file untouched
    assert (tmp_path / "build.log").read_text() == "log line 2"


def test_revert_turn_tool(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "main.py").write_text("def run(): pass")

    create_checkpoint(tmp_path, "session-test", 1)
    (tmp_path / "main.py").write_text("syntax error !!!")

    tools = git_tools(str(tmp_path), session_id="session-test")
    assert len(tools) == 2
    revert_fn = tools[1]
    assert revert_fn.__name__ == "revert_turn"

    # Call revert_turn without argument -> reverts latest turn (turn 1)
    res = revert_fn()
    assert res["ok"]
    assert (tmp_path / "main.py").read_text() == "def run(): pass"

    # Call on nonexistent turn
    err = revert_fn(turn=99)
    assert not err["ok"]
    assert "not found" in err["error"]


class DummyProvider(ProviderClient):
    def __init__(self, responses: list[AssistantTurn]) -> None:
        self.responses = list(responses)

    def complete(self, *, model, messages, tools=None, **settings):
        if self.responses:
            return self.responses.pop(0)
        return AssistantTurn(text="Done")

    def capabilities(self, model):
        from coworker.providers.base import ModelCapabilities

        return ModelCapabilities()


@pytest.mark.asyncio
async def test_engine_automatic_checkpoint_before_writes(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "target.txt").write_text("initial state")

    written_files = []

    def write_file(path: str, content: str) -> str:
        p = tmp_path / path
        p.write_text(content)
        written_files.append(path)
        return f"Wrote {path}"

    write_file.__name__ = "write_file"
    write_file.__aisuite_tool_metadata__ = None

    registry = ToolRegistry()
    registry.register(write_file)

    permissions = PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS)

    # Provider will request write_file
    call = ToolCall(
        id="c1",
        name="write_file",
        arguments={"path": "target.txt", "content": "agent mutated state"},
    )
    provider = DummyProvider(
        [
            AssistantTurn(text="Writing file", tool_calls=[call]),
            AssistantTurn(text="Finished write"),
        ]
    )

    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="mock-model",
        session_id="test-session",
    )

    events = []
    async for event in engine.run("Please write target.txt"):
        events.append(event)

    assert (tmp_path / "target.txt").read_text() == "agent mutated state"

    # A checkpoint should have been created for turn 1
    ckpts = list_checkpoints(tmp_path, session_id="test-session")
    assert len(ckpts) == 1
    assert ckpts[0]["turn"] == 1

    # Reverting via engine.revert_turn restores target.txt
    revert_res = engine.revert_turn()
    assert revert_res["ok"]
    assert (tmp_path / "target.txt").read_text() == "initial state"


@pytest.mark.asyncio
async def test_engine_skips_checkpoint_gracefully_in_non_git_workspace(tmp_path):
    assert not is_git_repo(tmp_path)
    (tmp_path / "file.txt").write_text("initial")

    def write_file(path: str, content: str) -> str:
        (tmp_path / path).write_text(content)
        return "ok"

    write_file.__name__ = "write_file"
    write_file.__aisuite_tool_metadata__ = None

    registry = ToolRegistry()
    registry.register(write_file)

    permissions = PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS)
    call = ToolCall(
        id="c1",
        name="write_file",
        arguments={"path": "file.txt", "content": "updated"},
    )
    provider = DummyProvider(
        [
            AssistantTurn(text="Write", tool_calls=[call]),
            AssistantTurn(text="Done"),
        ]
    )

    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="mock-model",
        session_id="non-git-session",
    )

    # Must complete cleanly without errors
    events = []
    async for event in engine.run("Update file"):
        events.append(event)

    assert (tmp_path / "file.txt").read_text() == "updated"
    assert list_checkpoints(tmp_path) == []


def test_restore_preserves_special_names_and_staged_state(tmp_path):
    _init_git_repo(tmp_path)
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], env=_git_env())
    names = ["with ü.txt", "tab\tname", "line\nname"]
    for name in names:
        (tmp_path / name).write_text("original")
    f = tmp_path / "staged.txt"
    f.write_text("staged")
    git("add", "--", ".")
    f.write_text("unstaged")
    (tmp_path / "untracked.txt").write_text("untracked")
    before_index = git("write-tree")
    assert create_checkpoint(tmp_path, "special", 1)
    f.write_text("agent edit")
    assert restore_checkpoint(tmp_path, "special", 1)["ok"]
    assert git("write-tree") == before_index
    assert f.read_text() == "unstaged"
    for name in names:
        assert (tmp_path / name).read_text() == "original"
    assert b"untracked.txt" in git("ls-files", "--others", "--exclude-standard")


def test_restore_enumeration_failure_changes_nothing(tmp_path, monkeypatch):
    import coworker.tools.git as mod
    _init_git_repo(tmp_path)
    f = tmp_path / "file.txt"
    f.write_text("before")
    assert create_checkpoint(tmp_path, "s", 1)
    f.write_text("after")
    original = mod._run_git
    def fail(root, *args, **kwargs):
        if args[0] == "ls-tree":
            raise OSError("tree unavailable")
        return original(root, *args, **kwargs)
    monkeypatch.setattr(mod, "_run_git", fail)
    assert not restore_checkpoint(tmp_path, "s", 1)["ok"]
    assert f.read_text() == "after"


def test_checkpoint_does_not_overwrite_an_existing_turn(tmp_path):
    _init_git_repo(tmp_path)
    f = tmp_path / "file.txt"
    f.write_text("first")
    assert create_checkpoint(tmp_path, "s", 1)
    f.write_text("second")
    assert create_checkpoint(tmp_path, "s", 1) is None
    assert restore_checkpoint(tmp_path, "s", 1)["ok"]
    assert f.read_text() == "first"


def test_checkpoint_skips_repository_subfolder(tmp_path):
    _init_git_repo(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    assert create_checkpoint(sub, "s", 1) is None
