"""Fake Matrix homeserver — minimal Client-Server API for integration tests."""

from __future__ import annotations

import uuid
from typing import Optional

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

BOT_USER = "@bot:fake.local"
DEFAULT_TOKEN = "syt_fake_matrix_token"


class FakeMatrix:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.user_id = BOT_USER
        self.device_id = "FAKE_DEVICE"
        self.token = DEFAULT_TOKEN
        self.next_batch = "s1"
        self.rooms: dict[str, list[dict]] = {}
        self.outbound: list[dict] = []
        self.reactions: list[dict] = []
        self._server: Optional[uvicorn.Server] = None
        self._task = None
        self.app = self._build_app()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _auth(self, request: Request) -> bool:
        auth = request.headers.get("authorization", "")
        return auth == f"Bearer {self.token}"

    def _build_app(self) -> Starlette:
        async def whoami(request: Request) -> JSONResponse:
            if not self._auth(request):
                return JSONResponse({"errcode": "M_UNKNOWN_TOKEN"}, status_code=401)
            return JSONResponse(
                {"user_id": self.user_id, "device_id": self.device_id, "is_guest": False}
            )

        async def sync(request: Request) -> JSONResponse:
            if not self._auth(request):
                return JSONResponse({"errcode": "M_UNKNOWN_TOKEN"}, status_code=401)
            return JSONResponse({"next_batch": self.next_batch, "rooms": {}})

        async def send_event(request: Request) -> JSONResponse:
            room_id = request.path_params["room_id"]
            if not self._auth(request):
                return JSONResponse({"errcode": "M_UNKNOWN_TOKEN"}, status_code=401)
            body = await request.json()
            event_id = f"${uuid.uuid4().hex[:8]}"
            entry = {
                "event_id": event_id,
                "type": request.path_params["event_type"],
                "content": body,
            }
            self.rooms.setdefault(room_id, []).append(entry)
            if request.path_params["event_type"] == "m.reaction":
                self.reactions.append({"room_id": room_id, **entry})
            else:
                self.outbound.append({"room_id": room_id, **entry})
            return JSONResponse({"event_id": event_id})

        async def keys_query(request: Request) -> JSONResponse:
            return JSONResponse({"device_keys": {}, "failures": {}})

        async def control_inject(request: Request) -> JSONResponse:
            body = await request.json()
            room_id = str(body.get("room_id") or "!fake:local")
            self.rooms.setdefault(room_id, []).append(body.get("event") or {})
            return JSONResponse({"ok": True})

        async def control_reset(request: Request) -> JSONResponse:
            self.outbound.clear()
            self.reactions.clear()
            self.rooms.clear()
            return JSONResponse({"ok": True})

        async def control_outbound(request: Request) -> JSONResponse:
            return JSONResponse({"messages": self.outbound, "reactions": self.reactions})

        return Starlette(
            routes=[
                Route("/_matrix/client/v3/account/whoami", whoami, methods=["GET"]),
                Route("/_matrix/client/v3/sync", sync, methods=["GET", "POST"]),
                Route(
                    "/_matrix/client/v3/rooms/{room_id}/send/{event_type}/{txn_id}",
                    send_event,
                    methods=["PUT"],
                ),
                Route("/_matrix/client/v3/keys/query", keys_query, methods=["POST"]),
                Route("/control/inject", control_inject, methods=["POST"]),
                Route("/control/reset", control_reset, methods=["POST"]),
                Route("/control/outbound", control_outbound, methods=["GET"]),
            ]
        )

    async def start(self) -> None:
        config = uvicorn.Config(
            self.app, host=self.host, port=self.port, log_level="warning"
        )
        self._server = uvicorn.Server(config)
        import asyncio

        self._task = asyncio.create_task(self._server.serve())
        for _ in range(50):
            if self._server.started:
                break
            await asyncio.sleep(0.05)
        sockets = self._server.servers[0].sockets if self._server.servers else []
        if sockets:
            self.port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            await self._task
