"""The recent-folders list must not make macOS ask about Desktop, Documents or Downloads."""

from pathlib import Path

from coworker.server import manager as manager_mod


def test_folders_macos_guards_are_reported_present_without_looking(tmp_path, monkeypatch):
    monkeypatch.setattr(manager_mod.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    looked: list[str] = []
    real_is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda self: looked.append(str(self)) or real_is_dir(self))
    for name in ("Desktop", "Documents", "Downloads"):
        assert manager_mod.folder_present(str(tmp_path / name / "project")) is True
        assert manager_mod.folder_present(str(tmp_path / name)) is True
    assert looked == []  # never stat()ed: that is what puts the prompt up
    (tmp_path / "code").mkdir()
    assert manager_mod.folder_present(str(tmp_path / "code")) is True
    assert manager_mod.folder_present(str(tmp_path / "gone")) is False
    assert looked  # ordinary folders are still checked


def test_elsewhere_every_folder_is_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(manager_mod.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert manager_mod.folder_present(str(tmp_path / "Downloads" / "project")) is False
    (tmp_path / "Downloads" / "project").mkdir(parents=True)
    assert manager_mod.folder_present(str(tmp_path / "Downloads" / "project")) is True
