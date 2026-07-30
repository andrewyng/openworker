"""Run browser actions through the Browser Use CLI.

Takes Python on stdin, helpers pre-imported. Its daemon owns the browser, so there's
nothing to hold open here.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

# read per call so the pytest guard and runtime overrides both work
def _cli_name() -> str:
    return os.environ.get("COWORKER_BROWSER_USE_CLI", "browser-use")
DEFAULT_TIMEOUT_S = 120.0
# cloud cold-starts a container
PROVISION_TIMEOUT_S = 180.0
# tag it, the CLI and the page print too
RESULT_PREFIX = "__COWORKER__"


class BrowserCLIError(RuntimeError):
    pass


def _tool_bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


def available() -> Optional[str]:
    """Path to the CLI, or None if it isn't installed."""
    # sidecar often runs without ~/.local/bin on PATH
    path = shutil.which(_cli_name())
    if path:
        return path
    candidate = _tool_bin_dir() / _cli_name()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


# bump on purpose, 0.13.3 swapped the CLI's guts
CLI_PIN = "browser-use==0.13.7"

_INSTALL_LOCK = threading.Lock()
_INSTALL_ATTEMPTED = False


def ensure_cli() -> Optional[str]:
    """CLI path, installing on first use if missing. One attempt per process."""
    global _INSTALL_ATTEMPTED
    path = available()
    # overridden name = caller owns the binary
    if path is not None or _cli_name() != "browser-use":
        return path
    with _INSTALL_LOCK:
        path = available()
        if path is not None or _INSTALL_ATTEMPTED:
            return path
        _INSTALL_ATTEMPTED = True
        for command in (["uv", "tool", "install", CLI_PIN], ["pipx", "install", CLI_PIN]):
            installer = shutil.which(command[0])
            if installer is None:
                continue
            try:
                done = subprocess.run(  # noqa: S603 - fixed installer commands
                    [installer, *command[1:]], capture_output=True, text=True, timeout=300
                )
            except Exception:  # noqa: BLE001 - fall through to the next installer
                continue
            if done.returncode == 0:
                path = available()
                if path is not None:
                    return path
        return None


def _setup_error(detail: str) -> dict[str, str]:
    return {
        "error": (
            "Browser automation requires the Browser Use CLI. OpenWorker tried to install "
            "it automatically but could not (that needs `uv` or `pipx` and network). "
            "Install it with `uv tool install browser-use` and make sure "
            f"`{_cli_name()}` is on PATH."
        ),
        "details": detail,
    }


def session_name() -> str:
    """The daemon this session talks to; empty means local Chrome.

    Config gates cloud, not env -- a stray BU_NAME shouldn't bill anyone.
    """
    from ..config import load_config

    cfg = load_config()
    if str(getattr(cfg, "browser_backend", "local") or "local").strip().lower() != "cloud":
        return ""
    name = (os.environ.get("BU_NAME") or "").strip()
    return name or str(getattr(cfg, "browser_cloud_name", "") or "coworker").strip()


def _env() -> dict[str, str]:
    name = session_name()
    return {**os.environ, **({"BU_NAME": name} if name else {})}


# the CLI runs one program then exits, so feed it a loop -- keeps vars between calls
# also raises helpers._send off its 5s ipc timeout. no retry, a timed-out cdp call may have landed
_SESSION_BOOT = """import io, json, traceback
from contextlib import redirect_stdout, redirect_stderr
try:
    import browser_harness.helpers as _bh_helpers
    from browser_harness import _ipc as _bh_ipc

    def _bu_patched_send(req):
        c, token = _bh_ipc.connect(_bh_helpers.NAME, timeout=30.0)
        try:
            r = _bh_ipc.request(c, token, req)
        finally:
            c.close()
        if "error" in r:
            raise RuntimeError(r["error"])
        return r

    _bh_helpers._send = _bu_patched_send
except Exception:
    pass  # tests run plain python3 as the CLI
_bu_ns = dict(globals())
while True:
    with open({inp!r}) as _bu_f:
        _bu_code = _bu_f.read()
    if not _bu_code.strip():
        break
    _bu_buf = io.StringIO()
    try:
        with redirect_stdout(_bu_buf), redirect_stderr(_bu_buf):
            exec(_bu_code, _bu_ns)
        _bu_err = 0
    except BaseException:
        traceback.print_exc(file=_bu_buf)
        _bu_err = 1
    with open({outp!r}, "w") as _bu_f:
        _bu_f.write(json.dumps({{"exit": _bu_err, "out": _bu_buf.getvalue()}}))
"""


class _Session:
    """One persistent CLI interpreter; killed and respawned on timeout."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._dir: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _start(self) -> None:
        import tempfile

        path = ensure_cli()
        if path is None:
            raise FileNotFoundError(f"{_cli_name()} not found on PATH and auto-install failed")
        self._dir = tempfile.mkdtemp(prefix="coworker-browser-session-")
        inp, outp = os.path.join(self._dir, "in"), os.path.join(self._dir, "out")
        os.mkfifo(inp)
        os.mkfifo(outp)
        boot = _SESSION_BOOT.format(inp=inp, outp=outp)
        self._proc = subprocess.Popen(  # noqa: S603 - fixed executable, code is ours
            [path],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=_env(),
        )
        assert self._proc.stdin is not None
        self._proc.stdin.write(boot.encode())
        self._proc.stdin.close()

    def _write(self, fifo: str, text: str, deadline: float) -> None:
        while True:
            try:
                fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:  # no reader yet
                if time.monotonic() >= deadline or not self.alive:
                    raise TimeoutError("browser session did not accept the code")
                time.sleep(0.05)
                continue
            with os.fdopen(fd, "w") as handle:
                handle.write(text)
            return

    def _read(self, fifo: str, deadline: float) -> str:
        fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        chunks: list[bytes] = []
        try:
            while True:
                try:
                    chunk: Optional[bytes] = os.read(fd, 65536)
                except BlockingIOError:
                    chunk = None
                if chunk:
                    chunks.append(chunk)
                elif chunk is not None and chunks:
                    return b"".join(chunks).decode("utf-8", errors="replace")
                if time.monotonic() >= deadline:
                    raise TimeoutError("browser session did not answer in time")
                if not chunk:
                    time.sleep(0.02)
        finally:
            os.close(fd)

    def run(self, code: str, timeout: float) -> dict[str, Any]:
        with self._lock:
            if not self.alive:
                self.close()
                self._start()
            assert self._dir is not None
            deadline = time.monotonic() + timeout
            inp, outp = os.path.join(self._dir, "in"), os.path.join(self._dir, "out")
            try:
                self._write(inp, code, deadline)
                payload = json.loads(self._read(outp, deadline))
            except TimeoutError:
                # wedged mid-exec. respawn keeps the browser, loses the namespace
                self.close()
                return {
                    "error": (
                        f"browser-use timed out after {timeout:.0f}s; the session was "
                        "restarted (the browser survives, session variables were lost)"
                    )
                }
            out = {
                "returncode": int(payload.get("exit", 1)),
                "stdout": str(payload.get("out", "")).strip(),
                "stderr": "",
            }
            if out["returncode"] != 0:
                out["error"] = out["stdout"] or "browser-use code raised"
            return out

    def close(self) -> None:
        proc, self._proc = self._proc, None
        directory, self._dir = self._dir, None
        if proc is not None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 - teardown only
                pass
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)


_SESSION = _Session()
atexit.register(_SESSION.close)


def run_code(code: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Run `code` in the persistent session; return its output and exit flag."""
    if ensure_cli() is None:
        return _setup_error(f"{_cli_name()} not found on PATH and auto-install failed")
    if not hasattr(os, "mkfifo"):  # windows: same contract, no persistence
        return _run_code_oneshot(code, timeout=timeout)
    try:
        return _SESSION.run(code, timeout)
    except FileNotFoundError:
        return _setup_error(f"{_cli_name()} not found on PATH")
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, never raised
        _SESSION.close()
        return {"error": f"browser session failed: {exc}"}


def _run_code_oneshot(code: str, *, timeout: float) -> dict[str, Any]:
    path = ensure_cli()
    try:
        completed = subprocess.run(  # noqa: S603 - fixed executable, code is ours
            [path],
            input=code,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_env(),
        )
    except subprocess.TimeoutExpired:
        return {"error": f"browser-use timed out after {timeout:.0f}s"}
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, never raised
        return _setup_error(str(exc))
    out = {
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }
    if completed.returncode != 0:
        out["error"] = out["stderr"] or f"browser-use exited with code {completed.returncode}"
    return out


def reset_session() -> None:
    """Kill the interpreter. For tests, and after BU_NAME changes."""
    _SESSION.close()


def call(body: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Run a snippet ending in `emit(...)` and return the result it printed."""
    code = f"import json\n{body}\n"
    out = run_code(code, timeout=timeout)
    if "error" in out and out.get("returncode") not in (0, None):
        return {"error": out["error"], "stderr": out.get("stderr", "")[:2000]}
    if "error" in out and "returncode" not in out:
        return out
    for line in reversed((out.get("stdout") or "").splitlines()):
        if line.startswith(RESULT_PREFIX):
            try:
                return json.loads(line[len(RESULT_PREFIX):])
            except json.JSONDecodeError:
                break
    return {
        "error": "browser-use produced no result",
        "stdout": (out.get("stdout") or "")[:2000],
        "stderr": (out.get("stderr") or "")[:2000],
    }


def emit(expression: str) -> str:
    """Snippet tail that prints `expression` as the tagged JSON result."""
    return f"print({RESULT_PREFIX!r} + json.dumps({expression}, default=str))"


def ensure_cloud_browser() -> dict[str, Any]:
    """Point this session's daemon at a cloud browser.

    ensure_daemon() runs first and spawns a local daemon under BU_NAME, so
    start_remote_daemon refuses until we stop that one.
    """
    name = session_name()
    if not name:
        return {"ok": True, "backend": "local"}
    body = (
        "import json\n"
        "from browser_harness.admin import daemon_browser_kind, restart_daemon, start_remote_daemon\n"
        "_name = " + json.dumps(name) + "\n"
        "if daemon_browser_kind(_name) == 'cloud':\n"
        "    _out = {'reused': True}\n"
        "else:\n"
        "    restart_daemon(_name)\n"
        "    _b = start_remote_daemon(_name)\n"
        "    _out = {'id': (_b or {}).get('id', ''), 'liveUrl': (_b or {}).get('liveUrl', '')}\n"
        + emit("_out")
    )
    out = run_code(body, timeout=PROVISION_TIMEOUT_S)
    if out.get("returncode") != 0:
        return {"error": out.get("error") or "could not start the cloud browser", "backend": "cloud"}
    for line in reversed((out.get("stdout") or "").splitlines()):
        if line.startswith(RESULT_PREFIX):
            try:
                return {"ok": True, "backend": "cloud", "name": name, **json.loads(line[len(RESULT_PREFIX):])}
            except json.JSONDecodeError:
                break
    return {"ok": True, "backend": "cloud", "name": name}
