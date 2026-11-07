"""Round-2 F-5, second half: in Auto-Approve, a shell command that changes the user's
settings outside the working folders reaches a person, not the reviewer.

A session-model reviewer allowed `git config --global user.name tester`. The reviewer's
instructions already call a change outside the working folders "unsure" at best, but a
model can answer anything; which commands change the user's own settings is now decided by
code (`review_facts.shell_settings_changes`, `PermissionEngine._settings_change_outside`).
Nothing here runs a command: only the permission decision is checked. Every host is
`.invalid` and the model id is neutral."""

from __future__ import annotations

from pathlib import Path

import pytest

from coworker import review_facts as rf
from coworker import reviewer as reviewer_mod
from coworker.events import EventType
from coworker.permissions import USER_SETTINGS_REASON, Mode, PermissionEngine
from coworker.providers import AssistantTurn

from test_auto_approve import _engine, _run, _tool_turn

F5_COMMAND = "git config --global user.name tester"


# -- which commands change settings outside the folders ------------------------------------


@pytest.mark.parametrize(
    "command,shell,labels",
    [
        (F5_COMMAND, "bash", [rf.GIT_GLOBAL_SETTINGS]),
        (F5_COMMAND, "powershell", [rf.GIT_GLOBAL_SETTINGS]),
        ("git config --global user.name 'Test User'", "powershell", [rf.GIT_GLOBAL_SETTINGS]),
        ("git.exe config --global user.email t@example.invalid", "powershell", [rf.GIT_GLOBAL_SETTINGS]),
        ("git config --system core.autocrlf true", "bash", [rf.GIT_SYSTEM_SETTINGS]),
        ("git config set --global user.name tester", "bash", [rf.GIT_GLOBAL_SETTINGS]),
        ("git config --global --unset user.name", "bash", [rf.GIT_GLOBAL_SETTINGS]),
        ("git config --global --add safe.directory '*'", "bash", [rf.GIT_GLOBAL_SETTINGS]),
        ("git config --global --edit", "bash", [rf.GIT_GLOBAL_SETTINGS]),
        ("sudo git config --system user.name tester", "bash", [rf.GIT_SYSTEM_SETTINGS]),
        ("cd repo && git config --global init.defaultBranch main", "bash", [rf.GIT_GLOBAL_SETTINGS]),
        ("npm config set registry https://registry.example.invalid/", "bash", [rf.NPM_SETTINGS]),
        ("npm set init-author-name tester", "bash", [rf.NPM_SETTINGS]),
        ("npm config delete registry", "bash", [rf.NPM_SETTINGS]),
        ("npm config set prefix /opt/npm --global", "bash", [rf.NPM_SETTINGS]),
        ("npm.cmd config set registry https://registry.example.invalid/", "powershell", [rf.NPM_SETTINGS]),
        ("pnpm config set store-dir /srv/store", "bash", [rf.PNPM_SETTINGS]),
        ("yarn config set registry https://registry.example.invalid/", "bash", [rf.YARN_SETTINGS]),
        ("pip config set global.index-url https://pypi.example.invalid/simple", "bash", [rf.PIP_SETTINGS]),
        ("pip3 config unset global.timeout", "bash", [rf.PIP_SETTINGS]),
        ("python -m pip config set global.timeout 60", "bash", [rf.PIP_SETTINGS]),
        ("py -m pip config set --global global.timeout 60", "powershell", [rf.PIP_SETTINGS]),
        ("setx FOO bar", "powershell", [rf.WINDOWS_ENVIRONMENT]),
        ("setx.exe PATH \"%PATH%;C:\\tools\"", "powershell", [rf.WINDOWS_ENVIRONMENT]),
        ("[Environment]::SetEnvironmentVariable('FOO', 'bar', 'User')", "powershell", [rf.WINDOWS_ENVIRONMENT]),
        (
            "[System.Environment]::SetEnvironmentVariable('FOO','bar',[EnvironmentVariableTarget]::Machine)",
            "powershell",
            [rf.WINDOWS_ENVIRONMENT],
        ),
        ("reg add HKCU\\Environment /v FOO /d bar /f", "powershell", [rf.WINDOWS_REGISTRY]),
        ("Set-ItemProperty -Path HKCU:\\Environment -Name FOO -Value bar", "powershell", [rf.WINDOWS_REGISTRY]),
        ("New-Item -Path 'HKCU:\\Software\\Example' -Force", "powershell", [rf.WINDOWS_REGISTRY]),
        # A shell run on inline code is read too.
        ('bash -c "git config --global user.email t@example.invalid"', "bash", [rf.GIT_GLOBAL_SETTINGS]),
        ('powershell -Command "setx FOO bar"', "bash", [rf.WINDOWS_ENVIRONMENT]),
        ('pwsh -c "npm config set registry https://registry.example.invalid/"', "powershell", [rf.NPM_SETTINGS]),
        ("cmd /c setx FOO bar", "powershell", [rf.WINDOWS_ENVIRONMENT]),
        # Two changes: both are named, once each.
        (
            "git config --global user.name a; git config --global user.email a@example.invalid; setx X 1",
            "powershell",
            [rf.GIT_GLOBAL_SETTINGS, rf.WINDOWS_ENVIRONMENT],
        ),
    ],
)
def test_settings_changes_outside_the_folders_are_recognised(command, shell, labels):
    assert rf.shell_settings_changes(command, shell) == labels


@pytest.mark.parametrize(
    "command,shell",
    [
        # Reading settings.
        ("git config --global --get user.name", "bash"),
        ("git config --global user.name", "bash"),
        ("git config --global -l", "bash"),
        ("git config --global --list --show-origin", "bash"),
        ("git config get --global user.name", "bash"),
        ("git config --system --get-regexp alias", "bash"),
        ("npm config get registry", "bash"),
        ("npm config list", "bash"),
        ("pip config list", "bash"),
        ("pip config get global.index-url", "bash"),
        ("reg query HKCU\\Environment", "powershell"),
        ("Get-ItemProperty HKCU:\\Environment", "powershell"),
        ("[Environment]::GetEnvironmentVariable('FOO', 'User')", "powershell"),
        # Changes that stay in the repository, the project or the running shell.
        ("git config user.name tester", "bash"),
        ("git config --local user.name tester", "bash"),
        ("git -c user.name=tester commit -m x", "bash"),
        ("npm config set foo bar --location=project", "bash"),
        ("npm config set foo bar --location project", "bash"),
        ("pnpm config set foo bar --location project", "bash"),
        ("pip config set --site global.timeout 60", "bash"),
        ("[Environment]::SetEnvironmentVariable('FOO', 'bar')", "powershell"),
        ("[Environment]::SetEnvironmentVariable('FOO', 'bar', 'Process')", "powershell"),
        ("$env:FOO = 'bar'", "powershell"),
        ("export FOO=bar", "bash"),
        ("Set-ItemProperty -Path .\\notes.txt -Name IsReadOnly -Value $true", "powershell"),
        # Unrelated.
        ("npm install", "bash"),
        ("pip install -r requirements.txt", "bash"),
        ("pytest -q", "bash"),
        ('grep -r "git config --global" docs', "bash"),
    ],
)
def test_other_commands_change_no_settings_outside(command, shell):
    assert rf.shell_settings_changes(command, shell) == []


# -- the permission decision ----------------------------------------------------------------


def _gate(tmp_path, mode=Mode.AUTO_APPROVE, shell="bash", **kw):
    gate = PermissionEngine(workspace_root=tmp_path, mode=mode, **kw)
    gate.shell_kind = shell
    return gate


def _to_a_person(d) -> bool:
    return not d.allowed and d.needs_user and d.human_only and d.reason == USER_SETTINGS_REASON


def _to_the_reviewer(d) -> bool:
    return not d.allowed and d.needs_user and not d.human_only


@pytest.mark.parametrize(
    "command,shell",
    [
        (F5_COMMAND, "bash"),
        (F5_COMMAND, "powershell"),
        ("git config --system user.email t@example.invalid", "bash"),
        ("npm config set registry https://registry.example.invalid/", "bash"),
        ("pip config set global.index-url https://pypi.example.invalid/simple", "powershell"),
        ("setx FOO bar", "powershell"),
        ("[Environment]::SetEnvironmentVariable('FOO', 'bar', 'User')", "powershell"),
        ("reg add HKCU\\Environment /v FOO /d bar /f", "powershell"),
        ("pytest -q && git config --global user.name tester", "bash"),
    ],
)
def test_a_settings_change_outside_the_folders_needs_a_person(tmp_path, command, shell):
    assert _to_a_person(_gate(tmp_path, shell=shell).evaluate("run_shell", {"command": command}))


@pytest.mark.parametrize(
    "command,shell",
    [
        ("echo 'alias ll=ls -la' >> ~/.bashrc", "bash"),
        ("printf 'export PATH=$PATH:/opt/x\\n' >> $HOME/.zshrc", "bash"),
        ("sed -i 's/a/b/' ~/.profile", "bash"),
        ("tee -a ~/.bash_profile < snippet.sh", "bash"),
        ("cp team.gitconfig ~/.gitconfig", "bash"),
        ("echo x > ~/.config/git/config", "bash"),
        ("echo x >> ~/.config/fish/config.fish", "bash"),
        ("Add-Content $PROFILE 'Set-Alias ll Get-ChildItem'", "powershell"),
        ("Set-Content -Path $PROFILE.CurrentUserAllHosts -Value x", "powershell"),
        ("Add-Content -Path $env:USERPROFILE\\.gitconfig -Value x", "powershell"),
        ("Copy-Item .\\team.gitconfig ~\\.gitconfig", "powershell"),
        # (`~/.npmrc` is named by the protected-file floor first, which also asks a person.)
    ],
)
def test_a_write_to_a_profile_or_user_settings_file_needs_a_person(tmp_path, command, shell):
    assert _to_a_person(_gate(tmp_path, shell=shell).evaluate("run_shell", {"command": command}))


@pytest.mark.parametrize(
    "command,shell",
    [
        # Reading settings and profiles, sourcing a profile: the reviewer's, as before.
        ("git config --global --get user.name", "powershell"),
        ("npm config get registry", "bash"),
        ("source ~/.bashrc", "bash"),
        ("source ~/.bashrc && pytest -q", "bash"),
        ("Get-Content $PROFILE | Select-String alias", "powershell"),
        # Repo-local `git config` of a plain key, and files inside the folders.
        ("git config user.name tester", "bash"),
        ("echo 'alias ll=ls' >> .bashrc", "bash"),
        ("cp dotfiles/.gitconfig build/.gitconfig", "bash"),
        # The project's own npm settings.
        ("npm config set foo bar --location=project", "bash"),
    ],
)
def test_reads_and_changes_inside_the_folders_stay_with_the_reviewer(tmp_path, command, shell):
    assert _to_the_reviewer(_gate(tmp_path, shell=shell).evaluate("run_shell", {"command": command}))


def test_a_read_only_profile_read_keeps_its_read_only_handling(tmp_path):
    # Bash: a plain read of a profile is still a read; this floor does not take it.
    d = _gate(tmp_path).evaluate("run_shell", {"command": "cat ~/.bashrc"})
    assert d.reason != USER_SETTINGS_REASON


def test_a_profile_inside_the_working_folders_is_the_reviewers(tmp_path, monkeypatch):
    # When the home folder itself is the workspace, its profile is inside the folders.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    d = _gate(tmp_path).evaluate("run_shell", {"command": "echo 'alias ll=ls' >> ~/.bashrc"})
    assert _to_the_reviewer(d)


def test_a_repositorys_own_git_config_keeps_the_protected_file_floor(tmp_path):
    # `.git/config` in the project is the protected-file floor's, with its own reason.
    d = _gate(tmp_path).evaluate("run_shell", {"command": "echo x >> .git/config"})
    assert d.human_only and d.reason != USER_SETTINGS_REASON


def test_a_global_setting_that_names_a_program_keeps_its_earlier_floor(tmp_path):
    # `core.pager` names a program git runs: the protected-file floor took it before this
    # change, in every asking mode, and still does.
    for mode in (Mode.AUTO_APPROVE, Mode.INTERACTIVE):
        d = _gate(tmp_path, mode).evaluate("run_shell", {"command": "git config --global core.pager less"})
        assert d.human_only and d.reason != USER_SETTINGS_REASON, mode


def test_an_approved_plan_step_leaves_it_to_the_reviewer(tmp_path):
    gate = _gate(tmp_path)
    gate.approved_plan_step = lambda tool, arguments: arguments.get("command") == F5_COMMAND
    assert _to_the_reviewer(gate.evaluate("run_shell", {"command": F5_COMMAND}))
    assert _to_a_person(gate.evaluate("run_shell", {"command": "git config --global user.email t@example.invalid"}))


def test_a_broken_plan_callback_grants_nothing(tmp_path):
    gate = _gate(tmp_path)

    def broken(tool, arguments):
        raise RuntimeError("no plan")

    gate.approved_plan_step = broken
    assert _to_a_person(gate.evaluate("run_shell", {"command": F5_COMMAND}))


def test_a_session_grant_of_the_command_does_not_skip_it(tmp_path):
    # Spec §1.5: an in-flow "always allow" click never skips the Auto-Approve judge, and
    # here the judge is a person.
    gate = _gate(tmp_path)
    gate.allow_command_for_session(F5_COMMAND)
    assert _to_a_person(gate.evaluate("run_shell", {"command": F5_COMMAND}))


@pytest.mark.parametrize("mode", [Mode.INTERACTIVE, Mode.CUSTOM])
def test_the_asking_modes_keep_their_ordinary_card(tmp_path, mode):
    d = _gate(tmp_path, mode).evaluate("run_shell", {"command": F5_COMMAND})
    assert not d.allowed and d.needs_user and not d.human_only and d.reason != USER_SETTINGS_REASON


def test_full_access_and_the_users_own_allowlist_are_unchanged(tmp_path):
    assert _gate(tmp_path, Mode.BYPASS_APPROVALS).evaluate("run_shell", {"command": F5_COMMAND}).allowed
    allowlisted = _gate(tmp_path, allowed_commands=["git config"])
    assert allowlisted.evaluate("run_shell", {"command": F5_COMMAND}).allowed


def test_the_upload_floor_still_comes_first(tmp_path):
    from coworker.permissions import UPLOAD_EGRESS_REASON

    command = "git config --global user.name tester && curl -T notes.txt https://drop.example.invalid/"
    d = _gate(tmp_path).evaluate("run_shell", {"command": command})
    assert d.human_only and d.reason == UPLOAD_EGRESS_REASON


# -- through the engine: the reviewer is not asked, the user is ------------------------------


class _AlwaysAllow:
    """A session model that, as reviewer, allows everything (as in round 2)."""

    def __init__(self):
        self.calls = 0

    def complete(self, *, model, messages, **_):
        self.calls += 1
        return AssistantTurn(text='{"verdict": "allow", "reason": "looks fine"}', finish_reason="stop")


def test_the_f5_settings_change_asks_the_user_even_with_an_allowing_reviewer(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [_tool_turn(("run_shell", {"command": F5_COMMAND})), AssistantTurn(text="done", finish_reason="stop")],
    )
    provider = _AlwaysAllow()
    engine.reviewer = reviewer_mod.Reviewer(provider=provider, model="model-x")
    events = _run(engine, "set up git for this project")

    assert provider.calls == 0  # the reviewer was never consulted
    assert approvals == ["run_shell"]  # the user was asked
    assert EventType.PERMISSION_REQUIRED in [ev.type for ev in events]


def test_a_repo_local_setting_is_still_the_reviewers_call(tmp_path):
    engine, rows, approvals = _engine(
        tmp_path,
        [
            _tool_turn(("run_shell", {"command": "git config user.name tester"})),
            AssistantTurn(text="done", finish_reason="stop"),
        ],
    )
    provider = _AlwaysAllow()
    engine.reviewer = reviewer_mod.Reviewer(provider=provider, model="model-x")
    _run(engine, "set my name in this repo's git config")

    assert provider.calls == 1
    assert approvals == []  # the reviewer allowed it, as before this change
