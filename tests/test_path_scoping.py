"""File-write path scoping — symlink escape + TOCTOU revalidation + overbroad roots (#35)."""

from __future__ import annotations

import os
from pathlib import Path

import aisuite as ai
import pytest

from coworker.engine import TurnEngine
from coworker.permissions import Mode, PermissionEngine, write_paths
from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    ToolCall,
)
from coworker.roots import overbroad_root_warning
from coworker.tools import ToolRegistry


def _symlink(target: Path, link: Path, *, directory: bool) -> None:
    """Create a symlink, or on Windows a directory junction (no admin required)."""
    try:
        os.symlink(target, link, target_is_directory=directory)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt" and directory:
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return
        except (OSError, AttributeError, NotImplementedError):
            pass
    pytest.skip("symlinks unavailable on this platform/user")


# -- PermissionEngine symlink escape -------------------------------------------


def test_symlink_escape_rejected_by_writable_root(tmp_path):
    root = tmp_path / "ws"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("leak", encoding="utf-8")
    _symlink(outside, root / "escape", directory=True)

    eng = PermissionEngine(workspace_root=root, mode=Mode.AUTO)
    assert eng._under_writable_root("ok.txt")
    assert not eng._under_writable_root("escape/secret.txt")
    d = eng.evaluate(
        "write_file",
        {"path": "escape/secret.txt", "content": "x"},
        None,
    )
    assert not d.allowed and not d.needs_user
    assert "writable" in d.reason


def test_toctou_revalidate_catches_symlink_swap(tmp_path):
    """Approve against a real in-root path, then swap a parent for an outbound symlink."""
    root = tmp_path / "ws"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    sub = root / "sub"
    sub.mkdir()
    (sub / "file.txt").write_text("ok", encoding="utf-8")

    eng = PermissionEngine(workspace_root=root, mode=Mode.AUTO)
    args = {"path": "sub/file.txt", "content": "new"}
    assert eng.evaluate("write_file", args, None).allowed
    assert eng.revalidate_write("write_file", args) is None

    # Swap the checked parent for a symlink that resolves outside the root.
    import shutil

    shutil.rmtree(sub)
    _symlink(outside, sub, directory=True)
    (outside / "file.txt").write_text("outside", encoding="utf-8")

    denied = eng.revalidate_write("write_file", args)
    assert denied is not None
    assert "writable" in denied


def test_execute_sync_blocks_after_symlink_swap(tmp_path):
    root = tmp_path / "ws"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    sub = root / "sub"
    sub.mkdir()
    (sub / "file.txt").write_text("ok", encoding="utf-8")

    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(root), allow_write=True))
    permissions = PermissionEngine(workspace_root=root, mode=Mode.AUTO)

    class _P(ProviderClient):
        def complete(self, *, model, messages, tools=None, **s):
            return AssistantTurn(text="", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    engine = TurnEngine(
        provider=_P(),
        registry=registry,
        permissions=permissions,
        model="test",
    )
    tc = ToolCall(
        id="c1",
        name="write_file",
        arguments={"path": "sub/file.txt", "content": "pwned"},
    )
    assert engine._execute_sync(tc)[1] == "ok"

    import shutil

    shutil.rmtree(sub)
    _symlink(outside, sub, directory=True)
    (outside / "file.txt").write_text("outside", encoding="utf-8")

    result, status = engine._execute_sync(tc)
    assert status == "error"
    assert result["error_type"] == "PermissionError"
    assert "writable" in result["error"]
    # Outside file must not have been overwritten by the blocked write.
    assert (outside / "file.txt").read_text(encoding="utf-8") == "outside"


def test_write_paths_from_apply_patch():
    patch = (
        "*** Begin Patch\n"
        "*** Add File: nested/new.txt\n"
        "+hi\n"
        "*** Update File: existing.py\n"
        "*** End Patch\n"
    )
    assert write_paths("apply_patch", {"patch": patch}) == [
        "nested/new.txt",
        "existing.py",
    ]


def test_apply_patch_escape_denied_at_evaluate(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO)
    patch = (
        "*** Begin Patch\n"
        "*** Add File: ../escape.txt\n"
        "+nope\n"
        "*** End Patch\n"
    )
    d = eng.evaluate("apply_patch", {"patch": patch}, None)
    assert not d.allowed and not d.needs_user


# -- Overbroad roots -----------------------------------------------------------


def test_overbroad_root_warning_home_and_ancestor(tmp_path, monkeypatch):
    home = tmp_path / "Users" / "alice"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)

    assert overbroad_root_warning(home) is not None
    assert "home directory" in overbroad_root_warning(home)

    ancestor = tmp_path
    warn = overbroad_root_warning(ancestor)
    assert warn is not None
    assert "above $HOME" in warn

    project = home / "proj"
    project.mkdir()
    assert overbroad_root_warning(project) is None


def test_overbroad_root_warning_via_symlink(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    alias = tmp_path / "alias-to-home"
    _symlink(home, alias, directory=True)
    warn = overbroad_root_warning(alias)
    assert warn is not None
    assert "home directory" in warn


def test_add_root_surfaces_overbroad_warning(tmp_path, monkeypatch):
    from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from coworker.server import SessionManager

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)

    class _Provider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **s):
            return AssistantTurn(text="", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    mgr = SessionManager(data_dir=tmp_path / "data", provider=_Provider())
    mgr._prefs["scratch_base"] = str(tmp_path / "scratchbase")
    sid = "overbroad"
    # Ensure an engine exists so add_root mutates live roots.
    assert mgr.get_engine(sid, agent="cowork") is not None

    res = mgr.add_root(sid, str(home), writable=True)
    assert res["ok"] is True
    assert "warning" in res
    assert "home directory" in res["warning"]
