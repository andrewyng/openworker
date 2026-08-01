"""Encrypted persistence for reusable Playwright authentication state.

The vault deliberately does not know about Playwright.  Its integration contract is:

    with vault.lease(profile_id) as profile:
        storage_state = profile.load()
        ... create/use one isolated BrowserContext ...
        profile.save(await context.storage_state(indexed_db=True))

Only the holder of the exclusive profile lease may read, replace, or delete a profile.
The JSON payload is encrypted with a fresh AES-256-GCM data key on every save.  A
platform ``KeyProtector`` wraps that data key; tests can inject
``InMemoryKeyProtector`` without touching an OS credential store.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import stat
import sys
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Optional, Protocol

try:  # Kept optional at import time so non-browser OpenWorker installs still start.
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover - exercised only in minimal non-browser installs
    AESGCM = None  # type: ignore[assignment,misc]

    class InvalidTag(Exception):
        pass


_ENVELOPE_VERSION = 1
_AAD_PREFIX = b"openworker/browser-profile/v1\x00"
_MAX_ENVELOPE_BYTES = 32 * 1024 * 1024
_MAX_STATE_BYTES = 16 * 1024 * 1024
_PROCESS_LEASES: set[str] = set()
_PROCESS_LEASES_LOCK = threading.Lock()


class BrowserProfileVaultError(RuntimeError):
    """Base class for fail-closed browser-profile errors."""


class VaultCryptoUnavailable(BrowserProfileVaultError):
    """AES-GCM or a platform key protector is unavailable."""


class VaultCorruptError(BrowserProfileVaultError):
    """A persisted profile is malformed or fails authenticated decryption."""


class VaultPermissionError(BrowserProfileVaultError):
    """A profile path does not have the expected user-only protection."""


class ProfileInUseError(BrowserProfileVaultError):
    """Another context or process currently owns the profile."""


class InvalidProfileId(BrowserProfileVaultError):
    """A caller supplied an invalid profile label."""


class KeyProtector(Protocol):
    """Wrap and unwrap a random vault data key.

    Implementations must bind the protected value to ``purpose`` and must fail rather
    than returning unauthenticated bytes.  The purpose contains only a profile digest,
    never the user-facing profile label.
    """

    def protect(self, plaintext: bytes, *, purpose: bytes) -> bytes: ...

    def unprotect(self, protected: bytes, *, purpose: bytes) -> bytes: ...


class BrowserProfileVault(Protocol):
    """Minimal persistence interface consumed by a browser session manager."""

    def lease(self, profile_id: str) -> "ProfileLease": ...

    def exists(self, profile_id: str) -> bool: ...


def _require_aesgcm() -> Any:
    if AESGCM is None:
        raise VaultCryptoUnavailable(
            "Browser profile encryption requires the browser build's cryptography package"
        )
    return AESGCM


class InMemoryKeyProtector:
    """Safe, dependency-light protector for tests and process-ephemeral profiles.

    The wrapping key exists only in this object.  Recreating the object intentionally
    makes old vaults undecryptable, so production callers must use an OS-backed
    protector instead.
    """

    def __init__(self, key: Optional[bytes] = None) -> None:
        self._key = bytes(key) if key is not None else os.urandom(32)
        if len(self._key) != 32:
            raise ValueError("InMemoryKeyProtector requires a 32-byte key")

    def protect(self, plaintext: bytes, *, purpose: bytes) -> bytes:
        aesgcm = _require_aesgcm()(self._key)
        nonce = os.urandom(12)
        return b"OWMK1" + nonce + aesgcm.encrypt(nonce, plaintext, purpose)

    def unprotect(self, protected: bytes, *, purpose: bytes) -> bytes:
        if not protected.startswith(b"OWMK1") or len(protected) < 5 + 12 + 16:
            raise VaultCorruptError("Invalid protected-key envelope")
        nonce = protected[5:17]
        try:
            return _require_aesgcm()(self._key).decrypt(
                nonce, protected[17:], purpose
            )
        except InvalidTag as exc:
            raise VaultCorruptError("Protected key failed authentication") from exc


class MacOSKeychainProtector:
    """AES key wrapping backed by a non-exported-by-OpenWorker Keychain master key.

    The implementation calls Security.framework directly.  It never places key
    material in command-line arguments, environment variables, or files.
    """

    _NOT_FOUND = -25300
    _DUPLICATE_ITEM = -25299

    def __init__(
        self,
        *,
        service: str = "ai.openworker.browser",
        account: str = "profile-vault-master-v1",
    ) -> None:
        if sys.platform != "darwin":
            raise VaultCryptoUnavailable("macOS Keychain is only available on macOS")
        self._service = service.encode("utf-8")
        self._account = account.encode("utf-8")
        self._master: Optional[bytes] = None
        self._lock = threading.Lock()

    def protect(self, plaintext: bytes, *, purpose: bytes) -> bytes:
        nonce = os.urandom(12)
        encrypted = _require_aesgcm()(self._master_key()).encrypt(
            nonce, plaintext, purpose
        )
        return b"OWKC1" + nonce + encrypted

    def unprotect(self, protected: bytes, *, purpose: bytes) -> bytes:
        if not protected.startswith(b"OWKC1") or len(protected) < 5 + 12 + 16:
            raise VaultCorruptError("Invalid Keychain-wrapped key")
        try:
            return _require_aesgcm()(self._master_key()).decrypt(
                protected[5:17], protected[17:], purpose
            )
        except InvalidTag as exc:
            raise VaultCorruptError("Keychain-wrapped key failed authentication") from exc

    def _master_key(self) -> bytes:
        with self._lock:
            if self._master is not None:
                return self._master
            found = self._find_password()
            if found is None:
                candidate = os.urandom(32)
                status = self._add_password(candidate)
                if status == self._DUPLICATE_ITEM:  # another process won the race
                    found = self._find_password()
                elif status != 0:
                    raise VaultCryptoUnavailable(
                        f"Keychain rejected browser vault key (OSStatus {status})"
                    )
                else:
                    found = candidate
            if found is None or len(found) != 32:
                raise VaultCryptoUnavailable("Invalid browser vault key in Keychain")
            self._master = found
            return found

    @staticmethod
    def _frameworks() -> tuple[Any, Any]:
        security = ctypes.CDLL(
            "/System/Library/Frameworks/Security.framework/Security"
        )
        core = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        core.CFRelease.argtypes = [ctypes.c_void_p]
        return security, core

    def _find_password(self) -> Optional[bytes]:
        security, core = self._frameworks()
        length = ctypes.c_uint32(0)
        data = ctypes.c_void_p()
        item = ctypes.c_void_p()
        status = security.SecKeychainFindGenericPassword(
            None,
            len(self._service),
            self._service,
            len(self._account),
            self._account,
            ctypes.byref(length),
            ctypes.byref(data),
            ctypes.byref(item),
        )
        if status == self._NOT_FOUND:
            return None
        if status != 0:
            raise VaultCryptoUnavailable(
                f"Unable to read browser vault key from Keychain (OSStatus {status})"
            )
        try:
            return ctypes.string_at(data, length.value)
        finally:
            if data:
                security.SecKeychainItemFreeContent(None, data)
            if item:
                core.CFRelease(item)

    def _add_password(self, value: bytes) -> int:
        security, _ = self._frameworks()
        return int(
            security.SecKeychainAddGenericPassword(
                None,
                len(self._service),
                self._service,
                len(self._account),
                self._account,
                len(value),
                value,
                None,
            )
        )


class WindowsDPAPIKeyProtector:
    """Wrap data keys to the current Windows user with DPAPI."""

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.c_uint32),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise VaultCryptoUnavailable("Windows DPAPI is only available on Windows")

    @classmethod
    def _blob(cls, value: bytes) -> tuple["WindowsDPAPIKeyProtector._DATA_BLOB", Any]:
        buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        return cls._DATA_BLOB(len(value), buffer), buffer

    def protect(self, plaintext: bytes, *, purpose: bytes) -> bytes:
        return self._crypt(plaintext, purpose=purpose, decrypt=False)

    def unprotect(self, protected: bytes, *, purpose: bytes) -> bytes:
        try:
            return self._crypt(protected, purpose=purpose, decrypt=True)
        except OSError as exc:
            raise VaultCorruptError("DPAPI could not unwrap the browser vault key") from exc

    def _crypt(self, value: bytes, *, purpose: bytes, decrypt: bool) -> bytes:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        blob_pointer = ctypes.POINTER(self._DATA_BLOB)
        crypt32.CryptProtectData.argtypes = [
            blob_pointer,
            ctypes.c_wchar_p,
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            blob_pointer,
        ]
        crypt32.CryptProtectData.restype = ctypes.c_int
        crypt32.CryptUnprotectData.argtypes = [
            blob_pointer,
            ctypes.c_void_p,
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            blob_pointer,
        ]
        crypt32.CryptUnprotectData.restype = ctypes.c_int
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        data, data_buffer = self._blob(value)
        entropy_value = hashlib.sha256(purpose).digest()
        entropy, entropy_buffer = self._blob(entropy_value)
        output = self._DATA_BLOB()
        if decrypt:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(data),
                None,
                ctypes.byref(entropy),
                None,
                None,
                self._CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output),
            )
        else:
            ok = crypt32.CryptProtectData(
                ctypes.byref(data),
                ctypes.c_wchar_p("OpenWorker Browser Profile"),
                ctypes.byref(entropy),
                None,
                None,
                self._CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output),
            )
        # Keep ctypes-owned input arrays alive until the native call returns.
        del data_buffer, entropy_buffer
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


def default_key_protector() -> KeyProtector:
    """Return the production platform protector, or fail closed."""

    if sys.platform == "darwin":
        return MacOSKeychainProtector()
    if sys.platform == "win32":
        return WindowsDPAPIKeyProtector()
    raise VaultCryptoUnavailable(
        "Remembered browser sign-ins require a supported OS credential store"
    )


@dataclass
class ProfileLease:
    """Exclusive capability for one saved browser profile."""

    _vault: "EncryptedBrowserProfileVault"
    profile_id: str
    _lock_path: Path
    _handle: BinaryIO
    _released: bool = False

    def load(self) -> Optional[dict[str, Any]]:
        self._ensure_active()
        return self._vault._load(self)

    def save(self, storage_state: Mapping[str, Any]) -> None:
        self._ensure_active()
        self._vault._save(self, storage_state)

    def delete(self) -> bool:
        self._ensure_active()
        return self._vault._delete(self)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._vault._release(self)

    def _ensure_active(self) -> None:
        if self._released:
            raise ProfileInUseError("Browser profile lease has already been released")

    def __enter__(self) -> "ProfileLease":
        self._ensure_active()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()


class EncryptedBrowserProfileVault:
    """File-backed AES-GCM vault with atomic replacement and exclusive leases."""

    def __init__(
        self,
        root: str | Path,
        *,
        key_protector: Optional[KeyProtector] = None,
    ) -> None:
        # Preserve the final path component so _prepare_root can reject a supplied
        # symlink rather than silently following it.
        self.root = Path(root).expanduser().absolute()
        self.key_protector = key_protector or default_key_protector()

    def lease(self, profile_id: str) -> ProfileLease:
        normalized = _normalize_profile_id(profile_id)
        self._prepare_root()
        lock_path = self._lock_path(normalized)
        lock_path.touch(mode=0o600, exist_ok=True)
        _restrict_path(lock_path, is_dir=False)
        identity = str(lock_path)
        with _PROCESS_LEASES_LOCK:
            if identity in _PROCESS_LEASES:
                raise ProfileInUseError("Browser profile is already in use")
            _PROCESS_LEASES.add(identity)
        handle: Optional[BinaryIO] = None
        try:
            handle = lock_path.open("r+b", buffering=0)
            _lock_file(handle)
            return ProfileLease(self, normalized, lock_path, handle)
        except BaseException:
            if handle is not None:
                handle.close()
            with _PROCESS_LEASES_LOCK:
                _PROCESS_LEASES.discard(identity)
            raise

    def exists(self, profile_id: str) -> bool:
        normalized = _normalize_profile_id(profile_id)
        return self._profile_path(normalized).is_file()

    def clear(self, profile_id: str) -> bool:
        """Acquire the profile and destroy its ciphertext."""

        with self.lease(profile_id) as profile:
            return profile.delete()

    def _load(self, lease: ProfileLease) -> Optional[dict[str, Any]]:
        self._assert_lease(lease)
        path = self._profile_path(lease.profile_id)
        if not path.exists():
            return None
        raw = _read_private_file(path)
        try:
            envelope = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise VaultCorruptError("Malformed browser profile envelope") from exc
        if not isinstance(envelope, dict) or set(envelope) != {
            "version",
            "profile",
            "wrapped_key",
            "nonce",
            "ciphertext",
        }:
            raise VaultCorruptError("Unexpected browser profile envelope")
        digest = _profile_digest(lease.profile_id)
        if envelope.get("version") != _ENVELOPE_VERSION or not hmac.compare_digest(
            str(envelope.get("profile") or ""), digest
        ):
            raise VaultCorruptError("Browser profile identity mismatch")
        purpose = _AAD_PREFIX + digest.encode("ascii")
        try:
            wrapped_key = _decode64(envelope["wrapped_key"])
            nonce = _decode64(envelope["nonce"])
            ciphertext = _decode64(envelope["ciphertext"])
            key = self.key_protector.unprotect(wrapped_key, purpose=purpose)
            if len(key) != 32 or len(nonce) != 12:
                raise VaultCorruptError("Invalid browser profile key or nonce")
            plaintext = _require_aesgcm()(key).decrypt(nonce, ciphertext, purpose)
        except VaultCorruptError:
            raise
        except (InvalidTag, TypeError, ValueError) as exc:
            raise VaultCorruptError(
                "Browser profile failed authenticated decryption"
            ) from exc
        if len(plaintext) > _MAX_STATE_BYTES:
            raise VaultCorruptError("Browser profile state is too large")
        try:
            state = json.loads(
                plaintext.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise VaultCorruptError("Malformed Playwright storage state") from exc
        if not isinstance(state, dict):
            raise VaultCorruptError("Playwright storage state must be an object")
        return state

    def _save(
        self, lease: ProfileLease, storage_state: Mapping[str, Any]
    ) -> None:
        self._assert_lease(lease)
        if not isinstance(storage_state, Mapping):
            raise TypeError("Playwright storage state must be a mapping")
        try:
            plaintext = json.dumps(
                dict(storage_state),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("Playwright storage state must be strict JSON") from exc
        if len(plaintext) > _MAX_STATE_BYTES:
            raise ValueError("Playwright storage state exceeds the vault size limit")
        digest = _profile_digest(lease.profile_id)
        purpose = _AAD_PREFIX + digest.encode("ascii")
        key = os.urandom(32)
        nonce = os.urandom(12)
        ciphertext = _require_aesgcm()(key).encrypt(nonce, plaintext, purpose)
        wrapped_key = self.key_protector.protect(key, purpose=purpose)
        envelope = {
            "version": _ENVELOPE_VERSION,
            "profile": digest,
            "wrapped_key": _encode64(wrapped_key),
            "nonce": _encode64(nonce),
            "ciphertext": _encode64(ciphertext),
        }
        serialized = json.dumps(
            envelope, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        _atomic_private_write(self._profile_path(lease.profile_id), serialized)

    def _delete(self, lease: ProfileLease) -> bool:
        self._assert_lease(lease)
        path = self._profile_path(lease.profile_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        _fsync_directory(path.parent)
        return True

    def _release(self, lease: ProfileLease) -> None:
        try:
            _unlock_file(lease._handle)
        finally:
            lease._handle.close()
            with _PROCESS_LEASES_LOCK:
                _PROCESS_LEASES.discard(str(lease._lock_path))

    def _assert_lease(self, lease: ProfileLease) -> None:
        if lease._vault is not self or lease._released:
            raise ProfileInUseError("A live lease from this vault is required")

    def _prepare_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise VaultPermissionError("Browser profile directory is not a real directory")
        _restrict_path(self.root, is_dir=True)

    def _profile_path(self, profile_id: str) -> Path:
        return self.root / f"{_profile_digest(profile_id)}.owbv"

    def _lock_path(self, profile_id: str) -> Path:
        return self.root / f"{_profile_digest(profile_id)}.lease"


def _normalize_profile_id(profile_id: str) -> str:
    if not isinstance(profile_id, str):
        raise InvalidProfileId("Browser profile id must be text")
    normalized = unicodedata.normalize("NFC", profile_id).strip()
    if (
        not normalized
        or len(normalized) > 128
        or any(unicodedata.category(char).startswith("C") for char in normalized)
    ):
        raise InvalidProfileId("Browser profile id is empty or contains control characters")
    return normalized


def _profile_digest(profile_id: str) -> str:
    return hashlib.sha256(profile_id.encode("utf-8")).hexdigest()


def _encode64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode64(value: Any) -> bytes:
    if not isinstance(value, str):
        raise VaultCorruptError("Invalid base64 field")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise VaultCorruptError("Invalid base64 field") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _restrict_path(path: Path, *, is_dir: bool) -> None:
    if sys.platform != "win32":
        os.chmod(path, 0o700 if is_dir else 0o600)
        return
    # Browser state is bearer-auth material.  Unlike the legacy SecretStore, a
    # failed ACL update is fatal rather than best-effort.
    import subprocess

    user = os.environ.get("USERNAME")
    if not user:
        raise VaultPermissionError("Unable to identify the Windows user")
    domain = os.environ.get("USERDOMAIN")
    account = f"{domain}\\{user}" if domain else user
    grant = f"{account}:(OI)(CI)F" if is_dir else f"{account}:F"
    try:
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
            capture_output=True,
            check=False,
        )
    except OSError as exc:  # pragma: no cover - Windows only
        raise VaultPermissionError("Unable to secure browser profile path") from exc
    if result.returncode != 0:  # pragma: no cover - Windows only
        raise VaultPermissionError("Unable to secure browser profile path")


def _read_private_file(path: Path) -> bytes:
    if path.is_symlink():
        raise VaultPermissionError("Refusing a symlinked browser profile")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise VaultPermissionError("Unable to safely open browser profile") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise VaultPermissionError("Browser profile is not a regular file")
        if sys.platform != "win32" and stat.S_IMODE(info.st_mode) & 0o077:
            raise VaultPermissionError("Browser profile is not user-only")
        if info.st_size > _MAX_ENVELOPE_BYTES:
            raise VaultCorruptError("Browser profile envelope is too large")
        chunks: list[bytes] = []
        remaining = _MAX_ENVELOPE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > _MAX_ENVELOPE_BYTES:
            raise VaultCorruptError("Browser profile envelope is too large")
        return raw
    finally:
        os.close(fd)


def _atomic_private_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _restrict_path(path.parent, is_dir=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        if sys.platform != "win32":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _restrict_path(temp, is_dir=False)
        os.replace(temp, path)
        _restrict_path(path, is_dir=False)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
        raise


def _fsync_directory(path: Path) -> None:
    if sys.platform == "win32":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        # Some filesystems do not support directory fsync.  The file itself was
        # already flushed and atomically replaced.
        pass


def _lock_file(handle: BinaryIO) -> None:
    try:
        if sys.platform == "win32":  # pragma: no cover - Windows only
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        raise ProfileInUseError("Browser profile is already in use") from exc


def _unlock_file(handle: BinaryIO) -> None:
    try:
        if sys.platform == "win32":  # pragma: no cover - Windows only
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
