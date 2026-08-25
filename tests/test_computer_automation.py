"""Cua Driver adapter: allowlists, fresh-state tokens, and approval metadata."""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

import pytest

from coworker.connectors import computer_automation
from coworker.roots import RootDir


def _platform(monkeypatch, name):
    monkeypatch.setattr(computer_automation, "computer_use_platform", lambda: name)


def _mac_app(tmp_path, name="Writer", bundle_id="com.example.writer"):
    app = tmp_path / f"{name}.app"
    executable = app / "Contents" / "MacOS" / name
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"\xcf\xfa\xed\xfe" + b"test")
    executable.chmod(0o755)
    with (app / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump(
            {"CFBundleExecutable": name, "CFBundleIdentifier": bundle_id}, stream
        )
    return app, executable


def _tools(tmp_path, monkeypatch, fake):
    computer_automation._TOKEN_LABELS.clear()
    computer_automation.configure_computer_use(enabled=True, allowed_programs=[])
    monkeypatch.setattr(computer_automation, "_run_driver", fake)
    monkeypatch.setattr(
        computer_automation,
        "_prepare_allowed_window",
        lambda _pid, _window_id: None,
    )
    monkeypatch.setattr(
        computer_automation,
        "_sync_allowed_windows",
        lambda windows, **_kwargs: (list(windows), False),
    )
    return {
        tool.__name__: tool
        for tool in computer_automation.make_computer_automation_tools(
            roots=[RootDir(tmp_path, writable=True)], session_id="test session"
        )
    }


def test_validate_allowed_programs_rejects_interpreters_and_deduplicates(
    tmp_path, monkeypatch
):
    _platform(monkeypatch, "windows")
    editor = tmp_path / "editor.exe"
    editor.write_bytes(b"MZ")
    command = tmp_path / "powershell.exe"
    command.write_bytes(b"MZ")

    assert computer_automation.validate_allowed_programs(
        [
            {"name": "Editor", "path": str(editor)},
            {"name": "Duplicate", "path": str(editor)},
        ]
    ) == [{"name": "Editor", "path": str(editor)}]
    with pytest.raises(ValueError, match="cannot be allowed"):
        computer_automation.validate_allowed_programs([str(command)])
    with pytest.raises(ValueError, match="only Windows .exe"):
        computer_automation.validate_allowed_programs([str(tmp_path / "notes.txt")])


def test_runtime_allowlist_binds_exact_executable_process_and_window(tmp_path, monkeypatch):
    _platform(monkeypatch, "windows")
    editor = tmp_path / "editor.exe"
    other = tmp_path / "other.exe"
    editor.write_bytes(b"MZ")
    other.write_bytes(b"MZ")
    computer_automation.configure_computer_use(
        enabled=True,
        allowed_programs=[{"name": "Editor", "path": str(editor)}],
    )
    monkeypatch.setattr(
        computer_automation,
        "_process_executable",
        lambda pid: str(editor if pid == 7 else other),
    )

    allowed = computer_automation._allowed_window_records(
        [
            {"app_name": "editor.exe", "pid": 7, "window_id": 9, "title": "Draft"},
            {"app_name": "other.exe", "pid": 8, "window_id": 10, "title": "Private"},
        ]
    )
    assert [(item["pid"], item["window_id"]) for item in allowed] == [(7, 9)]
    manifest = computer_automation._manifest_document(allowed)
    assert str(editor) in manifest
    assert "expires_after: 8h" in manifest
    assert "idle_timeout: 30m" in manifest
    assert "applications:\n    - 7" in manifest
    assert "window_id: 9" in manifest
    assert "Private" not in manifest and "window_id: 10" not in manifest


def test_macos_app_allowlist_resolves_inner_executable_and_rejects_automation_apps(
    tmp_path, monkeypatch
):
    _platform(monkeypatch, "macos")
    writer, executable = _mac_app(tmp_path)
    terminal, _ = _mac_app(tmp_path, "Terminal", "com.apple.Terminal")

    assert computer_automation.validate_allowed_programs([str(writer)]) == [
        {"name": "Writer", "path": str(writer.resolve())}
    ]
    assert computer_automation._program_executable(writer) == executable.resolve()
    assert computer_automation.program_path_available(writer) is True
    with pytest.raises(ValueError, match="cannot be allowed"):
        computer_automation.validate_allowed_programs([str(terminal)])
    with pytest.raises(ValueError, match="only macOS .app"):
        computer_automation.validate_allowed_programs([str(executable)])

    script_app, script_executable = _mac_app(
        tmp_path, "ScriptWrapped", "com.example.scriptwrapped"
    )
    script_executable.write_bytes(b"#!/bin/sh\n")
    with pytest.raises(ValueError, match="not a Mach-O"):
        computer_automation.validate_allowed_programs([str(script_app)])


def test_macos_runtime_allowlist_matches_pid_executable_and_manifest(tmp_path, monkeypatch):
    _platform(monkeypatch, "macos")
    writer, executable = _mac_app(tmp_path)
    other, other_executable = _mac_app(tmp_path, "Other", "com.example.other")
    computer_automation.configure_computer_use(
        enabled=True,
        allowed_programs=[{"name": "Writer", "path": str(writer)}],
    )
    monkeypatch.setattr(
        computer_automation,
        "_process_executable",
        lambda pid: str(executable if pid == 7 else other_executable),
    )

    allowed = computer_automation._allowed_window_records(
        [
            {"app_name": "Writer", "pid": 7, "window_id": 9, "title": "Draft"},
            {"app_name": "Other", "pid": 8, "window_id": 10, "title": "Private"},
        ]
    )
    assert [(item["pid"], item["window_id"]) for item in allowed] == [(7, 9)]
    manifest = computer_automation._manifest_document(allowed)
    assert str(executable.resolve()) in manifest
    assert str(other.resolve()) not in manifest


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS libproc only")
def test_macos_process_executable_resolves_current_pid():
    executable = computer_automation._process_executable(os.getpid())
    assert executable is not None
    assert Path(executable).is_file()
    assert Path(executable).name.casefold().startswith("python")


def test_macos_program_launch_uses_launch_services(tmp_path, monkeypatch):
    _platform(monkeypatch, "macos")
    writer, _ = _mac_app(tmp_path)
    calls = []
    tools = _tools(
        tmp_path,
        monkeypatch,
        lambda tool, _args, **_kwargs: {"ok": True, "windows": []}
        if tool == "list_windows"
        else {"ok": True},
    )
    computer_automation.configure_computer_use(
        enabled=True,
        allowed_programs=[{"name": "Writer", "path": str(writer)}],
    )
    monkeypatch.setattr(computer_automation.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        computer_automation.subprocess,
        "run",
        lambda args, **_kwargs: calls.append(args)
        or type("Completed", (), {"returncode": 0})(),
    )

    result = tools["computer_open_program"](str(writer))

    assert result["ok"] is True
    assert calls == [["/usr/bin/open", str(writer)]]


def test_daemon_uses_bounded_permission_mode(tmp_path):
    driver = tmp_path / "cua-driver.exe"
    args = computer_automation._daemon_args(driver)
    assert args[args.index("--permission-mode") + 1] == "bounded"
    assert "--dangerously-bypass-approvals" not in args


def test_runtime_manifest_lives_outside_signed_app_bundle(tmp_path, monkeypatch):
    state = tmp_path / "state"
    driver = tmp_path / "OpenWorker.app" / "Contents" / "Resources" / "cua-driver"
    monkeypatch.setenv("COWORKER_STATE_DIR", str(state))
    monkeypatch.delenv("OPENWORKER_CUA_MANIFEST", raising=False)

    manifest = computer_automation._manifest_path(driver)
    computer_automation._write_manifest(manifest, "version: 3\n")

    assert manifest == state / "cua-driver" / "computer-use-capabilities.yaml"
    assert manifest.read_text() == "version: 3\n"
    assert not driver.parent.exists()


def test_shutdown_revokes_manifest_and_stops_daemon(tmp_path, monkeypatch):
    driver = tmp_path / "cua-driver.exe"
    driver.write_bytes(b"MZ")
    calls = []
    monkeypatch.setattr(computer_automation, "_driver_path", lambda: driver)
    monkeypatch.setattr(
        computer_automation,
        "_install_manifest",
        lambda selected, windows, restart_if_running: calls.append(
            ("manifest", selected, windows, restart_if_running)
        ),
    )
    monkeypatch.setattr(
        computer_automation.subprocess,
        "run",
        lambda args, **_kwargs: calls.append(("run", args)),
    )

    computer_automation.shutdown_computer_use()
    assert calls[0] == ("manifest", driver, [], False)
    assert calls[1] == ("run", [str(driver), "stop"])


def test_find_windows_filters_before_returning(tmp_path, monkeypatch):
    calls = []

    def fake(tool, args, **kwargs):
        calls.append((tool, args, kwargs))
        return {
            "ok": True,
            "windows": [
                {"app_name": "editor.exe", "title": "Quarterly report", "pid": 7},
                {"app_name": "private.exe", "title": "Private window", "pid": 8},
            ],
        }

    tools = _tools(tmp_path, monkeypatch, fake)
    out = tools["computer_find_windows"]("report")
    assert out["windows"] == [
        {"app_name": "editor.exe", "title": "Quarterly report", "pid": 7}
    ]
    assert calls[0][0:2] == ("list_windows", {})


def test_snapshot_uses_exact_window_and_rotates_element_tokens(tmp_path, monkeypatch):
    calls = []

    def fake(tool, args, **kwargs):
        calls.append((tool, args, kwargs))
        return {
            "ok": True,
            "pid": 7,
            "window_id": 9,
            "snapshot_id": "fresh",
            "elements": [{"element_token": "fresh:0", "label": "Save"}],
            "tree_markdown": "duplicate",
            "screenshot_png_b64": "large",
        }

    tools = _tools(tmp_path, monkeypatch, fake)
    out = tools["computer_snapshot"](7, 9, query="Save")
    assert out["snapshot_id"] == "fresh"
    assert "tree_markdown" not in out and "screenshot_png_b64" not in out
    assert calls[0][1]["include_screenshot"] is False
    assert calls[0][1]["pid"] == 7 and calls[0][1]["window_id"] == 9
    assert calls[0][1]["query"] == "Save"


def test_screenshot_writes_only_to_the_session_root(tmp_path, monkeypatch):
    def fake(_tool, _args, **kwargs):
        kwargs["screenshot_path"].write_bytes(b"png")
        return {"ok": True, "screenshot_png_b64": "discarded", "elements": []}

    tools = _tools(tmp_path, monkeypatch, fake)
    out = tools["computer_screenshot"](7, 9)
    screenshot = tmp_path / out["screenshot_path"]
    assert screenshot.resolve().is_relative_to(tmp_path.resolve())
    assert screenshot.read_bytes() == b"png"
    assert "screenshot_png_b64" not in out


def test_all_desktop_input_and_program_launches_require_approval(tmp_path, monkeypatch):
    tools = _tools(tmp_path, monkeypatch, lambda *a, **k: {"ok": True})
    for name in (
        "computer_list_allowed_programs",
        "computer_find_windows",
        "computer_snapshot",
    ):
        assert tools[name].__aisuite_tool_metadata__.requires_approval is False
    for name in (
        "computer_open_program",
        "computer_screenshot",
        "computer_click",
        "computer_type_text",
        "computer_press_key",
    ):
        assert tools[name].__aisuite_tool_metadata__.requires_approval is True


def test_input_actions_require_fresh_labelled_token(tmp_path, monkeypatch):
    calls = []

    def fake(tool, args, **kwargs):
        calls.append((tool, args))
        if tool == "get_window_state":
            return {
                "ok": True,
                "elements": [{"element_token": "fresh:1", "label": "Message"}],
            }
        return {"ok": True}

    tools = _tools(tmp_path, monkeypatch, fake)
    assert "preceding" in tools["computer_click"](
        7, 9, element_token="old:1", element_label="Message"
    )["error"]
    assert "preceding" in tools["computer_type_text"](
        7, 9, text="hello", element_token="old:1", element_label="Message"
    )["error"]

    tools["computer_snapshot"](7, 9)
    assert "does not match" in tools["computer_click"](
        7, 9, element_token="fresh:1", element_label="Delete"
    )["error"]
    tools["computer_snapshot"](7, 9)
    assert tools["computer_click"](
        7, 9, element_token="fresh:1", element_label="Message"
    )["ok"] is True
    assert "preceding" in tools["computer_click"](
        7, 9, element_token="fresh:1", element_label="Message"
    )["error"]
    tools["computer_snapshot"](7, 9)
    assert tools["computer_type_text"](
        7,
        9,
        text="hello\nworld",
        element_token="fresh:1",
        element_label="Message",
    )["ok"] is True
    tools["computer_snapshot"](7, 9)
    assert tools["computer_press_key"](
        7,
        9,
        key="Enter",
        element_token="fresh:1",
        element_label="Message",
        modifiers=[],
    )["ok"] is True
    assert [tool for tool, _args in calls if tool != "get_window_state"] == [
        "click",
        "type_text",
        "press_key",
    ]


def test_integration_connector_filter_exposes_only_generic_computer_tools(
    tmp_path, monkeypatch
):
    from coworker.connectors.integration_tools import make_integration_tools
    from coworker.secrets import SecretStore

    monkeypatch.setattr(
        computer_automation, "_run_driver", lambda *a, **k: {"ok": True}
    )
    tools = make_integration_tools(
        SecretStore(tmp_path / "secrets.json"), enabled_connectors={"computer"}
    )
    assert {tool.__name__ for tool in tools} == {
        "computer_list_allowed_programs",
        "computer_find_windows",
        "computer_snapshot",
        "computer_screenshot",
        "computer_open_program",
        "computer_click",
        "computer_type_text",
        "computer_press_key",
    }
