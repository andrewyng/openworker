"""Matrix mention/session routing — Hermes-aligned session keys for manager."""

from __future__ import annotations

from typing import Optional

from .base import SessionSource, format_target
from .matrix_settings import MatrixSettings

_USER_SCOPE_PREFIX = "@user:"


def _user_thread_suffix(user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    return f"{_USER_SCOPE_PREFIX}{user_id}"


def effective_session_scope(settings: MatrixSettings) -> str:
    """`auto` behaves like `thread` (Hermes default)."""
    scope = (settings.session_scope or "auto").strip().lower()
    return "thread" if scope == "auto" else scope


def mention_thread_target(
    settings: MatrixSettings,
    source: SessionSource,
    message_id: Optional[str],
) -> str:
    """Mention-session key — same string used for standing send_message grants."""
    scope = effective_session_scope(settings)
    user_suffix = (
        _user_thread_suffix(source.user_id) if settings.group_sessions_per_user else None
    )
    if scope == "room":
        thread_id = user_suffix
    else:
        thread_id = source.thread_id or message_id
        if user_suffix and thread_id:
            thread_id = f"{thread_id}|{user_suffix}"
        elif user_suffix:
            thread_id = user_suffix
    return format_target("matrix", source.chat_id, thread_id)


def dm_mention_routes_to_thread(settings: MatrixSettings, *, mentions_me: bool, is_dm: bool) -> bool:
    return bool(is_dm and mentions_me and settings.dm_mention_threads)
