"""Persistent hostname permissions for agent-controlled Browser Use.

This policy is deliberately separate from the network destination policy:

* :mod:`destination` decides whether an address is safe to reach at all.
* this module decides whether the agent may visit a public hostname without
  asking the user.

The hostname permission applies to top-level agent navigation.  It does not
attempt to allow-list subresources because ordinary sites depend on fonts,
images, APIs, and CDNs on other origins.  Those requests remain constrained by
the fail-closed destination proxy.  Callers must run this policy again for each
explicit top-level URL; a runtime navigation guard may additionally apply it to
redirects, popups, and link/form navigations.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .destination import (
    DestinationPolicy,
    DestinationPolicyError,
    canonical_origin,
    is_explicit_local_origin,
)


class SiteAccessMode(str, Enum):
    ASK = "ask"
    AUTO = "auto"
    ALLOW = "allow"


@dataclass(frozen=True)
class BrowserSiteAccessDecision:
    allowed: bool
    needs_user: bool
    host: str
    origin: str
    reason: str
    blocked: bool = False
    is_public: bool = True


_DEFAULTS: dict[str, Any] = {
    "site_access_mode": SiteAccessMode.ASK.value,
    "allowed_hosts": [],
    "blocked_hosts": [],
    "download_directory": str(Path.home() / "Downloads"),
    "ask_download_location": False,
    "developer_mode": False,
}


class BrowserSitePermissionStore:
    """Thread-safe local settings and persistent hostname decisions."""

    def __init__(
        self,
        path: str | Path,
        *,
        destination_policy: Optional[DestinationPolicy] = None,
    ) -> None:
        self.path = Path(path).expanduser()
        self._destination_policy = destination_policy or DestinationPolicy()
        self._lock = threading.RLock()
        self._settings = self._load()

    def settings(self) -> dict[str, Any]:
        with self._lock:
            return {
                "site_access_mode": self._settings["site_access_mode"],
                "allowed_hosts": list(self._settings["allowed_hosts"]),
                "blocked_hosts": list(self._settings["blocked_hosts"]),
                "download_directory": self._settings["download_directory"],
                "ask_download_location": bool(
                    self._settings["ask_download_location"]
                ),
                "developer_mode": bool(self._settings["developer_mode"]),
            }

    def update(
        self,
        *,
        site_access_mode: Optional[str] = None,
        allowed_hosts: Optional[Iterable[str]] = None,
        blocked_hosts: Optional[Iterable[str]] = None,
        download_directory: Optional[str] = None,
        ask_download_location: Optional[bool] = None,
        developer_mode: Optional[bool] = None,
    ) -> dict[str, Any]:
        with self._lock:
            updated = dict(self._settings)
            if site_access_mode is not None:
                try:
                    updated["site_access_mode"] = SiteAccessMode(
                        str(site_access_mode).strip().lower()
                    ).value
                except ValueError as exc:
                    raise ValueError(
                        "site_access_mode must be ask, auto, or allow"
                    ) from exc
            if allowed_hosts is not None:
                updated["allowed_hosts"] = _normalized_hosts(allowed_hosts)
            if blocked_hosts is not None:
                updated["blocked_hosts"] = _normalized_hosts(blocked_hosts)
            # A deny always wins.  Keeping the lists disjoint also prevents the
            # settings surface from presenting an ambiguous state.
            denied = set(updated["blocked_hosts"])
            updated["allowed_hosts"] = [
                host for host in updated["allowed_hosts"] if host not in denied
            ]
            if download_directory is not None:
                value = str(download_directory).strip()
                if "\x00" in value:
                    raise ValueError("download_directory is invalid")
                updated["download_directory"] = (
                    str(Path(value).expanduser()) if value else ""
                )
            if ask_download_location is not None:
                updated["ask_download_location"] = bool(ask_download_location)
            if developer_mode is not None:
                updated["developer_mode"] = bool(developer_mode)
            self._settings = updated
            self._save()
            return self.settings()

    def evaluate_url(self, url: str) -> BrowserSiteAccessDecision:
        """Evaluate one explicit agent top-level navigation.

        Public destinations are resolved and validated before an ``auto`` or
        ``allow`` decision.  Private/local destinations never inherit those
        permissive modes: they require an exact saved hostname decision.
        """

        origin = canonical_origin(url)
        host = origin.host
        origin_value = origin.value
        with self._lock:
            mode = SiteAccessMode(self._settings["site_access_mode"])
            allowed = set(self._settings["allowed_hosts"])
            blocked = set(self._settings["blocked_hosts"])

        if host in blocked:
            return BrowserSiteAccessDecision(
                False,
                False,
                host,
                origin_value,
                f"Browser access to {host} is blocked in settings",
                blocked=True,
            )

        explicit_local = is_explicit_local_origin(origin_value)
        try:
            policy = (
                DestinationPolicy(local_origin_grants=[origin_value])
                if explicit_local
                else self._destination_policy
            )
            policy.evaluate(url)
        except DestinationPolicyError as exc:
            return BrowserSiteAccessDecision(
                False,
                False,
                host,
                origin_value,
                f"Browser destination was rejected ({exc.code})",
                blocked=True,
                is_public=not explicit_local,
            )

        if host in allowed:
            return BrowserSiteAccessDecision(
                True,
                False,
                host,
                origin_value,
                f"Browser access to {host} is allowed in settings",
                is_public=not explicit_local,
            )

        if explicit_local:
            return BrowserSiteAccessDecision(
                False,
                True,
                host,
                origin_value,
                f"Allow Browser Use to access local site {host}?",
                is_public=False,
            )

        if mode in {SiteAccessMode.AUTO, SiteAccessMode.ALLOW}:
            return BrowserSiteAccessDecision(
                True,
                False,
                host,
                origin_value,
                (
                    f"Public site {host} is auto-approved"
                    if mode is SiteAccessMode.AUTO
                    else f"All public sites are allowed"
                ),
            )

        return BrowserSiteAccessDecision(
            False,
            True,
            host,
            origin_value,
            f"Allow Browser Use to access {host}?",
        )

    def allow_host(self, value: str) -> dict[str, Any]:
        host = normalize_host(value)
        with self._lock:
            allowed = set(self._settings["allowed_hosts"])
            blocked = set(self._settings["blocked_hosts"])
            if host in blocked:
                raise ValueError(f"{host} is blocked in Browser settings")
            allowed.add(host)
            self._settings["allowed_hosts"] = sorted(allowed)
            self._save()
            return self.settings()

    def block_host(self, value: str) -> dict[str, Any]:
        host = normalize_host(value)
        with self._lock:
            blocked = set(self._settings["blocked_hosts"])
            blocked.add(host)
            self._settings["blocked_hosts"] = sorted(blocked)
            self._settings["allowed_hosts"] = [
                item
                for item in self._settings["allowed_hosts"]
                if item != host
            ]
            self._save()
            return self.settings()

    def navigation_allowed(self, url: str) -> bool:
        """Non-interactive guard suitable for redirects/popups.

        ``ask`` decisions fail closed because a network callback cannot safely
        suspend a tool call to show another approval card.
        """

        try:
            return self.evaluate_url(url).allowed
        except (DestinationPolicyError, ValueError):
            return False

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = {}
        if not isinstance(raw, Mapping):
            raw = {}
        try:
            mode = SiteAccessMode(
                str(raw.get("site_access_mode") or _DEFAULTS["site_access_mode"])
            ).value
        except ValueError:
            mode = SiteAccessMode.ASK.value
        try:
            allowed = _normalized_hosts(raw.get("allowed_hosts") or ())
            blocked = _normalized_hosts(raw.get("blocked_hosts") or ())
        except ValueError:
            allowed, blocked = [], []
        denied = set(blocked)
        return {
            "site_access_mode": mode,
            "allowed_hosts": [host for host in allowed if host not in denied],
            "blocked_hosts": blocked,
            "download_directory": str(
                raw.get("download_directory", _DEFAULTS["download_directory"])
            ),
            "ask_download_location": bool(
                raw.get(
                    "ask_download_location",
                    _DEFAULTS["ask_download_location"],
                )
            ),
            "developer_mode": bool(
                raw.get("developer_mode", _DEFAULTS["developer_mode"])
            ),
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(self._settings, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(self.path)


def normalize_host(value: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("Browser hostname cannot be empty")
    if "://" in text:
        return canonical_origin(text).host
    if any(char in text for char in ("/", "\\", "?", "#", "@", "*")):
        raise ValueError(f"Invalid Browser hostname: {text}")
    # canonical_origin supplies lower-casing, IDNA normalization, literal-IP
    # normalization, and control-character rejection.
    return canonical_origin(f"https://{text}").host


def _normalized_hosts(values: Iterable[str]) -> list[str]:
    if isinstance(values, (str, bytes, Mapping)):
        raise ValueError("Browser host lists must be arrays")
    hosts = {normalize_host(value) for value in values}
    if len(hosts) > 500:
        raise ValueError("Browser host list is too large")
    return sorted(hosts)
