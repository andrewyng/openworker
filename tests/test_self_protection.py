"""PR2 — the permission system protects its own settings, and in-project files that
execute later are never auto-approved.

The escalation being blocked: approve one ordinary-looking command, it quietly appends to
`risk_overrides.json`, and every future session is more permissive. That happens in the
DEFAULT interactive mode, so the protection must be a floor — not a property of a sandbox
or of any one mode. See `ocw-context/docs/reviewed-auto-mode.md` Part 3.
"""

from __future__ import annotations

import pytest

from coworker.permissions import Mode, PermissionEngine, protected_paths
from coworker.secrets import state_dir

ALL_MODES = [Mode.DISCUSS, Mode.PLAN, Mode.INTERACTIVE, Mode.CUSTOM, Mode.AUTO_APPROVE, Mode.BYPASS_APPROVALS]


def _engine(tmp_path, mode=Mode.INTERACTIVE, **kw):
    # The state dir is redirected per-test by the autouse fixture in conftest, so the
    # protected paths point somewhere isolated.
    return PermissionEngine(workspace_root=tmp_path, mode=mode, **kw)


# -- the settings files ---------------------------------------------------------
@pytest.mark.parametrize("mode", ALL_MODES)
def test_write_to_settings_blocked_in_every_mode(tmp_path, mode):
    eng = _engine(tmp_path, mode)
    target = str(state_dir() / "risk_overrides.json")
    d = eng.evaluate("write_file", {"path": target, "content": "x"}, None)
    assert not d.allowed
    assert not d.needs_user, "must be a hard refusal, not an approval the user can grant"


@pytest.mark.parametrize("mode", ALL_MODES)
def test_shell_touching_settings_blocked_in_every_mode(tmp_path, mode):
    eng = _engine(tmp_path, mode)
    target = str(state_dir() / "risk_overrides.json")
    d = eng.evaluate("run_shell", {"command": f'echo "{{}}" > "{target}"'}, None)
    assert not d.allowed and not d.needs_user


def test_settings_write_beats_auto_mode_and_allowlists(tmp_path):
    # Every auto-approve path must lose to the floor.
    target = str(state_dir() / "config.toml")
    auto = _engine(tmp_path, Mode.BYPASS_APPROVALS)
    assert not auto.evaluate("write_file", {"path": target, "content": "x"}, None).allowed

    custom = _engine(tmp_path, Mode.CUSTOM, auto_allow_tools={"write_file"})
    assert not custom.evaluate("write_file", {"path": target, "content": "x"}, None).allowed

    session = _engine(tmp_path)
    session.allow_tool_for_session("write_file")
    assert not session.evaluate("write_file", {"path": target, "content": "x"}, None).allowed


def test_settings_protected_via_patch_blob(tmp_path):
    # The patch path is extracted from the blob, so this route is covered too.
    eng = _engine(tmp_path, Mode.BYPASS_APPROVALS)
    target = str(state_dir() / "workspace_trust.json")
    patch = f"*** Begin Patch\n*** Update File: {target}\n@@\n-a\n+b\n*** End Patch"
    assert not eng.evaluate("apply_patch", {"patch": patch}, None).allowed


def test_protected_paths_cover_the_grant_and_trust_stores():
    names = {p.name for p in protected_paths()}
    assert {"config.toml", "risk_overrides.json", "workspace_trust.json", "coworker.db"} <= names


# -- in-project files that run later --------------------------------------------
@pytest.mark.parametrize(
    "rel",
    [
        ".git/hooks/pre-commit",
        ".github/workflows/ci.yml",
        ".vscode/tasks.json",
        ".coworker/config.toml",
    ],
)
def test_protected_in_project_never_auto_approved(tmp_path, rel):
    # Writable (inside the root) but must always reach a human, even in auto mode.
    for mode in (Mode.BYPASS_APPROVALS, Mode.CUSTOM, Mode.INTERACTIVE):
        eng = _engine(tmp_path, mode, auto_allow_tools={"write_file"})
        eng.allow_tool_for_session("write_file")
        d = eng.evaluate("write_file", {"path": rel, "content": "x"}, None)
        assert not d.allowed, f"{rel} auto-approved in {mode.value}"
        assert d.needs_user, f"{rel} should ask, not hard-deny, in {mode.value}"


def test_ordinary_project_file_still_auto_approves(tmp_path):
    # The protection must not leak onto normal edits.
    eng = _engine(tmp_path, Mode.BYPASS_APPROVALS)
    assert eng.evaluate("write_file", {"path": "src/app.py", "content": "x"}, None).allowed


def test_lookalike_paths_are_not_protected(tmp_path):
    # A file merely NAMED like a hook, outside the protected dirs, is ordinary.
    eng = _engine(tmp_path, Mode.BYPASS_APPROVALS)
    assert eng.evaluate("write_file", {"path": "docs/pre-commit.md", "content": "x"}, None).allowed
