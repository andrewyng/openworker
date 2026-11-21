"""Team registry — which sessions form a team: one lead, its workers, their board.

A team is created at the staffing gate ("Create team & start"): worker sessions are
PRE-SPAWNED as durable state on disk (spawn ≠ first turn — an unassigned worker costs
zero tokens; its first model turn fires when the first assignment lands). The registry
is the roster the wake plumbing walks each tick, and the tie that scopes staleness
digests by role membership.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TeamWorker:
    actor: str  # the lead-given NAME — board actor id, assignee handle, @mention target
    persona: str
    session_id: str
    model: str = ""
    reason: str = ""  # why the lead staffed it — surfaces in teammates' rosters
    approval_guidance: str = ""  # exact human-approved staffing text, not live steering


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
        self._load()

    def _quarantine(self) -> Optional[Path]:
        """Move an unreadable file aside so the next save cannot overwrite the only
        copy of it. Returns the path it moved to, or None if the move failed."""
        if not self.path:
            return None
        spare = self.path.with_suffix(self.path.suffix + ".corrupt")
        try:
            self.path.replace(spare)
        except OSError:
            return None
        return spare

    def _refuse_file(self, reason: str, teams: dict[str, Team]) -> dict[str, Team]:
        """Keep the teams that could be read, move the file aside, and say so out loud.

        A team is the roster the wake plumbing walks — losing one must not lose the
        others — but dropping a row is NOT neutral either, so the bytes are moved
        aside (never deleted, never overwritten by a later save) and the reason is
        logged, the way the risk-override, connection and parked stores do it.
        """
        message = f"{self.path}: {reason}; kept {len(teams)} team(s)"
        spare = self._quarantine()
        if spare is not None:
            message += f" (original kept at {spare})"
        logger.warning(message)
        return teams

    def _load(self) -> None:
        if not (self.path and self.path.is_file()):
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as e:
            # A torn file must never block startup: SessionManager builds this store.
            self._teams = self._refuse_file(f"unreadable ({type(e).__name__})", {})
            return
        if not isinstance(data, dict):
            # Valid JSON of the wrong shape (`[]`, `null`, a string): `data.get` would
            # raise AttributeError out of the constructor the same way.
            self._teams = self._refuse_file("not a JSON object", {})
            return
        raw_teams = data.get("teams")
        if raw_teams is None:
            raw_teams = []
        if not isinstance(raw_teams, list):
            self._teams = self._refuse_file("'teams' is not a JSON array", {})
            return

        # A row this build cannot construct — an unknown field (a later build writing
        # `strategy`/`reasons`/`integrations`), a missing required one, a row that is
        # not an object — used to abort the loop and take the whole roster with it.
        # Skip just that row: `for_worker_session`/`for_lead_session` return None for
        # what is missing, so a partial roster degrades instead of crashing.
        teams: dict[str, Team] = {}
        dropped: list[str] = []
        for index, raw in enumerate(raw_teams):
            if not isinstance(raw, dict):
                dropped.append(f"team {index}: not a JSON object")
                continue
            # Copy before popping: the original mutated the parsed document.
            raw = dict(raw)
            raw_workers = raw.pop("workers", [])
            if raw_workers is None:
                raw_workers = []
            if not isinstance(raw_workers, list):
                dropped.append(f"team {index}: 'workers' is not a JSON array")
                continue
            workers: list[TeamWorker] = []
            for windex, w in enumerate(raw_workers):
                if not isinstance(w, dict):
                    dropped.append(f"team {index} worker {windex}: not a JSON object")
                    continue
                try:
                    workers.append(TeamWorker(**w))
                except (TypeError, ValueError) as e:
                    dropped.append(f"team {index} worker {windex}: {type(e).__name__}")
            try:
                team = Team(**raw)
            except (TypeError, ValueError, KeyError) as e:
                dropped.append(f"team {index}: {type(e).__name__}")
                continue
            team.workers = workers
            teams[team.team_id] = team
        if dropped:
            detail = "; ".join(dropped[:3])
            if len(dropped) > 3:
                detail += f"; … and {len(dropped) - 3} more"
            self._teams = self._refuse_file(
                f"dropped {len(dropped)} record(s) ({detail})", teams
            )
            return
        self._teams = teams

    def _save(self) -> None:
        if not self.path:
            return
        # Write a sibling .tmp and rename it over the real file, so a crash mid-write
        # leaves the previous complete file instead of a truncated one — the same
        # shape mentions.py uses.
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(
                    {"teams": [asdict(t) for t in self._teams.values()]}, indent=2
                ),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            # Persistence is best-effort; memory stays authoritative for this process.
            logger.warning("could not save %s: %s", self.path, exc)
            try:
                temporary.unlink()
            except OSError:
                pass

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

    def refund_wake(self, team_id: str) -> None:
        """Give back a wake counted by `count_wake` whose delivery did not happen (the session
        was busy, stopped or the race was lost): a wake counts only once it is delivered
        (observability Phase 3, TD3-22). The hour stays the calendar UTC hour."""
        hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        with self._lock:
            team = self._teams.get(team_id)
            if team is None or team.wake_hour != hour or team.wakes_this_hour <= 0:
                return
            team.wakes_this_hour -= 1
            self._save()
