"""Allowlist-enforced desktop automation through the Cua Driver CLI.

The driver is a local native sidecar. OpenWorker remains the approval authority
for every input action, and refreshes a deny-by-default capability manifest with
the exact executable paths, process IDs, and window IDs selected by the user.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import aisuite as ai
import yaml

from ..secrets import state_dir
from .tool_defs import approval_for_tool


_DRIVER_LOCK = threading.Lock()
_DAEMON_READY = False
_BLOCKED_WINDOWS_PROGRAM_NAMES = {
    "bash.exe",
    "cmd.exe",
    "conhost.exe",
    "cscript.exe",
    "explorer.exe",
    "java.exe",
    "javaw.exe",
    "mshta.exe",
    "node.exe",
    "powershell.exe",
    "pwsh.exe",
    "python.exe",
    "pythonw.exe",
    "regedit.exe",
    "rundll32.exe",
    "regsvr32.exe",
    "sh.exe",
    "wscript.exe",
    "wsl.exe",
    "wt.exe",
}
_BLOCKED_MACOS_APP_NAMES = {
    "automator.app",
    "finder.app",
    "iterm.app",
    "iterm2.app",
    "script editor.app",
    "shortcuts.app",
    "terminal.app",
    "warp.app",
    "wezterm.app",
}
_BLOCKED_MACOS_BUNDLE_IDS = {
    "com.apple.automator",
    "com.apple.finder",
    "com.apple.scripteditor2",
    "com.apple.shortcuts",
    "com.apple.terminal",
    "com.github.wez.wezterm",
    "com.googlecode.iterm2",
    "dev.warp.warp-stable",
}
_BLOCKED_MACOS_EXECUTABLE_NAMES = {
    "automator",
    "bash",
    "finder",
    "iterm2",
    "node",
    "osascript",
    "perl",
    "python",
    "python3",
    "ruby",
    "script editor",
    "shortcuts",
    "sh",
    "terminal",
    "warp",
    "wezterm-gui",
    "zsh",
}
_CONFIG_LOCK = threading.RLock()
_COMPUTER_USE_ENABLED = False
_ALLOWED_PROGRAMS: tuple[dict[str, str], ...] = ()
_TOKEN_LOCK = threading.Lock()
_TOKEN_LABELS: dict[tuple[str, int, int, str], str] = {}
_MACHO_MAGICS = {
    b"\xbe\xba\xfe\xca",
    b"\xbf\xba\xfe\xca",
    b"\xca\xfe\xba\xbe",
    b"\xca\xfe\xba\xbf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
}


def computer_use_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "unsupported"


def computer_use_supported() -> bool:
    return computer_use_platform() != "unsupported"


def _path_key(value: str | Path) -> str:
    path = os.path.normpath(os.path.expandvars(str(value or "").strip()))
    if path.startswith("\\\\?\\"):
        path = path[4:]
    return path.casefold() if computer_use_platform() == "windows" else path


def _program_name(path: str | Path) -> str:
    stem = Path(str(path)).stem.strip()
    return stem or Path(str(path)).name or "Program"


def _macos_app_details(path: Path) -> tuple[Path, str]:
    """Resolve one .app bundle to its contained executable and bundle id."""

    bundle = path.resolve(strict=True)
    if not bundle.is_dir() or bundle.suffix.casefold() != ".app":
        raise ValueError(f"only macOS .app bundles can be allowed: {path}")
    contents = (bundle / "Contents").resolve(strict=True)
    if not contents.is_relative_to(bundle):
        raise ValueError(f"application Contents directory escapes its bundle: {path}")
    info_path = contents / "Info.plist"
    try:
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ValueError(f"application has no valid Info.plist: {path}") from exc
    executable_name = str(info.get("CFBundleExecutable") or "").strip()
    if (
        not executable_name
        or executable_name in {".", ".."}
        or "/" in executable_name
        or "\\" in executable_name
    ):
        raise ValueError(f"application has no valid CFBundleExecutable: {path}")
    executable_root = (contents / "MacOS").resolve(strict=True)
    if not executable_root.is_relative_to(contents):
        raise ValueError(f"application executable directory escapes its bundle: {path}")
    executable = (executable_root / executable_name).resolve(strict=True)
    if not executable.is_relative_to(executable_root) or not executable.is_file():
        raise ValueError(f"application executable escapes its bundle: {path}")
    if not os.access(executable, os.X_OK):
        raise ValueError(f"application executable is not runnable: {executable}")
    try:
        with executable.open("rb") as stream:
            magic = stream.read(4)
    except OSError as exc:
        raise ValueError(f"application executable cannot be read: {executable}") from exc
    if magic not in _MACHO_MAGICS:
        raise ValueError(f"application executable is not a Mach-O binary: {executable}")
    return executable, str(info.get("CFBundleIdentifier") or "").strip().casefold()


def _program_executable(path: str | Path) -> Path:
    selected = Path(str(path)).expanduser()
    if computer_use_platform() == "windows":
        resolved = selected.resolve(strict=True)
        if not resolved.is_file() or resolved.suffix.casefold() != ".exe":
            raise ValueError(f"only Windows .exe programs can be allowed: {selected}")
        return resolved
    if computer_use_platform() == "macos":
        return _macos_app_details(selected)[0]
    raise ValueError("Computer use is supported only on macOS and Windows")


def program_path_available(path: str | Path) -> bool:
    try:
        _program_executable(path)
    except (OSError, ValueError):
        return False
    return True


def validate_allowed_programs(
    value: Any, *, require_exists: bool = True
) -> list[dict[str, str]]:
    platform = computer_use_platform()
    if platform == "unsupported":
        raise ValueError("Computer use is supported only on macOS and Windows")
    if not isinstance(value, list):
        raise ValueError("allowed_programs must be a list")
    if len(value) > 20:
        raise ValueError("at most 20 local programs can be allowed")
    programs: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str):
            raw_path, raw_name = item, ""
        elif isinstance(item, dict):
            raw_path = str(item.get("path") or "")
            raw_name = str(item.get("name") or "")
        else:
            raise ValueError("each allowed program must be a path or object")
        expanded = os.path.expandvars(os.path.expanduser(raw_path.strip()))
        path = Path(expanded)
        if not path.is_absolute():
            raise ValueError(f"program path must be absolute: {raw_path}")
        if platform == "windows":
            if path.suffix.casefold() != ".exe":
                raise ValueError(f"only Windows .exe programs can be allowed: {path}")
            if path.name.casefold() in _BLOCKED_WINDOWS_PROGRAM_NAMES:
                raise ValueError(f"system command interpreters cannot be allowed: {path.name}")
            if require_exists and not path.is_file():
                raise ValueError(f"program was not found: {path}")
            if path.exists():
                path = path.resolve()
        else:
            if path.suffix.casefold() != ".app":
                raise ValueError(f"only macOS .app bundles can be allowed: {path}")
            if path.name.casefold() in _BLOCKED_MACOS_APP_NAMES:
                raise ValueError(f"system automation applications cannot be allowed: {path.name}")
            if require_exists and not path.is_dir():
                raise ValueError(f"program was not found: {path}")
            if path.exists():
                executable, bundle_id = _macos_app_details(path)
                if (
                    executable.name.casefold() in _BLOCKED_MACOS_EXECUTABLE_NAMES
                    or bundle_id in _BLOCKED_MACOS_BUNDLE_IDS
                ):
                    raise ValueError(
                        f"system automation applications cannot be allowed: {path.name}"
                    )
                path = path.resolve()
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        name = re.sub(r"[\r\n\t]+", " ", raw_name).strip()[:80] or _program_name(path)
        programs.append({"name": name, "path": str(path)})
    return programs


def configure_computer_use(
    *, enabled: bool, allowed_programs: list[dict[str, str]]
) -> None:
    global _COMPUTER_USE_ENABLED, _ALLOWED_PROGRAMS
    with _CONFIG_LOCK:
        _COMPUTER_USE_ENABLED = bool(enabled)
        _ALLOWED_PROGRAMS = tuple(
            {"name": str(item["name"]), "path": str(item["path"])}
            for item in allowed_programs
        )


def computer_use_configuration() -> dict[str, Any]:
    with _CONFIG_LOCK:
        return {
            "enabled": _COMPUTER_USE_ENABLED,
            "allowed_programs": [dict(item) for item in _ALLOWED_PROGRAMS],
        }


def _effective_allowed_paths() -> dict[str, dict[str, Any]]:
    with _CONFIG_LOCK:
        if not _COMPUTER_USE_ENABLED:
            return {}
        configured = [dict(item) for item in _ALLOWED_PROGRAMS]
    entries: list[dict[str, Any]] = []
    for item in configured:
        try:
            executable = _program_executable(item["path"])
        except (OSError, ValueError):
            continue
        entries.append(
            {
                "name": item["name"],
                "path": item["path"],
                "executable": str(executable),
                "launch": True,
            }
        )
    return {_path_key(item["executable"]): item for item in entries}


def _allowed_program_for_path(program_path: str) -> Optional[dict[str, Any]]:
    requested = _path_key(program_path)
    return next(
        (
            item
            for item in _effective_allowed_paths().values()
            if _path_key(item["path"]) == requested
        ),
        None,
    )


def _element_text(element: dict[str, Any]) -> str:
    values = [
        element.get(key)
        for key in ("label", "name", "value", "text", "title", "description")
    ]
    return " ".join(str(value).strip() for value in values if str(value or "").strip())


def _remember_snapshot_tokens(
    session: str, pid: int, window_id: int, elements: list[dict[str, Any]]
) -> None:
    with _TOKEN_LOCK:
        stale = [
            key
            for key in _TOKEN_LABELS
            if key[0] == session and key[1] == int(pid) and key[2] == int(window_id)
        ]
        for key in stale:
            _TOKEN_LABELS.pop(key, None)
        for element in elements:
            token = str(element.get("element_token") or "").strip()
            if token:
                _TOKEN_LABELS[(session, int(pid), int(window_id), token)] = _element_text(
                    element
                )


def _consume_snapshot_token(
    session: str, pid: int, window_id: int, token: str
) -> Optional[str]:
    with _TOKEN_LOCK:
        return _TOKEN_LABELS.pop((session, int(pid), int(window_id), str(token)), None)


def _label_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _consume_matching_token(
    session: str,
    pid: int,
    window_id: int,
    token: str,
    element_label: str,
) -> Optional[dict[str, Any]]:
    remembered = _consume_snapshot_token(session, pid, window_id, token)
    if remembered is None:
        return {
            "ok": False,
            "error": "element_token is not from the immediately preceding computer_snapshot",
        }
    claimed = _label_key(element_label)
    if not claimed:
        return {"ok": False, "error": "element_label is required"}
    if claimed not in _label_key(remembered):
        return {
            "ok": False,
            "error": "element_label does not match the control bound to element_token",
        }
    return None


def _meta(name: str, *, approval: bool) -> ai.ToolMetadata:
    return ai.ToolMetadata(
        name=name,
        category="connector",
        risk_level="medium" if approval else "low",
        capabilities=["computer", "desktop"],
        requires_approval=approval,
    )


def _schema(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def _attach(fn: Callable[..., Any], schema: dict[str, Any]) -> Callable[..., Any]:
    name = schema["function"]["name"]
    approval = approval_for_tool(name, default=True)
    fn.__coworker_schema__ = schema
    fn.__aisuite_tool_metadata__ = _meta(name, approval=approval)
    fn.__doc__ = schema["function"]["description"]
    return fn


def _driver_path() -> Optional[Path]:
    override = str(os.environ.get("OPENWORKER_CUA_DRIVER") or "").strip()
    if override:
        return Path(override).expanduser()

    exe = "cua-driver.exe" if os.name == "nt" else "cua-driver"
    server_dir = Path(sys.executable).resolve().parent
    candidates = [
        server_dir / "cua-driver" / exe,
        server_dir / exe,
    ]
    on_path = shutil.which("cua-driver")
    if on_path:
        candidates.append(Path(on_path))
    return next((path for path in candidates if path.is_file()), None)


def _process_executable(pid: int) -> Optional[str]:
    """Resolve a local PID to its full executable without shelling out."""

    if int(pid) <= 0:
        return None
    if computer_use_platform() == "macos":
        try:
            import ctypes

            libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            proc_pidpath = libproc.proc_pidpath
            proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            proc_pidpath.restype = ctypes.c_int
            buffer = ctypes.create_string_buffer(4096)
            length = proc_pidpath(int(pid), buffer, len(buffer))
            if length <= 0:
                return None
            return os.fsdecode(buffer.value)
        except (AttributeError, OSError, ValueError):
            return None
    if computer_use_platform() != "windows":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        query_path = kernel32.QueryFullProcessImageNameW
        query_path.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        query_path.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        handle = open_process(0x1000, False, int(pid))
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not query_path(handle, 0, buffer, ctypes.byref(size)):
                return None
            return buffer.value
        finally:
            close_handle(handle)
    except (AttributeError, OSError, ValueError):
        return None


def _allowed_window_records(
    windows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed_paths = _effective_allowed_paths()
    if not allowed_paths:
        return []
    pid_paths: dict[int, Optional[str]] = {}
    allowed: list[dict[str, Any]] = []
    for window in windows:
        if not isinstance(window, dict):
            continue
        try:
            pid = int(window.get("pid") or 0)
            window_id = int(window.get("window_id") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or window_id <= 0:
            continue
        if pid not in pid_paths:
            pid_paths[pid] = _process_executable(pid)
        executable = pid_paths[pid]
        entry = allowed_paths.get(_path_key(executable or ""))
        if entry is None:
            continue
        allowed.append({**window, "_program": entry, "_executable": executable})
    return allowed


def _manifest_document(allowed_windows: list[dict[str, Any]]) -> str:
    allowed_paths = _effective_allowed_paths()
    app_resources = []
    for item in allowed_paths.values():
        executable = Path(str(item["executable"]))
        if not executable.is_file():
            continue
        app_resources.append(
            {
                "executable": str(executable),
                "launch": bool(item.get("launch")),
                "windows": "all",
                "terminate": "deny",
            }
        )
    pids = sorted(
        {
            int(window["pid"])
            for window in allowed_windows
            if int(window.get("pid") or 0) > 0
        }
    )
    exact_windows = sorted(
        {
            (int(window["pid"]), int(window["window_id"]))
            for window in allowed_windows
            if int(window.get("pid") or 0) > 0
            and int(window.get("window_id") or 0) > 0
        }
    )
    document = {
        "version": 3,
        "expires_after": "8h",
        "idle_timeout": "30m",
        "resources": {
            "apps": app_resources,
            "desktop": {
                "display": True,
                "applications": pids,
                "windows": [
                    {"pid": pid, "window_id": window_id}
                    for pid, window_id in exact_windows
                ],
            },
        },
        "allow": {
            "tools": [
                "list_windows",
                "get_window_state",
                "click",
                "type_text",
                "press_key",
            ]
        },
    }
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False)


def _manifest_path(_driver: Path) -> Path:
    override = str(os.environ.get("OPENWORKER_CUA_MANIFEST") or "").strip()
    if override:
        return Path(override).expanduser()
    return state_dir() / "cua-driver" / "computer-use-capabilities.yaml"


def _write_manifest(manifest: Path, content: str) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        manifest.parent.chmod(0o700)
    tmp = manifest.with_name(
        f".{manifest.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        tmp.write_text(content, encoding="utf-8")
        if os.name != "nt":
            tmp.chmod(0o600)
        tmp.replace(manifest)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _install_manifest(
    driver: Path, allowed_windows: list[dict[str, Any]], *, restart_if_running: bool
) -> bool:
    """Install a reviewed manifest and restart the immutable CUA daemon if needed."""

    global _DAEMON_READY
    manifest = _manifest_path(driver)
    content = _manifest_document(allowed_windows)
    try:
        current = manifest.read_text(encoding="utf-8") if manifest.is_file() else ""
    except OSError:
        current = ""
    if current == content:
        return False
    with _DRIVER_LOCK:
        try:
            current = manifest.read_text(encoding="utf-8") if manifest.is_file() else ""
        except OSError:
            current = ""
        if current == content:
            return False
        was_running = _daemon_running(driver)
        if was_running:
            try:
                subprocess.run(
                    [str(driver), "stop"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        _write_manifest(manifest, content)
        _DAEMON_READY = False
        if was_running and restart_if_running:
            _start_daemon(driver)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                time.sleep(0.3)
                if _daemon_running(driver):
                    _DAEMON_READY = True
                    break
            if not _DAEMON_READY:
                raise RuntimeError("Cua Driver did not restart after updating its allowlist")
    return True


def _sync_allowed_windows(
    windows: list[dict[str, Any]], *, restart_if_running: bool = True
) -> tuple[list[dict[str, Any]], bool]:
    allowed = _allowed_window_records(windows)
    driver = _driver_path()
    changed = False
    if driver is not None:
        changed = _install_manifest(
            driver, allowed, restart_if_running=restart_if_running
        )
    return allowed, changed


def reset_computer_use_permissions() -> dict[str, Any]:
    """Revoke bound PIDs/windows after a Settings change; the next list rebinds safely."""

    driver = _driver_path()
    if driver is None:
        return {"driver_installed": False, "driver_reloaded": False}
    changed = _install_manifest(driver, [], restart_if_running=True)
    return {"driver_installed": True, "driver_reloaded": changed}


def shutdown_computer_use() -> None:
    """Revoke the live manifest and stop the per-user daemon on server shutdown."""

    global _DAEMON_READY
    driver = _driver_path()
    if driver is None:
        return
    try:
        _install_manifest(driver, [], restart_if_running=False)
        subprocess.run(
            [str(driver), "stop"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        pass
    finally:
        _DAEMON_READY = False


def _daemon_args(driver: Path) -> list[str]:
    manifest = _manifest_path(driver)
    return [
        str(driver),
        "serve",
        "--permission-mode",
        "bounded",
        "--capability-manifest",
        str(manifest),
        "--approve-capability-manifest",
    ]


def _start_daemon(driver: Path) -> None:
    manifest = _manifest_path(driver)
    if not manifest.is_file():
        _write_manifest(manifest, _manifest_document([]))
    env = os.environ.copy()
    env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "false"
    env["CUA_TELEMETRY_ENABLED"] = "false"
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        _daemon_args(driver),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        creationflags=creationflags,
    )


def _daemon_running(driver: Path) -> bool:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            [str(driver), "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and "daemon is running" in (
        f"{proc.stdout}\n{proc.stderr}".casefold()
    )


def _ensure_daemon(driver: Path) -> None:
    global _DAEMON_READY
    if _DAEMON_READY:
        return
    with _DRIVER_LOCK:
        if _daemon_running(driver):
            _DAEMON_READY = True
            return
        _start_daemon(driver)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            time.sleep(0.4)
            if _daemon_running(driver):
                _DAEMON_READY = True
                return
    raise RuntimeError("Cua Driver daemon did not start in the interactive desktop session")


def _execute(
    driver: Path,
    tool: str,
    args: dict[str, Any],
    *,
    screenshot_path: Optional[Path] = None,
) -> subprocess.CompletedProcess[str]:
    command = [str(driver), tool]
    if screenshot_path is not None:
        command.extend(["--screenshot-out-file", str(screenshot_path)])
    env = os.environ.copy()
    env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "false"
    env["CUA_TELEMETRY_ENABLED"] = "false"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.run(
        command,
        input=json.dumps(args, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        env=env,
        creationflags=creationflags,
        check=False,
    )


def _looks_disconnected(proc: subprocess.CompletedProcess[str]) -> bool:
    text = f"{proc.stdout}\n{proc.stderr}".casefold()
    return any(
        marker in text
        for marker in (
            "daemon is not running",
            "failed to connect",
            "could not connect",
            "named pipe",
            "the system cannot find the file specified",
        )
    )


def _run_driver(
    tool: str,
    args: dict[str, Any],
    *,
    screenshot_path: Optional[Path] = None,
) -> dict[str, Any]:
    driver = _driver_path()
    if driver is None:
        return {
            "ok": False,
            "error": "Cua Driver is not installed. Reinstall the OpenWorker desktop build.",
        }
    try:
        _ensure_daemon(driver)
        proc = _execute(driver, tool, args, screenshot_path=screenshot_path)
        if _looks_disconnected(proc):
            with _DRIVER_LOCK:
                retry_probe = _execute(driver, tool, args, screenshot_path=screenshot_path)
                if _looks_disconnected(retry_probe):
                    _start_daemon(driver)
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        time.sleep(0.4)
                        retry_probe = _execute(
                            driver, tool, args, screenshot_path=screenshot_path
                        )
                        if not _looks_disconnected(retry_probe):
                            break
                proc = retry_probe
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Cua Driver tool {tool} timed out"}
    except (OSError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}

    raw = (proc.stdout or "").strip()
    if not raw:
        detail = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
        return {"ok": False, "error": f"Cua Driver {tool} failed: {detail}"}
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": f"Cua Driver {tool} returned invalid UTF-8 JSON",
            "detail": raw[:1000],
        }
    if not isinstance(result, dict):
        return {"ok": False, "error": f"Cua Driver {tool} returned no object"}
    if result.get("status") == "refused" or result.get("isError") is True:
        refusal = result.get("refusal") or {}
        return {
            "ok": False,
            "error": refusal.get("message") or result.get("error") or "action refused",
            "code": refusal.get("code") or result.get("code"),
        }
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": result.get("error") or f"Cua Driver exited {proc.returncode}",
        }
    return {"ok": True, **result}


def request_computer_use_permissions() -> dict[str, Any]:
    """Start Cua Driver's user-initiated macOS permission onboarding flow."""

    if computer_use_platform() != "macos":
        return {"ok": False, "error": "Permission setup is available only on macOS"}
    driver = _driver_path()
    if driver is None:
        return {
            "ok": False,
            "error": "Cua Driver is not installed. Reinstall the OpenWorker desktop build.",
        }
    env = os.environ.copy()
    env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "false"
    env["CUA_TELEMETRY_ENABLED"] = "false"
    try:
        subprocess.Popen(
            [str(driver), "permissions", "grant"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except OSError as exc:
        return {"ok": False, "error": f"could not open macOS permission setup: {exc}"}
    return {
        "ok": True,
        "message": "Follow the macOS prompts, then restart OpenWorker if requested.",
    }


def _session_label(session_id: Optional[str]) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", session_id or "openworker")
    return f"ow-{cleaned[:48]}"


def _capture_path(roots: Optional[list[Any]], pid: int, window_id: int) -> Optional[Path]:
    for root in roots or []:
        if bool(getattr(root, "writable", False)):
            directory = Path(getattr(root, "path")).resolve()
            directory.mkdir(parents=True, exist_ok=True)
            return directory / f"computer-{pid}-{window_id}-{int(time.time() * 1000)}.png"
    return None


def _prepare_allowed_window(pid: int, window_id: int) -> Optional[dict[str, Any]]:
    if not computer_use_configuration()["enabled"]:
        return {
            "ok": False,
            "error": "Computer use is disabled in Settings > Computer use.",
        }
    result = _run_driver("list_windows", {})
    if not result.get("ok"):
        return result
    try:
        allowed, _ = _sync_allowed_windows(result.get("windows") or [])
    except (OSError, RuntimeError, yaml.YAMLError) as exc:
        return {"ok": False, "error": f"could not apply computer-use allowlist: {exc}"}
    if not any(
        int(window.get("pid") or 0) == int(pid)
        and int(window.get("window_id") or 0) == int(window_id)
        for window in allowed
    ):
        return {
            "ok": False,
            "error": (
                "the requested window belongs to a program that is not allowed in "
                "Settings > Computer use"
            ),
        }
    return None


def make_computer_automation_tools(
    *, roots: Optional[list[Any]] = None, session_id: Optional[str] = None
) -> list[Callable[..., Any]]:
    """Create the allowlist-enforced Cua CLI surface exposed to Cowork sessions."""

    session = _session_label(session_id)
    tools: list[Callable[..., Any]] = []

    def _disabled_error() -> Optional[dict[str, Any]]:
        if computer_use_configuration()["enabled"]:
            return None
        return {
            "ok": False,
            "error": "Computer use is disabled in Settings > Computer use.",
        }

    def _public_window(window: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in window.items() if not key.startswith("_")}

    def computer_list_allowed_programs() -> dict[str, Any]:
        config = computer_use_configuration()
        return {"ok": True, **config}

    computer_list_allowed_programs.__name__ = "computer_list_allowed_programs"
    tools.append(
        _attach(
            computer_list_allowed_programs,
            _schema(
                "computer_list_allowed_programs",
                "List the local desktop programs the user explicitly allowed in "
                "Settings. Only these programs may be opened or controlled.",
                {},
                [],
            ),
        )
    )

    def computer_find_windows(app_name: str = "") -> dict[str, Any]:
        disabled = _disabled_error()
        if disabled:
            return disabled
        result = _run_driver("list_windows", {})
        if not result.get("ok"):
            return result
        try:
            allowed, reloaded = _sync_allowed_windows(result.get("windows") or [])
        except (OSError, RuntimeError, yaml.YAMLError) as exc:
            return {"ok": False, "error": f"could not apply computer-use allowlist: {exc}"}
        needle = str(app_name or "").strip().casefold()
        matched = [
            _public_window(window)
            for window in allowed
            if not needle
            or needle in f"{window.get('app_name', '')} {window.get('title', '')}".casefold()
        ]
        return {
            "ok": True,
            "app_name": app_name,
            "windows": matched,
            "allowlist_reloaded": reloaded,
        }

    computer_find_windows.__name__ = "computer_find_windows"
    tools.append(
        _attach(
            computer_find_windows,
            _schema(
                "computer_find_windows",
                "Find visible windows for an installed desktop application. Start "
                "here, then use the exact pid and window_id returned. Results are "
                "filtered to app_name before the model sees them.",
                {
                    "app_name": {
                        "type": "string",
                        "description": (
                            "Optional application or window-title substring. Leave "
                            "empty to list all allow-listed windows."
                        ),
                    }
                },
                [],
            ),
        )
    )

    def _prepare_window(pid: int, window_id: int) -> Optional[dict[str, Any]]:
        return _prepare_allowed_window(pid, window_id)

    def computer_snapshot(
        pid: int,
        window_id: int,
        query: str = "",
        max_elements: int = 600,
    ) -> dict[str, Any]:
        denied = _prepare_window(pid, window_id)
        if denied:
            return denied
        args: dict[str, Any] = {
            "pid": int(pid),
            "window_id": int(window_id),
            "session": session,
            "include_screenshot": False,
            "max_elements": max(1, min(int(max_elements or 600), 2000)),
        }
        if query:
            args["query"] = str(query)
        result = _run_driver("get_window_state", args)
        if not result.get("ok"):
            return result
        elements = [
            element
            for element in (result.get("elements") or [])
            if isinstance(element, dict)
        ]
        _remember_snapshot_tokens(session, int(pid), int(window_id), elements)
        # Structured elements are authoritative; avoid duplicating the same tree.
        result.pop("tree_markdown", None)
        result.pop("screenshot_png_b64", None)
        result.pop("_note", None)
        result["instruction"] = (
            "Use element_token from this fresh snapshot. Snapshot again after every action; "
            "tokens from older snapshots fail closed."
        )
        return result

    computer_snapshot.__name__ = "computer_snapshot"
    tools.append(
        _attach(
            computer_snapshot,
            _schema(
                "computer_snapshot",
                "Read a fresh accessibility snapshot of one exact desktop window. "
                "Call once before every action and again to verify the result. Use "
                "element_token, not a remembered index.",
                {
                    "pid": {"type": "integer"},
                    "window_id": {"type": "integer"},
                    "query": {
                        "type": "string",
                        "description": "Optional label substring to return matches plus ancestors.",
                    },
                    "max_elements": {"type": "integer"},
                },
                ["pid", "window_id"],
            ),
        )
    )

    def computer_screenshot(pid: int, window_id: int) -> dict[str, Any]:
        denied = _prepare_window(pid, window_id)
        if denied:
            return denied
        capture = _capture_path(roots, int(pid), int(window_id))
        if capture is None:
            return {
                "ok": False,
                "error": "no writable session folder is available for the screenshot",
            }
        result = _run_driver(
            "get_window_state",
            {
                "pid": int(pid),
                "window_id": int(window_id),
                "session": session,
                "include_screenshot": True,
                "max_elements": 1,
            },
            screenshot_path=capture,
        )
        if not result.get("ok"):
            return result
        result.pop("tree_markdown", None)
        result.pop("screenshot_png_b64", None)
        result.pop("elements", None)
        result.pop("_note", None)
        if not capture.is_file():
            return {"ok": False, "error": "Cua Driver did not write the screenshot"}
        result["screenshot_path"] = str(capture)
        return result

    computer_screenshot.__name__ = "computer_screenshot"
    tools.append(
        _attach(
            computer_screenshot,
            _schema(
                "computer_screenshot",
                "Save a screenshot of one exact allow-listed desktop window to the "
                "writable session folder after approval.",
                {
                    "pid": {"type": "integer"},
                    "window_id": {"type": "integer"},
                },
                ["pid", "window_id"],
            ),
        )
    )

    def computer_open_program(program_path: str) -> dict[str, Any]:
        disabled = _disabled_error()
        if disabled:
            return disabled
        allowed = _allowed_program_for_path(program_path)
        if allowed is None or not bool(allowed.get("launch")):
            return {
                "ok": False,
                "error": "program is not allowed in Settings > Computer use",
            }
        path = Path(str(allowed["path"]))
        executable = Path(str(allowed["executable"]))
        if not program_path_available(path):
            return {"ok": False, "error": f"program was not found: {path}"}
        launch_pid: Optional[int] = None
        try:
            if computer_use_platform() == "macos":
                opened = subprocess.run(
                    ["/usr/bin/open", str(path)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    check=False,
                )
                if opened.returncode != 0:
                    return {
                        "ok": False,
                        "error": f"could not open {allowed['name']}: open exited {opened.returncode}",
                    }
            else:
                process = subprocess.Popen(
                    [str(executable)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                launch_pid = process.pid
        except OSError as exc:
            return {"ok": False, "error": f"could not open {allowed['name']}: {exc}"}
        time.sleep(1.5)
        result = _run_driver("list_windows", {})
        windows: list[dict[str, Any]] = []
        reloaded = False
        if result.get("ok"):
            try:
                current, reloaded = _sync_allowed_windows(result.get("windows") or [])
                windows = [
                    _public_window(window)
                    for window in current
                    if _path_key(window.get("_executable") or "")
                    == _path_key(executable)
                ]
            except (OSError, RuntimeError, yaml.YAMLError):
                pass
        return {
            "ok": True,
            "program": {"name": allowed["name"], "path": str(path)},
            "pid": int(windows[0]["pid"]) if windows else launch_pid,
            "windows": windows,
            "allowlist_reloaded": reloaded,
            "instruction": (
                "Call computer_find_windows, then take a fresh computer_snapshot "
                "before acting."
            ),
        }

    computer_open_program.__name__ = "computer_open_program"
    tools.append(
        _attach(
            computer_open_program,
            _schema(
                "computer_open_program",
                "Open one local desktop program that the user selected in Settings > "
                "Computer use. Call computer_list_allowed_programs first and pass an "
                "exact returned path.",
                {"program_path": {"type": "string"}},
                ["program_path"],
            ),
        )
    )

    _ACTION_TARGET = {
        "pid": {"type": "integer"},
        "window_id": {"type": "integer"},
        "element_token": {
            "type": "string",
            "description": "Fresh token returned by computer_snapshot.",
        },
        "element_label": {
            "type": "string",
            "description": (
                "Visible control label from the same snapshot. It is shown for "
                "approval and verified against element_token."
            ),
        },
        "delivery_mode": {
            "type": "string",
            "enum": ["background", "foreground"],
            "description": (
                "Always try background first. Retry foreground only after "
                "background_unavailable or a verified no-op."
            ),
        },
    }

    def _target_args(
        pid: int,
        window_id: int,
        element_token: str,
        delivery_mode: str,
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        args: dict[str, Any] = {
            "pid": int(pid),
            "window_id": int(window_id),
            "session": session,
            "delivery_mode": (
                "foreground" if delivery_mode == "foreground" else "background"
            ),
        }
        if not element_token:
            return None, {
                "ok": False,
                "error": "take a fresh computer_snapshot and provide element_token",
            }
        args["element_token"] = element_token
        return args, None

    def computer_click(
        pid: int,
        window_id: int,
        element_token: str = "",
        element_label: str = "",
        delivery_mode: str = "background",
    ) -> dict[str, Any]:
        denied = _prepare_window(pid, window_id)
        if denied:
            return denied
        if not element_token:
            return {
                "ok": False,
                "error": (
                    "coordinate-only clicks are disabled for direct desktop actions; "
                    "take a fresh computer_snapshot and provide element_token"
                ),
            }
        label_error = _consume_matching_token(
            session, pid, window_id, element_token, element_label
        )
        if label_error:
            return label_error
        args, error = _target_args(pid, window_id, element_token, delivery_mode)
        if error:
            return error
        return _run_driver("click", args or {})

    computer_click.__name__ = "computer_click"
    tools.append(
        _attach(
            computer_click,
            _schema(
                "computer_click",
                "Click one labelled element in an allow-listed desktop program after "
                "approval. A fresh element_token is mandatory; coordinates are not "
                "accepted. Snapshot again to verify the result.",
                _ACTION_TARGET,
                ["pid", "window_id", "element_token", "element_label"],
            ),
        )
    )

    def computer_type_text(
        pid: int,
        window_id: int,
        text: str,
        element_token: str = "",
        element_label: str = "",
        delivery_mode: str = "background",
    ) -> dict[str, Any]:
        denied = _prepare_window(pid, window_id)
        if denied:
            return denied
        label_error = _consume_matching_token(
            session, pid, window_id, element_token, element_label
        )
        if label_error:
            return label_error
        args, error = _target_args(pid, window_id, element_token, delivery_mode)
        if error:
            return error
        args = args or {}
        args["text"] = str(text)
        return _run_driver("type_text", args)

    computer_type_text.__name__ = "computer_type_text"
    tools.append(
        _attach(
            computer_type_text,
            _schema(
                "computer_type_text",
                "Type text into one labelled field in an allow-listed desktop program "
                "after approval. A fresh element_token is mandatory; snapshot "
                "afterward to verify the result.",
                {**_ACTION_TARGET, "text": {"type": "string"}},
                ["pid", "window_id", "element_token", "element_label", "text"],
            ),
        )
    )

    def computer_press_key(
        pid: int,
        window_id: int,
        key: str,
        element_token: str = "",
        element_label: str = "",
        delivery_mode: str = "background",
        modifiers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        denied = _prepare_window(pid, window_id)
        if denied:
            return denied
        if not element_token:
            return {
                "ok": False,
                "error": (
                    "coordinate-only key presses are disabled; take a fresh "
                    "computer_snapshot and provide element_token"
                ),
            }
        label_error = _consume_matching_token(
            session, pid, window_id, element_token, element_label
        )
        if label_error:
            return label_error
        args, error = _target_args(pid, window_id, element_token, delivery_mode)
        if error:
            return error
        args = args or {}
        args["key"] = str(key)
        args["modifiers"] = list(modifiers or [])
        return _run_driver("press_key", args)

    computer_press_key.__name__ = "computer_press_key"
    tools.append(
        _attach(
            computer_press_key,
            _schema(
                "computer_press_key",
                "Press a key in an allow-listed desktop program after approval using "
                "a fresh labelled element_token. Snapshot afterward to verify the "
                "result.",
                {
                    **_ACTION_TARGET,
                    "key": {"type": "string"},
                    "modifiers": {"type": "array", "items": {"type": "string"}},
                },
                ["pid", "window_id", "element_token", "element_label", "key"],
            ),
        )
    )

    return tools
