"""Multiple Ollama / local-inference endpoints under the single `ollama` provider.

The ProviderRouter still caches one `ollama` client; the selected endpoint's URL is mirrored
into the legacy `base_url` field so `_build_ollama`, verify, and older installs keep working.
Models and liveness always probe the *selected* enabled endpoint — never a mix of hosts.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional
from urllib.parse import urlparse

from .registry import DEFAULT_OLLAMA_URL

_ENDPOINT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def new_endpoint_id() -> str:
    return "ep_" + uuid.uuid4().hex[:12]


def normalize_endpoint_url(url: str) -> str:
    """Strip, drop trailing slash, and peel a trailing `/v1` so duplicates compare equal."""
    base = (url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")
    return base


def validate_endpoint_url(url: str) -> Optional[str]:
    """Return an error string, or None if the URL is acceptable."""
    raw = (url or "").strip()
    if not raw:
        return "Endpoint URL is required."
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return "URL must start with http:// or https://."
    if not parsed.netloc:
        return "URL must include a host."
    # base_url is echoed to the Settings UI as a non-secret value — reject embedded
    # userinfo so credentials never appear in the provider form or logs via values.
    if parsed.username is not None or parsed.password is not None:
        return "URL must not include a username or password."
    return None


def validate_label(label: str) -> Optional[str]:
    if not (label or "").strip():
        return "Nickname is required."
    if len(label.strip()) > 80:
        return "Nickname is too long."
    return None


def _legacy_endpoint(base_url: str) -> dict[str, Any]:
    url = normalize_endpoint_url(base_url) or DEFAULT_OLLAMA_URL
    return {
        "id": "ep_default",
        "label": "Default",
        "base_url": url,
        "enabled": True,
    }


def migrate_profile(profile: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Ensure `endpoints` + `selected_endpoint_id` exist; mirror selected URL into `base_url`.

    Idempotent. A legacy `{base_url}`-only profile (no `endpoints` key) becomes one "Default"
    endpoint. An explicit empty `endpoints` list is preserved (user deleted everything).
    """
    profile = dict(profile or {})
    had_endpoints_key = isinstance(profile.get("endpoints"), list)
    endpoints = profile.get("endpoints")
    if not isinstance(endpoints, list):
        endpoints = []

    cleaned: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for raw in endpoints:
        if not isinstance(raw, dict):
            continue
        eid = str(raw.get("id") or "").strip() or new_endpoint_id()
        if not _ENDPOINT_ID_RE.match(eid):
            eid = new_endpoint_id()
        label = str(raw.get("label") or "").strip() or "Endpoint"
        url = normalize_endpoint_url(str(raw.get("base_url") or ""))
        if not url:
            continue
        key = url.lower()
        if key in seen_urls:
            continue
        seen_urls.add(key)
        cleaned.append(
            {
                "id": eid,
                "label": label[:80],
                "base_url": url,
                "enabled": bool(raw.get("enabled", True)),
            }
        )

    legacy = normalize_endpoint_url(str(profile.get("base_url") or ""))
    if not cleaned and legacy and not had_endpoints_key:
        cleaned = [_legacy_endpoint(legacy)]

    selected = str(profile.get("selected_endpoint_id") or "").strip()
    by_id = {e["id"]: e for e in cleaned}
    # Never keep a disabled endpoint as selected — fall back to another enabled one,
    # or clear selection entirely (do not auto-pick a disabled row).
    if selected in by_id and not by_id[selected].get("enabled"):
        selected = ""
    if selected not in by_id:
        enabled = [e for e in cleaned if e.get("enabled")]
        selected = enabled[0]["id"] if enabled else ""

    if selected and selected in by_id:
        profile["base_url"] = by_id[selected]["base_url"]
        profile["selected_endpoint_id"] = selected
    else:
        profile.pop("base_url", None)
        profile.pop("selected_endpoint_id", None)

    profile["endpoints"] = cleaned
    return profile


def list_endpoints(profile: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(migrate_profile(profile).get("endpoints") or [])


def selected_endpoint(profile: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    migrated = migrate_profile(profile)
    sid = migrated.get("selected_endpoint_id")
    for ep in migrated.get("endpoints") or []:
        if ep.get("id") == sid:
            return ep
    return None


def selected_base_url(profile: Optional[dict[str, Any]]) -> str:
    """URL used for client build / probes.

    Returns the selected *enabled* endpoint's URL. If the profile has an explicit
    endpoints list but nothing enabled/selected, returns "" (callers must not fall
    through to probing localhost). Legacy `{base_url}`-only profiles still resolve
    to that URL / the default.
    """
    migrated = migrate_profile(profile)
    ep = None
    sid = migrated.get("selected_endpoint_id")
    for row in migrated.get("endpoints") or []:
        if row.get("id") == sid:
            ep = row
            break
    if ep and ep.get("enabled", True) and ep.get("base_url"):
        return normalize_endpoint_url(ep["base_url"]) or DEFAULT_OLLAMA_URL
    if migrated.get("endpoints"):
        # Multi-endpoint config with no usable selection — do not invent localhost.
        return ""
    legacy = normalize_endpoint_url(str((profile or {}).get("base_url") or ""))
    return legacy or DEFAULT_OLLAMA_URL


def public_endpoints(profile: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Shape returned on the Ollama provider row for the Settings UI."""
    migrated = migrate_profile(profile)
    return {
        "endpoints": migrated.get("endpoints") or [],
        "selected_endpoint_id": migrated.get("selected_endpoint_id") or None,
    }


def add_endpoint(
    profile: Optional[dict[str, Any]],
    *,
    label: str,
    base_url: str,
    enabled: bool = True,
    select: bool = True,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """Returns (updated_profile, error_dict?)."""
    err = validate_label(label) or validate_endpoint_url(base_url)
    if err:
        return {}, {"ok": False, "error": err}
    migrated = migrate_profile(profile)
    url = normalize_endpoint_url(base_url)
    for ep in migrated["endpoints"]:
        if ep["base_url"].lower() == url.lower():
            return {}, {"ok": False, "error": "An endpoint with this URL already exists."}
    eid = new_endpoint_id()
    migrated["endpoints"].append(
        {
            "id": eid,
            "label": label.strip()[:80],
            "base_url": url,
            "enabled": bool(enabled),
        }
    )
    if select and enabled:
        migrated["selected_endpoint_id"] = eid
    return migrate_profile(migrated), None


def update_endpoint(
    profile: Optional[dict[str, Any]],
    endpoint_id: str,
    *,
    label: Optional[str] = None,
    base_url: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    migrated = migrate_profile(profile)
    eid = (endpoint_id or "").strip()
    target = next((e for e in migrated["endpoints"] if e["id"] == eid), None)
    if target is None:
        return {}, {"ok": False, "error": "Endpoint not found."}

    if label is not None:
        err = validate_label(label)
        if err:
            return {}, {"ok": False, "error": err}
        target["label"] = label.strip()[:80]
    if base_url is not None:
        err = validate_endpoint_url(base_url)
        if err:
            return {}, {"ok": False, "error": err}
        url = normalize_endpoint_url(base_url)
        for ep in migrated["endpoints"]:
            if ep["id"] != eid and ep["base_url"].lower() == url.lower():
                return {}, {"ok": False, "error": "An endpoint with this URL already exists."}
        target["base_url"] = url
    if enabled is not None:
        target["enabled"] = bool(enabled)
        if not target["enabled"] and migrated.get("selected_endpoint_id") == eid:
            # Drop selection onto another enabled endpoint (or clear).
            alt = next(
                (e for e in migrated["endpoints"] if e["id"] != eid and e.get("enabled")),
                None,
            )
            migrated["selected_endpoint_id"] = alt["id"] if alt else ""

    return migrate_profile(migrated), None


def delete_endpoint(
    profile: Optional[dict[str, Any]], endpoint_id: str
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    migrated = migrate_profile(profile)
    eid = (endpoint_id or "").strip()
    before = len(migrated["endpoints"])
    migrated["endpoints"] = [e for e in migrated["endpoints"] if e["id"] != eid]
    if len(migrated["endpoints"]) == before:
        return {}, {"ok": False, "error": "Endpoint not found."}
    if migrated.get("selected_endpoint_id") == eid:
        enabled = [e for e in migrated["endpoints"] if e.get("enabled")]
        migrated["selected_endpoint_id"] = enabled[0]["id"] if enabled else ""
    return migrate_profile(migrated), None


def select_endpoint(
    profile: Optional[dict[str, Any]], endpoint_id: str
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    migrated = migrate_profile(profile)
    eid = (endpoint_id or "").strip()
    target = next((e for e in migrated["endpoints"] if e["id"] == eid), None)
    if target is None:
        return {}, {"ok": False, "error": "Endpoint not found."}
    if not target.get("enabled"):
        return {}, {"ok": False, "error": "Enable the endpoint before selecting it."}
    migrated["selected_endpoint_id"] = eid
    return migrate_profile(migrated), None


def upsert_from_base_url(
    profile: Optional[dict[str, Any]], base_url: str
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """Legacy `set_provider({base_url})` / blur-save: update selected endpoint or create one."""
    err = validate_endpoint_url(base_url)
    if err:
        return {}, {"ok": False, "error": err}
    migrated = migrate_profile(profile)
    url = normalize_endpoint_url(base_url)
    sid = migrated.get("selected_endpoint_id")
    selected = next((e for e in migrated["endpoints"] if e["id"] == sid), None)
    if selected is None and migrated["endpoints"]:
        selected = migrated["endpoints"][0]
    if selected:
        # URL collision with a *different* endpoint → reject.
        for ep in migrated["endpoints"]:
            if ep["id"] != selected["id"] and ep["base_url"].lower() == url.lower():
                return {}, {"ok": False, "error": "An endpoint with this URL already exists."}
        selected["base_url"] = url
        migrated["selected_endpoint_id"] = selected["id"]
        return migrate_profile(migrated), None
    # No endpoints yet — create Default (or a short label from host).
    label = "Default"
    host = urlparse(url).hostname or ""
    if host and host not in ("localhost", "127.0.0.1"):
        label = host
    return add_endpoint(migrated, label=label, base_url=url, enabled=True, select=True)
