"""In-project files that run later, and OpenWorker's own settings, are matched regardless
of letter case where the file system ignores case (OPE-231).

Windows and macOS (by default) treat `.GIT/hooks` and `.git/hooks` as the same folder,
but `Path.resolve()` only restores the on-disk case for parts that already exist. A
target that doesn't exist yet keeps whatever case the agent typed, so the string match
has to fold case itself."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from coworker import permissions
from coworker.permissions import Mode, PermissionEngine, _is_protected_in_project

_MARKER_TARGETS = [
    ".git/hooks/pre-commit",
    ".github/workflows/ci.yml",
    ".gitlab-ci.yml",
    ".vscode/tasks.json",
    ".coworker/config.toml",
    # OPE-231: run by a build backend, env tool, git or a hook manager without being named
    "setup.py",
    ".envrc",
    ".gitattributes",
    ".pre-commit-config.yaml",
    "tox.ini",
    ".devcontainer/devcontainer.json",
    ".husky/pre-push",
    ".circleci/config.yml",
    # read into every session's system prompt (coworker/project.py)
    "AGENTS.md",
]


def _variants(path: str) -> list[str]:
    return [path, path.upper(), "".join(c.upper() if i % 2 else c for i, c in enumerate(path))]


def _gate(tmp_path: Path, mode: Mode = Mode.AUTO_APPROVE) -> PermissionEngine:
    return PermissionEngine(workspace_root=tmp_path, mode=mode)


@pytest.fixture
def case_insensitive(monkeypatch):
    monkeypatch.setattr(permissions, "_case_insensitive_fs", lambda: True)


@pytest.fixture
def case_sensitive(monkeypatch):
    monkeypatch.setattr(permissions, "_case_insensitive_fs", lambda: False)


@pytest.mark.parametrize("target", _MARKER_TARGETS)
def test_every_protected_target_is_human_only(tmp_path, target):
    d = _gate(tmp_path).evaluate("write_file", {"path": target, "content": "x"})
    assert not d.allowed and d.needs_user and d.human_only


@pytest.mark.parametrize("target", _MARKER_TARGETS)
def test_any_letter_case_is_protected_on_case_insensitive_file_systems(tmp_path, case_insensitive, target):
    # The targets don't exist on disk, which is exactly when resolve() can't fix the case.
    for variant in _variants(target):
        d = _gate(tmp_path).evaluate("write_file", {"path": variant, "content": "x"})
        assert d.human_only, variant


def test_existing_protected_folder_with_different_case_is_protected(tmp_path, case_insensitive):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    d = _gate(tmp_path).evaluate("write_file", {"path": ".GIT/Hooks/pre-commit", "content": "x"})
    assert d.human_only


def test_case_sensitive_file_systems_keep_exact_matching(tmp_path, case_sensitive):
    # On Linux `.GIT/hooks` is a different folder that git never runs, so it is an
    # ordinary write: no extra card.
    d = _gate(tmp_path).evaluate("write_file", {"path": ".GIT/hooks/pre-commit", "content": "x"})
    assert not d.human_only
    d = _gate(tmp_path).evaluate("write_file", {"path": ".git/hooks/pre-commit", "content": "x"})
    assert d.human_only


@pytest.mark.parametrize(
    "path",
    ["src/package.json.bak", "docs/setup.py.txt", "notes/my-pyproject.toml", "README.md", "src/app.py"],
)
def test_lookalike_names_are_ordinary_writes(tmp_path, case_insensitive, path):
    d = _gate(tmp_path).evaluate("write_file", {"path": path, "content": "x"})
    assert not d.human_only


@pytest.mark.parametrize(
    "path", ["package.json", "packages/web/package.json", "pyproject.toml", "setup.cfg"]
)
def test_package_manifests_stay_with_the_reviewer(tmp_path, case_insensitive, path):
    # Users edit these constantly (versions, dependencies), and the reviewer sees the
    # content, so a version bump the user asked for runs without a card while a new
    # install script can still be caught. Not human-only by design (OPE-231).
    d = _gate(tmp_path).evaluate("replace_in_file", {"path": path, "old": "1.4.1", "new": "1.4.2"})
    assert not d.allowed and d.needs_user and not d.human_only


def test_nested_setup_py_is_protected(tmp_path):
    d = _gate(tmp_path).evaluate("write_file", {"path": "packages/core/setup.py", "content": "x"})
    assert d.human_only


def test_patch_touching_a_protected_file_in_another_case_is_protected(tmp_path, case_insensitive):
    patch = "*** Begin Patch\n*** Add File: .Husky/pre-commit\n+echo hi\n*** End Patch"
    d = _gate(tmp_path).evaluate("apply_patch", {"patch": patch})
    assert d.human_only


@pytest.mark.parametrize("mode", [Mode.INTERACTIVE, Mode.AUTO_APPROVE, Mode.BYPASS_APPROVALS])
def test_protection_holds_in_every_asking_mode(tmp_path, case_insensitive, mode):
    d = _gate(tmp_path, mode).evaluate("write_file", {"path": ".ENVRC", "content": "x"})
    assert not d.allowed and d.human_only


def test_dangerous_mode_still_clears_it_and_says_which_floor(tmp_path, case_insensitive):
    d = _gate(tmp_path, Mode.DANGEROUSLY_BYPASS_APPROVALS).evaluate(
        "write_file", {"path": ".GITHUB/workflows/ci.yml", "content": "x"}
    )
    assert d.allowed and "runs automatically later" in d.reason


def test_matcher_folds_case_only_when_asked(case_insensitive):
    assert _is_protected_in_project(Path("/w/.GITLAB-CI.YML"))
    assert _is_protected_in_project(Path("/w/.CoWorker/policy.toml"))


def test_matcher_is_exact_on_case_sensitive_systems(case_sensitive):
    assert not _is_protected_in_project(Path("/w/.GITLAB-CI.YML"))
    assert _is_protected_in_project(Path("/w/.gitlab-ci.yml"))


def test_own_settings_write_is_refused_in_any_case(tmp_path, monkeypatch, case_insensitive):
    # OpenWorker's own settings live outside the project; when a root does contain them,
    # a write must be refused whatever case the path is written in.
    state = tmp_path / "state"
    state.mkdir()
    settings = state / "config.toml"  # does not exist yet
    monkeypatch.setattr(permissions, "protected_paths", lambda: [settings])
    gate = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO_APPROVE)
    d = gate.evaluate("write_file", {"path": "STATE/CONFIG.TOML", "content": "x"})
    assert not d.allowed and not d.needs_user
    assert "OpenWorker's own settings" in d.reason


# -- git's own configuration, conftest.py, CI / editor / package-manager files ----------------

_NEW_MARKER_TARGETS = [
    # git reads these on its own: settings that can name programs or move the hooks
    # folder, attributes that pick filters, and a submodule's own git folder
    ".git/config",
    ".git/info/attributes",
    ".git/modules/vendor/lib/hooks/post-checkout",
    ".git/modules/vendor/lib/config",
    "vendor/lib/.git",  # a `.git` file says which folder git takes settings and hooks from
    ".git/worktrees/feature/config.worktree",  # a linked worktree's own settings
    # pytest imports these on every test run, at any depth
    "conftest.py",
    "tests/unit/conftest.py",
    "pytest.ini",  # its options can load a plugin on every test run
    # Python imports these on every start of an interpreter that can see them
    "sitecustomize.py",
    ".venv/Lib/site-packages/usercustomize.py",
    # mise loads these on entering the folder
    ".mise.toml",
    "mise.toml",
    "mise.local.toml",
    # CI services run these on push
    "azure-pipelines.yml",
    ".travis.yml",
    "Jenkinsfile",
    "bitbucket-pipelines.yml",
    ".buildkite/pipeline.yml",
    ".github/actions/setup/action.yml",  # actions the workflows call
    # the editor runs this when the user starts the program or the debugger
    ".vscode/launch.json",
    ".vscode/settings.json",  # can name the terminal shell, interpreter and formatters
    # package managers read these on every run, and they can name programs to run
    ".npmrc",
    ".yarnrc",
    ".yarnrc.yml",
    ".pnpmfile.cjs",
    # cargo's settings can name the program that runs, links or wraps every build
    ".cargo/config.toml",
    ".cargo/config",
]


@pytest.mark.parametrize("target", _NEW_MARKER_TARGETS)
def test_new_protected_targets_are_human_only(tmp_path, target):
    d = _gate(tmp_path).evaluate("write_file", {"path": target, "content": "x"})
    assert not d.allowed and d.needs_user and d.human_only, target


@pytest.mark.parametrize(
    "path",
    [
        ".gitmodules",
        ".git/index.lock",
        ".git/COMMIT_EDITMSG",
        ".git/info/exclude",
        ".vscode/extensions.json",
        ".github/ISSUE_TEMPLATE/bug.md",
        "docs/pytest.ini.md",
        "src/sitecustomize_helpers.py",
        "notes/mise.toml.example",
        ".cargo/credentials.toml",
        "src/cargo/config.rs",
        "src/conftest.py.bak",
        "tests/my_conftest.py",
        "docs/Jenkinsfile.md",
        "notes/.npmrc.example",
        "src/git/config.py",
        "src/.github_utils.py",
    ],
)
def test_neighbours_of_the_new_targets_stay_ordinary(tmp_path, case_insensitive, path):
    d = _gate(tmp_path).evaluate("write_file", {"path": path, "content": "x"})
    assert not d.human_only, path


# -- shell commands ----------------------------------------------------------------------------


def _shell(tmp_path: Path, command: str, mode: Mode = Mode.AUTO_APPROVE, **kw):
    gate = PermissionEngine(workspace_root=tmp_path, mode=mode, **kw)
    return gate.evaluate("run_shell", {"command": command})


@pytest.mark.parametrize(
    "command, shell",
    [
        ("cp hook.sh .git/hooks/pre-commit", "bash"),
        ("echo 'make test' >> .github/workflows/ci.yml", "bash"),
        ("sed -i 's/a/b/' setup.py", "bash"),
        ("printf 'import os' > tests/conftest.py", "bash"),
        ("mv tmp.yml ./.gitlab-ci.yml", "bash"),
        ("ln -sf ../../tools/hook .git/hooks/pre-push", "bash"),
        ("rm -rf .git/hooks", "bash"),
        ("cat > .envrc <<'X'\nexport A=1\nX", "bash"),
        ('tee -a ".git/config" < extra.cfg', "bash"),
        ("Set-Content -Path .GIT\\hooks\\pre-commit -Value x", "powershell"),
        ("Add-Content .github\\workflows\\ci.yml 'run: x'", "powershell"),
        ("Copy-Item hook.ps1 -Destination:.husky\\pre-commit", "powershell"),
        ("Out-File -FilePath 'AGENTS.md' -InputObject x", "powershell"),
    ],
)
def test_shell_commands_that_change_a_protected_file_are_human_only(tmp_path, case_insensitive, command, shell):
    d = _shell(tmp_path, command, shell_kind=shell)
    assert not d.allowed and d.needs_user and d.human_only, command
    assert "runs automatically later" in d.reason


@pytest.mark.parametrize(
    "command, shell",
    [
        ("cat .github/workflows/ci.yml", "bash"),
        ("grep -n hook .git/hooks/pre-commit", "bash"),
        ("cat .git/config", "bash"),
        ("git diff setup.py", "bash"),
        ("git log -p -- tests/conftest.py", "bash"),
        ("ls -la .husky", "bash"),
        ("head -n 20 tox.ini | grep envlist", "bash"),
        ("cat .github\\workflows\\ci.yml", "powershell"),
    ],
)
def test_shell_reads_of_protected_files_keep_their_normal_handling(tmp_path, command, shell):
    d = _shell(tmp_path, command, shell_kind=shell)
    assert not d.human_only, command
    # The read-only session grant still covers them, as before.
    d = _shell(tmp_path, command, Mode.INTERACTIVE, shell_kind=shell, session_readonly=True)
    assert d.allowed, command


@pytest.mark.parametrize(
    "command",
    ["ls -la .git/hooks 2>/dev/null | head", "cat .git/config 2>&1", "cat setup.py > /dev/null"],
)
def test_a_read_that_throws_output_away_is_still_a_read(tmp_path, command):
    assert not _shell(tmp_path, command, shell_kind="bash").human_only, command


@pytest.mark.parametrize(
    "command",
    ["ls -la .git && make", "find . -newer x | grep -v .git > files.txt", "tar czf src.tgz --exclude=.git ."],
)
def test_naming_the_git_folder_in_a_command_is_not_a_change_to_it(tmp_path, command):
    # A `.git` FILE is protected for the file tools; in a command `.git` almost always
    # names the folder being listed or left out.
    (tmp_path / ".git").mkdir()
    assert not _shell(tmp_path, command, shell_kind="bash").human_only, command


@pytest.mark.parametrize(
    "command",
    ["cat setup.py && cat tox.ini", "cat .gitattributes; ls .git/hooks",
     "cd /src/app && head -40 .gitlab-ci.yml", "cd sub; cat .pre-commit-config.yaml | head -40"],
)
def test_a_sequence_of_reads_is_still_a_read(tmp_path, command):
    d = _shell(tmp_path, command, shell_kind="bash")
    assert not d.human_only, command


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "python -m build",
        "npm install",
        "cp docs/setup.py.txt notes/",
        "echo setup.pyx > notes.txt",
        "rm -rf build dist",
        "git config --get user.email",
        "git config user.name",
        "git config --list --show-origin",
        "git config user.email a@b.c",
        "git config --global user.name 'A B'",
        "git config set --local init.defaultBranch main",
        "git config --global core.longpaths true",
        "git config push.autoSetupRemote true",
        "git config --global push.default simple",
        "git config fetch.prune true",
        "git config rebase.autoStash true",
        "git config merge.conflictStyle zdiff3",
        "cd .git && cat config",
    ],
)
def test_other_shell_commands_are_unaffected(tmp_path, case_insensitive, command):
    d = _shell(tmp_path, command, shell_kind="bash")
    assert not d.human_only, command


@pytest.mark.parametrize(
    "command",
    [
        "git config core.hooksPath tools/hooks",
        "git config --local core.fsmonitor ./watch.sh",
        "git -C sub config alias.st '!sh x'",
        "git config set core.pager less",
        "git config --global core.editor vim",
        "git config --unset core.hooksPath",
        "git config -e",
        "git config --system --add safe.directory /deps/repo",
        "git config --global url.file:///deps/.insteadOf https://github.com/",
        "git config --global protocol.file.allow always",
        "git config user.email a@b.c && git config core.hooksPath x",
        "cd $HOME && cp hook .git/hooks/pre-commit",
    ],
)
def test_git_config_changes_are_human_only(tmp_path, command):
    d = _shell(tmp_path, command, shell_kind="bash")
    assert d.human_only, command


@pytest.mark.parametrize(
    "command",
    ["python setup.py develop", "python scripts/setup.py", "py -3 setup.py sdist",
     "bash .husky/pre-commit", "./setup.py install", "python3 -u setup.py test && pytest -q"],
)
def test_running_a_protected_file_is_not_changing_it(tmp_path, command):
    # Running it now is an ordinary command the reviewer sees; nothing is left behind.
    d = _shell(tmp_path, command, shell_kind="bash")
    assert not d.human_only, command


@pytest.mark.parametrize(
    "command",
    ["python setup.py > setup.py", "python -c \"open('setup.py', 'w').write('x')\"",
     "python tools/gen.py --out conftest.py", "python setup.py && cp x setup.py"],
)
def test_another_mention_beside_running_it_still_counts(tmp_path, command):
    d = _shell(tmp_path, command, shell_kind="bash")
    assert d.human_only, command


def test_shell_paths_are_read_from_the_shells_current_folder(tmp_path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    d = _shell(tmp_path, "cp ../hook.sh hooks/pre-commit", shell_kind="bash",
               shell_cwd=lambda: str(tmp_path / ".git"))
    assert d.human_only


def test_shell_wildcards_are_expanded_against_the_folder(tmp_path):
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "pre-commit").write_text("")
    assert _shell(tmp_path, "cp pre-commit .git/ho*", shell_kind="bash").human_only
    assert _shell(tmp_path, "cp hook.sh .git/ho*/pre-c*", shell_kind="bash").human_only


@pytest.mark.parametrize("mode", [Mode.INTERACTIVE, Mode.AUTO_APPROVE, Mode.BYPASS_APPROVALS])
def test_shell_protection_ignores_allowlists_and_grants(tmp_path, mode):
    command = "cp hook.sh .git/hooks/pre-commit"
    gate = PermissionEngine(
        workspace_root=tmp_path, mode=mode, allowed_commands=["cp"], shell_kind="bash",
        session_allow_commands={command}, session_readonly=True,
    )
    d = gate.evaluate("run_shell", {"command": command})
    assert not d.allowed and d.human_only


def test_dangerous_mode_clears_the_shell_floor_too(tmp_path):
    d = _shell(tmp_path, "cp hook.sh .git/hooks/pre-commit", Mode.DANGEROUSLY_BYPASS_APPROVALS,
               shell_kind="bash")
    assert d.allowed and "runs automatically later" in d.reason


# -- every spelling that reaches the same file (Windows) ---------------------------------------

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows path spellings")


def _short_name(path: Path) -> "str | None":
    import ctypes

    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
    if not n or buf.value.lower() == str(path).lower():
        return None
    return Path(buf.value).name


@windows_only
@pytest.mark.parametrize(
    "target",
    [
        "setup.py.",
        "setup.py. . ",
        "setup.py::$DATA",
        "conftest.py:extra",
        ".envrc...",
        "tests/conftest.py ",
        ".git/hooks/pre-commit.",
    ],
)
def test_windows_trailing_dots_spaces_and_streams_reach_the_same_file(tmp_path, target):
    (tmp_path / "tests").mkdir()
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    d = _gate(tmp_path).evaluate("write_file", {"path": target, "content": "x"})
    assert not d.allowed and d.human_only, target


@windows_only
def test_windows_folder_stream_spelling_is_the_folder(tmp_path):
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    d = _gate(tmp_path).evaluate(
        "write_file", {"path": ".git::$INDEX_ALLOCATION/hooks/pre-commit", "content": "x"}
    )
    assert not d.allowed and d.human_only


@windows_only
def test_windows_short_names_resolve_to_the_real_folder(tmp_path):
    github = tmp_path / ".github"
    (github / "workflows").mkdir(parents=True)
    short = _short_name(github)
    if short is None:
        pytest.skip("8.3 short names are turned off on this volume")
    d = _gate(tmp_path).evaluate("write_file", {"path": f"{short}/workflows/ci.yml", "content": "x"})
    assert d.human_only
    d = _shell(tmp_path, f"Copy-Item x.yml {short}\\workflows\\ci.yml", shell_kind="powershell")
    assert d.human_only


@windows_only
def test_windows_junction_into_a_protected_folder_is_followed(tmp_path):
    import subprocess

    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True)
    link = tmp_path / "tools"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(hooks)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("could not create a junction here")
    d = _gate(tmp_path).evaluate("write_file", {"path": "tools/pre-commit", "content": "x"})
    assert d.human_only
    d = _shell(tmp_path, "Set-Content tools\\pre-commit x", shell_kind="powershell")
    assert d.human_only


def test_a_write_whose_location_cannot_be_worked_out_fails_closed(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("cannot resolve")

    monkeypatch.setattr(permissions.os.path, "realpath", boom)
    assert _is_protected_in_project(Path("/w/src/app.py"))
    # Words in a shell command are not all paths, so there it is only a missed match.
    assert not _is_protected_in_project(Path("/w/src/app.py"), fail_closed=False)


@windows_only
def test_a_name_windows_would_reduce_to_nothing_fails_closed(tmp_path):
    d = _gate(tmp_path).evaluate("write_file", {"path": "src/...", "content": "x"})
    assert d.human_only
