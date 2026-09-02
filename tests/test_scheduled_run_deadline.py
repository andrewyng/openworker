"""A scheduled run must be able to stop itself.

`engine.run` had no timeout. An automation that got stuck on one step looped until
something outside killed it, and unattended at 07:15 there is nothing outside. Measured
2026-09-01: one applier run spent 21 minutes retrying a single dropdown and wrote no
output; a second went 26 the same way. Both were only recorded because a human restarted
the server — which is not a mechanism, it is a person.

These pin the deadline and, as importantly, that hitting it is reported as hitting it.
A run stopped by the clock must never read as a completed one.
"""

from __future__ import annotations

import asyncio

import pytest


async def _forever():
    while True:
        await asyncio.sleep(0.01)


def test_a_hung_run_is_cut_at_the_deadline():
    async def go():
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.2):
                await _forever()
    asyncio.run(go())


def test_a_run_inside_the_deadline_is_untouched():
    async def go():
        async with asyncio.timeout(1.0):
            await asyncio.sleep(0.05)
            return "completed"
    assert asyncio.run(go()) == "completed"


def test_the_manager_wraps_the_engine_loop_in_a_timeout():
    """Guards the actual call site, so the timeout cannot be dropped in a refactor."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "coworker/server/manager.py"
    text = src.read_text(encoding="utf-8")
    assert "COWORKER_RUN_TIMEOUT_S" in text, "the deadline is no longer configurable"
    i = text.index("async for _event in engine.run(opening)")
    window = text[max(0, i - 700):i]
    assert "asyncio.timeout(deadline)" in window, (
        "engine.run is no longer inside a deadline — a stuck scheduled run would hang "
        "forever again"
    )


def test_a_timed_out_run_is_not_reported_as_completed():
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "coworker/server/manager.py"
    text = src.read_text(encoding="utf-8")
    assert 'terminal = "timed out"' in text
    assert "stopped at the" in text and "limit" in text, (
        "a run cut by the clock must say so in run.error, not fall back to "
        "'ended as unknown'"
    )
