"""The scheduler loop — runs in the always-on server.

Policy (agreed): **run-once-catch-up** for runs missed while down (due tasks fire once on
startup, then resume), and **skip-on-overlap** (don't stack a run if the previous is still
going). The actual execution is injected as `runner(task, trigger) -> TaskRun` so this stays
independent of the engine/manager.

Catch-up waits, and goes one at a time. Both halves were learned the hard way (2026-08-30):
the pass used to be the first thing the loop did, spawning every missed task at once. On a
box that boots into its own model server, that meant seven runs starting 1.0s after
`Application startup complete` and all seven dead 3.1s later with no output -- the local
model server had not even begun loading (measured: vLLM started 44s AFTER they gave up, with
minutes of weights to read). `next_run` advanced regardless, so a morning of automations was
consumed rather than recovered, which is the exact opposite of what catch-up is for. It also
threw away the cron staggering that exists so two runs never share one local GPU.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from .models import ScheduledTask, TaskRun
from .store import TaskStore

logger = logging.getLogger("coworker.automation")

Runner = Callable[[ScheduledTask, str], Awaitable[TaskRun]]


class Scheduler:
    def __init__(
        self,
        store: TaskStore,
        runner: Runner,
        *,
        tick_seconds: float = 30.0,
        extra_tick: Optional[Callable[[], Awaitable[None]]] = None,
        catchup_delay_seconds: float = 15.0,
        ready_probe: Optional[Callable[[], Awaitable[bool]]] = None,
        ready_poll_seconds: float = 10.0,
        ready_timeout_seconds: float = 1800.0,
    ) -> None:
        self.store = store
        self.runner = runner
        self.tick_seconds = tick_seconds
        # An extra per-tick coroutine (self-wake resumption: resume sessions whose wakes are due).
        self.extra_tick = extra_tick
        # Settle margin before catch-up even asks whether the backend is up. The probe below
        # does the real waiting; this only keeps us from probing into a half-built process.
        self.catchup_delay_seconds = catchup_delay_seconds
        # Optional "is the model backend answering yet?" check, injected so this module stays
        # independent of the engine/manager. None = assume ready (the pre-2026-08-30 behaviour).
        self.ready_probe = ready_probe
        self.ready_poll_seconds = ready_poll_seconds
        # Cap on holding missed runs back. Generous on purpose: a local server that needs ten
        # minutes to load weights is normal, and releasing early recreates the storm.
        self.ready_timeout_seconds = ready_timeout_seconds
        self._task: Optional[asyncio.Task] = None
        self._running_ids: set[str] = set()  # overlap guard
        self._spawned: set[asyncio.Task] = set()  # keep spawned runs referenced
        # Missed tasks claimed by the catch-up drain but not yet run. The schedule tick skips
        # these: they are still `due()` until the drain reaches them, and without the claim a
        # 30s tick would fire the queue concurrently and undo the serialisation.
        self._catchup_pending: set[str] = set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # In-flight runs died with the loop before they were spawned; keep that shutdown
        # contract now that they're independent tasks (a suspended run must not outlive us).
        for spawned in list(self._spawned):
            spawned.cancel()
            try:
                await spawned
            except asyncio.CancelledError:
                pass
        self._spawned.clear()

    async def _loop(self) -> None:
        # Catch-up drains on its own task: it waits for the model backend and then runs the
        # missed tasks one at a time, which can take an hour. The schedule tick must not wait
        # behind it, and a run parked on an approval must not stop the clock.
        drain = asyncio.create_task(self._catchup())
        self._spawned.add(drain)
        drain.add_done_callback(self._spawned.discard)
        while True:
            await asyncio.sleep(self.tick_seconds)
            try:
                await self._tick(trigger="schedule")
            except Exception:
                logger.exception("scheduler tick failed")

    async def _catchup(self) -> None:
        """Run-once-catch-up for what was missed while the server was down."""
        try:
            await asyncio.sleep(self.catchup_delay_seconds)
            due = list(self.store.due())
            if not due:
                return
            # Claim the whole batch before the first await that can yield to a schedule tick.
            self._catchup_pending = {t.id for t in due}
            logger.info(
                "catch-up: %d task(s) missed while down — running one at a time", len(due)
            )
            if not await self._await_backend():
                # Held long enough. Drop the claim rather than firing into a backend that is
                # not answering: the tasks stay due, so the schedule tick owns them from here.
                logger.warning(
                    "catch-up: backend still not answering after %.0fs — releasing %d "
                    "missed task(s) to the normal tick",
                    self.ready_timeout_seconds,
                    len(self._catchup_pending),
                )
                return
            for task in due:
                # Released one at a time: each is in flight (and so in _running_ids) before
                # the next is unclaimed, so the tick can never overtake the drain.
                self._catchup_pending.discard(task.id)
                await self.run_task(task, trigger="catchup")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler catch-up failed")
        finally:
            self._catchup_pending.clear()

    async def _await_backend(self) -> bool:
        """Block until the model backend answers. True when it does, False on timeout."""
        if self.ready_probe is None:
            return True
        deadline = time.monotonic() + self.ready_timeout_seconds
        waited = False
        while True:
            try:
                if await self.ready_probe():
                    if waited:
                        logger.info("catch-up: backend is up — starting the missed runs")
                    return True
            except Exception:
                logger.exception("catch-up: backend probe failed — treating as not ready")
            if time.monotonic() >= deadline:
                return False
            waited = True
            await asyncio.sleep(self.ready_poll_seconds)

    async def _tick(self, *, trigger: str) -> None:
        for task in self.store.due():
            if task.id in self._catchup_pending:
                continue  # the catch-up drain owns it; it runs there, in turn
            # Spawn, don't await: a run can suspend on a parked approval (standing
            # scoped approvals, §25) and one blocked automation must never stall the
            # scheduler loop, other due tasks, or self-wake resumption. The overlap
            # guard must be claimed *here*, before the spawn: this due() snapshot
            # goes stale, and if the in-flight run finishes before a spawned
            # duplicate gets its first step, a guard checked inside the spawn is
            # already clear — the task runs twice.
            if not self._claim(task.id):
                continue
            spawned = asyncio.create_task(self._run_claimed(task, trigger=trigger))
            self._spawned.add(spawned)
            spawned.add_done_callback(self._spawned.discard)
        if self.extra_tick is not None:
            try:
                await self.extra_tick()
            except Exception:
                logger.exception("scheduler extra_tick (wake resume) failed")

    def _claim(self, task_id: str) -> bool:
        if task_id in self._running_ids:  # skip-on-overlap
            logger.info("skipping %s — previous run still going", task_id)
            return False
        self._running_ids.add(task_id)
        return True

    async def run_task(self, task: ScheduledTask, *, trigger: str) -> Optional[TaskRun]:
        if not self._claim(task.id):
            return None
        return await self._run_claimed(task, trigger=trigger)

    async def _run_claimed(
        self, task: ScheduledTask, *, trigger: str
    ) -> Optional[TaskRun]:
        try:
            run = await self.runner(task, trigger)
        except Exception as exc:
            logger.exception("task %s run failed", task.id)
            run = TaskRun(
                task_id=task.id, status="error", error=str(exc), trigger=trigger
            )
            self.store.add_run(run)
        finally:
            self._running_ids.discard(task.id)
        # advance the task (run_count/last_run) → save recomputes next_run.
        fresh = self.store.get(task.id)
        if fresh is not None:
            fresh.run_count += 1
            fresh.last_run = run.started_at if run else None
            fresh.last_status = run.status if run else "error"
            self.store.save(fresh)
        return run
