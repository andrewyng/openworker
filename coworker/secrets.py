"""Secret store — one canonical, file-backed store for connector/MCP credentials.

Design (from OpenClaw): secrets **never enter the model's context, prompts, or traces**.
The store holds profiles keyed by `connector[:account]`; values may be literals OR
`${ENV_VAR}` references resolved at read time from the process env / `~/.config/coworker/.env`.
(`.env` itself stays plaintext by design — it's a hand-edited file following the universal
dotenv convention, not something this store manages.)

v2: `secrets.json` is Fernet-encrypted at rest. The wrapping key is stored in the OS keychain
(macOS Keychain / Windows Credential Manager / Linux Secret Service, via `keyring`), scoped per
state directory. If no keychain backend is available (e.g. headless Linux without a Secret
Service), the wrapping key falls back to a local `0600` file next to the store — secrets stay
encrypted either way, just without the extra OS-level protection layer on the fallback path.
v1 plaintext stores are detected on read, backed up to `<path>.bak`, and migrated in place.
The public interface (`get`/`put`/`delete`/`status`) is unchanged, so callers never see this.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

try:
    import keyring
    from keyring.errors import KeyringError
except Exception:  # pragma: no cover - keyring is a hard dep; defend against odd platforms anyway
    keyring = None  # type: ignore[assignment]
    KeyringError = Exception  # type: ignore[assignment,misc]

_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_IS_WINDOWS = sys.platform == "win32"
_KEYRING_SERVICE = "coworker-secrets"
_STORE_VERSION = 2


class SecretStoreError(RuntimeError):
    """The on-disk secret store can't be decrypted — wrong/missing wrapping key, or corruption.

    Raised rather than silently discarding the store, because silently returning an empty store
    here would make `put()` overwrite (and permanently lose) every existing credential."""


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


def write_private_text(path: str | Path, content: str) -> Path:
    """Atomically write a user-only text file using the SecretStore's OS protections."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        _restrict_to_user(target.parent, is_dir=True)
    except OSError:
        pass
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    _restrict_to_user(tmp, is_dir=False)
    os.replace(tmp, target)
    return target


def _wrap_key_identity(store_path: Path) -> str:
    """Keyring username scoped to the store's directory, so distinct state dirs (tests, or a
    `COWORKER_STATE_DIR` override) never share — or collide with — another store's key."""
    digest = hashlib.sha256(str(store_path.parent.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"wrapkey:{digest}"


def _get_or_create_wrap_key(store_path: Path) -> bytes:
    """Fernet key for encrypting the store at `store_path`. Prefers the OS keychain; falls back
    to a local `0600` key file if no keychain backend is available."""
    username = _wrap_key_identity(store_path)

    if keyring is not None:
        try:
            existing = keyring.get_password(_KEYRING_SERVICE, username)
            if existing:
                return existing.encode("ascii")
            new_key = Fernet.generate_key()
            keyring.set_password(_KEYRING_SERVICE, username, new_key.decode("ascii"))
            return new_key
        except Exception:
            # Backend absence/failure is an expected condition on some platforms (headless
            # Linux with no Secret Service, sandboxed CI, ...) — fall through to the file key
            # rather than treating it as a fatal error.
            pass

    key_path = store_path.parent / ".secrets.key"
    if key_path.is_file():
        return key_path.read_text(encoding="ascii").strip().encode("ascii")
    warnings.warn(
        f"coworker: no OS keychain backend available; the secrets-at-rest wrapping key for "
        f"{store_path} is stored in {key_path} instead of the OS keychain. Secrets remain "
        "encrypted, just without the extra keychain protection layer.",
        RuntimeWarning,
        stacklevel=2,
    )
    new_key = Fernet.generate_key()
    write_private_text(key_path, new_key.decode("ascii"))
    return new_key


class SecretStore:
    """File-backed, encrypted-at-rest secret store. Reads resolve `${VAR}` refs; status never
    leaks values."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path).expanduser() if path else state_dir() / "secrets.json"
        self._dotenv_path = self.path.parent / ".env"
        self._lock = threading.RLock()
        self._fernet: Optional[Fernet] = None

    def _cipher(self) -> Fernet:
        if self._fernet is None:
            self._fernet = Fernet(_get_or_create_wrap_key(self.path))
        return self._fernet

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
        with self._lock:
            if not self.path.is_file():
                return {}
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            if not isinstance(raw, dict):
                return {}

            if raw.get("__version__") == _STORE_VERSION and "data" in raw:
                try:
                    plaintext = self._cipher().decrypt(raw["data"].encode("ascii"))
                except (InvalidToken, ValueError) as exc:
                    raise SecretStoreError(
                        f"Failed to decrypt {self.path}: the wrapping key is missing/changed, "
                        "or the file is corrupted. Existing secrets are inaccessible until this "
                        "is resolved — check the OS keychain entry (service "
                        f"'{_KEYRING_SERVICE}') or the {self.path.parent / '.secrets.key'} "
                        "fallback file before deleting anything."
                    ) from exc
                try:
                    store = json.loads(plaintext)
                except json.JSONDecodeError as exc:
                    raise SecretStoreError(f"Decrypted {self.path} is not valid JSON") from exc
                return store if isinstance(store, dict) else {}

            # Legacy (pre-encryption) plaintext store. Back it up once, then migrate in place —
            # every subsequent read/write goes through the encrypted format from here on.
            backup = self.path.with_name(self.path.name + ".bak")
            if not backup.is_file():
                backup.write_text(json.dumps(raw, indent=2), encoding="utf-8")
                _restrict_to_user(backup, is_dir=False)
            self._write(raw)
            return raw

    def _write(self, store: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                _restrict_to_user(self.path.parent, is_dir=True)
            except OSError:
                pass
            token = self._cipher().encrypt(json.dumps(store).encode("utf-8"))
            payload = {"__version__": _STORE_VERSION, "data": token.decode("ascii")}
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            _restrict_to_user(tmp, is_dir=False)
            os.replace(tmp, self.path)
