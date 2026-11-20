"""Persistent personal bots, backed by the normal session and approval runtime.

A bot owns one conversation and workspace. Delegations are durable FIFO jobs;
check-ins join that same queue rather than starting unrelated automation sessions.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter

from .automation.models import Schedule, ScheduledTask
from .automation.store import compute_next_run
from .sessions import SessionRecord


def _schedule(cron: str, timezone: str, after: float) -> float | None:
    if not cron:
        return None
    return compute_next_run(
        ScheduledTask("check-in", "check-in", Schedule("cron", cron=cron, timezone=timezone), ""),
        after=after,
    )


class BotStore:
    def __init__(self, path: Path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS personal_bots (
                    id TEXT PRIMARY KEY, session_id TEXT UNIQUE NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bot_jobs (
                    id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, prompt TEXT NOT NULL,
                    trigger TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                    created_at REAL NOT NULL, finished_at REAL, error TEXT
                );
                CREATE INDEX IF NOT EXISTS bot_jobs_queue ON bot_jobs(bot_id, state, created_at);
            """)
            # Never silently replay a possibly completed external action after a crash.
            self._db.execute(
                "UPDATE bot_jobs SET state='interrupted', finished_at=?, error=? WHERE state='running'",
                (time.time(), "Host stopped during this task. Review the conversation before delegating it again."),
            )

    def close(self):
        self._db.close()

    def list(self) -> list[dict]:
        with self._lock:
            return [json.loads(r[0]) for r in self._db.execute("SELECT data FROM personal_bots ORDER BY rowid")]

    def get(self, bot_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT data FROM personal_bots WHERE id=?", (bot_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def for_session(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT data FROM personal_bots WHERE session_id=?", (session_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, bot: dict):
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO personal_bots VALUES (?, ?, ?)",
                             (bot["id"], bot["session_id"], json.dumps(bot)))

    def enqueue(self, bot_id: str, prompt: str, trigger: str = "delegation") -> str:
        job_id = "job-" + uuid.uuid4().hex
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO bot_jobs(id, bot_id, prompt, trigger, created_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, bot_id, prompt, trigger, time.time()),
            )
        return job_id

    def jobs(self, bot_id: str) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM bot_jobs WHERE bot_id=? ORDER BY created_at DESC, rowid DESC LIMIT 50", (bot_id,))]

    def pending_checkin(self, bot_id: str) -> bool:
        with self._lock:
            return self._db.execute(
                "SELECT 1 FROM bot_jobs WHERE bot_id=? AND trigger='check-in' "
                "AND state IN ('queued', 'running') LIMIT 1", (bot_id,),
            ).fetchone() is not None

    def claim(self, bot_id: str) -> dict | None:
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT * FROM bot_jobs WHERE bot_id=? AND state='queued' ORDER BY created_at, rowid LIMIT 1",
                (bot_id,),
            ).fetchone()
            if not row:
                return None
            self._db.execute("UPDATE bot_jobs SET state='running' WHERE id=?", (row["id"],))
            return dict(row)

    def finish(self, job_id: str, state: str, error: str | None = None):
        with self._lock, self._db:
            self._db.execute("UPDATE bot_jobs SET state=?, finished_at=?, error=? WHERE id=?",
                             (state, None if state == "queued" else time.time(), error, job_id))


class BotService:
    def __init__(self, manager: Any, base: Path):
        self.manager = manager
        self.base = base
        self.store = BotStore(base / "bots.db")
        self._inflight: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self.dedicated = os.environ.get("COWORKER_DEDICATED_BOT_RUNTIME") == "1"
        if self.dedicated and len(self.store.list()) > 1:
            self.store.close()
            raise ValueError("A dedicated runtime cannot reuse a multi-bot state directory")
        self._create_lock = threading.Lock()

    def runtime(self) -> dict:
        return {"dedicated": self.dedicated, "capacity": 1 if self.dedicated else None,
                "bot_count": len(self.store.list())}

    def permits_session(self, session_id: str) -> bool:
        return not self.dedicated or self.store.for_session(session_id) is not None

    def create(self, payload: dict) -> dict:
        # Serialize admission before any session/folder is provisioned. A dedicated
        # runtime is a whole OS/container, not another directory on a shared engine.
        with self._create_lock:
            if self.dedicated and self.store.list():
                raise ValueError("This dedicated runtime already belongs to a bot")
            if payload.get("dedicated") is True and not self.dedicated:
                raise ValueError("Choose a dedicated bot runtime, not a shared machine")
            return self._create(payload)

    def _create(self, payload: dict) -> dict:
        fields = {k: payload.get(k, "") for k in ("name", "instructions", "cron", "timezone")}
        if any(not isinstance(v, str) for v in fields.values()):
            raise ValueError("Bot fields must be text")
        fields = {k: v.strip() for k, v in fields.items()}
        if not fields["name"] or len(fields["name"]) > 100:
            raise ValueError("Name must contain 1–100 characters")
        if not fields["instructions"] or len(fields["instructions"]) > 20000:
            raise ValueError("Instructions must contain 1–20000 characters")
        cron = fields["cron"]
        if cron and (len(cron.split()) != 5 or not croniter.is_valid(cron)):
            raise ValueError("Use a valid five-field cron schedule")
        timezone = fields["timezone"] or "UTC"
        try:
            ZoneInfo(timezone)
        except (ValueError, KeyError):
            raise ValueError("Use an IANA timezone, such as Asia/Taipei") from None
        now = time.time()
        bot_id = "bot-" + uuid.uuid4().hex
        workspace = self.base / "bots" / bot_id
        workspace.mkdir(parents=True)
        bot = dict(fields, id=bot_id, session_id=bot_id, workspace=str(workspace.resolve()),
                   agent="cowork", timezone=timezone, enabled=True, created_at=now,
                   execution="dedicated" if self.dedicated else "shared",
                   next_check=_schedule(cron, timezone, now))
        self.manager.session_store.save(SessionRecord(
            session_id=bot_id, workspace=bot["workspace"], model=self.manager.resolve_persona_model("cowork"),
            mode="interactive", agent="cowork", title=fields["name"], origin="bot",
            origin_label=fields["name"],
        ))
        self.manager.unattended.set(bot_id, "inbox")
        self.store.save(bot)
        return bot

    def set_enabled(self, bot_id: str, enabled: bool) -> dict:
        bot = self.store.get(bot_id)
        if not bot:
            raise KeyError(bot_id)
        bot["enabled"] = enabled
        bot["next_check"] = _schedule(bot["cron"], bot["timezone"], time.time()) if enabled else None
        self.store.save(bot)
        if not enabled:
            self.manager.stop_session(bot["session_id"])
        else:
            self.manager.wakes.set_stopped(bot["session_id"], False)
        return bot

    def instructions(self, session_id: str) -> str:
        bot = self.store.for_session(session_id)
        if not bot:
            return ""
        return (
            f"You are {bot['name']}, a persistent personal assistant.\n{bot['instructions']}\n\n"
            "Continue work in this conversation and workspace. Keep durable project facts in "
            "workspace memory and task progress in a workspace file when useful. Check-ins are "
            "opportunities to advance existing goals, not authorization for unrelated work. "
            "Report concrete results, blockers and the next step. Never claim a task succeeded "
            "without evidence. Existing tool permissions and human approval requirements apply."
        )

    async def tick(self):
        now = time.time()
        for bot in self.store.list():
            if not bot["enabled"]:
                continue
            sid = bot["session_id"]
            if bot["next_check"] is not None and bot["next_check"] <= now:
                # One catch-up, never a burst of every missed occurrence.
                if not self.store.pending_checkin(bot["id"]):
                    self.store.enqueue(bot["id"], "Review your existing goals and task progress. "
                                       "Advance useful authorized work, report meaningful changes or blockers; "
                                       "if nothing needs attention, keep the check-in brief.", "check-in")
                bot["next_check"] = _schedule(bot["cron"], bot["timezone"], now)
                self.store.save(bot)
            if sid in self._inflight or self.manager.is_running(sid) or sid in self.manager._team_inflight:
                continue
            job = self.store.claim(bot["id"])
            if job:
                self._inflight.add(sid)
                task = asyncio.create_task(self._run(bot, job))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _run(self, bot: dict, job: dict):
        errors: list[str] = []
        try:
            # Check pause again: an API call may have won after this job was claimed.
            current = self.store.get(bot["id"])
            if not current or not current["enabled"]:
                self.store.finish(job["id"], "interrupted", "Bot was paused")
                return
            delivered = await self.manager.deliver_to_session(
                bot["session_id"], job["prompt"], on_error=errors.append, idle_only=True,
            )
            current = self.store.get(bot["id"])
            if (not current or not current["enabled"]
                    or bot["session_id"] in self.manager.wakes.stopped_sessions):
                self.store.finish(job["id"], "interrupted", "Bot was stopped; review the conversation")
                return
            if not delivered and self.manager.is_running(bot["session_id"]):
                self.store.finish(job["id"], "queued")
                return
            self.store.finish(job["id"], "error" if errors or not delivered else "done",
                              "; ".join(errors) or (None if delivered else "Session unavailable or paused"))
        except asyncio.CancelledError:
            self.store.finish(job["id"], "interrupted", "Host stopped; review before retrying")
            raise
        except Exception as exc:
            self.store.finish(job["id"], "error", str(exc))
        finally:
            self._inflight.discard(bot["session_id"])

    async def stop(self):
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.store.close()
