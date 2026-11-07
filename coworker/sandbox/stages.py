"""The stages of one sandbox start, for the recording's setup step (observability OB-13,
format 1.2 `sandbox_attrs`). Stage names come from a fixed list; each stage is closed when
the next one begins, and the last one when the start ends."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

STAGES = ("preparing", "download_image", "copy_logins", "create", "connect", "verify", "ready")


class StageLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stages: list[dict[str, Any]] = []
        self._open: Optional[tuple[dict[str, Any], float]] = None
        self.failed_stage: Optional[str] = None
        self.error: Optional[dict[str, Any]] = None
        self.started_at = int(time.time() * 1000)

    def begin(self, name: str) -> None:
        """Close the stage in progress as `ok` and start `name`."""
        if name not in STAGES:
            return
        with self._lock:
            self._close("ok")
            stage = {"name": name, "started_at": int(time.time() * 1000), "duration_ms": None, "status": "ok"}
            self.stages.append(stage)
            self._open = (stage, time.monotonic())

    def mark(self, name: str, **extra: Any) -> None:
        """A stage that cannot be timed on its own (OpenShell's image pull happens inside
        `sandbox create`): written with no duration, the stage in progress left open."""
        if name not in STAGES:
            return
        with self._lock:
            self.stages.append({"name": name, "started_at": int(time.time() * 1000), "duration_ms": None, "status": "ok", **extra})

    def end(self, error: Optional[BaseException] = None) -> None:
        """The start is over. With `error`, the stage in progress failed."""
        with self._lock:
            if error is not None:
                if self._open is not None:
                    self.failed_stage = self._open[0]["name"]
                elif self.stages:
                    self.failed_stage = self.stages[-1]["name"]
                else:
                    self.failed_stage = "preparing"
                text = str(error).strip()
                self.error = {"type": type(error).__name__, "text": text or None}
            self._close("error" if error is not None else "ok")

    def _close(self, status: str) -> None:
        if self._open is None:
            return
        stage, mono = self._open
        stage["duration_ms"] = max(0, int((time.monotonic() - mono) * 1000))
        stage["status"] = status
        self._open = None

    def attrs(self, provider: str, trigger: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "provider": provider,
            "stages": [dict(s) for s in self.stages],
            "failed_stage": self.failed_stage,
            "trigger": trigger,
        }
        if self.error is not None:
            out["error"] = dict(self.error)
        return out


def stage(provider: Any, name: str) -> None:
    """A provider says it moved to stage `name` of its start. Nothing when nobody listens."""
    log = getattr(provider, "stage_log", None)
    if isinstance(log, StageLog):
        log.begin(name)


def mark(provider: Any, name: str, **extra: Any) -> None:
    log = getattr(provider, "stage_log", None)
    if isinstance(log, StageLog):
        log.mark(name, **extra)
