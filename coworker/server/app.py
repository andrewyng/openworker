"""FastAPI app — OpenAI-compatible endpoint + WS session API + REST.

The control plane every surface (GUI/IDE/messaging) rides on. The WS carries the engine
event stream and the approval channel; `/v1/chat/completions` is the OpenAI-compatible
proxy so any OpenAI-format client can use the runtime as a backend.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import secrets
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Origins allowed to talk to the local sidecar. It binds to 127.0.0.1, but a page in the
# user's own browser can still reach loopback — so without an origin gate, any website they
# visit could read `GET /v1/sessions` (CORS was `*`) and drive a session over the WS (which
# CORS never covers) into shell/file tools. We pin to the desktop webview's own origins
# (`tauri://localhost`, Windows' `http(s)://tauri.localhost`) and localhost dev/browser
# builds. Requests with NO Origin header (curl, native clients, tests, server-to-server) are
# allowed — the gate targets browsers, which always attach an unforgeable Origin.
_ALLOWED_ORIGIN_RE = re.compile(
    r"^(tauri://localhost"
    r"|https?://localhost(:\d+)?"
    r"|https?://127\.0\.0\.1(:\d+)?"
    r"|https?://tauri\.localhost)$"
)


def _origin_allowed(origin: str | None) -> bool:
    """True if a browser Origin may use the API. Missing Origin (non-browser) passes."""
    return origin is None or bool(_ALLOWED_ORIGIN_RE.match(origin))


# Caps on inbound WebSocket traffic. The loopback socket is unauthenticated (any local
# process can reach it), so bound frames, messages, and per-connection request rate before
# building model content or starting a turn.
_WS_MAX_FRAME_BYTES = 16 * 1024 * 1024
_WS_RATE_LIMIT_COUNT = 30
_WS_RATE_LIMIT_WINDOW_SECONDS = 10.0
_MAX_MESSAGE_TEXT_CHARS = 200_000
_MAX_ATTACHMENTS_BYTES = 15_000_000  # leaves JSON overhead below the 16 MiB frame cap


def _json_value_size(value: Any) -> int:
    """Conservative UTF-8 size of parsed JSON without allocating another giant string."""
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, dict):
        return sum(_json_value_size(k) + _json_value_size(v) for k, v in value.items())
    if isinstance(value, list):
        return sum(_json_value_size(v) for v in value)
    return 8  # numbers, booleans, null, separators


# Brand colors for the connector badge riding the ✓ (UX-DECISIONS §30). The GUI owns the
# real logos; this page must render offline with zero assets, so a colored initial stands in.
_BRAND_COLORS = {
    "slack": "#4A154B",
    "github": "#24292f",
    "hubspot": "#ff7a59",
    "gmail": "#ea4335",
    "google_calendar": "#4285f4",
}


def _browser_page(
    title: str, detail: str, *, ok: bool = True, error: str = "", connector: str = ""
) -> str:
    """The page shown in the user's browser at the end of a loopback flow (sign-in or
    connector callback) — one branded card (UX-DECISIONS §30): OCW mark, ok/fail icon
    (the connector's initial rides the ✓), the friendly detail, and the raw error
    preserved on failures (it's the debugging breadcrumb). Inline CSS, light/dark via
    prefers-color-scheme, no external assets — it must render offline."""
    import html as _html

    badge = ""
    if ok and connector:
        color = _BRAND_COLORS.get(connector, "#3670b2")
        initial = _html.escape((connector[:1] or "?").upper())
        badge = f'<span class="mini" style="background:{color}">{initial}</span>'
    icon = (
        f'<div class="ico ok">✓{badge}</div>' if ok else '<div class="ico bad">✕</div>'
    )
    err = f'<div class="err">{_html.escape(error)}</div>' if error else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_html.escape(title)} — OpenWorker</title><style>"
        ":root{--paper:#f6f5f2;--panel:#fff;--line:#e4e2dc;--ink:#2c2c2a;--muted:#6f6e68;"
        "--faint:#a3a19a;--accent:#3670b2;--ok:#2e7d4f;--ok-soft:#e3f2e9;--bad:#b3423a;"
        "--bad-soft:#f8e7e5}"
        "@media(prefers-color-scheme:dark){:root{--paper:#191918;--panel:#232322;"
        "--line:#373633;--ink:#e8e6e1;--muted:#9d9b94;--faint:#6b6a64;--accent:#6ba3dd;"
        "--ok:#5cb884;--ok-soft:#20362a;--bad:#d97b74;--bad-soft:#3a2422}}"
        "body{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;"
        "justify-content:center;gap:18px;background:var(--paper);color:var(--ink);"
        'font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:24px}'
        ".card{background:var(--panel);border:1px solid var(--line);border-radius:16px;"
        "padding:34px 32px 28px;max-width:320px;width:100%;text-align:center;"
        "box-shadow:0 10px 30px rgba(0,0,0,.06);box-sizing:border-box}"
        ".mark{display:flex;align-items:center;justify-content:center;gap:7px;margin-bottom:22px;"
        "font-size:13px;font-weight:650}"
        ".mark i{width:20px;height:20px;border-radius:6px;background:var(--accent);"
        "display:inline-block;position:relative}"
        ".mark i::after{content:'';position:absolute;inset:5px;border-radius:2px;"
        "background:conic-gradient(from 0deg,#fff 0 25%,transparent 0 50%,#fff 0 75%,transparent 0)}"
        ".ico{width:52px;height:52px;border-radius:50%;margin:0 auto 14px;display:flex;"
        "align-items:center;justify-content:center;font-size:24px;position:relative}"
        ".ico.ok{background:var(--ok-soft);color:var(--ok)}"
        ".ico.bad{background:var(--bad-soft);color:var(--bad)}"
        ".mini{position:absolute;right:-3px;bottom:-3px;width:22px;height:22px;border-radius:7px;"
        "display:flex;align-items:center;justify-content:center;color:#fff;font-size:10px;"
        "font-weight:700;border:2px solid var(--panel)}"
        "h1{font-size:17px;font-weight:650;margin:0 0 6px;letter-spacing:-.01em}"
        "p{font-size:12.5px;color:var(--muted);margin:0}"
        ".err{font-size:11.5px;color:var(--bad);background:var(--bad-soft);border-radius:8px;"
        "padding:7px 10px;margin-top:12px;text-align:left;word-break:break-word}"
        ".foot{font-size:10.5px;color:var(--faint)}"
        "</style></head><body>"
        '<div class="card"><div class="mark"><i></i>OpenWorker</div>'
        f"{icon}<h1>{_html.escape(title)}</h1><p>{_html.escape(detail)}</p>{err}</div>"
        '<div class="foot">Served locally by OpenWorker on your Mac</div>'
        "</body></html>"
    )


def _connector_title(name: str) -> str:
    """Display name for the loopback page — 'Slack connected', never 'slack connected'."""
    from ..connectors.descriptors import get_descriptor

    d = get_descriptor(name)
    return d.title if d else (name[:1].upper() + name[1:])


_CONNECT_FAILED_DETAIL = (
    "Something went wrong finishing this connection. "
    "Close this tab and try again from OpenWorker."
)

from ..attachments import (
    MAX_ATTACHMENTS as _MAX_ATTACHMENTS,
    MAX_IMAGE_CHARS,
    MAX_PDF_CHARS,
    MAX_TEXT_CHARS,
    build_user_content,
)
from ..engine import ApprovalOutcome
from ..events import EventType
from ..connectors import connector_list
from ..inbox import VIS_INBOX, VIS_INLINE, args_preview
from ..permissions import Mode
from ..providers import AssistantTurn
from .. import toolchain
from ..teams.model import AuthorityError as TeamsAuthorityError
from ..teams.model import BoardError as TeamsBoardError
from ..teams.model import BoardNotFoundError as TeamsBoardNotFoundError
from .manager import SessionManager, _approval_body


def create_app(manager: SessionManager) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            live = (
                await manager.start_gateway()
            )  # start messaging listeners (if configured)
            if live:
                print(f"[coworker] messaging gateway live: {', '.join(live)}")
        except Exception:  # never let a bad connector stop the server
            import traceback

            traceback.print_exc()
        yield
        await manager.aclose()  # stop gateway + close MCP connections on shutdown

    app = FastAPI(title="coworker", version="0.0.0", lifespan=lifespan)
    api_token = os.environ.get("COWORKER_API_TOKEN", "")
    tokenless_paths = {
        "/v1/health",
        "/auth/callback",
        "/mcp/oauth/callback",
        "/oauth/callback",
        # Machine-facing halves of the device-authorization flow (`openworker
        # auth join`): the box holds no sidecar token. Approval stays gated.
        "/v1/remote/device/start",
        "/v1/remote/device/poll",
    }

    def _request_authenticated(request: Request) -> bool:
        provided = request.headers.get("x-openworker-token", "")
        return bool(
            api_token
            and provided
            and secrets.compare_digest(provided, api_token)
        )

    def _websocket_authenticated(ws: WebSocket) -> bool:
        if not api_token:
            return True
        protocols = {
            part.strip()
            for part in ws.headers.get("sec-websocket-protocol", "").split(",")
            if part.strip()
        }
        return any(secrets.compare_digest(part, api_token) for part in protocols)

    @app.middleware("http")
    async def require_sidecar_token(request: Request, call_next):
        # Preflights carry the requested header name, not its value. CORS checks the
        # Origin; the actual state-changing request still must authenticate.
        if (
            not api_token
            or request.method == "OPTIONS"
            or request.url.path in tokenless_paths
            # `/v1/board` carries its own, stronger auth: per-actor board tokens
            # (identity + access), designed to be handed to external harnesses and
            # other machines — which can never hold the machine-local sidecar token.
            or request.url.path.startswith("/v1/board/")
            # Join-URL hint page: read by a human on a remote box (no sidecar
            # token there); serves constant instructions, validates nothing.
            or request.url.path.startswith("/j/")
            or _request_authenticated(request)
        ):
            return await call_next(request)
        return JSONResponse(
            {"error": "missing or invalid OpenWorker sidecar token"},
            status_code=401,
        )

    app.add_middleware(
        CORSMiddleware,
        # Pinned to the desktop webview + localhost (see _ALLOWED_ORIGIN_RE): stops a random
        # website the user visits from reading local API responses cross-origin.
        allow_origin_regex=_ALLOWED_ORIGIN_RE.pattern,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.manager = manager

    @app.get("/v1/capabilities")
    def capabilities() -> dict[str, Any]:
        """Deployment self-description (remote-home-design.md §Cloud dashboard).
        The SAME SPA serves both tiers; this server-driven flag — never a build
        config — is what tells it which surfaces exist. The desktop sidecar is
        a full home; the acceptor-only cloud service answers mode:"cloud"."""
        return {"mode": "desktop"}

    @app.get("/v1/health")
    def health(request: Request) -> dict[str, Any]:
        if api_token and not _request_authenticated(request):
            return {"status": "ok"}
        return {
            "status": "ok",
            "default_workspace": manager.default_workspace,
            "model": manager.model,
        }

    @app.get("/v1/activity")
    def activity() -> dict[str, Any]:
        # Idle signal for a controller that can stop this box (managed
        # sandboxes): turns in flight + when the last one ended.
        return manager.activity()

    @app.get("/v1/agents")
    def agents() -> dict[str, Any]:
        return {"agents": manager.list_agents()}

    @app.get("/v1/personas")
    def personas() -> dict[str, Any]:
        from ..personas.registry import include_unshipped

        # `internal` tells the GUI it may show internal-build affordances (the
        # "Not in this release" group, the Gallery entry point).
        return {"personas": manager.personas_index(), "internal": include_unshipped()}

    @app.get("/v1/inbox")
    def inbox(session_id: str = "", state: str = "") -> dict[str, Any]:
        from dataclasses import asdict
        manager.reconcile_obsolete_prompts(session_id)

        # The cross-session Inbox list shows only Unattended (inbox-visibility) items; a per-session
        # query returns inline ones too, so the answer-in-context card sees parked attended prompts.
        items = manager.inbox.list(
            session_id=session_id or None,
            state=state or None,
            visibility=None if session_id else VIS_INBOX,
        )
        # Enrich with the originating session's context so the Inbox is self-contained — the
        # "go to session" chip needs title/agent/workspace without depending on a (possibly stale)
        # client-side session list, and can link straight to it.
        out: list[dict[str, Any]] = []
        for i in items:
            d = asdict(i)
            rec = manager.session_store.load(i.session_id)
            if (
                rec is None
                and not session_id
                and i.state == "pending"
                and i.session_id not in manager._engines
            ):
                # Lazy cleanup for legacy orphans (sessions deleted before delete_session
                # started closing their items): an orphaned prompt can never be answered.
                # A LIVE engine without a record yet (brand-new session, first turn still
                # running) is NOT an orphan — hence the engine guard.
                manager.inbox.resolve_session(i.session_id)
                continue
            d["session_title"] = (rec.title if rec else None) or i.session_id
            d["session_agent"] = rec.agent if rec else None
            d["session_workspace"] = rec.workspace if rec else None
            d["session_exists"] = rec is not None
            out.append(d)
        return {"items": out}

    @app.post("/v1/inbox/{item_id}/resolve")
    async def resolve_inbox_item(item_id: str, body: dict, request: Request) -> dict[str, Any]:
        # Idempotent + first-responder-wins: ok=False means it was already resolved elsewhere.
        # Routes through resolve_inbox so a restart-orphaned prompt durably resumes its turn.
        # The deciding person: the controller-verified login on a proxied call (the only
        # way that header exists on a joined box), else unknown.
        ok = await manager.resolve_inbox(
            item_id,
            str(body.get("resolution", "deny")),
            by=request.headers.get("x-openworker-actor", ""),
        )
        return {"ok": ok}

    @app.get("/v1/subscriptions")
    def subscriptions() -> dict[str, Any]:
        # Global view-only list: each (session → channel) subscription, enriched with the session's
        # title/agent and the channel its Inbox routes OUT to (so an inbound/outbound collision on
        # the same channel is visible).
        out: list[dict[str, Any]] = []
        for sub in manager.subscriptions.all():
            rec = manager.session_store.load(sub.session_id)
            agent = rec.agent if rec else ""
            routing = manager._routing_targets(sub.session_id, agent or "cowork")
            out.append(
                {
                    "session_id": sub.session_id,
                    "session_title": (rec.title if rec else None) or sub.session_id,
                    "agent": agent,
                    "channel": sub.channel,
                    # Display name from the channel buffer ("#ocw-test"), when any inbound
                    # message has carried one — the address stays the identifier.
                    "channel_name": manager.channel_buffer.name_for(sub.channel),
                    "routing_target": routing[0] if routing else None,
                    "collision": bool(routing and sub.channel in routing),
                }
            )
        return {"subscriptions": out}

    @app.get("/v1/channels/recent")
    def recent_channels() -> dict[str, Any]:
        # The picker's "recently-seen" source: channels the bot has received messages from.
        return {"channels": manager.channel_buffer.channels()}

    @app.get("/v1/unrouted")
    def unrouted() -> dict[str, Any]:
        # Dead-letter view: inbound messages with no destination + background-turn failures.
        return {"items": manager.unrouted.list()}

    @app.post("/v1/subscriptions")
    def subscribe(body: dict) -> dict[str, Any]:
        from ..subscriptions import resolve_channel

        session_id = str(body.get("session_id", "")).strip()
        raw = str(body.get("channel", ""))
        addr = resolve_channel(raw)
        if not session_id or not addr or ":" not in addr:
            if raw.strip().startswith("#"):
                # A bare #name can't be looked up locally — storing it literally would create a
                # subscription that never matches real traffic (resolve_channel returns "").
                return {
                    "ok": False,
                    "error": "Channel names can't be looked up — paste the channel ID "
                    "(channel name ▸ About) or the channel's Copy-link URL.",
                }
            return {"ok": False, "error": "need a session_id and a channel"}
        # Cloud first (spec §3.3): one session across all machines answers a source.
        # A clash comes back as {ok: false, error: "held", held_by, move_allowed};
        # `move: true` takes it over.
        return manager.subscribe_session(session_id, addr, move=bool(body.get("move")))

    @app.post("/v1/subscriptions/remove")
    def unsubscribe(body: dict) -> dict[str, Any]:
        from ..subscriptions import resolve_channel

        session_id = str(body.get("session_id", "")).strip()
        addr = resolve_channel(str(body.get("channel", "")))
        removed = manager.unsubscribe_session(session_id, addr)
        return {"ok": True, "removed": removed}

    @app.get("/v1/inbox/reconcile")
    def reconcile_inbox(session_id: str) -> dict[str, Any]:
        # Called when a session resumes attended control (surface pending + recap inline).
        manager.reconcile_obsolete_prompts(session_id)
        return manager.inbox.reconcile_on_resume(session_id)

    @app.get("/v1/inbox/routing")
    def inbox_routing() -> dict[str, Any]:
        return {"bindings": manager.inbox_routing.bindings()}

    @app.post("/v1/inbox/routing/binding")
    def set_inbox_binding(body: dict) -> dict[str, Any]:
        name = str(body.get("name", "")).strip()
        if not name:
            return {"ok": False, "error": "binding needs a `name`"}
        return manager.set_inbox_binding(
            name,
            channel=body.get("channel") or None,
            target=str(body.get("target", "")),
        )

    @app.get("/v1/sessions/{session_id}/unattended")
    def get_unattended(session_id: str) -> dict[str, Any]:
        return {"unattended": manager.unattended.is_unattended(session_id)}

    @app.get("/v1/sessions/{session_id}/reviewer-stats")
    def get_reviewer_stats(session_id: str) -> dict[str, Any]:
        # Auto-Approve metering (§1.7): checks/verdicts/tokens from the durable audit rows.
        # Drives the composer's "Auto-Approve · N checks" badge and the mode-menu summary.
        return manager.audit_store.reviewer_stats(session_id)

    @app.post("/v1/sessions/{session_id}/unattended")
    def set_unattended(session_id: str, body: dict) -> dict[str, Any]:
        # The GUI gates the on-transition behind a one-tap confirm; the manager records the
        # transition either way, so the change is answerable from the audit store.
        return manager.set_unattended(session_id, bool(body.get("unattended")))

    @app.get("/v1/sessions/{session_id}/skills")
    def session_skills(session_id: str, workspace: str = "") -> dict[str, Any]:
        # The rail's Skills group + the composer popup both read this (SKILLS-SPEC §4.1).
        return manager.session_skills_view(session_id, workspace or None)

    @app.post("/v1/sessions/{session_id}/skills")
    def set_session_skill(session_id: str, body: dict) -> dict[str, Any]:
        # A session mute. `clear` drops the override (inherit again); otherwise explicit
        # on/off. Nothing on disk changes — Settings owns permanent state.
        body = body or {}
        skill = str(body.get("skill", "")).strip()
        if not skill:
            return {"ok": False, "error": "skill required"}
        if body.get("clear"):
            manager.session_skills.clear(session_id, skill)
        else:
            manager.session_skills.set(
                session_id, skill, bool(body.get("enabled", False))
            )
        return manager.session_skills_view(
            session_id, str(body.get("workspace", "")) or None
        )

    @app.get("/v1/sessions/{session_id}/connections")
    def session_connections(session_id: str, persona: str = "") -> dict[str, Any]:
        # `persona` is the GUI's hint for brand-new sessions (no record yet) — without it the
        # view resolves to the default persona and shows the wrong defaults/recommends.
        # §6: the Sources drawer payload — connected connectors w/ state + recommended + ⚠ count.
        return manager.session_connections_view(session_id, persona or None)

    @app.post("/v1/sessions/{session_id}/connections")
    def set_session_connection(session_id: str, body: dict) -> dict[str, Any]:
        # §6: a session override. `clear` drops the override (inherit the persona default again);
        # otherwise set an explicit on/off. Return the refreshed view so the drawer can re-render.
        body = body or {}
        connector = str(body.get("connector", "")).strip()
        if not connector:
            return {"ok": False, "error": "connector required"}
        if body.get("clear"):
            manager.session_connections.clear(session_id, connector)
        else:
            manager.session_connections.set(
                session_id, connector, bool(body.get("enabled", False))
            )
        persona = str(body.get("persona", "")) or None
        return {
            "ok": True,
            "connections": manager.session_connections_view(session_id, persona),
        }

    @app.post("/v1/personas/install")
    def install_persona(body: dict) -> dict[str, Any]:
        # Returns a consent summary per persona; they land disabled pending the user's approval
        # (then POST /v1/personas/{id} {enabled:true, surfaced:true}).
        reg = manager.personas
        try:
            if body.get("git_url"):
                summaries = reg.install_from_git(str(body["git_url"]))
            elif body.get("dir"):
                summaries = reg.install_from_dir(str(body["dir"]))
            elif body.get("zip_b64"):
                # Sharing v1 (OPE-7): a bundle zip — the export format — round-trips
                # through the same dir installer + consent path.
                try:
                    data = base64.b64decode(str(body["zip_b64"]), validate=True)
                except (ValueError, binascii.Error):
                    return {"ok": False, "error": "Invalid archive encoding."}
                summaries = reg.install_from_zip(
                    data, str(body.get("filename", ""))
                )
            elif body.get("gallery_slug"):
                # Gallery install = fetch the manifest markdown from the cloud
                # (sign-in required), verify its hash, then reuse the exact
                # same parser + consent path as a local/Git install. The
                # gallery never changes the trust model: no executable code,
                # lands disabled pending consent.
                import hashlib
                import tempfile

                from .. import cloud
                from ..config import load_config

                slug = str(body["gallery_slug"]).strip()
                manifest = cloud.gallery_manifest(manager.secrets, load_config(), slug)
                if manifest is None:
                    return {
                        "ok": False,
                        "error": "gallery requires cloud sign-in (or the cloud is unreachable)",
                    }
                markdown = manifest.get("manifest_markdown", "")
                digest = "sha256:" + hashlib.sha256(markdown.encode()).hexdigest()
                if (
                    manifest.get("manifest_hash")
                    and manifest["manifest_hash"] != digest
                ):
                    return {"ok": False, "error": "manifest hash mismatch"}
                with tempfile.TemporaryDirectory() as td:
                    (Path(td) / f"{slug}.md").write_text(markdown)
                    summaries = reg.install_from_dir(td)
                cloud.gallery_install_event(manager.secrets, load_config(), slug)
            else:
                return {
                    "ok": False,
                    "error": "provide a `dir`, `git_url`, `zip_b64`, or `gallery_slug`",
                }
        except Exception as e:  # surface manifest/clone errors to the caller
            return {"ok": False, "error": str(e)}
        return {"ok": True, "consent": summaries, "personas": reg.list_all()}

    @app.post("/v1/personas/{persona_id}/export")
    def export_persona(persona_id: str, body: dict) -> dict[str, Any]:
        # Sharing v1 (OPE-7): zip the persona's bundle into the chosen folder. The zip
        # is the import format — send it to a teammate, they import it from the picker.
        return manager.personas.export_persona(
            persona_id, str((body or {}).get("dir", ""))
        )

    @app.get("/v1/cloud/gallery/{slug}")
    def cloud_gallery_detail(slug: str) -> dict[str, Any]:
        """Solo page for one gallery coworker: publisher pitch + capabilities
        derived locally from the manifest (same parser as install)."""
        from .. import cloud
        from ..config import load_config

        body = cloud.gallery_detail(manager.secrets, load_config(), slug)
        if body is None:
            return {"ok": False, "error": "gallery requires cloud sign-in"}
        return body

    @app.get("/v1/cloud/gallery")
    def cloud_gallery() -> dict[str, Any]:
        """Gallery cards for the GUI. Signed out ⇒ ok:false (the gallery is a
        signed-in feature by design; local personas are unaffected)."""
        from .. import cloud
        from ..config import load_config

        body = cloud.gallery_list(manager.secrets, load_config())
        if body is None:
            return {
                "ok": False,
                "error": "gallery requires cloud sign-in",
                "personas": [],
            }
        return {"ok": True, "personas": body.get("personas", [])}

    @app.post("/v1/personas/{persona_id}")
    def update_persona(persona_id: str, body: dict) -> dict[str, Any]:
        reg = manager.personas
        archived = 0
        try:
            if "enabled" in body:
                # Disable archives the persona's sessions atomically (server-side, one
                # request) so any client gets the same semantic. See set_persona_enabled.
                archived = manager.set_persona_enabled(
                    persona_id, bool(body["enabled"])
                )["archived_sessions"]
            if "surfaced" in body:
                reg.set_surfaced(persona_id, bool(body["surfaced"]))
            if body.get("default"):
                reg.set_default(persona_id)
        except KeyError:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        return {"ok": True, "personas": reg.list_all(), "archived_sessions": archived}

    @app.delete("/v1/personas/{persona_id}")
    def persona_delete(persona_id: str) -> dict[str, Any]:
        # Uninstall a non-builtin persona (snapshot dir + lifecycle state). Local
        # operation — works signed out, regardless of where the persona came from.
        try:
            manager.personas.uninstall(persona_id)
        except KeyError:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "personas": manager.personas.list_all()}

    @app.get("/v1/personas/{persona_id}")
    def persona_detail(persona_id: str) -> dict[str, Any]:
        # §5 detail page: identity + capabilities + recommends(+connected) + default connections.
        detail = manager.persona_detail(persona_id)
        if detail is None:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        return detail

    @app.get("/v1/personas/{persona_id}/media/{name}")
    def persona_media(persona_id: str, name: str) -> Any:
        # Screenshots from the persona bundle's media/ folder. The name is confined
        # to that folder: no separators, resolved path must stay inside it.
        from fastapi.responses import FileResponse, Response

        media_dir = manager.personas.media_dir(persona_id)
        if media_dir is None or "/" in name or "\\" in name or name.startswith("."):
            return Response(status_code=404)
        f = (media_dir / name).resolve()
        try:
            inside = f.is_relative_to(media_dir.resolve())
        except AttributeError:  # pragma: no cover — py<3.9 has no is_relative_to
            inside = str(f).startswith(str(media_dir.resolve()))
        if not inside or not f.is_file():
            return Response(status_code=404)
        return FileResponse(f)

    @app.post("/v1/personas/{persona_id}/enable")
    def persona_enable(persona_id: str, body: dict) -> dict[str, Any]:
        # Dedicated §5/§8 route; delegates to the same manager toggle as POST /v1/personas/{id}
        # (so disable archives the persona's sessions here too).
        try:
            manager.set_persona_enabled(
                persona_id, bool((body or {}).get("enabled", True))
            )
        except KeyError:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        return {"ok": True, "personas": manager.personas.list_all()}

    @app.post("/v1/personas/{persona_id}/connections")
    def persona_set_connection(persona_id: str, body: dict) -> dict[str, Any]:
        # §5: flip a persona-default connector on/off; re-reads so the client can refresh.
        body = body or {}
        connector = str(body.get("connector", "")).strip()
        if not connector:
            return {"ok": False, "error": "connector required"}
        return manager.set_persona_connection(
            persona_id, connector, bool(body.get("enabled", False))
        )

    @app.get("/v1/skills")
    def skills(workspace: str = "") -> dict[str, Any]:
        return {"skills": manager.list_skills(workspace or None)}

    @app.post("/v1/skills")
    def create_skill(body: dict) -> dict[str, Any]:
        return manager.create_skill(body or {})

    @app.patch("/v1/skills/{name}")
    def update_skill(name: str, body: dict) -> dict[str, Any]:
        return manager.update_skill(name, body or {})

    @app.delete("/v1/skills/{name}")
    def delete_skill(name: str, workspace: str = "") -> dict[str, Any]:
        return manager.delete_skill(name, workspace or None)

    @app.post("/v1/skills/{name}/move")
    def move_skill(name: str, body: dict) -> dict[str, Any]:
        return manager.move_skill(name, body or {})

    @app.post("/v1/skills/{name}/reveal")
    def reveal_skill(name: str, body: dict) -> dict[str, Any]:
        # §6 "Show folder": open the skill's folder in the OS file manager (local machine).
        return manager.reveal_skill(name, str((body or {}).get("workspace", "")) or None)

    @app.post("/v1/skills/upload")
    def stage_skill_upload(body: dict) -> dict[str, Any]:
        # Stage → preview; nothing is installed until /upload/confirm (SKILLS-SPEC §4.2).
        data_b64 = str((body or {}).get("data_b64", ""))
        if not data_b64:
            return {"ok": False, "error": "No archive supplied."}
        try:
            data = base64.b64decode(data_b64, validate=True)
        except (ValueError, binascii.Error):
            return {"ok": False, "error": "Invalid archive encoding."}
        return manager.stage_skill_upload(data, str((body or {}).get("filename", "")))

    @app.post("/v1/skills/upload/confirm")
    def confirm_skill_upload(body: dict) -> dict[str, Any]:
        return manager.confirm_skill_upload(body or {})

    @app.get("/v1/workspaces/recent")
    def recent_workspaces() -> dict[str, Any]:
        return {"workspaces": manager.recent_workspaces()}

    @app.post("/v1/workspaces/open")
    def open_workspace(body: dict) -> dict[str, Any]:
        return manager.open_workspace(
            body.get("path", ""), create=bool(body.get("create"))
        )

    @app.get("/v1/workspaces/trusted")
    def trusted_workspaces() -> dict[str, Any]:
        return {"workspaces": manager.trusted_workspaces()}

    @app.post("/v1/workspaces/trust")
    def set_workspace_trust(body: dict) -> dict[str, Any]:
        return manager.set_workspace_trust(
            str((body or {}).get("path", "")),
            trusted=bool((body or {}).get("trusted", False)),
        )

    @app.post("/v1/workspaces/temp")
    def provision_temp_workspace(body: dict) -> dict[str, Any]:
        # UX-029: a code-family session starting "in a temporary folder" — created only
        # at send time, with git ready. Knowledge families keep their auto-provisioned dir.
        return manager.provision_temp_workspace(
            str((body or {}).get("session_id", "")),
            git=bool((body or {}).get("git", True)),
        )

    @app.post("/v1/sessions/{session_id}/save-as-project")
    def save_session_as_project(session_id: str, body: dict) -> dict[str, Any]:
        # UX-029 "Save as project…": move the temporary folder somewhere real. The GUI
        # reconnects afterwards so the engine rebinds to the new path.
        return manager.save_temp_as_project(session_id, str((body or {}).get("path", "")))

    @app.post("/v1/workspaces/pick")
    async def pick_workspace() -> dict[str, Any]:
        # Native folder picker opened by the LOCAL sidecar (browser GUIs can't get absolute
        # paths from web file dialogs). Off the event loop: blocks until pick/cancel.
        return await asyncio.to_thread(manager.pick_native_folder)

    @app.get("/v1/sessions")
    def sessions(workspace: str | None = None) -> dict[str, Any]:
        return {"sessions": manager.list_sessions(workspace)}

    @app.get("/v1/sessions/{session_id}/messages")
    def session_messages(session_id: str) -> dict[str, Any]:
        return {"messages": manager.session_messages(session_id)}

    @app.patch("/v1/sessions/{session_id}")
    def session_patch(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        if "pinned" in body or "archived" in body:
            return manager.set_session_flags(
                session_id,
                pinned=bool(body["pinned"]) if "pinned" in body else None,
                archived=bool(body["archived"]) if "archived" in body else None,
            )
        return manager.rename_session(session_id, str(body.get("title", "")))

    @app.delete("/v1/sessions/{session_id}")
    def session_delete(session_id: str) -> dict[str, Any]:
        return manager.delete_session(session_id)

    @app.get("/v1/sessions/{session_id}/roots")
    def session_roots(session_id: str) -> dict[str, Any]:
        return {"roots": manager.get_roots(session_id)}

    @app.post("/v1/sessions/{session_id}/roots")
    def session_add_root(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.add_root(
            session_id, str(body.get("path", "")), bool(body.get("writable", False))
        )

    @app.delete("/v1/sessions/{session_id}/roots")
    def session_remove_root(session_id: str, path: str) -> dict[str, Any]:
        return manager.remove_root(session_id, path)

    @app.get("/v1/sessions/{session_id}/artifacts")
    def session_artifacts(session_id: str) -> dict[str, Any]:
        return {"artifacts": manager.list_artifacts(session_id)}

    @app.get("/v1/sessions/{session_id}/artifacts/read")
    def session_artifact_read(session_id: str, path: str) -> dict[str, Any]:
        return manager.read_artifact(session_id, path)

    @app.post("/v1/sessions/{session_id}/artifacts/reveal")
    def session_artifact_reveal(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.reveal_artifact(
            session_id, str(body.get("path", "")), str(body.get("mode", "reveal"))
        )

    # Agent teams (OPE-96): the session's board (workspace-keyed space) + journal
    # overview. Mutations act as the USER — the human side of the gates.
    @app.get("/v1/sessions/{session_id}/board")
    def session_board(session_id: str) -> dict[str, Any]:
        return manager.session_board(session_id)

    @app.get("/v1/sessions/{session_id}/board/item")
    def session_board_item(session_id: str, id: int) -> dict[str, Any]:
        return manager.board_item_detail(session_id, int(id))

    @app.get("/v1/sessions/{session_id}/board/attachment")
    def session_board_attachment(session_id: str, name: str):
        from fastapi.responses import Response

        try:
            data, mime = manager.board_attachment(session_id, name)
        except TeamsBoardError as error:
            return JSONResponse({"error": str(error)}, status_code=404)
        return Response(
            content=data,
            media_type=mime,
            headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
        )

    @app.post("/v1/sessions/{session_id}/board/comment")
    def session_board_comment(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.board_comment(
            session_id, int(body.get("item", 0)), str(body.get("body", ""))
        )

    @app.post("/v1/sessions/{session_id}/board/transition")
    def session_board_transition(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.board_transition(
            session_id,
            int(body.get("item", 0)),
            str(body.get("to", "")),
            comment=str(body.get("comment", "")),
        )

    @app.get("/v1/teams/{team_id}/summary")
    def team_summary(team_id: str):
        result = manager.team_summary(team_id)
        return JSONResponse(result, status_code=404 if "error" in result else 200)

    @app.get("/v1/teams/{team_id}/chat")
    def team_chat(team_id: str) -> dict[str, Any]:
        return manager.team_chat(team_id)

    @app.post("/v1/teams/{team_id}/chat")
    def team_chat_post(team_id: str, body: dict) -> dict[str, Any]:
        return manager.post_team_chat(team_id, str((body or {}).get("text", "")))

    @app.get("/v1/teams/journal")
    def teams_journal() -> dict[str, Any]:
        return {"cases": manager.journal_overview()}

    # ---- The open board surface (OPE-100): token-authenticated `/v1/board` API.
    # Identity is the TOKEN (actor+role bound at mint, resolved per request, never
    # client-asserted); authority is the STORE — the same double gate in-app agents
    # get. This is the one wire protocol every external front door rides:
    # RemoteDialect (the `ocw` CLI, the team-board MCP server, headless instances)
    # today, a hosted board service later. Tokens are required even on loopback —
    # they carry identity, not just access.

    def _board_actor(request: Request):
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        return manager.board_tokens.resolve(token)

    def _board(request: Request, handler):
        actor = _board_actor(request)
        if actor is None:
            return JSONResponse(
                {"error": "board token required (Authorization: Bearer …) — mint"
                          " one with `ocw board token` on the serving machine"},
                status_code=401,
            )
        try:
            return handler(actor)
        except TeamsBoardNotFoundError as error:
            return JSONResponse({"error": str(error)}, status_code=404)
        except TeamsAuthorityError as error:
            return JSONResponse({"error": str(error)}, status_code=403)
        except (TeamsBoardError, ValueError) as error:
            return JSONResponse({"error": str(error)}, status_code=400)

    @app.get("/v1/board/whoami")
    def board_whoami(request: Request):
        return _board(
            request, lambda actor: {"actor": actor.id, "role": actor.role.value}
        )

    @app.get("/v1/board/spaces")
    def board_spaces(request: Request):
        return _board(request, lambda actor: {"spaces": manager.team_store.spaces()})

    @app.get("/v1/board/items")
    def board_list_items(
        request: Request, space: str, state: str = "", assignee: str = ""
    ):
        return _board(
            request,
            lambda actor: {
                "items": manager.team_store.list_items(
                    space, actor, state=state or None, assignee=assignee or None
                )
            },
        )

    @app.get("/v1/board/item")
    def board_get_item(request: Request, space: str, id: int):
        return _board(
            request,
            lambda actor: manager.team_store.get_item(space, int(id), actor=actor),
        )

    @app.get("/v1/board/comments")
    def board_comments(request: Request, space: str, id: int, after_seq: int = 0, limit: int = 20):
        return _board(request, lambda actor: manager.team_store.comment_page(
            space, id, actor=actor, after_seq=after_seq, limit=limit))

    @app.get("/v1/board/comment")
    def board_comment_text(request: Request, space: str, id: int, seq: int,
                           offset: int = 0, max_chars: int = 12000):
        return _board(request, lambda actor: manager.team_store.comment_text(
            space, id, actor=actor, seq=seq, offset=offset, max_chars=max_chars))

    @app.post("/v1/board/items")
    def board_create_item(request: Request, body: dict):
        body = body or {}

        def run(actor):
            item = manager.team_store.create_item(
                str(body.get("space", "")),
                actor,
                title=str(body.get("title", "")),
                criteria=str(body.get("criteria", "")),
                description=str(body.get("description", "")),
                parent=(
                    int(body["parent"]) if body.get("parent") is not None else None
                ),
                case=str(body.get("case") or "") or None,
            )
            manager.kick_team_tick()  # a new filing is lead-subscription news
            return item

        return _board(request, run)

    @app.post("/v1/board/items/transition")
    def board_transition_item(request: Request, body: dict):
        body = body or {}

        def run(actor):
            item = manager.team_store.transition(
                str(body.get("space", "")),
                actor,
                int(body.get("id", 0)),
                str(body.get("to", "")),
                comment=str(body.get("comment", "")),
                refs=[str(ref) for ref in body.get("refs") or []],
            )
            manager.kick_team_tick()  # review/blocked should reach the lead now
            return item

        return _board(request, run)

    @app.post("/v1/board/items/comment")
    def board_comment_item(request: Request, body: dict):
        body = body or {}
        def run(actor):
            result = manager.team_store.comment(
                str(body.get("space", "")),
                actor,
                int(body.get("id", 0)),
                str(body.get("body", "")),
                refs=[str(ref) for ref in body.get("refs") or []],
                needs_attention=body.get("needs_attention", False),
            )
            manager.kick_team_tick()
            return result
        return _board(request, run)

    @app.post("/v1/board/items/assign")
    def board_assign_item(request: Request, body: dict):
        body = body or {}

        def run(actor):
            item = manager.team_store.assign(
                str(body.get("space", "")),
                actor,
                int(body.get("id", 0)),
                str(body.get("assignee", "")),
            )
            manager.kick_team_tick()  # the assignee's queue has news
            return item

        return _board(request, run)

    @app.post("/v1/board/items/status")
    def board_set_status(request: Request, body: dict):
        return _board(request, lambda actor: manager.team_store.set_status(
            str(body.get("space", "")), actor, body.get("id"), body.get("text")
        ))  # Display-only: deliberately no team tick.

    @app.post("/v1/board/items/claim")
    def board_claim_item(request: Request, body: dict):
        body = body or {}

        def run(actor):
            item = manager.team_store.claim(
                str(body.get("space", "")), actor, int(body.get("id", 0))
            )
            manager.kick_team_tick()  # claims land in the lead's feed
            return item

        return _board(request, run)

    @app.post("/v1/board/link")
    def board_link_items(request: Request, body: dict):
        body = body or {}
        return _board(
            request,
            lambda actor: manager.team_store.link(
                str(body.get("space", "")),
                actor,
                int(body.get("src", 0)),
                str(body.get("kind", "")),
                int(body.get("dst", 0)),
            ),
        )

    @app.post("/v1/board/items/attach")
    def board_attach(request: Request, body: dict):
        body = body or {}

        def run(actor):
            manager.team_store.require_attachment_write(
                str(body.get("space", "")), actor, int(body.get("id", 0))
            )
            raw = str(body.get("data_b64", ""))
            # Cheap pre-decode bound: base64 is ~4/3 of the payload, so anything
            # multiples over the cap is refused before allocating the decode.
            if len(raw) > 15 * 1024 * 1024:
                return JSONResponse(
                    {"error": "attachment exceeds 10MB"}, status_code=400
                )
            try:
                data = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError):
                return JSONResponse(
                    {"error": "data_b64 is not valid base64"}, status_code=400
                )
            ref = manager.attachment_store.put(
                data, str(body.get("filename", ""))
            )
            filename = str(body.get("filename", ""))
            event = manager.team_store.attach_ref(
                str(body.get("space", "")),
                actor,
                int(body.get("id", 0)),
                str(body.get("caption", "")) or f"attached {filename}",
                ref,
            )
            return {"ref": ref, "seq": event["seq"]}

        return _board(request, run)

    @app.get("/v1/board/attachment")
    def board_attachment(request: Request, name: str, space: str):
        def run(actor):
            from fastapi.responses import Response

            manager.team_store.require_attachment_access(space, actor, name)
            path = manager.attachment_store.path_for(name)
            return Response(
                content=path.read_bytes(),
                media_type=manager.attachment_store.mime_for(name),
                headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
            )

        return _board(request, run)

    @app.get("/v1/board/policy")
    def board_get_policy(request: Request, space: str):
        return _board(request, lambda actor: manager.team_store.policy(space))

    @app.post("/v1/board/policy")
    def board_set_policy(request: Request, body: dict):
        body = body or {}
        return _board(
            request,
            lambda actor: manager.team_store.set_policy(
                str(body.get("space", "")), actor, claims=str(body.get("claims", ""))
            ),
        )

    @app.get("/v1/board/pending")
    def board_pending(request: Request, space: str, limit: int = 200):
        # The actor's FEED: events on its slice since its cursor — interest
        # follows the assignment relation, same projection in-app workers use.
        return _board(
            request,
            lambda actor: {
                "events": manager.team_store.feed_for(
                    space, actor.id, limit=int(limit)
                )
            },
        )

    @app.post("/v1/board/consume")
    def board_consume(request: Request, body: dict):
        body = body or {}

        def run(actor):
            manager.team_store.consume_feed(
                str(body.get("space", "")), actor.id, int(body.get("upto_seq", 0))
            )
            return {"ok": True}

        return _board(request, run)

    @app.get("/v1/board/journal/cases")
    def board_journal_cases(request: Request):
        return _board(
            request, lambda actor: {"cases": manager.journal_store.overview(actor)}
        )

    @app.get("/v1/board/journal")
    def board_journal_read(
        request: Request,
        case: str,
        item: Optional[int] = None,
        author: str = "",
        kind: str = "",
        entity: str = "",
        include_raw: str = "",
        limit: int = 100,
    ):
        return _board(
            request,
            lambda actor: {
                "entries": manager.journal_store.read(
                    actor,
                    case,
                    item=item,
                    author=author or None,
                    kind=kind or None,
                    entity=entity or None,
                    include_raw=bool(include_raw),
                    limit=int(limit),
                )
            },
        )

    @app.post("/v1/board/journal")
    def board_journal_append(request: Request, body: dict):
        body = body or {}
        return _board(
            request,
            lambda actor: manager.journal_store.append(
                actor,
                str(body.get("case", "")),
                str(body.get("body", "")),
                kind=str(body.get("kind") or "note"),
                space=str(body.get("space") or "") or None,
                item=int(body["item"]) if body.get("item") is not None else None,
                entities=[str(e) for e in body.get("entities") or []],
                refs=[str(ref) for ref in body.get("refs") or []],
            ),
        )

    @app.get("/v1/memory")
    def memory() -> dict[str, Any]:
        return {"memory": manager.list_memory()}

    @app.post("/v1/memory")
    def add_memory(body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.add_memory(
            str(body.get("content", "")), str(body.get("scope", "workspace"))
        )

    # Declared before the /{item_id} routes so "settings" can never be parsed as an id.
    @app.get("/v1/memory/settings")
    def memory_settings() -> dict[str, Any]:
        return manager.get_memory_settings()

    @app.put("/v1/memory/settings")
    def memory_settings_put(body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.set_memory_settings(
            enabled=bool(body["enabled"]) if "enabled" in body else None,
            user_rules=str(body["user_rules"]) if "user_rules" in body else None,
        )

    @app.patch("/v1/memory/{item_id}")
    def memory_patch(item_id: int, body: dict) -> dict[str, Any]:
        return manager.update_memory(item_id, str((body or {}).get("content", "")))

    @app.delete("/v1/memory/{item_id}")
    def memory_delete(item_id: int) -> dict[str, Any]:
        return manager.delete_memory(item_id)

    @app.delete("/v1/memory")
    def memory_delete_all() -> dict[str, Any]:
        return manager.delete_all_memory()

    # -- project bindings (pass 20 / UX-044) --------------------------------------

    @app.get("/v1/sessions/{session_id}/project-menu")
    def project_menu(session_id: str, kind: str = "memory") -> dict[str, Any]:
        return manager.project_menu(session_id, kind)

    @app.put("/v1/sessions/{session_id}/bindings")
    def put_binding(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        name = body.get("name")
        return manager.set_binding(
            session_id, str(body.get("kind", "")), str(name) if name else None
        )

    @app.post("/v1/sessions/{session_id}/project-name")
    def name_project(session_id: str, body: dict) -> dict[str, Any]:
        body = body or {}
        return manager.name_current_project(
            session_id, str(body.get("kind", "")), str(body.get("name", ""))
        )

    @app.post("/v1/chat/completions")
    def chat_completions(body: dict) -> dict[str, Any]:
        model = body.get("model", manager.model)
        turn = manager.provider_complete(
            model, body.get("messages", []), body.get("tools")
        )
        return _openai_response(model, turn)

    # -- MCP servers ------------------------------------------------------------
    @app.get("/v1/mcp")
    def mcp_list() -> dict[str, Any]:
        return {"servers": manager.list_mcp()}

    @app.post("/v1/mcp")
    def mcp_add(body: dict) -> dict[str, Any]:
        name = body.get("name")
        config = body.get("config")
        if not name or not isinstance(config, dict):
            return {"ok": False, "error": "name and config required"}
        return manager.add_mcp(name, config)

    @app.patch("/v1/mcp/{name}")
    def mcp_patch(name: str, body: dict) -> dict[str, Any]:
        return manager.patch_mcp(name, body or {})

    @app.delete("/v1/mcp/{name}")
    def mcp_delete(name: str) -> dict[str, Any]:
        return manager.delete_mcp(name)

    @app.post("/v1/mcp/config/reveal")
    def mcp_config_reveal() -> dict[str, Any]:
        return manager.reveal_mcp_config()

    @app.get("/v1/mcp/{name}/tools")
    async def mcp_tools(name: str) -> dict[str, Any]:
        return await manager.mcp_tools(name)

    # OPE-136 §4/§5: the server detail page's trust surface — which tools carry a
    # standing "don't ask" rule, revoke one, and the one-click migration off the
    # legacy server-wide requires_approval flag.
    @app.get("/v1/mcp/{name}/trust")
    def mcp_trust(name: str) -> dict[str, Any]:
        return manager.mcp_trust(name)

    @app.delete("/v1/mcp/{name}/trust/{tool}")
    def mcp_trust_revoke(name: str, tool: str) -> dict[str, Any]:
        return manager.revoke_mcp_trust(name, tool)

    @app.post("/v1/mcp/{name}/trust/convert")
    async def mcp_trust_convert(name: str) -> dict[str, Any]:
        return await manager.convert_mcp_trust(name)

    @app.post("/v1/mcp/{name}/connect")
    async def mcp_connect(name: str) -> dict[str, Any]:
        # Connect now. For `auth: oauth` servers the first connect opens the system
        # browser and waits on the loopback callback — that can take minutes, so it
        # runs as a background task; the GUI polls /v1/mcp for the status flip
        # (authorizing → connected | needs_auth + last_error).
        manager.begin_mcp_connect(name)  # authorizing shows on the very next poll
        asyncio.create_task(manager.connect_mcp(name))
        return {"ok": True, "started": True}

    @app.post("/v1/mcp/{name}/signout")
    async def mcp_signout(name: str) -> dict[str, Any]:
        return await manager.signout_mcp(name)

    @app.get("/mcp/oauth/callback")
    async def mcp_oauth_callback(
        code: str = "", state: str = "", error: str = ""
    ) -> Any:
        # Loopback landing for the MCP OAuth browser flow (mcp/oauth.py). Browser-facing:
        # returns the same styled page as the managed-connector callbacks.
        from fastapi.responses import HTMLResponse

        from ..mcp import oauth as mcp_oauth

        if error:
            return HTMLResponse(
                _browser_page(
                    "Sign-in failed",
                    "The service reported an error. Return to OpenWorker and try again.",
                    ok=False,
                    error=error,
                ),
                status_code=400,
            )
        if not code or not mcp_oauth.deliver_callback(code, state or None):
            return HTMLResponse(
                _browser_page(
                    "Nothing waiting for this sign-in",
                    "The sign-in may have timed out. Return to OpenWorker and start it again.",
                    ok=False,
                ),
                status_code=400,
            )
        return HTMLResponse(
            _browser_page(
                "Connected",
                "Sign-in complete. You can close this tab and return to OpenWorker.",
                ok=True,
            )
        )

    @app.post("/v1/mcp/reload")
    async def mcp_reload() -> dict[str, Any]:
        return await manager.reload_mcp()

    # -- connectors (Slack / Telegram / …) --------------------------------------
    @app.get("/v1/connectors")
    def connectors_list() -> dict[str, Any]:
        return {"connectors": manager.list_connectors()}

    async def _refresh_listeners_if_two_way(name: str) -> None:
        # New/removed creds only take effect when the platform socket reconnects (Socket Mode
        # authenticates at connect time) — hot-reload the listeners in-process so pasting
        # tokens works immediately, no sidecar restart (§19).
        from ..connectors.config import PLATFORMS

        if name in PLATFORMS:
            try:
                await manager.refresh_gateway()
            except Exception:
                pass  # a listener that fails to come up must not fail the save

    @app.post("/v1/connectors/{name}/connect")
    async def connector_connect(name: str, body: dict) -> dict[str, Any]:
        fields = body.get("fields") if isinstance(body, dict) else None
        # experimental connectors require the caller to explicitly acknowledge the risk notice
        acknowledged = bool(isinstance(body, dict) and body.get("acknowledge_risk"))
        # token validation does a blocking HTTP call → keep it off the event loop
        result = await asyncio.to_thread(
            lambda: manager.connect_connector(
                name, fields or {}, acknowledged=acknowledged
            )
        )
        if result.get("ok"):
            await _refresh_listeners_if_two_way(name)
        return result

    @app.post("/v1/connectors/{name}/mcp-connect")
    async def connector_mcp_connect(name: str) -> dict[str, Any]:
        # One-click connect for an MCP-backed connector: the browser OAuth flow can
        # take minutes, so it runs in the background; the GUI polls /v1/connectors
        # until the card flips to connected (mode "mcp").
        from ..connectors.descriptors import get_descriptor

        d = get_descriptor(name)
        if d is None or not d.mcp_url:
            return {"ok": False, "error": f"{name} has no MCP connect path"}
        asyncio.create_task(manager.mcp_connect_connector(name))
        return {"ok": True, "started": True}

    @app.get("/v1/connectors/{name}/handoff-info")
    def connector_handoff_info_route(name: str) -> dict[str, Any]:
        """Grant handoff (spec §Remote OAuth): whether this connector's grant
        can move to a machine, and which profile keys the move carries. The
        GUI then wallet-sends those names (sealed, existing path) and
        disconnects here — one grant, one holder."""
        from ..connectors.setup import connector_handoff_info

        return connector_handoff_info(manager.secrets, name)

    @app.post("/v1/connectors/{name}/delegate")
    async def connector_delegate(name: str, body: Optional[dict] = None) -> dict[str, Any]:
        """Handoff step for managed grants: mark each of this connector's
        managed connections machine-held at the broker, and stamp the
        broker's opaque user_id into the profile — it travels with the grant
        so the machine can renew by possession.

        With the target machine's `seal_pubkey` in the body, the broker also
        mints the machine credential (spec §Managed events) — stamped
        alongside. Delegation is ONE call per connection_id: several profiles
        can share a connection (every Slack workspace does), and a second
        call would rotate the credential the first one minted."""
        from .. import cloud
        from ..config import load_config
        from ..connectors.setup import connector_profile_keys

        cfg = load_config()
        seal_pubkey = str((body or {}).get("seal_pubkey") or "")
        # Hosted machines only (spec §Fly sandboxes): the id the machines
        # service knows, so the broker can wake a sleeping sandbox on events.
        machine_id = str((body or {}).get("machine_id") or "")
        grants: dict[str, dict[str, str]] = {}  # connection_id → delegate result
        delegated: list[str] = []
        failed: list[str] = []
        for key in connector_profile_keys(manager.secrets, name):
            # The slack/github pointers hold no grant of their own — they're
            # stamped after the loop from the minted grant.
            if name in ("slack", "github") and key == f"{name}:default":
                continue
            profile = manager.secrets.get(key) or {}
            if not profile.get("managed"):
                continue
            connection_id = str(profile.get("connection_id") or "")
            if not connection_id:
                failed.append(key)
                continue
            if connection_id not in grants:
                grant = await asyncio.to_thread(
                    lambda cid=connection_id: cloud.delegate_connection(
                        manager.secrets, cfg, cid, seal_pubkey=seal_pubkey, machine_id=machine_id
                    )
                )
                if not grant:
                    failed.append(key)
                    continue
                grants[connection_id] = grant
            profile["broker_user_id"] = grants[connection_id]["user_id"]
            if grants[connection_id].get("machine_credential"):
                profile["machine_credential"] = grants[connection_id]["machine_credential"]
            manager.secrets.put(key, profile)
            delegated.append(key)
        # The poll adapter (slack) and the delegated mint (github) read the
        # pointer profile, which carries no connection_id of its own — stamp
        # it with the (single) grant.
        if name in ("slack", "github") and grants:
            connection_id, grant = next(iter(grants.items()))
            pointer = manager.secrets.get(f"{name}:default") or {}
            pointer["connection_id"] = connection_id
            pointer["broker_user_id"] = grant["user_id"]
            if grant.get("machine_credential"):
                pointer["machine_credential"] = grant["machine_credential"]
            manager.secrets.put(f"{name}:default", pointer)
        if failed:
            return {
                "ok": False,
                "error": "could not delegate: " + ", ".join(sorted(failed)),
                "delegated": delegated,
            }
        return {"ok": True, "delegated": delegated}

    # -- broker views for the desktop GUI (UX-049 4c): the dashboard calls the
    # broker directly with the user's token; the desktop goes through here so
    # the token never leaves the sidecar. Explicit paths, no generic proxy.
    async def _broker(method: str, path: str, body: Optional[dict] = None) -> Any:
        from .. import cloud
        from ..config import load_config

        status, data = await asyncio.to_thread(
            cloud.broker_request, manager.secrets, load_config(), method, path, body
        )
        if status == 0:
            return JSONResponse({"error": "cloud unreachable"}, status_code=502)
        return JSONResponse(data, status_code=status)

    @app.get("/v1/cloud/connections")
    async def cloud_connections() -> Any:
        return await _broker("GET", "/v1/connections")

    @app.post("/v1/cloud/connections/{connection_id}/delegate")
    async def cloud_connection_delegate(connection_id: str, body: dict) -> Any:
        return await _broker("POST", f"/v1/connections/{connection_id}/delegate", body or {})

    @app.post("/v1/cloud/connections/{connection_id}/default-machine")
    async def cloud_connection_default_machine(connection_id: str, body: dict) -> Any:
        return await _broker("POST", f"/v1/connections/{connection_id}/default-machine", body or {})

    @app.delete("/v1/cloud/connections/{connection_id}/holders/{machine_id}")
    async def cloud_connection_forget_holder(connection_id: str, machine_id: str) -> Any:
        return await _broker("DELETE", f"/v1/connections/{connection_id}/holders/{machine_id}")

    @app.get("/v1/cloud/subscriptions")
    async def cloud_subscriptions(connector: str = "") -> Any:
        return await _broker("GET", "/v1/subscriptions" + (f"?connector={connector}" if connector else ""))

    @app.post("/v1/cloud/subscriptions/remove")
    async def cloud_subscriptions_remove(body: dict) -> Any:
        return await _broker("POST", "/v1/subscriptions/remove", body or {})

    @app.get("/v1/cloud/people")
    async def cloud_people(connector: str = "", scope: str = "") -> Any:
        q = "&".join(p for p in (f"connector={connector}" if connector else "", f"scope={scope}" if scope else "") if p)
        return await _broker("GET", "/v1/people" + (f"?{q}" if q else ""))

    @app.post("/v1/cloud/people")
    async def cloud_people_add(body: dict) -> Any:
        return await _broker("POST", "/v1/people", body or {})

    @app.post("/v1/cloud/people/remove")
    async def cloud_people_remove(body: dict) -> Any:
        return await _broker("POST", "/v1/people/remove", body or {})

    # Configurations (connectors spec §10): the GitHub glance page's rows.
    @app.get("/v1/cloud/configurations")
    async def cloud_configurations(connector: str = "github") -> Any:
        return await _broker("GET", f"/v1/configurations?connector={connector}")

    @app.post("/v1/cloud/configurations")
    async def cloud_configurations_add(body: dict) -> Any:
        return await _broker("POST", "/v1/configurations", body or {})

    @app.post("/v1/cloud/configurations/{config_id}/edit")
    async def cloud_configurations_edit(config_id: str, body: dict) -> Any:
        return await _broker("POST", f"/v1/configurations/{config_id}/edit", body or {})

    @app.delete("/v1/cloud/configurations/{config_id}")
    async def cloud_configurations_delete(config_id: str) -> Any:
        return await _broker("DELETE", f"/v1/configurations/{config_id}")

    @app.post("/v1/cloud/connections/{name}/revoke")
    async def cloud_revoke_connections(name: str) -> dict[str, Any]:
        """Revoke a connector's broker connections by NAME (machines spec
        §Managed events, drill finding): the completion of a machine-scope
        disconnect for a MANAGED grant. The box deletes its copy over the
        proxy; this — from the desktop, which holds the session — kills the
        delegation at the broker so events stop queueing for a machine that
        no longer listens. Best-effort and idempotent."""
        from .. import cloud
        from ..config import load_config

        revoked = await asyncio.to_thread(
            lambda: cloud.revoke_connector_connections(
                manager.secrets, load_config(), name
            )
        )
        return {"ok": True, "revoked": revoked}

    @app.post("/v1/connectors/{name}/handoff-sealed")
    async def connector_handoff_sealed(name: str, body: dict) -> dict[str, Any]:
        """Enable on another machine FROM this one (UX-049, spec §2): this
        engine's profiles for the connector, stamped with the grant the
        caller obtained for the target (its broker user id + machine
        credential), sealed to the target's pinned key. The caller relays the
        ciphertext to the target's deploy route; no plaintext ever leaves
        this process. Nothing is forgotten here — Enable is a copy."""
        from ..connectors.setup import connector_handoff_info, connector_profile_keys
        from ..remote.identity import seal_b64

        seal_pubkey = str((body or {}).get("seal_pubkey") or "")
        grant = dict((body or {}).get("grant") or {})
        if not seal_pubkey:
            return {"ok": False, "error": "seal_pubkey required"}
        info = connector_handoff_info(manager.secrets, name)
        if not info.get("ok") or not info.get("portable"):
            return {"ok": False, "error": info.get("error") or f"{name} can't be copied to a machine", "reason": info.get("reason", "")}
        if info.get("needs_delegation") and not (grant.get("user_id") and grant.get("machine_credential")):
            return {"ok": False, "error": "a delegated grant for the target machine is required"}
        profiles: dict[str, Any] = {}
        for key in connector_profile_keys(manager.secrets, name):
            data = dict(manager.secrets.get(key) or {})
            if data.get("managed") or (name in ("slack", "github") and key == f"{name}:default"):
                if grant.get("user_id"):
                    data["broker_user_id"] = grant["user_id"]
                if grant.get("machine_credential"):
                    data["machine_credential"] = grant["machine_credential"]
            profiles[key] = data
        sealed = seal_b64(seal_pubkey, json.dumps({"profiles": profiles}).encode())
        return {"ok": True, "sealed_b64": sealed, "profiles": sorted(profiles)}

    @app.post("/v1/connectors/{name}/forget-local")
    async def connector_forget_local(name: str) -> dict[str, Any]:
        """The handoff's forget step: delete local profiles WITHOUT telling
        the broker — the connection is not ending, it MOVED, and a broker
        disconnect would revoke the delegation the machine now lives on."""
        from ..connectors.setup import disconnect_connector as _local_disconnect

        return await asyncio.to_thread(_local_disconnect, manager.secrets, name)

    @app.post("/v1/connectors/{name}/disconnect")
    async def connector_disconnect(name: str) -> dict[str, Any]:
        # Managed profiles: best-effort flip of the cloud metadata record first
        # (network call → off the loop). Local deletion always proceeds.
        from .. import cloud
        from ..config import load_config

        await asyncio.to_thread(
            lambda: cloud.cloud_disconnect(manager.secrets, load_config(), name)
        )
        result = manager.disconnect_connector(name)
        await _refresh_listeners_if_two_way(name)
        return result

    @app.post("/v1/connectors/slack/workspaces/{team_id}/disconnect")
    async def slack_workspace_disconnect(team_id: str) -> dict[str, Any]:
        """Stop relaying one workspace (managed relay). Cloud routing row deleted
        best-effort, local per-team token removed, gateway hot-reloaded."""
        return await manager.disconnect_slack_workspace(team_id)

    @app.get("/v1/connectors/slack/status")
    async def slack_status() -> dict[str, Any]:
        """Slack health, three layers: relay socket / cloud sign-in / per-team tokens."""
        return manager.slack_status()

    @app.post("/v1/connectors/github/installations/{installation_id}/disconnect")
    async def github_installation_disconnect(installation_id: str) -> dict[str, Any]:
        """Stop relaying one GitHub App installation (managed relay). Cloud
        routing rows deleted best-effort, local profile removed, gateway
        hot-reloaded."""
        return await manager.disconnect_github_installation(installation_id)

    @app.get("/v1/connectors/github/status")
    async def github_status() -> dict[str, Any]:
        """GitHub health: relay socket / cloud sign-in / per-installation tokens."""
        return manager.github_status()

    @app.post("/v1/connectors/gmail/accounts/{email}/disconnect")
    async def gmail_account_disconnect(email: str) -> dict[str, Any]:
        """Drop ONE mailbox (cloud metadata best-effort first, like a full
        disconnect); the default pointer moves to the next account."""
        from .. import cloud
        from ..config import load_config
        from ..connectors import gmail_accounts

        profile_key = gmail_accounts.PREFIX + email.strip().lower()
        await asyncio.to_thread(
            lambda: cloud.cloud_disconnect(
                manager.secrets, load_config(), "gmail", profile_key=profile_key
            )
        )
        return gmail_accounts.disconnect_account(manager.secrets, email)

    @app.post("/v1/connectors/gmail/accounts/{email}/default")
    def gmail_account_default(email: str) -> dict[str, Any]:
        from ..connectors import gmail_accounts

        return gmail_accounts.set_default(manager.secrets, email)

    @app.patch("/v1/connectors/gmail/filters")
    def gmail_filters(body: dict) -> dict[str, Any]:
        """Replace the "Never show agents" lists. Enforced in the local tool
        layer; agents see silent omissions, the user sees counts + audit."""
        from ..connectors import gmail_accounts

        senders = body.get("senders") if isinstance(body, dict) else None
        labels = body.get("labels") if isinstance(body, dict) else None
        if senders is not None and not isinstance(senders, list):
            return {"ok": False, "error": "senders must be a list"}
        if labels is not None and not isinstance(labels, list):
            return {"ok": False, "error": "labels must be a list"}
        return gmail_accounts.set_filters(manager.secrets, senders, labels)

    @app.post("/v1/connectors/google_calendar/accounts/{email}/disconnect")
    async def gcal_account_disconnect(email: str) -> dict[str, Any]:
        """Drop ONE Google Calendar account (cloud metadata best-effort first);
        the default pointer moves to the next account."""
        from .. import cloud
        from ..config import load_config
        from ..connectors import gcal_accounts

        profile_key = gcal_accounts.PREFIX + email.strip().lower()
        await asyncio.to_thread(
            lambda: cloud.cloud_disconnect(
                manager.secrets,
                load_config(),
                "google_calendar",
                profile_key=profile_key,
            )
        )
        return gcal_accounts.disconnect_account(manager.secrets, email)

    @app.post("/v1/connectors/google_calendar/accounts/{email}/default")
    def gcal_account_default(email: str) -> dict[str, Any]:
        from ..connectors import gcal_accounts

        return gcal_accounts.set_default(manager.secrets, email)

    @app.post("/v1/connectors/hubspot/portals/{hub_id}/disconnect")
    async def hubspot_portal_disconnect(hub_id: str) -> dict[str, Any]:
        from .. import cloud
        from ..config import load_config
        from ..connectors import hubspot_portals

        profile_key = hubspot_portals.PREFIX + hub_id.strip()
        await asyncio.to_thread(
            lambda: cloud.cloud_disconnect(
                manager.secrets, load_config(), "hubspot", profile_key=profile_key
            )
        )
        return hubspot_portals.disconnect_portal(manager.secrets, hub_id)

    @app.post("/v1/connectors/hubspot/portals/{hub_id}/default")
    def hubspot_portal_default(hub_id: str) -> dict[str, Any]:
        from ..connectors import hubspot_portals

        return hubspot_portals.set_default(manager.secrets, hub_id)

    @app.post("/v1/connectors/{name}/accounts/{account_id}/disconnect")
    async def account_disconnect(name: str, account_id: str) -> dict[str, Any]:
        """Generic per-account disconnect for account-patterned connectors
        (batch 2+). Gmail/Calendar keep their specific email routes."""
        from .. import cloud
        from ..config import load_config
        from ..connectors import accounts

        if not accounts.is_account_connector(name):
            return {"ok": False, "error": "not a multi-account connector"}
        _id, profile_key, profile = accounts.resolve(manager.secrets, name, account_id)
        if profile and profile.get("managed"):
            await asyncio.to_thread(
                lambda: cloud.cloud_disconnect(
                    manager.secrets, load_config(), name, profile_key=profile_key
                )
            )
        return accounts.disconnect_account(manager.secrets, name, account_id)

    @app.post("/v1/connectors/{name}/accounts/{account_id}/default")
    def account_default(name: str, account_id: str) -> dict[str, Any]:
        from ..connectors import accounts

        if not accounts.is_account_connector(name):
            return {"ok": False, "error": "not a multi-account connector"}
        return accounts.set_default(manager.secrets, name, account_id)

    @app.patch("/v1/connectors/hubspot/hidden-fields")
    def hubspot_hidden_fields(body: dict) -> dict[str, Any]:
        """Replace the hidden-fields denylist (property names stripped from every
        record agents read — model-facing policy, not a human ACL)."""
        from ..connectors import hubspot_portals

        fields = body.get("hidden_fields") if isinstance(body, dict) else None
        if not isinstance(fields, list):
            return {"ok": False, "error": "hidden_fields must be a list"}
        return hubspot_portals.set_hidden_fields(manager.secrets, fields)

    @app.post("/v1/connectors/{name}/unauthorized/{item_id}")
    async def connector_unauthorized_resolve(
        name: str, item_id: str, body: dict
    ) -> dict[str, Any]:
        # Resolve a parked unauthorized message: dismiss / allow / allow_deliver (§19).
        action = str((body or {}).get("action", "")).strip()
        return await manager.resolve_unauthorized(name, item_id, action)

    # -- OpenWorker Cloud: sign-in + managed one-click connect ---------------
    # All optional: the app is fully functional signed out (manual token paste
    # stays available for every connector, before and after sign-in).

    @app.get("/v1/cloud/status")
    def cloud_status() -> dict[str, Any]:
        from .. import cloud

        return {
            **cloud.status(manager.secrets),
            "telemetry_enabled": cloud.telemetry_enabled(manager.secrets),
        }

    @app.post("/v1/cloud/telemetry")
    def cloud_telemetry(body: dict) -> dict[str, Any]:
        """The Phase 5 opt-out toggle. Local preference only — signed-out users
        send nothing regardless of this value."""
        from .. import cloud

        return cloud.set_telemetry_enabled(
            manager.secrets, bool((body or {}).get("enabled", True))
        )

    @app.post("/v1/cloud/login")
    def cloud_login() -> dict[str, Any]:
        """Start browser sign-in. The sidecar opens the system browser itself
        (works identically under Tauri and plain-browser dev)."""
        import webbrowser

        from .. import cloud
        from ..config import load_config

        out = cloud.begin_login(load_config())
        webbrowser.open(out["authorize_url"])
        return {"ok": True, "authorize_url": out["authorize_url"]}

    @app.post("/v1/cloud/logout")
    def cloud_logout() -> dict[str, Any]:
        from .. import cloud

        return cloud.logout(manager.secrets)

    @app.get("/auth/callback")
    async def cloud_auth_callback(code: str = "", state: str = "", error: str = ""):
        from fastapi.responses import HTMLResponse

        from .. import cloud
        from ..config import load_config

        signin_failed_detail = (
            "Close this tab and try signing in again from OpenWorker."
        )
        if error:
            return HTMLResponse(
                _browser_page(
                    "Sign-in failed", signin_failed_detail, ok=False, error=error
                ),
                status_code=400,
            )
        result = await asyncio.to_thread(
            lambda: cloud.complete_login(manager.secrets, load_config(), code, state)
        )
        if not result.get("ok"):
            return HTMLResponse(
                _browser_page(
                    "Sign-in failed",
                    signin_failed_detail,
                    ok=False,
                    error=result.get("error", ""),
                ),
                status_code=400,
            )

        # Restore managed connections in the background: best-effort metadata work
        # that must not hold the "Signed in" page (or the GUI's signed-in flip)
        # hostage to another broker round trip. Restored GitHub installs hot-add
        # the gateway so the relay connects without a restart.
        async def _restore_connections() -> None:
            try:
                out = await asyncio.to_thread(
                    lambda: cloud.sync_connections(manager.secrets, load_config())
                )
                if out.get("restored"):
                    await manager.refresh_gateway()
            except Exception:
                pass  # sign-in stands; the user can still connect by hand

        asyncio.get_running_loop().create_task(_restore_connections())
        return HTMLResponse(
            _browser_page(
                "Signed in",
                "You're signed in to OpenWorker Cloud. "
                "You can close this tab and return to OpenWorker.",
            )
        )

    @app.post("/v1/connectors/{name}/connect-managed")
    async def connector_connect_managed(
        name: str, body: Optional[dict] = None
    ) -> dict[str, Any]:
        """One-click managed OAuth (requires cloud sign-in). Opens the provider
        consent page in the system browser; the broker's callback page will
        form-POST the tokens to /oauth/callback below. `access` picks a consent
        tier by NAME (e.g. hubspot read | write) — the broker owns the scopes."""
        import webbrowser

        from .. import cloud
        from ..config import load_config
        from ..connectors.descriptors import get_descriptor

        d = get_descriptor(name)
        if d is not None and d.managed_paused:
            # GUI shows the Coming-soon state; this guard covers stale GUIs/API callers.
            return {
                "ok": False,
                "error": f"one-click connect for {d.title} is coming soon — connect manually for now",
            }
        access = str((body or {}).get("access") or "")
        flow = str((body or {}).get("flow") or "")  # github: "" install | "authorize"
        # Machine-targeted connect (machines spec §Remote OAuth): OAuth still
        # completes in THIS browser, but the callback ships the grant — sealed —
        # to the named machine and stores nothing locally. GitHub included:
        # its install-flow callback stages every returned installation (the
        # same unit a Move ships) — metadata + credential, no secrets.
        machine_id = str((body or {}).get("machine_id") or "")
        machine_name = str((body or {}).get("machine_name") or "")
        out = await asyncio.to_thread(
            lambda: cloud.begin_managed_connect(
                manager.secrets,
                load_config(),
                name,
                access=access,
                flow=flow,
                machine_id=machine_id,
                machine_name=machine_name,
            )
        )
        if out.get("ok"):
            webbrowser.open(out["authorize_url"])
        return out

    @asynccontextmanager
    async def _machine_api(machine_id: str):
        """A client aimed at the machine's controller, by the GUI's id
        convention: `cloud:`-prefixed ids go to the hosted machines service
        under the user's cloud session; bare ids are this controller's own
        machines, reached through its acceptor routes in-process — same
        sealing, ledger, and audit as a GUI call. Yields (client, headers,
        bare machine id); raises RuntimeError for a missing cloud session."""
        import httpx as _httpx

        from .. import cloud
        from ..config import load_config

        if machine_id.startswith("cloud:"):
            token = await asyncio.to_thread(
                cloud.fresh_access_token, manager.secrets, load_config()
            )
            if not token:
                raise RuntimeError("cloud session expired — sign in again")
            async with _httpx.AsyncClient(
                base_url=load_config().cloud_machines_base.rstrip("/"), timeout=30
            ) as client:
                yield client, {"Authorization": f"Bearer {token}"}, machine_id[
                    len("cloud:") :
                ]
            return
        async with _httpx.AsyncClient(
            transport=_httpx.ASGITransport(app=app),
            base_url="http://sidecar",
            timeout=30,
        ) as client:
            yield client, (
                {"X-OpenWorker-Token": api_token} if api_token else {}
            ), machine_id

    async def _find_machine(client, headers: dict, mid: str) -> Optional[dict]:
        r = await client.get("/v1/machines", headers=headers)
        rows = r.json().get("machines", []) if r.status_code == 200 else []
        return next((m for m in rows if m.get("id") == mid), None)

    async def _post_sealed_profiles(
        client, headers: dict, mid: str, seal_pubkey: str, profiles: dict[str, Any]
    ) -> dict[str, Any]:
        from ..remote.acceptor import _hash_profile
        from ..remote.identity import seal_b64

        names = sorted(profiles)
        sealed = seal_b64(seal_pubkey, json.dumps({"profiles": profiles}).encode())
        r = await client.post(
            f"/v1/machines/{mid}/secrets",
            json={
                "sealed_b64": sealed,
                "profiles": names,
                "hashes": {n: _hash_profile(profiles[n]) for n in names},
            },
            headers=headers,
        )
        if r.status_code != 200:
            detail = {}
            try:
                detail = r.json()
            except ValueError:
                pass
            return {
                "ok": False,
                "error": str(
                    detail.get("message") or detail.get("error") or "deploy failed"
                ),
            }
        return {"ok": True}

    @app.post("/oauth/callback")
    async def managed_oauth_callback(request: Request) -> Any:
        from fastapi.responses import HTMLResponse

        from .. import cloud
        from ..config import load_config
        from ..connectors.setup import (
            managed_connect_slack_install,
            store_managed_grant,
            store_managed_grant_bundle,
        )
        from ..secrets import EphemeralSecretStore

        form = await request.form()
        data = {k: str(v) for k, v in form.items()}
        connector = data.get("connector", "")
        pending = cloud.consume_managed_state(data.get("app_state", ""))
        if pending is None:
            return HTMLResponse(
                _browser_page(
                    "Connection failed",
                    _CONNECT_FAILED_DETAIL,
                    ok=False,
                    error="unknown or expired connection attempt",
                ),
                status_code=400,
            )
        if data.get("error"):
            return HTMLResponse(
                _browser_page(
                    "Connection failed",
                    _CONNECT_FAILED_DETAIL,
                    ok=False,
                    error=data["error"],
                ),
                status_code=400,
            )
        # Machine-targeted connect (machines spec §Remote OAuth + §Managed
        # events): the grant is for another machine — delegate at the broker
        # (minting the machine credential against the machine's pinned key),
        # stage through the normal storage layers into an in-memory store,
        # seal, deploy, and store NOTHING locally (one grant, one holder).
        # `stage(staging, grant)` is the per-family staging step.
        target_machine = str(pending.get("machine_id") or "")
        machine_label = str(pending.get("machine_name") or "") or "the machine"

        async def _targeted_handoff(connection_id: str, stage) -> HTMLResponse:
            import httpx as _httpx

            def _handoff_failed(error: str, *, status: int = 400) -> HTMLResponse:
                return HTMLResponse(
                    _browser_page(
                        "Connection failed",
                        f"The connection for {machine_label} could not be completed "
                        "— nothing was stored anywhere. Connect again from the "
                        "machine's Connectors page.",
                        ok=False,
                        error=error,
                    ),
                    status_code=status,
                )

            if not connection_id:
                return _handoff_failed("no connection id from the broker")
            try:
                async with _machine_api(target_machine) as (mclient, mheaders, mid):
                    row = await _find_machine(mclient, mheaders, mid)
                    if row is None:
                        return _handoff_failed("unknown machine")
                    seal_pubkey = str(row.get("seal_pubkey") or "")
                    if not seal_pubkey:
                        return _handoff_failed(
                            "machine has no pinned sealing key yet"
                        )
                    # Delegate WITH the seal key: the broker mints the machine
                    # credential and flips this connection's events to the
                    # machine's sealed queue in the same stroke.
                    grant = await asyncio.to_thread(
                        lambda: cloud.delegate_connection(
                            manager.secrets,
                            load_config(),
                            connection_id,
                            seal_pubkey=seal_pubkey,
                            # Hosted rows only: the machines service's own id.
                            machine_id=mid if target_machine.startswith("cloud:") else "",
                        )
                    )
                    if not grant:
                        return _handoff_failed(
                            "could not delegate the connection for machine renewal"
                        )
                    staging = EphemeralSecretStore()
                    staged = stage(staging, grant)
                    if not staged.get("ok"):
                        return _handoff_failed(
                            staged.get("error", "could not stage the grant")
                        )
                    deployed = await _post_sealed_profiles(
                        mclient, mheaders, mid, seal_pubkey, staging.profiles()
                    )
                    if not deployed.get("ok"):
                        return _handoff_failed(
                            deployed.get("error", "deploy failed"), status=502
                        )
            except RuntimeError as exc:
                return _handoff_failed(str(exc))
            except _httpx.HTTPError as exc:
                return _handoff_failed(
                    f"machine unreachable: {type(exc).__name__}", status=502
                )
            return HTMLResponse(
                _browser_page(
                    f"{_connector_title(connector)} connected on {machine_label}",
                    "The connection lives on that machine and renews there. "
                    "This Mac holds nothing. You can close this tab.",
                    connector=connector,
                )
            )

        # Managed GitHub deliberately carries NO token fields — the loopback POST
        # is routing metadata only (installation tokens are minted on demand,
        # github-relay-spec §4) — so its branch precedes the access_token check.
        if connector == "github" and data.get("installation_id"):
            from ..connectors.github_installs import managed_connect_install

            if target_machine:
                # Connect-direct: the shared bundle routine stages EVERY
                # installation the callback returned (the same unit a Move
                # ships) and stamps the delegation — one implementation with
                # the box's browser-sealed path.
                return await _targeted_handoff(
                    data.get("connection_id", ""),
                    lambda staging, grant: store_managed_grant_bundle(
                        staging, "github", data, grant
                    ),
                )
            result = managed_connect_install(manager.secrets, data)
            if result.get("ok"):
                await manager.refresh_gateway()  # hot-add, like a workspace
            if not result.get("ok"):
                return HTMLResponse(
                    _browser_page(
                        "Connection failed",
                        _CONNECT_FAILED_DETAIL,
                        ok=False,
                        error=result.get("error", ""),
                    ),
                    status_code=400,
                )
            return HTMLResponse(
                _browser_page(
                    "GitHub connected",
                    "You can close this tab and return to OpenWorker.",
                    connector="github",
                )
            )
        if not connector or not data.get("access_token"):
            return HTMLResponse(
                _browser_page(
                    "Connection failed",
                    _CONNECT_FAILED_DETAIL,
                    ok=False,
                    error="missing fields",
                ),
                status_code=400,
            )
        if target_machine and connector != "github":
            return await _targeted_handoff(
                data.get("connection_id", ""),
                lambda staging, grant: store_managed_grant_bundle(
                    staging, connector, data, grant
                ),
            )
        # Managed Slack is multi-workspace + relay: store the per-team bot token
        # and flip to relay mode, rather than the single-token connector path.
        if connector == "slack" and data.get("team_id"):
            result = managed_connect_slack_install(manager.secrets, data)
            if result.get("ok"):
                # Hot-add: rebuild the gateway so the new workspace's token loads
                # (and the relay socket opens on a first-ever install) right away.
                await manager.refresh_gateway()
        else:
            result = store_managed_grant(
                manager.secrets, connector, data, cloud.managed_profile_from_callback(data)
            )
        if not result.get("ok"):
            return HTMLResponse(
                _browser_page(
                    "Connection failed",
                    _CONNECT_FAILED_DETAIL,
                    ok=False,
                    error=result.get("error", ""),
                ),
                status_code=400,
            )
        return HTMLResponse(
            _browser_page(
                f"{_connector_title(connector)} connected",
                "You can close this tab and return to OpenWorker.",
                connector=connector,
            )
        )

    @app.patch("/v1/connectors/{name}/tools")
    def connector_tools_patch(name: str, body: dict) -> dict[str, Any]:
        enabled = (body or {}).get("enabled")
        if not isinstance(enabled, dict):
            return {"ok": False, "error": "enabled map required"}
        return manager.update_connector_tools(name, enabled)

    @app.post("/v1/connectors/{name}/allow")
    def connector_allow(name: str, body: dict) -> dict[str, Any]:
        # `team_id` scopes the edit to one workspace (managed relay); absent → flat list.
        # `name` (optional) seeds the people directory so a directory-picked user's
        # chip shows their display name before they've ever sent a message.
        return manager.allow_user(
            name,
            str(body.get("user_id", "")),
            str(body.get("team_id", "")) or None,
            display_name=str(body.get("name", "")),
        )

    @app.get("/v1/connectors/slack/workspaces/{team_id}/directory")
    async def slack_directory(
        team_id: str, q: str = "", limit: int = 25
    ) -> dict[str, Any]:
        """Workspace member roster for the people picker (team_id "default" =
        the manual Socket-Mode workspace). Cached locally; never leaves this machine."""
        from ..connectors import slack_directory as roster

        return await asyncio.to_thread(
            lambda: roster.list_members(manager.secrets, team_id, q, limit)
        )

    @app.get("/v1/connectors/slack/workspaces/{team_id}/channels")
    async def slack_channels(
        team_id: str, q: str = "", limit: int = 25
    ) -> dict[str, Any]:
        """Channel roster for the channel typeahead: all public channels, private
        ones only where the bot is a member (Slack API constraint)."""
        from ..connectors import slack_directory as roster

        return await asyncio.to_thread(
            lambda: roster.list_channels(manager.secrets, team_id, q, limit)
        )

    @app.post("/v1/connectors/{name}/disallow")
    def connector_disallow(name: str, body: dict) -> dict[str, Any]:
        return manager.disallow_user(
            name, str(body.get("user_id", "")), str(body.get("team_id", "")) or None
        )

    @app.post("/v1/connectors/slack/approval-owners/add")
    def slack_approval_owner_add(body: dict) -> dict[str, Any]:
        return manager.set_slack_approval_owner(
            str(body.get("user_id", "")),
            add=True,
            display_name=str(body.get("name", "")),
        )

    @app.post("/v1/connectors/slack/approval-owners/remove")
    def slack_approval_owner_remove(body: dict) -> dict[str, Any]:
        return manager.set_slack_approval_owner(
            str(body.get("user_id", "")), add=False
        )

    # -- audit / browser observability ------------------------------------------
    @app.get("/v1/audit")
    def audit_list(
        limit: int = 100,
        session_id: str | None = None,
        connector: str | None = None,
        tool: str | None = None,
    ) -> dict[str, Any]:
        return {
            "events": manager.list_audit(
                limit=limit, session_id=session_id, connector=connector, tool=tool
            )
        }

    @app.get("/v1/browser/state")
    def browser_state_get() -> dict[str, Any]:
        return manager.browser_state()

    @app.post("/v1/browser/screenshot")
    def browser_screenshot_post() -> dict[str, Any]:
        return manager.browser_screenshot()

    @app.post("/v1/browser/close")
    def browser_close_post() -> dict[str, Any]:
        return manager.browser_close()

    # -- web search -------------------------------------------------------------
    @app.get("/v1/web-search")
    def web_search_get() -> dict[str, Any]:
        return manager.get_web_search()

    @app.post("/v1/web-search")
    def web_search_set(body: dict) -> dict[str, Any]:
        provider = (body or {}).get("provider", "")
        if not provider:
            return {"ok": False, "error": "provider required"}
        return manager.set_web_search(provider, (body or {}).get("api_key"))

    # -- model providers (OpenAI, Ollama, …) ------------------------------------
    @app.get("/v1/providers")
    def providers_get() -> list[dict[str, Any]]:
        return manager.get_providers()

    @app.post("/v1/providers")
    def providers_set(body: dict) -> dict[str, Any]:
        name = (body or {}).get("name", "")
        if not name:
            return {"ok": False, "error": "name required"}
        return manager.set_provider(name, (body or {}).get("fields"))

    @app.delete("/v1/providers/{name}")
    def providers_remove(name: str) -> dict[str, Any]:
        return manager.remove_provider(name)

    @app.post("/v1/providers/verify")
    async def providers_verify(body: dict) -> dict[str, Any]:
        # Live read-only credential check (sync httpx) — run off the event loop.
        name = (body or {}).get("name", "") or "openai"
        return await asyncio.to_thread(
            manager.verify_provider, name, (body or {}).get("fields")
        )

    @app.post("/v1/providers/openai-codex/signin")
    async def codex_signin() -> dict[str, Any]:
        # Opens the system browser and waits on the loopback callback — that can
        # take minutes, so it runs as a background task; the GUI polls the status
        # route for the flip (authorizing → signed_in | last_error). Same shape as
        # the MCP OAuth connect route.
        manager.begin_codex_signin()
        asyncio.create_task(manager.codex_signin())
        return {"ok": True, "started": True}

    @app.get("/v1/providers/openai-codex/status")
    def codex_status() -> dict[str, Any]:
        return manager.codex_status()

    @app.post("/v1/providers/openai-codex/signout")
    def codex_signout() -> dict[str, Any]:
        return manager.codex_signout()

    # -- settings (model API key) -----------------------------------------------
    @app.get("/v1/settings")
    def settings_get() -> dict[str, Any]:
        return manager.get_settings()

    @app.post("/v1/settings/model-key")
    def settings_set_model_key(body: dict) -> dict[str, Any]:
        return manager.set_model_key((body or {}).get("api_key", ""))

    @app.post("/v1/settings/default-model")
    def settings_set_default_model(body: dict) -> dict[str, Any]:
        return manager.set_default_model((body or {}).get("model", ""))

    @app.post("/v1/settings/default-mode")
    def settings_set_default_mode(body: dict) -> dict[str, Any]:
        return manager.set_default_mode((body or {}).get("mode", ""))

    @app.post("/v1/settings/models/add")
    def settings_models_add(body: dict) -> dict[str, Any]:
        return manager.add_model((body or {}).get("model", ""))

    @app.post("/v1/settings/models/remove")
    def settings_models_remove(body: dict) -> dict[str, Any]:
        return manager.remove_model((body or {}).get("model", ""))

    @app.post("/v1/settings/onboarded")
    def settings_set_onboarded(body: dict) -> dict[str, Any]:
        return manager.set_onboarded(bool((body or {}).get("value", True)))

    @app.post("/v1/settings/experimental-connectors")
    def settings_set_experimental(body: dict) -> dict[str, Any]:
        return manager.set_experimental_connectors(bool((body or {}).get("value")))

    @app.post("/v1/settings/surfaces")
    def settings_set_surfaces(body: dict) -> dict[str, Any]:
        b = body or {}
        return manager.set_surfaces(chat=b.get("chat"), code=b.get("code"))

    @app.post("/v1/settings/scratch-base")
    def settings_set_scratch_base(body: dict) -> dict[str, Any]:
        return manager.set_scratch_base(str((body or {}).get("path", "")))

    @app.post("/v1/settings/nav-layout")
    def settings_set_nav_layout(body: dict) -> dict[str, Any]:
        return manager.set_nav_layout(str((body or {}).get("nav_layout", "")))

    @app.post("/v1/settings/sessions-peek")
    def settings_set_sessions_peek(body: dict) -> dict[str, Any]:
        # Sidebar: sessions shown per group before "Show more" (owner ask, 2026-07-03).
        return manager.set_sessions_peek((body or {}).get("sessions_peek", 5))

    @app.post("/v1/settings/context-bar")
    def settings_set_context_bar(body: dict) -> dict[str, Any]:
        # Composer: show the context-window fill bar, or just the popover (owner ask).
        return manager.set_context_bar((body or {}).get("context_bar", True))

    @app.post("/v1/settings/auto-approve")
    def settings_set_auto_approve(body: dict) -> dict[str, Any]:
        # Auto-Approve feature flag (spec §1.5): when on, Mode.AUTO_APPROVE gets an LLM
        # reviewer. Takes effect on the next session build. Turning it off leaves any
        # shadow-eval setting alone (they are independent switches).
        return manager.set_auto_approve((body or {}).get("auto_approve", False))

    @app.post("/v1/settings/auto-approve-shadow")
    def settings_set_auto_approve_shadow(body: dict) -> dict[str, Any]:
        # Shadow evaluation (Part 6 step 3): the reviewer records what it WOULD decide on
        # every approval card while the human still decides. Independent of the live flag.
        return manager.set_auto_approve_shadow((body or {}).get("auto_approve_shadow", False))

    @app.post("/v1/settings/pdf")
    def settings_set_pdf(body: dict) -> dict[str, Any]:
        # Token savings (owner ask, 2026-07-17): fallback mode for models without native
        # PDF support + attach-time page/size thresholds.
        b = body or {}
        return manager.set_pdf_settings(
            fallback=b.get("pdf_fallback"),
            max_pages=b.get("pdf_max_pages"),
            max_mb=b.get("pdf_max_mb"),
        )

    @app.get("/v1/settings/sandbox")
    def settings_get_sandbox() -> dict[str, Any]:
        # Settings ▸ Sandbox (UX-051 A): provider, network profile, credential grants.
        # Machine-level, read from the machine's config.toml.
        from ..sandbox import settings as sandbox_settings

        return sandbox_settings.snapshot()

    @app.post("/v1/settings/sandbox")
    def settings_set_sandbox(body: dict) -> dict[str, Any]:
        from ..sandbox import settings as sandbox_settings

        return sandbox_settings.update(body or {})

    @app.post("/v1/settings/compaction")
    def settings_set_compaction(body: dict) -> dict[str, Any]:
        # Auto-compaction overrides (OPE-27): threshold % of the context window, the
        # absolute token cap, and the summarizer-model pin ("" → session's own model).
        b = body or {}
        return manager.set_compaction_settings(
            threshold_pct=b.get("compaction_threshold_pct"),
            cap_tokens=b.get("compaction_cap_tokens"),
            model=b.get("compaction_model"),
        )

    @app.post("/v1/attachments/inspect-pdf")
    def attachments_inspect_pdf(body: dict) -> dict[str, Any]:
        # Attach-time page/size probe for the composer's threshold check. Local only.
        from ..pdf_support import inspect

        return inspect(str((body or {}).get("data_url", "")))

    # -- direct-message routing -------------------------------------------------
    @app.get("/v1/messaging/dm-route")
    def dm_route_get() -> dict[str, Any]:
        return {"dm_session": manager.dm_session()}

    @app.post("/v1/messaging/dm-route")
    def dm_route_set(body: dict) -> dict[str, Any]:
        # A falsy session_id clears the designation (DMs then park as unrouted).
        return manager.set_dm_session((body or {}).get("session_id", ""))

    if os.environ.get("COWORKER_DEBUG_INJECT") == "1":
        # Dev-only (env-gated, localhost): feed a message through the real inbound path so the
        # messaging stack can be exercised without a live bot connection. Not registered otherwise.
        @app.post("/v1/_debug/inject_inbound")
        async def debug_inject_inbound(body: dict) -> dict[str, Any]:
            from ..connectors.base import MessageEvent, SessionSource

            event = MessageEvent(
                text=str((body or {}).get("text", "")),
                source=SessionSource(
                    platform=str(body.get("platform", "slack")),
                    chat_id=str(body.get("chat_id", "C0BD7KZ1AH5")),
                    user_id=str(body.get("user_id", "U07JK68S4BH")),
                    user_name=str(body.get("user_name", "tester")),
                    chat_type=str(body.get("chat_type", "channel")),
                    chat_name=str(body.get("chat_name", "")) or None,
                    thread_id=str(body.get("thread_ts", "")) or None,
                    team_id=str(body.get("team_id", "")) or None,
                ),
                message_id=str(body.get("ts", "")) or None,
                # §31 mention router: the flag is normally computed from the raw Slack text
                # at mapping time; the injector sets it directly.
                mentions_me=bool(body.get("mentions_me")),
            )
            await manager._dispatch_inbound(event)
            return {"ok": True}

    # -- automations (scheduled tasks) ------------------------------------------
    @app.get("/v1/automations")
    def automations_list() -> dict[str, Any]:
        return manager.list_automations()

    @app.post("/v1/automations")
    def automations_create(body: dict) -> dict[str, Any]:
        return manager.create_automation(body or {})

    @app.get("/v1/automations/{task_id}")
    def automation_get(task_id: str) -> dict[str, Any]:
        return manager.get_automation(task_id)

    @app.patch("/v1/automations/{task_id}")
    def automation_update(task_id: str, body: dict) -> dict[str, Any]:
        return manager.update_automation(task_id, body or {})

    @app.delete("/v1/automations/{task_id}")
    def automation_delete(task_id: str) -> dict[str, Any]:
        return manager.delete_automation(task_id)

    @app.post("/v1/automations/{task_id}/seen")
    def automations_seen(task_id: str) -> dict[str, Any]:
        return manager.mark_automation_seen(task_id)

    @app.post("/v1/automations/{task_id}/run")
    def automation_run(task_id: str) -> dict[str, Any]:
        # Prepare a live manual run; the GUI opens the returned session and drives it.
        return manager.prepare_manual_run(task_id)

    @app.post("/v1/automations/{task_id}/runs/{run_id}/finalize")
    def automation_run_finalize(task_id: str, run_id: str) -> dict[str, Any]:
        return manager.finalize_manual_run(task_id, run_id)

    @app.websocket("/ws/session/{session_id}")
    async def ws_session(ws: WebSocket, session_id: str) -> None:
        if not _websocket_authenticated(ws):
            await ws.close(code=1008)
            return
        # CORS never gates WebSockets, so a cross-site page could otherwise open this socket
        # and drive the session into tool calls. Reject a disallowed browser Origin before
        # accepting the handshake (1008 = policy violation).
        if not _origin_allowed(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol="openworker" if api_token else None)
        agent = ws.query_params.get("agent") or "code"
        # Session actor (spec §Fleet under the org): only the channel bridge can
        # present this header (a joined box has no listener), and the controller
        # sets it from the login it verified. First writer wins.
        manager.note_session_actor(session_id, ws.headers.get("x-openworker-actor", ""))

        # All four interactive prompts (approval / question / directory / plan) are parked as Inbox
        # items and awaited via inbox.wait — so they survive a dropped socket (redelivered on
        # reconnect) and can be resolved from any surface. `visibility` decides where they SHOW:
        # Unattended → the cross-session Inbox; attended → inline in this session only. The agent
        # stays blocked until the item is resolved (live WS response, REST, or a bound channel).
        def _visibility() -> str:
            return (
                VIS_INBOX
                if manager.unattended.is_unattended(session_id)
                else VIS_INLINE
            )

        async def _mirror(item) -> None:
            # Unattended items mirror to a bound channel as buttons (see mirror_inbox_item).
            await manager.mirror_inbox_item(item)

        def _route() -> str:
            return manager.inbox_routing.route_for(session_id, agent)

        async def approver(_request) -> ApprovalOutcome:
            # The engine has already emitted PERMISSION_REQUIRED (the live inline card). Park the
            # item so the answer can also come from the Inbox / a reconnect / after a restart.
            item = manager.inbox.add_approval(
                session_id,
                f"Run `{_request.tool_name}`?",
                # Shared with inbox_approver so parked/mirrored bodies match the live
                # card's dialect (boilerplate-reason filtering included, §35).
                body=_approval_body(_request),
                inbox=_route(),
                visibility=_visibility(),
                # Automation-run context (manual "Run now" rides this socket): lets the
                # card offer the task-persistent "Allow every time" (§25). {} elsewhere.
                data=manager.approval_prompt_data(session_id, _request),
                tool_call_id=getattr(_request, "tool_call_id", None),
            )
            if item.state == "pending":
                # §11.6: a worker parked here under a Manual lead — tell its lead via the
                # board, with what it needs to decide (the prompt id is the call_id).
                manager.note_worker_waiting(
                    session_id,
                    _request.tool_name,
                    prompt_id=item.id,
                    preview=args_preview(getattr(_request, "arguments", None)) or "",
                )
            if (
                item.state == "pending"
            ):  # freshly raised (not a durable-resume re-raise)
                manager.persist_session(
                    session_id
                )  # the pending tool call is now on disk
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resolution = await manager.inbox.wait(item.id)
            # Accept every vocabulary: the live card sends once/always_tool/always_command/
            # always_task/deny; the Inbox / a channel send allow/always/deny.
            return manager.approval_outcome(resolution, _request, session_id)

        async def question_asker(args: dict, tool_call_id=None) -> dict:
            # ask_user (engine does NOT emit the event — we do, only when attended).
            from ..tools.ask import answer_result, question_item_fields

            fields = question_item_fields(args)
            if fields is None:  # engine guards too; belt-and-braces
                return {"answer": "", "error": "no question"}
            item = manager.inbox.add_question(
                session_id,
                inbox=_route(),
                visibility=_visibility(),
                tool_call_id=tool_call_id,
                **fields,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                manager.note_worker_waiting(session_id, "ask_user", prompt_id=item.id, preview=item.title)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
                else:
                    await ws.send_json(
                        {
                            "type": "question_requested",
                            "data": {
                                "question": item.title,
                                "options": item.options,
                                "allow_text": item.allow_text,
                                "multi": item.multi,
                                "header": item.header,
                                "questions": item.questions,
                            },
                        }
                    )
            return answer_result(item.questions, await manager.inbox.wait(item.id))

        async def tool_requester(args: dict, tool_call_id=None) -> dict:
            """Park a TOOL_REQUESTED prompt, then install the PINNED build if approved.

            Declining is a first-class outcome: the agent is told to fall back and disclose
            the gap rather than drop the check (OPE-85). Installs only ever come from the
            pinned registry with its digest verified — an approval is consent to install
            THAT artifact, not licence to fetch whatever a prompt asked for.
            """
            name = str(args.get("name", "")).strip()
            info = toolchain.describe(name)
            if not info:
                # Not in the pinned catalog: never show an install card that can only
                # end in "no pinned build" AFTER approval (owner-hit 2026-08-20 — agents
                # routed ordinary brew/pip installs through the card). Steer to the
                # shell, which has its own approval flow.
                return {
                    "installed": False,
                    "error": (
                        f"'{name}' is not in the pinned tool catalog "
                        f"({', '.join(sorted(toolchain.MANAGED))}). Install it yourself "
                        "with the shell (brew/pip/…, subject to the normal command "
                        "approval), or proceed without it and note the gap."
                    ),
                }
            item = manager.inbox.add_tool_request(
                session_id,
                f"Install {name}?" if name else "Install a tool?",
                body=str(args.get("reason", "")),
                inbox=_route(),
                visibility=_visibility(),
                data={
                    "tool": name,
                    "installable": bool(info),
                    "version": (info or {}).get("version", ""),
                    "summary": (info or {}).get("summary", ""),
                    "url": (info or {}).get("url", ""),
                    "source": (info or {}).get("source", ""),
                },
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resp = _parse_json(await manager.inbox.wait(item.id))  # {approved}
            if not resp.get("approved"):
                return {
                    "installed": False,
                    "reason": "the user declined to install it",
                }
            if not info:
                return {
                    "installed": False,
                    "error": f"no pinned build of {name} is available for this platform",
                }
            try:
                path = await asyncio.to_thread(toolchain.install, name)
            except Exception as exc:  # noqa: BLE001 - surfaced to the agent verbatim
                return {"installed": False, "error": str(exc)}
            return {"installed": True, "path": path, "version": info["version"]}

        async def directory_requester(args: dict, tool_call_id=None) -> dict:
            # The engine has already emitted DIRECTORY_REQUESTED. Park, await, then apply the grant.
            item = manager.inbox.add_directory(
                session_id,
                "Grant access to a folder?",
                body=str(args.get("reason", "")),
                inbox=_route(),
                visibility=_visibility(),
                data={
                    "path": str(args.get("path", "")),
                    "writable": bool(args.get("writable", False)),
                    "primary": bool(args.get("primary", False)),
                },
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resp = _parse_json(
                await manager.inbox.wait(item.id)
            )  # {granted, path, writable}
            if not resp.get("granted"):
                return {"granted": False, "reason": "the user declined the request"}
            path = (resp.get("path") or args.get("path") or "").strip()
            if not path:
                return {"granted": False, "error": "no directory was provided"}
            writable = bool(resp.get("writable", args.get("writable", False)))
            if bool(args.get("primary", False)):
                # Root promotion (workspace-scratch-design.md §5) — the shell cd inside
                # is blocking, keep it off the event loop.
                promo = await asyncio.to_thread(
                    manager.promote_workspace, session_id, path
                )
                if promo.get("ok"):
                    return {
                        "granted": True,
                        "path": promo["path"],
                        "writable": True,
                        "primary": True,
                        "note": (
                            "This folder is now the session's workspace. For the rest "
                            "of this turn, address it by absolute path."
                        ),
                    }
                # Promotion refused (e.g. the session already has a workspace): still
                # honor the grant as a plain additional folder.
                res = manager.add_root(session_id, path, writable)
                if not res.get("ok"):
                    return {
                        "granted": False,
                        "error": promo.get("error", "could not promote"),
                    }
                return {
                    "granted": True,
                    "path": path,
                    "writable": writable,
                    "primary": False,
                    "note": promo.get("error", "")
                    + " — granted as an additional folder instead",
                }
            res = manager.add_root(session_id, path, writable)
            if not res.get("ok"):
                return {
                    "granted": False,
                    "error": res.get("error", "could not grant access"),
                }
            primary = next(
                (
                    r
                    for r in res.get("roots", [])
                    if r.get("path")
                    and Path(r["path"]).expanduser().resolve()
                    == Path(path).expanduser().resolve()
                ),
                None,
            )
            return {
                "granted": True,
                "path": (primary or {}).get("path", path),
                "writable": writable,
            }

        async def plan_approver(_args: dict, tool_call_id=None) -> dict:
            # The engine has already emitted PLAN_PROPOSED. Park, await the verdict.
            item = manager.inbox.add_plan(
                session_id,
                "Approve the plan?",
                body=str(_args.get("plan", "")),
                inbox=_route(),
                visibility=_visibility(),
                tool_call_id=tool_call_id,
            )
            if item.state == "pending":
                manager.persist_session(session_id)
                if item.visibility == VIS_INBOX:
                    await _mirror(item)
            resp = _parse_json(
                await manager.inbox.wait(item.id)
            )  # {approved, mode, feedback}
            if not resp.get("approved"):
                return {
                    "approved": False,
                    "feedback": resp.get("feedback") or "the user rejected the plan",
                }
            return {"approved": True, "mode": resp.get("mode") or "interactive"}

        # §11.6: the connector asks and the team gates are manager handlers now (they
        # must work on background turns too); the socket only supplies attended
        # visibility so the prompt renders inline when someone is watching.
        connector_requester = manager.inbox_connector_requester(session_id, agent, visibility=_visibility)

        team_approver = manager.inbox_team_approver(session_id, agent, visibility=_visibility)

        items_approver = manager.inbox_items_approver(session_id, agent, visibility=_visibility)
        async def _apply_model(model: Optional[str]) -> None:
            # Mid-session rebind is allowed (roadmap item 3, supersedes the 2026-07-04
            # lock): history is canonical and providers convert per call. A real switch
            # appends a persisted notice; broadcast it so live views render the marker
            # and update their header. Never rebind mid-turn — the running loop reads
            # `engine.model` per iteration and a mixed turn is exactly the breakage the
            # old lock existed to prevent.
            if not model or manager.is_running(session_id):
                return
            # The coworker's `models:` list binds (§4): a model outside it resolves to
            # the list's first runnable entry, whatever the client asked for.
            model = manager.resolve_persona_model(getattr(engine, "agent_name", "") or "", model)
            notice = engine.switch_model(model)
            if notice is None:  # same model, or first bind on a fresh session
                return
            manager.persist_session(session_id)
            await manager.broadcast_session(
                session_id,
                {"type": "model_changed", "data": {"model": model, "text": notice}},
            )

        viewer_actor = ws.headers.get("x-openworker-actor", "")

        def _resolve_pending(resolution: str) -> None:
            # Live WS responses resolve THE session's single pending prompt (one at a time, since the
            # agent blocks). Reconnect / Inbox resolve by id via REST instead. The decider is this
            # socket's verified viewer (bridged sessions) — "" on a local desktop.
            pend = manager.inbox.pending(session_id)
            if pend:
                manager.inbox.resolve(pend[0].id, resolution, by=viewer_actor)

        workspace = ws.query_params.get("workspace")
        mcp_tools = await manager.prepare_mcp_tools(
            session_id, workspace=workspace, agent=agent
        )
        engine = manager.get_engine(
            session_id,
            workspace=workspace,
            agent=agent,
            approver=approver,
            extra_tools=mcp_tools,
            directory_requester=directory_requester,
            plan_approver=plan_approver,
            question_asker=question_asker,
            tool_requester=tool_requester,
            team_approver=team_approver,
            items_approver=items_approver,
            connector_requester=connector_requester,
        )
        if engine is None:
            await ws.send_json(
                {
                    "type": "error",
                    "data": {
                        "error": "no valid workspace — choose a project folder first"
                    },
                }
            )
            await ws.close()
            return
        # MCP servers that failed to start while preparing this session's tools:
        # leave a quiet, persistent notice instead of the session silently lacking
        # them (drill 2026-08-20: three silent startup failures in a row).
        for name, err in manager.pop_mcp_failures(session_id):
            detail = f": {err}" if err else ""
            # `server` makes the notice structured: the GUI renders one quiet line
            # with the full error behind a disclosure + an Open-Connectors action
            # (owner ruling 2026-08-21) instead of a wall of stderr.
            engine._append_notice(
                "mcp_error",
                f"MCP server “{name}” failed to start{detail}"[:500],
                server=name,
            )
        # Auto-compaction failure prompt (OPE-27): only an ATTENDED session may be asked
        # Retry/Trim — unattended runs auto-trim (the policy in engine._compact_now).
        # §11.5/§11.6: a session the human opted into auto-approve (a spawned session's
        # configuration, or a worker under an auto-approve lead) is reviewed even when
        # nobody attends it.
        engine.is_attended = lambda: _visibility() == VIS_INLINE or manager.reviewer_opted(session_id)
        await ws.send_json(
            {
                "type": "ready",
                "data": {
                    "session_id": session_id,
                    # A reconnect can land MID-TURN (sidebar revisit, app relaunch, WS
                    # drop). Without server truth the GUI never learns a turn is live —
                    # no Stop button, no waiting row (owner catch 2026-08-24).
                    "running": manager.is_running(session_id),
                    "agent": getattr(engine, "agent_name", "code"),
                    "model": engine.model,
                    "mode": engine.permissions.mode.value,
                    "workspace": (
                        str(getattr(engine, "executor").cwd)
                        if getattr(engine, "executor", None)
                        else None
                    ),
                    # UX-029: the GUI never shows a temporary folder's raw path — this flag
                    # is how it knows to say "Temporary folder" (and offer Save as project).
                    "temp_workspace": manager.is_temp_workspace(
                        str(getattr(engine, "executor").cwd)
                        if getattr(engine, "executor", None)
                        else None
                    ),
                    "command_trust": manager.workspace_command_trust(
                        str(getattr(engine, "audit_context", {}).get("workspace", ""))
                    ),
                },
            }
        )

        # Checkpoint events: persist mid-turn so a crash/quit can't eat the conversation.
        # turn_start = the user message just landed (a brand-new session gets its row here,
        # not at connect — empty never-used sessions shouldn't appear in Recents);
        # permission_required/directory_requested = parked indefinitely on the user;
        # iteration_end = a model response + its tool results completed.
        _CHECKPOINTS = {
            "turn_start",
            "permission_required",
            "directory_requested",
            "plan_proposed",
            "iteration_end",
        }

        async def run_turn(content, *, retry: bool = False, display=None) -> None:
            # The receive loop atomically claims this session before scheduling the task.
            # Keeping the claim outside prevents two back-to-back frames from both starting.
            try:
                events = (
                    engine.retry()
                    if retry
                    else engine.run(content, display=display,
                                    activity=manager.prepare_activity(session_id, "user activity"))
                )
                async for event in events:
                    data = event.data
                    # Broadcast to every socket viewing this session (this socket included — it's a
                    # registered client), so a second view of the same session stays in sync too.
                    await manager.broadcast_session(
                        session_id, {"type": event.type.value, "data": data}
                    )
                    if event.type.value in _CHECKPOINTS:
                        manager.save(session_id, engine)
                    if event.type.value == "turn_start":
                        # Title on the user's words the moment they land — never behind
                        # a long agentic turn (owner catch 2026-08-24).
                        manager._maybe_autotitle(session_id)
            finally:
                manager.mark_idle(session_id)
                manager.save(session_id, engine)
                await manager.broadcast_session(
                    session_id, {"type": "turn_done", "data": {}}
                )

        # This socket is now a live view of the session; background turns (channel delivery,
        # self-wake, durable resume) broadcast here too, not just locally driven run_turns.
        manager.register_session_client(session_id, ws.send_json)
        if engine.permissions.mode is Mode.AUTO_APPROVE and not any(
            m.get("kind") == "mode_notice" for m in engine.messages
        ):
            from coworker.permissions import AUTO_APPROVE_NOTICE

            engine._append_notice(
                "mode_notice", AUTO_APPROVE_NOTICE, title="Auto-approve is on."
            )
            manager.save(session_id, engine, touch=False)  # migration ≠ activity
            await ws.send_json(
                {
                    "type": "mode_notice",
                    "data": {
                        "title": "Auto-approve is on.",
                        "text": AUTO_APPROVE_NOTICE,
                    },
                }
            )
        inbound_times: deque[float] = deque()

        async def reject_input(reason: str) -> None:
            # Input validation failures are not provider failures and must not offer "Retry"
            # or flush an in-progress assistant stream in the GUI.
            await ws.send_json({"type": "input_rejected", "data": {"error": reason}})

        async def claim_turn(*, retry: bool = False, content=None, display=None) -> None:
            if not manager.try_mark_running(session_id):
                await reject_input(
                    "This session is already running a turn. Wait for it to finish or stop it."
                )
                return
            asyncio.create_task(run_turn(content, retry=retry, display=display))

        try:
            while True:
                try:
                    message = await ws.receive_json()
                except (json.JSONDecodeError, UnicodeDecodeError):
                    await reject_input("Invalid WebSocket message: expected JSON.")
                    continue

                now = asyncio.get_running_loop().time()
                while (
                    inbound_times
                    and now - inbound_times[0] > _WS_RATE_LIMIT_WINDOW_SECONDS
                ):
                    inbound_times.popleft()
                if len(inbound_times) >= _WS_RATE_LIMIT_COUNT:
                    await reject_input("Too many WebSocket messages; reconnect and try again.")
                    await ws.close(code=1008)
                    return
                inbound_times.append(now)

                if not isinstance(message, dict):
                    await reject_input("Invalid WebSocket message: expected an object.")
                    continue
                kind = message.get("type")
                if not isinstance(kind, str):
                    await reject_input("Invalid WebSocket message: missing string type.")
                    continue
                if kind == "approval":
                    _resolve_pending(message.get("decision", "deny"))
                elif kind == "directory_response":
                    _resolve_pending(
                        json.dumps(
                            {
                                "granted": bool(message.get("granted")),
                                "path": message.get("path", ""),
                                "writable": bool(message.get("writable", False)),
                            }
                        )
                    )
                elif kind == "tool_response":
                    _resolve_pending(
                        json.dumps({"approved": bool(message.get("approved"))})
                    )
                elif kind == "plan_response":
                    _resolve_pending(
                        json.dumps(
                            {
                                "approved": bool(message.get("approved")),
                                "mode": message.get("mode", "interactive"),
                                "feedback": message.get("feedback", ""),
                            }
                        )
                    )
                elif kind in ("team_response", "items_response"):
                    _resolve_pending(
                        json.dumps(
                            {
                                "approved": bool(message.get("approved")),
                                "feedback": message.get("feedback", ""),
                                **(
                                    {"enable_chat": bool(message.get("enable_chat"))}
                                    if "enable_chat" in message
                                    else {}
                                ),
                                **(
                                    {"members": message.get("members")}
                                    if isinstance(message.get("members"), list)
                                    else {}
                                ),
                            }
                        )
                    )
                elif kind == "connector_response":
                    _resolve_pending(
                        json.dumps({"approved": bool(message.get("approved"))})
                    )
                elif kind == "question_response":
                    _resolve_pending(str(message.get("answer", "")))
                elif kind == "allow_anyway":
                    # §8.4: the user clicked "Allow anyway" on a reviewer-denied tool card.
                    # Registers a ONE-SHOT exact-action approval on the engine; the GUI then
                    # sends its canned retry message through the normal user_message path,
                    # and the re-proposed identical action runs without the reviewer/card.
                    name = message.get("name")
                    arguments = message.get("arguments")
                    if not isinstance(name, str) or not name:
                        await reject_input("Invalid allow_anyway: missing tool name.")
                    elif arguments is not None and not isinstance(arguments, dict):
                        await reject_input("Invalid allow_anyway: arguments must be an object.")
                    else:
                        engine.approve_action_once(name, arguments or {})
                elif kind == "interrupt":
                    manager.stop_session(session_id)
                elif kind == "retry":
                    # Re-run after a provider error (engine guards on the error-notice
                    # tail, so a stray frame is a no-op that still ends with turn_done).
                    await claim_turn(retry=True)
                elif kind == "set_mode":
                    try:
                        new_mode = Mode(message.get("mode"))
                    except (TypeError, ValueError):
                        pass
                    else:
                        previous = engine.permissions.mode
                        engine.permissions.mode = new_mode
                        if previous is not new_mode:
                            manager.audit_autonomy_change(
                                session_id, "mode", previous.value, new_mode.value
                            )
                            # The transcript records which mode each exchange ran under
                            # (owner ruling 2026-08-24): full explainer the first time a
                            # session enters Auto-Approve, a one-line marker otherwise.
                            # Server-authored + persisted, so reloads show it in place
                            # exactly once instead of re-announcing on every restart.
                            from coworker.permissions import (
                                AUTO_APPROVE_NOTICE,
                                MODE_LABELS,
                            )

                            if new_mode is Mode.AUTO_APPROVE and not any(
                                m.get("kind") == "mode_notice"
                                for m in engine.messages
                            ):
                                engine._append_notice(
                                    "mode_notice",
                                    AUTO_APPROVE_NOTICE,
                                    title="Auto-approve is on.",
                                )
                                notice_data = {
                                    "title": "Auto-approve is on.",
                                    "text": AUTO_APPROVE_NOTICE,
                                }
                            else:
                                label = MODE_LABELS.get(
                                    new_mode.value, new_mode.value
                                )
                                engine._append_notice(
                                    "mode_switch", f"{label} is on."
                                )
                                notice_data = {"text": f"{label} is on."}
                            # A mode switch with no accompanying message is bookkeeping,
                            # not activity (owner ruling 2026-08-24): the transcript
                            # records it, Recents doesn't reorder. The next real turn's
                            # checkpoint save bumps recency as usual.
                            manager.save(session_id, engine, touch=False)
                            manager.sync_cached_reviewers()
                            await manager.broadcast_session(
                                session_id,
                                {"type": "mode_notice", "data": notice_data},
                            )
                elif kind == "set_model":
                    model = message.get("model")
                    if model is not None and not isinstance(model, str):
                        await reject_input("Invalid model: expected a string.")
                    else:
                        await _apply_model(model)
                elif kind == "user_message":
                    raw_text = message.get("text")
                    if raw_text is None:
                        raw_text = ""
                    if not isinstance(raw_text, str):
                        await reject_input("Invalid message text: expected a string.")
                        continue
                    text = raw_text.strip()
                    raw_attachments = message.get("attachments")
                    attachments = [] if raw_attachments is None else raw_attachments
                    # Reject an oversized frame instead of buffering it into a turn. Send a
                    # visible error so the surface can tell the user, and drop the message.
                    if not isinstance(attachments, list):
                        await reject_input("Invalid attachments: expected a list.")
                        continue
                    reject = None
                    if len(text) > _MAX_MESSAGE_TEXT_CHARS:
                        reject = (
                            f"Message too long ({len(text)} chars; "
                            f"limit {_MAX_MESSAGE_TEXT_CHARS})."
                        )
                    elif len(attachments) > _MAX_ATTACHMENTS:
                        reject = (
                            f"Too many attachments ({len(attachments)}; "
                            f"limit {_MAX_ATTACHMENTS})."
                        )
                    elif any(not isinstance(a, dict) for a in attachments):
                        reject = "Invalid attachment: expected an object."
                    elif _json_value_size(attachments) > _MAX_ATTACHMENTS_BYTES:
                        reject = "Attachments too large (limit 15 MB per message)."
                    else:
                        for attachment in attachments:
                            attachment_kind = attachment.get("kind")
                            name = attachment.get("name")
                            mime = attachment.get("mime")
                            if attachment_kind not in {"image", "pdf", "text"}:
                                reject = "Invalid attachment kind."
                            elif name is not None and (
                                not isinstance(name, str) or len(name) > 1024
                            ):
                                reject = "Invalid attachment name."
                            elif mime is not None and (
                                not isinstance(mime, str) or len(mime) > 255
                            ):
                                reject = "Invalid attachment MIME type."
                            elif attachment_kind == "image":
                                data = attachment.get("data_url")
                                if (
                                    not isinstance(data, str)
                                    or not data.startswith("data:image/")
                                    or ";base64," not in data
                                    or len(data) > MAX_IMAGE_CHARS
                                ):
                                    reject = "Invalid or oversized image attachment."
                            elif attachment_kind == "pdf":
                                data = attachment.get("data_url")
                                if (
                                    not isinstance(data, str)
                                    or not data.startswith(
                                        "data:application/pdf;base64,"
                                    )
                                    or len(data) > MAX_PDF_CHARS
                                ):
                                    reject = "Invalid or oversized PDF attachment."
                            else:
                                body = attachment.get("text")
                                if (
                                    not isinstance(body, str)
                                    or len(body) > MAX_TEXT_CHARS
                                ):
                                    reject = "Invalid or oversized text attachment."
                            if reject is not None:
                                break
                    if reject is not None:
                        await reject_input(reject)
                        continue
                    # The composer sends its visible model with every message — the FIRST
                    # one binds the session (race-proof across reconnects; see api.ts
                    # Session.userMessage), later ones may switch it (notice persisted).
                    model = message.get("model")
                    if model is not None and not isinstance(model, str):
                        await reject_input("Invalid model: expected a string.")
                        continue
                    # Force-run (SKILLS-SPEC §4.1 #3): the composer's `/skill` pick rides as a
                    # separate field. Validated against the session's effective menu — a muted
                    # or unknown skill is a visible error, never a silent no-op (§4.6 #15).
                    # The model-facing framing goes into `content`; the transcript shows the
                    # user's literal "/name …" line via the `_display` sidecar (one bubble).
                    skill = message.get("skill")
                    display = None
                    if skill is not None:
                        if not isinstance(skill, str) or not skill.strip():
                            await reject_input("Invalid skill: expected a name.")
                            continue
                        skill = skill.strip()
                        menu = manager.effective_skill_names(session_id, workspace)
                        if skill not in menu:
                            await reject_input(
                                f"Skill '{skill}' is not available in this session."
                            )
                            continue
                        display = f"/{skill}" + (f" {text}" if text else "")
                        text = (
                            f'Use the skill "{skill}" for this request: first call '
                            f'load_skill("{skill}") and follow its instructions.'
                            + (f"\n\n{text}" if text else "")
                        )
                    await _apply_model(model)
                    if text or attachments:
                        content = build_user_content(text, attachments)
                        await claim_turn(content=content, display=display)
                else:
                    await reject_input(f"Unknown WebSocket message type: {kind}.")
        except WebSocketDisconnect:
            pass
        finally:
            manager.unregister_session_client(session_id, ws.send_json)
            # Nobody is watching this session any more: a prompt parked inline would wait
            # invisibly, so it moves to the Inbox (and a bound channel) right now — not
            # on the next engine rebuild.
            try:
                await manager.promote_pending_prompts(session_id)
            except Exception:
                pass

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        """App-wide event stream (session-independent): the GUI keeps one open for
        pushes like automation_run_started (the UX-026 toast). Read-only — inbound
        frames are ignored; the receive loop just detects disconnect."""
        if not _websocket_authenticated(ws):
            await ws.close(code=1008)
            return
        if not _origin_allowed(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol="openworker" if api_token else None)
        manager.register_event_client(ws.send_json)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            manager.unregister_event_client(ws.send_json)

    # Remote homes: the join-acceptor is a separate module mounted here (never
    # inlined) so the cloud service can deploy it without the engine.
    from ..remote.acceptor import mount_acceptor
    from ..remote.audit import AuditLog
    from ..remote.registry import MachinesRegistry

    app.state.remote_acceptor = mount_acceptor(
        app,
        MachinesRegistry(manager.session_store.db_path),
        ws_authenticated=_websocket_authenticated,
        origin_allowed=_origin_allowed,
        api_token=api_token,
        # The desktop's SecretStore IS the keys wallet (§Keys wallet).
        wallet=manager.secrets,
        audit=AuditLog(manager.session_store.db_path.parent / "remote-audit.jsonl"),
        # Org policy + audit sink (spec §Audit export): a desktop controller has
        # neither; an embedder sets them on the manager before create_app.
        policy_for=getattr(manager, "policy_for", None),
        audit_sink=getattr(manager, "audit_sink", None),
        policy_status=getattr(manager, "policy_status", None),
    )

    # Union view (spec §"Union view on the signed-in desktop"): a signed-in
    # desktop also shows the hosted cloud's machines, proxied through here so
    # the cloud session token never enters the webview. Signed out or expired
    # degrades the cloud section only — never the local path.
    from ..config import load_config as _load_cfg
    from ..remote.cloudproxy import mount_cloud_proxy

    _machines_base = _load_cfg().cloud_machines_base
    if _machines_base:

        async def _cloud_machines_token() -> tuple[str, Optional[str]]:
            from .. import cloud as _cloud

            profile = manager.secrets.get(_cloud.CLOUD_AUTH_PROFILE) or {}
            if not profile.get("access_token"):
                return "signed_out", None
            token = await asyncio.to_thread(
                _cloud.fresh_access_token, manager.secrets, _load_cfg()
            )
            return ("ok", token) if token else ("expired", None)

        mount_cloud_proxy(
            app,
            token_provider=_cloud_machines_token,
            base_url=_machines_base,
            wallet=manager.secrets,
            ws_authenticated=_websocket_authenticated,
            origin_allowed=_origin_allowed,
        )

    return app


def _parse_json(s: str) -> dict[str, Any]:
    """Parse a structured Inbox resolution (directory/plan carry their reply as a JSON string)."""
    try:
        v = json.loads(s) if s else {}
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _openai_response(model: str, turn: AssistantTurn) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": turn.text or ""}
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in turn.tool_calls
        ]
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:12],
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": turn.finish_reason or "stop",
            }
        ],
    }
