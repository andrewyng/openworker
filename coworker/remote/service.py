"""The acceptor-only cloud service — the commercial tier's data plane, runnable
today on loopback (remote-home-design.md §Cloud dashboard).

This is the payoff of the acceptor-as-separate-module ruling: import the
acceptor, add a capabilities flag, serve the SAME SPA. No engine, no sessions
of its own, no conversation content (data-path doctrine) — machines join it,
and every session it shows lives on a joined box, reached through the proxy.

What it deliberately does NOT have:
  - a wallet: server-side sealing would put plaintext keys in the cloud's
    memory. Cloud key-deploy waits for BROWSER sealing (§Keys wallet tiers);
    until then the deploy endpoints answer "no wallet configured".
  - multi-tenant auth: the production wrapper adds identity on top; this
    skeleton is single-tenant, gated by the same COWORKER_API_TOKEN header
    scheme as the sidecar so the SPA's auth plumbing is exercised unchanged.
"""

from __future__ import annotations

import argparse
import os
import secrets as pysecrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .acceptor import mount_acceptor
from .audit import AuditLog
from .registry import MachinesRegistry

# The SPA's boot path touches a handful of desktop endpoints before it learns
# the mode. Answer the harmless ones with honest emptiness (a cloud has no
# LOCAL sessions/workspaces/personas) instead of 404 noise.
_EMPTY_STUBS: dict[str, dict[str, Any]] = {
    "/v1/sessions": {"sessions": []},
    "/v1/workspaces/recent": {"workspaces": []},
    "/v1/personas": {"personas": []},
    "/v1/inbox": {"items": []},
    "/v1/subscriptions": {"subscriptions": []},
}


def add_cloud_shims(app: FastAPI) -> None:
    """The desktop-shaped endpoints an acceptor-only deployment must answer
    for the SPA to boot cleanly — shared by this skeleton and the hosted
    machines service, so the two cloud tiers can't drift apart."""

    @app.get("/v1/settings")
    def cloud_settings() -> dict[str, Any]:
        # Minimal: the cloud has no models of its own — every session's models
        # come from its machine (the machine-scoped picker).
        return {
            "model": "",
            "models": [],
            "model_labels": {},
            "model_context_windows": {},
            "model_ready": False,
            "nav_layout": "machine",
            "sessions_peek": 5,
            "context_bar": False,
            "surfaces": {"cowork": True, "chat": False, "code": False},
        }

    for path, payload in _EMPTY_STUBS.items():
        def _stub(payload=payload) -> dict[str, Any]:
            return payload

        app.get(path)(_stub)

    @app.websocket("/ws/events")
    async def cloud_events(ws) -> None:
        # The SPA keeps a global events socket open; a cloud has no local
        # engine emitting events, but refusing the socket puts the SPA in a
        # retry loop (and behind a StaticFiles mount the handshake 500s).
        # Accept and stay silent until the client hangs up. Selecting the
        # offered subprotocol matters: browsers close an unanswered offer.
        offered = {
            part.strip()
            for part in ws.headers.get("sec-websocket-protocol", "").split(",")
            if part.strip()
        }
        await ws.accept(subprotocol="openworker" if "openworker" in offered else None)
        try:
            while True:
                await ws.receive_text()
        except Exception:
            pass


def create_cloud_app(data_dir: str | Path) -> FastAPI:
    base = Path(data_dir).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="openworker-cloud", version="0.0.0")
    api_token = os.environ.get("COWORKER_API_TOKEN", "")

    def _request_authenticated(request: Request) -> bool:
        provided = request.headers.get("x-openworker-token", "")
        return bool(
            api_token and provided and pysecrets.compare_digest(provided, api_token)
        )

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if (
            not api_token
            or request.method == "OPTIONS"
            or request.url.path
            in (
                "/v1/health",
                "/v1/capabilities",
                # Machine-facing device-flow halves; approval stays gated.
                "/v1/remote/device/start",
                "/v1/remote/device/poll",
            )
            or request.url.path.startswith("/j/")
            or _request_authenticated(request)
        ):
            return await call_next(request)
        return JSONResponse({"error": "missing or invalid token"}, status_code=401)

    def _ws_authenticated(ws) -> bool:
        if not api_token:
            return True
        protocols = {
            part.strip()
            for part in ws.headers.get("sec-websocket-protocol", "").split(",")
            if part.strip()
        }
        return any(pysecrets.compare_digest(part, api_token) for part in protocols)

    @app.get("/v1/capabilities")
    def capabilities() -> dict[str, Any]:
        return {"mode": "cloud"}

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        # Shape-compatible with the sidecar's health: the SPA's boot check
        # passes without knowing the mode yet.
        return {"status": "ok", "default_workspace": None, "model": ""}

    add_cloud_shims(app)

    mount_acceptor(
        app,
        MachinesRegistry(base / "cloud.db"),
        ws_authenticated=_ws_authenticated,
        api_token=api_token,
        wallet=None,  # cloud never holds plaintext keys; browser sealing lands later
        audit=AuditLog(base / "remote-audit.jsonl"),
    )
    return app


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="openworker-acceptor",
        description="Acceptor-only cloud service: machines join it; the SPA runs against it in cloud mode.",
    )
    parser.add_argument("--data-dir", default="~/.config/openworker-cloud")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9787)
    parser.add_argument(
        "--spa",
        default=None,
        help="path to the built GUI bundle (surfaces/gui/dist) to serve at /",
    )
    args = parser.parse_args(argv)
    app = create_cloud_app(args.data_dir)
    if args.spa:
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=args.spa, html=True), name="spa")
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, ws_max_size=16 * 2**20)


if __name__ == "__main__":
    main()
