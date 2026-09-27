"""The Settings ▸ Sandbox page's data (UX-051 A): which provider this machine uses, which
network profile, and which credential files an agent may be given. Everything here is
machine-level and lives in the machine's config.toml, never in a repository's.
"""

from __future__ import annotations

import sys
from typing import Any, Optional

from .. import config as app_config
from . import credentials, network_profiles
from .workspace import DIRECT, OPENSHELL, SEATBELT


def _availability(name: str) -> tuple[bool, str]:
    """(usable, why not) for a provider on this machine."""
    if name == DIRECT:
        return True, ""
    if name == SEATBELT:
        if sys.platform != "darwin":
            return False, "macOS only"
        from .providers import seatbelt

        try:
            seatbelt.preflight()
            return True, ""
        except seatbelt.SeatbeltUnavailable as exc:
            return False, str(exc)
    if name == OPENSHELL:
        from .selection import openshell_problem

        problem = openshell_problem()
        return (problem is None), (problem or "")
    return False, "unknown"


def snapshot(cfg: Optional[app_config.Config] = None) -> dict[str, Any]:
    cfg = cfg or app_config.load_config()
    from .selection import select

    try:
        chosen = select(cfg.sandbox_provider)
        effective, refused = chosen.provider, ""
    except Exception as exc:  # an explicit choice that cannot be used: sessions are refused
        effective, refused = "", str(exc)
    providers = []
    for name in (DIRECT, SEATBELT, OPENSHELL):
        usable, why = _availability(name)
        providers.append({"name": name, "usable": usable, "why": why})
    return {
        "platform": sys.platform,
        "provider": cfg.sandbox_provider or "",  # "" = the default rule
        "effective_provider": effective,
        "refused": refused,
        "providers": providers,
        "network_profile": (cfg.sandbox_network_profile or network_profiles.DEFAULT_PROFILE),
        "network_profiles": [
            {"name": name, "hosts": network_profiles.hosts(name)} for name in network_profiles.PROFILES
        ],
        "credentials": _for_display(credentials.entries(cfg.sandbox_credentials)),
        "config_path": str(app_config.global_config_path()),
    }


def _for_display(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A shipped entry's title and wording are left out when the user has not changed
    them, so the app can show them in the user's language; a user's own text is kept."""
    shipped = {e["name"]: e for e in credentials.DEFAULT_ENTRIES}
    out = []
    for row in rows:
        base = shipped.get(row["name"])
        row = dict(row)
        if base is not None:
            for key in ("title", "does"):
                if row.get(key) == base.get(key):
                    row.pop(key, None)
        out.append(row)
    return out


def update(body: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial change and return the new snapshot. Validates before writing."""
    body = body or {}
    if "provider" in body:
        provider = str(body.get("provider") or "").strip().lower()
        if provider and provider not in (DIRECT, SEATBELT, OPENSHELL):
            return {"ok": False, "error": f"unknown sandbox provider: {provider}"}
        app_config.set_global_value("sandbox_provider", provider) if provider else _unset("sandbox_provider")
        from .selection import openshell_problem

        if provider == OPENSHELL:
            openshell_problem(fresh=True)
    if "network_profile" in body:
        profile = str(body.get("network_profile") or "").strip().lower()
        try:
            network_profiles.check(profile)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        app_config.set_global_value("sandbox_network_profile", profile)
    if "credentials" in body:
        rows = body.get("credentials")
        if not isinstance(rows, list):
            return {"ok": False, "error": "credentials must be a list"}
        cleaned: list[dict[str, Any]] = []
        for raw in rows:
            if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
                return {"ok": False, "error": "every credential entry needs a name"}
            path = str(raw.get("path") or "").strip()
            if path and not (path == "~" or path.startswith("~/") or path.startswith("/") or path[1:3] == ":\\"):
                return {"ok": False, "error": f"the path for '{raw['name']}' must start with ~/ or be absolute"}
            row: dict[str, Any] = {"name": str(raw["name"]).strip(), "enabled": bool(raw.get("enabled"))}
            for key in ("title", "path", "does"):
                if raw.get(key) is not None:
                    row[key] = str(raw[key])
            if raw.get("hosts") is not None:
                hosts = raw["hosts"]
                if not isinstance(hosts, list) or not all(isinstance(h, str) and ":" in h for h in hosts):
                    return {"ok": False, "error": f"hosts for '{raw['name']}' must be host:port entries"}
                row["hosts"] = [h.strip() for h in hosts]
            cleaned.append(row)
        # Keep only what differs from the shipped entry, so the file stays small and the
        # shipped defaults can still improve underneath.
        shipped = {e["name"]: e for e in credentials.DEFAULT_ENTRIES}
        slim: list[dict[str, Any]] = []
        for row in cleaned:
            base = shipped.get(row["name"])
            if base is None:
                slim.append(row)
                continue
            diff = {k: v for k, v in row.items() if k == "name" or base.get(k) != v}
            if len(diff) > 1:
                slim.append(diff)
        app_config.set_global_tables("sandbox_credentials", slim)
    return {"ok": True, **snapshot()}


def _unset(key: str) -> None:
    """Remove a top-level key from the machine's config.toml (back to the default rule)."""
    import re

    target = app_config.global_config_path()
    if not target.is_file():
        return
    lines = target.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not re.match(rf"\s*{re.escape(key)}\s*=", line)]
    target.write_text("\n".join(kept) + "\n", encoding="utf-8")
