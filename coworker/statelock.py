"""One engine per state directory.

Two engines writing one state dir corrupt each other quietly: SQLite rows
vanish behind a cached handle, boards get two writers, sessions double-wake.
The failure we actually shipped was two systemd user units on one VM, both
running `openworker up` (a stale `openworker.service` from the first machine
test beside the hand-written unit) — every "kill the stray" was undone by
`Restart=always` five seconds later. This lock makes the second engine say so
and stop, instead of running.

`acquire()` holds an advisory lock on `<state>/engine.lock` for the life of
the returned handle (the process, in practice) and records the holder's pid
in the file for the refusal message. POSIX uses flock; Windows uses
msvcrt.locking on the first byte. Best effort where neither exists: the
lock degrades to "always acquired" rather than blocking a launch.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

LOCK_NAME = "engine.lock"


class EngineBusy(RuntimeError):
    def __init__(self, state: Path, holder_pid: Optional[int]) -> None:
        self.state = Path(state)
        self.holder_pid = holder_pid
        who = f"pid {holder_pid}" if holder_pid else "another process"
        super().__init__(
            f"another engine ({who}) already holds the state dir {self.state} — "
            "two engines on one state dir corrupt it. Stop the other one first "
            "(on a systemd box: `systemctl --user list-units 'openworker*'`)."
        )


class EngineLock:
    """Handle returned by `acquire()`; keep it referenced. `release()` is for tests."""

    def __init__(self, path: Path, fh) -> None:
        self.path = path
        self._fh = fh

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def _try_lock(fh) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, ImportError):
        return False


def holder_pid(state: Path) -> Optional[int]:
    """Pid recorded by the current holder, if any (informational only)."""
    try:
        text = (Path(state) / LOCK_NAME).read_text().strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def acquire(state: Path, *, timeout: float = 0.0) -> EngineLock:
    """Take the engine lock for `state`, waiting up to `timeout` seconds for a
    dying predecessor (a supervisor restarting us while the old process is
    still tearing down). Raises `EngineBusy` when it stays held."""
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    path = state / LOCK_NAME
    # "a+" never truncates: the holder's pid stays readable for the message.
    fh = open(path, "a+")
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if _try_lock(fh):
            break
        if time.monotonic() >= deadline:
            pid = holder_pid(state)
            fh.close()
            raise EngineBusy(state, pid if pid != os.getpid() else None)
        time.sleep(0.2)
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError:
        pass
    return EngineLock(path, fh)
