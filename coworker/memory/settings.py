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
import threading
from pathlib import Path
from typing import Optional

# User Rules is a bounded settings field, not a document store: big enough for any
# real rule list, small enough that a paste-accident (or a hostile client) can't
# bloat every future system prompt.
MAX_USER_RULES_CHARS = 20_000


class MemorySettingsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @property
    def enabled(self) -> bool:
        return bool(self._load().get("enabled", True))  # on by default (spec §5.4)

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
                data["enabled"] = bool(enabled)
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
