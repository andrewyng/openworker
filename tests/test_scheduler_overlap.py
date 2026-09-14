"""Deterministically exercise completion racing with a scheduler tick."""
import asyncio

from coworker.automation.models import ScheduledTask, Schedule, TaskRun
from coworker.automation.scheduler import Scheduler
from coworker.automation.store import TaskStore


async def test_tick_does_not_queue_a_run_while_previous_completion_is_ready(tmp_path):
    store = TaskStore(tmp_path / 'tasks.db')
    task = ScheduledTask(
        title='blocked', instructions='test', workspace=str(tmp_path),
        schedule=Schedule(kind='cron', cron='0 9 * * 1'),
    )
    store.save(task)
    store._conn.execute('UPDATE scheduled_tasks SET next_run=1.0 WHERE id=?', (task.id,))
    store._conn.commit()
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def runner(current, trigger):
        calls.append(current.id)
        started.set()
        await finish.wait()
        return TaskRun(task_id=current.id, status='ok', trigger=trigger)

    scheduler = Scheduler(store, runner)
    first = asyncio.create_task(scheduler.run_task(task, trigger='catchup'))
    try:
        await started.wait()
        # Wake the active run without yielding to it. Its completion will execute
        # before any duplicate work queued by this tick, clearing the old guard.
        finish.set()
        await scheduler._tick(trigger='schedule')
        await first
        if scheduler._spawned:
            await asyncio.gather(*list(scheduler._spawned))
        assert calls == [task.id]
        assert store.get(task.id).run_count == 1
    finally:
        finish.set()
        await first
        await scheduler.stop()
