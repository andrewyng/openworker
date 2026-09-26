"""One engine per state dir (coworker/statelock.py)."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from coworker import statelock


def test_second_acquire_in_process_is_refused_with_holder_pid(tmp_path):
    first = statelock.acquire(tmp_path)
    try:
        assert (tmp_path / statelock.LOCK_NAME).read_text() == str(os.getpid())
        with pytest.raises(statelock.EngineBusy) as exc:
            statelock.acquire(tmp_path)
        # Same process holds it; the message still says who, without claiming a foreign pid.
        assert str(tmp_path) in str(exc.value)
    finally:
        first.release()
    # Released → free again.
    statelock.acquire(tmp_path).release()


def test_lock_is_held_across_processes_and_names_the_holder(tmp_path):
    """A second PROCESS (the systemd-twin case) is refused and told the holder's pid."""
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys,time; from coworker import statelock; "
            f"h=statelock.acquire({str(tmp_path)!r}); print('held',flush=True); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
        cwd=os.getcwd(),
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(statelock.EngineBusy) as exc:
            statelock.acquire(tmp_path, timeout=0.3)
        assert exc.value.holder_pid == holder.pid
        assert f"pid {holder.pid}" in str(exc.value)
    finally:
        holder.kill()
        holder.wait()
    # Holder gone → the lock is free; the stale pid file does not block anyone.
    statelock.acquire(tmp_path).release()


def test_acquire_waits_for_a_dying_predecessor(tmp_path):
    """A supervisor restart may overlap the old process's teardown: the new one
    waits up to `timeout` instead of failing on the first try."""
    import threading
    import time

    first = statelock.acquire(tmp_path)
    threading.Timer(0.4, first.release).start()
    t0 = time.monotonic()
    second = statelock.acquire(tmp_path, timeout=3.0)
    try:
        assert 0.3 <= time.monotonic() - t0 < 3.0
    finally:
        second.release()


def test_server_entrypoint_warns_unless_strict(tmp_path, monkeypatch, capsys):
    from coworker.server import run as server_run

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    held = statelock.acquire(tmp_path)
    try:
        monkeypatch.delenv("COWORKER_STATE_LOCK", raising=False)
        server_run._warn_if_state_shared()  # coexisting desktop + hand-run server: warn only
        assert "warning: another engine" in capsys.readouterr().err
        monkeypatch.setenv("COWORKER_STATE_LOCK", "strict")
        with pytest.raises(SystemExit) as exc:
            server_run._warn_if_state_shared()
        assert exc.value.code == 3
    finally:
        held.release()
        if server_run._ENGINE_LOCK is not None:
            server_run._ENGINE_LOCK.release()
            server_run._ENGINE_LOCK = None
