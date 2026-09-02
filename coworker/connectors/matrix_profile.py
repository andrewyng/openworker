"""PATCH helpers for Matrix connector advanced settings."""

from __future__ import annotations

from typing import Any

from ..secrets import SecretStore
from .matrix_settings import MatrixSettings

_MATRIX_SETTING_KEYS = frozenset(
    {
        "require_mention",
        "auto_thread",
        "session_scope",
        "dm_mention_threads",
        "dm_auto_thread",
        "group_sessions_per_user",
        "lifecycle_reactions",
        "allowed_rooms",
        "free_response_rooms",
    }
)


def matrix_settings_public(profile: dict) -> dict[str, Any]:
    s = MatrixSettings.from_profile(profile)
    return {
        "homeserver_url": s.homeserver_url,
        "user_id": s.user_id,
        "require_mention": s.require_mention,
        "auto_thread": s.auto_thread,
        "session_scope": s.session_scope,
        "dm_mention_threads": s.dm_mention_threads,
        "dm_auto_thread": s.dm_auto_thread,
        "group_sessions_per_user": s.group_sessions_per_user,
        "lifecycle_reactions": s.lifecycle_reactions,
        "allowed_rooms": sorted(s.allowed_rooms),
        "free_response_rooms": sorted(s.free_response_rooms),
    }


def patch_matrix_settings(secrets: SecretStore, body: dict[str, Any]) -> dict[str, Any]:
    profile = secrets.get("matrix:default")
    if not profile:
        return {"ok": False, "error": "matrix not connected"}
    if not isinstance(body, dict):
        return {"ok": False, "error": "body must be an object"}
    updated = dict(profile)
    for key, value in body.items():
        if key not in _MATRIX_SETTING_KEYS:
            continue
        if key in ("allowed_rooms", "free_response_rooms"):
            if isinstance(value, str):
                value = [p.strip() for p in value.split(",") if p.strip()]
            if not isinstance(value, list):
                return {"ok": False, "error": f"{key} must be a list or CSV string"}
            updated[key] = value
        elif key == "session_scope":
            scope = str(value or "auto").strip().lower()
            if scope not in ("auto", "room", "thread"):
                return {"ok": False, "error": "session_scope must be auto, room, or thread"}
            updated[key] = scope
        elif key in (
            "require_mention",
            "auto_thread",
            "dm_mention_threads",
            "dm_auto_thread",
            "group_sessions_per_user",
            "lifecycle_reactions",
        ):
            updated[key] = bool(value)
        else:
            updated[key] = value
    secrets.put("matrix:default", updated)
    return {"ok": True, "settings": matrix_settings_public(updated)}
