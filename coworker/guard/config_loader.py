"""GuardConfigLoader — load and cache guard YAML configuration.

Supports change detection via file mtime so the rule set can be hot-reloaded
without restarting the engine (optional for MVP).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional


class GuardConfigLoader:
    """Reads guard YAML config from disk, caches it, and detects file changes.

    Usage::

        loader = GuardConfigLoader("config/guard.yaml")
        cfg = loader.load_config()          # first load
        cfg = loader.load_config()          # cached, unless mtime changed
    """

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self._cached: Optional[dict[str, Any]] = None
        self._mtime: float = 0.0

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_config(self) -> dict[str, Any]:
        """Load and cache the YAML config.

        Returns the cached dict when the file hasn't changed since the last
        load.  Returns an empty dict when the file is missing.
        """
        if not self._has_changed():
            return self._cached or {}

        try:
            import yaml

            with open(self.config_path, "r") as f:
                self._cached = yaml.safe_load(f) or {}
            self._mtime = os.path.getmtime(self.config_path)
        except (FileNotFoundError, PermissionError):
            self._cached = {}
            self._mtime = 0.0
        except yaml.YAMLError as exc:
            self._cached = {}
            self._mtime = 0.0

        return self._cached or {}

    # ------------------------------------------------------------------
    # Change detection
    # ------------------------------------------------------------------

    def has_changed(self) -> bool:
        """True when the file has been modified since the last :meth:`load_config`."""
        try:
            current = os.path.getmtime(self.config_path)
            return current != self._mtime
        except (FileNotFoundError, PermissionError):
            return self._cached is not None

    def _has_changed(self) -> bool:
        """Internal version (used by :meth:`load_config`)."""
        return self.has_changed()
