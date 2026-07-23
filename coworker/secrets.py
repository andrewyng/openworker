"""Secret store — one canonical store for connector/MCP credentials.

Design (from OpenClaw): secrets **never enter the model's context, prompts, or traces**.
The store holds profiles keyed by `connector[:account]`; values may be literals OR
`${ENV_VAR}` references resolved at read time from the process env / `~/.config/coworker/.env`.

Two backends behind one interface (callers depend only on the interface):

  - **file** (the default, unchanged v1 behavior): a `0600` JSON file at
    `<state_dir>/secrets.json`.
  - **keychain** (opt-in, macOS): the profile map lives as a single generic-password
    item in the login Keychain instead of a plaintext file, so credentials are
    encrypted at rest and gated by the OS. Select it with
    `COWORKER_SECRETS_BACKEND=keychain`; on first use an existing `secrets.json`
    is imported (the file itself is left untouched — delete it once satisfied).
    On platforms without a keychain the store falls back to the file backend so
    the server always boots.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_IS_WINDOWS = sys.platform == "win32"
_BACKEND_ENV = "COWORKER_SECRETS_BACKEND"


def state_dir() -> Path:
    """Where coworker keeps its state — the one cross-platform source of truth.

    Resolution order:
    1. `$COWORKER_STATE_DIR` — explicit override on any OS (used by tests/sidecars).
    2. Windows: `%APPDATA%\\coworker` (e.g. `C:\\Users\\You\\AppData\\Roaming\\coworker`),
       the native per-user app-data location.
    3. macOS / Linux: `~/.config/coworker` (XDG-style, unchanged from prior behavior).
    """
    base = os.environ.get("COWORKER_STATE_DIR")
    if base:
        return Path(base).expanduser()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "coworker"
    return Path.home() / ".config" / "coworker"


def _load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _restrict_to_user(path: Path, *, is_dir: bool) -> None:
    """Restrict a path so only the current user can access it.

    POSIX expresses this with mode bits (0700 dir / 0600 file). Windows has no such bits —
    `os.chmod` there only toggles the read-only flag, so a 0600 chmod is a silent no-op and
    the file inherits broad ACLs (SYSTEM, Administrators, …). Use an ACL instead: strip
    inherited entries and grant the current user alone. Best-effort on Windows so a transient
    icacls failure never blocks saving a key."""
    if _IS_WINDOWS:
        user = os.environ.get("USERNAME")
        if not user:
            return
        domain = os.environ.get("USERDOMAIN")
        account = f"{domain}\\{user}" if domain else user
        # A directory grant MUST be inheritable — (OI) object-inherit for files, (CI)
        # container-inherit for subdirs — so everything created inside (the SQLite stores,
        # conversations, …) inherits the user's access. Without these flags, /inheritance:r
        # leaves the directory with a non-inheritable ACE and any child file ends up with an
        # empty DACL → sqlite3 "unable to open database file", crashing the server on launch.
        grant = f"{account}:(OI)(CI)F" if is_dir else f"{account}:F"
        try:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
                capture_output=True,
                check=False,
            )
        except OSError:
            pass
        return
    os.chmod(path, 0o700 if is_dir else 0o600)


class _FileBackend:
    """v1 storage: the profile map as a `0600` JSON file (atomic replace on write)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def write(self, store: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            _restrict_to_user(self.path.parent, is_dir=True)
        except OSError:
            pass
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(store, indent=2), encoding="utf-8")
        _restrict_to_user(tmp, is_dir=False)
        os.replace(tmp, self.path)


class _KeychainBackend:
    """macOS storage: the profile map as ONE generic-password item in the login Keychain.

    One item (not one per profile) keeps `status()`/listing a single read and mirrors the
    file backend's whole-map read/write semantics exactly. The blob is base64-encoded
    JSON — pure ASCII, so it survives `security`'s output conventions (which hex-mangle
    non-printable passwords) and needs no shell quoting.

    Writes go through `security -i` with the command on **stdin**, so the secret material
    never appears in the process argv (visible to any local `ps`). The item's account is
    the logical store path, so distinct state dirs (tests, sidecars, `$COWORKER_STATE_DIR`
    overrides) get distinct items instead of sharing one.

    On first read, if the keychain item doesn't exist yet but the v1 `secrets.json` does,
    the file's contents are imported once. The file is left in place (not scrubbed) so
    flipping the backend back is non-destructive; deleting it is the user's call.
    """

    SERVICE = "coworker-secrets"
    LABEL = "OpenWorker secrets"
    _NOT_FOUND = 44  # errSecItemNotFound

    def __init__(self, path: Path) -> None:
        self.path = path  # identity for the keychain account + import source
        self._account = str(path)
        self._cache: Optional[dict[str, Any]] = None
        self._cache_lock = threading.Lock()

    @staticmethod
    def _quote(value: str) -> str:
        """Quote one argument for `security -i`'s command parser."""
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _find(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                self.SERVICE,
                "-a",
                self._account,
                "-w",
            ],
            capture_output=True,
            text=True,
        )

    def _store_blob(self, blob: str) -> None:
        # `add-generic-password -U` updates in place; `-i` reads the command from stdin
        # so the blob rides a pipe, not the argv.
        command = (
            f"add-generic-password -U -s {self._quote(self.SERVICE)} "
            f"-a {self._quote(self._account)} -l {self._quote(self.LABEL)} "
            f"-w {blob}\n"
        )
        done = subprocess.run(
            ["security", "-i"], input=command, capture_output=True, text=True
        )
        if done.returncode != 0:
            raise OSError(
                f"keychain write failed (security exited {done.returncode}): "
                f"{(done.stderr or '').strip()[:200]}"
            )

    def read(self) -> dict[str, Any]:
        with self._cache_lock:
            if self._cache is not None:
                return dict(self._cache)
            found = self._find()
            if found.returncode == self._NOT_FOUND:
                # First use: seed from the v1 file so existing setups keep working.
                imported = _FileBackend(self.path).read()
                if imported:
                    try:
                        self._store_blob(_encode(imported))
                        logger.info(
                            "secrets: imported %d profile(s) from %s into the keychain",
                            len(imported),
                            self.path,
                        )
                    except OSError:
                        logger.warning(
                            "secrets: keychain import failed; serving the file copy",
                            exc_info=True,
                        )
                        return imported  # don't cache: retry the import next read
                self._cache = imported
                return dict(imported)
            if found.returncode != 0:
                # Locked/errored keychain: serve empty but DON'T cache, so recovery
                # (unlocking) is picked up on the next read.
                logger.warning(
                    "secrets: keychain read failed (security exited %d): %s",
                    found.returncode,
                    (found.stderr or "").strip()[:200],
                )
                return {}
            self._cache = _decode(found.stdout.strip())
            return dict(self._cache)

    def write(self, store: dict[str, Any]) -> None:
        with self._cache_lock:
            self._store_blob(_encode(store))
            self._cache = dict(store)


def _encode(store: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(store).encode("utf-8")).decode("ascii")


def _decode(blob: str) -> dict[str, Any]:
    try:
        data = json.loads(base64.b64decode(blob, validate=True).decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def _pick_backend(path: Path) -> _FileBackend | _KeychainBackend:
    choice = (os.environ.get(_BACKEND_ENV) or "file").strip().lower()
    if choice in ("", "file"):
        return _FileBackend(path)
    if choice == "keychain":
        if sys.platform == "darwin" and shutil.which("security"):
            return _KeychainBackend(path)
        logger.warning(
            "secrets: %s=keychain requires the macOS `security` tool; "
            "falling back to the file backend",
            _BACKEND_ENV,
        )
        return _FileBackend(path)
    logger.warning(
        "secrets: unknown %s=%r; falling back to the file backend", _BACKEND_ENV, choice
    )
    return _FileBackend(path)


class SecretStore:
    """Secret store over a swappable backend (file by default, macOS Keychain opt-in).
    Reads resolve `${VAR}` refs; status never leaks values."""

    def __init__(
        self,
        path: Optional[str | Path] = None,
        *,
        backend: Optional[Any] = None,
    ) -> None:
        self.path = Path(path).expanduser() if path else state_dir() / "secrets.json"
        self._dotenv_path = self.path.parent / ".env"
        self._lock = threading.Lock()
        self._backend = backend if backend is not None else _pick_backend(self.path)

    # -- reads ------------------------------------------------------------------
    def get(self, profile: str) -> Optional[dict[str, Any]]:
        """Return a profile with `${VAR}` refs resolved, or None if absent."""
        data = self._read().get(profile)
        if data is None:
            return None
        return self.resolve(data)

    def resolve(self, value: Any) -> Any:
        """Resolve `${VAR}` refs in a value (recursively) from env + the local `.env`."""
        env = _load_dotenv(self._dotenv_path)

        def _walk(v: Any) -> Any:
            if isinstance(v, str):
                return _REF.sub(
                    lambda m: os.environ.get(m.group(1))
                    or env.get(m.group(1))
                    or m.group(0),
                    v,
                )
            if isinstance(v, dict):
                return {k: _walk(x) for k, x in v.items()}
            if isinstance(v, list):
                return [_walk(x) for x in v]
            return v

        return _walk(value)

    def status(self) -> list[dict[str, Any]]:
        """Profile metadata only — **never** the secret values themselves."""
        out: list[dict[str, Any]] = []
        for profile, data in self._read().items():
            data = data if isinstance(data, dict) else {}
            expires = data.get("expires")
            expired = isinstance(expires, (int, float)) and expires < time.time()
            out.append(
                {
                    "profile": profile,
                    "type": data.get("type"),
                    "account": data.get("account_id"),
                    "expired": bool(expired),
                }
            )
        return out

    # -- writes -----------------------------------------------------------------
    def put(self, profile: str, data: dict[str, Any]) -> None:
        with self._lock:
            store = self._read()
            store[profile] = data
            self._write(store)

    def delete(self, profile: str) -> bool:
        with self._lock:
            store = self._read()
            if profile not in store:
                return False
            del store[profile]
            self._write(store)
            return True

    # -- internals --------------------------------------------------------------
    def _read(self) -> dict[str, Any]:
        return self._backend.read()

    def _write(self, store: dict[str, Any]) -> None:
        self._backend.write(store)
