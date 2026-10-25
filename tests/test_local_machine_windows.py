"""Machine facts on Windows: no console window flashes, and a readable processor name."""

from __future__ import annotations

import subprocess
import sys
import types

from coworker.providers import local_machine


def test_console_programs_run_without_a_window_on_windows(monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="24564\n", stderr="")

    monkeypatch.setattr(local_machine, "_NO_WINDOW", {"creationflags": 0x08000000})
    monkeypatch.setattr(local_machine.subprocess, "run", fake_run)
    monkeypatch.setattr(local_machine.shutil, "which", lambda name: "C:/nvidia-smi.exe")
    assert local_machine.nvidia_gpu_memory_bytes() == 24564 * 1024 * 1024
    assert seen["creationflags"] == 0x08000000


def test_no_window_flag_only_on_windows():
    expected = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    assert local_machine._NO_WINDOW == expected


def test_windows_processor_name_comes_from_the_registry(monkeypatch):
    fake = types.SimpleNamespace(
        HKEY_LOCAL_MACHINE=object(),
        OpenKey=lambda root, path: ("key", path),
        QueryValueEx=lambda key, name: ("Intel(R) Core(TM)  i7-12700H  ", 1),
        CloseKey=lambda key: None,
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    assert local_machine._windows_processor() == "Intel(R) Core(TM) i7-12700H"
    monkeypatch.setitem(sys.modules, "winreg", None)  # not Windows: import fails
    assert local_machine._windows_processor() == ""
