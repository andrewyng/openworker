"""The runner daemon: the long-lived process inside a sandbox. Standard library only.

It owns the persistent shells (one per agent that uses this sandbox), their background
tasks and the file operations, and serves them over a Unix socket FILE (not a network
port). A client never talks to it directly: each connection comes through a small attach
relay that the sandbox provider starts, and that relay dies with its stream while this
process and its shells live on.

Trust: this process is on the untrusted side. The agent's commands run as the same user
and can tamper with it. It holds no keys and decides nothing; the server treats what it
returns as data.

Delivery rules (design doc, section 4c):
- The RESULT of a request is the record. Results are kept until acknowledged and replayed
  after a reconnect (`session.resume`).
- `shell.output` / `shell.progress` notifications are a live view and may be dropped.
- The newest connection wins; an older one is closed.
- A request id seen twice runs once.
"""

from __future__ import annotations

import collections
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from . import fsops, protocol as P, toolcalls
from .executor import LocalExecutor

RUNNER_VERSION = "0.1.0"
_RESULT_LOG_MAX = 5000
_REQUEST_MEMORY_MAX = 5000


class _Conn:
    """One attached client (through a relay). Writes are serialized."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self._lock = threading.Lock()
        self.alive = True

    def send(self, frame: bytes) -> bool:
        if not self.alive:
            return False
        try:
            with self._lock:
                self.sock.sendall(frame)
            return True
        except OSError:
            self.alive = False
            return False

    def close(self) -> None:
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class _Shell:
    def __init__(self, name: str, executor: LocalExecutor) -> None:
        self.name = name
        self.executor = executor
        self.busy = threading.Lock()


class Daemon:
    def __init__(self, socket_path: str, default_cwd: Optional[str] = None, exit_with_parent: bool = False) -> None:
        self.socket_path = socket_path
        # Outside a sandbox nothing else ends this process when its server goes away.
        self._parent = os.getppid() if exit_with_parent else None
        self.default_cwd = str(Path(default_cwd or os.getcwd()).expanduser().resolve())
        self.instance_id = uuid.uuid4().hex
        self._shells: dict[str, _Shell] = {}
        self._shells_lock = threading.Lock()
        self._seq = 0
        self._out_lock = threading.Lock()  # guards seq, the result log and the active conn
        self._results: "collections.deque[tuple[int, bytes]]" = collections.deque(maxlen=_RESULT_LOG_MAX)
        self._gap = False  # a result fell off the log before it was acknowledged
        self._requests: "collections.OrderedDict[str, Optional[bytes]]" = collections.OrderedDict()
        self._conn: Optional[_Conn] = None
        self._stop = threading.Event()

    # -- outgoing ---------------------------------------------------------------------
    def _emit(self, frame: dict[str, Any], *, record: bool) -> None:
        """Stamp a sequence number, remember results, send to whoever is attached."""
        with self._out_lock:
            self._seq += 1
            seq = self._seq
            if "result" in frame:
                frame["result"]["seq"] = seq
            elif "error" in frame:
                frame["error"].setdefault("data", {})["seq"] = seq
            else:
                frame.setdefault("params", {})["seq"] = seq
            data = P.encode(frame)
            if record:
                if len(self._results) == self._results.maxlen:
                    self._gap = True
                self._results.append((seq, data))
                req_id = frame.get("id")
                if isinstance(req_id, str) and req_id in self._requests:
                    self._requests[req_id] = data
            conn = self._conn
        if conn is not None:
            conn.send(data)

    def _reply(self, req_id: str, value: dict[str, Any]) -> None:
        self._emit(P.result(req_id, value), record=True)

    def _fail(self, req_id: Optional[str], code: int, message: str, data: Optional[dict] = None) -> None:
        self._emit(P.error(req_id, code, message, data), record=req_id is not None)

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._emit(P.notification(method, params), record=False)

    # -- shells -----------------------------------------------------------------------
    def _shell(self, name: str, *, cwd: Optional[str] = None, create: bool = True) -> Optional[_Shell]:
        with self._shells_lock:
            shell = self._shells.get(name)
            if shell is None and create:
                start = cwd or self.default_cwd
                if not os.path.isdir(start):
                    start = self.default_cwd
                shell = _Shell(name, LocalExecutor(cwd=start))
                self._shells[name] = shell
            return shell

    def _resolve(self, params: dict[str, Any], key: str = "path") -> str:
        raw = params.get(key)
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"'{key}' is required")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            shell = self._shell(str(params.get("shell") or ""), create=False) if params.get("shell") else None
            base = shell.executor.cwd if shell is not None else self.default_cwd
            path = Path(base) / path
        return str(path)

    # -- methods ----------------------------------------------------------------------
    def _hello(self, params: dict[str, Any]) -> dict[str, Any]:
        theirs = params.get("protocol_version")
        if theirs is not None and theirs != P.PROTOCOL_VERSION:
            raise P.RunnerError(P.VERSION_MISMATCH, f"runner speaks protocol {P.PROTOCOL_VERSION}, client sent {theirs}")
        return {
            "protocol_version": P.PROTOCOL_VERSION,
            "runner_version": RUNNER_VERSION,
            "instance_id": self.instance_id,
            "pid": os.getpid(),
            "os": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "shell": "powershell.exe" if sys.platform == "win32" else "/bin/bash",
            "cwd": self.default_cwd,
            "shells": sorted(self._shells),
        }

    def _resume(self, params: dict[str, Any]) -> dict[str, Any]:
        last = int(params.get("last_seq") or 0)
        with self._out_lock:
            pending = [data for seq, data in self._results if seq > last]
            gap, self._gap = self._gap, False
            conn = self._conn
        for data in pending:
            if conn is not None:
                conn.send(data)
        return {"instance_id": self.instance_id, "replayed": len(pending), "gap": gap}

    def _ack(self, params: dict[str, Any]) -> None:
        upto = int(params.get("seq") or 0)
        with self._out_lock:
            while self._results and self._results[0][0] <= upto:
                self._results.popleft()

    def _shell_run(self, req_id: str, params: dict[str, Any]) -> dict[str, Any]:
        command = params.get("command")
        if not isinstance(command, str):
            raise ValueError("'command' is required")
        name = str(params.get("shell") or "main")
        shell = self._shell(name, cwd=params.get("cwd"))
        assert shell is not None
        if not shell.busy.acquire(blocking=False):
            raise P.RunnerError(P.SHELL_BUSY, f"shell '{name}' is running another command")
        started = time.monotonic()
        live = _LiveView(lambda method, extra: self._notify(method, {"id": req_id, "shell": name, **extra}))
        try:
            timeout = params.get("timeout_s")
            result = shell.executor.run(
                command,
                timeout=float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else None,
                on_output=live.feed,
            )
        finally:
            live.stop()
            shell.busy.release()
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        result["output_bytes"] = live.total_bytes
        return result

    def _proc_run(self, params: dict[str, Any]) -> dict[str, Any]:
        argv = params.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("'argv' must be a non-empty list of strings")
        cwd = self._resolve(params, "cwd") if params.get("cwd") else self.default_cwd
        env = {**os.environ, **{str(k): str(v) for k, v in (params.get("env") or {}).items()}}
        timeout = params.get("timeout_s")
        try:
            done = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                input=params.get("stdin"),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else None,
            )
        except subprocess.TimeoutExpired as exc:
            return {"exit_code": None, "stdout": exc.stdout or "", "stderr": exc.stderr or "", "timed_out": True}
        except OSError as exc:
            return {"exit_code": None, "stdout": "", "stderr": "", "timed_out": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"exit_code": done.returncode, "stdout": done.stdout, "stderr": done.stderr, "timed_out": False}

    def _dispatch(self, req_id: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "runner.hello":
            return self._hello(params)
        if method == "session.resume":
            return self._resume(params)
        if method == "shell.open":
            shell = self._shell(str(params.get("shell") or "main"), cwd=params.get("cwd"))
            assert shell is not None
            return {"shell": shell.name, "cwd": shell.executor.cwd}
        if method == "shell.run":
            return self._shell_run(req_id, params)
        if method in ("shell.interrupt", "shell.close", "task.start", "task.output", "task.kill", "task.list"):
            name = str(params.get("shell") or "main")
            # A task call opens the shell if needed, so an unknown task id is reported as
            # that (as the in-process executor does), not as a missing shell.
            shell = self._shell(name, cwd=params.get("cwd"), create=method.startswith("task."))
            if shell is None:
                return {"shell": name, "closed": True} if method == "shell.close" else {"shell": name}
            ex = shell.executor
            if method == "shell.interrupt":
                ex.interrupt_now()
                return {"shell": name}
            if method == "shell.close":
                with self._shells_lock:
                    self._shells.pop(name, None)
                ex.close()
                return {"shell": name, "closed": True}
            if method == "task.start":
                return ex.run_background(str(params.get("command") or ""))
            if method == "task.output":
                return ex.background_output(str(params.get("task_id") or ""))
            if method == "task.kill":
                return ex.background_kill(str(params.get("task_id") or ""))
            return {
                "tasks": [
                    {"task_id": tid, "command": t.command, "status": "running" if t.proc.poll() is None else "exited"}
                    for tid, t in ex._bg_tasks.items()
                ]
            }
        if method == "proc.run":
            return self._proc_run(params)
        if method == "tool.call":
            # One of OpenWorker's own workspace tools, run here with the same code the
            # in-process tool runs. The value goes back as it is (a dict, a list, a string).
            value = toolcalls.call(
                str(params.get("name") or ""),
                dict(params.get("args") or {}),
                workspace=str(params.get("workspace") or self.default_cwd),
                roots=params.get("roots") or None,
            )
            return {"value": value}
        if method.startswith("fs."):
            path = self._resolve(params)
            try:
                if method == "fs.read":
                    return fsops.read(path, **_pick(params, "offset_line", "limit_lines", "offset_bytes", "limit_bytes"))
                if method == "fs.write":
                    return fsops.write(path, **_pick(params, "text", "data_b64", "if_match_sha256", "append", "make_parents"))
                if method == "fs.stat":
                    return fsops.stat(path)
                if method == "fs.list":
                    return fsops.list_dir(path, **_pick(params, "limit"))
                if method == "fs.mkdir":
                    return fsops.mkdir(path, **_pick(params, "parents"))
                if method == "fs.remove":
                    return fsops.remove(path, **_pick(params, "recursive"))
                if method == "fs.search":
                    return fsops.search(path, str(params.get("pattern") or ""), **_pick(params, "max_matches", "ignore_case"))
            except fsops.FsError as exc:
                raise P.RunnerError(P.FS_ERROR, str(exc)) from exc
        if method == "runner.shutdown":
            self._stop.set()
            return {"stopping": True}
        raise P.RunnerError(P.METHOD_NOT_FOUND, f"unknown method: {method}")

    def _run_request(self, req_id: str, method: str, params: dict[str, Any]) -> None:
        try:
            self._reply(req_id, self._dispatch(req_id, method, params))
        except P.RunnerError as exc:
            self._fail(req_id, exc.code, exc.message)
        except toolcalls.ToolRaised as exc:
            self._fail(req_id, P.TOOL_RAISED, str(exc), {"type": exc.type_name})
        except (ValueError, TypeError) as exc:
            self._fail(req_id, P.INVALID_PARAMS, str(exc))
        except Exception as exc:  # never let one request take the daemon down
            self._fail(req_id, P.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
        if method == "runner.shutdown":
            self._shutdown()

    # -- connections ------------------------------------------------------------------
    def _serve_conn(self, conn: _Conn) -> None:
        with self._out_lock:
            old, self._conn = self._conn, conn
        if old is not None:
            old.close()  # the newest connection wins
        reader = conn.sock.makefile("rb")
        try:
            for line in reader:
                frame = P.decode(line)
                if frame is None:
                    continue
                method = frame.get("method")
                if not isinstance(method, str):
                    continue
                params = frame.get("params") if isinstance(frame.get("params"), dict) else {}
                req_id = frame.get("id")
                if req_id is None:  # a notification from the client
                    if method == "session.ack":
                        self._ack(params)
                    continue  # "$/ping" and anything unknown: nothing to do
                req_id = str(req_id)
                with self._out_lock:
                    seen = req_id in self._requests
                    stored = self._requests.get(req_id)
                    if not seen:
                        self._requests[req_id] = None
                        while len(self._requests) > _REQUEST_MEMORY_MAX:
                            self._requests.popitem(last=False)
                if seen:
                    if stored is not None:
                        conn.send(stored)  # already done: same answer, not a second run
                    continue
                if method in ("runner.hello", "session.resume"):
                    self._run_request(req_id, method, params)  # inline: keeps their order
                else:
                    threading.Thread(target=self._run_request, args=(req_id, method, params), daemon=True).start()
        except OSError:
            pass
        finally:
            conn.close()
            with self._out_lock:
                if self._conn is conn:
                    self._conn = None

    def _shutdown(self) -> None:
        with self._shells_lock:
            shells, self._shells = list(self._shells.values()), {}
        for shell in shells:
            for task_id in list(shell.executor._bg_tasks):
                shell.executor.background_kill(task_id)
            shell.executor.close()
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        folder = os.path.dirname(self.socket_path)
        try:
            if os.path.basename(folder).startswith("owr-"):
                # A folder a provider made for this one runner (socket, temporary files, log).
                # Nobody else cleans it up when the server was killed.
                shutil.rmtree(folder, ignore_errors=True)
            else:
                os.rmdir(folder)  # only if the socket had its own folder
        except OSError:
            pass

    def serve_forever(self) -> None:
        path = self.socket_path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_mask = os.umask(0o077)  # the socket file is for this user only
        try:
            server.bind(path)
        finally:
            os.umask(old_mask)
        server.listen(16)
        server.settimeout(0.5)
        print(f"openworker tool runner {RUNNER_VERSION} ready on {path} (instance {self.instance_id})", file=sys.stderr, flush=True)
        try:
            while not self._stop.is_set():
                try:
                    sock, _ = server.accept()
                except socket.timeout:
                    if self._parent is not None and os.getppid() != self._parent:
                        break  # the server that started us is gone
                    continue
                except OSError:
                    break
                threading.Thread(target=self._serve_conn, args=(_Conn(sock),), daemon=True).start()
        finally:
            server.close()
            self._shutdown()


class _LiveView:
    """Turns a command's lines into `shell.output` notifications: a chunk when it reaches
    LIVE_CHUNK_BYTES or LIVE_CHUNK_SECONDS of age, nothing at all for a command that ends
    sooner than that, and only progress ticks once LIVE_BUDGET_BYTES has been sent."""

    def __init__(self, notify: Callable[[str, dict[str, Any]], None]) -> None:
        self._notify = notify
        self._lock = threading.Lock()
        self._pending: list[str] = []
        self._pending_bytes = 0
        self._sent_bytes = 0
        self.total_bytes = 0
        self._started = time.monotonic()
        self._last_progress = self._started
        self._done = threading.Event()
        self._ticker = threading.Thread(target=self._tick, daemon=True)
        self._ticker.start()

    def feed(self, line: str) -> None:
        size = len(line.encode("utf-8", errors="replace"))
        with self._lock:
            self.total_bytes += size
            if self._sent_bytes >= P.LIVE_BUDGET_BYTES:
                return
            self._pending.append(line)
            self._pending_bytes += size
            flush = self._pending_bytes >= P.LIVE_CHUNK_BYTES
        if flush:
            self._flush()

    def _flush(self) -> None:
        with self._lock:
            if not self._pending:
                return
            text = "".join(self._pending)
            self._sent_bytes += self._pending_bytes
            self._pending, self._pending_bytes = [], 0
        self._notify("shell.output", {"text": text})

    def _tick(self) -> None:
        while not self._done.wait(P.LIVE_CHUNK_SECONDS):
            self._flush()
            now = time.monotonic()
            with self._lock:
                paused = self._sent_bytes >= P.LIVE_BUDGET_BYTES
                total = self.total_bytes
            if paused and now - self._last_progress >= P.PROGRESS_SECONDS:
                self._last_progress = now
                self._notify(
                    "shell.progress",
                    {"live_output_paused": True, "output_bytes": total, "elapsed_s": int(now - self._started)},
                )

    def stop(self) -> None:
        self._done.set()


def _pick(params: dict[str, Any], *names: str) -> dict[str, Any]:
    return {name: params[name] for name in names if params.get(name) is not None}
