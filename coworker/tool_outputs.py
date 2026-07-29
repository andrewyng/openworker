"""Session-scoped storage and bounded model projection for tool results."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from itertools import chain
from pathlib import Path
from typing import Any, Iterable, Iterator

import aisuite as ai

from .secrets import _restrict_to_user as restrict_to_user

_OUTPUT_REF = re.compile(r"^out_[0-9a-f]{32}$")
_SESSION_OUTPUT_KEY = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_VERSION = 1
DEFAULT_MAX_GLOBAL_OUTPUT_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_RETENTION_AGE_SECONDS = 30 * 24 * 60 * 60
_GLOBAL_STORE_LOCK = threading.RLock()
_EVICTION_PREFIX = ".evict-"
_SERIALIZATION_CHUNK_CHARS = 64 * 1024
_MAX_METADATA_BYTES = 64 * 1024
_MANAGED_RESULT_FILE = re.compile(r"^(out_[0-9a-f]{32})\.(txt|json)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ToolOutputPolicy:
    """Limits applied while retaining and exposing tool output."""

    inline_limit_chars: int = 8_000
    preview_chars: int = 2_000
    read_default_bytes: int = 4_000
    read_max_bytes: int = 8_000
    max_single_output_bytes: int = 64 * 1024 * 1024
    max_session_output_bytes: int = 512 * 1024 * 1024
    max_global_output_bytes: int = DEFAULT_MAX_GLOBAL_OUTPUT_BYTES
    max_retention_age_seconds: int = DEFAULT_MAX_RETENTION_AGE_SECONDS
    min_disk_headroom_bytes: int = 64 * 1024 * 1024


class ToolOutputStoreError(RuntimeError):
    """A retained result could not be stored or verified safely."""


@dataclass(frozen=True)
class ProjectedToolOutput:
    """The bounded value for the model and any durable backing record."""

    model_value: Any
    stored: StoredToolOutput | None


@dataclass(frozen=True)
class StoredToolOutput:
    """Metadata required to retrieve and verify one retained result."""

    ref: str
    tool_call_id: str
    tool_name: str
    chars: int
    bytes: int
    sha256: str
    created_at: float
    content_complete: bool = True
    schema_version: int = _SCHEMA_VERSION


@dataclass(frozen=True)
class ToolOutputPage:
    """One UTF-8-safe byte page of retained output."""

    output_ref: str
    offset_bytes: int
    content: str
    next_offset_bytes: int | None
    complete: bool
    total_chars: int
    total_bytes: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetainedOutputCollection:
    """Result of applying orphan, age, and global-cap retention."""

    removed_sessions: int
    removed_bytes: int
    remaining_bytes: int
    over_cap_bytes: int
    skipped_unsafe_entries: int


@dataclass(frozen=True)
class _StoreDirectory:
    path: Path
    key: str
    bytes: int
    modified_at: float


def serialize_tool_result(result: Any) -> str:
    """Serialize a result exactly once for sizing and durable retention."""

    return result if isinstance(result, str) else json.dumps(result, default=str)


def _iter_text_chunks(value: str) -> Iterator[str]:
    """Yield bounded slices without copying the complete string."""

    for start in range(0, len(value), _SERIALIZATION_CHUNK_CHARS):
        yield value[start : start + _SERIALIZATION_CHUNK_CHARS]


def _iter_json_string(value: str) -> Iterator[str]:
    """Encode a JSON string in bounded pieces.

    Each slice can be escaped independently because the surrounding quotes are
    emitted only once. This keeps a large nested string from becoming a second
    equally large allocation inside ``json.dumps``.
    """

    yield '"'
    for chunk in _iter_text_chunks(value):
        yield json.dumps(chunk)[1:-1]
    yield '"'


def _json_object_key(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return json.dumps(value)
    raise TypeError(
        f"keys must be str, int, float, bool or None, not {type(value).__name__}"
    )


def _iter_json_value(value: Any, active_containers: set[int]) -> Iterator[str]:
    """Stream the subset of JSON used by tool results with ``default=str``."""

    if isinstance(value, str):
        yield from _iter_json_string(value)
        return
    if value is None:
        yield "null"
        return
    if value is True:
        yield "true"
        return
    if value is False:
        yield "false"
        return
    if isinstance(value, (int, float)):
        yield json.dumps(value)
        return

    if isinstance(value, dict):
        container_id = id(value)
        if container_id in active_containers:
            raise ValueError("Circular reference detected")
        active_containers.add(container_id)
        try:
            yield "{"
            for index, (key, item) in enumerate(value.items()):
                if index:
                    yield ", "
                yield from _iter_json_string(_json_object_key(key))
                yield ": "
                yield from _iter_json_value(item, active_containers)
            yield "}"
        finally:
            active_containers.remove(container_id)
        return

    if isinstance(value, (list, tuple)):
        container_id = id(value)
        if container_id in active_containers:
            raise ValueError("Circular reference detected")
        active_containers.add(container_id)
        try:
            yield "["
            for index, item in enumerate(value):
                if index:
                    yield ", "
                yield from _iter_json_value(item, active_containers)
            yield "]"
        finally:
            active_containers.remove(container_id)
        return

    yield from _iter_json_string(str(value))


def _iter_serialized_tool_result(result: Any) -> Iterator[str]:
    if isinstance(result, str):
        yield from _iter_text_chunks(result)
    else:
        yield from _iter_json_value(result, set())


def session_output_key(session_id: str) -> str:
    """Return a stable directory key without exposing the session identifier."""

    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def is_valid_output_ref(ref: str) -> bool:
    """Return whether a value is an opaque retained-output reference."""

    return isinstance(ref, str) and bool(_OUTPUT_REF.fullmatch(ref))


def _real_directory_stat(path: Path) -> os.stat_result | None:
    """lstat a directory, rejecting symlinks and other file types."""

    try:
        path_stat = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
        return None
    return path_stat


def _inspect_flat_store(path: Path, key: str) -> _StoreDirectory | None:
    """Size one store without following links or descending into subdirectories."""

    directory_stat = _real_directory_stat(path)
    if directory_stat is None:
        return None
    total = 0
    try:
        entries = os.scandir(path)
        with entries:
            for entry in entries:
                entry_stat = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(entry_stat.st_mode):
                    return None
                total += entry_stat.st_size
    except OSError:
        return None
    return _StoreDirectory(
        path=path,
        key=key,
        bytes=total,
        modified_at=directory_stat.st_mtime,
    )


def _managed_output_bytes(output_root: Path, *, strict: bool) -> int:
    """Count managed store/quarantine bytes without traversing symlinks."""

    if _real_directory_stat(output_root) is None:
        if output_root.exists() and strict:
            raise ToolOutputStoreError("unsafe global tool output store")
        return 0
    total = 0
    try:
        entries = os.scandir(output_root)
        with entries:
            for entry in entries:
                if not (
                    _SESSION_OUTPUT_KEY.fullmatch(entry.name)
                    or entry.name.startswith(_EVICTION_PREFIX)
                ):
                    continue
                inspected = _inspect_flat_store(Path(entry.path), entry.name)
                if inspected is None:
                    if strict:
                        raise ToolOutputStoreError(
                            "unsafe retained tool output directory"
                        )
                    continue
                total += inspected.bytes
    except ToolOutputStoreError:
        raise
    except OSError as exc:
        if strict:
            raise ToolOutputStoreError(
                "global tool output quota is unavailable"
            ) from exc
    return total


def _unlink_flat_entry(path: Path) -> bool:
    """Remove a quarantined flat store without ever following a symlink."""

    try:
        path_stat = os.lstat(path)
    except FileNotFoundError:
        return True
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
        try:
            os.unlink(path)
            return True
        except OSError:
            return False
    try:
        entries = os.scandir(path)
        with entries:
            for entry in entries:
                entry_stat = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(entry_stat.st_mode):
                    return False
                os.unlink(entry.path)
        os.rmdir(path)
        return True
    except OSError:
        return False


def _evict_flat_store(output_root: Path, path: Path) -> bool:
    """Atomically quarantine then unlink one managed store."""

    if path.parent != output_root or not _SESSION_OUTPUT_KEY.fullmatch(path.name):
        return False
    quarantine = output_root / (f"{_EVICTION_PREFIX}{path.name}-{secrets.token_hex(8)}")
    try:
        os.replace(path, quarantine)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return _unlink_flat_entry(quarantine)


def collect_retained_output_stores(
    root: str | Path,
    *,
    known_session_ids: set[str] | None = None,
    active_session_ids: set[str] | None = None,
    orphan_grace_seconds: float = 24 * 60 * 60,
    policy: ToolOutputPolicy | None = None,
    now: float | None = None,
) -> RetainedOutputCollection:
    """Apply bounded cross-session retention without touching active stores.

    Unknown stores first become eligible after the orphan grace period. Every
    inactive store becomes eligible after the age limit. If managed bytes still
    exceed the global cap, the oldest remaining inactive stores are evicted.
    """

    chosen_policy = policy or ToolOutputPolicy()
    output_root = Path(root) / "tool-outputs"
    current_time = time.time() if now is None else now
    known_keys = {
        session_output_key(session_id) for session_id in known_session_ids or set()
    }
    active_keys = {
        session_output_key(session_id) for session_id in active_session_ids or set()
    }
    removed_sessions = 0
    removed_bytes = 0
    skipped_unsafe = 0

    with _GLOBAL_STORE_LOCK:
        if _real_directory_stat(output_root) is None:
            return RetainedOutputCollection(0, 0, 0, 0, 0)

        try:
            raw_entries = list(os.scandir(output_root))
        except OSError:
            return RetainedOutputCollection(0, 0, 0, 0, 1)

        stores: list[_StoreDirectory] = []
        for entry in raw_entries:
            if entry.name.startswith(_EVICTION_PREFIX):
                if not _unlink_flat_entry(Path(entry.path)):
                    skipped_unsafe += 1
                continue
            if not _SESSION_OUTPUT_KEY.fullmatch(entry.name):
                continue
            inspected = _inspect_flat_store(Path(entry.path), entry.name)
            if inspected is None:
                skipped_unsafe += 1
                continue
            stores.append(inspected)

        removed_keys: set[str] = set()

        def evict(store: _StoreDirectory) -> bool:
            nonlocal removed_sessions, removed_bytes
            if store.key in active_keys or store.key in removed_keys:
                return False
            if not _evict_flat_store(output_root, store.path):
                return False
            removed_keys.add(store.key)
            removed_sessions += 1
            removed_bytes += store.bytes
            return True

        oldest_first = sorted(stores, key=lambda item: (item.modified_at, item.key))
        orphan_grace = max(0.0, orphan_grace_seconds)
        max_age = max(0.0, float(chosen_policy.max_retention_age_seconds))
        for store in oldest_first:
            age = current_time - store.modified_at
            if (store.key not in known_keys and age >= orphan_grace) or age >= max_age:
                evict(store)

        remaining = sum(
            store.bytes for store in stores if store.key not in removed_keys
        )
        cap = max(0, int(chosen_policy.max_global_output_bytes))
        if remaining > cap:
            for store in oldest_first:
                if remaining <= cap:
                    break
                if evict(store):
                    remaining -= store.bytes

        remaining = _managed_output_bytes(output_root, strict=False)
        return RetainedOutputCollection(
            removed_sessions=removed_sessions,
            removed_bytes=removed_bytes,
            remaining_bytes=remaining,
            over_cap_bytes=max(0, remaining - cap),
            skipped_unsafe_entries=skipped_unsafe,
        )


class SessionToolOutputStore:
    """Filesystem store isolated to one session."""

    def __init__(
        self,
        root: str | Path,
        session_id: str,
        policy: ToolOutputPolicy | None = None,
        *,
        create: bool = True,
    ) -> None:
        if not session_id:
            raise ValueError("session_id is required")
        self.policy = policy or ToolOutputPolicy()
        self._lock = threading.Lock()
        self._verified_content: dict[
            str,
            tuple[int, int, int, int, int, str],
        ] = {}
        self.output_root = Path(root) / "tool-outputs"
        self.directory = self.output_root / session_output_key(session_id)
        if create:
            for directory, parents in (
                (self.output_root, True),
                (self.directory, False),
            ):
                try:
                    directory_stat = os.lstat(directory)
                except FileNotFoundError:
                    try:
                        directory.mkdir(parents=parents, exist_ok=False)
                    except FileExistsError:
                        pass
                    directory_stat = os.lstat(directory)
                if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(
                    directory_stat.st_mode
                ):
                    raise ToolOutputStoreError("unsafe retained tool output directory")
                restrict_to_user(directory, is_dir=True)
        else:
            try:
                root_stat = os.lstat(self.output_root)
                directory_stat = os.lstat(self.directory)
            except FileNotFoundError as exc:
                raise FileNotFoundError("session tool output store not found") from exc
            if (
                stat.S_ISLNK(root_stat.st_mode)
                or not stat.S_ISDIR(root_stat.st_mode)
                or stat.S_ISLNK(directory_stat.st_mode)
                or not stat.S_ISDIR(directory_stat.st_mode)
            ):
                raise ToolOutputStoreError("unsafe retained tool output directory")
        with _GLOBAL_STORE_LOCK, self._lock:
            self._reconcile_interrupted_writes()

    def _path(self, ref: str, suffix: str) -> Path:
        if not is_valid_output_ref(ref):
            raise ValueError("invalid output reference")
        return self.directory / f"{ref}{suffix}"

    def _assert_directories_safe(self) -> None:
        if (
            _real_directory_stat(self.output_root) is None
            or _real_directory_stat(self.directory) is None
        ):
            raise ToolOutputStoreError("unsafe retained tool output directory")

    def is_available(self) -> bool:
        """Return whether both managed directories still exist without links."""

        return (
            _real_directory_stat(self.output_root) is not None
            and _real_directory_stat(self.directory) is not None
        )

    def _fsync_directory(self) -> None:
        self._assert_directories_safe()
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(self.directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _reconcile_interrupted_writes(self) -> None:
        """Remove unpublished and half-published records after a crash."""

        self._assert_directories_safe()
        result_files: dict[str, dict[str, Path]] = {}
        removed = False
        try:
            entries = list(os.scandir(self.directory))
        except OSError as exc:
            raise ToolOutputStoreError(
                "retained tool output directory is unavailable"
            ) from exc
        for entry in entries:
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ToolOutputStoreError(
                    "retained tool output directory is unavailable"
                ) from exc
            entry_path = Path(entry.path)
            if entry.name.startswith(".pending-"):
                if stat.S_ISDIR(entry_stat.st_mode):
                    raise ToolOutputStoreError("unsafe retained tool output directory")
                try:
                    os.unlink(entry_path)
                except OSError as exc:
                    raise ToolOutputStoreError(
                        "interrupted tool output could not be reconciled"
                    ) from exc
                removed = True
                continue
            match = _MANAGED_RESULT_FILE.fullmatch(entry.name)
            if match:
                if not stat.S_ISREG(entry_stat.st_mode):
                    raise ToolOutputStoreError("unsafe retained tool output file")
                result_files.setdefault(match.group(1), {})[match.group(2)] = entry_path
            elif not stat.S_ISREG(entry_stat.st_mode):
                raise ToolOutputStoreError("unsafe retained tool output directory")

        for files in result_files.values():
            if set(files) == {"txt", "json"}:
                continue
            for path in files.values():
                try:
                    os.unlink(path)
                except OSError as exc:
                    raise ToolOutputStoreError(
                        "interrupted tool output could not be reconciled"
                    ) from exc
                removed = True
        if removed:
            self._fsync_directory()

    def _atomic_write(self, path: Path, data: bytes) -> None:
        self._assert_directories_safe()
        descriptor, pending_path = tempfile.mkstemp(
            prefix=".pending-",
            dir=self.directory,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            restrict_to_user(Path(pending_path), is_dir=False)
            os.replace(pending_path, path)
            self._fsync_directory()
        except BaseException:
            try:
                os.unlink(pending_path)
            except OSError:
                pass
            raise

    @staticmethod
    def _content_signature(stat: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )

    def _used_bytes(self) -> int:
        inspected = _inspect_flat_store(self.directory, self.directory.name)
        if inspected is None:
            raise ToolOutputStoreError("unsafe retained tool output directory")
        return inspected.bytes

    def _global_used_bytes(self) -> int:
        """Count managed regular files without traversing symlinks."""

        return _managed_output_bytes(self.output_root, strict=True)

    def _ensure_quota(
        self,
        total_write_bytes: int,
        content_bytes: int,
        *,
        used_session_bytes: int,
        used_global_bytes: int,
        available_disk_bytes: int,
    ) -> None:
        if content_bytes > self.policy.max_single_output_bytes:
            raise ToolOutputStoreError("tool output exceeds per-result quota")
        if (
            used_session_bytes + total_write_bytes
            > self.policy.max_session_output_bytes
        ):
            raise ToolOutputStoreError("tool output exceeds session quota")
        if used_global_bytes + total_write_bytes > self.policy.max_global_output_bytes:
            raise ToolOutputStoreError("tool output exceeds global quota")
        if (
            available_disk_bytes
            < self.policy.min_disk_headroom_bytes + total_write_bytes
        ):
            raise ToolOutputStoreError("insufficient disk headroom for tool output")

    def _put_serialized_chunks(
        self,
        tool_call_id: str,
        tool_name: str,
        chunks: Iterable[str],
        *,
        content_complete: bool = True,
        preview_chars: int | None = None,
    ) -> tuple[StoredToolOutput, str, str]:
        with _GLOBAL_STORE_LOCK, self._lock:
            self._assert_directories_safe()
            used_session_bytes = self._used_bytes()
            used_global_bytes = self._global_used_bytes()
            try:
                available_disk_bytes = shutil.disk_usage(self.directory).free
            except OSError as exc:
                raise ToolOutputStoreError(
                    "tool output disk quota is unavailable"
                ) from exc

            descriptor, pending_path_text = tempfile.mkstemp(
                prefix=".pending-",
                dir=self.directory,
            )
            pending_path = Path(pending_path_text)
            chars = 0
            content_bytes = 0
            digest = hashlib.sha256()
            preview_limit = max(
                2,
                self.policy.preview_chars if preview_chars is None else preview_chars,
            )
            preview_head = ""
            preview_tail = ""
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    for chunk in chunks:
                        if not isinstance(chunk, str):
                            raise TypeError(
                                "serialized tool output chunks must be strings"
                            )
                        for text_chunk in _iter_text_chunks(chunk):
                            chars += len(text_chunk)
                            raw_chunk = text_chunk.encode("utf-8")
                            content_bytes += len(raw_chunk)
                            self._ensure_quota(
                                content_bytes,
                                content_bytes,
                                used_session_bytes=used_session_bytes,
                                used_global_bytes=used_global_bytes,
                                available_disk_bytes=available_disk_bytes,
                            )
                            handle.write(raw_chunk)
                            digest.update(raw_chunk)
                            if len(preview_head) < preview_limit:
                                needed = preview_limit - len(preview_head)
                                preview_head += text_chunk[:needed]
                            preview_tail = (preview_tail + text_chunk)[-preview_limit:]
                    handle.flush()
                    os.fsync(handle.fileno())

                record = StoredToolOutput(
                    ref=f"out_{secrets.token_hex(16)}",
                    tool_call_id=str(tool_call_id),
                    tool_name=str(tool_name),
                    chars=chars,
                    bytes=content_bytes,
                    sha256=digest.hexdigest(),
                    created_at=time.time(),
                    content_complete=content_complete,
                )
                content_path = self._path(record.ref, ".txt")
                metadata_path = self._path(record.ref, ".json")
                metadata = json.dumps(
                    asdict(record),
                    sort_keys=True,
                ).encode("utf-8")
                if len(metadata) > _MAX_METADATA_BYTES:
                    raise ToolOutputStoreError(
                        "tool output metadata exceeds the supported limit"
                    )
                self._ensure_quota(
                    content_bytes + len(metadata),
                    content_bytes,
                    used_session_bytes=used_session_bytes,
                    used_global_bytes=used_global_bytes,
                    available_disk_bytes=available_disk_bytes,
                )
                restrict_to_user(pending_path, is_dir=False)
                os.replace(pending_path, content_path)
                self._fsync_directory()
                try:
                    self._atomic_write(metadata_path, metadata)
                except BaseException:
                    try:
                        os.unlink(content_path)
                        self._fsync_directory()
                    except OSError:
                        pass
                    raise
                content_stat = os.stat(content_path, follow_symlinks=False)
                if not stat.S_ISREG(content_stat.st_mode):
                    raise ToolOutputStoreError("unsafe retained tool output file")
                self._verified_content[record.ref] = (
                    *self._content_signature(content_stat),
                    record.sha256,
                )
                return record, preview_head, preview_tail
            except BaseException:
                try:
                    os.unlink(pending_path)
                except OSError:
                    pass
                raise

    def put(
        self,
        tool_call_id: str,
        tool_name: str,
        serialized: str,
        *,
        content_complete: bool = True,
    ) -> StoredToolOutput:
        if not isinstance(serialized, str):
            raise TypeError("serialized tool output must be a string")
        if len(serialized) > self.policy.max_single_output_bytes:
            raise ToolOutputStoreError("tool output exceeds per-result quota")
        record, _, _ = self._put_serialized_chunks(
            tool_call_id,
            tool_name,
            _iter_text_chunks(serialized),
            content_complete=content_complete,
        )
        return record

    @staticmethod
    def _open_regular_binary(path: Path):
        try:
            path_stat = os.lstat(path)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ToolOutputStoreError(
                "retained tool output file is unavailable"
            ) from exc
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            raise ToolOutputStoreError("unsafe retained tool output file")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ToolOutputStoreError(
                "unsafe or unavailable retained tool output file"
            ) from exc
        try:
            opened_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_dev != path_stat.st_dev
                or opened_stat.st_ino != path_stat.st_ino
            ):
                raise ToolOutputStoreError("unsafe retained tool output file")
            return os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise

    def read(
        self,
        ref: str,
        offset_bytes: int = 0,
        limit_bytes: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(offset_bytes, int) or offset_bytes < 0:
            raise ValueError("offset_bytes must be non-negative")
        limit = self.policy.read_default_bytes if limit_bytes is None else limit_bytes
        if (
            not isinstance(limit, int)
            or limit < 1
            or limit > self.policy.read_max_bytes
        ):
            raise ValueError("invalid limit_bytes")

        metadata_path = self._path(ref, ".json")
        content_path = self._path(ref, ".txt")
        with self._lock:
            self._assert_directories_safe()
            try:
                with self._open_regular_binary(metadata_path) as metadata_stream:
                    raw_metadata = metadata_stream.read(_MAX_METADATA_BYTES + 1)
            except FileNotFoundError as exc:
                raise KeyError("unknown output reference") from exc
            if len(raw_metadata) > _MAX_METADATA_BYTES:
                raise ToolOutputStoreError("tool output metadata is corrupt")
            try:
                metadata = json.loads(raw_metadata.decode("utf-8"))
                total_chars = int(metadata["chars"])
                total_bytes = int(metadata["bytes"])
                digest = str(metadata["sha256"])
                metadata_ref = str(metadata["ref"])
                schema_version = int(metadata["schema_version"])
            except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
                raise ToolOutputStoreError("tool output metadata is corrupt") from exc
            if (
                total_chars < 0
                or total_bytes < 0
                or metadata_ref != ref
                or schema_version != _SCHEMA_VERSION
                or not _SHA256.fullmatch(digest)
            ):
                raise ToolOutputStoreError("tool output metadata is corrupt")
            if offset_bytes > total_bytes:
                raise ValueError("offset beyond output")

            try:
                stream = self._open_regular_binary(content_path)
            except FileNotFoundError as exc:
                raise KeyError("unknown output reference") from exc
            try:
                with stream:
                    opened_stat = os.fstat(stream.fileno())
                    if opened_stat.st_size != total_bytes:
                        raise ToolOutputStoreError("tool output content is corrupt")
                    signature = self._content_signature(opened_stat)
                    if self._verified_content.get(ref) != (*signature, digest):
                        hasher = hashlib.sha256()
                        for chunk in iter(
                            lambda: stream.read(1024 * 1024),
                            b"",
                        ):
                            hasher.update(chunk)
                        if not secrets.compare_digest(
                            hasher.hexdigest(),
                            digest,
                        ):
                            raise ToolOutputStoreError("tool output content is corrupt")
                        self._verified_content[ref] = (*signature, digest)
                    if 0 < offset_bytes < total_bytes:
                        stream.seek(offset_bytes)
                        current = stream.read(1)
                        if current and current[0] & 0xC0 == 0x80:
                            raise ValueError("offset_bytes is not on a UTF-8 boundary")
                    stream.seek(offset_bytes)
                    raw_page = stream.read(limit)
            except OSError as exc:
                raise ToolOutputStoreError(
                    "tool output content is unavailable"
                ) from exc

        complete = offset_bytes + len(raw_page) >= total_bytes
        if complete:
            try:
                text = raw_page.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("offset_bytes is not on a UTF-8 boundary") from exc
        else:
            while raw_page:
                try:
                    text = raw_page.decode("utf-8")
                    break
                except UnicodeDecodeError as exc:
                    if exc.reason != "unexpected end of data":
                        raise ValueError(
                            "offset_bytes is not on a UTF-8 boundary"
                        ) from exc
                    raw_page = raw_page[: exc.start]
            else:
                raise ValueError(
                    "limit_bytes is too small for the next UTF-8 character"
                )

        next_offset = offset_bytes + len(raw_page)
        return ToolOutputPage(
            output_ref=ref,
            offset_bytes=offset_bytes,
            content=text,
            next_offset_bytes=None if complete else next_offset,
            complete=complete,
            total_chars=total_chars,
            total_bytes=total_bytes,
            sha256=digest,
        ).as_dict()

    def list_references(self) -> set[str]:
        with self._lock:
            self._assert_directories_safe()
            parts: dict[str, set[str]] = {}
            try:
                entries = os.scandir(self.directory)
                with entries:
                    for entry in entries:
                        match = _MANAGED_RESULT_FILE.fullmatch(entry.name)
                        if not match:
                            continue
                        entry_stat = entry.stat(follow_symlinks=False)
                        if not stat.S_ISREG(entry_stat.st_mode):
                            raise ToolOutputStoreError(
                                "unsafe retained tool output file"
                            )
                        parts.setdefault(match.group(1), set()).add(match.group(2))
            except ToolOutputStoreError:
                raise
            except OSError as exc:
                raise ToolOutputStoreError(
                    "retained tool output directory is unavailable"
                ) from exc
            return {
                ref for ref, suffixes in parts.items() if suffixes == {"txt", "json"}
            }

    def delete_all(self) -> None:
        """Delete every retained result for this session."""

        self._verified_content.clear()
        try:
            shutil.rmtree(self.directory)
        except FileNotFoundError:
            pass


class ToolResultProjector:
    """Keep small results inline and retain oversized results outside context."""

    def __init__(
        self,
        store: SessionToolOutputStore,
        policy: ToolOutputPolicy | None = None,
    ) -> None:
        self.store = store
        self.policy = policy or store.policy
        if self.policy.preview_chars < 2:
            raise ValueError("preview_chars must be at least 2")
        if self.policy.preview_chars >= self.policy.inline_limit_chars:
            raise ValueError("preview_chars must be smaller than inline_limit_chars")

    def project(
        self,
        tool_call_id: str,
        tool_name: str,
        result: Any,
    ) -> ProjectedToolOutput:
        if tool_name == "read_tool_output":
            serialized = serialize_tool_result(result)
            if len(serialized) > self.policy.inline_limit_chars:
                return ProjectedToolOutput(
                    model_value={
                        "error": "retrieved page exceeds the inline output limit",
                        "error_kind": "limit",
                    },
                    stored=None,
                )
            return ProjectedToolOutput(model_value=result, stored=None)
        if isinstance(result, str):
            if len(result) <= self.policy.inline_limit_chars:
                return ProjectedToolOutput(model_value=result, stored=None)
            serialized_chunks: Iterable[str] = _iter_text_chunks(result)
        else:
            iterator = _iter_serialized_tool_result(result)
            buffered_chunks: list[str] = []
            buffered_chars = 0
            for chunk in iterator:
                buffered_chunks.append(chunk)
                buffered_chars += len(chunk)
                if buffered_chars > self.policy.inline_limit_chars:
                    break
            else:
                return ProjectedToolOutput(model_value=result, stored=None)
            serialized_chunks = chain(buffered_chunks, iterator)
        content_complete = not (
            isinstance(result, dict)
            and (
                result.get("retained_complete") is False
                or result.get("truncated") is True
            )
        )
        record, retained_head, retained_tail = self.store._put_serialized_chunks(
            tool_call_id,
            tool_name,
            serialized_chunks,
            content_complete=content_complete,
            preview_chars=self.policy.preview_chars,
        )
        preview_chars = self.policy.preview_chars
        while True:
            head_chars = preview_chars // 2
            tail_chars = preview_chars - head_chars
            omitted_chars = record.chars - preview_chars
            preview = (
                retained_head[:head_chars]
                + f"\n\n[... {omitted_chars} characters omitted ...]\n\n"
                + retained_tail[-tail_chars:]
            )
            envelope = {
                "output_ref_version": _SCHEMA_VERSION,
                "output_ref": record.ref,
                "truncated": True,
                "original_chars": record.chars,
                "original_bytes": record.bytes,
                "sha256": record.sha256,
                "content_complete": record.content_complete,
                "preview": preview,
                "instruction": (
                    (
                        "Use read_tool_output with output_ref and offset_bytes to inspect "
                        "the complete result."
                    )
                    if record.content_complete
                    else (
                        "Use read_tool_output with output_ref and offset_bytes to inspect "
                        "the retained portion. The source exceeded its capture quota, so "
                        "the original output is not fully recoverable."
                    )
                ),
            }
            if (
                len(serialize_tool_result(envelope)) <= self.policy.inline_limit_chars
                or preview_chars <= 2
            ):
                return ProjectedToolOutput(model_value=envelope, stored=record)
            preview_chars = max(2, preview_chars // 2)


def read_tool_output_tool(store: SessionToolOutputStore):
    """Create the low-risk tool used to retrieve retained output by opaque ref."""

    default_limit = store.policy.read_default_bytes

    def read_tool_output(
        output_ref: str,
        offset_bytes: int = 0,
        limit_bytes: int = default_limit,
    ) -> dict[str, Any]:
        try:
            requested_bytes = limit_bytes
            while requested_bytes >= 1:
                page = store.read(output_ref, offset_bytes, requested_bytes)
                if len(serialize_tool_result(page)) <= store.policy.inline_limit_chars:
                    return page
                requested_bytes //= 2
            return {
                "error": "retrieved page exceeds the inline output limit",
                "error_kind": "limit",
            }
        except ValueError as exc:
            return {"error": str(exc), "error_kind": "invalid"}
        except KeyError as exc:
            return {"error": str(exc), "error_kind": "missing"}
        except ToolOutputStoreError as exc:
            return {"error": str(exc), "error_kind": "corrupt"}

    read_tool_output.__name__ = "read_tool_output"
    read_tool_output.__coworker_schema__ = {
        "type": "function",
        "function": {
            "name": "read_tool_output",
            "description": (
                "Read an exact bounded page of a retained tool output. Pass the "
                "opaque output_ref from a truncated result envelope."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "output_ref": {
                        "type": "string",
                        "description": "Opaque reference from a truncated tool result.",
                    },
                    "offset_bytes": {
                        "type": "integer",
                        "description": "Byte offset into the retained UTF-8 output.",
                    },
                    "limit_bytes": {
                        "type": "integer",
                        "description": (
                            f"Maximum bytes to return (default {default_limit}, "
                            f"maximum {store.policy.read_max_bytes})."
                        ),
                    },
                },
                "required": ["output_ref"],
            },
        },
    }
    read_tool_output.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="read_tool_output",
        category="context",
        risk_level="low",
        capabilities=["read"],
        requires_approval=False,
    )
    return read_tool_output
