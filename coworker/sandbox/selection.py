"""Which sandbox provider a session gets (design doc, rulings 17, 18, 22 and 29).

- An explicit choice (the environment variable, or `sandbox_provider` in the machine's
  config.toml) is obeyed. If it says `openshell` and OpenShell is not usable, sessions are
  REFUSED: a silent fallback would hide the loss of protection.
- With no choice made: a headless machine (`openworker up`) uses OpenShell when it is
  installed and running, and otherwise runs `direct` with a loud warning. Anything else
  (the desktop app, the terminal app, tests) runs `direct`, as it always has.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .workspace import DIRECT, OPENSHELL, PROVIDER_ENV, RUNNER_LOCAL, SEATBELT

HEADLESS_ENV = "OPENWORKER_HEADLESS"  # set by `openworker up` / `join` for their process
KNOWN = (DIRECT, SEATBELT, OPENSHELL, RUNNER_LOCAL)
_PROBE_SECONDS = 30.0

_probe_lock = threading.Lock()
_probe: tuple[float, Optional[str]] = (0.0, None)  # (when, why OpenShell is unusable | None)


@dataclass(frozen=True)
class Selection:
    provider: str
    explicit: bool  # someone chose it (environment or config), as opposed to the default rule
    warning: str = ""  # say this loudly (headless machine running unprotected)


def is_headless() -> bool:
    return os.environ.get(HEADLESS_ENV) == "1"


def openshell_problem(*, fresh: bool = False) -> Optional[str]:
    """None when OpenShell is usable on this machine, else why not. The answer is kept for a
    short while, because it is asked for every session and costs two CLI calls."""
    global _probe
    with _probe_lock:
        when, problem = _probe
        if not fresh and when and time.monotonic() - when < _PROBE_SECONDS:
            return problem
        from .providers import openshell

        try:
            openshell.preflight()
            problem = None
        except (openshell.OpenShellUnavailable, RuntimeError, OSError) as exc:
            problem = str(exc)
        _probe = (time.monotonic(), problem)
        return problem


def select(configured: Optional[str] = None, *, headless: Optional[bool] = None) -> Selection:
    """Raises `OpenShellUnavailable` or `SeatbeltUnavailable` when that sandbox was chosen
    explicitly and is not usable: a silent fallback would hide the loss of protection."""
    chosen = (os.environ.get(PROVIDER_ENV) or configured or "").strip().lower()
    if chosen:
        if chosen not in KNOWN:
            raise ValueError(f"unknown sandbox provider {chosen!r} (known: {', '.join(KNOWN)})")
        if chosen == OPENSHELL:
            problem = openshell_problem(fresh=True)
            if problem:
                from .providers.openshell import OpenShellUnavailable

                raise OpenShellUnavailable(
                    f"This machine is set to run agents in OpenShell sandboxes, and OpenShell cannot be used right now, so no session will start. {problem}"
                )
        if chosen == SEATBELT:
            from .providers import seatbelt

            try:
                seatbelt.preflight()
            except seatbelt.SeatbeltUnavailable as exc:
                raise seatbelt.SeatbeltUnavailable(
                    f"This machine is set to run agents in the macOS sandbox (Seatbelt), and it cannot be used right now, so no session will start. {exc}"
                ) from None
        return Selection(chosen, explicit=True)
    if headless if headless is not None else is_headless():
        problem = openshell_problem()
        if problem is None:
            return Selection(OPENSHELL, explicit=False)
        return Selection(DIRECT, explicit=False, warning=unprotected_warning(problem))
    return Selection(DIRECT, explicit=False)


def unprotected_warning(problem: str) -> str:
    hint = "" if "sandbox setup" in problem else " Run `openworker machine sandbox setup` to protect this machine."
    return f"WARNING: agents on this machine run WITHOUT a sandbox (direct mode). {problem}{hint}"
