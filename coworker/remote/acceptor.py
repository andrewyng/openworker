"""Join-acceptor: the controller half of remote homes, as one mountable module.

Deliberately self-contained (remote-home-design.md, owner ruling 2026-08-24):
the desktop sidecar mounts this next to its own routes; the future cloud
service imports THIS module and adds multi-tenant auth — never an extraction
project. It owns:

  - arming + single-use enrollment tokens (10-minute TTL, in-memory: a
    restart disarms, which is the safe default),
  - the machines registry (durable) and the live socket map (presence),
  - the `/ws/machine` endpoint (hello → challenge → auth → welcome),
  - the proxy prefix `/v1/machines/{mid}/p/<path>` that makes a remote home
    literally "a different base URL" for the GUI.

Auth model: the REST surface rides the sidecar-token middleware like every
other /v1 route (GUI-only). The machine WebSocket is the one surface remote
boxes reach, and it authenticates with the enrollment token (first contact)
or an ed25519 challenge-response (every reconnect) — never the sidecar token,
which must not leave this machine.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import secrets as pysecrets
import time
from typing import Any, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from . import channel as ch
from .audit import AuditLog, NullAuditLog
from .identity import seal_b64, verify_b64, verify_seal_attestation
from .provision import LocalProvisioner, Provisioner
from .stores import Ephemera, InMemoryEphemera, Registry

TOKEN_TTL_SECONDS = 600.0
_CHALLENGE_BYTES = 32

# Device-authorization flow (`openworker join`): the box asks, the user
# approves in the controller UI, approval mints the one join token.
DEVICE_TTL_SECONDS = 900.0
DEVICE_POLL_SECONDS = 5
# TTL-leased Ephemera backends (Redis) get a heartbeat from each live machine
# connection, so a crashed acceptor node frees its identities by expiry.
LEASE_REFRESH_SECONDS = 30.0
# No lookalike characters — the user reads this code off a terminal.
_USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

# The one proxied path whose successful responses feed the transcript cache.
_TRANSCRIPT_PATH = re.compile(r"v1/sessions/([^/]+)/messages")
# How long a proxied call waits for a just-woken sandbox to dial back in.
WAKE_WAIT_SECONDS = 45.0

# Proxied hops must not forward controller-side auth or hop-by-hop headers.
_PROXY_SKIP_RESPONSE_HEADERS = {
    "content-length",
    "transfer-encoding",
    "connection",
    "content-encoding",
}


class _LiveMachine:
    def __init__(self, machine_id: str, name: str, rpc: ch.RpcClient) -> None:
        self.machine_id = machine_id
        self.name = name
        self.rpc = rpc
        self.connected_at = time.time()
        # Bridged WebSocket streams: stream id -> the GUI-facing socket.
        self.streams: dict[str, WebSocket] = {}
        self.stream_counter = 0


class RemoteAcceptor:
    def __init__(self, registry: Registry, ephemera: Optional[Ephemera] = None) -> None:
        self.registry = registry
        self.ephemera: Ephemera = ephemera or InMemoryEphemera()
        self._live: dict[str, _LiveMachine] = {}  # machine_id -> live channel

    # -- arming ---------------------------------------------------------------
    def arm(
        self,
        bound_fingerprint: str = "",
        org_id: str = "",
        actor: str = "",
        provenance: Optional[dict[str, str]] = None,
    ) -> tuple[str, float]:
        """Mint one single-use enrollment token; arming is per-token, not a
        mode. A bound token enrolls only the machine identity it was approved
        for (the device flow binds; the Add-a-machine card does not). The
        token carries the org the enrollment lands in — that is how a brand-new
        machine inherits its tenant — and, for a managed sandbox, the
        provenance {"kind", "ref"} stamped on the row it enrolls."""
        token = pysecrets.token_urlsafe(24)
        expiry = time.time() + TOKEN_TTL_SECONDS
        self.ephemera.add_token(token, expiry, bound_fingerprint, org_id, actor, provenance)
        return token, expiry

    def disarm(self) -> None:
        self.ephemera.clear_tokens()

    def consume_token(self, token: str) -> Optional[dict[str, Any]]:
        """None = invalid/expired; else {"fingerprint": binding or "", "org_id": …}."""
        return self.ephemera.consume_token(token)

    @property
    def armed(self) -> bool:
        return self.ephemera.has_tokens()

    # -- presence -------------------------------------------------------------
    def live(self, machine_id: str) -> Optional[_LiveMachine]:
        return self._live.get(machine_id)

    def machines(self, org_id: Optional[str] = None) -> list[dict[str, Any]]:
        rows = []
        for row in self.registry.list(org_id):
            live = self._live.get(row["id"])
            rows.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "fingerprint": _fingerprint(row["pubkey"]),
                    "app_version": row.get("app_version") or "",
                    # Fleet under the org: who armed/approved the enrollment.
                    "enrolled_by": row.get("enrolled_by") or "",
                    # "" = the user's own machine; "fly" = a managed sandbox
                    # (spec §Fly sandboxes) — the dashboard shows the difference.
                    "provenance": row.get("provenance") or "",
                    "provenance_ref": row.get("provenance_ref") or "",
                    "created_at": row["created_at"],
                    "last_seen": row["last_seen"],
                    "connected": live is not None,
                    # Public halves only. The sealing key lets a BROWSER seal
                    # deploys to this machine (OPE-149); its fingerprint is
                    # what the user checks against `openworker machine status`.
                    "seal_pubkey": row.get("seal_pubkey") or "",
                    "seal_fingerprint": (
                        _fingerprint(row["seal_pubkey"])
                        if row.get("seal_pubkey")
                        else ""
                    ),
                }
            )
        return rows

    def attach(self, machine_id: str, name: str, pubkey: str, rpc: ch.RpcClient) -> None:
        """Caller holds the clone-trap lease for pubkey (acquired pre-enroll)."""
        self._live[machine_id] = _LiveMachine(machine_id, name, rpc)

    async def push_policy(self, machine_id: str, policy: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Hand a live machine its (changed) org policy; (status, body) from the
        box. Offline machines get it on their next welcome."""
        live = self.live(machine_id)
        if live is None:
            return 503, {"error": "machine offline"}
        status, body, _ = await live.rpc.request_frame({"type": "policy", "policy": policy})
        try:
            parsed = json.loads(body.decode() or "{}")
        except (ValueError, AttributeError):
            parsed = {}
        return status, parsed

    async def detach(self, machine_id: str, pubkey: str) -> None:
        live = self._live.pop(machine_id, None)
        self.ephemera.release_lease(pubkey)
        if live is not None:
            live.rpc.close()
            for gui_ws in list(live.streams.values()):
                try:
                    await gui_ws.close(code=1001)  # machine went away
                except Exception:
                    pass
            live.streams.clear()
        self.registry.touch(machine_id)


def _fingerprint(pubkey_b64: str) -> str:
    import base64
    import hashlib

    try:
        raw = base64.b64decode(pubkey_b64.encode("ascii"))
    except Exception:
        return ""
    return hashlib.sha256(raw).hexdigest()[:16]


def mount_acceptor(
    app: FastAPI,
    registry: Registry,
    *,
    ws_authenticated=None,
    origin_allowed=None,
    api_token: str = "",
    wallet=None,
    ephemera: Optional[Ephemera] = None,
    provisioner: Optional[Provisioner] = None,
    audit: Optional[AuditLog] = None,
    tenant_resolver=None,
    deploy_allowed=None,
    approval_url: str = "",
    policy_for=None,
    audit_sink=None,
    policy_status=None,
    machine_activity=None,
    machine_wake=None,
) -> RemoteAcceptor:
    """`ws_authenticated` is the sidecar's GUI WebSocket auth check — passed in
    because it lives in create_app's closure and guards the GUI-facing bridged
    socket exactly like /ws/session. The machine-facing socket never uses it.
    `wallet` is the desktop's SecretStore — the keys wallet the deploy
    endpoints read from (values resolved server-side; the GUI sends NAMES).
    `registry`/`ephemera` are the control-plane stores (stores.py) — desktop
    passes SQLite/nothing, cloud passes Postgres/Redis implementations.

    `tenant_resolver` makes the mount multi-tenant: called with the incoming
    Request (or the GUI-facing WebSocket) it returns {"org_id": …, "actor": …}
    for an authenticated caller or None (→ 401). Every control-plane endpoint
    is then row-scoped to the caller's org, tokens carry the org so machines
    enroll into it, and the pending-grants LISTING disappears (approval is by
    typed code only — a listing would show other tenants' machines). Omitted
    (the desktop), everything runs in the single org ""."""
    acceptor = RemoteAcceptor(registry, ephemera)
    app.state.remote_acceptor = acceptor
    provisioner = provisioner or LocalProvisioner(acceptor)

    # Managed sandboxes (spec §Fly sandboxes, lifecycle): `machine_activity(
    # machine_id)` is told about every proxied call and bridged socket so the
    # control plane can idle-stop a sandbox nobody is using; `machine_wake(
    # machine_id) -> bool` is asked when a caller reaches an OFFLINE machine —
    # True means "it is a sandbox and I started it", and the proxy then waits
    # for the box to dial back in before answering. Desktop passes neither.
    def _touch(machine_id: str) -> None:
        if machine_activity is not None:
            try:
                machine_activity(machine_id)
            except Exception:
                pass

    async def _live_or_wake(machine_id: str):
        live = acceptor.live(machine_id)
        if live is not None or machine_wake is None:
            return live
        try:
            woke = await machine_wake(machine_id)
        except Exception:
            woke = False
        if not woke:
            return None
        deadline = time.monotonic() + WAKE_WAIT_SECONDS
        while time.monotonic() < deadline:
            live = acceptor.live(machine_id)
            if live is not None:
                return live
            await asyncio.sleep(0.5)
        return None
    audit = audit or NullAuditLog()
    multi_tenant = tenant_resolver is not None
    _desktop_tenant = {"org_id": "", "actor": ""}

    async def _tenant(conn) -> Optional[dict[str, Any]]:
        """Resolve the caller's tenant from a Request or WebSocket; None = 401.
        The resolver may be sync or async (a hosted one awaits its IdP)."""
        if tenant_resolver is None:
            return _desktop_tenant
        result = tenant_resolver(conn)
        if inspect.isawaitable(result):
            result = await result
        return result

    _unauthorized = JSONResponse({"error": "unauthorized"}, status_code=401)

    def _org_machine(machine_id: str, tenant: dict[str, Any]):
        """Fetch a machine the caller is allowed to touch, or None. A row in
        another org is a 404, indistinguishable from not existing at all."""
        row = registry.by_id(machine_id)
        if row is None or (row.get("org_id") or "") != tenant["org_id"]:
            return None
        return row

    @app.get("/j/{token}")
    async def join_hint(token: str) -> PlainTextResponse:
        # Informational only, and deliberately constant: it must not confirm
        # whether a token is valid to an unauthenticated caller.
        return PlainTextResponse(
            "This is an OpenWorker join URL. On the machine you want to add, run:\n"
            "  openworker join <this URL> [--name=my-box]\n"
        )

    @app.post("/v1/remote/arm")
    async def arm(request: Request) -> Any:
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        token, expiry = acceptor.arm(org_id=tenant["org_id"], actor=tenant.get("actor", ""))
        base = str(request.base_url).rstrip("/")
        return {
            "join_url": f"{base}/j/{token}",
            "expires_at": expiry,
            "ttl_seconds": TOKEN_TTL_SECONDS,
        }

    @app.delete("/v1/remote/arm")
    async def disarm(request: Request) -> Any:
        if await _tenant(request) is None:
            return _unauthorized
        # Disarm clears ALL tokens; on multi-tenant this is coarse but safe
        # (tokens are 10-minute, single-use — worst case someone re-arms).
        acceptor.disarm()
        return {"armed": False}

    # -- device-authorization flow (`openworker join`) --------------------
    # start/poll are machine-facing and tokenless (like /j/ and /ws/machine);
    # listing and approve/deny ride the GUI's sidecar-token middleware. The
    # approval MINTS the one join token, bound to the requesting identity —
    # auth join is a third path to the same enrollment primitive, never a
    # second primitive.

    @app.post("/v1/remote/device/start")
    async def device_start(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            body = {}
        device_code = pysecrets.token_urlsafe(24)
        user_code = "-".join(
            "".join(pysecrets.choice(_USER_CODE_ALPHABET) for _ in range(4))
            for _ in range(2)
        )
        acceptor.ephemera.put_grant(
            {
                "device_code": device_code,
                "user_code": user_code,
                "name": str(body.get("name") or "machine")[:64],
                "fingerprint": str(body.get("fingerprint") or "")[:64],
                "status": "pending",
                "join_url": "",
                "created_at": time.time(),
                "expires_at": time.time() + DEVICE_TTL_SECONDS,
            }
        )
        return {
            "device_code": device_code,
            "user_code": user_code,
            "interval": DEVICE_POLL_SECONDS,
            "expires_in": DEVICE_TTL_SECONDS,
            # A deployment that serves the SPA hands out a clickable approval
            # deep link (the joiner prints it); desktop has no approval web
            # page, so the hint is the instruction there.
            "verification_url": (
                approval_url.format(code=user_code) if approval_url else ""
            ),
            "verification_hint": "Approve in the OpenWorker app: Settings → Machines",
        }

    @app.post("/v1/remote/device/poll")
    async def device_poll(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            body = {}
        grant = acceptor.ephemera.grant_by_device_code(str(body.get("device_code") or ""))
        if grant is None:
            return {"status": "expired"}
        if grant["status"] == "approved":
            # Single-shot delivery: the join URL leaves the store with this reply.
            acceptor.ephemera.remove_grant(grant["device_code"])
            return {"status": "approved", "join_url": grant["join_url"]}
        if grant["status"] == "denied":
            acceptor.ephemera.remove_grant(grant["device_code"])
            return {"status": "denied"}
        return {"status": "pending", "interval": DEVICE_POLL_SECONDS}

    @app.get("/v1/remote/device")
    async def device_requests(request: Request) -> Any:
        if await _tenant(request) is None:
            return _unauthorized
        if multi_tenant:
            # Pending grants belong to no org until approved; listing them
            # would show every tenant's machine names and fingerprints.
            # Multi-tenant approval is by typed code only (GitHub-style).
            return JSONResponse({"error": "approval is by code"}, status_code=404)
        pending = [
            {
                "user_code": g["user_code"],
                "name": g["name"],
                "fingerprint": g["fingerprint"],
                "created_at": g["created_at"],
                "expires_at": g["expires_at"],
            }
            for g in acceptor.ephemera.list_grants()
            if g["status"] == "pending"
        ]
        return {"requests": sorted(pending, key=lambda g: g["created_at"])}

    @app.get("/v1/remote/device/{user_code}")
    async def device_detail(user_code: str, request: Request) -> Any:
        """What the approval surface shows before the user confirms: the
        machine's claimed name + fingerprint. Knowing the code IS the
        capability (short-lived, high-entropy, single grant)."""
        if await _tenant(request) is None:
            return _unauthorized
        grant = acceptor.ephemera.grant_by_user_code(user_code)
        if grant is None or grant["status"] != "pending":
            return JSONResponse({"error": "unknown or expired code"}, status_code=404)
        return {
            "user_code": grant["user_code"],
            "name": grant["name"],
            "fingerprint": grant["fingerprint"],
            "expires_at": grant["expires_at"],
        }

    @app.post("/v1/remote/device/{user_code}/approve")
    async def device_approve(user_code: str, request: Request) -> Response:
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        grant = acceptor.ephemera.grant_by_user_code(user_code)
        if grant is None or grant["status"] != "pending":
            return JSONResponse({"error": "unknown or expired code"}, status_code=404)
        # Approval CLAIMS the grant into the approver's org: the minted token
        # carries it, so the machine enrolls as that tenant's.
        minted = provisioner.mint_token(
            {
                "fingerprint": grant["fingerprint"],
                "org_id": tenant["org_id"],
                "actor": tenant.get("actor", ""),
            }
        )
        base = str(request.base_url).rstrip("/")
        acceptor.ephemera.update_grant(
            grant["device_code"],
            status="approved",
            join_url=f"{base}/j/{minted['token']}",
        )
        audit.emit(
            "update",
            entity_type="device_grant",
            entity_name=grant["name"],
            detail={"decision": "approved", "fingerprint": grant["fingerprint"]},
            actor=tenant["actor"],
            org_id=tenant["org_id"],
        )
        return JSONResponse({"ok": True})

    @app.post("/v1/remote/device/{user_code}/deny")
    async def device_deny(user_code: str, request: Request) -> Response:
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        grant = acceptor.ephemera.grant_by_user_code(user_code)
        if grant is None or grant["status"] != "pending":
            return JSONResponse({"error": "unknown or expired code"}, status_code=404)
        acceptor.ephemera.update_grant(grant["device_code"], status="denied")
        audit.emit(
            "update",
            entity_type="device_grant",
            entity_name=grant["name"],
            detail={"decision": "denied", "fingerprint": grant["fingerprint"]},
            actor=tenant["actor"],
            org_id=tenant["org_id"],
        )
        return JSONResponse({"ok": True})

    @app.get("/v1/machines")
    async def list_machines(request: Request) -> Any:
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        return {
            "machines": acceptor.machines(tenant["org_id"]),
            "armed": acceptor.armed,
        }

    @app.patch("/v1/machines/{machine_id}")
    async def rename_machine(machine_id: str, request: Request):
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        if _org_machine(machine_id, tenant) is None:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        payload = await request.json()
        name = str(payload.get("name", "")).strip()
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        try:
            row = registry.rename(machine_id, name)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        if row is None:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        live = acceptor.live(machine_id)
        if live is not None:
            live.name = row["name"]
        return {"machine": {"id": row["id"], "name": row["name"]}}

    @app.delete("/v1/machines/{machine_id}")
    async def remove_machine(machine_id: str, request: Request):
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        row = _org_machine(machine_id, tenant)
        if row is None:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        live = acceptor.live(machine_id)
        if live is not None:
            return JSONResponse(
                {"error": "machine is connected; it must leave (or go offline) first"},
                status_code=409,
            )
        if not registry.remove(machine_id):
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        audit.emit(
            "delete",
            entity_type="machine",
            entity_name=row["name"],
            entity_uid=machine_id,
            actor=tenant["actor"],
            org_id=tenant["org_id"],
        )
        return {"removed": machine_id}

    @app.get("/v1/wallet")
    async def wallet_profiles(request: Request):
        """Wallet contents by NAME AND TYPE only — never values. Feeds the
        enrollment card's "provision with my defaults" and the chips UIs."""
        if await _tenant(request) is None:
            return _unauthorized
        if wallet is None:
            return {"profiles": []}
        return {"profiles": wallet.status()}

    @app.get("/v1/machines/{machine_id}/secrets")
    async def machine_secrets(machine_id: str, request: Request):
        """The deployments ledger + staleness: which wallet profiles this
        machine holds, and whether the wallet's value changed since (hash
        comparison — values never flow back)."""
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        if _org_machine(machine_id, tenant) is None:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        rows = []
        for row in registry.deploys(machine_id):
            current = _wallet_hash(wallet, row["profile"])
            rows.append(
                {
                    "profile": row["profile"],
                    "deployed_at": row["deployed_at"],
                    # stale: wallet value changed since deploy (rotate = deploy
                    # again — owner ruling: no third verb). missing: profile no
                    # longer in the wallet at all. A wallet-less deployment
                    # (browser-sealed deploys) has no reference to compare —
                    # both fields honestly read false there.
                    "stale": current is not None and current != row["value_hash"],
                    "missing_from_wallet": wallet is not None and current is None,
                }
            )
        return {"secrets": rows}

    @app.post("/v1/machines/{machine_id}/secrets")
    async def deploy_secrets(machine_id: str, request: Request):
        """Key deploy, two sources, one frame:

        - Wallet path (`{"profiles": [names]}`): the GUI sends NAMES; the
          controller resolves values from its own SecretStore, seals them to
          the machine's pinned sealing key, and pushes.
        - Sealed path (`{"sealed_b64": …, "profiles": [names]}`, OPE-149):
          the BROWSER already sealed the payload to that same pinned key;
          this endpoint is a blind relay — it never sees plaintext, needs no
          wallet, and records only names in the ledger and audit trail.

        Either way, deploy = copy — the box owns its copy.
        """
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        known = _org_machine(machine_id, tenant)
        if known is None:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        # Org-level policy checkpoint (OPE-150): the embedder may refuse key
        # pushes per tenant. Enforced HERE at the relay — hiding a button is
        # cosmetics; this refusal is what a security review tests. The denial
        # itself is an audit event (who tried, which org said no).
        if deploy_allowed is not None and not deploy_allowed(tenant):
            audit.emit(
                "create",
                entity_type="secret_deploy",
                entity_name="(refused)",
                entity_uid=machine_id,
                detail={"denied": "key_push_disabled"},
                success=False,
                actor=tenant["actor"],
                org_id=tenant["org_id"],
            )
            return JSONResponse(
                {
                    "error": "key_push_disabled",
                    "message": "key pushes are turned off for this org.",
                },
                status_code=403,
            )
        payload = await request.json()
        sealed_b64 = str(payload.get("sealed_b64") or "")
        if wallet is None and not sealed_b64:
            return JSONResponse({"error": "no wallet configured"}, status_code=500)
        live = acceptor.live(machine_id)
        if live is None:
            return JSONResponse({"error": "machine offline"}, status_code=503)
        seal_pubkey = known.get("seal_pubkey") or ""
        if not seal_pubkey:
            return JSONResponse(
                {
                    "error": "machine has no pinned sealing key yet — "
                    "reconnect it once on current code, then retry"
                },
                status_code=409,
            )
        names = [str(n) for n in (payload.get("profiles") or []) if str(n).strip()]
        if not names:
            return JSONResponse({"error": "profiles required"}, status_code=400)
        if sealed_b64:
            sealed = sealed_b64
            # Ledger hashes come from the client (it alone saw the values);
            # they only ever drive the staleness display, never a decision.
            hashes = payload.get("hashes") or {}
            ledger = {
                name: str(hashes.get(name) or "browser-sealed") for name in names
            }
        else:
            profiles: dict[str, Any] = {}
            for name in names:
                data = wallet.get(name)  # resolved: refs to desktop-only env vars
                if data is None:        # would silently break on the box
                    return JSONResponse(
                        {"error": f"profile '{name}' not in the wallet"},
                        status_code=400,
                    )
                profiles[name] = data
            sealed = seal_b64(seal_pubkey, json.dumps({"profiles": profiles}).encode())
            ledger = {name: _hash_profile(data) for name, data in profiles.items()}
        try:
            status, body, _ = await live.rpc.request_frame(
                {"type": "secret_deploy", "sealed_b64": sealed}
            )
        except (asyncio.TimeoutError, ConnectionError):
            return JSONResponse({"error": "machine unreachable"}, status_code=503)
        if status != 200:
            detail = _safe_json(body)
            return JSONResponse(
                {"error": detail.get("error", "deploy failed")}, status_code=502
            )
        for name, value_hash in ledger.items():
            registry.record_deploy(machine_id, name, value_hash)
            audit.emit(
                "create",
                entity_type="secret_deploy",
                entity_name=name,  # profile NAME — values never reach the log
                entity_uid=machine_id,
                detail={
                    "machine": known["name"],
                    **({"source": "browser-sealed"} if sealed_b64 else {}),
                },
                actor=tenant["actor"],
                org_id=tenant["org_id"],
            )
        return {"ok": True, "deployed": sorted(names)}

    @app.post("/v1/machines/{machine_id}/connectors/{name}/connect-sealed")
    async def connector_connect_sealed(machine_id: str, name: str, request: Request):
        """Remote manual connect: the client sealed the connector FIELDS to
        this machine's pinned key; we relay ciphertext and the box runs its
        own connect (validation included). A credential push, so the same
        org policy checkpoint as key deploys applies."""
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        known = _org_machine(machine_id, tenant)
        if known is None:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        if deploy_allowed is not None and not deploy_allowed(tenant):
            audit.emit(
                "create",
                entity_type="connector_connect",
                entity_name=name,
                entity_uid=machine_id,
                detail={"denied": "key_push_disabled"},
                success=False,
                actor=tenant["actor"],
                org_id=tenant["org_id"],
            )
            return JSONResponse(
                {
                    "error": "key_push_disabled",
                    "message": "key pushes are turned off for this org.",
                },
                status_code=403,
            )
        live = acceptor.live(machine_id)
        if live is None:
            return JSONResponse({"error": "machine offline"}, status_code=503)
        payload = await request.json()
        sealed = str(payload.get("sealed_b64") or "")
        if not sealed:
            return JSONResponse({"error": "sealed_b64 required"}, status_code=400)
        try:
            status, body, _ = await live.rpc.request_frame(
                {
                    "type": "connector_connect_sealed",
                    "connector": name,
                    "sealed_b64": sealed,
                }
            )
        except (asyncio.TimeoutError, ConnectionError):
            return JSONResponse({"error": "machine unreachable"}, status_code=503)
        detail = _safe_json(body)
        audit.emit(
            "create",
            entity_type="connector_connect",
            entity_name=name,  # connector NAME — fields never reach the log
            entity_uid=machine_id,
            detail={"machine": known["name"], "source": "browser-sealed"},
            success=status == 200 and bool(detail.get("ok")),
            actor=tenant["actor"],
            org_id=tenant["org_id"],
        )
        return JSONResponse(detail, status_code=status if status != 200 else 200)

    @app.post("/v1/machines/{machine_id}/connectors/{name}/managed-grant-sealed")
    async def managed_grant_sealed(machine_id: str, name: str, request: Request):
        """Browser connect-direct (spec §Cloud-dashboard connect-direct): the
        dashboard tab sealed a broker OAuth result to this machine's pinned
        key; we relay ciphertext and the box stores it through its own
        routine. A credential push — the org's key-push checkpoint applies."""
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        known = _org_machine(machine_id, tenant)
        if known is None:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        if deploy_allowed is not None and not deploy_allowed(tenant):
            audit.emit(
                "create", entity_type="managed_grant", entity_name=name, entity_uid=machine_id,
                detail={"denied": "key_push_disabled"}, success=False,
                actor=tenant["actor"], org_id=tenant["org_id"],
            )
            return JSONResponse(
                {"error": "key_push_disabled", "message": "key pushes are turned off for this org."},
                status_code=403,
            )
        live = await _live_or_wake(machine_id)
        if live is None:
            return JSONResponse({"error": "machine offline"}, status_code=503)
        payload = await request.json()
        sealed = str(payload.get("sealed_b64") or "")
        if not sealed:
            return JSONResponse({"error": "sealed_b64 required"}, status_code=400)
        try:
            status, body, _ = await live.rpc.request_frame(
                {"type": "managed_grant_sealed", "connector": name, "sealed_b64": sealed}
            )
        except (asyncio.TimeoutError, ConnectionError):
            return JSONResponse({"error": "machine unreachable"}, status_code=503)
        detail = _safe_json(body)
        audit.emit(
            "create", entity_type="managed_grant", entity_name=name, entity_uid=machine_id,
            detail={"machine": known["name"], "source": "browser-sealed"},
            success=status == 200 and bool(detail.get("ok")),
            actor=tenant["actor"], org_id=tenant["org_id"],
        )
        return JSONResponse(detail, status_code=status if status != 200 else 200)

    @app.delete("/v1/machines/{machine_id}/secrets")
    async def revoke_secrets(machine_id: str, request: Request):
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        if _org_machine(machine_id, tenant) is None:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        live = acceptor.live(machine_id)
        if live is None:
            # An offline box can't be scrubbed; keeping the ledger row is the
            # honest state ("this machine still holds that key").
            return JSONResponse({"error": "machine offline"}, status_code=503)
        payload = await request.json()
        names = [str(n) for n in (payload.get("profiles") or []) if str(n).strip()]
        if not names:
            return JSONResponse({"error": "profiles required"}, status_code=400)
        try:
            status, body, _ = await live.rpc.request_frame(
                {"type": "secret_revoke", "profiles": names}
            )
        except (asyncio.TimeoutError, ConnectionError):
            return JSONResponse({"error": "machine unreachable"}, status_code=503)
        if status != 200:
            detail = _safe_json(body)
            return JSONResponse(
                {"error": detail.get("error", "revoke failed")}, status_code=502
            )
        for name in names:
            registry.remove_deploy(machine_id, name)
            audit.emit(
                "delete",
                entity_type="secret_deploy",
                entity_name=name,
                entity_uid=machine_id,
                actor=tenant["actor"],
                org_id=tenant["org_id"],
            )
        return {"ok": True, "revoked": names}

    @app.api_route(
        "/v1/machines/{machine_id}/p/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy(machine_id: str, path: str, request: Request):
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        known = _org_machine(machine_id, tenant)
        if known is None:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        _touch(machine_id)
        live = await _live_or_wake(machine_id)
        if live is None:
            return JSONResponse({"error": "machine offline"}, status_code=503)
        target = "/" + path
        if request.url.query:
            target += "?" + request.url.query
        body = await request.body()
        try:
            status, payload, content_type = await live.rpc.request(
                request.method,
                target,
                body=body or None,
                content_type=request.headers.get("content-type"),
                # Session actor (spec §Fleet under the org): the box records WHO
                # is acting, from the login the controller verified — never from a
                # header the caller could set.
                actor=str(tenant.get("actor") or ""),
            )
        except asyncio.TimeoutError:
            return JSONResponse({"error": "machine timed out"}, status_code=504)
        except ConnectionError:
            return JSONResponse({"error": "machine disconnected"}, status_code=503)
        # Tier-2 snapshot: every successful unfiltered session-list fetch doubles
        # as the offline fallback (greyed, never vanished). Metadata only.
        if (
            request.method == "GET"
            and path == "v1/sessions"
            and not request.url.query
            and status == 200
        ):
            try:
                registry.save_sessions_snapshot(machine_id, payload.decode("utf-8"))
            except Exception:
                pass
        # Desktop transcript cache (cache-on-view): a transcript fetched while
        # connected stays readable read-only while the box is offline.
        transcript_match = _TRANSCRIPT_PATH.fullmatch(path)
        if request.method == "GET" and transcript_match and status == 200:
            try:
                registry.save_transcript(
                    machine_id, transcript_match.group(1), payload.decode("utf-8")
                )
            except Exception:
                pass
        return Response(content=payload, status_code=status, media_type=content_type)

    @app.get("/v1/machines/{machine_id}/sessions/{session_id}/messages")
    async def machine_transcript(machine_id: str, session_id: str, request: Request):
        """The controller's view of one remote transcript: live (proxied, and
        re-cached) while the machine is connected; the last synced copy —
        read-only by construction — while it is offline."""
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        if _org_machine(machine_id, tenant) is None:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        live = acceptor.live(machine_id)
        if live is not None:
            try:
                status, payload, _ = await live.rpc.request(
                    "GET", f"/v1/sessions/{session_id}/messages"
                )
                if status == 200:
                    text = payload.decode("utf-8")
                    registry.save_transcript(machine_id, session_id, text)
                    data = json.loads(text)
                    return {"messages": data.get("messages", []), "live": True}
            except (asyncio.TimeoutError, ConnectionError, ValueError):
                pass  # fall through to the cache
        cached = registry.transcript(machine_id, session_id)
        if cached is None:
            return {"messages": [], "live": False, "cached": False}
        try:
            data = json.loads(cached)
        except ValueError:
            return {"messages": [], "live": False, "cached": False}
        return {"messages": data.get("messages", []), "live": False, "cached": True}

    @app.get("/v1/machines/{machine_id}/sessions")
    async def machine_sessions(machine_id: str, request: Request):
        """The controller's view of one machine's sessions: live (proxied) while
        the machine is connected, the last-known snapshot while it is offline."""
        tenant = await _tenant(request)
        if tenant is None:
            return _unauthorized
        if _org_machine(machine_id, tenant) is None:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        live = acceptor.live(machine_id)
        if live is not None:
            try:
                status, payload, _ = await live.rpc.request("GET", "/v1/sessions")
                if status == 200:
                    registry.save_sessions_snapshot(
                        machine_id, payload.decode("utf-8")
                    )
                    data = json.loads(payload.decode("utf-8"))
                    return {"sessions": data.get("sessions", []), "live": True}
            except (asyncio.TimeoutError, ConnectionError, ValueError):
                pass  # fall through to the snapshot
        snapshot = registry.sessions_snapshot(machine_id)
        if snapshot is None:
            return {"sessions": [], "live": False}
        try:
            data = json.loads(snapshot)
        except ValueError:
            return {"sessions": [], "live": False}
        return {"sessions": data.get("sessions", []), "live": False}

    @app.websocket("/ws/machine")
    async def ws_machine(ws: WebSocket) -> None:
        # Never sidecar-token-authenticated: boxes can't (and must not) hold that
        # token. A browser can't pass the challenge without the private key, but
        # reject browser origins outright anyway.
        origin = ws.headers.get("origin")
        if origin is not None:
            await ws.close(code=1008)
            return
        await ws.accept()

        machine_id = pubkey = ""
        attached = leased = False
        heartbeat_task: Optional[asyncio.Task] = None
        try:
            hello = json.loads(await ws.receive_text())
            if hello.get("type") != "hello":
                await _reject(ws, "bad-handshake")
                return
            if int(hello.get("protocol_version", -1)) != ch.PROTOCOL_VERSION:
                # P1 stance: strict equality. The friendly error IS the feature.
                await _reject(
                    ws,
                    "protocol-mismatch",
                    f"controller speaks protocol {ch.PROTOCOL_VERSION}, "
                    f"machine spoke {hello.get('protocol_version')} — "
                    "update the older side",
                )
                return
            pubkey = str(hello.get("pubkey", ""))
            if not pubkey:
                await _reject(ws, "bad-handshake", "missing pubkey")
                return

            known = registry.by_pubkey(pubkey)
            token = str(hello.get("token") or "")
            grant_org = ""
            grant_actor = ""
            grant_provenance: dict[str, str] = {}
            if known is None:
                binding = acceptor.consume_token(token)
                if binding is None:
                    await _reject(
                        ws,
                        "not-enrolled",
                        "unknown machine and no valid enrollment token — arm the "
                        "controller and join with a fresh URL",
                    )
                    return
                if binding["fingerprint"] and binding["fingerprint"] != _fingerprint(
                    pubkey
                ):
                    # A device-flow token names the identity the user approved;
                    # any other machine presenting it burns it and gets nothing.
                    await _reject(
                        ws,
                        "wrong-machine",
                        "this join token was approved for a different machine "
                        "identity — request approval again from this machine",
                    )
                    return
                grant_org = binding["org_id"]
                grant_actor = str(binding.get("actor") or "")
                grant_provenance = dict(binding.get("provenance") or {})

            # Both paths prove possession of the private key.
            nonce = pysecrets.token_bytes(_CHALLENGE_BYTES)
            await ws.send_text(
                json.dumps({"type": "challenge", "nonce": ch.encode_body(nonce)})
            )
            auth = json.loads(await ws.receive_text())
            if auth.get("type") != "auth" or not verify_b64(
                pubkey, nonce, str(auth.get("signature", ""))
            ):
                await _reject(ws, "bad-signature")
                return

            if not acceptor.ephemera.acquire_lease(pubkey):
                # Clone trap: one concurrent connection per identity. A copied
                # state dir must not silently become a second "same" machine.
                await _reject(
                    ws,
                    "already-connected",
                    "a machine with this identity is already connected",
                )
                return
            leased = True

            app_version = str(hello.get("app_version", ""))
            seal_pubkey = str(hello.get("seal_pubkey", ""))
            # The challenge only proves the identity key; the sealing key is
            # pinned only if that same identity key signed it. Without this a
            # middlebox could swap in its own key and read wallet deploys.
            if seal_pubkey and not verify_seal_attestation(
                pubkey, seal_pubkey, str(hello.get("seal_sig", ""))
            ):
                await _reject(
                    ws,
                    "bad-seal-attestation",
                    "sealing key is not signed by this machine's identity key",
                )
                return
            if known is None:
                known = registry.enroll(
                    str(hello.get("name") or "machine"),
                    pubkey,
                    app_version,
                    seal_pubkey,
                    org_id=grant_org,
                    enrolled_by=grant_actor,
                    provenance=str(grant_provenance.get("kind") or ""),
                    provenance_ref=str(grant_provenance.get("ref") or ""),
                )
                audit.emit(
                    "create",
                    entity_type="machine",
                    entity_name=known["name"],
                    entity_uid=known["id"],
                    detail={
                        "fingerprint": _fingerprint(pubkey),
                        **({"provenance": grant_provenance["kind"]} if grant_provenance.get("kind") else {}),
                    },
                    org_id=grant_org,
                )
            elif seal_pubkey and not known.get("seal_pubkey"):
                # Backfill: a box enrolled before the sealing key pins it on its
                # next reconnect. Sound over untrusted transports because the
                # attestation above ties it to the challenged identity key.
                registry.set_seal_pubkey(known["id"], seal_pubkey)
            machine_id = known["id"]
            registry.touch(machine_id, app_version)

            rpc = ch.RpcClient(ws.send_text)
            acceptor.attach(machine_id, known["name"], pubkey, rpc)
            attached = True
            # Heartbeat for TTL-leased backends: the lease outlives the node
            # only by its TTL, never forever.
            refresh = getattr(acceptor.ephemera, "refresh_lease", None)
            if refresh is not None:

                async def _heartbeat() -> None:
                    while True:
                        await asyncio.sleep(LEASE_REFRESH_SECONDS)
                        refresh(pubkey)

                heartbeat_task = asyncio.create_task(_heartbeat())
            welcome: dict[str, Any] = {
                "type": "welcome",
                "machine_id": machine_id,
                "name": known["name"],
            }
            # Org policy rides the welcome (spec §Audit export): hosted controllers
            # supply `policy_for(machine_id, known)`; the desktop supplies nothing
            # and the box keeps whatever record it already holds.
            if policy_for is not None:
                try:
                    policy = policy_for(machine_id, known)
                    if asyncio.iscoroutine(policy):
                        policy = await policy
                except Exception:
                    policy = None
                if isinstance(policy, dict):
                    welcome["policy"] = policy
                    if policy.get("org_id"):
                        welcome["org_id"] = str(policy["org_id"])
            await ws.send_text(json.dumps(welcome))

            live = acceptor.live(machine_id)
            while True:
                frame = json.loads(await ws.receive_text())
                kind = frame.get("type")
                if kind == "rpc_result":
                    rpc.resolve(frame)
                elif kind == "policy_check":
                    # The box reports the version it holds (spec §Versioned org
                    # policy). Record it — status is what the machine SAYS — then
                    # answer with the newer document or "current".
                    reported = _int(frame.get("version"))
                    _record(policy_status, machine_id, known, reported, "checked")
                    doc = None
                    if policy_for is not None:
                        try:
                            doc = policy_for(machine_id, known)
                            if asyncio.iscoroutine(doc):
                                doc = await doc
                        except Exception:
                            doc = None
                    current = _int((doc or {}).get("version")) if isinstance(doc, dict) else 0
                    if isinstance(doc, dict) and (current > reported or (current == 0 and reported == 0)):

                        async def _send_newer(doc=doc, current=current):
                            try:
                                status, body, _ = await rpc.request_frame(
                                    {"type": "policy", "policy": doc}
                                )
                                parsed = _safe_json(body)
                                ok = status == 200 and bool(parsed.get("ok"))
                                _record(
                                    policy_status,
                                    machine_id,
                                    known,
                                    _int(parsed.get("version")) if ok else reported,
                                    "applied" if ok and parsed.get("applied") else ("ignored" if ok else "failed"),
                                )
                            except (asyncio.TimeoutError, ConnectionError):
                                _record(policy_status, machine_id, known, reported, "unreachable")

                        asyncio.create_task(_send_newer())
                    else:
                        await ws.send_text(
                            json.dumps({"type": "policy_current", "version": reported})
                        )
                elif kind == "audit":
                    # Box-originated audit batch (cloud sink). Accepted only where a
                    # sink exists; a controller without one says so and the box backs
                    # off instead of dropping rows.
                    events = frame.get("events")
                    accepted = False
                    if audit_sink is not None and isinstance(events, list):
                        try:
                            res = audit_sink(machine_id, known, events)
                            if asyncio.iscoroutine(res):
                                res = await res
                            accepted = bool(res)
                        except Exception:
                            accepted = False
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "audit_ack",
                                "id": str(frame.get("id")),
                                "accepted": accepted,
                                "count": len(events) if isinstance(events, list) else 0,
                            }
                        )
                    )
                elif kind == "ws_msg" and live is not None:
                    gui_ws = live.streams.get(str(frame.get("stream")))
                    if gui_ws is not None:
                        try:
                            await gui_ws.send_text(str(frame.get("text", "")))
                        except Exception:
                            pass  # GUI went away; its handler sends ws_close
                elif kind == "ws_close" and live is not None:
                    gui_ws = live.streams.pop(str(frame.get("stream")), None)
                    if gui_ws is not None:
                        try:
                            await gui_ws.close()
                        except Exception:
                            pass
        except (WebSocketDisconnect, json.JSONDecodeError, KeyError, ValueError):
            pass
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
            if attached:
                await acceptor.detach(machine_id, pubkey)  # releases the lease
            elif leased:
                # Rejected between lease and attach (e.g. bad seal attestation):
                # free the identity or the real box could never reconnect.
                acceptor.ephemera.release_lease(pubkey)

    @app.websocket("/ws/machines/{machine_id}/p/{path:path}")
    async def ws_bridge(ws: WebSocket, machine_id: str, path: str) -> None:
        """GUI side of a bridged session socket: `/ws/machines/{mid}/p/ws/session/{sid}`
        behaves exactly like the box's own `/ws/session/{sid}` — the remote home is
        literally a different base URL. Sidecar-token + Origin gated like /ws/session."""
        if ws_authenticated is not None and not ws_authenticated(ws):
            await ws.close(code=1008)
            return
        if origin_allowed is not None and not origin_allowed(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        tenant = await _tenant(ws)
        if tenant is None:
            await ws.close(code=1008)
            return
        if _org_machine(machine_id, tenant) is None:
            await ws.close(code=1008)  # not this tenant's machine (or unknown)
            return
        _touch(machine_id)
        live = await _live_or_wake(machine_id)
        if live is None:
            await ws.close(code=1013)  # machine offline: try again later
            return
        # Select "openworker" whenever the client OFFERED subprotocols: a
        # browser treats an unanswered offer as a failed handshake and closes
        # 1006 the moment the socket opens (python clients are lenient, which
        # hid this until the first real browser hit the hosted tier — the
        # api_token gate here only ever covered the desktop deployment).
        offered = {
            part.strip()
            for part in ws.headers.get("sec-websocket-protocol", "").split(",")
            if part.strip()
        }
        await ws.accept(subprotocol="openworker" if "openworker" in offered else None)
        live.stream_counter += 1
        stream_id = f"s{live.stream_counter}"
        live.streams[stream_id] = ws
        target = "/" + path
        query = ws.scope.get("query_string", b"").decode()
        if query:
            target += "?" + query
        try:
            await live.rpc.send_frame(
                {
                    "type": "ws_open",
                    "stream": stream_id,
                    "path": target,
                    "actor": str(tenant.get("actor") or ""),
                }
            )
            while True:
                text = await ws.receive_text()
                await live.rpc.send_frame(
                    {"type": "ws_msg", "stream": stream_id, "text": text}
                )
        except (WebSocketDisconnect, ConnectionError):
            pass
        finally:
            if live.streams.pop(stream_id, None) is not None:
                try:
                    await live.rpc.send_frame({"type": "ws_close", "stream": stream_id})
                except Exception:
                    pass

    return acceptor


async def _reject(ws: WebSocket, reason: str, detail: Optional[str] = None) -> None:
    try:
        await ws.send_text(json.dumps(ch.reject_frame(reason, detail)))
        await ws.close(code=1008)
    except Exception:
        pass


def _hash_profile(data: Any) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _wallet_hash(wallet, profile: str) -> Optional[str]:
    if wallet is None:
        return None
    try:
        data = wallet.get(profile)
    except Exception:
        return None
    return None if data is None else _hash_profile(data)


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _record(policy_status, machine_id: str, known: dict[str, Any], version: int, outcome: str) -> None:
    """Best-effort status hook: (machine_id, known, version, outcome) where
    outcome ∈ checked | applied | ignored | failed | unreachable."""
    if policy_status is None:
        return
    try:
        policy_status(machine_id, known, version, outcome)
    except Exception:
        pass


def _safe_json(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}
