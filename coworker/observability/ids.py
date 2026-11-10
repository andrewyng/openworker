"""ULIDs for recordings: message ids (`mid`), turn ids and step ids.

A ULID is 26 characters of Crockford base32: 48 bits of wall-clock milliseconds, then
80 random bits. Ids made later sort after ids made earlier. Within one millisecond the
random part is incremented instead of redrawn, so ids from one process stay strictly
increasing even when many are made at once (technical design §3.4).
"""

from __future__ import annotations

import os
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_RANDOM_MAX = (1 << 80) - 1

_lock = threading.Lock()
_last_ms = -1
_last_random = 0


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_ulid() -> str:
    """A new ULID, strictly greater than every earlier one from this process."""
    global _last_ms, _last_random
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms <= _last_ms:
            ms = _last_ms
            if _last_random == _RANDOM_MAX:
                ms += 1  # the random part ran out within one millisecond: borrow the next
                rand = int.from_bytes(os.urandom(10), "big")
            else:
                rand = _last_random + 1
        else:
            rand = int.from_bytes(os.urandom(10), "big")
        _last_ms, _last_random = ms, rand
    return _encode(ms, 10) + _encode(rand, 16)
