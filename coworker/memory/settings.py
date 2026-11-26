"""Memory settings — the on/off switch and the user's standing rules.

Settings-level state, deliberately outside the memory table (MEMORY-SPEC §2, §4.3, §6):

- ``enabled``: off means engines are built with no memory tools, no memories block, and
  no memory guidance. Existing memories are kept but inert. Read at build time; running
  sessions finish under the mode they started with.
- ``user_rules``: one text blob the user typed into Settings. Injected verbatim above
  auto memories; on conflict the rule wins. **The agent never writes, edits, or deletes
  this** — no tool touches it; the only writer is the Settings UI via the manager.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# User Rules is a bounded settings field, not a document store: big enough for any
# real rule list, small enough that a paste-accident (or a hostile client) can't
# bloat every future system prompt.
MAX_USER_RULES_CHARS = 20_000

# Only these read as true — Python truthiness would make the string "false" true,
# so a hand-edited (or non-GUI-written) file could not express "off". The same
# policy as _flag in tools/shell.py (#937) and automation (#947), copied rather
# than imported: this module must not depend on the tool layer.
_TRUE_STRINGS = frozenset({"true", "1", "yes"})


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):  # bools are handled above
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return False


def _quarantine(path: Path) -> Optional[Path]:
    """Move a file we cannot use to ``<name>.corrupt`` — never deleted, never
    overwritten by a later save — so the bytes stay recoverable."""
    spare = path.with_suffix(path.suffix + ".corrupt")
    try:
        path.replace(spare)
    except OSError:
        return None
    return spare


def _refuse_file(path: Path, reason: str) -> None:
    spare = _quarantine(path)
    where = f"; kept the bytes in {spare}" if spare else ""
    logger.warning("%s: %s%s", path, reason, where)


class MemorySettingsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _refuse_file(self.path, f"unreadable ({type(exc).__name__})")
            return {}
        if not isinstance(data, dict):
            _refuse_file(self.path, "not a JSON object")
            return {}
        return data

    def _save(self, data: dict) -> None:
        # Write a sibling and rename over the live file: an interrupted write
        # must tear the .tmp, never the settings the user is looking at.
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise

    @property
    def enabled(self) -> bool:
        return _flag(self._load().get("enabled", True))  # on by default (spec §5.4)

    @property
    def user_rules(self) -> str:
        rules = self._load().get("user_rules", "")
        return rules if isinstance(rules, str) else ""

    def set(
        self, *, enabled: Optional[bool] = None, user_rules: Optional[str] = None
    ) -> dict:
        with self._lock:
            data = self._load()
            if enabled is not None:
                data["enabled"] = _flag(enabled)
            if user_rules is not None:
                data["user_rules"] = str(user_rules)[:MAX_USER_RULES_CHARS]
            self._save(data)
        return {"enabled": self.enabled, "user_rules": self.user_rules}

    def snapshot(self) -> dict:
        return {"enabled": self.enabled, "user_rules": self.user_rules}


def format_user_rules(rules: str) -> str:
    """The system-prompt block for user rules. Empty rules -> empty string."""
    text = (rules or "").strip()
    if not text:
        return ""
    return (
        "User rules (written by the user in Settings; always follow these — on any "
        f"conflict they outrank learned memories):\n{text}"
    )
