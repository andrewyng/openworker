"""Control-plane store interfaces: the seam between one desktop and a fleet.

The acceptor speaks to two stores and never cares what backs them
(remote-home-design.md §Planes — seams, not services):

  - `Registry`: durable machine state — enrollments, pinned keys, deploy
    ledger, session snapshots. Desktop default: `MachinesRegistry` (SQLite).
    Cloud: the same interface over Postgres.
  - `Ephemera`: shared transient state — enrollment tokens and the clone-trap
    lease. Desktop default: `InMemoryEphemera` (a restart disarms, which is
    the safe default). Cloud: Redis, where atomic consume/acquire make the
    single-use token and one-connection-per-identity guarantees hold across a
    gateway fleet.

What deliberately stays OUT of `Ephemera`: the live socket map. An RpcClient
is a process-local object — in a fleet, presence answers "which node holds the
socket" and routing replays to that node; the map itself never leaves the
gateway. So `_live` stays in the acceptor.
"""

from __future__ import annotations

import time
from typing import Any, Optional, Protocol


class Registry(Protocol):
    """Durable machine state. `MachinesRegistry` is the SQLite implementation.

    Tenancy: every machine belongs to an org (`org_id`); the desktop is the
    degenerate single-tenant case where everything lives in org "". Machine
    names are unique PER ORG. Row-level scoping is enforced by the acceptor's
    endpoints (fetch → compare org) — `by_id`/`by_pubkey` are deliberately
    unscoped so the enrollment handshake can find any pinned identity."""

    def enroll(
        self,
        name: str,
        pubkey: str,
        app_version: str = "",
        seal_pubkey: str = "",
        enrolled_by: str = "",
        org_id: str = "",
        provenance: str = "",
        provenance_ref: str = "",
    ) -> dict[str, Any]: ...
    def by_pubkey(self, pubkey: str) -> Optional[dict[str, Any]]: ...
    def by_id(self, machine_id: str) -> Optional[dict[str, Any]]: ...
    def list(self, org_id: Optional[str] = None) -> list[dict[str, Any]]: ...
    def touch(self, machine_id: str, app_version: Optional[str] = None) -> None: ...
    def rename(self, machine_id: str, name: str) -> None: ...
    def remove(self, machine_id: str) -> None: ...
    def set_seal_pubkey(self, machine_id: str, seal_pubkey: str) -> None: ...
    def record_deploy(self, machine_id: str, profile: str, value_hash: str) -> None: ...
    def remove_deploy(self, machine_id: str, profile: str) -> None: ...
    def deploys(self, machine_id: str) -> list[dict[str, Any]]: ...
    def save_sessions_snapshot(self, machine_id: str, payload: str) -> None: ...
    def sessions_snapshot(self, machine_id: str) -> Optional[str]: ...
    def save_transcript(self, machine_id: str, session_id: str, payload: str) -> None: ...
    def transcript(self, machine_id: str, session_id: str) -> Optional[str]: ...


class Ephemera(Protocol):
    """Shared transient state. The operations that gate security guarantees
    (`consume_token`, `acquire_lease`) must be atomic check-and-take in any
    implementation — that is the whole reason this interface exists."""

    def add_token(
        self,
        token: str,
        expires_at: float,
        bound_fingerprint: str = "",
        org_id: str = "",
        actor: str = "",
        provenance: Optional[dict[str, str]] = None,
    ) -> None: ...
    def consume_token(self, token: str) -> Optional[dict[str, Any]]:
        """Take the token if it exists and is unexpired; single-use — two
        concurrent consumers must never both succeed. Returns None if invalid,
        else {"fingerprint": bound fingerprint or "", "org_id": the org the
        enrollment lands in, "actor": who armed/approved it, "provenance":
        {"kind", "ref"} or {}} — the token is how a machine inherits its
        tenant, its owner AND its provenance (a managed sandbox's token names
        the sandbox it was minted for; spec §Fly sandboxes)."""
        ...

    def clear_tokens(self) -> None: ...
    def has_tokens(self) -> bool: ...

    def acquire_lease(self, pubkey: str) -> bool:
        """Claim the one-connection-per-identity lease (clone trap). False if
        already held anywhere in the deployment."""
        ...

    def release_lease(self, pubkey: str) -> None: ...

    def refresh_lease(self, pubkey: str) -> None:
        """Heartbeat: TTL-leased backends (Redis) extend the lease; the
        acceptor calls this periodically for every live connection. In-memory
        leases have no TTL — a process death releases them with the process."""
        ...

    # Device-authorization grants (`openworker join <address>`): a box's pending
    # request to be approved. Keyed by device_code (secret, box-held); looked
    # up by user_code (short, human-typed) on the approval side.
    def put_grant(self, grant: dict[str, Any]) -> None: ...
    def grant_by_device_code(self, device_code: str) -> Optional[dict[str, Any]]: ...
    def grant_by_user_code(self, user_code: str) -> Optional[dict[str, Any]]: ...
    def update_grant(self, device_code: str, **fields: Any) -> None: ...
    def remove_grant(self, device_code: str) -> None: ...
    def list_grants(self) -> list[dict[str, Any]]: ...


class InMemoryEphemera:
    """Desktop default: one process, so plain dicts under the event loop's
    implicit serialization are already atomic."""

    def __init__(self) -> None:
        # token -> (expiry, bound fingerprint, org, actor, provenance)
        self._tokens: dict[str, tuple[float, str, str, str, dict[str, str]]] = {}
        self._leases: set[str] = set()
        self._grants: dict[str, dict[str, Any]] = {}  # device_code -> grant

    def add_token(
        self,
        token: str,
        expires_at: float,
        bound_fingerprint: str = "",
        org_id: str = "",
        actor: str = "",
        provenance: Optional[dict[str, str]] = None,
    ) -> None:
        self._prune()
        self._tokens[token] = (expires_at, bound_fingerprint, org_id, actor, dict(provenance or {}))

    def consume_token(self, token: str) -> Optional[dict[str, Any]]:
        self._prune()
        entry = self._tokens.pop(token, None)
        if entry is None or entry[0] <= time.time():
            return None
        return {
            "fingerprint": entry[1],
            "org_id": entry[2],
            "actor": entry[3],
            "provenance": dict(entry[4]),
        }

    def clear_tokens(self) -> None:
        self._tokens.clear()

    def has_tokens(self) -> bool:
        self._prune()
        return bool(self._tokens)

    def acquire_lease(self, pubkey: str) -> bool:
        if pubkey in self._leases:
            return False
        self._leases.add(pubkey)
        return True

    def release_lease(self, pubkey: str) -> None:
        self._leases.discard(pubkey)

    def refresh_lease(self, pubkey: str) -> None:
        pass  # no TTL to extend

    def put_grant(self, grant: dict[str, Any]) -> None:
        self._prune()
        self._grants[str(grant["device_code"])] = dict(grant)

    def grant_by_device_code(self, device_code: str) -> Optional[dict[str, Any]]:
        self._prune()
        grant = self._grants.get(device_code)
        return dict(grant) if grant else None

    def grant_by_user_code(self, user_code: str) -> Optional[dict[str, Any]]:
        self._prune()
        for grant in self._grants.values():
            if grant.get("user_code") == user_code:
                return dict(grant)
        return None

    def update_grant(self, device_code: str, **fields: Any) -> None:
        grant = self._grants.get(device_code)
        if grant is not None:
            grant.update(fields)

    def remove_grant(self, device_code: str) -> None:
        self._grants.pop(device_code, None)

    def list_grants(self) -> list[dict[str, Any]]:
        self._prune()
        return [dict(g) for g in self._grants.values()]

    def _prune(self) -> None:
        now = time.time()
        self._tokens = {t: e for t, e in self._tokens.items() if e[0] > now}
        self._grants = {
            c: g for c, g in self._grants.items() if float(g.get("expires_at", 0)) > now
        }
