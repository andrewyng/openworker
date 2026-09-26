"""The clock, as a tool.

The current time is deliberately NOT in the per-turn context block (OPE-192): that block
is glued onto a message the provider has already cached, and a value that changes by
itself rewrites the message on every turn, which throws the whole cached conversation
away — measured at up to 77% of a long session's cost. A tool call costs a
few tokens and only when the model actually needs the time (a deadline, "how long ago",
an absolute wake time for `sleep_until`). Relative waits (`sleep_for`) and timer wakes
(the wake message carries its fire time) need no clock reading at all.
"""

from __future__ import annotations

from datetime import datetime, timezone


def current_time() -> dict:
    """The current date and time. Call this when a task depends on the clock — a
    deadline, "how long ago", the date for a note, or the wake time for sleep_until.
    The "Today's date" in your environment is a session-start snapshot and may be stale;
    this is live. Returns the local time with its UTC offset and timezone name, the
    same instant in UTC, and the weekday."""
    now = datetime.now().astimezone()
    utc = now.astimezone(timezone.utc)
    return {
        "local": now.isoformat(timespec="seconds"),
        "timezone": now.tzname() or "",
        "utc": utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "weekday": now.strftime("%A"),
    }


def clock_tools() -> list:
    return [current_time]
