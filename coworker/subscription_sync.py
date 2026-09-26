"""Register channel subscriptions at the cloud before writing them locally.

Connectors-across-machines spec §3.3: for managed inbound connectors (Slack in
relay mode, GitHub) the broker's subscription row is the truth — ONE session
across all of the user's machines answers a source. The local
``SubscriptionStore`` is the on-box mirror. So a subscribe goes to the broker
first; only an accepted claim is written locally, and a clash comes back as
``held`` so the UI can offer the move.

Two doors, picked by what this process holds:
- a joined box: the connector profile carries the machine credential minted at
  delegation → ``POST /v1/machine/subscriptions`` (the broker takes the
  machine id from the credential's holder);
- the signed-in desktop: the cloud session token → ``POST /v1/subscriptions``
  as machine ``desktop``.
Neither (Socket-Mode Slack, Telegram, signed out) → nothing to register; the
subscription is local-only, exactly as before.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from .cloud import fresh_access_token
from .config import Config
from .secrets import SecretStore

logger = logging.getLogger("coworker.subscriptions")

MANAGED_INBOUND = ("slack", "github")
_TIMEOUT = 15


def _door(secrets: SecretStore, config: Config, connector: str) -> Optional[dict[str, Any]]:
    """How to reach the broker for this connector, or None (local-only)."""
    if connector not in MANAGED_INBOUND:
        return None
    profile = secrets.get(f"{connector}:default") or {}
    if profile.get("mode") != "relay":
        return None
    base = (config.cloud_base_url or "").rstrip("/")
    if not base:
        return None
    if (
        profile.get("machine_credential")
        and profile.get("broker_user_id")
        and profile.get("connection_id")
    ):
        return {
            "kind": "machine",
            "url": base + "/v1/machine/subscriptions",
            "headers": {"Authorization": f"Bearer {profile['machine_credential']}"},
            "ids": {
                "user_id": str(profile["broker_user_id"]),
                "connection_id": str(profile["connection_id"]),
            },
        }
    token = fresh_access_token(secrets, config)
    if token:
        return {
            "kind": "user",
            "url": base + "/v1/subscriptions",
            "headers": {"Authorization": f"Bearer {token}"},
            "ids": {},
        }
    return None


def _connector_of(source: str) -> str:
    return source.split(":", 1)[0] if ":" in source else ""


def register(
    secrets: SecretStore,
    config: Config,
    *,
    source: str,
    session_id: str,
    title: str = "",
    move: bool = False,
) -> dict[str, Any]:
    """Claim `source` for `session_id` at the broker.

    Returns ``{"ok": True, "registered": bool}`` (False = local-only, nothing
    to register), ``{"ok": False, "error": "held", "held_by", "also",
    "move_allowed"}`` on a clash, or ``{"ok": False, "error": <text>}`` when the
    broker refused or could not be reached (the local write must NOT happen)."""
    door = _door(secrets, config, _connector_of(source))
    if door is None:
        return {"ok": True, "registered": False}
    body = {
        **door["ids"],
        "source": source,
        "session_id": session_id,
        "title": title[:200],
        "move": bool(move),
    }
    try:
        resp = httpx.post(door["url"], json=body, headers=door["headers"], timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.warning("subscription register unreachable: %s", type(exc).__name__)
        return {"ok": False, "error": "The cloud could not be reached — try again in a moment."}
    if resp.status_code == 404:
        # A broker without the subscriptions routes yet (or a connection it no
        # longer knows): behave as before the cloud table existed — local-only.
        return {"ok": True, "registered": False}
    if resp.status_code == 409:
        detail = (resp.json() or {}).get("detail") or {}
        return {
            "ok": False,
            "error": "held",
            "held_by": detail.get("held_by") or {},
            "also": detail.get("also") or [],
            "move_allowed": bool(detail.get("move_allowed", True)),
        }
    if resp.status_code != 200:
        try:
            detail = (resp.json() or {}).get("detail")
        except ValueError:
            detail = ""
        return {"ok": False, "error": str(detail or f"cloud refused (http {resp.status_code})")}
    data = resp.json() or {}
    return {
        "ok": True,
        "registered": True,
        "moved_from": data.get("moved_from") or [],
    }


def _post(door: dict[str, Any], path: str, body: dict[str, Any]) -> None:
    try:
        httpx.post(
            door["url"] + path, json={**door["ids"], **body}, headers=door["headers"], timeout=_TIMEOUT
        )
    except httpx.HTTPError as exc:  # best effort: the next reconcile catches up
        logger.info("subscription %s not reported: %s", path or "remove", type(exc).__name__)


def remove(secrets: SecretStore, config: Config, *, source: str, session_id: str) -> None:
    """Best-effort release of a row this session held (a stale box cannot
    release a session that took over — the broker checks)."""
    door = _door(secrets, config, _connector_of(source))
    if door is not None:
        _post(door, "/remove", {"source": source, "session_id": session_id})


def report_orphan(secrets: SecretStore, config: Config, *, source: str) -> None:
    """The broker named a session this box no longer has: the row stops
    capturing events and the glance page shows it as gone."""
    door = _door(secrets, config, _connector_of(source))
    if door is not None and door["kind"] == "machine":
        _post(door, "/orphan", {"source": source})
    elif door is not None:
        _post(door, "/remove", {"source": source})


# --- mirror refresh (spec §3.3 follow-up, UX-049 4b) ---------------------------


def _get(door: dict[str, Any], path: str) -> Optional[dict[str, Any]]:
    try:
        resp = httpx.get(door["url"] + path, params=door["ids"], headers=door["headers"], timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.info("mirror refresh unreachable: %s", type(exc).__name__)
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json() or {}
    except ValueError:
        return None


def pull(secrets: SecretStore, config: Config) -> dict[str, Any]:
    """What the broker holds for this box: its subscription rows and the
    owner's People lists, per managed inbound connector. Only a joined box
    (machine credential) pulls; the desktop's truth is its own store plus
    the user-authed views. Returns
    ``{"subscriptions": [rows for THIS machine], "elsewhere": [rows on other
    machines], "people": {connector: [rows]}}`` — empty when nothing to pull."""
    out: dict[str, Any] = {"subscriptions": [], "elsewhere": [], "people": {}}
    for connector in MANAGED_INBOUND:
        door = _door(secrets, config, connector)
        if door is None or door["kind"] != "machine":
            continue
        subs = _get(door, "")
        if subs is not None:
            mine = str(subs.get("machine_id") or "")
            for row in subs.get("subscriptions") or []:
                if row.get("connector") != connector:
                    continue
                (out["subscriptions"] if mine and row.get("machine_id") == mine else out["elsewhere"]).append(row)
        ppl = _get({**door, "url": door["url"].replace("/subscriptions", "/people")}, "")
        if ppl is not None:
            out["people"][connector] = [r for r in ppl.get("people") or [] if r.get("connector") == connector]
    return out
