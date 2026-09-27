"""Workspace tools in a sandbox: same definitions, same results, execution in the runner.

Every operation here runs twice on twin folders: once with the in-process tools (direct
mode) and once with the proxies that send execution to a tool runner. The answers, the
errors and the files left on disk must match, and what the model is shown about each tool
(name, schema, approval metadata) must be identical.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the tool runner needs Unix sockets")

from coworker.agents.base import AgentContext  # noqa: E402  (before catalog: import order)
from coworker import catalog  # noqa: E402, I001
from coworker.roots import RootDir  # noqa: E402
from coworker.sandbox.bundle import build_runner_zipapp  # noqa: E402
from coworker.sandbox.providers.runner_local import RunnerLocalProvider  # noqa: E402
from coworker.sandbox.workspace import RunnerWorkspace  # noqa: E402
from coworker.tools import ToolRegistry  # noqa: E402

CAPABILITIES = ["code_files", "git", "search"]


def _seed(folder: Path) -> None:
    (folder / "src").mkdir(parents=True)
    (folder / "src" / "app.py").write_text("def greet():\n    return 'hello'\n\n# TODO: say more\n")
    (folder / "src" / "util.py").write_text("VALUE = 1\n# TODO: remove\n")
    (folder / "README.md").write_text("# Demo\n\nSome text.\n")
    (folder / "node_modules").mkdir()
    (folder / "node_modules" / "junk.js").write_text("// TODO: never shown\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "first"]):
        subprocess.run(["git", "-C", str(folder), *cmd], check=True, capture_output=True)


def _tree(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): p.read_text(errors="replace")
        for p in sorted(folder.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(folder).parts
    }


@pytest.fixture
def twins(tmp_path):
    """(direct tools, runner tools, direct folder, runner folder), both folders seeded alike."""
    direct_dir, runner_dir = tmp_path / "direct" / "ws", tmp_path / "runner" / "ws"
    for folder in (direct_dir, runner_dir):
        folder.mkdir(parents=True)
        _seed(folder)
    direct_ctx = AgentContext(workspace=direct_dir)
    provider = RunnerLocalProvider(cwd=runner_dir, runner_path=build_runner_zipapp(tmp_path / "dist"))
    sandbox = RunnerWorkspace(provider, cwd=runner_dir)
    runner_ctx = AgentContext(workspace=runner_dir, executor=sandbox.executor, sandbox=sandbox)
    direct = {t.__name__: t for t in catalog.expand(CAPABILITIES, direct_ctx)}
    through_runner = {t.__name__: t for t in catalog.expand(CAPABILITIES, runner_ctx)}
    yield direct, through_runner, direct_dir, runner_dir
    sandbox.close()


def _both(twins, name, *args, **kwargs):
    """Run one tool in both modes. Returns the two outcomes with each folder's own path
    replaced by a placeholder, so they can be compared."""
    direct, through_runner, direct_dir, runner_dir = twins
    outcomes = []
    for tools, folder in ((direct, direct_dir), (through_runner, runner_dir)):
        try:
            outcome = ("ok", tools[name](*args, **kwargs))
        except Exception as exc:
            outcome = ("raised", type(exc).__name__, str(exc))
        outcomes.append(repr(outcome).replace(str(folder.resolve()), "<ws>").replace(str(folder), "<ws>"))
    return outcomes


def test_the_model_sees_the_same_tools_either_way(twins):
    direct, through_runner, _, _ = twins
    assert sorted(direct) == sorted(through_runner)
    a, b = ToolRegistry(), ToolRegistry()
    a.register_all(list(direct.values()))
    b.register_all(list(through_runner.values()))
    for name in direct:
        assert a.get(name).schema == b.get(name).schema, name
        assert vars(a.get(name).metadata) == vars(b.get(name).metadata), name


@pytest.mark.parametrize(
    "name, args, kwargs",
    [
        ("read_file", ("src/app.py",), {}),
        ("read_file", ("src/app.py",), {"start_line": 2, "max_lines": 1}),
        ("read_file", ("missing.py",), {}),
        ("read_file", ("../outside.txt",), {}),
        ("list_files", (), {}),
        ("list_files", ("src",), {"pattern": "*.py", "recursive": False}),
        ("grep", ("TODO",), {}),
        ("grep", ("TODO", "src"), {"glob": "util.*", "max_results": 5}),
        ("grep", ("(unclosed",), {}),
        ("grep", ("x", "../.."), {}),
        ("git_status", (), {}),
        ("git_diff", (), {}),
        ("git_log", (), {"max_count": 5}),
        ("git_log", ("src/app.py",), {}),
    ],
)
def test_reading_tools_answer_alike(twins, name, args, kwargs):
    direct_answer, runner_answer = _both(twins, name, *args, **kwargs)
    if name == "git_log":  # commit hashes differ between the twin repositories by design
        import re

        direct_answer, runner_answer = (re.sub(r"'hash': '[0-9a-f]+'", "'hash': '<h>'", x) for x in (direct_answer, runner_answer))
    assert direct_answer == runner_answer


def test_editing_tools_answer_alike_and_leave_the_same_files(twins):
    _, _, direct_dir, runner_dir = twins
    steps = [
        ("write_file", ("notes/new.md", "one\ntwo\nthree\n"), {}),
        ("write_file", ("notes/new.md", "again\n"), {"overwrite": False}),  # refuses
        ("replace_in_file", ("src/app.py", "'hello'", "'hi'"), {}),
        ("replace_in_file", ("src/app.py", "not-there", "x"), {}),  # raises
        ("replace_in_file", ("src/util.py", "VALUE", "NUMBER"), {"expected_replacements": 2}),  # raises
        (
            "apply_unified_diff",
            ("--- a/README.md\n+++ b/README.md\n@@ -1,3 +1,3 @@\n # Demo\n \n-Some text.\n+Some better text.\n",),
            {},
        ),
        (
            "apply_patch",
            ("*** Begin Patch\n*** Add File: notes/added.txt\n+made by a patch\n*** Delete File: src/util.py\n*** End Patch\n",),
            {},
        ),
        ("write_file", ("../escape.txt", "no"), {}),  # outside the session's folders
        ("git_status", (), {}),
        ("git_diff", ("README.md",), {}),
    ]
    for name, args, kwargs in steps:
        direct_answer, runner_answer = _both(twins, name, *args, **kwargs)
        assert direct_answer == runner_answer, name
    assert _tree(direct_dir) == _tree(runner_dir)
    assert "Some better text." in (runner_dir / "README.md").read_text()
    assert not (runner_dir / "src" / "util.py").exists()
    assert not (runner_dir.parent / "escape.txt").exists()


def test_a_folder_granted_later_is_seen_at_once_and_read_only_is_kept(tmp_path):
    """The session's roots list is live: a folder added while the session runs must work
    on the next call, and a read-only folder must refuse writes, exactly as in direct mode."""
    primary, extra = tmp_path / "scratch", tmp_path / "granted"
    primary.mkdir()
    extra.mkdir()
    (extra / "ref.txt").write_text("reference\n")
    roots = [RootDir(path=primary, writable=True)]
    provider = RunnerLocalProvider(cwd=primary, runner_path=build_runner_zipapp(tmp_path / "dist"))
    sandbox = RunnerWorkspace(provider, cwd=primary)
    try:
        ctx = AgentContext(workspace=primary, executor=sandbox.executor, roots=roots, sandbox=sandbox)
        tools = {t.__name__: t for t in catalog.expand(["files"], ctx)}
        assert "error" in tools["read_file"](str(extra / "ref.txt"))  # not granted yet
        roots.append(RootDir(path=extra, writable=False))
        assert "reference" in tools["read_file"](str(extra / "ref.txt"))["content"]
        with pytest.raises(PermissionError):
            tools["write_file"](str(extra / "new.txt"), "nope")
        tools["write_file"]("mine.txt", "fine")
        assert (primary / "mine.txt").read_text() == "fine"
    finally:
        sandbox.close()


def test_direct_mode_uses_the_tools_untouched(tmp_path):
    (tmp_path / "f.txt").write_text("x\n")
    plain = catalog.expand(CAPABILITIES, AgentContext(workspace=tmp_path))
    assert not any(hasattr(t, "__wrapped__") for t in plain)  # no proxy in the way


def test_the_packed_runner_carries_the_toolkits_and_needs_no_site_packages(tmp_path):
    zipapp = build_runner_zipapp(tmp_path)
    with zipfile.ZipFile(zipapp) as zf:
        names = set(zf.namelist())
    assert {"owrunner/aisuite_toolkits/files.py", "owrunner/aisuite_toolkits/git.py", "owrunner/agents.py"} <= names
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from owrunner import toolcalls;"
        "assert toolcalls.FileToolkit.__module__.startswith('owrunner.'), toolcalls.FileToolkit.__module__;"
        "print(toolcalls.call('list_files', {}, workspace=sys.argv[2]))"
    )
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "a.txt").write_text("a")
    done = subprocess.run([sys.executable, "-S", "-c", probe, str(zipapp), str(tmp_path / "ws")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "['a.txt']"
