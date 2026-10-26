"""The runner's pipe on Windows: a named pipe instead of a Unix socket file.

Windows Python has no `AF_UNIX`, so when the daemon's address starts with `\\\\.\\pipe\\` it
listens on a named pipe and the relay connects to one. Both ends are the standard library's
`multiprocessing.connection.PipeConnection` (message mode, overlapped I/O, cancellable from
another thread); on top of that this module gives the daemon and the relay the same small
shape the socket has: `sendall`, `recv`, `lines`, `close`.

The pipe's security descriptor is the one thing that matters for a sandbox: when the daemon
runs as the sandbox account, only the server's user may connect. `listen(address,
allow_sids=[...])` writes exactly those accounts into the descriptor; with no list, the
pipe keeps Windows' default (the creator, Administrators and SYSTEM may use it).
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any, Iterator, Optional, Sequence

PIPE_PREFIX = "\\\\.\\pipe\\"
_BUFSIZE = 65536


def is_pipe(address: str) -> bool:
    return address.startswith(PIPE_PREFIX)


def pipe_name(tag: str) -> str:
    return PIPE_PREFIX + tag


class PipeStream:
    """One connected end. `recv` returns b"" once the other side is gone."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def sendall(self, data: bytes) -> None:
        self._conn.send_bytes(data)

    def recv(self, _size: int = _BUFSIZE) -> bytes:
        try:
            return self._conn.recv_bytes()
        except (EOFError, OSError):
            return b""

    def lines(self) -> Iterator[bytes]:
        """Lines as the socket's `makefile("rb")` yields them: a message from the other
        side may hold part of a line or several, so they are cut here."""
        buffer = b""
        while True:
            chunk = self.recv()
            if not chunk:
                break
            buffer += chunk
            while True:
                cut = buffer.find(b"\n")
                if cut < 0:
                    break
                yield buffer[: cut + 1]
                buffer = buffer[cut + 1 :]
        if buffer:
            yield buffer

    def close(self) -> None:
        try:
            self._conn.close()
        except OSError:
            pass


def connect(address: str) -> PipeStream:
    from multiprocessing.connection import PipeClient

    return PipeStream(PipeClient(address))


class Listener:
    """The daemon's side. `accept(timeout)` returns a stream or None when nobody came."""

    def __init__(self, address: str, allow_sids: Sequence[str] = ()) -> None:
        import _winapi

        self._winapi = _winapi
        self.address = address
        self._security = _SecurityAttributes(allow_sids) if allow_sids else None
        self._pending: Optional[tuple[int, Any]] = None  # (handle, overlapped connect)
        self._closed = False
        self._new_instance(first=True)

    def _new_instance(self, first: bool = False) -> None:
        w = self._winapi
        flags = w.PIPE_ACCESS_DUPLEX | w.FILE_FLAG_OVERLAPPED
        if first:
            flags |= w.FILE_FLAG_FIRST_PIPE_INSTANCE  # fail if the name is already taken
        handle = w.CreateNamedPipe(
            self.address,
            flags,
            w.PIPE_TYPE_MESSAGE | w.PIPE_READMODE_MESSAGE | w.PIPE_WAIT,
            w.PIPE_UNLIMITED_INSTANCES,
            _BUFSIZE,
            _BUFSIZE,
            w.NMPWAIT_WAIT_FOREVER,
            self._security.pointer if self._security is not None else w.NULL,
        )
        self._pending = (handle, w.ConnectNamedPipe(handle, overlapped=True))

    def accept(self, timeout: float) -> Optional[PipeStream]:
        from multiprocessing.connection import PipeConnection

        w = self._winapi
        if self._pending is None:
            raise OSError("the listener is closed")
        handle, overlapped = self._pending
        if w.WaitForSingleObject(overlapped.event, int(timeout * 1000)) == w.WAIT_TIMEOUT:
            return None
        try:
            overlapped.GetOverlappedResult(True)
        except OSError:
            if self._closed:
                raise
            w.CloseHandle(handle)  # a client that went away before we looked
            self._new_instance()
            return None
        self._new_instance()  # the next instance waits while this one is served
        return PipeStream(PipeConnection(handle))

    def close(self) -> None:
        self._closed = True
        pending, self._pending = self._pending, None
        if pending is not None:
            handle, overlapped = pending
            try:
                overlapped.cancel()
            except OSError:
                pass
            self._winapi.CloseHandle(handle)


class _SecurityAttributes:
    """A SECURITY_ATTRIBUTES whose descriptor grants full access to the given accounts and
    nothing to anyone else (the creator keeps the owner's right to read and change it)."""

    def __init__(self, sids: Sequence[str]) -> None:
        from ctypes import wintypes

        sddl = "D:P" + "".join(f"(A;;GA;;;{sid})" for sid in sids)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG)]
        descriptor = ctypes.c_void_p()
        size = wintypes.ULONG()
        ok = convert(sddl, 1, ctypes.byref(descriptor), ctypes.byref(size))  # 1 = SDDL_REVISION_1
        if not ok:
            raise OSError(ctypes.get_last_error(), f"cannot build the pipe's security descriptor from {sddl!r}")
        self._descriptor = descriptor  # LocalAlloc'd; lives as long as this object

        class SECURITY_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", wintypes.BOOL)]

        self._attributes = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
        self.pointer = ctypes.addressof(self._attributes)


def process_is_gone(pid: int) -> bool:
    """Whether the process with this id has exited. `os.getppid()` on Windows keeps
    returning the old parent's id after it dies, so the daemon asks the process itself."""
    import _winapi

    try:
        handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, pid)
    except OSError as exc:
        # 87 (invalid parameter) is "no such process". Anything else (access denied from
        # inside a restricted token, say) means the process is there but not ours to open.
        return getattr(exc, "winerror", None) == 87
    try:
        return _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_OBJECT_0
    finally:
        _winapi.CloseHandle(handle)


def wait_ready(address: str, timeout_ms: int = 50) -> bool:
    """Whether a server instance of the pipe exists (without connecting to it)."""
    import _winapi

    try:
        _winapi.WaitNamedPipe(address, timeout_ms)
        return True
    except OSError:
        return False


def default_creation_flags() -> int:
    """For a child that must not share our console or our Ctrl-C (subprocess kwargs)."""
    if sys.platform != "win32":
        return 0
    import subprocess

    return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
