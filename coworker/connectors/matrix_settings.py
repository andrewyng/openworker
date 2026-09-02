"""Matrix connector settings from the `matrix:default` profile."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from .config import _csv, _profile_list, _profile_set


@dataclass
class MatrixSettings:
    homeserver_url: str
    access_token: str
    user_id: Optional[str] = None
    recovery_key: Optional[str] = None
    allowed_users: set[str] = field(default_factory=set)
    allowed_rooms: set[str] = field(default_factory=set)
    free_response_rooms: set[str] = field(default_factory=set)
    ignore_user_patterns: list[re.Pattern[str]] = field(default_factory=list)
    require_mention: bool = True
    auto_thread: bool = True
    session_scope: str = "auto"  # auto | room | thread
    dm_mention_threads: bool = False
    dm_auto_thread: bool = True
    group_sessions_per_user: bool = True
    lifecycle_reactions: bool = True
    e2ee_mode: str = "required"
    max_message_length: int = 4000
    max_media_bytes: int = 104_857_600
    approval_require_sender: bool = True

    @classmethod
    def from_profile(cls, profile: dict) -> "MatrixSettings":
        patterns = []
        raw_patterns = _profile_list(profile.get("ignore_user_patterns"))
        if not raw_patterns:
            raw_patterns = ["^@telegram_", "^@slack_", "^@whatsapp_"]
        for raw in raw_patterns:
            try:
                patterns.append(re.compile(raw))
            except re.error:
                continue
        allowed_rooms = _profile_set(profile.get("allowed_rooms")) | _csv(
            os.environ.get("MATRIX_ALLOWED_ROOMS")
        )
        free_response_rooms = _profile_set(profile.get("free_response_rooms")) | _csv(
            os.environ.get("MATRIX_FREE_RESPONSE_ROOMS")
        )
        return cls(
            homeserver_url=str(profile.get("homeserver_url") or "").rstrip("/"),
            access_token=str(profile.get("access_token") or ""),
            user_id=profile.get("user_id"),
            recovery_key=profile.get("recovery_key"),
            allowed_users=_profile_set(profile.get("allowed_users")),
            allowed_rooms=allowed_rooms,
            free_response_rooms=free_response_rooms,
            ignore_user_patterns=patterns,
            require_mention=bool(profile.get("require_mention", True)),
            auto_thread=bool(profile.get("auto_thread", True)),
            session_scope=str(profile.get("session_scope") or "auto"),
            dm_mention_threads=bool(profile.get("dm_mention_threads", False)),
            dm_auto_thread=bool(profile.get("dm_auto_thread", True)),
            group_sessions_per_user=bool(profile.get("group_sessions_per_user", True)),
            lifecycle_reactions=bool(profile.get("lifecycle_reactions", True)),
            e2ee_mode=str(profile.get("e2ee_mode") or "required"),
            max_message_length=int(profile.get("max_message_length") or 4000),
            max_media_bytes=int(profile.get("max_media_bytes") or 104_857_600),
            approval_require_sender=bool(profile.get("approval_require_sender", True)),
        )

    def ignored_user(self, user_id: Optional[str]) -> bool:
        if not user_id:
            return False
        return any(p.search(user_id) for p in self.ignore_user_patterns)
