"""Regression: skip-on-overlap must hold even when a due() scan races the finish.

The scheduler documents "skip-on-overlap (don't stack a run if the previous is still
going)". The guard used to be taken inside the *spawned* ``run_task`` coroutine, which
leaves a TOCTOU window:

1. A run suspends (parked approval). It never advances ``next_run``, so every tick's
   ``due()`` scan keeps handing the task back and each tick spawns another ``run_task``.
2. Those extra spawns normally start while the guard is still held and bail out.
3. But if one of them only gets its first execution slot *after* the in-flight run
   finished — the guard released **and** ``next_run`` advanced — it sails past the guard
   and runs the task a second time.

On a loaded CI runner this surfaced as an intermittent failure of
``test_standing_approvals.py::test_blocked_run_does_not_stall_other_tasks``
(``assert 2 == 1``). The test below pins the window deterministically instead of racing
for it: it opens the gate from inside a ``due()`` call, i.e. at the exact moment a tick
has just decided to spawn a duplicate.
"""

from __future__ import annotations

import asyncio

import pytest

from coworker.automation.models import Schedule, ScheduledTask, TaskRun
from coworker.automation.scheduler import Scheduler
from coworker.automation.store import TaskStore


def _task(title: str) -> ScheduledTask:
    return ScheduledTask(
        title=title,
        instructions="summarize the week and post it",
        schedule=Schedule(kind="cron", cron="0 9 * * 1"),
        workspace="/tmp/cw-overlap",
    )


@pytest.mark.asyncio
async def test_due_scan_racing_a_finishing_run_does_not_double_run(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    blocked = _task("blocked")
    store.save(blocked)
    store._conn.execute(
        "UPDATE scheduled_tasks SET next_run=1.0 WHERE id=?", (blocked.id,)
    )
    store._conn.commit()

    gate = asyncio.Event()

    async def runner(task, trigger):
        if task.id == blocked.id:
            await gate.wait()  # parked approval: suspended until a human answers
        return TaskRun(task_id=task.id, status="ok", trigger=trigger)

    # Open the gate from *inside* a due() scan: at that instant the tick has already
    # read the task as due (next_run is still 1.0 — the in-flight run hasn't saved yet)
    # and is about to spawn for it, while the original run is free to finish.
    real_due = store.due
    scans = 0

    def due(**kwargs):
        nonlocal scans
        rows = real_due(**kwargs)
        scans += 1
        if scans == 4:
            gate.set()
        return rows

    store.due = due  # type: ignore[method-assign]

    sched = Scheduler(store, runner, tick_seconds=0.05)
    sched.start()
    await asyncio.sleep(0.5)
    try:
        assert store.get(blocked.id).run_count == 1
    finally:
        await sched.stop()
