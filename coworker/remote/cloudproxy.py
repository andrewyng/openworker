"""Union view: the desktop sidecar's window onto a hosted cloud registry.

The desktop SPA talks only to its own sidecar; this module gives that sidecar
a `/v1/cloud/machines*` surface that forwards to the hosted machines service
with the user's cloud session token. One backend for the SPA, no CORS, and
the token never enters the webview. The two registries stay separate — this
is a VIEW, never a merge (spec: "Union view on the signed-in desktop").

Design rule enforced here: a cloud-session problem degrades the cloud
section, it never errors the local path. Signed out or expired comes back as
a normal 200 with a `session` state the GUI renders; only true network
failures surface as 502.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Optional

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from .identity import seal_b64

# "signed_out" = no cloud profile at all; "expired" = a profile exists but the
# session could not be refreshed (Auth0 refresh tokens die from inactivity);
# "ok" = a usable token was produced. The GUI hides the cloud section on
# signed_out and shows a sign-in-again row on expired.
TokenProvider = Callable[[], Awaitable[tuple[str, Optional[str]]]]

_ME_TTL_SECONDS = 900.0


def mount_cloud_proxy(
    app: FastAPI,
    *,
    token_provider: TokenProvider,
    base_url: str,
    wallet=None,
    ws_authenticated=None,
    origin_allowed=None,
    client: Optional[httpx.AsyncClient] = None,
) -> None:
    """`token_provider` returns (state, token) — state per the notes above,
    token only when state == "ok". `wallet` is the desktop SecretStore the
    wallet-send deploy path resolves names from (values are sealed HERE, to
    the machine's pinned key — the cloud only ever relays ciphertext).
    `client` is injectable for tests (httpx.MockTransport)."""

    upstream = client or httpx.AsyncClient(base_url=base_url, timeout=30)
    me_cache: dict[str, Any] = {"at": 0.0, "org": None}

    async def _forward(
        method: str,
        path: str,
        token: str,
        *,
        body: Optional[bytes] = None,
        content_type: Optional[str] = None,
        query: str = "",
    ) -> Response:
        headers = {"Authorization": f"Bearer {token}"}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            resp = await upstream.request(
                method, path + (f"?{query}" if query else ""), content=body, headers=headers
            )
        except httpx.HTTPError:
            return JSONResponse({"error": "cloud unreachable"}, status_code=502)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type"),
        )

    async def _forward_request(path: str, request: Request) -> Response:
        state, token = await token_provider()
        if state != "ok":
            return JSONResponse({"error": "cloud_session", "session": state}, status_code=401)
        return await _forward(
            request.method,
            path,
            token or "",
            body=await request.body() or None,
            content_type=request.headers.get("content-type"),
            query=request.url.query,
        )

    async def _org(token: str) -> Optional[dict[str, str]]:
        if time.monotonic() - me_cache["at"] < _ME_TTL_SECONDS and me_cache["org"]:
            return me_cache["org"]
        try:
            resp = await upstream.get(
                "/v1/me", headers={"Authorization": f"Bearer {token}"}
            )
            if resp.status_code != 200:
                return me_cache["org"]
            me = resp.json()
            row = next(
                (o for o in me.get("orgs", []) if o.get("id") == me.get("org_id")),
                None,
            )
            me_cache["org"] = {
                "id": me.get("org_id", ""),
                "name": (row or {}).get("name") or me.get("org_id", ""),
            }
            me_cache["at"] = time.monotonic()
        except httpx.HTTPError:
            pass
        return me_cache["org"]

    @app.get("/v1/cloud/machines")
    async def cloud_machines() -> Any:
        state, token = await token_provider()
        if state != "ok":
            return {"session": state, "machines": []}
        try:
            resp = await upstream.get(
                "/v1/machines", headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError:
            return {"session": "unreachable", "machines": []}
        if resp.status_code == 401:
            # The token minted fine but the service refused it — treat like
            # an expired session rather than a hard error.
            return {"session": "expired", "machines": []}
        if resp.status_code != 200:
            return {"session": "unreachable", "machines": []}
        rows = resp.json().get("machines", [])
        return {"session": "ok", "org": await _org(token or ""), "machines": rows}

    @app.api_route("/v1/cloud/machines/{machine_id}", methods=["PATCH", "DELETE"])
    async def cloud_machine_admin(machine_id: str, request: Request) -> Response:
        return await _forward_request(f"/v1/machines/{machine_id}", request)

    @app.get("/v1/cloud/machines/{machine_id}/sessions")
    async def cloud_machine_sessions(machine_id: str, request: Request) -> Response:
        return await _forward_request(f"/v1/machines/{machine_id}/sessions", request)

    @app.get("/v1/cloud/machines/{machine_id}/sessions/{session_id}/messages")
    async def cloud_machine_transcript(
        machine_id: str, session_id: str, request: Request
    ) -> Response:
        return await _forward_request(
            f"/v1/machines/{machine_id}/sessions/{session_id}/messages", request
        )

    @app.api_route("/v1/cloud/machines/{machine_id}/secrets", methods=["GET", "DELETE"])
    async def cloud_machine_secrets(machine_id: str, request: Request) -> Response:
        return await _forward_request(f"/v1/machines/{machine_id}/secrets", request)

    @app.post("/v1/cloud/machines/{machine_id}/secrets")
    async def cloud_deploy_secrets(machine_id: str, request: Request) -> Response:
        """Wallet-send to a cloud machine (the OPE-149 increment the union
        view unlocks): the GUI sends NAMES; values resolve from the desktop
        wallet and are sealed HERE to the machine's pinned key. The cloud
        endpoint receives only the sealed blob — the same blind-relay path
        the browser uses, with wallet-hash provenance in the ledger."""
        state, token = await token_provider()
        if state != "ok":
            return JSONResponse({"error": "cloud_session", "session": state}, status_code=401)
        payload = await request.json()
        names = [str(n) for n in (payload.get("profiles") or []) if str(n).strip()]
        if not names:
            return JSONResponse({"error": "profiles required"}, status_code=400)
        if wallet is None:
            return JSONResponse({"error": "no wallet configured"}, status_code=500)
        try:
            resp = await upstream.get(
                "/v1/machines", headers={"Authorization": f"Bearer {token}"}
            )
            rows = resp.json().get("machines", []) if resp.status_code == 200 else []
        except httpx.HTTPError:
            return JSONResponse({"error": "cloud unreachable"}, status_code=502)
        row = next((m for m in rows if m.get("id") == machine_id), None)
        if row is None:
            return JSONResponse({"error": "unknown machine"}, status_code=404)
        seal_pubkey = row.get("seal_pubkey") or ""
        if not seal_pubkey:
            return JSONResponse(
                {"error": "machine has no pinned sealing key yet"}, status_code=409
            )
        profiles: dict[str, Any] = {}
        for name in names:
            data = wallet.get(name)
            if data is None:
                return JSONResponse(
                    {"error": f"profile '{name}' not in the wallet"}, status_code=400
                )
            profiles[name] = data
        from .acceptor import _hash_profile

        sealed = seal_b64(seal_pubkey, json.dumps({"profiles": profiles}).encode())
        body = {
            "sealed_b64": sealed,
            "profiles": names,
            "hashes": {name: _hash_profile(data) for name, data in profiles.items()},
        }
        return await _forward(
            "POST",
            f"/v1/machines/{machine_id}/secrets",
            token or "",
            body=json.dumps(body).encode(),
            content_type="application/json",
        )

    @app.post("/v1/cloud/machines/{machine_id}/connectors/{name}/connect-sealed")
    async def cloud_connector_connect_sealed(
        machine_id: str, name: str, request: Request
    ) -> Response:
        # Already ciphertext (sealed in the GUI to the machine's pinned key) —
        # a plain forward; the hosted service relays it blind like key deploys.
        return await _forward_request(
            f"/v1/machines/{machine_id}/connectors/{name}/connect-sealed", request
        )

    @app.api_route(
        "/v1/cloud/machines/{machine_id}/p/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def cloud_proxy(machine_id: str, path: str, request: Request) -> Response:
        return await _forward_request(f"/v1/machines/{machine_id}/p/{path}", request)

    @app.websocket("/ws/cloud/machines/{machine_id}/p/{path:path}")
    async def cloud_ws_bridge(ws: WebSocket, machine_id: str, path: str) -> None:
        """Bridged session socket for a cloud machine: the sidecar dials the
        hosted service's own bridge and pipes frames both ways. Gated like
        every GUI-facing socket (sidecar token + Origin)."""
        if ws_authenticated is not None and not ws_authenticated(ws):
            await ws.close(code=1008)
            return
        if origin_allowed is not None and not origin_allowed(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        state, token = await token_provider()
        if state != "ok":
            await ws.close(code=1008)
            return
        import websockets

        ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")
        target = f"{ws_base}/ws/machines/{machine_id}/p/{path}"
        query = ws.scope.get("query_string", b"").decode()
        if query:
            target += "?" + query
        offered = {
            part.strip()
            for part in ws.headers.get("sec-websocket-protocol", "").split(",")
            if part.strip()
        }
        try:
            upstream_ws = await websockets.connect(
                target,
                additional_headers={"Authorization": f"Bearer {token}"},
                subprotocols=["openworker"],
                max_size=16 * 1024 * 1024,
            )
        except Exception:
            await ws.close(code=1013)  # cloud or machine unreachable: retry later
            return
        await ws.accept(subprotocol="openworker" if "openworker" in offered else None)

        async def pump_down() -> None:
            try:
                async for frame in upstream_ws:
                    if isinstance(frame, str):
                        await ws.send_text(frame)
            finally:
                # Upstream ended: close the GUI side too, or its receive loop
                # would sit open on a dead bridge.
                try:
                    await ws.close()
                except Exception:
                    pass

        down = asyncio.create_task(pump_down())
        try:
            while True:
                text = await ws.receive_text()
                await upstream_ws.send(text)
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            down.cancel()
            try:
                await upstream_ws.close()
            except Exception:
                pass
            try:
                await ws.close()
            except Exception:
                pass
