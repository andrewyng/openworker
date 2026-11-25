"""One engine per state dir (coworker/statelock.py)."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager

import pytest

from coworker import statelock


@contextmanager
def _holder_process(state, *, legacy=False):
    # A Windows venv executable can be a launcher. Ask the actual holder for its
    # PID and let EOF stop it, rather than killing only the launcher process.
    acquire = (
        "import msvcrt; "
        "f=open(state / 'engine.lock', 'a+'); f.seek(0); "
        "msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1); "
        "f.seek(0); f.truncate(); f.write(str(os.getpid())); f.flush(); "
        if legacy else "from coworker import statelock; h=statelock.acquire(state); "
    )
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import os,sys; from pathlib import Path; state=Path(sys.argv[1]); "
         + acquire + "print(os.getpid(),flush=True); sys.stdin.read()", str(state)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=os.getcwd(),
    )
    try:
        yield int(holder.stdout.readline().strip())
    finally:
        _, err = holder.communicate(timeout=10)
        assert holder.returncode == 0, err


def test_second_acquire_in_process_is_refused_with_holder_pid(tmp_path):
    first = statelock.acquire(tmp_path)
    try:
        assert statelock.holder_pid(tmp_path) == os.getpid()
        if sys.platform != "win32":
            assert (tmp_path / statelock.LOCK_NAME).read_text() == str(os.getpid())
        with pytest.raises(statelock.EngineBusy) as exc:
            statelock.acquire(tmp_path)
        # Same process holds it; the message still says who, without claiming a foreign pid.
        assert str(tmp_path) in str(exc.value)
        assert exc.value.holder_pid is None
    finally:
        first.release()
    # Released → free again.
    statelock.acquire(tmp_path).release()


def test_lock_is_held_across_processes_and_names_the_holder(tmp_path):
    """A second PROCESS (the systemd-twin case) is refused and told the holder's pid."""
    with _holder_process(tmp_path) as pid:
        assert statelock.holder_pid(tmp_path) == pid
        with pytest.raises(statelock.EngineBusy) as exc:
            statelock.acquire(tmp_path, timeout=0.3)
        assert exc.value.holder_pid == pid
        assert f"pid {pid}" in str(exc.value)
    # Holder gone → the lock is free; the stale pid file does not block anyone.
    statelock.acquire(tmp_path).release()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows byte-lock protocol")
def test_legacy_holder_still_blocks_new_engine_without_a_wrong_pid(tmp_path):
    # An old holder truncates any metadata left by a previous new holder.
    statelock.acquire(tmp_path).release()
    with _holder_process(tmp_path, legacy=True):
        assert statelock.holder_pid(tmp_path) is None
        with pytest.raises(statelock.EngineBusy) as exc:
            statelock.acquire(tmp_path)
        assert exc.value.holder_pid is None
    statelock.acquire(tmp_path).release()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows byte-lock protocol")
def test_new_holder_still_blocks_legacy_engine(tmp_path):
    held = statelock.acquire(tmp_path)
    try:
        contender = subprocess.run(
            [sys.executable, "-c", """
import msvcrt, sys
with open(sys.argv[1], 'a+') as f:
    f.seek(0)
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print('busy')
    else:
        print('acquired')
""", str(tmp_path / statelock.LOCK_NAME)],
            capture_output=True, text=True, timeout=10,
        )
        assert contender.returncode == 0, contender.stderr
        assert contender.stdout.strip() == "busy"
    finally:
        held.release()


@pytest.mark.parametrize("record, expected", [
    (b"12345", 12345), (b"", None), (b"bad", None), (b"\xff", None),
    (b"#pid=bad", None), (b"#pid=", None),
])
def test_holder_pid_reads_legacy_records_and_ignores_invalid_records(tmp_path, record, expected):
    (tmp_path / statelock.LOCK_NAME).write_bytes(record)
    assert statelock.holder_pid(tmp_path) == expected


def test_holder_pid_missing_file(tmp_path):
    assert statelock.holder_pid(tmp_path) is None


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
