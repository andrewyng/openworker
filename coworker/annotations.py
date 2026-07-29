"""Validation and provider-facing expansion for artifact annotations.

Annotations are persisted in a user message's ``_display`` sidecar so the GUI can
reopen them after a restart. Providers never receive that private shape directly:
``append_annotation_context`` converts it into ordinary text and image parts at
send time.
"""

from __future__ import annotations

import json
import math
from typing import Any, Optional

from .attachments import MAX_ATTACHMENTS, MAX_IMAGE_CHARS

MAX_ANNOTATIONS = MAX_ATTACHMENTS
MAX_COMMENT_CHARS = 10_000
MAX_SELECTED_TEXT_CHARS = 20_000


def _short_string(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str) or len(value) > limit:
        return None
    return value


def _normalized_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 1:
        return None
    return number


def _validate_rect(value: Any) -> Optional[dict[str, float]]:
    if not isinstance(value, dict):
        return None
    rect = {key: _normalized_number(value.get(key)) for key in ("x", "y", "width", "height")}
    if any(number is None for number in rect.values()):
        return None
    if not rect["width"] or not rect["height"]:
        return None
    # A tiny rounding overrun should not make an otherwise valid browser selection unsafe.
    if rect["x"] + rect["width"] > 1.001 or rect["y"] + rect["height"] > 1.001:
        return None
    return {key: float(number) for key, number in rect.items() if number is not None}


def validate_annotations(
    raw: Any, *, attachment_count: int = 0
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Return a compact, sanitized annotation list or a user-facing validation error."""
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return [], "Invalid annotations: expected a list."
    if len(raw) > MAX_ANNOTATIONS or len(raw) + attachment_count > MAX_ATTACHMENTS:
        return [], f"Too many combined attachments and annotations (limit {MAX_ATTACHMENTS})."

    clean: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            return [], "Invalid annotation: expected an object."
        annotation_id = _short_string(item.get("id"), 128)
        comment = _short_string(item.get("comment"), MAX_COMMENT_CHARS)
        artifact = item.get("artifact")
        target = item.get("target")
        preview = item.get("preview")
        if not annotation_id or comment is None or not comment.strip():
            return [], "Invalid annotation id or comment."
        if not isinstance(artifact, dict):
            return [], "Invalid annotation artifact."
        path = _short_string(artifact.get("path"), 4096)
        name = _short_string(artifact.get("name"), 1024)
        kind = _short_string(artifact.get("kind"), 64)
        sha256 = _short_string(artifact.get("sha256"), 64)
        if (
            not path
            or not name
            or not kind
            or not sha256
            or len(sha256) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in sha256)
        ):
            return [], "Invalid annotation artifact identity."
        if not isinstance(target, dict):
            return [], "Invalid annotation target."
        target_kind = target.get("kind")
        rect = _validate_rect(target.get("rect"))
        if target_kind not in {"region", "text", "dom"} or rect is None:
            return [], "Invalid annotation target coordinates."

        clean_target: dict[str, Any] = {"kind": target_kind, "rect": rect}
        page = target.get("page")
        if page is not None:
            if isinstance(page, bool) or not isinstance(page, int) or page < 1 or page > 100_000:
                return [], "Invalid annotation page number."
            clean_target["page"] = page
        if target_kind == "text":
            exact = _short_string(target.get("exact"), MAX_SELECTED_TEXT_CHARS)
            if not exact:
                return [], "Invalid annotation selected text."
            clean_target["exact"] = exact
            for key in ("prefix", "suffix"):
                value = _short_string(target.get(key), 1000)
                if value:
                    clean_target[key] = value
        elif target_kind == "dom":
            selector = _short_string(target.get("selector"), 4096)
            if not selector:
                return [], "Invalid annotation DOM selector."
            clean_target["selector"] = selector
            for key, limit in (("tag", 64), ("exact", MAX_SELECTED_TEXT_CHARS)):
                value = _short_string(target.get(key), limit)
                if value:
                    clean_target[key] = value

        if not isinstance(preview, dict):
            return [], "Invalid annotation preview."
        data_url = preview.get("data_url")
        width = preview.get("width")
        height = preview.get("height")
        if (
            not isinstance(data_url, str)
            or not data_url.startswith("data:image/")
            or ";base64," not in data_url
            or len(data_url) > MAX_IMAGE_CHARS
            or isinstance(width, bool)
            or isinstance(height, bool)
            or not isinstance(width, int)
            or not isinstance(height, int)
            or width < 1
            or height < 1
            or width > 20_000
            or height > 20_000
        ):
            return [], "Invalid or oversized annotation preview."

        clean.append(
            {
                "id": annotation_id,
                "comment": comment.strip(),
                "artifact": {
                    "path": path,
                    "name": name,
                    "kind": kind,
                    "sha256": sha256.lower(),
                },
                "target": clean_target,
                "preview": {"data_url": data_url, "width": width, "height": height},
            }
        )
    return clean, None


def _annotation_text(index: int, annotation: dict[str, Any]) -> str:
    artifact = annotation["artifact"]
    target = annotation["target"]
    rect = target["rect"]
    lines = [
        f"Annotation {index}",
        f"Artifact: {artifact['path']}",
        f"Artifact version (SHA-256): {artifact['sha256']}",
        f"Target kind: {target['kind']}",
    ]
    if target.get("page"):
        lines.append(f"Page: {target['page']}")
    lines.append(
        "Normalized target rectangle: "
        + json.dumps(rect, separators=(",", ":"), sort_keys=True)
    )
    if target.get("exact"):
        lines.append(f"Selected content: {target['exact']}")
    if target.get("selector"):
        lines.append(f"DOM selector: {target['selector']}")
    lines.append(f"User comment: {annotation['comment']}")
    return "\n".join(lines)


def append_annotation_context(content: Any, annotations: list[dict[str, Any]]) -> Any:
    """Append model-compatible text and preview-image parts to canonical user content."""
    if not annotations:
        return content
    if isinstance(content, list):
        parts = [dict(part) if isinstance(part, dict) else part for part in content]
    elif isinstance(content, str) and content:
        parts = [{"type": "text", "text": content}]
    else:
        parts = []
    parts.append(
        {
            "type": "text",
            "text": (
                f"<artifact_annotations count=\"{len(annotations)}\">\n"
                "The user selected these exact areas in local artifact previews. "
                "Use each comment, target description, and following cropped image as grounded "
                "feedback about the named artifact."
            ),
        }
    )
    for index, annotation in enumerate(annotations, 1):
        parts.append({"type": "text", "text": _annotation_text(index, annotation)})
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": annotation["preview"]["data_url"]},
            }
        )
    parts.append({"type": "text", "text": "</artifact_annotations>"})
    return parts
