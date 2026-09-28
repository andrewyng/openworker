"""The Settings ▸ Sandbox page's data (UX-051 A): which provider this machine uses, which
network profile, and which credential files an agent may be given. Everything here is
machine-level and lives in the machine's config.toml, never in a repository's.
"""

from __future__ import annotations

import sys
from typing import Any, Optional

from .. import config as app_config
from . import credentials, network_profiles, toolchains
from .workspace import DIRECT, OPENSHELL, SEATBELT, WINDOWS


def _availability(name: str) -> tuple[bool, str, str]:
    """(usable, why not, state) for a provider on this machine. `state` is what the page
    shows next to the provider: "ready", "unavailable", or "needs_download" for OpenShell
    when everything is in place except the base image (OPE-205: the page said "ready" while
    the first session was bound to hang on that download)."""
    if name == DIRECT:
        return True, "", "ready"
    if name == SEATBELT:
        if sys.platform != "darwin":
            return False, "macOS only", "unavailable"
        from .providers import seatbelt

        try:
            seatbelt.preflight()
            return True, "", "ready"
        except seatbelt.SeatbeltUnavailable as exc:
            return False, str(exc), "unavailable"
    if name == OPENSHELL:
        if sys.platform == "win32":  # its Windows driver is a preview; the setup command here sets up the Windows sandbox
            return False, "OpenShell is not available on Windows yet.", "unavailable"
        from .providers.openshell import is_image_problem
        from .selection import openshell_problem

        problem = openshell_problem()
        if problem is None:
            return True, "", "ready"
        return False, problem, ("needs_download" if is_image_problem(problem) else "unavailable")
    if name == WINDOWS:
        if sys.platform != "win32":
            return False, "Windows only", "unavailable"
        from .providers import windows

        try:
            windows.preflight()
            return True, "", "ready"
        except windows.WindowsUnavailable as exc:
            return False, str(exc), "unavailable"
    return False, "unknown", "unavailable"


def native_sandbox() -> str:
    """The operating system's own sandbox provider: the middle option on the page."""
    return WINDOWS if sys.platform == "win32" else SEATBELT


def snapshot(cfg: Optional[app_config.Config] = None) -> dict[str, Any]:
    cfg = cfg or app_config.load_config()
    from .selection import select

    try:
        chosen = select(cfg.sandbox_provider)
        effective, refused = chosen.provider, ""
    except Exception as exc:  # an explicit choice that cannot be used: sessions are refused
        effective, refused = "", str(exc)
    providers = []
    for name in (DIRECT, native_sandbox(), OPENSHELL):
        usable, why, state = _availability(name)
        providers.append({"name": name, "usable": usable, "why": why, "state": state})
    return {
        "platform": sys.platform,
        "provider": cfg.sandbox_provider or "",  # "" = the default rule
        "effective_provider": effective,
        "refused": refused,
        "providers": providers,
        "windows_setup": _windows_setup_info(),
        "network_profile": (cfg.sandbox_network_profile or network_profiles.default_profile()),
        "network_profiles": [
            {"name": name, "hosts": network_profiles.hosts(name)} for name in network_profiles.PROFILES
        ],
        "credentials": _for_display(credentials.entries(cfg.sandbox_credentials)),
        "toolchains": toolchains.for_display(cfg.sandbox_toolchains),
        "config_path": str(app_config.global_config_path()),
    }


def _windows_setup_info() -> Optional[dict[str, Any]]:
    """The Windows setup's state for the page (UX-053): None off Windows."""
    if sys.platform != "win32":
        return None
    from .providers import windows_setup

    return windows_setup.info()


def readiness() -> dict[str, Any]:
    """The checklist Settings ▸ Sandbox shows for OpenShell: the same rows as `openworker
    machine sandbox status`, with keys and whether the app may fix each one itself
    (OPE-207). Costs a few CLI calls; the page asks for it separately from `snapshot`."""
    from . import setup_cmd

    if sys.platform == "win32":
        return {"platform": sys.platform, "supported": False, "steps": [], "all_ok": False}
    rows = [s.as_dict() for s in setup_cmd.steps()]
    return {"platform": sys.platform, "supported": True, "steps": rows, "all_ok": all(r["ok"] for r in rows)}


def _for_display(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A shipped entry's title and wording are left out when the user has not changed
    them, so the app can show them in the user's language; a user's own text is kept.
    `kind` says what the path is on this machine: file, folder, or "" when it is missing."""
    shipped = {e["name"]: e for e in credentials.DEFAULT_ENTRIES}
    out = []
    for row in rows:
        base = shipped.get(row["name"])
        row = dict(row)
        if base is not None:
            for key in ("title", "does"):
                if row.get(key) == base.get(key):
                    row.pop(key, None)
        row["kind"] = credentials.kind_of(str(row.get("path") or ""))
        row["shipped"] = base is not None
        out.append(row)
    return out


def run_windows_setup() -> dict[str, Any]:
    """The setup dialog's "Set up now" (UX-053): run the elevated setup, prove the wall
    from inside a throwaway sandbox, and only then make the Windows sandbox this
    machine's choice. Returns the outcome with the new snapshot."""
    if sys.platform != "win32":
        return {"ok": False, "error": "the Windows sandbox exists only on Windows", **snapshot()}
    from .providers import windows_setup

    ok, said = windows_setup.run_setup()
    if not ok:
        return {"ok": False, "error": said or "setup did not finish", "said": said, **snapshot()}
    checked, detail = _throwaway_check()
    if not checked:
        return {"ok": False, "error": f"setup ran, but a throwaway sandbox failed its check: {detail}", "said": said, **snapshot()}
    app_config.set_global_value("sandbox_provider", WINDOWS)
    return {"ok": True, "said": said, "checked": detail, **snapshot()}


def run_windows_remove() -> dict[str, Any]:
    """"Remove setup": undo what setup made and put the machine back on No sandbox."""
    if sys.platform != "win32":
        return {"ok": False, "error": "the Windows sandbox exists only on Windows", **snapshot()}
    from .providers import windows_setup

    ok, said = windows_setup.run_remove()
    if not ok:
        return {"ok": False, "error": said or "remove did not finish", "said": said, **snapshot()}
    app_config.set_global_value("sandbox_provider", DIRECT)
    return {"ok": True, "said": said, **snapshot()}


def _throwaway_check() -> tuple[bool, str]:
    """Open one sandbox on an empty folder, let the provider prove the wall (the profile
    is not listable from inside; in the closed mode no local port but the proxy's), and
    close it. (ok, what was proved or what failed)."""
    import shutil
    import tempfile

    from .workspace import open_workspace

    folder = tempfile.mkdtemp(prefix="ow-check-")
    try:
        # The strict profile takes the closed account, so the loopback filters are proved
        # too. open_workspace runs the provider's verify(); it raises when the wall is down.
        ws = open_workspace(
            cwd=folder, provider=WINDOWS, session_id="setup-check", agent="setup", credentials=[], network_profile=network_profiles.DEFAULT_PROFILE, toolchains=[]
        )
        ws.close()
        return True, "your profile is not readable from inside; no local port but the proxy's is reachable"
    except Exception as exc:  # noqa: BLE001 - the message is the outcome
        return False, str(exc)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def update(body: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial change and return the new snapshot. Validates before writing."""
    body = body or {}
    if "provider" in body:
        provider = str(body.get("provider") or "").strip().lower()
        if provider and provider not in (DIRECT, SEATBELT, WINDOWS, OPENSHELL):
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
            if raw.get("label") is not None:
                if raw["label"] not in credentials.LABELS:
                    return {"ok": False, "error": f"the label for '{raw['name']}' must be credential or configuration"}
                row["label"] = str(raw["label"])
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
    if "toolchains" in body:
        rows = body.get("toolchains")
        if not isinstance(rows, list):
            return {"ok": False, "error": "toolchains must be a list"}
        cleaned = []
        for raw in rows:
            if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
                return {"ok": False, "error": "every toolchain entry needs a name"}
            path = str(raw.get("path") or "").strip()
            if path and not (path.startswith("~/") or path.startswith("/") or path[1:3] == ":\\" or path[1:3] == ":/"):
                return {"ok": False, "error": f"the path for '{raw['name']}' must start with ~/ or be absolute"}
            row = {"name": str(raw["name"]).strip(), "enabled": bool(raw.get("enabled"))}
            for key in ("title", "path"):
                if raw.get(key) is not None:
                    row[key] = str(raw[key])
            cleaned.append(row)
        shipped = {e["name"]: e for e in toolchains.defaults()}
        slim = []
        for row in cleaned:
            base = shipped.get(row["name"])
            if base is None:
                slim.append(row)
                continue
            diff = {k: v for k, v in row.items() if k == "name" or base.get(k) != v}
            if len(diff) > 1:
                slim.append(diff)
        app_config.set_global_tables("sandbox_toolchains", slim)
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
