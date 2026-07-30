"""GuardLogger — dedicated logger for guard decisions with audit trail.

Writes structured, timestamped entries to a configurable log file so every
allow/deny decision can be traced post-hoc.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

_DEFAULT_LOG_PATH = "guard.log"


class GuardLogger:
    """Logs guard decisions to a dedicated file.

    Each entry carries: timestamp, level, status (ALLOW/DENY), agent id,
    tool name, matching rule, and reason.
    """

    def __init__(
        self,
        log_path: str | Path = _DEFAULT_LOG_PATH,
        level: int = logging.INFO,
    ):
        self._logger = logging.getLogger("coworker.guard")
        self._logger.setLevel(level)
        self._logger.handlers.clear()  # don't duplicate on re-init

        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        handler = logging.FileHandler(str(path), encoding="utf-8")
        handler.setLevel(level)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        self._logger.addHandler(handler)

        # Also propagate warnings+ to the root logger so errors appear in the
        # main log during development.  Keep INFO silent at root.
        self._logger.propagate = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_decision(
        self,
        *,
        agent_id: str = "",
        tool_name: str,
        arguments: dict[str, Any],
        allowed: bool,
        reason: str = "",
        rule: str = "",
        needs_user: bool = False,
    ) -> None:
        """Persist a guard decision record to the log file.

        Args:
            agent_id: Short identifier for the running agent.
            tool_name: The tool that was called (e.g. ``run_shell``, ``explore``).
            arguments: The tool's argument dict.
            allowed: Whether the call was allowed.
            reason: Human-readable reason for the decision.
            rule: Name of the rule that matched, if any.
            needs_user: Whether the decision defers to the user.
        """
        status = "ALLOW" if allowed else "DENY"
        parts = [
            status,
            f"agent={agent_id}",
            f"tool={tool_name}",
        ]
        if rule:
            parts.append(f"rule={rule}")
        if reason:
            parts.append(f"reason={reason}")
        if needs_user:
            parts.append("needs_user=1")

        self._logger.info(" | ".join(parts))

    def set_level(self, level: int) -> None:
        """Change the log level at runtime (e.g. for debugging)."""
        self._logger.setLevel(level)
        for handler in self._logger.handlers:
            handler.setLevel(level)
