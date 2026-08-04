"""Connector inbound agent routing.

This store is intentionally narrower than persona/session connection settings:
it only answers "which persona should create NEW inbound conversations for this
connector?" Existing sessions keep their stored agent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import threading
import time
from pathlib import Path
from typing import Optional


@dataclass
class ConnectorAgentRoute:
    connector: str
    agent: str
    workspace: str = ""
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class ConnectorAgentRouteStore:
    """``{connector: {agent, workspace}}`` for new inbound connector sessions."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._routes: dict[str, ConnectorAgentRoute] = {}
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            self._routes = {}
            return
        routes: dict[str, ConnectorAgentRoute] = {}
        raw_routes = data.get("routes", {}) if isinstance(data, dict) else {}
        for connector, raw in raw_routes.items():
            if not isinstance(raw, dict):
                continue
            name = str(connector).strip()
            agent = str(raw.get("agent") or "").strip()
            if not name or not agent:
                continue
            try:
                updated_at = float(raw.get("updated_at") or 0.0)
            except (TypeError, ValueError):
                updated_at = 0.0
            routes[name] = ConnectorAgentRoute(
                connector=name,
                agent=agent,
                workspace=str(raw.get("workspace") or ""),
                updated_at=updated_at,
            )
        self._routes = routes

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "routes": {
                        connector: {
                            "agent": route.agent,
                            "workspace": route.workspace,
                            "updated_at": route.updated_at,
                        }
                        for connector, route in sorted(self._routes.items())
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def get(self, connector: str) -> Optional[ConnectorAgentRoute]:
        with self._lock:
            route = self._routes.get(connector)
            if route is None:
                return None
            return ConnectorAgentRoute(**route.to_dict())

    def all(self) -> list[ConnectorAgentRoute]:
        with self._lock:
            return [ConnectorAgentRoute(**r.to_dict()) for r in self._routes.values()]

    def set(
        self, connector: str, agent: str, workspace: str = ""
    ) -> ConnectorAgentRoute:
        connector = connector.strip()
        agent = agent.strip()
        if not connector:
            raise ValueError("connector required")
        if not agent:
            raise ValueError("agent required")
        route = ConnectorAgentRoute(
            connector=connector,
            agent=agent,
            workspace=workspace,
            updated_at=time.time(),
        )
        with self._lock:
            self._routes[connector] = route
            self._save()
            return ConnectorAgentRoute(**route.to_dict())

    def delete(self, connector: str) -> bool:
        with self._lock:
            existed = connector in self._routes
            if existed:
                del self._routes[connector]
                self._save()
            return existed
