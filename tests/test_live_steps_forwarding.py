"""OPE-261: the live-step batch forwarder must never stall a session for good.

The old logic scheduled a flush only when a batch went from empty to one event; if that
one scheduling call failed (a closed or stale loop), the batch stayed non-empty forever
and no later event ever scheduled again. Now one flush is tracked explicitly per session
and a failure is retried by the next event; the batch is bounded too.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from coworker.server.manager import SessionManager


class _DeadLoop:
    def call_soon_threadsafe(self, *a, **k):
        raise RuntimeError("Event loop is closed")

    def call_later(self, *a, **k):  # pragma: no cover - never reached on the dead loop
        raise AssertionError


class _LiveLoop:
    def __init__(self):
        self.scheduled = []

    def call_soon_threadsafe(self, fn, *args):
        # The forwarder hands over `loop.call_later` plus its arguments; record the intent.
        self.scheduled.append((fn, args))

    def call_later(self, *a, **k):  # pragma: no cover - the stub never runs the timer
        raise AssertionError("call_later is scheduled through call_soon_threadsafe, not called directly")


def _forwarder(loop):
    """The forwarder with just the state it touches (SessionManager is heavy to build)."""
    me = SimpleNamespace(
        _loop=loop,
        _live_loop=None,
        _live_steps={},
        _live_steps_lock=threading.Lock(),
        LIVE_STEP_BATCH_S=SessionManager.LIVE_STEP_BATCH_S,
        LIVE_STEP_BATCH_MAX=SessionManager.LIVE_STEP_BATCH_MAX,
        sent=[],
    )
    me._send_live_steps = lambda sid: SessionManager._send_live_steps(me, sid)
    me.has_session_clients = lambda sid: False  # pop-and-drop path in _send_live_steps
    me.forward = lambda ev: SessionManager._forward_recording_event(me, ev)
    return me


def test_a_failed_schedule_is_retried_by_the_next_event():
    dead, live = _DeadLoop(), _LiveLoop()
    me = _forwarder(dead)
    me.forward({"session_id": "s1", "type": "step_started"})
    assert me._live_steps["s1"] and "s1" not in me._live_flush_pending  # retry allowed

    me._loop = live  # the next event arrives on a live loop
    me.forward({"session_id": "s1", "type": "step_ended"})
    assert len(live.scheduled) == 1, "the second event must schedule the flush the first could not"
    assert "s1" in me._live_flush_pending


def test_one_flush_in_flight_per_session_and_the_batch_is_bounded():
    live = _LiveLoop()
    me = _forwarder(live)
    for i in range(SessionManager.LIVE_STEP_BATCH_MAX + 50):
        me.forward({"session_id": "s1", "type": "step_started", "i": i})
    assert len(live.scheduled) == 1  # not one per event
    assert len(me._live_steps["s1"]) == SessionManager.LIVE_STEP_BATCH_MAX

    # The flush clears the pending flag so a later burst schedules again.
    me._send_live_steps("s1")
    assert "s1" not in me._live_flush_pending and "s1" not in me._live_steps
    me.forward({"session_id": "s1", "type": "step_started"})
    assert len(live.scheduled) == 2
