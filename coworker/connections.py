"""Connection hierarchy (UI-REFRESH §4) — the per-persona + per-session connector layers.

Three layers gate whether a connector is *effective* for a session:

1. **account-connected** — a connector profile with valid creds exists (``connector_list[].connected``).
   Owned by the SecretStore; not stored here.
2. **persona-default-enabled** — per persona, which connected connectors are on by default for its
   sessions (``PersonaConnectionStore``). Seeded from the persona manifest's ``recommends`` and then
   user-editable.
3. **session-override** — per session, an explicit on/off that overrides the persona default
   (``SessionConnectionStore``). Absence of an override means *inherit the persona default*.

``effective(connector)`` = **connected** AND (``session_override`` if present, else the persona
default if present, else inherit-on). A connector that is not connected is never effective. A
connector with no persona opinion and no session override inherits *on* — the persona's
``recommends`` curates what to *suggest*/seed-on, it is not an exhaustive allow-list, so a connected
connector the persona never mentions stays available unless something explicitly turns it off.

Both stores are tiny JSON files mirroring ``SubscriptionStore`` (optional path, ``_load``/``_save``,
``indent=2``); the manager owns one of each and resolves via :func:`effective`.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _quarantine(path: Optional[Path]) -> Optional[Path]:
    """Move an unreadable file aside so the next save cannot overwrite the only copy of it.
    Returns the path it moved to, or None if the move failed."""
    if not path:
        return None
    spare = path.with_suffix(path.suffix + ".corrupt")
    try:
        path.replace(spare)
    except OSError:
        return None
    return spare


def _load_rows(path: Optional[Path], key: str) -> dict[str, dict[str, bool]]:
    """The rows of a connections file — or an empty set if the file cannot be used.

    A corrupt file must never stop startup: ``SessionManager.__init__`` constructs these
    stores for every server start, so one truncated file stopped the server from starting.

    Starting empty is NOT neutral — these files hold the user's connector choices, and a
    dropped *mute* brings a connector back on — so the bytes are kept (moved aside, never
    deleted) and the path is logged. The same shape the risk-override store uses.
    """
    if not (path and path.is_file()):
        return {}
    reason: Optional[str] = None
    rows: dict[str, dict[str, bool]] = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as e:
        reason = f"unreadable ({type(e).__name__})"
    else:
        if not isinstance(data, dict):
            reason = "not a JSON object"
        else:
            raw = data.get(key)
            if raw is None:
                raw = {}
            if not isinstance(raw, dict):
                reason = f"'{key}' is not a JSON object"
            else:
                rows = {
                    str(pid): {
                        str(c): bool(v)
                        for c, v in (row if isinstance(row, dict) else {}).items()
                    }
                    for pid, row in raw.items()
                }
    if reason is None:
        return rows
    message = f"{path}: {reason}; loaded no connections"
    spare = _quarantine(path)
    if spare is not None:
        message += f" (original kept at {spare})"
    logger.warning(message)
    return {}


def _write_rows(path: Optional[Path], key: str, rows: dict[str, dict[str, bool]]) -> None:
    """Persist the rows, writing a sibling .tmp and renaming it over the real file — so a
    crash mid-write leaves the previous complete file instead of a truncated one (the
    shape mentions.py uses)."""
    if not path:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps({key: rows}, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


class PersonaConnectionStore:
    """``{persona_id: {connector: bool}}`` — the per-persona default on/off for each connector."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, bool]] = {}
        self._load()

    def _load(self) -> None:
        self._rows = _load_rows(self.path, "personas")

    def _save(self) -> None:
        _write_rows(self.path, "personas", self._rows)

    # -- queries ----------------------------------------------------------------
    def get(self, persona_id: str) -> dict[str, bool]:
        """The persona's stored row (a copy). Empty dict if it was never seeded/edited — this does
        NOT seed; use :meth:`defaults_for` to seed from a manifest."""
        return dict(self._rows.get(persona_id, {}))

    def defaults_for(
        self, persona_id: str, manifest, *, connected: set[str]
    ) -> dict[str, bool]:
        """The persona's default connector map, seeding it from the manifest on first read.

        Seeding rule: a ``recommends`` item of kind ``connector`` with ``tier == "core"`` defaults
        **True**; every other recommended connector (optional) defaults **False**. (mcp recommends
        and non-connector kinds are ignored.) The seeded row is persisted on first read so the seed
        is stable thereafter — a later edit/toggle persists over it. A persona with no manifest
        (e.g. a builtin) seeds an empty row.

        NOTE: this intentionally deviates from §4.2's literal "whose connector is connected" wording
        to honor its intent. A core connector seeds True even when not connected yet:
        :func:`effective` already gates on ``connected``, so it stays filtered out while
        disconnected and **self-lights when it later connects** — rather than being frozen False
        forever (a stale seed that would break the "connect a core connector → on by default"
        flow). ``connected`` is kept in the signature for back-compat but is no longer read here,
        leaving :func:`effective`'s connected-gate the single source of truth for connectedness.
        """
        with self._lock:
            if persona_id in self._rows:
                return dict(self._rows[persona_id])
            seeded: dict[str, bool] = {}
            recommends = list(getattr(manifest, "recommends", None) or [])
            for rec in recommends:
                if getattr(rec, "kind", None) != "connector":
                    continue
                # core → on by default (connectedness is enforced later by effective()).
                seeded[rec.ref] = getattr(rec, "tier", "") == "core"
            self._rows[persona_id] = seeded
            self._save()
            return dict(seeded)

    # -- mutations --------------------------------------------------------------
    def set(self, persona_id: str, connector: str, enabled: bool) -> None:
        with self._lock:
            self._rows.setdefault(persona_id, {})[connector] = bool(enabled)
            self._save()


class SessionConnectionStore:
    """``{session_id: {connector: bool}}`` — per-session overrides only; an absent entry means the
    session inherits the persona default."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, bool]] = {}
        self._load()

    def _load(self) -> None:
        self._rows = _load_rows(self.path, "sessions")

    def _save(self) -> None:
        _write_rows(self.path, "sessions", self._rows)

    # -- queries ----------------------------------------------------------------
    def get(self, session_id: str) -> dict[str, bool]:
        return dict(self._rows.get(session_id, {}))

    # -- mutations --------------------------------------------------------------
    def set(self, session_id: str, connector: str, enabled: bool) -> None:
        with self._lock:
            self._rows.setdefault(session_id, {})[connector] = bool(enabled)
            self._save()

    def clear(self, session_id: str, connector: str) -> None:
        """Drop a single override so the session inherits the persona default again."""
        with self._lock:
            row = self._rows.get(session_id)
            if row and connector in row:
                del row[connector]
                if not row:
                    del self._rows[session_id]
                self._save()

    def remove_session(self, session_id: str) -> None:
        """Drop all of a session's overrides (called when the session is deleted)."""
        with self._lock:
            if session_id in self._rows:
                del self._rows[session_id]
                self._save()


def effective(
    *,
    connected: set[str],
    persona_defaults: dict[str, bool],
    session_overrides: dict[str, bool],
) -> dict[str, bool]:
    """Resolve the effective-enabled connectors for a session — the §4 invariant.

    For each **connected** connector: a session override (if present) wins; otherwise the persona
    default (if present) applies; otherwise it inherits *on*. Not-connected connectors are never
    effective. Returns only the effective-**enabled** connectors, each mapped to ``True`` (muted /
    off connectors are omitted), so the result reads as the session's live connector set.
    """
    out: dict[str, bool] = {}
    for connector in connected:
        if connector in session_overrides:
            enabled = session_overrides[connector]
        elif connector in persona_defaults:
            enabled = persona_defaults[connector]
        else:
            enabled = True  # connected, no opinion → inherit on
        if enabled:
            out[connector] = True
    return out
