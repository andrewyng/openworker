"""Bound model-facing tool results without discarding the canonical output.

Tools should be free to return the data their caller requested.  The turn engine, not
each individual connector, owns the provider context budget.  Small results therefore
pass through unchanged; large results are written inside the session workspace and are
replaced with a compact, pageable reference for the model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import aisuite as ai


DEFAULT_INLINE_CHARS = 40_000
DEFAULT_READ_CHARS = 20_000
MAX_READ_CHARS = 40_000
DEFAULT_MAX_RESULT_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_STORE_BYTES = 512 * 1024 * 1024
_PREVIEW_HEAD_CHARS = 8_000
_PREVIEW_TAIL_CHARS = 4_000


@dataclass(frozen=True)
class PreparedToolResult:
    """The bounded value sent to the model plus its optional durable reference."""

    value: Any
    original_chars: int
    reference: Optional[str] = None
    externalized: bool = False


class ToolResultStore:
    """Externalize oversized tool results into a workspace-private result directory."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        inline_chars: int = DEFAULT_INLINE_CHARS,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
        max_store_bytes: int = DEFAULT_MAX_STORE_BYTES,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.result_dir = self.workspace / ".openworker" / "tool-results"
        self.inline_chars = max(1, int(inline_chars))
        self.max_result_bytes = max(1, int(max_result_bytes))
        self.max_store_bytes = max(self.max_result_bytes, int(max_store_bytes))

    def prepare(self, tool_name: str, result: Any) -> PreparedToolResult:
        """Return *result* unchanged when small, otherwise persist and reference it."""
        serialized = _serialize_for_model(result)
        original_chars = len(serialized)
        if original_chars <= self.inline_chars:
            return PreparedToolResult(value=result, original_chars=original_chars)

        # The paging tool is already responsible for bounding its own response. Never
        # externalize a page again: that would produce a new reference instead of making
        # progress through the original one.
        if tool_name == "read_tool_result":
            return PreparedToolResult(
                value={
                    "error": "tool-result page exceeds the inline context limit",
                    "recoverable": False,
                },
                original_chars=original_chars,
            )

        digest = hashlib.sha256(
            serialized.encode("utf-8", errors="replace")
        ).hexdigest()
        suffix = ".txt" if isinstance(result, str) else ".json"
        filename = f"{_safe_name(tool_name)}-{digest[:20]}{suffix}"
        target = self.result_dir / filename
        reference = target.relative_to(self.workspace).as_posix()
        stored_text = _serialize_for_storage(result, fallback=serialized)
        storage_error: Optional[str] = None

        try:
            self.result_dir.mkdir(parents=True, exist_ok=True)
            try:
                self.result_dir.chmod(0o700)
            except OSError:
                pass
            if not target.exists():
                encoded_bytes = len(stored_text.encode("utf-8"))
                if encoded_bytes > self.max_result_bytes:
                    raise OSError(
                        "tool result exceeds the per-result retention quota "
                        f"({encoded_bytes} > {self.max_result_bytes} bytes)"
                    )
                used_bytes = sum(
                    path.stat().st_size
                    for path in self.result_dir.iterdir()
                    if path.is_file()
                )
                if used_bytes + encoded_bytes > self.max_store_bytes:
                    raise OSError(
                        "tool-result store exceeds its workspace quota "
                        f"({used_bytes + encoded_bytes} > {self.max_store_bytes} bytes)"
                    )
                _atomic_write_text(target, stored_text)
        except OSError as exc:
            reference = ""
            storage_error = f"{type(exc).__name__}: {exc}"

        value: dict[str, Any] = {
            "openworker_large_result": True,
            "summary": _summary(tool_name, result, original_chars),
            "original_chars": original_chars,
            "sha256": digest,
            "preview": _bounded_preview(stored_text),
        }
        if reference:
            value.update(
                {
                    "result_ref": reference,
                    "read_instruction": (
                        "The complete result is stored locally. Call read_tool_result "
                        f'with ref="{reference}" and offset=0 to read it in bounded pages.'
                    ),
                }
            )
        else:
            value.update(
                {
                    "storage_error": storage_error or "result could not be stored",
                    "read_instruction": (
                        "Only the bounded preview is available because local persistence "
                        "failed; do not assume the preview is complete."
                    ),
                }
            )

        return PreparedToolResult(
            value=value,
            original_chars=original_chars,
            reference=reference or None,
            externalized=bool(reference),
        )

    def reader_tool(self):
        """Build the workspace-scoped, bounded reader exposed to the model."""
        store = self

        def read_tool_result(
            ref: str,
            offset: int = 0,
            max_chars: int = DEFAULT_READ_CHARS,
        ) -> dict[str, Any]:
            start = offset if isinstance(offset, int) and offset >= 0 else 0
            limit = (
                max_chars
                if isinstance(max_chars, int) and max_chars > 0
                else DEFAULT_READ_CHARS
            )
            limit = min(limit, MAX_READ_CHARS)
            target = store._resolve_reference(ref)
            if target is None:
                return {"error": "invalid tool-result reference"}
            if not target.is_file():
                return {"error": f"tool result not found: {ref}"}

            requested = limit
            while requested > 0:
                page = store._read_page(target, start=start, limit=requested)
                if "error" in page:
                    return page
                if len(_serialize_for_model(page)) <= store.inline_chars:
                    return page
                requested //= 2
            return {"error": "tool-result page cannot fit the inline context limit"}

        read_tool_result.__name__ = "read_tool_result"
        read_tool_result.__doc__ = _READER_SCHEMA["function"]["description"]
        read_tool_result.__aisuite_tool_metadata__ = ai.ToolMetadata(
            name="read_tool_result",
            category="filesystem",
            risk_level="low",
            capabilities=["read"],
            requires_approval=False,
        )
        read_tool_result.__coworker_schema__ = _READER_SCHEMA
        return read_tool_result

    def _resolve_reference(self, ref: str) -> Optional[Path]:
        if not isinstance(ref, str) or not ref.strip():
            return None
        raw = Path(ref).expanduser()
        target = raw.resolve() if raw.is_absolute() else (self.workspace / raw).resolve()
        try:
            target.relative_to(self.result_dir.resolve())
        except ValueError:
            return None
        return target

    def _read_page(self, target: Path, *, start: int, limit: int) -> dict[str, Any]:
        try:
            total = target.stat().st_size
            if start > total:
                return {"error": "offset is beyond the end of the tool result"}
            with open(target, "rb") as fh:
                if 0 < start < total:
                    fh.seek(start)
                    current = fh.read(1)
                    if current and current[0] & 0xC0 == 0x80:
                        return {"error": "offset is not on a UTF-8 boundary"}
                fh.seek(start)
                data = fh.read(limit)
        except OSError as exc:
            return {"error": f"tool result read failed: {exc}"}

        complete = start + len(data) >= total
        if not complete:
            while data:
                try:
                    content = data.decode("utf-8")
                    break
                except UnicodeDecodeError as exc:
                    if exc.reason != "unexpected end of data":
                        return {"error": "stored tool result is not valid UTF-8"}
                    data = data[: exc.start]
            else:
                return {"error": "page size is too small for the next UTF-8 character"}
        else:
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError:
                return {"error": "stored tool result is not valid UTF-8"}

        next_offset = start + len(data)
        result: dict[str, Any] = {
            "result_ref": target.relative_to(self.workspace).as_posix(),
            "offset": start,
            "next_offset": next_offset,
            "total_bytes": total,
            "complete": next_offset >= total,
            "content": content,
        }
        if next_offset < total:
            result["read_instruction"] = (
                f"Call read_tool_result again with offset={next_offset} to continue."
            )
        return result


_READER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_tool_result",
        "description": (
            "Read a bounded page from a large tool result that OpenWorker stored locally. "
            "Use the result_ref and next_offset returned by prior tool results. Offsets are "
            "UTF-8 byte offsets; each call is capped so the result cannot flood context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "The result_ref supplied by an earlier tool result.",
                },
                "offset": {
                    "type": "integer",
                    "description": "UTF-8 byte offset to start at (default 0).",
                },
                "max_chars": {
                    "type": "integer",
                    "description": (
                        f"Maximum bytes to return (default {DEFAULT_READ_CHARS}, "
                        f"hard cap {MAX_READ_CHARS})."
                    ),
                },
            },
            "required": ["ref"],
        },
    },
}


def _serialize_for_model(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str)
    except Exception:
        return str(result)


def _serialize_for_storage(result: Any, *, fallback: str) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str, ensure_ascii=False, indent=2)
    except Exception:
        return fallback


def _summary(tool_name: str, result: Any, original_chars: int) -> str:
    detail = type(result).__name__
    if isinstance(result, dict):
        keys = [str(key) for key in list(result)[:20]]
        detail = f"object with keys: {', '.join(keys) or '(none)'}"
    elif isinstance(result, (list, tuple)):
        detail = f"{type(result).__name__} with {len(result)} item(s)"
    elif isinstance(result, str):
        detail = "text"
    return (
        f"{tool_name} returned a large {detail} result ({original_chars} characters). "
        "A bounded preview is included; the complete value is available by reference."
    )


def _bounded_preview(text: str) -> str:
    budget = _PREVIEW_HEAD_CHARS + _PREVIEW_TAIL_CHARS
    if len(text) <= budget:
        return text
    omitted = len(text) - budget
    return (
        text[:_PREVIEW_HEAD_CHARS]
        + f"\n\n... [{omitted} characters omitted from preview] ...\n\n"
        + text[-_PREVIEW_TAIL_CHARS:]
    )


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "tool")).strip("-._")
    return (safe or "tool")[:80]


def _atomic_write_text(target: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(temporary, target)
        try:
            target.chmod(0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
