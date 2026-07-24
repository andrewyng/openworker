"""Session-scoped durable storage for oversized tool results.

Large tool outputs are written under a hashed session directory, projected into a
bounded head/tail envelope for the model, and retrieved later via `read_tool_output`
or the HTTP paging route. References are opaque (`out_…`) and never embed paths or
provider-controlled IDs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import aisuite as ai

from .local_state import restrict_to_user

_REF = re.compile(r"^out_[0-9a-f]{32}$")
_SCHEMA_VERSION = 1
# Leave this much free space on the volume before accepting a write.
_MIN_DISK_HEADROOM_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ToolOutputPolicy:
    inline_limit_chars: int = 8_000
    preview_chars: int = 2_000
    read_default_bytes: int = 4_000
    read_max_bytes: int = 8_000
    max_single_output_bytes: int = 64 * 1024 * 1024
    max_session_output_bytes: int = 512 * 1024 * 1024
    min_disk_headroom_bytes: int = _MIN_DISK_HEADROOM_BYTES


@dataclass(frozen=True)
class StoredToolOutput:
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
class ProjectedToolOutput:
    model_value: Any
    stored: StoredToolOutput | None


@dataclass(frozen=True)
class ToolOutputPage:
    output_ref: str
    offset_bytes: int
    content: str
    next_offset_bytes: int | None
    complete: bool
    total_chars: int
    total_bytes: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "output_ref": self.output_ref,
            "offset_bytes": self.offset_bytes,
            "content": self.content,
            "next_offset_bytes": self.next_offset_bytes,
            "complete": self.complete,
            "total_chars": self.total_chars,
            "total_bytes": self.total_bytes,
            "sha256": self.sha256,
        }


class ToolOutputStoreError(RuntimeError):
    """Raised when an oversized result cannot be retained (quota, disk, I/O)."""


def serialize_tool_result(result: Any) -> str:
    """Canonical serialization shared by projector, engine, and audit sizing."""
    return result if isinstance(result, str) else json.dumps(result, default=str)


def session_output_key(session_id: str) -> str:
    """Stable hashed directory name for a session. Session id never appears on disk."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def is_valid_output_ref(ref: str) -> bool:
    return bool(isinstance(ref, str) and _REF.fullmatch(ref))


class SessionToolOutputStore:
    """Filesystem store scoped to one opaque session directory."""

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
        self.session_id = session_id
        self._lock = threading.Lock()
        self.directory = Path(root) / "tool-outputs" / session_output_key(session_id)
        if create:
            self.directory.mkdir(parents=True, exist_ok=True)
            restrict_to_user(self.directory, is_dir=True)
            self.captures_dir = self.directory / "captures"
            self.captures_dir.mkdir(exist_ok=True)
            restrict_to_user(self.captures_dir, is_dir=True)
        else:
            if not self.directory.is_dir():
                raise FileNotFoundError("session tool output store not found")
            self.captures_dir = self.directory / "captures"

    def _path(self, ref: str, suffix: str) -> Path:
        if not is_valid_output_ref(ref):
            raise ValueError("invalid output reference")
        return self.directory / f"{ref}{suffix}"

    def _used_bytes(self) -> int:
        total = 0
        paths = list(self.directory.glob("out_*.txt"))
        paths.extend(self.directory.glob("out_*.json"))
        if self.captures_dir.is_dir():
            paths.extend(self.captures_dir.glob("*.log"))
        for path in paths:
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

    def _ensure_quota(self, nbytes: int, *, content_bytes: int | None = None) -> None:
        single_bytes = nbytes if content_bytes is None else content_bytes
        if single_bytes > self.policy.max_single_output_bytes:
            raise ToolOutputStoreError("tool output exceeds per-result quota")
        if self._used_bytes() + nbytes > self.policy.max_session_output_bytes:
            raise ToolOutputStoreError("tool output exceeds session quota")
        try:
            usage = shutil.disk_usage(self.directory)
            if usage.free < self.policy.min_disk_headroom_bytes + nbytes:
                raise ToolOutputStoreError("insufficient disk headroom for tool output")
        except FileNotFoundError:
            pass

    def _atomic_write(self, path: Path, data: bytes) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            restrict_to_user(Path(tmp), is_dir=False)
            os.replace(tmp, path)
            restrict_to_user(path, is_dir=False)
        except BaseException:
            try:
                os.unlink(tmp)
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
        raw = serialized.encode("utf-8")
        with self._lock:
            record = StoredToolOutput(
                ref=f"out_{secrets.token_hex(16)}",
                tool_call_id=str(tool_call_id),
                tool_name=str(tool_name),
                chars=len(serialized),
                bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                created_at=time.time(),
                content_complete=content_complete,
            )
            meta = {**asdict(record), "schema_version": record.schema_version}
            meta_raw = json.dumps(meta, sort_keys=True).encode("utf-8")
            self._ensure_quota(
                len(raw) + len(meta_raw),
                content_bytes=len(raw),
            )
            content_path = self._path(record.ref, ".txt")
            # Publish content first so a crash never leaves a metadata pointer without bytes.
            self._atomic_write(content_path, raw)
            try:
                self._atomic_write(self._path(record.ref, ".json"), meta_raw)
            except BaseException:
                # A metadata failure must not leave an unreachable content blob consuming
                # this known session's quota forever.
                try:
                    content_path.unlink()
                except OSError:
                    pass
                raise
            return record

    def read(
        self,
        ref: str,
        offset_bytes: int = 0,
        limit_bytes: int | None = None,
    ) -> dict[str, Any]:
        if offset_bytes < 0:
            raise ValueError("offset_bytes must be non-negative")
        limit = self.policy.read_default_bytes if limit_bytes is None else limit_bytes
        if (
            not isinstance(limit, int)
            or limit < 1
            or limit > self.policy.read_max_bytes
        ):
            raise ValueError("invalid limit_bytes")
        # Validate ref before touching the filesystem (traversal / malformed → 400).
        meta_path = self._path(ref, ".json")
        content_path = self._path(ref, ".txt")
        if not meta_path.is_file() or not content_path.is_file():
            raise KeyError("unknown output reference")
        try:
            info = json.loads(meta_path.read_text(encoding="utf-8"))
            total_bytes = int(info["bytes"])
            total_chars = int(info["chars"])
            digest = str(info["sha256"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ToolOutputStoreError("tool output metadata is corrupt") from exc
        try:
            actual_bytes = content_path.stat().st_size
        except OSError as exc:
            raise ToolOutputStoreError("tool output content is unavailable") from exc
        if total_bytes != actual_bytes:
            raise ToolOutputStoreError("tool output content is corrupt")
        if offset_bytes > total_bytes:
            raise ValueError("offset beyond output")
        try:
            with content_path.open("rb") as stream:
                if 0 < offset_bytes < total_bytes:
                    # Stored content is valid UTF-8. A byte offset is a boundary exactly
                    # when the byte at that position is not a continuation byte. Inspect
                    # one byte instead of decoding the entire prefix for every page.
                    stream.seek(offset_bytes)
                    current = stream.read(1)
                    if current and current[0] & 0xC0 == 0x80:
                        raise ValueError("offset_bytes is not on a UTF-8 boundary")
                stream.seek(offset_bytes)
                page = stream.read(limit)
        except OSError as exc:
            raise ToolOutputStoreError("tool output content is unavailable") from exc
        complete = offset_bytes + len(page) >= total_bytes
        if not complete:
            while page:
                try:
                    text = page.decode("utf-8")
                    break
                except UnicodeDecodeError as exc:
                    if exc.reason != "unexpected end of data":
                        raise ValueError(
                            "offset_bytes is not on a UTF-8 boundary"
                        ) from exc
                    page = page[: exc.start]
            else:
                raise ValueError(
                    "limit_bytes is too small for the next UTF-8 character"
                )
        else:
            try:
                text = page.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("offset_bytes is not on a UTF-8 boundary") from exc
        nxt = offset_bytes + len(page)
        return ToolOutputPage(
            output_ref=ref,
            offset_bytes=offset_bytes,
            content=text,
            next_offset_bytes=None if complete else nxt,
            complete=complete,
            total_chars=total_chars,
            total_bytes=total_bytes,
            sha256=digest,
        ).as_dict()

    def list_references(self) -> set[str]:
        refs: set[str] = set()
        for path in self.directory.glob("out_*.json"):
            ref = path.stem
            if is_valid_output_ref(ref) and (self.directory / f"{ref}.txt").is_file():
                refs.add(ref)
        return refs

    def delete_all(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


class ToolResultProjector:
    """Serialize a tool result into either its inline form or a durable envelope."""

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
        self, tool_call_id: str, tool_name: str, result: Any
    ) -> ProjectedToolOutput:
        serialized = serialize_tool_result(result)
        content_complete = not (
            isinstance(result, dict) and result.get("retained_complete") is False
        )
        # Retrieval must never spill recursively. The tool factory sizes pages to
        # the inline policy; this guard handles custom callers or corrupt results.
        if tool_name == "read_tool_output":
            if len(serialized) > self.policy.inline_limit_chars:
                return ProjectedToolOutput(
                    model_value={
                        "error": "retrieved page exceeds the inline output limit",
                        "error_kind": "limit",
                    },
                    stored=None,
                )
            return ProjectedToolOutput(model_value=result, stored=None)
        if len(serialized) <= self.policy.inline_limit_chars:
            return ProjectedToolOutput(model_value=result, stored=None)
        record = self.store.put(
            tool_call_id,
            tool_name,
            serialized,
            content_complete=content_complete,
        )
        preview_chars = self.policy.preview_chars
        while True:
            head = preview_chars // 2
            tail = preview_chars - head
            omitted = len(serialized) - preview_chars
            preview = (
                serialized[:head]
                + f"\n\n[... {omitted} characters omitted ...]\n\n"
                + serialized[-tail:]
            )
            envelope = {
                "output_ref_version": 1,
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
            # JSON escaping can expand control characters and non-ASCII content.
            # Size the actual provider payload, not only the raw preview characters.
            if (
                len(serialize_tool_result(envelope))
                <= self.policy.inline_limit_chars
                or preview_chars <= 2
            ):
                break
            preview_chars = max(2, preview_chars // 2)
        return ProjectedToolOutput(model_value=envelope, stored=record)


def read_tool_output_tool(store: SessionToolOutputStore):
    """Low-risk tool factory closed over a session store."""

    default_limit = store.policy.read_default_bytes

    def read_tool_output(
        output_ref: str,
        offset_bytes: int = 0,
        limit_bytes: int = default_limit,
    ) -> dict[str, Any]:
        try:
            requested = limit_bytes
            while requested >= 1:
                page = store.read(output_ref, offset_bytes, requested)
                if (
                    len(serialize_tool_result(page))
                    <= store.policy.inline_limit_chars
                ):
                    return page
                requested //= 2
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
                "output_ref from a truncated tool result envelope, plus optional "
                "offset_bytes / limit_bytes. Does not accept filesystem paths."
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
                            f"Max bytes to return (default {default_limit}, "
                            f"max {store.policy.read_max_bytes})."
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
