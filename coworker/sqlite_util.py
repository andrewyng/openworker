"""One place to open a SQLite connection, so every store gets the same durability
and concurrency settings.

Two defaults SQLite ships with are wrong for a long-lived local service:

`journal_mode=delete` (the default) takes an exclusive lock for the whole write
and leaves no way to read consistently while it holds. In practice that meant the
server had to be STOPPED to copy coworker.db or automation.db safely — copying
them hot yields a torn file that looks fine until it is restored. WAL lets readers
continue during a write and makes an online copy coherent, which is what a backup,
an `sqlite3` CLI session, or an external audit actually needs. It is a persistent
property of the file: set once, applies to every later connection.

`busy_timeout=0` (the default) is the worst possible value here. Each store already
serializes its OWN threads behind an RLock, but nothing serializes a second process
— a backup, a CLI query, another tool. With 0 those callers get SQLITE_BUSY
immediately instead of waiting the few milliseconds a write actually takes. Unlike
journal_mode this is per-connection, so it has to be set every time.

WAL keeps two sidecar files next to the database (-wal and -shm). Copy them with
the .db, or use `VACUUM INTO` / `sqlite3 .backup` which handle it for you.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union

# Long enough to outlast any write these stores make (they are small and
# single-writer), short enough that a genuine deadlock still surfaces as an error
# rather than hanging a request forever.
BUSY_TIMEOUT_MS = 5000


def connect(path: Union[str, Path], *, row_factory: bool = True) -> sqlite3.Connection:
    """Open `path` with WAL and a non-zero busy timeout.

    check_same_thread=False matches every caller here: the stores are shared
    between the scheduler thread and the server's request threads, each guarding
    its own connection with an RLock.
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    if row_factory:
        conn.row_factory = sqlite3.Row
    # Persistent once set, but harmless to re-assert, and it covers a fresh file.
    # Wrapped because a database on a filesystem without proper locking (some
    # network mounts) refuses WAL — falling back is better than failing to start.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    # NORMAL is the standard pairing with WAL: durable across process crashes,
    # and only risks the last transaction on a power cut, which for scheduler
    # bookkeeping is an acceptable trade for not fsyncing every commit.
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn
