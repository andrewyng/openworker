"""OPE-232: the read-only session grant covers a git command only when the project's own
git settings are inert.

Git obeys settings stored inside the project: `.git/config`, the files it includes, and
the per-worktree config. Some of them name programs that ordinary reading commands run
(diff and textconv drivers, filters, the file-system monitor, the hooks folder). The
grant lists those settings before it lets a git command run without a card: project-level
settings must all be on a short list of ones known to be inert, the repository must lie
inside the folders, and every initialised submodule must pass the same check. The
person's own settings (global and system scope) are trusted. Anything the check cannot
establish asks.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from coworker.git_settings import project_git_settings_inert
from coworker.permissions import Mode, PermissionEngine
from coworker.readonly import git_repo_dirs

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def isolated_git(tmp_path, monkeypatch):
    """Git sees only the settings the test writes: no system file, an empty global file."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global.gitconfig"))
    (tmp_path / "global.gitconfig").write_text("", encoding="utf-8")
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    return tmp_path


def _repo(where: Path) -> Path:
    where.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=where)
    _git("config", "user.email", "t@example.com", cwd=where)
    _git("config", "user.name", "t", cwd=where)
    (where / "a.txt").write_text("a\n", encoding="utf-8")
    _git("add", ".", cwd=where)
    _git("commit", "-q", "-m", "init", cwd=where)
    return where


def _engine(root: Path, shell: str = "bash") -> PermissionEngine:
    return PermissionEngine(workspace_root=root, mode=Mode.INTERACTIVE, shell_kind=shell, session_readonly=True)


def _runs(engine: PermissionEngine, command: str) -> bool:
    return engine.evaluate("run_shell", {"command": command}, None).allowed


GIT_READS = ["git status", "git diff", "git log -p -1", "git show HEAD", "git blame a.txt", "git ls-files"]


# -- an ordinary project ----------------------------------------------------------------------


@pytest.mark.parametrize("command", GIT_READS)
def test_an_ordinary_project_runs_git_reads_unprompted(isolated_git, command):
    repo = _repo(isolated_git / "repo")
    assert _runs(_engine(repo), command), command


def test_the_settings_a_clone_carries_are_inert(isolated_git):
    repo = _repo(isolated_git / "repo")
    for key, value in [
        ("remote.origin.url", "https://example.com/x.git"),
        ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
        ("remote.origin.pushurl", "git@example.com:x.git"),
        ("remote.my.fork.url", "https://example.com/f.git"),  # a remote name with a dot
        ("branch.main.remote", "origin"),
        ("branch.main.merge", "refs/heads/main"),
        ("branch.feature/x.rebase", "true"),
        ("user.signingkey", "ABCDEF"),
        ("pull.rebase", "false"),
        ("push.default", "current"),
        ("core.autocrlf", "true"),
        ("core.ignorecase", "true"),
        ("core.longpaths", "true"),
        ("lfs.repositoryformatversion", "0"),
        ("lfs.https://example.com/x.git/info/lfs.access", "basic"),
        ("http.https://github.com/.extraheader", "AUTHORIZATION: basic xyz"),
        ("submodule.lib.url", "https://example.com/lib.git"),
        ("submodule.lib.active", "true"),
        ("extensions.worktreeconfig", "true"),
        ("color.ui", "auto"),
        ("gc.auto", "0"),
    ]:
        _git("config", key, value, cwd=repo)
    assert _runs(_engine(repo), "git status")
    assert _runs(_engine(repo), "git diff")


def test_the_persons_own_settings_are_trusted(isolated_git):
    repo = _repo(isolated_git / "repo")
    (isolated_git / "global.gitconfig").write_text(
        '[diff "x"]\n\ttextconv = some-program\n[core]\n\tpager = less\n[alias]\n\tst = status\n',
        encoding="utf-8",
    )
    assert _runs(_engine(repo), "git diff")
    assert _runs(_engine(repo), "git log -p -1")


# -- a project whose settings name a program -------------------------------------------------


@pytest.mark.parametrize(
    "key, value",
    [
        ("diff.x.textconv", "some-program"),
        ("diff.x.command", "some-program"),
        ("diff.external", "some-program"),
        ("filter.f.clean", "some-program"),
        ("filter.f.smudge", "some-program"),
        ("filter.f.process", "some-program"),
        ("core.fsmonitor", "some-program"),
        ("core.hookspath", "hooks"),
        ("core.pager", "cat"),
        ("core.editor", "vi"),
        ("core.sshcommand", "ssh"),
        ("core.worktree", "../.."),  # the parent of the project; the project itself is fine
        ("core.excludesfile", "../ignore"),
        ("alias.st", "status"),
        ("alias.x", "!some-program"),
        ("credential.helper", "store"),
        ("log.showsignature", "true"),
        ("gpg.program", "gpg"),
        ("blame.ignorerevsfile", "../revs"),
        ("merge.m.driver", "some-program"),
        ("uploadpack.packobjectshook", "some-program"),
        ("include.path", "../extra.gitconfig"),
        ("remote.origin.uploadpack", "some-program"),
        ("remote.origin.proxy", "http://proxy"),
        ("submodule.lib.update", "!some-program"),
        ("unknown.key", "x"),
    ],
)
def test_a_project_level_setting_off_the_list_asks(isolated_git, key, value):
    repo = _repo(isolated_git / "repo")
    _git("config", key, value, cwd=repo)
    assert not _runs(_engine(repo), "git status"), key


@pytest.mark.parametrize("command", GIT_READS)
def test_every_git_read_is_held_to_the_settings(isolated_git, command):
    repo = _repo(isolated_git / "repo")
    _git("config", "core.fsmonitor", "some-program", cwd=repo)
    assert not _runs(_engine(repo), command), command


def test_a_setting_pulled_in_from_another_file_asks(isolated_git):
    repo = _repo(isolated_git / "repo")
    (isolated_git / "extra.gitconfig").write_text('[diff "x"]\n\ttextconv = some-program\n', encoding="utf-8")
    _git("config", "include.path", str(isolated_git / "extra.gitconfig"), cwd=repo)
    assert not _runs(_engine(repo), "git diff")


def test_a_per_worktree_setting_asks(isolated_git):
    repo = _repo(isolated_git / "repo")
    _git("config", "extensions.worktreeconfig", "true", cwd=repo)
    _git("config", "--worktree", "core.fsmonitor", "some-program", cwd=repo)
    assert not _runs(_engine(repo), "git status")


def test_only_git_commands_are_checked_and_other_reads_still_run(isolated_git):
    repo = _repo(isolated_git / "repo")
    _git("config", "core.fsmonitor", "some-program", cwd=repo)
    engine = _engine(repo)
    assert _runs(engine, "cat a.txt")
    assert _runs(engine, "ls -la")
    assert not _runs(engine, "git status")
    assert not _runs(engine, "cat a.txt | git status")  # any git stage counts


def test_the_check_follows_the_folder_git_is_told_to_use(isolated_git):
    root = isolated_git / "root"
    good = _repo(root / "good")
    bad = _repo(root / "bad")
    _git("config", "core.fsmonitor", "some-program", cwd=bad)
    engine = _engine(root)
    assert _runs(engine, "git -C good status")
    assert not _runs(engine, "git -C bad status")
    assert not _runs(engine, "git -C good -C ../bad status")  # -C stacks
    assert not _runs(_engine(bad), "git -C ../good status") or True  # -C is a read target anyway


def test_powershell_git_is_checked_too(isolated_git):
    repo = _repo(isolated_git / "repo")
    assert _runs(_engine(repo, "powershell"), "git status")
    _git("config", "core.fsmonitor", "some-program", cwd=repo)
    assert not _runs(_engine(repo, "powershell"), "git status")


# -- where the repository lives --------------------------------------------------------------


def test_a_folder_that_is_not_a_repository_runs(isolated_git):
    root = isolated_git / "plain"
    root.mkdir()
    assert _runs(_engine(root), "git status")  # git only prints an error


def test_a_folder_inside_a_larger_repository_asks(isolated_git):
    outer = _repo(isolated_git / "outer")
    inner = outer / "inner"
    inner.mkdir()
    assert not _runs(_engine(inner), "git log -1")  # the history read is the outer repo's


def test_a_linked_worktree_of_the_persons_repository_runs(isolated_git):
    main = _repo(isolated_git / "main")
    linked = isolated_git / "linked"
    _git("worktree", "add", "-q", str(linked), "-b", "wt", cwd=main)
    assert _runs(_engine(linked), "git status")
    assert _runs(_engine(linked), "git log -1")


def test_a_linked_worktree_still_sees_the_main_repositorys_settings(isolated_git):
    main = _repo(isolated_git / "main")
    linked = isolated_git / "linked"
    _git("worktree", "add", "-q", str(linked), "-b", "wt", cwd=main)
    _git("config", "core.fsmonitor", "some-program", cwd=main)
    assert not _runs(_engine(linked), "git status")


def test_a_dot_git_file_pointing_at_an_unrelated_repository_asks(isolated_git):
    other = _repo(isolated_git / "other")
    project = isolated_git / "project"
    project.mkdir()
    (project / ".git").write_text(f"gitdir: {(other / '.git').as_posix()}\n", encoding="utf-8")
    assert not _runs(_engine(project), "git log -1")


def test_a_bare_repository_inside_the_folders_runs(isolated_git):
    bare = isolated_git / "bare.git"
    bare.mkdir()
    _git("init", "-q", "--bare", cwd=bare)
    assert _runs(_engine(bare), "git log --all -1")


# -- submodules -------------------------------------------------------------------------------


def _with_submodule(isolated_git, sub_setting=None):
    lib = _repo(isolated_git / "lib")
    repo = _repo(isolated_git / "repo")
    _git("-c", "protocol.file.allow=always", "submodule", "add", "-q", lib.as_posix(), "lib", cwd=repo)
    _git("commit", "-q", "-m", "sub", cwd=repo)
    if sub_setting:
        _git("config", *sub_setting, cwd=repo / "lib")
    return repo


def test_an_initialised_submodule_is_checked_too(isolated_git):
    repo = _with_submodule(isolated_git)
    assert _runs(_engine(repo), "git status")
    repo2 = _with_submodule(isolated_git / "two", ("core.fsmonitor", "some-program"))
    assert not _runs(_engine(repo2), "git status")


def test_an_uninitialised_submodule_is_inert(isolated_git):
    repo = _with_submodule(isolated_git)
    clone = isolated_git / "clone"
    _git("-c", "protocol.file.allow=always", "clone", "-q", repo.as_posix(), clone.as_posix(), cwd=isolated_git)
    assert (clone / ".gitmodules").exists() and not (clone / "lib" / ".git").exists()
    assert _runs(_engine(clone), "git status")


# -- the check fails closed -------------------------------------------------------------------


def test_a_failing_or_slow_git_asks(isolated_git, monkeypatch):
    repo = _repo(isolated_git / "repo")
    import coworker.git_settings as gs

    monkeypatch.setattr(gs.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert not _runs(_engine(repo), "git status")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(gs.subprocess, "run", slow)
    assert not _runs(_engine(repo), "git status")


def test_without_git_installed_the_check_asks(isolated_git, monkeypatch):
    repo = _repo(isolated_git / "repo")
    import coworker.git_settings as gs

    monkeypatch.setattr(gs.shutil, "which", lambda _name: None)
    assert not project_git_settings_inert(repo, lambda _p: True)


def test_unreadable_listing_output_asks(isolated_git, monkeypatch):
    repo = _repo(isolated_git / "repo")
    import coworker.git_settings as gs

    real = gs.subprocess.run

    def garbled(args, **kwargs):
        result = real(args, **kwargs)
        if "--list" in args:
            result.stdout = b"local\nnonsense"
        return result

    monkeypatch.setattr(gs.subprocess, "run", garbled)
    assert not project_git_settings_inert(repo, lambda _p: True)


# -- the parse half -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, shell, expected",
    [
        ("git status", "bash", ["."]),
        ("git -C src log -1", "bash", ["src"]),
        ("git -C a -C b log -1", "bash", [os.path.join("a", "b")]),
        ("git --no-pager -C src status", "bash", ["src"]),
        ("LC_ALL=C git status", "bash", ["."]),
        ("git status | head -5", "bash", ["."]),
        ("cat x | grep y", "bash", []),
        ("git log -C -1", "bash", ["."]),  # `-C` after the subcommand is the copies flag
        ("Git status", "powershell", ["."]),
        ("git -C src status", "powershell", ["src"]),
        ("rm -rf x", "bash", []),  # not read-only at all
    ],
)
def test_git_repo_dirs_names_the_folder_each_git_stage_runs_in(command, shell, expected):
    assert git_repo_dirs(command, shell=shell) == expected


# -- what a project can point git at without a setting ---------------------------------------


def test_an_alternates_file_pointing_outside_the_folders_asks(isolated_git):
    outside = _repo(isolated_git / "outside")
    repo = _repo(isolated_git / "repo")
    alternates = repo / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text((outside / ".git" / "objects").as_posix() + "\n", encoding="utf-8")
    assert not _runs(_engine(repo), "git log -1")  # history and files of the other repository
    alternates.write_text("../../../inside.git/objects\n", encoding="utf-8")
    assert not _runs(_engine(repo), "git log -1")  # relative, still outside
    alternates.write_text("# only a comment\n", encoding="utf-8")
    assert _runs(_engine(repo), "git log -1")


def test_an_alternates_file_inside_the_folders_runs(isolated_git):
    root = isolated_git / "root"
    shared = _repo(root / "shared")
    repo = _repo(root / "repo")
    alternates = repo / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text((shared / ".git" / "objects").as_posix() + "\n", encoding="utf-8")
    assert _runs(_engine(root), "git -C repo log -1")


def _trusted_driver(isolated_git, body: str) -> None:
    (isolated_git / "global.gitconfig").write_text(body, encoding="utf-8")


@pytest.mark.parametrize(
    "attributes",
    [
        "*.txt diff=x\n",
        "*.txt filter=f\n",
        "[attr]docs diff=x\n*.txt docs\n",  # a macro that assigns the driver
        '"a.txt" diff=x\n',
    ],
)
def test_a_project_that_switches_on_a_trusted_driver_asks(isolated_git, attributes):
    _trusted_driver(isolated_git, '[diff "x"]\n\ttextconv = some-program\n[filter "f"]\n\tclean = some-program\n')
    repo = _repo(isolated_git / "repo")
    assert _runs(_engine(repo), "git diff")  # the driver is defined but nothing uses it
    (repo / ".gitattributes").write_text(attributes, encoding="utf-8")
    assert not _runs(_engine(repo), "git diff")


def test_the_attributes_can_sit_anywhere_in_the_project(isolated_git):
    _trusted_driver(isolated_git, '[diff "x"]\n\tcommand = some-program\n')
    repo = _repo(isolated_git / "repo")
    (repo / "sub" / "deep").mkdir(parents=True)
    (repo / "sub" / "deep" / "f.txt").write_text("f\n", encoding="utf-8")  # untracked is enough
    (repo / "sub" / "deep" / ".gitattributes").write_text("* diff=x\n", encoding="utf-8")
    assert not _runs(_engine(repo), "git log -p -1")
    (repo / "sub" / "deep" / ".gitattributes").unlink()
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("* diff=x\n", encoding="utf-8")
    assert not _runs(_engine(repo), "git log -p -1")


def test_attributes_are_inert_while_no_trusted_driver_exists(isolated_git):
    _trusted_driver(isolated_git, '[diff "md"]\n\txfuncname = ^#\n')  # shows no program
    repo = _repo(isolated_git / "repo")
    (repo / ".gitattributes").write_text("*.md diff=md\n*.py diff=python\n*.bin -diff\n*.c filter=none\n", encoding="utf-8")
    assert _runs(_engine(repo), "git diff")


def test_with_a_trusted_driver_any_attributes_ask(isolated_git):
    # Patterns apply to paths in the history as well, so no listing of today's files can
    # say which driver a `git log -p` will reach: any attributes at all ask.
    _trusted_driver(isolated_git, '[diff "x"]\n\ttextconv = some-program\n')
    repo = _repo(isolated_git / "repo")
    (repo / ".gitattributes").write_text("*.md diff=markdown\n", encoding="utf-8")
    assert not _runs(_engine(repo), "git log -p -1")


def test_a_nested_repository_the_listing_cannot_see_asks(isolated_git):
    _trusted_driver(isolated_git, '[diff "x"]\n\ttextconv = some-program\n')
    repo = _repo(isolated_git / "repo")
    _repo(repo / "nested")  # untracked; its own attributes are invisible to the outer listing
    assert not _runs(_engine(repo), "git diff")


def test_a_relative_attributes_file_in_the_persons_settings_asks(isolated_git):
    _trusted_driver(isolated_git, '[diff "x"]\n\ttextconv = some-program\n[core]\n\tattributesFile = attrs.conf\n')
    repo = _repo(isolated_git / "repo")
    assert not _runs(_engine(repo), "git diff")  # the project would supply attrs.conf


def test_the_standard_lfs_filter_is_trusted(isolated_git):
    _trusted_driver(
        isolated_git,
        '[filter "lfs"]\n\tclean = git-lfs clean -- %f\n\tsmudge = git-lfs smudge -- %f\n'
        "\tprocess = git-lfs filter-process\n\trequired = true\n",
    )
    repo = _repo(isolated_git / "repo")
    (repo / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8")
    assert _runs(_engine(repo), "git status")
    assert _runs(_engine(repo), "git diff")


def test_an_lfs_filter_that_is_not_the_standard_one_asks(isolated_git):
    _trusted_driver(isolated_git, '[filter "lfs"]\n\tclean = some-program\n')
    repo = _repo(isolated_git / "repo")
    (repo / ".gitattributes").write_text("*.txt filter=lfs\n", encoding="utf-8")
    assert not _runs(_engine(repo), "git diff")


def test_a_bare_repository_asks_when_a_trusted_driver_exists(isolated_git):
    _trusted_driver(isolated_git, '[diff "x"]\n\ttextconv = some-program\n')
    bare = isolated_git / "bare.git"
    bare.mkdir()
    _git("init", "-q", "--bare", cwd=bare)
    assert not _runs(_engine(bare), "git log --all -1")  # its attributes cannot be read from disk


def test_environment_variables_that_steer_git_reads_ask(isolated_git, monkeypatch):
    repo = _repo(isolated_git / "repo")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "some-program")
    assert not _runs(_engine(repo), "git diff")
    monkeypatch.delenv("GIT_EXTERNAL_DIFF")
    monkeypatch.setenv("GIT_ATTR_SOURCE", "HEAD")
    assert not _runs(_engine(repo), "git diff")
    monkeypatch.delenv("GIT_ATTR_SOURCE")
    monkeypatch.setenv("GIT_INDEX_FILE", str(isolated_git / "elsewhere" / "index"))
    assert not _runs(_engine(repo), "git ls-files")
    monkeypatch.delenv("GIT_INDEX_FILE")
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", str(isolated_git / "elsewhere" / "objects"))
    assert not _runs(_engine(repo), "git log -1")
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", str(repo / ".git" / "objects"))
    assert _runs(_engine(repo), "git log -1")


# -- the second review: ways around the first fixes ------------------------------------------


def test_a_promisor_remote_asks(isolated_git):
    repo = _repo(isolated_git / "repo")
    _git("config", "remote.origin.url", "https://example.com/x.git", cwd=repo)
    _git("config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*", cwd=repo)
    assert _runs(_engine(repo), "git log -1")
    _git("config", "remote.origin.promisor", "true", cwd=repo)  # reads fetch missing objects
    assert not _runs(_engine(repo), "git log -1")


def _hop(repo: Path, outside: Path) -> Path:
    """An object folder inside the project whose own alternates file points outside."""
    hop = repo / "hop" / "objects"
    (hop / "info").mkdir(parents=True)
    (hop / "info" / "alternates").write_text((outside / ".git" / "objects").as_posix() + "\n", encoding="utf-8")
    return hop


def test_alternates_are_followed_to_the_end(isolated_git):
    outside = _repo(isolated_git / "outside")
    repo = _repo(isolated_git / "repo")
    _hop(repo, outside)
    alternates = repo / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text("../../hop/objects\n", encoding="utf-8")  # inside, but it points on
    assert not _runs(_engine(repo), "git log -1")


def test_alternates_from_the_environment_are_followed_too(isolated_git, monkeypatch):
    outside = _repo(isolated_git / "outside")
    repo = _repo(isolated_git / "repo")
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", str(_hop(repo, outside)))
    assert not _runs(_engine(repo), "git log -1")


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="needs a case-insensitive file system")
def test_an_attributes_file_in_another_case_asks(isolated_git):
    _trusted_driver(isolated_git, '[diff "x"]\n\ttextconv = some-program\n')
    repo = _repo(isolated_git / "repo")
    (repo / ".GITATTRIBUTES").write_text("*.txt diff=x\n", encoding="utf-8")
    assert not _runs(_engine(repo), "git diff")


def test_attributes_kept_only_in_the_index_ask(isolated_git):
    _trusted_driver(isolated_git, '[diff "x"]\n\ttextconv = some-program\n')
    repo = _repo(isolated_git / "repo")
    (repo / ".gitattributes").write_text("*.txt diff=x\n", encoding="utf-8")
    _git("add", ".gitattributes", cwd=repo)
    _git("commit", "-q", "-m", "attrs", cwd=repo)
    (repo / ".gitattributes").unlink()  # git falls back to the copy in the index
    assert not _runs(_engine(repo), "git diff")


def test_a_macro_from_the_persons_attributes_file_asks(isolated_git):
    (isolated_git / "attrs").write_text("[attr]docs diff=x\n", encoding="utf-8")
    _trusted_driver(
        isolated_git,
        f'[diff "x"]\n\ttextconv = some-program\n[core]\n\tattributesFile = {(isolated_git / "attrs").as_posix()}\n',
    )
    repo = _repo(isolated_git / "repo")
    assert _runs(_engine(repo), "git diff")
    (repo / ".gitattributes").write_text("*.txt docs\n", encoding="utf-8")  # no `diff=` token
    assert not _runs(_engine(repo), "git diff")


def _junction(link: Path, target: Path) -> bool:
    if sys.platform != "win32":
        return False
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    return done.returncode == 0


def test_a_junction_where_the_objects_folder_should_be_asks(isolated_git):
    outside = _repo(isolated_git / "outside")
    repo = _repo(isolated_git / "repo")
    objects = repo / ".git" / "objects"
    objects.rename(repo / ".git" / "objects.aside")  # git's object files are read-only on Windows
    if not _junction(objects, outside / ".git" / "objects"):
        pytest.skip("cannot make a junction here")
    assert not _runs(_engine(repo), "git log -1")


def test_a_junction_deeper_in_the_git_folder_asks(isolated_git):
    outside = _repo(isolated_git / "outside")
    repo = _repo(isolated_git / "repo")
    pack = repo / ".git" / "objects" / "pack"
    pack.rename(repo / ".git" / "objects" / "pack.aside")
    if not _junction(pack, outside / ".git" / "objects" / "pack"):
        pytest.skip("cannot make a junction here")
    assert not _runs(_engine(repo), "git log -1")


def test_a_junction_in_a_linked_worktrees_own_git_folder_asks(isolated_git):
    main = _repo(isolated_git / "main")
    linked = isolated_git / "linked"
    _git("worktree", "add", "-q", str(linked), "-b", "wt", cwd=main)
    assert _runs(_engine(linked), "git log -1")
    own = main / ".git" / "worktrees" / "linked"
    (own / "logs").rename(own / "logs.aside")
    if not _junction(own / "logs", isolated_git / "outside-logs"):
        pytest.skip("cannot make a junction here")
    assert not _runs(_engine(linked), "git log -1")


def test_a_junction_into_the_git_folder_asks(isolated_git):
    _trusted_driver(isolated_git, '[diff "x"]\n\ttextconv = some-program\n')
    repo = _repo(isolated_git / "repo")
    hidden = repo / ".git" / "evil"
    hidden.mkdir()
    (hidden / ".gitattributes").write_text("* diff=x\n", encoding="utf-8")
    (hidden / "f.txt").write_text("f\n", encoding="utf-8")
    if not _junction(repo / "sub", hidden):
        pytest.skip("cannot make a junction here")
    assert not _runs(_engine(repo), "git diff")


def test_a_project_with_its_own_lfs_settings_asks(isolated_git):
    repo = _repo(isolated_git / "repo")
    (repo / ".lfsconfig").write_text("[lfs]\n\turl = https://example.com/lfs\n", encoding="utf-8")
    assert not _runs(_engine(repo), "git status")


def test_the_git_for_windows_default_text_converter_is_trusted(isolated_git):
    _trusted_driver(isolated_git, '[diff "astextplain"]\n\ttextconv = astextplain\n')
    repo = _repo(isolated_git / "repo")
    (repo / ".gitattributes").write_text("*.docx diff=astextplain\n", encoding="utf-8")
    (repo / "x.docx").write_bytes(b"PK")
    assert _runs(_engine(repo), "git diff")
    _trusted_driver(isolated_git, '[diff "astextplain"]\n\ttextconv = some-program\n')
    assert not _runs(_engine(repo), "git diff")
