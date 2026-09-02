"""Team registry — which sessions form a team: one lead, its workers, their board.

A team is created at the staffing gate ("Create team & start"): worker sessions are
PRE-SPAWNED as durable state on disk (spawn ≠ first turn — an unassigned worker costs
zero tokens; its first model turn fires when the first assignment lands). The registry
is the roster the wake plumbing walks each tick, and the tie that scopes staleness
digests by role membership.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class TeamWorker:
    actor: str  # the lead-given NAME — board actor id, assignee handle, @mention target
    persona: str
    session_id: str
    model: str = ""
    reason: str = ""  # why the lead staffed it — surfaces in teammates' rosters


@dataclass
class Team:
    team_id: str
    space: str
    lead_session: str
    lead_actor: str
    workers: list[TeamWorker] = field(default_factory=list)
    chat_enabled: bool = False
    chat_group: str = ""  # ChatStore group_id when chat is enabled
    paused: bool = False  # budget/user pause: the wake gate skips a paused team
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    # Rolling budget gate: automatic wakes this hour (reset when the hour rolls).
    wake_hour: str = ""
    wakes_this_hour: int = 0


class TeamRegistry:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._teams: dict[str, Team] = {}
        if self.path and self.path.is_file():
            for raw in json.loads(self.path.read_text(encoding="utf-8")).get(
                "teams", []
            ):
                workers = [TeamWorker(**w) for w in raw.pop("workers", [])]
                team = Team(**{**raw, "workers": []})
                team.workers = workers
                self._teams[team.team_id] = team

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"teams": [asdict(t) for t in self._teams.values()]}, indent=2
            ),
            encoding="utf-8",
        )

    def create(
        self,
        *,
        space: str,
        lead_session: str,
        lead_actor: str,
        workers: list[TeamWorker],
        chat_enabled: bool = False,
        chat_group: str = "",
    ) -> Team:
        team = Team(
            team_id=uuid.uuid4().hex[:12],
            space=space,
            lead_session=lead_session,
            lead_actor=lead_actor,
            workers=workers,
            chat_enabled=chat_enabled,
            chat_group=chat_group,
        )
        with self._lock:
            self._teams[team.team_id] = team
            self._save()
        return team

    def all(self) -> list[Team]:
        return list(self._teams.values())

    def get(self, team_id: str) -> Optional[Team]:
        return self._teams.get(team_id)

    def for_lead_session(self, session_id: str) -> Optional[Team]:
        for team in self._teams.values():
            if team.lead_session == session_id:
                return team
        return None

    def for_worker_session(self, session_id: str) -> Optional[tuple[Team, TeamWorker]]:
        for team in self._teams.values():
            for worker in team.workers:
                if worker.session_id == session_id:
                    return team, worker
        return None

    def set_paused(self, team_id: str, paused: bool) -> None:
        with self._lock:
            team = self._teams.get(team_id)
            if team is not None:
                team.paused = paused
                self._save()

    def count_wake(self, team_id: str, *, cap: int) -> bool:
        """The budget gate at the wake gate: count one automatic wake against the
        team's rolling hour; False = over cap (the caller skips the wake and the
        team reads as paused-for-budget until the hour rolls). A runaway loop
        stops BETWEEN turns, never mid-flight."""
        hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        with self._lock:
            team = self._teams.get(team_id)
            if team is None:
                return False
            if team.wake_hour != hour:
                team.wake_hour, team.wakes_this_hour = hour, 0
            if team.wakes_this_hour >= cap:
                self._save()
                return False
            team.wakes_this_hour += 1
            self._save()
            return True
