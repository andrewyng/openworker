"""Attendance — a per-session setting for *who answers when the agent asks*.

It does **not** change the autonomy ceiling (the permission mode does). Three values:

* ``attended`` — a person is at the screen: questions and approval cards appear inline.
* ``inbox`` — nobody is watching right now: anything that would prompt inline is parked in
  the Inbox and the agent suspends until answered; the composer is disabled.
* ``auto`` — nobody will come: the engine answers by fixed rule and records each answer.
  Questions get a least-destructive default, folder requests are declined with guidance,
  pinned tool installs run the verified installer, and an approval card that only a person
  could clear is refused (unless the permission mode clears it). Never a hang.

The toggle was a boolean (attended / unattended-to-Inbox) before the third value existed;
stored ``true`` still reads as ``inbox`` and ``false`` as ``attended``. Turning it on is a
one-tap confirm (enforced at the API/GUI layer). This registry just persists the per-session
value; the fixed replies live here too so every surface says the same thing.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

ATTENDED = "attended"
INBOX = "inbox"
AUTO = "auto"
ATTENDANCE_VALUES = (ATTENDED, INBOX, AUTO)

# What the engine says on the person's behalf in `auto`. Fixed text, never model-authored.
AUTO_QUESTION_ANSWER = (
    "No one is available. Choose the least destructive option that still satisfies the "
    "task as written."
)
AUTO_DIRECTORY_REPLY = (
    "No user is available to choose a folder. Work inside the current workspace. If the "
    "task cannot continue without information only the user has, stop and say what is "
    "missing so it can be restarted."
)
# In dangerously-bypass-approvals the out-of-root floor is cleared, so the shell reaches
# any path; the file tools stay scoped to the session's folders by construction.
AUTO_DIRECTORY_REPLY_UNSCOPED = (
    "No user is available to choose a folder. Outside the workspace, use your shell tools "
    "(they can reach any path); the file tools stay inside the workspace. If the task "
    "cannot continue without information only the user has, stop and say what is missing "
    "so it can be restarted."
)
# Printed once when a session starts in dangerously-bypass-approvals.
DANGEROUS_MODE_WARNING = "Dangerously bypass approvals is on. Nothing will stop for you."


def normalize_attendance(value: object) -> str:
    """Accept the three names or the legacy boolean; anything else reads as attended."""
    if isinstance(value, bool):
        return INBOX if value else ATTENDED
    text = str(value or "").strip().lower()
    if text in ("true", "on", "unattended"):
        return INBOX
    return text if text in ATTENDANCE_VALUES else ATTENDED


class UnattendedRegistry:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}
        if self.path and self.path.is_file():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for sid, value in dict(raw).items():
                normalized = normalize_attendance(value)
                if normalized != ATTENDED:
                    self._values[str(sid)] = normalized

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._values, indent=2), encoding="utf-8")

    def attendance(self, session_id: str) -> str:
        return self._values.get(session_id, ATTENDED)

    def is_unattended(self, session_id: str) -> bool:
        """True for both unattended values — the Inbox route and the auto route."""
        return self.attendance(session_id) != ATTENDED

    def is_auto(self, session_id: str) -> bool:
        return self.attendance(session_id) == AUTO

    def set(self, session_id: str, value: object) -> str:
        """Set the session's attendance (a name, or the legacy boolean) and return it."""
        normalized = normalize_attendance(value)
        with self._lock:
            if normalized == ATTENDED:
                self._values.pop(session_id, None)
            else:
                self._values[session_id] = normalized
            self._save()
        return normalized

    def sessions(self) -> list[str]:
        return list(self._values)
