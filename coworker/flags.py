"""Reading a boolean the way the caller meant it — at every outside boundary.

`bool("false")` is `True`, so an API body that arrives as a string silently
inverts what it asked for: a client that sends ``{"enabled": "false"}`` turns
the feature ON. ``"0"`` is inverted the same way, and a body is a raw JSON
dict at these boundaries — nothing validates its types before the handler
reads it. So every boundary that reads a flag from outside goes through
`flag()`.
"""

from __future__ import annotations

from typing import Any

_TRUE_STRINGS = frozenset({"true", "1", "yes"})


def flag(value: Any) -> bool:
    """True only for a value a caller could have meant as "on".

    `True`, `1`, `"true"`, `"1"` and `"yes"` (any case, surrounding space
    ignored) are true; everything else — `"false"`, `"0"`, `None`, `0`, `2`,
    and any other string — is false. A wrong-shaped value is never an
    exception: a flag is a question, and "no" is the safe answer.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return False
