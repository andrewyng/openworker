"""The scheduler loop — runs in the always-on server.

Policy (agreed): **run-once-catch-up** for runs missed while down (due tasks fire once on
startup, then resume), and **skip-on-overlap** (don't stack a run if the previous is still
going). The actual execution is injected as `runner(task, trigger) -> TaskRun` so this stays
independent of the engine/manager.

Features (Issue #621):
- Run timeout (per-task or default e.g. 15 min), releasing overlap guard on expiry
- Error retry with exponential backoff for runs ending in error
- Force stop action to cancel stuck in-flight runs from UI
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
        default_timeout: float = 900.0,
        on_timeout: Optional[Callable[[ScheduledTask, TaskRun], Awaitable[None]]] = None,
    ) -> None:
        self.store = store
        self.runner = runner
        self.tick_seconds = tick_seconds
        # An extra per-tick coroutine (self-wake resumption: resume sessions whose wakes are due).
        self.extra_tick = extra_tick
        self.default_timeout = default_timeout
        self.on_timeout = on_timeout
        self._task: Optional[asyncio.Task] = None
        self._running_ids: set[str] = set()  # overlap guard
        self._spawned: set[asyncio.Task] = set()  # keep spawned runs referenced
        self._active_runs: dict[str, asyncio.Task] = {}  # task_id -> running asyncio.Task

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
        self._active_runs.clear()

    async def _loop(self) -> None:
        # First pass = run-once-catch-up for anything missed while the server was down.
        try:
            await self._tick(trigger="catchup")
        except Exception:
            logger.exception("scheduler catch-up failed")
        while True:
            await asyncio.sleep(self.tick_seconds)
            try:
                await self._tick(trigger="schedule")
            except Exception:
                logger.exception("scheduler tick failed")

    async def _tick(self, *, trigger: str) -> None:
        for task in self.store.due():
            # Spawn, don't await: a run can suspend on a parked approval (standing
            # scoped approvals, §25) and one blocked automation must never stall the
            # scheduler loop, other due tasks, or self-wake resumption. The overlap
            # guard must be claimed *here*, before the spawn: this due() snapshot
            # goes stale, and if the in-flight run finishes before a spawned
            # duplicate gets its first step, a guard checked inside the spawn is
            # already clear — the task runs twice.
            if not self._claim(task.id):
                continue
            run_trigger = "retry" if task.retry_count > 0 else trigger
            spawned = asyncio.create_task(self._run_claimed(task, trigger=run_trigger))
            self._spawned.add(spawned)
            self._active_runs[task.id] = spawned
            spawned.add_done_callback(self._spawned.discard)
            spawned.add_done_callback(
                lambda _, tid=task.id: self._active_runs.pop(tid, None)
            )
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

    def force_stop(self, task_id: str) -> bool:
        """Cancel an in-flight run for task_id, immediately releasing the overlap guard."""
        self._running_ids.discard(task_id)
        active = self._active_runs.pop(task_id, None)
        if active is not None and not active.done():
            active.cancel()
            return True
        return False

    async def run_task(self, task: ScheduledTask, *, trigger: str) -> Optional[TaskRun]:
        if not self._claim(task.id):
            return None
        current = asyncio.current_task()
        if current is not None:
            self._active_runs[task.id] = current
        try:
            return await self._run_claimed(task, trigger=trigger)
        finally:
            self._active_runs.pop(task.id, None)

    async def _run_claimed(
        self, task: ScheduledTask, *, trigger: str
    ) -> Optional[TaskRun]:
        timeout = (
            task.timeout_seconds
            if task.timeout_seconds is not None
            else self.default_timeout
        )
        run = None
        try:
            if timeout and timeout > 0:
                run = await asyncio.wait_for(
                    self.runner(task, trigger), timeout=timeout
                )
            else:
                run = await self.runner(task, trigger)
        except asyncio.TimeoutError:
            logger.warning("task %s run timed out after %ss", task.id, timeout)
            run = TaskRun(
                task_id=task.id,
                status="timed_out",
                error=f"Task run timed out after {timeout}s",
                trigger=trigger,
                finished_at=time.time(),
            )
            self.store.add_run(run)
            if self.on_timeout is not None:
                try:
                    await self.on_timeout(task, run)
                except Exception:
                    logger.exception(
                        "scheduler on_timeout callback failed for %s", task.id
                    )
        except asyncio.CancelledError:
            logger.info("task %s was cancelled / force stopped", task.id)
            run = TaskRun(
                task_id=task.id,
                status="cancelled",
                error="Force stopped by user",
                trigger=trigger,
                finished_at=time.time(),
            )
            self.store.add_run(run)
        except Exception as exc:
            logger.exception("task %s run failed", task.id)
            run = TaskRun(
                task_id=task.id,
                status="error",
                error=str(exc),
                trigger=trigger,
                finished_at=time.time(),
            )
            self.store.add_run(run)
        finally:
            self._running_ids.discard(task.id)
            self._active_runs.pop(task.id, None)

        # advance the task (run_count/last_run/status/retry).
        fresh = self.store.get(task.id)
        if fresh is not None:
            fresh.run_count += 1
            fresh.last_run = run.started_at if run else None
            fresh.last_status = run.status if run else "error"

            # Retry on error with exponential backoff (not cancelled or timed-out)
            if (
                run
                and run.status == "error"
                and fresh.max_retries > 0
                and fresh.retry_count < fresh.max_retries
            ):
                backoff = fresh.retry_backoff_seconds * (2**fresh.retry_count)
                fresh.retry_count += 1
                fresh.next_run = time.time() + backoff
                logger.info(
                    "task %s failed (%s); scheduled retry %d/%d in %.1fs",
                    fresh.id,
                    run.error,
                    fresh.retry_count,
                    fresh.max_retries,
                    backoff,
                )
                self.store.save(fresh, recompute_next_run=False)
            else:
                fresh.retry_count = 0
                self.store.save(fresh, recompute_next_run=True)
        return run
