"""The byte-exact hashing rules shared with the Rust harness (recording format 1.2, §3.11).

`args_hash` is the first 6 hex characters of SHA-256 over the arguments' canonical JSON, and
canonical JSON is RFC 8785 (JCS): keys sorted by UTF-16 code units, no whitespace, strings with
only the required escapes, numbers in the ECMAScript form. `json.dumps` is not the rule: it
writes 1e-07 and 100.0 where JCS writes 1e-7 and 100. A plan item's hash is over its plain
text. The vectors are ocw-context docs/observability/golden/phase2/vectors/hashes.json.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def _number(x: Any) -> str:
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    if math.isnan(x) or math.isinf(x):
        raise ValueError("JSON has no NaN or Infinity")
    if x == 0:
        return "0"
    if x == int(x) and abs(x) < 1e21:
        return str(int(x))
    r = repr(x)  # shortest round-trip digits, as ECMAScript writes them
    if "e" in r:
        mant, exp = r.split("e")
        sign = "-" if exp.startswith("-") else "+"
        return f"{mant}e{sign}{int(exp.lstrip('+-'))}"
    return r


def jcs(value: Any) -> str:
    """RFC 8785 canonical JSON text of `value`."""
    if value is None:
        return "null"
    if isinstance(value, (bool, int, float)):
        return _number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(jcs(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=lambda k: str(k).encode("utf-16-be"))
        return "{" + ",".join(json.dumps(str(k), ensure_ascii=False) + ":" + jcs(value[k]) for k in keys) + "}"
    raise TypeError(f"not JSON: {type(value).__name__}")


def short_hash(value: Any) -> str:
    """6 hex of SHA-256 over the canonical JSON of `value`."""
    return hashlib.sha256(jcs(value).encode("utf-8")).hexdigest()[:6]


def text_hash(text: str) -> str:
    """6 hex of SHA-256 over the text's own UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:6]
