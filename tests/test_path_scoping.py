"""File-write path scoping: symlink escape, TOCTOU revalidation, overbroad roots (#35)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import aisuite as ai
import pytest

from coworker.engine import TurnEngine
from coworker.permissions import Mode, PermissionEngine
from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    ToolCall,
)
from coworker.roots import overbroad_root_warning
from coworker.tools import ToolRegistry


def _link_dir(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return
        except (OSError, AttributeError, NotImplementedError):
            pass
    pytest.skip("symlinks unavailable on this platform/user")


def test_symlink_escape_rejected(tmp_path):
    root, outside = tmp_path / "ws", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("leak", encoding="utf-8")
    _link_dir(outside, root / "escape")

    eng = PermissionEngine(workspace_root=root, mode=Mode.AUTO)
    assert not eng._under_writable_root("escape/secret.txt")
    d = eng.evaluate("write_file", {"path": "escape/secret.txt", "content": "x"}, None)
    assert not d.allowed and not d.needs_user


def test_execute_blocks_after_symlink_swap(tmp_path):
    root, outside = tmp_path / "ws", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    sub = root / "sub"
    sub.mkdir()
    (sub / "file.txt").write_text("ok", encoding="utf-8")

    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(root), allow_write=True))

    class _P(ProviderClient):
        def complete(self, *, model, messages, tools=None, **s):
            return AssistantTurn(text="", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    engine = TurnEngine(
        provider=_P(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=root, mode=Mode.AUTO),
        model="test",
    )
    tc = ToolCall(
        id="c1",
        name="write_file",
        arguments={"path": "sub/file.txt", "content": "pwned"},
    )
    assert engine._execute_sync(tc)[1] == "ok"

    shutil.rmtree(sub)
    _link_dir(outside, sub)
    (outside / "file.txt").write_text("outside", encoding="utf-8")

    result, status = engine._execute_sync(tc)
    assert status == "error"
    assert result["error_type"] == "PermissionError"
    assert (outside / "file.txt").read_text(encoding="utf-8") == "outside"


def test_overbroad_root_warning(tmp_path, monkeypatch):
    home = tmp_path / "Users" / "alice"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)

    assert overbroad_root_warning(home) is not None
    assert overbroad_root_warning(tmp_path) is not None
    project = home / "proj"
    project.mkdir()
    assert overbroad_root_warning(project) is None

    alias = tmp_path / "alias"
    _link_dir(home, alias)
    assert overbroad_root_warning(alias) is not None


def test_add_root_surfaces_overbroad_warning(tmp_path, monkeypatch):
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
    assert mgr.get_engine("overbroad", agent="cowork") is not None

    res = mgr.add_root("overbroad", str(home), writable=True)
    assert res["ok"] and "warning" in res
