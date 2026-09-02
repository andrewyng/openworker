"""A scheduled run that dies on a provider error must say WHAT failed.

The engine's provider-failure path yields ERROR and returns without a TURN_END. The
scheduled-run loop watched only TURN_END/INTERRUPTED, so `terminal` stayed empty and every
one of these was filed as "run ended as unknown" — 20 of 28 recorded failures on this
machine, each one an error message the engine had already built and this loop dropped.
"""

import pytest

from coworker.events import Event, EventType
from coworker.server.manager import run_failure_message


def _drive(events):
    """The exact terminal/failure classification the scheduled-run loop performs."""
    terminal, failure = "", ""
    for ev in events:
        if ev.type is EventType.TURN_END:
            terminal = str((ev.data or {}).get("status") or "")
        elif ev.type is EventType.INTERRUPTED:
            terminal = "interrupted"
        elif ev.type is EventType.ERROR:
            terminal = "error"
            data = ev.data or {}
            kind = str(data.get("error_type") or "").strip()
            text = str(data.get("error") or "").strip()
            failure = f"{kind}: {text}" if kind and text else (text or kind)
    return terminal, failure


def _message(terminal, failure, *, produced=True, timed_out=False, deadline=900.0):
    """The REAL classifier — not a copy of it."""
    return run_failure_message(
        terminal, failure, produced=produced, timed_out=timed_out, deadline=deadline
    )


def test_provider_error_is_named_not_unknown():
    ev = Event(EventType.ERROR, {"error": "connection refused", "error_type": "APIConnectionError"})
    terminal, failure = _drive([ev])
    msg = _message(terminal, failure)
    assert "unknown" not in msg
    assert "APIConnectionError" in msg and "connection refused" in msg


def test_error_without_a_type_still_reports_the_text():
    terminal, failure = _drive([Event(EventType.ERROR, {"error": "402 payment required"})])
    assert "402 payment required" in _message(terminal, failure)


def test_error_with_no_payload_falls_back_to_unknown():
    """The genuine no-information case must still be honest rather than inventing detail."""
    terminal, failure = _drive([Event(EventType.ERROR, {})])
    assert _message(terminal, failure) == "run ended as error"


def test_a_clean_run_is_unaffected():
    terminal, failure = _drive([Event(EventType.TURN_END, {"status": "completed"})])
    assert _message(terminal, failure) is None


def test_max_iterations_still_reported_as_itself():
    terminal, failure = _drive([Event(EventType.TURN_END, {"status": "max_iterations_exceeded"})])
    assert _message(terminal, failure) == "run ended as max_iterations_exceeded"


def test_interrupt_after_an_error_wins_as_the_last_word():
    terminal, failure = _drive([
        Event(EventType.ERROR, {"error": "boom", "error_type": "X"}),
        Event(EventType.INTERRUPTED, {}),
    ])
    assert terminal == "interrupted"
    assert _message(terminal, failure) == "run ended as interrupted"
