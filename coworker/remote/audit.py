"""OCSF-shaped audit trail for acceptor actions — the management plane, v0.

One JSONL file, one event per security-relevant acceptor action: machine
enrolled/removed/renamed, secrets deployed/revoked, device grants
approved/denied. Events follow OCSF Entity Management (class_uid 3004,
category 3: Identity & Access Management) so a SIEM ingests them without a
custom parser (remote-home-design.md §Planes — management plane starts as
events, not services). Values never appear here: secrets are named by profile,
machines by id/name/fingerprint.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

_OCSF_VERSION = "1.1.0"
_CLASS_ENTITY_MANAGEMENT = 3004
_CATEGORY_IAM = 3

# OCSF Entity Management activity ids.
_ACTIVITIES = {"create": 1, "read": 2, "update": 3, "delete": 4}


class AuditLog:
    """Append-only JSONL. Write failures never break the action being audited
    — but they are counted, so tests (and a future health surface) can see."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.dropped = 0

    def emit(
        self,
        activity: str,
        *,
        entity_type: str,
        entity_name: str,
        entity_uid: str = "",
        detail: Optional[dict[str, Any]] = None,
        success: bool = True,
        actor: str = "",
        org_id: str = "",
    ) -> None:
        """`actor` is the acting identity (account sub or email) and `org_id`
        the tenant it acted in — both empty on the desktop, REQUIRED in any
        multi-tenant deployment (SOC2: every control-plane event has a who)."""
        activity_id = _ACTIVITIES.get(activity, 0)
        event: dict[str, Any] = {
            "class_uid": _CLASS_ENTITY_MANAGEMENT,
            "category_uid": _CATEGORY_IAM,
            "activity_id": activity_id,
            "activity_name": activity,
            "type_uid": _CLASS_ENTITY_MANAGEMENT * 100 + activity_id,
            "time": int(time.time() * 1000),
            "severity_id": 1,  # informational
            "status_id": 1 if success else 2,
            "metadata": {
                "version": _OCSF_VERSION,
                "product": {"name": "OpenWorker", "vendor_name": "OpenWorker"},
            },
            "entity": {
                "type": entity_type,
                "name": entity_name,
                **({"uid": entity_uid} if entity_uid else {}),
            },
        }
        if actor or org_id:
            event["actor"] = {"user": {"name": actor}}
            if org_id:
                event["actor"]["user"]["org"] = {"uid": org_id}
        if detail:
            event["unmapped"] = detail
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, separators=(",", ":")) + "\n")
        except OSError:
            self.dropped += 1


class NullAuditLog:
    """For embedders that opt out; both real apps pass a file-backed log."""

    dropped = 0

    def emit(self, *args: Any, **kwargs: Any) -> None:
        pass
