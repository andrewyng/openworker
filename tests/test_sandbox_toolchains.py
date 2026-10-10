"""The developer-tool folders a sandbox may read (coworker/sandbox/toolchains.py): a shipped
list per platform, the user's switches and additions by name, only what exists under the
home folder."""

from __future__ import annotations

import os

from coworker.sandbox import toolchains


def test_the_shipped_lists_are_per_platform_and_switched_off():
    mac = toolchains.defaults("darwin")
    win = toolchains.defaults("win32")
    assert {e["name"] for e in mac} >= {"nvm", "pyenv", "cargo", "go", "uv"}
    assert {e["name"] for e in win} >= {"nvm", "npm", "pyenv", "scoop", "cargo"}
    assert all(not e["enabled"] and e["path"].startswith("~/") for e in mac + win)  # nothing until switched on (UX-053 v6)
    assert toolchains.defaults("linux") == []  # OpenShell mounts no home folder


def test_the_user_edits_and_adds_by_name():
    rows = toolchains.entries([{"name": "nvm", "enabled": False}, {"name": "mytools", "path": "~/tools", "title": "My tools"}], "darwin")
    by = {e["name"]: e for e in rows}
    assert by["nvm"]["enabled"] is False and by["nvm"]["path"] == "~/.nvm"  # the path stays shipped
    assert by["mytools"] == {"name": "mytools", "title": "My tools", "path": "~/tools", "enabled": True}


def test_granted_is_only_what_is_on_and_exists_under_home(tmp_path):
    home = tmp_path / "home"
    (home / ".nvm").mkdir(parents=True)
    (home / "tools").mkdir()
    (tmp_path / "outside").mkdir()
    configured = [
        {"name": "nvm", "enabled": True},
        {"name": "pyenv", "enabled": True},  # on, but missing here
        {"name": "cargo", "enabled": False},
        {"name": "mytools", "path": "~/tools"},
        {"name": "escape", "path": str(tmp_path / "outside")},  # outside the home folder: never
        {"name": "home", "path": "~"},  # the home folder itself: never
    ]
    got = toolchains.granted(configured, home=str(home), platform="darwin")
    assert got == [os.path.realpath(home / ".nvm"), os.path.realpath(home / "tools")]
    shown = {e["name"]: e for e in toolchains.for_display(configured, home=str(home), platform="darwin")}
    assert shown["nvm"]["exists"] and shown["nvm"]["shipped"]
    assert "pyenv" not in shown and "go" not in shown  # shipped but not on this machine: not shown
    assert shown["mytools"]["exists"] and not shown["mytools"]["shipped"]
