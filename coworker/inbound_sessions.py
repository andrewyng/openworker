"""Inbound session routing registry.

Maps an external conversation source to a durable OpenWorker session id so
repeat DM traffic reuses a dedicated session instead of falling back to a
global DM sink.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from .connectors.base import SessionSource, format_target


@dataclass
class InboundSessionLink:
    route_key: str
    session_id: str
    platform: str
    chat_type: str
    chat_id: str
    user_id: str = ""
    user_name: str = ""
    chat_name: str = ""
    thread_id: str = ""
    team_id: str = ""
    origin: str = ""
    origin_label: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


def inbound_route_key(source: SessionSource) -> str:
    parts = [str(source.platform or "").strip() or "unknown"]
    chat_type = str(source.chat_type or "dm").strip().lower() or "dm"
    parts.append(chat_type)
    team_id = str(getattr(source, "team_id", "") or "").strip()
    if team_id:
        parts.extend(["team", team_id])
    chat_id = str(source.chat_id or "").strip()
    if chat_id:
        parts.append(chat_id)
    else:
        user_id = str(source.user_id or "").strip()
        if user_id:
            parts.extend(["user", user_id])
    thread_id = str(source.thread_id or "").strip()
    if thread_id:
        parts.append(thread_id)
    return ":".join(parts)


class InboundSessionRegistry:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._links: list[InboundSessionLink] = []
        self._load()

    def _load(self) -> None:
        if self.path and self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._links = [InboundSessionLink(**raw) for raw in data.get("links", [])]

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"links": [asdict(link) for link in self._links]}, indent=2),
            encoding="utf-8",
        )

    def get(self, route_key: str) -> Optional[InboundSessionLink]:
        for link in self._links:
            if link.route_key == route_key:
                return link
        return None

    def resolve(self, route_key: str) -> Optional[str]:
        link = self.get(route_key)
        return link.session_id if link else None

    def upsert(self, link: InboundSessionLink) -> InboundSessionLink:
        now = time.time()
        with self._lock:
            for idx, current in enumerate(self._links):
                if current.route_key == link.route_key:
                    link.created_at = current.created_at or link.created_at or now
                    link.updated_at = now
                    self._links[idx] = link
                    self._save()
                    return link
            if not link.created_at:
                link.created_at = now
            if not link.updated_at:
                link.updated_at = link.created_at
            self._links.append(link)
            self._save()
            return link

    def touch(self, route_key: str) -> None:
        now = time.time()
        with self._lock:
            for link in self._links:
                if link.route_key == route_key:
                    link.updated_at = now
                    self._save()
                    return

    def remove_session(self, session_id: str) -> int:
        with self._lock:
            before = len(self._links)
            self._links = [link for link in self._links if link.session_id != session_id]
            changed = len(self._links) != before
            if changed:
                self._save()
            return before - len(self._links)

    def remove_route(self, route_key: str) -> bool:
        with self._lock:
            before = len(self._links)
            self._links = [link for link in self._links if link.route_key != route_key]
            changed = len(self._links) != before
            if changed:
                self._save()
            return changed

    def all(self) -> list[dict[str, Any]]:
        return [asdict(link) for link in self._links]

    def targets_for(self, session_id: str) -> list[str]:
        targets: list[str] = []
        for link in self._links:
            if link.session_id != session_id or not link.platform or not link.chat_id:
                continue
            targets.append(format_target(link.platform, link.chat_id, link.thread_id or None))
        return targets
