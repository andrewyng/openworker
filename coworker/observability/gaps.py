"""Gaps in the timeline (observability OB-14, format 1.2): stretches when the machine was
offline from its controller, or nobody was watching the session. Each is a notice with a
duration in every turn running at the time, cut at the turn's start and end
(phase-2 design §11.3).

A condition is "on" from a moment until it ends. `nobody_watching` belongs to one session
(its engine's recording); `controller_offline` to the whole machine, so it goes to every
recording here, including those of engines made while it is on."""

from __future__ import annotations

import json
import logging
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

GAP_NOTICES = ("controller_offline", "nobody_watching", "clock_jump")

_lock = threading.Lock()
_recordings: "weakref.WeakSet[Any]" = weakref.WeakSet()
_machine: dict[str, tuple[int, dict[str, Any]]] = {}  # name -> (since ms, attrs)


def now_ms() -> int:
    return int(time.time() * 1000)


def register(recording: Any) -> None:
    """A new engine's recording hears the machine's conditions, and any already on."""
    with _lock:
        _recordings.add(recording)
        ongoing = dict(_machine)
    for name, (since, attrs) in ongoing.items():
        recording.condition(name, True, at_ms=since, attrs=attrs)


def machine_condition(name: str, on: bool, *, at_ms: Optional[int] = None, attrs: Optional[dict[str, Any]] = None) -> None:
    """A machine-wide condition starts or ends, for every recording on this machine."""
    at = at_ms if at_ms is not None else now_ms()
    with _lock:
        if on:
            if name in _machine:
                return  # already on: the first moment stands
            _machine[name] = (at, dict(attrs or {}))
        else:
            if name not in _machine and not attrs:
                return
            since, held = _machine.pop(name, (at, {}))
            attrs = {**held, **(attrs or {})}
        targets = list(_recordings)
    for rec in targets:
        try:
            rec.condition(name, on, at_ms=at, attrs=attrs)
        except Exception:  # noqa: BLE001 - recording never breaks the link
            log.exception("observability: could not note %s", name)


def machine_on(name: str) -> bool:
    with _lock:
        return name in _machine


# -- the controller link (E15b-1) ----------------------------------------------------------------
# When the link is lost, the moment is also saved in the state folder, so a restart before
# it comes back still writes the gap into the turns that are running when it does.

_LOST_FILE = "controller-link-lost.json"


def link_lost(state: Optional[Path], reason: str) -> None:
    at = now_ms()
    if state is not None:
        path = Path(state) / _LOST_FILE
        try:
            if not path.exists():
                path.write_text(json.dumps({"lost_at": at, "reason": reason}), encoding="utf-8")
        except OSError:
            log.warning("observability: could not save when the controller link was lost")
    machine_condition("controller_offline", True, at_ms=at, attrs={"reason": reason})


def link_back(state: Optional[Path]) -> None:
    saved: dict[str, Any] = {}
    if state is not None:
        path = Path(state) / _LOST_FILE
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            path.unlink()
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.warning("observability: could not read when the controller link was lost")
    if saved and not machine_on("controller_offline"):
        # Lost before a restart: the condition starts at the saved moment.
        machine_condition("controller_offline", True, at_ms=int(saved.get("lost_at") or now_ms()),
                          attrs={"reason": str(saved.get("reason") or "failed")})
    machine_condition("controller_offline", False)


def link_reason(exc: Optional[BaseException]) -> str:
    """`closed` (the controller closed it), `ping_timeout` or `failed`."""
    if exc is None:
        return "closed"
    text = str(exc).lower()
    if "ping" in text and ("timeout" in text or "timed out" in text):
        return "ping_timeout"
    return "failed"
