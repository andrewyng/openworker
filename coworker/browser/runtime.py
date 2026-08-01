"""Isolated Playwright runtime for the in-app Browser Use product.

The public API is deliberately transport neutral.  Every synchronous call is
executed on one dedicated asyncio/Playwright thread, while each conversation
owns an isolated browser context and an operation lock.  Model-facing adapters
bind a trusted session id once via :meth:`BrowserRuntime.bind`; a model never
chooses a session or profile id.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import os
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..browser_security.actions import BrowserActionPolicy, BrowserActionRequest
from .errors import BrowserRuntimeError, browser_error

EventCallback = Callable[[dict[str, Any]], Any]
logger = logging.getLogger(__name__)

_REF_RE = re.compile(r"\[ref=([A-Za-z0-9_-]{1,80})\]")
_VALID_REF_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_CHROME_UA_RE = re.compile(
    r"\b(?P<product>HeadlessChrome|Chrome)/"
    r"(?P<version>\d+(?:\.\d+){1,3})\b"
)
_INPUT_ROLE_RE = re.compile(
    r"^(\s*-\s+(?:textbox|searchbox|combobox|spinbutton)\b.*?\[ref=[^\]]+\]"
    r"(?:\s+\[box=[^\]]+\])?)(?::.*)?$"
)
_WAIT_UNTIL = {"commit", "domcontentloaded", "load", "networkidle"}
_DISPLAY_JPEG_QUALITY = 84
_PERSONAL_TARGET_RE = re.compile(
    r"\b(email|e-mail|phone|telephone|address|postal|postcode|zip|"
    r"full\s+name|first\s+name|last\s+name|date\s+of\s+birth|birthday)\b",
    re.IGNORECASE,
)
_AUTH_TARGET_RE = re.compile(
    r"\b(password|passcode|pin|one[- ]?time|otp|verification\s+code|"
    r"security\s+code|username)\b",
    re.IGNORECASE,
)
_FINANCIAL_TARGET_RE = re.compile(
    r"\b(card|cvv|cvc|bank|account\s+number|routing|iban|swift|"
    r"payment|billing)\b",
    re.IGNORECASE,
)
_HEALTH_TARGET_RE = re.compile(
    r"\b(health|medical|diagnos|patient|prescription|insurance)\b",
    re.IGNORECASE,
)


def _chrome_compatible_user_agent(
    value: str, *, browser_version: str = ""
) -> str | None:
    """Keep Chromium's real platform/version while removing its headless token.

    Some mainstream web apps treat ``HeadlessChrome`` as an unsupported browser
    even when the bundled Chromium is much newer than their minimum version.
    This is a compatibility normalization only: automation signals such as
    ``navigator.webdriver`` remain untouched and truthful.
    """

    match = _CHROME_UA_RE.search(value)
    if match is None:
        return None
    if browser_version:
        runtime_major = browser_version.split(".", 1)[0]
        if match.group("version").split(".", 1)[0] != runtime_major:
            return None
    if match.group("product") == "HeadlessChrome":
        return (
            value[: match.start("product")]
            + "Chrome"
            + value[match.end("product") :]
        )
    return value


@dataclass
class _Snapshot:
    snapshot_id: str
    content: str
    refs: set[str]
    url: str
    title: str
    chunks: list[str]
    document_generation: int
    viewport_generation: int
    input_generation: int
    cursors: dict[str, int] = field(default_factory=dict)


@dataclass
class _DirectInputState:
    mouse_buttons: set[str] = field(default_factory=set)
    keys: set[str] = field(default_factory=set)


@dataclass
class _DirectNavigationScope:
    expected_url: tuple[str, str, int | None, str, str] | None
    claimed: bool = False
    claimed_event: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class _Tab:
    tab_id: str
    page: Any
    latest: _Snapshot | None = None
    dialog: Any | None = None
    dialog_event: asyncio.Event = field(default_factory=asyncio.Event)
    frame_sequence: int = 0
    latest_frame_id: str | None = None
    cdp: Any | None = None
    guard_cdp: Any | None = None
    screencasting: bool = False
    title: str = ""
    can_go_back: bool = False
    can_go_forward: bool = False
    console_logs: list[dict[str, Any]] = field(default_factory=list)
    download_armed: bool = False
    document_generation: int = 0
    viewport_generation: int = 0
    input_generation: int = 0
    input_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    agent_navigation_depth: int = 0
    agent_action_depth: int = 0
    direct_navigation_scopes: dict[str, _DirectNavigationScope] = field(
        default_factory=dict
    )
    direct_navigation_network_ids: set[str] = field(default_factory=set)
    direct_inputs: dict[str, _DirectInputState] = field(default_factory=dict)


@dataclass
class _Session:
    session_id: str
    context: Any
    profile_id: str | None
    proxy_credentials: tuple[str, str] | None = None
    tabs: dict[str, _Tab] = field(default_factory=dict)
    active_tab_id: str | None = None
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    counter: int = 0
    event_sequence: int = 0
    streaming: bool = False
    screencast_quality: int = _DISPLAY_JPEG_QUALITY
    device_scale_factor: float = 1.0
    visible: bool = True
    clipboard: str = ""
    default_viewport: tuple[int, int] = (1280, 900)
    developer_mode: bool = False
    allowed_file_roots: tuple[Path, ...] = ()
    navigation_guard: Callable[[str], bool] | None = None


class BoundBrowserSession:
    """A trusted session-bound facade suitable for constructing tool closures."""

    def __init__(self, runtime: "BrowserRuntime", session_id: str) -> None:
        self._runtime = runtime
        self.session_id = session_id

    def state(self) -> dict[str, Any]:
        return self._runtime.state(self.session_id)

    def navigate(self, url: str, **kwargs: Any) -> dict[str, Any]:
        return self._runtime.navigate(self.session_id, url, **kwargs)

    def history(self, direction: str) -> dict[str, Any]:
        return self._runtime.history(self.session_id, direction)

    def snapshot(self, **kwargs: Any) -> dict[str, Any]:
        return self._runtime.snapshot(self.session_id, **kwargs)

    def snapshot_more(self, cursor: str) -> dict[str, Any]:
        return self._runtime.snapshot_more(self.session_id, cursor)

    def screenshot(self, **kwargs: Any) -> dict[str, Any]:
        return self._runtime.screenshot(self.session_id, **kwargs)

    def click(self, tab_id: str, snapshot_id: str, ref: str) -> dict[str, Any]:
        return self._runtime.click(self.session_id, tab_id, snapshot_id, ref)

    def fill(
        self, tab_id: str, snapshot_id: str, ref: str, value: str
    ) -> dict[str, Any]:
        return self._runtime.fill(
            self.session_id, tab_id, snapshot_id, ref, value
        )

    def press(
        self, tab_id: str, snapshot_id: str, ref: str, key: str
    ) -> dict[str, Any]:
        return self._runtime.press(self.session_id, tab_id, snapshot_id, ref, key)

    def select(
        self, tab_id: str, snapshot_id: str, ref: str, value: str
    ) -> dict[str, Any]:
        return self._runtime.select(
            self.session_id, tab_id, snapshot_id, ref, value
        )

    def hover(self, tab_id: str, snapshot_id: str, ref: str) -> dict[str, Any]:
        return self._runtime.hover(self.session_id, tab_id, snapshot_id, ref)

    def scroll(
        self,
        *,
        delta_x: float = 0,
        delta_y: float = 0,
        tab_id: str | None = None,
        snapshot_id: str | None = None,
        ref: str | None = None,
    ) -> dict[str, Any]:
        return self._runtime.scroll(
            self.session_id,
            delta_x=delta_x,
            delta_y=delta_y,
            tab_id=tab_id,
            snapshot_id=snapshot_id,
            ref=ref,
        )

    def tabs(self) -> dict[str, Any]:
        return self._runtime.tabs(self.session_id)

    def select_tab(self, tab_id: str) -> dict[str, Any]:
        return self._runtime.select_tab(self.session_id, tab_id)

    def close_tab(self, tab_id: str) -> dict[str, Any]:
        return self._runtime.close_tab(self.session_id, tab_id)

    def dialog(
        self, action: str, prompt_text: str | None = None
    ) -> dict[str, Any]:
        return self._runtime.dialog(
            self.session_id, action, prompt_text=prompt_text
        )

    def set_visibility(self, visible: bool) -> dict[str, Any]:
        return self._runtime.set_visibility(self.session_id, visible)

    def set_viewport(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        dpr: float | None = None,
        reset: bool = False,
    ) -> dict[str, Any]:
        return self._runtime.set_viewport(
            self.session_id,
            width=width,
            height=height,
            dpr=dpr,
            reset=reset,
        )

    def finalize_tabs(self, keep_tab_ids: list[str]) -> dict[str, Any]:
        return self._runtime.finalize_tabs(self.session_id, keep_tab_ids)

    def coordinate_click(
        self,
        tab_id: str,
        x: float,
        y: float,
        *,
        button: str = "left",
        click_count: int = 1,
    ) -> dict[str, Any]:
        return self._runtime.coordinate_click(
            self.session_id,
            tab_id,
            x,
            y,
            button=button,
            click_count=click_count,
        )

    def coordinate_move(
        self, tab_id: str, x: float, y: float
    ) -> dict[str, Any]:
        return self._runtime.coordinate_move(self.session_id, tab_id, x, y)

    def coordinate_drag(
        self, tab_id: str, path: list[dict[str, float]]
    ) -> dict[str, Any]:
        return self._runtime.coordinate_drag(self.session_id, tab_id, path)

    def type_text(self, tab_id: str, text: str) -> dict[str, Any]:
        return self._runtime.type_text(self.session_id, tab_id, text)

    def keypress(self, tab_id: str, keys: list[str]) -> dict[str, Any]:
        return self._runtime.keypress(self.session_id, tab_id, keys)

    def clipboard(
        self, action: str, text: str | None = None
    ) -> dict[str, Any]:
        return self._runtime.clipboard(self.session_id, action, text=text)

    def console_logs(
        self,
        tab_id: str,
        *,
        levels: list[str] | None = None,
        filter_text: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        return self._runtime.console_logs(
            self.session_id,
            tab_id,
            levels=levels,
            filter_text=filter_text,
            limit=limit,
        )

    def download(
        self,
        tab_id: str,
        snapshot_id: str,
        ref: str,
        *,
        destination: str | None = None,
    ) -> dict[str, Any]:
        return self._runtime.download(
            self.session_id,
            tab_id,
            snapshot_id,
            ref,
            destination=destination,
        )

    def upload(
        self,
        tab_id: str,
        snapshot_id: str,
        ref: str,
        paths: list[str],
    ) -> dict[str, Any]:
        return self._runtime.upload(
            self.session_id, tab_id, snapshot_id, ref, paths
        )

    def cdp(
        self, tab_id: str, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        return self._runtime.cdp(
            self.session_id, tab_id, method, params=params
        )

    def dom_evaluate(
        self,
        tab_id: str,
        expression: str,
        *,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._runtime.dom_evaluate(
            self.session_id, tab_id, expression, args=args
        )

    def close(self) -> dict[str, Any]:
        return self._runtime.close_session(self.session_id)

    def classify_action(
        self, action: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        return self._runtime.classify_action(
            self.session_id, action, arguments
        )


class BrowserRuntime:
    """One Playwright driver/browser with isolated contexts per conversation."""

    def __init__(
        self,
        *,
        headless: bool = True,
        channel: str | None = "chromium",
        viewport: tuple[int, int] = (1280, 900),
        device_scale_factor: float = 2.0,
        action_timeout_ms: int = 10_000,
        visual_ack_timeout_ms: int = 320,
        launch_options: dict[str, Any] | None = None,
    ) -> None:
        self.headless = headless
        self.channel = channel
        self.viewport = viewport
        if not 1 <= float(device_scale_factor) <= 3:
            raise ValueError("device_scale_factor must be between 1 and 3")
        # Chromium's CDP screencast is CSS-pixel sized even on HiDPI contexts,
        # whereas settled Playwright screenshots contain device pixels. A 2x
        # context therefore gives the shared view a crisp final frame on Retina
        # and scaled displays without changing responsive CSS layout.
        self.device_scale_factor = float(device_scale_factor)
        self.action_timeout_ms = action_timeout_ms
        self.visual_ack_timeout_ms = max(0, min(500, int(visual_ack_timeout_ms)))
        self.launch_options = dict(launch_options or {})
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = threading.Event()
        self._start_lock = threading.Lock()
        self._startup_error: BaseException | None = None
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._user_agent: str | None = None
        self._action_policy = BrowserActionPolicy()
        self._sessions: dict[str, _Session] = {}
        self._profile_leases: dict[str, str] = {}
        self._subscribers: dict[str, tuple[EventCallback, str | None, set[str] | None]] = {}
        self._subscriber_lock = threading.RLock()
        self._cursor_acks: dict[
            str, tuple[str, str | None, asyncio.Event]
        ] = {}
        self._closed = False

    # -- lifecycle and threading -------------------------------------------------

    def start(self) -> "BrowserRuntime":
        with self._start_lock:
            if self._closed:
                raise browser_error("RUNTIME_CLOSED", "Browser runtime is closed")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name="openworker-browser-runtime",
                    daemon=True,
                )
                self._thread.start()
        self._started.wait(timeout=30)
        if not self._started.is_set():
            raise browser_error("SETUP_ERROR", "Browser runtime startup timed out")
        if self._startup_error is not None:
            raise browser_error(
                "SETUP_ERROR",
                "Playwright Chromium could not be started",
                details=str(self._startup_error),
            )
        return self

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_start())
        except BaseException as exc:  # startup is reported synchronously to caller
            self._startup_error = exc
            self._started.set()
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            return
        self._started.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    async def _async_start(self) -> None:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        caller_options = dict(self.launch_options)
        caller_args = list(caller_options.pop("args", []) or [])
        mandatory_args = [
            "--proxy-bypass-list=<-loopback>",
            "--disable-quic",
            # Chromium's HTTP/2 tunnel can stall behind the authenticated,
            # DNS-pinning MVP proxy on sites such as google.com. Keep transport
            # deterministic and fail-closed by negotiating HTTP/1.1 instead.
            "--disable-http2",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        ]
        # Callers may add flags but cannot silently remove the fail-closed browser
        # transport defaults.  De-duplicate while preserving deterministic order.
        merged_args = list(dict.fromkeys([*mandatory_args, *caller_args]))
        options = {
            "headless": self.headless,
            **caller_options,
            "args": merged_args,
        }
        if self.channel:
            options["channel"] = self.channel
        self._browser = await self._playwright.chromium.launch(**options)
        try:
            cdp = await self._browser.new_browser_cdp_session()
            try:
                version = await cdp.send("Browser.getVersion")
            finally:
                await cdp.detach()
            raw_user_agent = str(version.get("userAgent") or "").strip()
            self._user_agent = _chrome_compatible_user_agent(
                raw_user_agent,
                browser_version=self._browser.version,
            )
            if self._user_agent is None:
                logger.warning(
                    "could not derive a Chrome-compatible user agent from "
                    "bundled Chromium; retaining the browser default"
                )
        except Exception as exc:
            self._user_agent = None
            logger.warning(
                "could not inspect bundled Chromium user agent; retaining "
                "the browser default: %s",
                exc,
            )
        self._browser.on("disconnected", self._on_browser_disconnected)

    def _on_browser_disconnected(self) -> None:
        for session in tuple(self._sessions.values()):
            for tab in session.tabs.values():
                tab.latest = None
            self._emit(
                {
                    "type": "browser_state",
                    "version": 1,
                    "session_id": session.session_id,
                    "status": "crashed",
                }
            )

    def _call(self, factory: Callable[[], Any], *, timeout: float = 45) -> Any:
        self.start()
        if threading.current_thread() is self._thread:
            raise browser_error(
                "RUNTIME_REENTRANCY",
                "A browser callback cannot synchronously call BrowserRuntime",
            )
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(factory(), self._loop)
        try:
            return future.result(timeout=timeout)
        except BrowserRuntimeError:
            raise
        except TimeoutError as exc:
            future.cancel()
            raise browser_error("ACTION_TIMEOUT", "Browser operation timed out") from exc

    def close(self) -> None:
        with self._start_lock:
            if self._closed:
                return
            if self._thread is None:
                self._closed = True
                return
        try:
            self._call(self._async_close, timeout=20)
        finally:
            self._closed = True
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=10)

    async def _async_close(self) -> None:
        for session_id in list(self._sessions):
            await self._close_session(session_id)
        if self._browser is not None and self._browser.is_connected():
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._browser = None
        self._playwright = None

    def __enter__(self) -> "BrowserRuntime":
        return self.start()

    def __exit__(self, *_args: Any) -> None:
        self.close()

    # -- events ------------------------------------------------------------------

    def subscribe(
        self,
        callback: EventCallback,
        *,
        session_id: str | None = None,
        event_types: set[str] | None = None,
    ) -> str:
        token = "sub_" + secrets.token_urlsafe(10)
        with self._subscriber_lock:
            self._subscribers[token] = (
                callback,
                session_id,
                set(event_types) if event_types else None,
            )
        return token

    def unsubscribe(self, token: str) -> None:
        with self._subscriber_lock:
            self._subscribers.pop(token, None)

    def _emit(self, event: dict[str, Any]) -> None:
        with self._subscriber_lock:
            subscribers = tuple(self._subscribers.values())
        for callback, session_id, event_types in subscribers:
            if session_id and event.get("session_id") != session_id:
                continue
            if event_types and event.get("type") not in event_types:
                continue
            try:
                result = callback(event)
                if inspect.isawaitable(result):
                    asyncio.create_task(result)
            except Exception:
                # Presentation must never stop browser execution.
                continue

    def _has_subscriber(self, event_type: str, session_id: str) -> bool:
        with self._subscriber_lock:
            subscribers = tuple(self._subscribers.values())
        return any(
            (bound_session is None or bound_session == session_id)
            and (event_types is None or event_type in event_types)
            for _callback, bound_session, event_types in subscribers
        )

    def acknowledge_cursor(
        self,
        session_id: str,
        action_id: str,
        *,
        frame_id: str | None = None,
    ) -> dict[str, Any]:
        """Acknowledge that the primary viewport rendered an action's move phase."""

        return self._call(
            lambda: self._acknowledge_cursor(session_id, action_id, frame_id)
        )

    async def _acknowledge_cursor(
        self, session_id: str, action_id: str, frame_id: str | None
    ) -> dict[str, Any]:
        pending = self._cursor_acks.get(action_id)
        if pending is None:
            return {"ok": True, "accepted": False}
        expected_session, expected_frame, event = pending
        if expected_session != session_id:
            return {"ok": True, "accepted": False}
        if frame_id and expected_frame and frame_id != expected_frame:
            return {"ok": True, "accepted": False}
        event.set()
        return {"ok": True, "accepted": True}

    async def _pace_visual_move(
        self,
        session: _Session,
        action_id: str,
        frame_id: str | None,
        event: asyncio.Event | None,
    ) -> None:
        if event is None or self.visual_ack_timeout_ms <= 0:
            return
        try:
            await asyncio.wait_for(
                event.wait(), timeout=self.visual_ack_timeout_ms / 1000
            )
        except asyncio.TimeoutError:
            pass
        finally:
            self._cursor_acks.pop(action_id, None)

    def bind(self, session_id: str) -> BoundBrowserSession:
        return BoundBrowserSession(self, session_id)

    # -- sessions ----------------------------------------------------------------

    def create_session(
        self,
        session_id: str,
        *,
        storage_state: dict[str, Any] | None = None,
        profile_id: str | None = None,
        proxy: dict[str, str] | None = None,
        viewport: tuple[int, int] | None = None,
        developer_mode: bool = False,
        allowed_file_roots: list[str | Path] | tuple[str | Path, ...] | None = None,
        navigation_guard: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._create_session(
                session_id,
                storage_state=storage_state,
                profile_id=profile_id,
                proxy=proxy,
                viewport=viewport,
                developer_mode=developer_mode,
                allowed_file_roots=allowed_file_roots,
                navigation_guard=navigation_guard,
            )
        )

    async def _create_session(
        self,
        session_id: str,
        *,
        storage_state: dict[str, Any] | None,
        profile_id: str | None,
        proxy: dict[str, str] | None,
        viewport: tuple[int, int] | None,
        developer_mode: bool,
        allowed_file_roots: list[str | Path] | tuple[str | Path, ...] | None,
        navigation_guard: Callable[[str], bool] | None,
    ) -> dict[str, Any]:
        if not session_id:
            raise browser_error("INVALID_ARGUMENT", "session_id is required")
        if session_id in self._sessions:
            return await self._state(session_id)
        if profile_id and profile_id in self._profile_leases:
            raise browser_error(
                "PROFILE_IN_USE",
                "Saved browser profile is already in use",
                owner_session_id=self._profile_leases[profile_id],
            )
        assert self._browser is not None
        width, height = viewport or self.viewport
        options: dict[str, Any] = {
            "viewport": {"width": int(width), "height": int(height)},
            "device_scale_factor": self.device_scale_factor,
            "service_workers": "block",
            # Downloads are accepted only while browser_download has armed one
            # exact fresh ref.  The page-level listener cancels every ambient
            # or drive-by download.
            "accept_downloads": True,
        }
        if self._user_agent is not None:
            options["user_agent"] = self._user_agent
        if storage_state is not None:
            options["storage_state"] = storage_state
        if proxy is not None:
            options["proxy"] = proxy
        context = await self._browser.new_context(**options)
        context.set_default_timeout(self.action_timeout_ms)
        roots = self._normalize_file_roots(allowed_file_roots)
        proxy_credentials = None
        if proxy is not None and (
            proxy.get("username") is not None or proxy.get("password") is not None
        ):
            proxy_credentials = (
                str(proxy.get("username") or ""),
                str(proxy.get("password") or ""),
            )
        session = _Session(
            session_id,
            context,
            profile_id,
            proxy_credentials=proxy_credentials,
            device_scale_factor=self.device_scale_factor,
            default_viewport=(int(width), int(height)),
            developer_mode=bool(developer_mode),
            allowed_file_roots=roots,
            navigation_guard=navigation_guard,
        )
        self._sessions[session_id] = session
        if profile_id:
            self._profile_leases[profile_id] = session_id
        page = await context.new_page()
        await self._register_page(session, page, activate=True)

        async def activate_popup(new_page: Any) -> None:
            async with session.operation_lock:
                tab = await self._register_page(session, new_page, activate=True)
                try:
                    await new_page.wait_for_load_state(
                        "domcontentloaded", timeout=self.action_timeout_ms
                    )
                except Exception:
                    pass
                if session.streaming:
                    await self._handoff_screencast(session, tab)
                try:
                    await self._capture_frame(
                        session, tab, quality=_DISPLAY_JPEG_QUALITY
                    )
                except Exception:
                    pass
                await self._emit_state(session, status="open")

        # Register only after the initial page exists, so "page" means a real
        # new tab/popup and can atomically become the active streamed tab.
        context.on("page", activate_popup)
        await self._emit_state(session, status="open")
        return await self._state(session_id)

    async def _register_page(
        self, session: _Session, page: Any, *, activate: bool
    ) -> _Tab:
        for tab in session.tabs.values():
            if tab.page is page:
                if activate:
                    session.active_tab_id = tab.tab_id
                return tab
        session.counter += 1
        tab = _Tab("tab_" + secrets.token_urlsafe(8), page)
        session.tabs[tab.tab_id] = tab
        if activate or session.active_tab_id is None:
            session.active_tab_id = tab.tab_id

        def invalidate(frame: Any) -> None:
            if frame is page.main_frame:
                tab.latest = None
                tab.document_generation += 1
                # Redirect hops have all completed once the new main document
                # commits.  Do not let a direct-user chain identifier become a
                # reusable permission for a later navigation.
                tab.direct_navigation_network_ids.clear()

        async def on_dialog(dialog: Any) -> None:
            tab.dialog = dialog
            tab.dialog_event.set()
            tab.latest = None
            self._emit(
                {
                    "type": "browser_dialog",
                    "version": 1,
                    "session_id": session.session_id,
                    "tab_id": tab.tab_id,
                    "dialog_type": dialog.type,
                    "message": dialog.message,
                    "default_value": dialog.default_value,
                }
            )
            await self._emit_state(session, status="open")

        async def reject_unexpected_download(download: Any) -> None:
            if not tab.download_armed:
                await download.cancel()

        def record_console(message: Any) -> None:
            try:
                entry = {
                    "level": str(message.type or "log"),
                    "text": str(message.text or "")[:20_000],
                    "location": dict(message.location or {}),
                }
            except Exception:
                entry = {
                    "level": "log",
                    "text": "<console message unavailable>",
                    "location": {},
                }
            tab.console_logs.append(entry)
            del tab.console_logs[:-500]

        def record_page_error(error: Any) -> None:
            tab.console_logs.append(
                {
                    "level": "error",
                    "text": str(error)[:20_000],
                    "location": {},
                }
            )
            del tab.console_logs[:-500]

        page.on("framenavigated", invalidate)
        page.on("dialog", on_dialog)
        page.on("download", reject_unexpected_download)
        page.on("console", record_console)
        page.on("pageerror", record_page_error)
        if session.navigation_guard is not None:
            await self._start_navigation_guard(session, tab)
        return tab

    async def _start_navigation_guard(
        self, session: _Session, tab: _Tab
    ) -> None:
        """Intercept every top-level Document request, including redirect hops.

        Playwright routing intentionally sees only the first URL in a redirect
        chain. CDP Fetch interception pauses each network request before it is
        sent, so a 30x cannot cross the persisted hostname boundary unnoticed.
        """

        if tab.guard_cdp is not None:
            return
        cdp = await session.context.new_cdp_session(tab.page)
        frame_tree = await cdp.send("Page.getFrameTree")
        root_frame_id = str(
            (frame_tree.get("frameTree") or {}).get("frame", {}).get("id") or ""
        )

        async def on_paused(params: dict[str, Any]) -> None:
            request_id = str(params.get("requestId") or "")
            network_id = str(params.get("networkId") or "")
            request = params.get("request") or {}
            url = str(request.get("url") or "")
            frame_id = str(params.get("frameId") or "")
            # A trusted direct navigation is attributed to one exact address-bar
            # request or to the first top-level Document request synchronously
            # caused by one concrete viewport event.  Redirects retain Chromium's
            # Network request id.  Agent operations always take precedence here,
            # so an overlapping agent navigation cannot consume a user's scope.
            agent_driven = bool(
                tab.agent_navigation_depth or tab.agent_action_depth
            )
            direct_request = (
                not agent_driven
                and bool(network_id)
                and network_id in tab.direct_navigation_network_ids
            )
            if frame_id == root_frame_id and not agent_driven and not direct_request:
                request_key = _navigation_url_key(url)
                for scope in tuple(tab.direct_navigation_scopes.values()):
                    if scope.claimed:
                        continue
                    if (
                        scope.expected_url is not None
                        and request_key != scope.expected_url
                    ):
                        continue
                    scope.claimed = True
                    scope.claimed_event.set()
                    if network_id:
                        tab.direct_navigation_network_ids.add(network_id)
                    direct_request = True
                    break
            allowed = frame_id != root_frame_id or direct_request
            if not allowed:
                try:
                    allowed = bool(session.navigation_guard(url))
                except Exception:
                    allowed = False
            try:
                if allowed:
                    await cdp.send(
                        "Fetch.continueRequest", {"requestId": request_id}
                    )
                    return
                self._emit(
                    {
                        "type": "browser_navigation_blocked",
                        "version": 1,
                        "session_id": session.session_id,
                        "tab_id": tab.tab_id,
                        "url": url,
                        "reason": "site_permission_required",
                    }
                )
                await cdp.send(
                    "Fetch.failRequest",
                    {
                        "requestId": request_id,
                        "errorReason": "BlockedByClient",
                    },
                )
            except Exception:
                # Closing a page/CDP session can race an already-paused request.
                return

        async def on_auth_required(params: dict[str, Any]) -> None:
            """Answer only the private loopback proxy's auth challenge.

            Enabling CDP Fetch interception for redirect-hop enforcement takes
            ownership of authentication handling away from Playwright. Without
            this callback Chromium rejects the otherwise valid per-context proxy
            credentials with ``ERR_INVALID_AUTH_CREDENTIALS``.
            """

            request_id = str(params.get("requestId") or "")
            challenge = params.get("authChallenge") or {}
            credentials = session.proxy_credentials
            response: dict[str, Any] = {"response": "Default"}
            if challenge.get("source") == "Proxy" and credentials is not None:
                response = {
                    "response": "ProvideCredentials",
                    "username": credentials[0],
                    "password": credentials[1],
                }
            try:
                await cdp.send(
                    "Fetch.continueWithAuth",
                    {
                        "requestId": request_id,
                        "authChallengeResponse": response,
                    },
                )
            except Exception:
                # Closing a page/CDP session can race an auth challenge.
                return

        cdp.on("Fetch.requestPaused", on_paused)
        cdp.on("Fetch.authRequired", on_auth_required)
        await cdp.send(
            "Fetch.enable",
            {
                "handleAuthRequests": session.proxy_credentials is not None,
                "patterns": [
                    {
                        "urlPattern": "*",
                        "resourceType": "Document",
                        "requestStage": "Request",
                    }
                ]
            },
        )
        tab.guard_cdp = cdp

    async def _sync_tabs(self, session: _Session) -> None:
        for page in session.context.pages:
            await self._register_page(session, page, activate=False)
        closed = [
            tab_id for tab_id, tab in session.tabs.items() if tab.page.is_closed()
        ]
        for tab_id in closed:
            session.tabs.pop(tab_id, None)
        if session.active_tab_id not in session.tabs:
            session.active_tab_id = next(iter(session.tabs), None)

    def close_session(self, session_id: str) -> dict[str, Any]:
        return self._call(lambda: self._close_session(session_id), timeout=20)

    async def _close_session(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return {"ok": True, "session_id": session_id, "closed": True}
        async with session.operation_lock:
            for tab in session.tabs.values():
                await self._release_tab_direct_input(tab)
                await self._stop_tab_screencast(tab)
                await self._stop_tab_navigation_guard(tab)
            await session.context.close()
            if (
                session.profile_id
                and self._profile_leases.get(session.profile_id) == session_id
            ):
                self._profile_leases.pop(session.profile_id, None)
        self._emit(
            {
                "type": "browser_state",
                "version": 1,
                "session_id": session_id,
                "status": "closed",
            }
        )
        return {"ok": True, "session_id": session_id, "closed": True}

    def storage_state(self, session_id: str) -> dict[str, Any]:
        return self._call(lambda: self._storage_state(session_id))

    async def _storage_state(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            return await session.context.storage_state(indexed_db=True)

    def state(self, session_id: str) -> dict[str, Any]:
        return self._call(lambda: self._state(session_id))

    async def _state(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            await self._sync_tabs(session)
            return {
                "ok": True,
                "session_id": session_id,
                "status": "open",
                "visible": session.visible,
                "active_tab_id": session.active_tab_id,
                "tabs": await self._tab_payloads(session),
                "dialog": self._active_dialog_payload(session),
                "capabilities": {
                    "shared_input": True,
                    "coordinate_input": True,
                    "downloads": True,
                    "uploads": True,
                    "clipboard": True,
                    "console": True,
                    "read_only_dom": True,
                    "cdp": session.developer_mode,
                },
            }

    def _require_session(self, session_id: str) -> _Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise browser_error(
                "SESSION_NOT_FOUND", "Browser session does not exist"
            )
        return session

    async def _emit_state(self, session: _Session, **changes: Any) -> None:
        self._emit(
            {
                "type": "browser_state",
                "version": 1,
                "session_id": session.session_id,
                "active_tab_id": session.active_tab_id,
                "visible": session.visible,
                "tabs": await self._tab_payloads(session),
                "dialog": self._active_dialog_payload(session),
                **changes,
            }
        )

    @staticmethod
    def _active_dialog_payload(session: _Session) -> dict[str, Any] | None:
        tab = session.tabs.get(session.active_tab_id or "")
        if tab is None or tab.dialog is None:
            return None
        return {
            "tab_id": tab.tab_id,
            "dialog_type": tab.dialog.type,
            "message": tab.dialog.message,
            "default_value": tab.dialog.default_value,
        }

    # -- tabs, navigation, snapshots --------------------------------------------

    def navigate(
        self,
        session_id: str,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        new_tab: bool = False,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._navigate(
                session_id, url, wait_until, new_tab, trusted_user=False
            )
        )

    def user_navigate(
        self,
        session_id: str,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        new_tab: bool = False,
    ) -> dict[str, Any]:
        """Navigate from the always-interactive trusted in-app address bar."""

        return self._call(
            lambda: self._navigate(
                session_id, url, wait_until, new_tab, trusted_user=True
            )
        )

    async def _navigate(
        self,
        session_id: str,
        url: str,
        wait_until: str,
        new_tab: bool,
        *,
        trusted_user: bool,
    ) -> dict[str, Any]:
        self._validate_url(url)
        if wait_until not in _WAIT_UNTIL:
            raise browser_error("INVALID_ARGUMENT", "Invalid wait_until value")
        session = self._require_session(session_id)

        async def perform() -> dict[str, Any]:
            if new_tab or not session.active_tab_id:
                page = await session.context.new_page()
                tab = await self._register_page(session, page, activate=True)
            else:
                tab = self._require_tab(session, session.active_tab_id)
            if trusted_user:
                self._invalidate_for_direct_input(tab)
                # Address-bar navigation supersedes an in-flight/failed page
                # navigation. Stop Chromium's pending error-page fallback first
                # so it cannot interrupt the user's destination.
                try:
                    await tab.page.evaluate("window.stop()")
                except Exception:
                    pass

            async def goto_once() -> Any:
                if trusted_user:
                    return await self._run_direct_navigation(
                        tab,
                        lambda: tab.page.goto(
                            url, wait_until=wait_until, timeout=30_000
                        ),
                        expected_url=url,
                    )
                tab.agent_navigation_depth += 1
                try:
                    return await tab.page.goto(
                        url, wait_until=wait_until, timeout=30_000
                    )
                finally:
                    tab.agent_navigation_depth = max(
                        0, tab.agent_navigation_depth - 1
                    )

            try:
                try:
                    response = await goto_once()
                except Exception as exc:
                    # Chromium may finish committing its internal error page
                    # just after a guarded agent redirect is cancelled. A
                    # single bounded retry lets a user's address-bar action
                    # supersede that internal navigation.
                    if not (
                        trusted_user
                        and "chrome-error://chromewebdata/" in str(exc)
                    ):
                        raise
                    await asyncio.sleep(0.05)
                    response = await goto_once()
            except Exception as exc:
                self._raise_playwright(exc, "NAVIGATION_FAILED")
            tab.latest = None
            session.active_tab_id = tab.tab_id
            await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            snapshot = await self._capture_snapshot(session, tab)
            await self._emit_state(session, status="open")
            snapshot["status_code"] = response.status if response else None
            return snapshot

        # User browser chrome bypasses the agent-operation lock. Calls that
        # overlap are ordered by Chromium; stale agent targeting fails closed.
        if trusted_user:
            return await perform()
        async with session.operation_lock:
            return await perform()

    def history(self, session_id: str, direction: str) -> dict[str, Any]:
        return self._call(
            lambda: self._history(session_id, direction, trusted_user=False)
        )

    def user_history(self, session_id: str, direction: str) -> dict[str, Any]:
        """Use always-interactive back/forward/reload browser chrome."""

        return self._call(
            lambda: self._history(session_id, direction, trusted_user=True)
        )

    async def _history(
        self, session_id: str, direction: str, *, trusted_user: bool
    ) -> dict[str, Any]:
        if direction not in {"back", "forward", "reload"}:
            raise browser_error(
                "INVALID_ARGUMENT", "direction must be back, forward, or reload"
            )
        session = self._require_session(session_id)

        async def perform() -> dict[str, Any]:
            tab = self._active_tab(session)
            if trusted_user:
                self._invalidate_for_direct_input(tab)
            expected_url = (
                await self._history_destination(session, tab, direction)
                if trusted_user
                else None
            )

            async def change_history() -> Any:
                if direction == "back":
                    operation = lambda: tab.page.go_back(
                        wait_until="domcontentloaded"
                    )
                elif direction == "forward":
                    operation = lambda: tab.page.go_forward(
                        wait_until="domcontentloaded"
                    )
                else:
                    operation = lambda: tab.page.reload(
                        wait_until="domcontentloaded"
                    )
                if trusted_user:
                    return await self._run_direct_navigation(
                        tab, operation, expected_url=expected_url
                    )
                tab.agent_navigation_depth += 1
                try:
                    return await operation()
                finally:
                    tab.agent_navigation_depth = max(
                        0, tab.agent_navigation_depth - 1
                    )

            try:
                await change_history()
            except Exception as exc:
                self._raise_playwright(exc, "NAVIGATION_FAILED")
            tab.latest = None
            await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            return await self._capture_snapshot(session, tab)

        if trusted_user:
            return await perform()
        async with session.operation_lock:
            return await perform()

    def snapshot(
        self,
        session_id: str,
        *,
        tab_id: str | None = None,
        max_chars: int = 32_768,
    ) -> dict[str, Any]:
        return self._call(lambda: self._snapshot(session_id, tab_id, max_chars))

    async def _snapshot(
        self, session_id: str, tab_id: str | None, max_chars: int
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = (
                self._require_tab(session, tab_id)
                if tab_id
                else self._active_tab(session)
            )
            return await self._capture_snapshot(session, tab, max_chars=max_chars)

    async def _capture_snapshot(
        self, session: _Session, tab: _Tab, *, max_chars: int = 32_768
    ) -> dict[str, Any]:
        if tab.dialog is not None:
            raise browser_error(
                "DIALOG_OPEN",
                "A browser dialog must be accepted or dismissed first",
                tab_id=tab.tab_id,
            )
        generation = self._tab_generation(tab)
        try:
            raw = await tab.page.aria_snapshot(
                mode="ai", boxes=True, timeout=self.action_timeout_ms
            )
            title = await tab.page.title()
        except Exception as exc:
            self._raise_playwright(exc, "SNAPSHOT_FAILED")
        self._require_tab_generation(tab, generation)
        content = _sanitize_snapshot(raw)
        refs = set(_REF_RE.findall(content))
        session.counter += 1
        snapshot_id = f"snap_{session.counter}_{secrets.token_urlsafe(5)}"
        chunks = _chunk_snapshot(content, max_chars=max_chars)
        record = _Snapshot(
            snapshot_id,
            content,
            refs,
            tab.page.url,
            title,
            chunks,
            *generation,
        )
        tab.title = title
        tab.latest = record
        return self._snapshot_chunk(session, tab, record, 0)

    def snapshot_more(self, session_id: str, cursor: str) -> dict[str, Any]:
        return self._call(lambda: self._snapshot_more(session_id, cursor))

    async def _snapshot_more(
        self, session_id: str, cursor: str
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            for tab in session.tabs.values():
                record = tab.latest
                if record and cursor in record.cursors:
                    return self._snapshot_chunk(
                        session, tab, record, record.cursors[cursor]
                    )
            raise browser_error(
                "STALE_SNAPSHOT", "Snapshot continuation is no longer valid"
            )

    def _snapshot_chunk(
        self, session: _Session, tab: _Tab, record: _Snapshot, index: int
    ) -> dict[str, Any]:
        next_cursor = None
        if index + 1 < len(record.chunks):
            next_cursor = "cur_" + secrets.token_urlsafe(10)
            record.cursors[next_cursor] = index + 1
        viewport = tab.page.viewport_size or {
            "width": self.viewport[0],
            "height": self.viewport[1],
        }
        return {
            "ok": True,
            "session_id": session.session_id,
            "tab_id": tab.tab_id,
            "snapshot_id": record.snapshot_id,
            "url": record.url,
            "title": record.title,
            "snapshot": record.chunks[index] if record.chunks else "",
            "truncated": next_cursor is not None,
            "continuation": next_cursor,
            "viewport": {
                **viewport,
                "dpr": session.device_scale_factor,
            },
        }

    def tabs(self, session_id: str) -> dict[str, Any]:
        return self._call(lambda: self._tabs(session_id))

    async def _tabs(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            await self._sync_tabs(session)
            return {
                "ok": True,
                "active_tab_id": session.active_tab_id,
                "tabs": await self._tab_payloads(session),
            }

    async def _tab_payloads(self, session: _Session) -> list[dict[str, Any]]:
        result = []
        for tab in session.tabs.values():
            if tab.page.is_closed():
                continue
            if tab.dialog is None:
                try:
                    tab.title = await tab.page.title()
                except Exception:
                    pass
                (
                    tab.can_go_back,
                    tab.can_go_forward,
                ) = await self._navigation_capabilities(session, tab)
            result.append(
                {
                    "tab_id": tab.tab_id,
                    "url": tab.page.url,
                    "title": tab.title,
                    "active": tab.tab_id == session.active_tab_id,
                    "can_go_back": tab.can_go_back,
                    "can_go_forward": tab.can_go_forward,
                }
            )
        return result

    async def _navigation_capabilities(
        self, session: _Session, tab: _Tab
    ) -> tuple[bool, bool]:
        """Read Chromium's navigation index without mutating browser history."""

        cdp = tab.cdp
        temporary = cdp is None
        try:
            if cdp is None:
                cdp = await session.context.new_cdp_session(tab.page)
            history = await cdp.send("Page.getNavigationHistory")
            index = int(history.get("currentIndex", 0))
            entries = history.get("entries") or []
            return index > 0, index + 1 < len(entries)
        except Exception:
            return False, False
        finally:
            if temporary and cdp is not None:
                try:
                    await cdp.detach()
                except Exception:
                    pass

    async def _history_destination(
        self, session: _Session, tab: _Tab, direction: str
    ) -> str | None:
        """Resolve the exact history URL a direct chrome command will request."""

        if direction == "reload":
            return str(tab.page.url or "") or None
        cdp = tab.cdp
        temporary = cdp is None
        try:
            if cdp is None:
                cdp = await session.context.new_cdp_session(tab.page)
            history = await cdp.send("Page.getNavigationHistory")
            index = int(history.get("currentIndex", 0))
            target_index = index - 1 if direction == "back" else index + 1
            entries = history.get("entries") or []
            if not 0 <= target_index < len(entries):
                return None
            value = str((entries[target_index] or {}).get("url") or "")
            return value or None
        except Exception:
            # The action remains event-scoped if history inspection races a
            # commit; it merely loses the additional exact-URL constraint.
            return None
        finally:
            if temporary and cdp is not None:
                try:
                    await cdp.detach()
                except Exception:
                    pass

    async def _run_direct_navigation(
        self,
        tab: _Tab,
        operation: Callable[[], Any],
        *,
        expected_url: str | None,
    ) -> Any:
        """Run one user navigation with request-chain-scoped provenance."""

        scope_id = "nav_" + secrets.token_urlsafe(8)
        expected_key = (
            _navigation_url_key(expected_url) if expected_url else None
        )
        tab.direct_navigation_scopes[scope_id] = _DirectNavigationScope(
            expected_key
        )
        try:
            return await operation()
        finally:
            tab.direct_navigation_scopes.pop(scope_id, None)

    def select_tab(self, session_id: str, tab_id: str) -> dict[str, Any]:
        return self._call(lambda: self._select_tab(session_id, tab_id))

    async def _select_tab(
        self, session_id: str, tab_id: str
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = self._require_tab(session, tab_id)
            session.active_tab_id = tab_id
            await tab.page.bring_to_front()
            if session.streaming:
                await self._handoff_screencast(session, tab)
            await self._emit_state(session, status="open")
            await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            return await self._capture_snapshot(session, tab)

    def close_tab(self, session_id: str, tab_id: str) -> dict[str, Any]:
        return self._call(lambda: self._close_tab(session_id, tab_id))

    async def _close_tab(
        self, session_id: str, tab_id: str
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = self._require_tab(session, tab_id)
            await self._stop_tab_screencast(tab)
            await self._stop_tab_navigation_guard(tab)
            await tab.page.close()
            session.tabs.pop(tab_id, None)
            if not session.tabs:
                page = await session.context.new_page()
                await self._register_page(session, page, activate=True)
            elif session.active_tab_id == tab_id:
                session.active_tab_id = next(iter(session.tabs))
            active = self._active_tab(session)
            if session.streaming:
                await self._handoff_screencast(session, active)
            await self._capture_frame(
                session, active, quality=_DISPLAY_JPEG_QUALITY
            )
            await self._emit_state(session, status="open")
            return {
                "ok": True,
                "active_tab_id": session.active_tab_id,
                "tabs": await self._tab_payloads(session),
            }

    def dialog(
        self,
        session_id: str,
        action: str,
        *,
        prompt_text: str | None = None,
    ) -> dict[str, Any]:
        """Resolve the exact pending JavaScript dialog on the active tab."""

        return self._call(
            lambda: self._dialog(session_id, action, prompt_text)
        )

    async def _dialog(
        self, session_id: str, action: str, prompt_text: str | None
    ) -> dict[str, Any]:
        if action not in {"accept", "dismiss"}:
            raise browser_error(
                "INVALID_ARGUMENT",
                "Dialog action must be accept or dismiss",
            )
        if prompt_text is not None and not isinstance(prompt_text, str):
            raise browser_error(
                "INVALID_ARGUMENT", "prompt_text must be a string"
            )
        if prompt_text is not None and len(prompt_text) > 200_000:
            raise browser_error(
                "INVALID_ARGUMENT", "prompt_text is too large"
            )
        if action == "dismiss" and prompt_text is not None:
            raise browser_error(
                "INVALID_ARGUMENT",
                "prompt_text is valid only when accepting a dialog",
            )
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = self._active_tab(session)
            pending = tab.dialog
            if pending is None:
                raise browser_error(
                    "DIALOG_NOT_FOUND",
                    "The active browser tab has no pending dialog",
                    tab_id=tab.tab_id,
                )
            dialog_type = pending.type
            try:
                if action == "accept":
                    if prompt_text is None:
                        await pending.accept()
                    else:
                        await pending.accept(prompt_text)
                else:
                    await pending.dismiss()
            except Exception as exc:
                self._raise_playwright(exc, "DIALOG_RESOLUTION_FAILED")
            tab.dialog = None
            tab.dialog_event.clear()
            tab.latest = None
            await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            await self._emit_state(session, status="open")
            result = await self._capture_snapshot(session, tab)
            result["dialog"] = {
                "action": action,
                "type": dialog_type,
            }
            return result

    # -- ref-scoped agent actions ------------------------------------------------

    def classify_action(
        self,
        session_id: str,
        action: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Classify one current ref-scoped action before the permission prompt.

        Target semantics come from the trusted live DOM and the exact fresh
        snapshot/ref pair, never from model-authored descriptions.  A failed
        inspection is deliberately consequential so it cannot inherit a routine
        Browser Use session grant.
        """

        return self._call(
            lambda: self._classify_action(
                session_id, str(action), dict(arguments or {})
            )
        )

    async def _classify_action(
        self,
        session_id: str,
        action: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        tab_id = str(arguments.get("tab_id") or "")
        snapshot_id = str(arguments.get("snapshot_id") or "")
        ref = str(arguments.get("ref") or "")
        async with session.operation_lock:
            if action in {
                "browser_coordinate_click",
                "browser_coordinate_drag",
                "browser_type_text",
                "browser_keypress",
            }:
                try:
                    tab = self._require_tab(session, tab_id)
                    if action in {
                        "browser_coordinate_click",
                        "browser_coordinate_drag",
                    }:
                        coordinate_arguments = arguments
                        if action == "browser_coordinate_drag":
                            path = arguments.get("path")
                            if not isinstance(path, list) or not path:
                                raise ValueError("drag path unavailable")
                            coordinate_arguments = path[-1]
                        point = self._validate_page_point(
                            tab,
                            coordinate_arguments.get("x"),
                            coordinate_arguments.get("y"),
                        )
                        raw_target = await tab.page.evaluate(
                            """
                            ({x, y}) => {
                              const element = document.elementFromPoint(x, y);
                              if (!element) return null;
                              const tag = String(element.tagName || "").toLowerCase();
                              const type = String(element.getAttribute("type") || tag).toLowerCase();
                              const form = element.closest ? element.closest("form") : null;
                              const anchor = element.closest ? element.closest("a[href]") : null;
                              const labels = element.labels
                                ? Array.from(element.labels).map(label => label.innerText || label.textContent || "")
                                : [];
                              const text = [
                                element.getAttribute("aria-label"),
                                ...labels,
                                element.getAttribute("alt"),
                                element.getAttribute("title"),
                                element.getAttribute("placeholder"),
                                element.innerText,
                              ].filter(Boolean).join(" ").replace(/\\s+/g, " ").trim().slice(0, 512);
                              const submits = !!form && (
                                (tag === "button" && (!element.hasAttribute("type") || type === "submit")) ||
                                (tag === "input" && (type === "submit" || type === "image"))
                              );
                              let destination = "";
                              try {
                                if (anchor && anchor.href) destination = new URL(anchor.href, document.baseURI).href;
                                else if (submits && form) destination = new URL(
                                  element.getAttribute("formaction") || form.getAttribute("action") || location.href,
                                  document.baseURI
                                ).href;
                              } catch (_) {}
                              return {
                                role: String(element.getAttribute("role") || tag),
                                accessible_name: text,
                                element_type: type,
                                inside_form: !!form,
                                submits_form: submits,
                                destination_url: destination,
                                autocomplete: String(element.getAttribute("autocomplete") || ""),
                                name: String(element.getAttribute("name") || ""),
                                id: String(element.id || ""),
                              };
                            }
                            """,
                            point,
                        )
                        policy_action = "browser_click"
                        policy_arguments = {
                            "button": (
                                arguments.get("button", "left")
                                if action == "browser_coordinate_click"
                                else "left"
                            ),
                            "click_count": (
                                arguments.get("click_count", 1)
                                if action == "browser_coordinate_click"
                                else 1
                            ),
                        }
                    else:
                        raw_target = await tab.page.evaluate(
                            """
                            () => {
                              const element = document.activeElement;
                              if (!element) return null;
                              const tag = String(element.tagName || "").toLowerCase();
                              const type = String(element.getAttribute("type") || tag).toLowerCase();
                              const form = element.closest ? element.closest("form") : null;
                              const labels = element.labels
                                ? Array.from(element.labels).map(label => label.innerText || label.textContent || "")
                                : [];
                              const text = [
                                element.getAttribute("aria-label"),
                                ...labels,
                                element.getAttribute("title"),
                                element.getAttribute("placeholder"),
                              ].filter(Boolean).join(" ").replace(/\\s+/g, " ").trim().slice(0, 512);
                              return {
                                role: String(element.getAttribute("role") || tag),
                                accessible_name: text,
                                element_type: type,
                                inside_form: !!form,
                                submits_form: false,
                                destination_url: "",
                                autocomplete: String(element.getAttribute("autocomplete") || ""),
                                name: String(element.getAttribute("name") || ""),
                                id: String(element.id || ""),
                              };
                            }
                            """
                        )
                        policy_action = (
                            "browser_type"
                            if action == "browser_type_text"
                            else "browser_press"
                        )
                        policy_arguments = (
                            {"key": (arguments.get("keys") or [""])[-1]}
                            if action == "browser_keypress"
                            else {}
                        )
                    if not isinstance(raw_target, dict):
                        raise ValueError("target unavailable")
                    descriptor = " ".join(
                        str(raw_target.get(key) or "")
                        for key in (
                            "accessible_name",
                            "autocomplete",
                            "name",
                            "id",
                        )
                    )
                    classifications = _browser_target_classifications(
                        (
                            "browser_fill"
                            if policy_action == "browser_type"
                            else policy_action
                        ),
                        descriptor,
                        element_type=str(raw_target.get("element_type") or ""),
                        autocomplete=str(raw_target.get("autocomplete") or ""),
                    )
                    request = BrowserActionRequest.build(
                        session_id=session_id,
                        tab_id=tab.tab_id,
                        snapshot_id=(
                            tab.latest.snapshot_id if tab.latest else "coordinate"
                        ),
                        ref="coordinate",
                        origin=tab.page.url,
                        action=policy_action,
                        arguments=policy_arguments,
                        target=raw_target,
                        data_classification=classifications,
                    )
                    decision = self._action_policy.classify(request)
                    return {
                        "requires_confirmation": decision.requires_confirmation,
                        "reasons": list(decision.reasons),
                        **(
                            {"destination_url": str(raw_target["destination_url"])}
                            if raw_target.get("destination_url")
                            else {}
                        ),
                    }
                except Exception:
                    return {
                        "requires_confirmation": True,
                        "reasons": ["unverified_browser_target"],
                    }
            try:
                tab, locator, generation = await self._require_target(
                    session, tab_id, snapshot_id, ref
                )
                raw_target = await locator.evaluate(
                    """
                    element => {
                      const tag = String(element.tagName || "").toLowerCase();
                      const type = String(element.getAttribute("type") || tag).toLowerCase();
                      const labels = element.labels
                        ? Array.from(element.labels).map(label => label.innerText || label.textContent || "")
                        : [];
                      const text = [
                        element.getAttribute("aria-label"),
                        ...labels,
                        element.getAttribute("alt"),
                        element.getAttribute("title"),
                        element.getAttribute("placeholder"),
                        element.innerText,
                      ].filter(Boolean).join(" ").replace(/\\s+/g, " ").trim().slice(0, 512);
                      const form = element.closest ? element.closest("form") : null;
                      const anchor = element.closest ? element.closest("a[href]") : null;
                      const submits = !!form && (
                        (tag === "button" && (!element.hasAttribute("type") || type === "submit")) ||
                        (tag === "input" && (type === "submit" || type === "image"))
                      );
                      let destination = "";
                      try {
                        if (anchor && anchor.href) {
                          destination = new URL(anchor.href, document.baseURI).href;
                        } else if (submits && form) {
                          const submitAction = element.getAttribute("formaction");
                          const formAction = submitAction || form.getAttribute("action") || location.href;
                          destination = new URL(formAction, document.baseURI).href;
                        }
                      } catch (_) {}
                      return {
                        role: String(element.getAttribute("role") || tag),
                        accessible_name: text,
                        element_type: type,
                        inside_form: !!form,
                        submits_form: submits,
                        destination_url: destination,
                        autocomplete: String(element.getAttribute("autocomplete") || ""),
                        name: String(element.getAttribute("name") || ""),
                        id: String(element.id || ""),
                      };
                    }
                    """
                )
                self._require_tab_generation(tab, generation)
            except Exception:
                return {
                    "requires_confirmation": True,
                    "reasons": ["unverified_browser_target"],
                }

            record = tab.latest
            snapshot_hint = ""
            if record is not None:
                marker = f"[ref={ref}]"
                snapshot_hint = next(
                    (
                        line.strip()[:512]
                        for line in record.content.splitlines()
                        if marker in line
                    ),
                    "",
                )
            descriptor = " ".join(
                str(raw_target.get(key) or "")
                for key in (
                    "accessible_name",
                    "autocomplete",
                    "name",
                    "id",
                )
            )
            classifications = _browser_target_classifications(
                action,
                descriptor,
                element_type=str(raw_target.get("element_type") or ""),
                autocomplete=str(raw_target.get("autocomplete") or ""),
            )
            try:
                request = BrowserActionRequest.build(
                    session_id=session_id,
                    tab_id=tab_id,
                    snapshot_id=snapshot_id,
                    ref=ref,
                    origin=tab.page.url,
                    action=action,
                    arguments={
                        key: value
                        for key, value in arguments.items()
                        if key not in {"tab_id", "snapshot_id", "ref"}
                    },
                    target={
                        "role": raw_target.get("role"),
                        "accessible_name": raw_target.get("accessible_name"),
                        "element_type": raw_target.get("element_type"),
                        "inside_form": raw_target.get("inside_form"),
                        "submits_form": raw_target.get("submits_form"),
                        "page_risk_hints": (
                            [snapshot_hint] if snapshot_hint else []
                        ),
                    },
                    data_classification=classifications,
                )
                decision = self._action_policy.classify(request)
            except Exception:
                return {
                    "requires_confirmation": True,
                    "reasons": ["unverified_browser_target"],
                }
            return {
                "requires_confirmation": decision.requires_confirmation,
                "reasons": list(decision.reasons),
                **(
                    {"destination_url": str(raw_target["destination_url"])}
                    if raw_target.get("destination_url")
                    else {}
                ),
            }

    def click(
        self, session_id: str, tab_id: str, snapshot_id: str, ref: str
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._target_action(
                session_id, tab_id, snapshot_id, ref, "click", None
            )
        )

    def fill(
        self,
        session_id: str,
        tab_id: str,
        snapshot_id: str,
        ref: str,
        value: str,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._target_action(
                session_id, tab_id, snapshot_id, ref, "fill", value
            )
        )

    def press(
        self,
        session_id: str,
        tab_id: str,
        snapshot_id: str,
        ref: str,
        key: str,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._target_action(
                session_id, tab_id, snapshot_id, ref, "press", key
            )
        )

    def select(
        self,
        session_id: str,
        tab_id: str,
        snapshot_id: str,
        ref: str,
        value: str,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._target_action(
                session_id, tab_id, snapshot_id, ref, "select", value
            )
        )

    def hover(
        self, session_id: str, tab_id: str, snapshot_id: str, ref: str
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._target_action(
                session_id, tab_id, snapshot_id, ref, "hover", None
            )
        )

    async def _target_action(
        self,
        session_id: str,
        tab_id: str,
        snapshot_id: str,
        ref: str,
        kind: str,
        value: str | None,
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab, locator, generation = await self._require_target(
                session, tab_id, snapshot_id, ref
            )
            action_id = "act_" + secrets.token_urlsafe(8)
            try:
                dialog_opened = False
                if kind == "click":
                    if (await locator.get_attribute("type") or "").lower() == "file":
                        raise browser_error(
                            "UNSUPPORTED_ACTION",
                            "File upload is not supported by Browser Use",
                        )
                    await locator.click(trial=True, timeout=self.action_timeout_ms)
                else:
                    await locator.wait_for(
                        state="visible", timeout=self.action_timeout_ms
                    )
                    await locator.scroll_into_view_if_needed(
                        timeout=self.action_timeout_ms
                    )
                self._require_tab_generation(tab, generation)
                box = await locator.bounding_box()
                if box is None:
                    raise browser_error(
                        "REF_NOT_FOUND", "Target is not visible", ref=ref
                    )
                self._require_tab_generation(tab, generation)
                frame = await self._capture_frame(
                    session, tab, quality=_DISPLAY_JPEG_QUALITY
                )
                ack_event = None
                if self._has_subscriber(
                    "browser_action_visual", session.session_id
                ):
                    ack_event = asyncio.Event()
                    self._cursor_acks[action_id] = (
                        session.session_id,
                        frame["frame_id"],
                        ack_event,
                    )
                self._emit_visual(
                    session,
                    tab,
                    action_id,
                    snapshot_id,
                    ref,
                    kind,
                    "move",
                    box,
                    frame["frame_id"],
                )
                await self._pace_visual_move(
                    session,
                    action_id,
                    frame["frame_id"],
                    ack_event,
                )
                async with tab.input_lock:
                    self._require_tab_generation(tab, generation)
                    tab.agent_action_depth += 1
                    try:
                        if kind == "click":
                            self._emit_visual(
                                session,
                                tab,
                                action_id,
                                snapshot_id,
                                ref,
                                kind,
                                "down",
                                box,
                                frame["frame_id"],
                            )
                            if ack_event is not None:
                                await asyncio.sleep(0.03)
                            self._require_tab_generation(tab, generation)
                            if await locator.count() != 1:
                                raise browser_error(
                                    "REF_NOT_FOUND",
                                    "Target disappeared before click",
                                    ref=ref,
                                )
                            self._require_tab_generation(tab, generation)
                            dialog_opened = await self._await_action_or_dialog(
                                tab,
                                locator.click(timeout=self.action_timeout_ms),
                            )
                            self._emit_visual(
                                session,
                                tab,
                                action_id,
                                snapshot_id,
                                ref,
                                kind,
                                "up",
                                box,
                                frame["frame_id"],
                            )
                            if ack_event is not None:
                                await asyncio.sleep(0.04)
                        elif kind == "fill":
                            if value is None or len(value) > 200_000:
                                raise browser_error(
                                    "INVALID_ARGUMENT", "Invalid fill value"
                                )
                            dialog_opened = await self._await_action_or_dialog(
                                tab,
                                locator.fill(
                                    value, timeout=self.action_timeout_ms
                                ),
                            )
                        elif kind == "press":
                            if not value or len(value) > 100:
                                raise browser_error(
                                    "INVALID_ARGUMENT", "Invalid key"
                                )
                            dialog_opened = await self._await_action_or_dialog(
                                tab,
                                locator.press(
                                    value, timeout=self.action_timeout_ms
                                ),
                            )
                        elif kind == "select":
                            if value is None or len(value) > 10_000:
                                raise browser_error(
                                    "INVALID_ARGUMENT", "Invalid option value"
                                )
                            dialog_opened = await self._await_action_or_dialog(
                                tab,
                                locator.select_option(
                                    value=value,
                                    timeout=self.action_timeout_ms,
                                ),
                            )
                        elif kind == "hover":
                            dialog_opened = await self._await_action_or_dialog(
                                tab,
                                locator.hover(timeout=self.action_timeout_ms),
                            )
                        else:
                            raise browser_error(
                                "INVALID_ARGUMENT", "Unknown action"
                            )
                    finally:
                        tab.agent_action_depth = max(
                            0, tab.agent_action_depth - 1
                        )
                if dialog_opened or tab.dialog is not None:
                    # The triggering action succeeded, but the browser is now
                    # modal.  Do not attempt a screenshot/snapshot until the
                    # exact pending dialog has been resolved.
                    self._emit_visual(
                        session,
                        tab,
                        action_id,
                        snapshot_id,
                        ref,
                        kind,
                        "completed",
                        box,
                        frame["frame_id"],
                    )
                    tab.latest = None
                    raise browser_error(
                        "DIALOG_OPEN",
                        "Browser action opened a dialog that must be resolved",
                        tab_id=tab.tab_id,
                        dialog_type=tab.dialog.type,
                        message=tab.dialog.message,
                    )
                post_frame = await self._capture_frame(
                    session, tab, quality=_DISPLAY_JPEG_QUALITY
                )
                self._emit_visual(
                    session,
                    tab,
                    action_id,
                    snapshot_id,
                    ref,
                    kind,
                    "completed",
                    box,
                    post_frame["frame_id"],
                )
            except BrowserRuntimeError as exc:
                if exc.code != "DIALOG_OPEN":
                    self._emit_visual(
                        session,
                        tab,
                        action_id,
                        snapshot_id,
                        ref,
                        kind,
                        "failed",
                        None,
                        tab.latest_frame_id,
                    )
                raise
            except Exception as exc:
                self._emit_visual(
                    session,
                    tab,
                    action_id,
                    snapshot_id,
                    ref,
                    kind,
                    "failed",
                    None,
                    tab.latest_frame_id,
                )
                self._raise_playwright(exc, "ACTION_FAILED")
            tab.latest = None
            return await self._capture_snapshot(session, tab)

    async def _await_action_or_dialog(
        self, tab: _Tab, operation: Any
    ) -> bool:
        """Let a modal dialog interrupt an otherwise blocked Playwright action."""

        tab.dialog_event.clear()
        action_task = asyncio.create_task(operation)
        dialog_task = asyncio.create_task(tab.dialog_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {action_task, dialog_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if dialog_task in done and tab.dialog is not None:
                action_task.cancel()
                await asyncio.gather(action_task, return_exceptions=True)
                return True
            dialog_task.cancel()
            await asyncio.gather(dialog_task, return_exceptions=True)
            await action_task
            return tab.dialog is not None
        finally:
            if not dialog_task.done():
                dialog_task.cancel()

    async def _require_target(
        self,
        session: _Session,
        tab_id: str,
        snapshot_id: str,
        ref: str,
    ) -> tuple[_Tab, Any, tuple[int, int, int]]:
        if not _VALID_REF_RE.fullmatch(ref):
            raise browser_error("INVALID_ARGUMENT", "Invalid element ref")
        tab = self._require_tab(session, tab_id)
        record = tab.latest
        if record is None or record.snapshot_id != snapshot_id:
            latest = await self._capture_snapshot(session, tab)
            raise browser_error(
                "STALE_SNAPSHOT",
                "Element ref belongs to an older page snapshot",
                latest_snapshot=latest,
            )
        generation = self._snapshot_generation(record)
        self._require_tab_generation(tab, generation)
        if ref not in record.refs:
            raise browser_error(
                "REF_NOT_FOUND",
                "Element ref does not exist in the supplied snapshot",
                ref=ref,
            )
        if tab.dialog is not None:
            raise browser_error("DIALOG_OPEN", "A browser dialog is open")
        locator = tab.page.locator(f"aria-ref={ref}")
        try:
            count = await locator.count()
        except Exception as exc:
            self._raise_playwright(exc, "REF_NOT_FOUND")
        if count != 1:
            raise browser_error(
                "REF_NOT_FOUND",
                "Element ref is no longer present",
                ref=ref,
            )
        session.active_tab_id = tab_id
        await tab.page.bring_to_front()
        self._require_tab_generation(tab, generation)
        return tab, locator, generation

    def scroll(
        self,
        session_id: str,
        *,
        delta_x: float = 0,
        delta_y: float = 0,
        tab_id: str | None = None,
        snapshot_id: str | None = None,
        ref: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._scroll(
                session_id, delta_x, delta_y, tab_id, snapshot_id, ref
            )
        )

    async def _scroll(
        self,
        session_id: str,
        delta_x: float,
        delta_y: float,
        tab_id: str | None,
        snapshot_id: str | None,
        ref: str | None,
    ) -> dict[str, Any]:
        if abs(delta_x) > 100_000 or abs(delta_y) > 100_000:
            raise browser_error("INVALID_ARGUMENT", "Scroll delta is too large")
        session = self._require_session(session_id)
        async with session.operation_lock:
            if ref:
                if not (tab_id and snapshot_id):
                    raise browser_error(
                        "INVALID_ARGUMENT",
                        "tab_id and snapshot_id are required with ref",
                    )
                tab, locator, generation = await self._require_target(
                    session, tab_id, snapshot_id, ref
                )
            else:
                tab = (
                    self._require_tab(session, tab_id)
                    if tab_id
                    else self._active_tab(session)
                )
                generation = self._tab_generation(tab)
            async with tab.input_lock:
                self._require_tab_generation(tab, generation)
                tab.agent_action_depth += 1
                try:
                    if ref:
                        await locator.hover(timeout=self.action_timeout_ms)
                    else:
                        viewport = tab.page.viewport_size or {
                            "width": self.viewport[0],
                            "height": self.viewport[1],
                        }
                        await tab.page.mouse.move(
                            viewport["width"] / 2,
                            viewport["height"] / 2,
                        )
                    self._require_tab_generation(tab, generation)
                    await tab.page.mouse.wheel(
                        float(delta_x), float(delta_y)
                    )
                finally:
                    tab.agent_action_depth = max(
                        0, tab.agent_action_depth - 1
                    )
            tab.latest = None
            await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            return await self._capture_snapshot(session, tab)

    # -- advanced in-app browser capabilities ----------------------------------

    def set_visibility(
        self, session_id: str, visible: bool
    ) -> dict[str, Any]:
        return self._call(lambda: self._set_visibility(session_id, visible))

    async def _set_visibility(
        self, session_id: str, visible: bool
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            session.visible = bool(visible)
            await self._emit_state(session, status="open")
            return {"ok": True, "visible": session.visible}

    def set_viewport(
        self,
        session_id: str,
        *,
        width: int | None = None,
        height: int | None = None,
        dpr: float | None = None,
        reset: bool = False,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._set_viewport(
                session_id,
                width=width,
                height=height,
                dpr=dpr,
                reset=reset,
            )
        )

    async def _set_viewport(
        self,
        session_id: str,
        *,
        width: int | None,
        height: int | None,
        dpr: float | None,
        reset: bool,
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = self._active_tab(session)
            current = tab.page.viewport_size or {
                "width": session.default_viewport[0],
                "height": session.default_viewport[1],
            }
            if reset:
                target_width, target_height = session.default_viewport
            else:
                target_width = int(width if width is not None else current["width"])
                target_height = int(
                    height if height is not None else current["height"]
                )
            if not (320 <= target_width <= 7680 and 240 <= target_height <= 4320):
                raise browser_error(
                    "INVALID_ARGUMENT", "Viewport size is out of range"
                )
            if dpr is not None and abs(float(dpr) - session.device_scale_factor) > 0.01:
                raise browser_error(
                    "UNSUPPORTED_DPR_OVERRIDE",
                    (
                        "Device scale factor is fixed for this isolated browser "
                        f"session at {session.device_scale_factor:g}"
                    ),
                )
            await tab.page.set_viewport_size(
                {"width": target_width, "height": target_height}
            )
            tab.viewport_generation += 1
            tab.latest = None
            frame = await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            snapshot = await self._capture_snapshot(session, tab)
            await self._emit_state(session, status="open")
            return {
                **snapshot,
                "width": target_width,
                "height": target_height,
                "dpr": session.device_scale_factor,
                "frame_id": frame["frame_id"],
            }

    def finalize_tabs(
        self, session_id: str, keep_tab_ids: list[str]
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._finalize_tabs(session_id, keep_tab_ids)
        )

    async def _finalize_tabs(
        self, session_id: str, keep_tab_ids: list[str]
    ) -> dict[str, Any]:
        if not isinstance(keep_tab_ids, list) or len(keep_tab_ids) > 50:
            raise browser_error(
                "INVALID_ARGUMENT", "keep_tab_ids must be a short list"
            )
        session = self._require_session(session_id)
        async with session.operation_lock:
            await self._sync_tabs(session)
            requested = list(dict.fromkeys(str(value) for value in keep_tab_ids))
            if not requested and session.active_tab_id:
                requested = [session.active_tab_id]
            unknown = [tab_id for tab_id in requested if tab_id not in session.tabs]
            if unknown:
                raise browser_error(
                    "TAB_NOT_FOUND",
                    "One or more retained tabs do not exist",
                    tab_ids=unknown,
                )
            keep = set(requested)
            closed: list[str] = []
            for tab_id, tab in list(session.tabs.items()):
                if tab_id in keep:
                    continue
                await self._stop_tab_screencast(tab)
                await self._stop_tab_navigation_guard(tab)
                await tab.page.close()
                session.tabs.pop(tab_id, None)
                closed.append(tab_id)
            if not session.tabs:
                page = await session.context.new_page()
                await self._register_page(session, page, activate=True)
            else:
                session.active_tab_id = requested[0]
                active = self._active_tab(session)
                await active.page.bring_to_front()
                if session.streaming:
                    await self._handoff_screencast(session, active)
                await self._capture_frame(
                    session, active, quality=_DISPLAY_JPEG_QUALITY
                )
            await self._emit_state(session, status="open")
            return {
                "ok": True,
                "closed_tab_ids": closed,
                "active_tab_id": session.active_tab_id,
                "tabs": await self._tab_payloads(session),
            }

    def coordinate_move(
        self, session_id: str, tab_id: str, x: float, y: float
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._coordinate_move(session_id, tab_id, x, y)
        )

    async def _coordinate_move(
        self, session_id: str, tab_id: str, x: float, y: float
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = self._require_tab(session, tab_id)
            generation = self._tab_generation(tab)
            point = self._validate_page_point(tab, x, y)
            session.active_tab_id = tab.tab_id
            await tab.page.bring_to_front()
            async with tab.input_lock:
                self._require_tab_generation(tab, generation)
                tab.agent_action_depth += 1
                try:
                    await tab.page.mouse.move(
                        point["x"], point["y"], steps=8
                    )
                finally:
                    tab.agent_action_depth = max(
                        0, tab.agent_action_depth - 1
                    )
            action_id = "act_" + secrets.token_urlsafe(8)
            frame = await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            self._emit_coordinate_visual(
                session, tab, action_id, "move", "move", point, frame["frame_id"]
            )
            return {
                "ok": True,
                "tab_id": tab.tab_id,
                "x": point["x"],
                "y": point["y"],
                "frame_id": frame["frame_id"],
            }

    def coordinate_click(
        self,
        session_id: str,
        tab_id: str,
        x: float,
        y: float,
        *,
        button: str = "left",
        click_count: int = 1,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._coordinate_click(
                session_id,
                tab_id,
                x,
                y,
                button=button,
                click_count=click_count,
            )
        )

    async def _coordinate_click(
        self,
        session_id: str,
        tab_id: str,
        x: float,
        y: float,
        *,
        button: str,
        click_count: int,
    ) -> dict[str, Any]:
        if button not in {"left", "right", "middle"}:
            raise browser_error("INVALID_ARGUMENT", "Invalid mouse button")
        if isinstance(click_count, bool) or not 1 <= int(click_count) <= 3:
            raise browser_error("INVALID_ARGUMENT", "Invalid click_count")
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = self._require_tab(session, tab_id)
            if tab.dialog is not None:
                raise browser_error("DIALOG_OPEN", "A browser dialog is open")
            generation = self._tab_generation(tab)
            point = self._validate_page_point(tab, x, y)
            session.active_tab_id = tab.tab_id
            await tab.page.bring_to_front()
            action_id = "act_" + secrets.token_urlsafe(8)
            async with tab.input_lock:
                self._require_tab_generation(tab, generation)
                tab.agent_action_depth += 1
                try:
                    await tab.page.mouse.move(
                        point["x"], point["y"], steps=10
                    )
                finally:
                    tab.agent_action_depth = max(
                        0, tab.agent_action_depth - 1
                    )
            frame = await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            ack_event = None
            if self._has_subscriber("browser_action_visual", session.session_id):
                ack_event = asyncio.Event()
                self._cursor_acks[action_id] = (
                    session.session_id,
                    frame["frame_id"],
                    ack_event,
                )
            self._emit_coordinate_visual(
                session, tab, action_id, "click", "move", point, frame["frame_id"]
            )
            await self._pace_visual_move(
                session, action_id, frame["frame_id"], ack_event
            )
            self._emit_coordinate_visual(
                session, tab, action_id, "click", "down", point, frame["frame_id"]
            )
            async with tab.input_lock:
                self._require_tab_generation(tab, generation)
                tab.agent_action_depth += 1
                try:
                    async def click_sequence() -> None:
                        await tab.page.mouse.move(point["x"], point["y"])
                        self._require_tab_generation(tab, generation)
                        await tab.page.mouse.down(
                            button=button,
                            click_count=int(click_count),
                        )
                        await tab.page.mouse.up(
                            button=button,
                            click_count=int(click_count),
                        )

                    dialog_opened = await self._await_action_or_dialog(
                        tab, click_sequence()
                    )
                finally:
                    tab.agent_action_depth = max(
                        0, tab.agent_action_depth - 1
                    )
            self._emit_coordinate_visual(
                session, tab, action_id, "click", "up", point, frame["frame_id"]
            )
            if dialog_opened or tab.dialog is not None:
                tab.latest = None
                raise browser_error(
                    "DIALOG_OPEN",
                    "Browser action opened a dialog that must be resolved",
                    tab_id=tab.tab_id,
                    dialog_type=tab.dialog.type,
                    message=tab.dialog.message,
                )
            tab.latest = None
            post = await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            self._emit_coordinate_visual(
                session,
                tab,
                action_id,
                "click",
                "completed",
                point,
                post["frame_id"],
            )
            return await self._capture_snapshot(session, tab)

    def coordinate_drag(
        self,
        session_id: str,
        tab_id: str,
        path: list[dict[str, float]],
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._coordinate_drag(session_id, tab_id, path)
        )

    async def _coordinate_drag(
        self,
        session_id: str,
        tab_id: str,
        path: list[dict[str, float]],
    ) -> dict[str, Any]:
        if not isinstance(path, list) or not 2 <= len(path) <= 100:
            raise browser_error(
                "INVALID_ARGUMENT", "Drag path must contain 2 to 100 points"
            )
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = self._require_tab(session, tab_id)
            generation = self._tab_generation(tab)
            points = [
                self._validate_page_point(tab, item.get("x"), item.get("y"))
                if isinstance(item, dict)
                else self._invalid_drag_point()
                for item in path
            ]
            session.active_tab_id = tab.tab_id
            await tab.page.bring_to_front()
            action_id = "act_" + secrets.token_urlsafe(8)
            first = points[0]
            async with tab.input_lock:
                self._require_tab_generation(tab, generation)
                tab.agent_action_depth += 1
                try:
                    await tab.page.mouse.move(
                        first["x"], first["y"], steps=8
                    )
                finally:
                    tab.agent_action_depth = max(
                        0, tab.agent_action_depth - 1
                    )
            frame = await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            self._emit_coordinate_visual(
                session, tab, action_id, "drag", "move", first, frame["frame_id"]
            )
            async with tab.input_lock:
                self._require_tab_generation(tab, generation)
                tab.agent_action_depth += 1
                mouse_down = False
                try:
                    await tab.page.mouse.move(first["x"], first["y"])
                    self._require_tab_generation(tab, generation)
                    await tab.page.mouse.down(button="left")
                    mouse_down = True
                    self._emit_coordinate_visual(
                        session,
                        tab,
                        action_id,
                        "drag",
                        "down",
                        first,
                        frame["frame_id"],
                    )
                    for point in points[1:]:
                        await tab.page.mouse.move(
                            point["x"], point["y"], steps=4
                        )
                finally:
                    if mouse_down:
                        await tab.page.mouse.up(button="left")
                    tab.agent_action_depth = max(
                        0, tab.agent_action_depth - 1
                    )
            last = points[-1]
            tab.latest = None
            post = await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            self._emit_coordinate_visual(
                session,
                tab,
                action_id,
                "drag",
                "completed",
                last,
                post["frame_id"],
            )
            return await self._capture_snapshot(session, tab)

    def type_text(
        self, session_id: str, tab_id: str, text: str
    ) -> dict[str, Any]:
        return self._call(lambda: self._type_text(session_id, tab_id, text))

    async def _type_text(
        self, session_id: str, tab_id: str, text: str
    ) -> dict[str, Any]:
        if not isinstance(text, str) or len(text) > 200_000:
            raise browser_error("INVALID_ARGUMENT", "Invalid text")
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = self._require_tab(session, tab_id)
            generation = self._tab_generation(tab)
            session.active_tab_id = tab.tab_id
            await tab.page.bring_to_front()
            async with tab.input_lock:
                self._require_tab_generation(tab, generation)
                tab.agent_action_depth += 1
                try:
                    await tab.page.keyboard.insert_text(text)
                finally:
                    tab.agent_action_depth = max(
                        0, tab.agent_action_depth - 1
                    )
            tab.latest = None
            await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            return await self._capture_snapshot(session, tab)

    def keypress(
        self, session_id: str, tab_id: str, keys: list[str]
    ) -> dict[str, Any]:
        return self._call(lambda: self._keypress(session_id, tab_id, keys))

    async def _keypress(
        self, session_id: str, tab_id: str, keys: list[str]
    ) -> dict[str, Any]:
        if (
            not isinstance(keys, list)
            or not 1 <= len(keys) <= 100
            or any(
                not isinstance(key, str) or not key or len(key) > 100
                for key in keys
            )
        ):
            raise browser_error(
                "INVALID_ARGUMENT", "keys must contain 1 to 100 valid key names"
            )
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = self._require_tab(session, tab_id)
            generation = self._tab_generation(tab)
            session.active_tab_id = tab.tab_id
            await tab.page.bring_to_front()
            async with tab.input_lock:
                self._require_tab_generation(tab, generation)
                tab.agent_action_depth += 1
                try:
                    for key in keys:
                        await tab.page.keyboard.press(key)
                finally:
                    tab.agent_action_depth = max(
                        0, tab.agent_action_depth - 1
                    )
            tab.latest = None
            await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            return await self._capture_snapshot(session, tab)

    def clipboard(
        self,
        session_id: str,
        action: str,
        *,
        text: str | None = None,
    ) -> dict[str, Any]:
        return self._call(lambda: self._clipboard(session_id, action, text))

    async def _clipboard(
        self, session_id: str, action: str, text: str | None
    ) -> dict[str, Any]:
        if action not in {"read", "write", "paste"}:
            raise browser_error(
                "INVALID_ARGUMENT", "Clipboard action must be read, write, or paste"
            )
        if text is not None and (
            not isinstance(text, str) or len(text) > 200_000
        ):
            raise browser_error("INVALID_ARGUMENT", "Invalid clipboard text")
        session = self._require_session(session_id)
        async with session.operation_lock:
            if action == "write":
                if text is None:
                    raise browser_error(
                        "INVALID_ARGUMENT", "text is required when writing"
                    )
                session.clipboard = text
                return {
                    "ok": True,
                    "action": action,
                    "length": len(session.clipboard),
                }
            if action == "read":
                return {
                    "ok": True,
                    "action": action,
                    "text": session.clipboard,
                }
            tab = self._active_tab(session)
            await tab.page.keyboard.insert_text(session.clipboard)
            tab.latest = None
            await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            return await self._capture_snapshot(session, tab)

    def console_logs(
        self,
        session_id: str,
        tab_id: str,
        *,
        levels: list[str] | None = None,
        filter_text: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._console_logs(
                session_id,
                tab_id,
                levels=levels,
                filter_text=filter_text,
                limit=limit,
            )
        )

    async def _console_logs(
        self,
        session_id: str,
        tab_id: str,
        *,
        levels: list[str] | None,
        filter_text: str,
        limit: int,
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = self._require_tab(session, tab_id)
            normalized_levels = {
                str(value).strip().casefold() for value in (levels or []) if value
            }
            needle = str(filter_text or "").casefold()
            cap = max(1, min(int(limit), 500))
            entries = [
                dict(entry)
                for entry in tab.console_logs
                if (
                    not normalized_levels
                    or str(entry.get("level", "")).casefold() in normalized_levels
                )
                and (
                    not needle
                    or needle in str(entry.get("text", "")).casefold()
                )
            ][-cap:]
            return {
                "ok": True,
                "tab_id": tab.tab_id,
                "entries": entries,
                "count": len(entries),
                "available": len(tab.console_logs),
            }

    def download(
        self,
        session_id: str,
        tab_id: str,
        snapshot_id: str,
        ref: str,
        *,
        destination: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._download(
                session_id,
                tab_id,
                snapshot_id,
                ref,
                destination=destination,
            ),
            timeout=90,
        )

    async def _download(
        self,
        session_id: str,
        tab_id: str,
        snapshot_id: str,
        ref: str,
        *,
        destination: str | None,
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab, locator, generation = await self._require_target(
                session, tab_id, snapshot_id, ref
            )
            tab.download_armed = True
            try:
                async with tab.page.expect_download(
                    timeout=max(30_000, self.action_timeout_ms)
                ) as pending:
                    async with tab.input_lock:
                        self._require_tab_generation(tab, generation)
                        tab.agent_action_depth += 1
                        try:
                            await locator.click(
                                timeout=self.action_timeout_ms
                            )
                        finally:
                            tab.agent_action_depth = max(
                                0, tab.agent_action_depth - 1
                            )
                download = await pending.value
            except Exception as exc:
                self._raise_playwright(exc, "DOWNLOAD_FAILED")
            finally:
                tab.download_armed = False
            suggested = Path(str(download.suggested_filename or "download")).name
            if not suggested or suggested in {".", ".."}:
                suggested = "download"
            target = self._resolve_download_path(
                session, destination, suggested
            )
            try:
                await download.save_as(str(target))
            except Exception as exc:
                self._raise_playwright(exc, "DOWNLOAD_FAILED")
            tab.latest = None
            await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            return {
                "ok": True,
                "tab_id": tab.tab_id,
                "path": str(target),
                "filename": target.name,
                "suggested_filename": suggested,
            }

    def upload(
        self,
        session_id: str,
        tab_id: str,
        snapshot_id: str,
        ref: str,
        paths: list[str],
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._upload(
                session_id, tab_id, snapshot_id, ref, paths
            ),
            timeout=90,
        )

    async def _upload(
        self,
        session_id: str,
        tab_id: str,
        snapshot_id: str,
        ref: str,
        paths: list[str],
    ) -> dict[str, Any]:
        if not isinstance(paths, list) or not 1 <= len(paths) <= 20:
            raise browser_error(
                "INVALID_ARGUMENT", "Upload requires 1 to 20 paths"
            )
        session = self._require_session(session_id)
        resolved = [
            self._resolve_upload_path(session, str(value)) for value in paths
        ]
        if sum(path.stat().st_size for path in resolved) > 500 * 1024 * 1024:
            raise browser_error(
                "INVALID_ARGUMENT", "Combined upload size exceeds 500 MB"
            )
        async with session.operation_lock:
            tab, locator, generation = await self._require_target(
                session, tab_id, snapshot_id, ref
            )
            try:
                async with tab.input_lock:
                    self._require_tab_generation(tab, generation)
                    tab.agent_action_depth += 1
                    try:
                        await locator.set_input_files(
                            [str(path) for path in resolved],
                            timeout=self.action_timeout_ms,
                        )
                    finally:
                        tab.agent_action_depth = max(
                            0, tab.agent_action_depth - 1
                        )
            except Exception as exc:
                self._raise_playwright(exc, "UPLOAD_FAILED")
            tab.latest = None
            await self._capture_frame(
                session, tab, quality=_DISPLAY_JPEG_QUALITY
            )
            result = await self._capture_snapshot(session, tab)
            result["files"] = [path.name for path in resolved]
            return result

    def cdp(
        self,
        session_id: str,
        tab_id: str,
        method: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._cdp(
                session_id, tab_id, method, params=dict(params or {})
            )
        )

    async def _cdp(
        self,
        session_id: str,
        tab_id: str,
        method: str,
        *,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        if not session.developer_mode:
            raise browser_error(
                "DEVELOPER_MODE_REQUIRED",
                "Raw CDP access requires Browser Developer Mode",
            )
        if not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9]*\.[A-Za-z][A-Za-z0-9]*", method or ""
        ):
            raise browser_error("INVALID_ARGUMENT", "Invalid CDP method")
        if method in {
            "Browser.close",
            "Target.closeTarget",
            "Target.disposeBrowserContext",
        }:
            raise browser_error(
                "UNSUPPORTED_ACTION",
                "Browser lifecycle CDP commands are not exposed",
            )
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = self._require_tab(session, tab_id)
            cdp = await session.context.new_cdp_session(tab.page)
            try:
                result = await cdp.send(method, params)
            except Exception as exc:
                self._raise_playwright(exc, "CDP_FAILED")
            finally:
                await cdp.detach()
            return {
                "ok": True,
                "tab_id": tab.tab_id,
                "method": method,
                "result": result,
            }

    def dom_evaluate(
        self,
        session_id: str,
        tab_id: str,
        expression: str,
        *,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._dom_evaluate(
                session_id,
                tab_id,
                expression,
                args=dict(args or {}),
            )
        )

    async def _dom_evaluate(
        self,
        session_id: str,
        tab_id: str,
        expression: str,
        *,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Evaluate a fixed read-only query vocabulary, never arbitrary JavaScript."""

        session = self._require_session(session_id)
        if expression not in {
            "document.title",
            "location.href",
            "document.body.innerText",
            "document.documentElement.lang",
            "query.text",
            "query.html",
            "query.value",
            "query.attribute",
            "query.count",
            "query.box",
            "query.style",
        }:
            raise browser_error(
                "READ_ONLY_EXPRESSION_REQUIRED",
                "Use a documented read-only DOM expression",
            )
        selector = str(args.get("selector") or "")
        if expression.startswith("query.") and (
            not selector or len(selector) > 2_000
        ):
            raise browser_error(
                "INVALID_ARGUMENT", "A valid selector is required"
            )
        async with session.operation_lock:
            tab = self._require_tab(session, tab_id)
            try:
                if expression == "document.title":
                    value = await tab.page.title()
                elif expression == "location.href":
                    value = tab.page.url
                elif expression == "document.body.innerText":
                    value = await tab.page.locator("body").inner_text()
                elif expression == "document.documentElement.lang":
                    value = await tab.page.locator("html").get_attribute("lang")
                else:
                    locator = tab.page.locator(selector)
                    if expression == "query.count":
                        value = await locator.count()
                    else:
                        if await locator.count() != 1:
                            raise browser_error(
                                "LOCATOR_NOT_UNIQUE",
                                "Read-only DOM selectors must match exactly one element",
                            )
                        if expression == "query.text":
                            value = await locator.inner_text()
                        elif expression == "query.html":
                            value = await locator.inner_html()
                        elif expression == "query.value":
                            value = await locator.input_value()
                        elif expression == "query.attribute":
                            name = str(args.get("name") or "")
                            if not re.fullmatch(r"[A-Za-z_:][-A-Za-z0-9_:.]*", name):
                                raise browser_error(
                                    "INVALID_ARGUMENT",
                                    "A valid attribute name is required",
                                )
                            value = await locator.get_attribute(name)
                        elif expression == "query.box":
                            value = await locator.bounding_box()
                        else:
                            properties = args.get("properties") or []
                            if (
                                not isinstance(properties, list)
                                or not 1 <= len(properties) <= 50
                                or any(
                                    not isinstance(item, str)
                                    or not re.fullmatch(r"[-A-Za-z0-9]{1,80}", item)
                                    for item in properties
                                )
                            ):
                                raise browser_error(
                                    "INVALID_ARGUMENT",
                                    "properties must contain 1 to 50 CSS property names",
                                )
                            value = await locator.evaluate(
                                """
                                (element, names) => {
                                  const style = getComputedStyle(element);
                                  return Object.fromEntries(
                                    names.map(name => [name, style.getPropertyValue(name)])
                                  );
                                }
                                """,
                                properties,
                            )
            except BrowserRuntimeError:
                raise
            except Exception as exc:
                self._raise_playwright(exc, "DOM_READ_FAILED")
            if isinstance(value, str) and len(value) > 100_000:
                value = value[:100_000]
                truncated = True
            else:
                truncated = False
            return {
                "ok": True,
                "tab_id": tab.tab_id,
                "expression": expression,
                "value": value,
                "truncated": truncated,
            }

    # -- frames and direct local input ------------------------------------------

    def screenshot(
        self,
        session_id: str,
        *,
        tab_id: str | None = None,
        image_format: str = "jpeg",
        quality: int = 75,
    ) -> dict[str, Any]:
        return self._call(
            lambda: self._screenshot(
                session_id, tab_id, image_format, quality
            )
        )

    async def _screenshot(
        self,
        session_id: str,
        tab_id: str | None,
        image_format: str,
        quality: int,
    ) -> dict[str, Any]:
        if image_format not in {"jpeg", "png"}:
            raise browser_error("INVALID_ARGUMENT", "Unsupported image format")
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = (
                self._require_tab(session, tab_id)
                if tab_id
                else self._active_tab(session)
            )
            return await self._capture_frame(
                session,
                tab,
                image_format=image_format,
                quality=quality,
            )

    async def _capture_frame(
        self,
        session: _Session,
        tab: _Tab,
        *,
        image_format: str = "jpeg",
        quality: int = 75,
    ) -> dict[str, Any]:
        quality = max(1, min(int(quality), 100))
        kwargs: dict[str, Any] = {
            "type": image_format,
            "animations": "disabled",
            "timeout": self.action_timeout_ms,
        }
        if image_format == "jpeg":
            kwargs["quality"] = quality
        data = await tab.page.screenshot(**kwargs)
        tab.frame_sequence += 1
        frame_id = f"frame_{tab.frame_sequence}_{secrets.token_urlsafe(4)}"
        tab.latest_frame_id = frame_id
        viewport = tab.page.viewport_size or {
            "width": self.viewport[0],
            "height": self.viewport[1],
        }
        dpr = session.device_scale_factor
        pixel_width = max(1, round(viewport["width"] * dpr))
        pixel_height = max(1, round(viewport["height"] * dpr))
        event = {
            "type": "browser_frame",
            "version": 1,
            "session_id": session.session_id,
            "tab_id": tab.tab_id,
            "frame_id": frame_id,
            "sequence": tab.frame_sequence,
            "mime_type": f"image/{image_format}",
            "width": pixel_width,
            "height": pixel_height,
            "metadata": {
                "dpr": dpr,
                "device_scale_factor": dpr,
                "viewport_width": viewport["width"],
                "viewport_height": viewport["height"],
            },
            "data": data,
        }
        self._emit(event)
        return {
            "ok": True,
            "session_id": session.session_id,
            "tab_id": tab.tab_id,
            "frame_id": frame_id,
            "mime_type": event["mime_type"],
            "width": pixel_width,
            "height": pixel_height,
            "viewport_width": viewport["width"],
            "viewport_height": viewport["height"],
            "dpr": dpr,
            "data": data,
        }

    def start_screencast(
        self, session_id: str, *, quality: int = _DISPLAY_JPEG_QUALITY
    ) -> dict[str, Any]:
        return self._call(lambda: self._start_screencast(session_id, quality))

    async def _start_screencast(
        self, session_id: str, quality: int
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            tab = self._active_tab(session)
            session.streaming = True
            session.screencast_quality = max(1, min(int(quality), 100))
            await self._handoff_screencast(session, tab)
            return {"ok": True, "tab_id": tab.tab_id}

    async def _handoff_screencast(
        self, session: _Session, target: _Tab
    ) -> None:
        """Move the one live CDP stream to the active tab without taking the lock."""

        for tab in session.tabs.values():
            if tab is not target and (tab.screencasting or tab.cdp is not None):
                await self._stop_tab_screencast(tab)
        if target.screencasting:
            return
        target.cdp = await session.context.new_cdp_session(target.page)

        async def on_frame(params: dict[str, Any]) -> None:
            if not target.screencasting or target.cdp is None:
                return
            await target.cdp.send(
                "Page.screencastFrameAck",
                {"sessionId": params["sessionId"]},
            )
            if session.active_tab_id != target.tab_id:
                return
            target.frame_sequence += 1
            frame_id = (
                f"frame_{target.frame_sequence}_{secrets.token_urlsafe(4)}"
            )
            target.latest_frame_id = frame_id
            metadata = dict(params.get("metadata") or {})
            frame_width = int(metadata.get("deviceWidth") or 1)
            frame_height = int(metadata.get("deviceHeight") or 1)
            # Page.startScreencast emits one image pixel per CSS pixel even when
            # the BrowserContext is HiDPI. Tell the renderer the frame's actual
            # scale; settled Playwright screenshots later replace it at 2x.
            metadata.update(
                {
                    "dpr": 1,
                    "device_scale_factor": 1,
                    "viewport_width": frame_width,
                    "viewport_height": frame_height,
                }
            )
            self._emit(
                {
                    "type": "browser_frame",
                    "version": 1,
                    "session_id": session.session_id,
                    "tab_id": target.tab_id,
                    "frame_id": frame_id,
                    "sequence": target.frame_sequence,
                    "mime_type": "image/jpeg",
                    "width": frame_width,
                    "height": frame_height,
                    "metadata": metadata,
                    "data": base64.b64decode(params["data"]),
                }
            )

        target.cdp.on("Page.screencastFrame", on_frame)
        target.screencasting = True
        await target.cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": session.screencast_quality,
                "everyNthFrame": 1,
            },
        )

    def stop_screencast(self, session_id: str) -> dict[str, Any]:
        return self._call(lambda: self._stop_screencast(session_id))

    async def _stop_screencast(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        async with session.operation_lock:
            session.streaming = False
            for tab in session.tabs.values():
                await self._stop_tab_screencast(tab)
            return {"ok": True}

    async def _stop_tab_screencast(self, tab: _Tab) -> None:
        if tab.cdp is not None:
            if tab.screencasting:
                try:
                    await tab.cdp.send("Page.stopScreencast")
                except Exception:
                    pass
            try:
                await tab.cdp.detach()
            except Exception:
                pass
        tab.cdp = None
        tab.screencasting = False

    @staticmethod
    async def _stop_tab_navigation_guard(tab: _Tab) -> None:
        if tab.guard_cdp is None:
            return
        try:
            await tab.guard_cdp.send("Fetch.disable")
        except Exception:
            pass
        try:
            await tab.guard_cdp.detach()
        except Exception:
            pass
        tab.guard_cdp = None

    def set_takeover(self, session_id: str, active: bool) -> dict[str, Any]:
        """Compatibility no-op for clients predating always-shared input.

        There is intentionally no persisted control owner and ``active`` has no
        effect. New clients should not call this method.
        """

        return self._call(lambda: self._set_takeover(session_id, active))

    async def _set_takeover(
        self, session_id: str, active: bool
    ) -> dict[str, Any]:
        self._require_session(session_id)
        return {
            "ok": True,
            "shared_input": True,
            "deprecated": True,
        }

    async def _direct_event_destination(
        self,
        tab: _Tab,
        *,
        kind: str,
        x: float | None,
        y: float | None,
        key: str,
    ) -> str | None:
        """Return an exact declarative destination for one direct UI event."""

        pointer_event = kind == "mouse_up" and x is not None and y is not None
        keyboard_event = kind == "key_down" and key in {"Enter", "Return"}
        if not pointer_event and not keyboard_event:
            return None
        try:
            value = await tab.page.evaluate(
                """
                ({pointerEvent, x, y}) => {
                  const element = pointerEvent
                    ? document.elementFromPoint(x, y)
                    : document.activeElement;
                  if (!element || !element.closest) return "";
                  const anchor = element.closest("a[href]");
                  if (anchor && anchor.href) return anchor.href;
                  const form = element.closest("form");
                  if (!form) return "";
                  const submit = pointerEvent
                    ? element.closest(
                        "button:not([type]),button[type=submit],input[type=submit],input[type=image]"
                      )
                    : element;
                  if (!submit) return "";
                  const override = submit.getAttribute
                    ? submit.getAttribute("formaction")
                    : "";
                  return new URL(
                    override || form.getAttribute("action") || location.href,
                    document.baseURI
                  ).href;
                }
                """,
                {"pointerEvent": pointer_event, "x": x or 0, "y": y or 0},
            )
        except Exception:
            return None
        return str(value or "") or None

    def dispatch_input(
        self, session_id: str, event: dict[str, Any]
    ) -> dict[str, Any]:
        return self._call(lambda: self._dispatch_input(session_id, event))

    async def _dispatch_input(
        self, session_id: str, event: dict[str, Any]
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        kind = event.get("type")
        tab = self._active_tab(session)
        source_id = event.get("_source_id", "direct")
        if (
            not isinstance(source_id, str)
            or not source_id
            or len(source_id) > 128
        ):
            raise browser_error("INVALID_ARGUMENT", "Invalid input source")
        if kind == "pointer":
            action = event.get("phase", event.get("action", "move"))
            aliases = {
                "move": "mouse_move",
                "down": "mouse_down",
                "up": "mouse_up",
            }
            kind = aliases.get(action)
            if kind is None:
                raise browser_error(
                    "INVALID_ARGUMENT", "Invalid pointer action"
                )
        elif kind == "key":
            action = event.get("phase", event.get("action", "down"))
            kind = {"down": "key_down", "up": "key_up"}.get(action)
            if kind is None:
                raise browser_error("INVALID_ARGUMENT", "Invalid key action")

        interactive = kind != "resize"
        width = height = 0
        requested_dpr = session.device_scale_factor
        x = y = delta_x = delta_y = 0.0
        button = "left"
        click_count = 1
        key = ""
        text = ""
        if kind == "mouse_move":
            x, y = _number(event, "x"), _number(event, "y")
        elif kind in {"mouse_down", "mouse_up"}:
            has_x = "x" in event
            has_y = "y" in event
            if has_x != has_y:
                raise browser_error(
                    "INVALID_ARGUMENT",
                    "Pointer down/up requires both x and y when either is supplied",
                )
            if has_x:
                x, y = _number(event, "x"), _number(event, "y")
            button = event.get("button", "left")
            if isinstance(button, int) and not isinstance(button, bool):
                button = {0: "left", 1: "middle", 2: "right"}.get(button)
            if button not in {"left", "right", "middle"}:
                raise browser_error("INVALID_ARGUMENT", "Invalid mouse button")
            click_count = max(
                1, min(int(event.get("click_count", 1)), 3)
            )
        elif kind == "wheel":
            delta_x = _number(event, "delta_x", default=0)
            delta_y = _number(event, "delta_y", default=0)
        elif kind in {"key_down", "key_up"}:
            key = event.get("key")
            if not isinstance(key, str) or not key or len(key) > 100:
                raise browser_error("INVALID_ARGUMENT", "Invalid key")
        elif kind == "text":
            text = event.get("text")
            if not isinstance(text, str) or len(text) > 200_000:
                raise browser_error("INVALID_ARGUMENT", "Invalid text")
        elif kind == "resize":
            width = int(_number(event, "width"))
            height = int(_number(event, "height"))
            requested_dpr = _number(
                event,
                "dpr",
                default=session.device_scale_factor,
            )
            if not (320 <= width <= 7680 and 240 <= height <= 4320):
                raise browser_error(
                    "INVALID_ARGUMENT", "Viewport size is out of range"
                )
            if not 0.5 <= requested_dpr <= 3:
                raise browser_error(
                    "INVALID_ARGUMENT",
                    "Viewport device scale factor is out of range",
                )
        else:
            raise browser_error(
                "INVALID_ARGUMENT", "Unsupported browser input event"
            )

        self._invalidate_for_direct_input(tab)
        if kind == "resize":
            tab.viewport_generation += 1
        scope_id = "event_" + secrets.token_urlsafe(8)
        async with tab.input_lock:
            expected_navigation = await self._direct_event_destination(
                tab,
                kind=kind,
                x=x if "x" in event else None,
                y=y if "y" in event else None,
                key=key,
            )
            scope = None
            if expected_navigation:
                scope = _DirectNavigationScope(
                    _navigation_url_key(expected_navigation)
                )
                tab.direct_navigation_scopes[scope_id] = scope
            try:
                state = (
                    tab.direct_inputs.setdefault(
                        source_id, _DirectInputState()
                    )
                    if interactive
                    else _DirectInputState()
                )
                if kind == "mouse_move":
                    await tab.page.mouse.move(x, y)
                elif kind in {"mouse_down", "mouse_up"}:
                    if "x" in event:
                        await tab.page.mouse.move(x, y)
                    if kind == "mouse_down":
                        if button not in state.mouse_buttons:
                            if not self._direct_button_held(tab, button):
                                await tab.page.mouse.down(
                                    button=button,
                                    click_count=click_count,
                                )
                            state.mouse_buttons.add(button)
                    elif button in state.mouse_buttons:
                        state.mouse_buttons.discard(button)
                        if not self._direct_button_held(tab, button):
                            await tab.page.mouse.up(
                                button=button,
                                click_count=click_count,
                            )
                elif kind == "wheel":
                    await tab.page.mouse.wheel(delta_x, delta_y)
                elif kind in {"key_down", "key_up"}:
                    if kind == "key_down":
                        if key not in state.keys:
                            if not self._direct_key_held(tab, key):
                                await tab.page.keyboard.down(key)
                            state.keys.add(key)
                    elif key in state.keys:
                        state.keys.discard(key)
                        if not self._direct_key_held(tab, key):
                            await tab.page.keyboard.up(key)
                elif kind == "text":
                    await tab.page.keyboard.insert_text(text)
                else:
                    await tab.page.set_viewport_size(
                        {"width": width, "height": height}
                    )
                    tab.latest_frame_id = None
                if scope is not None and not scope.claimed:
                    # Browser event dispatch returns just before Chromium emits
                    # Fetch.requestPaused. Wait only for this exact hit-tested
                    # destination to claim its one request-chain identity.
                    try:
                        await asyncio.wait_for(
                            scope.claimed_event.wait(), timeout=0.25
                        )
                    except asyncio.TimeoutError:
                        pass
            finally:
                if scope is not None:
                    tab.direct_navigation_scopes.pop(scope_id, None)
        if kind == "resize":
            await self._capture_frame(
                session,
                tab,
                quality=_DISPLAY_JPEG_QUALITY,
            )
        return {
            "ok": True,
            "type": kind,
            **({"actor": "user"} if interactive else {}),
            **(
                {
                    "width": width,
                    "height": height,
                    "requested_dpr": requested_dpr,
                    "dpr": session.device_scale_factor,
                }
                if kind == "resize"
                else {}
            ),
        }

    def release_direct_input(
        self, session_id: str, *, source_id: str | None = None
    ) -> dict[str, Any]:
        """Release keys/buttons left down by a disconnected local viewport."""

        return self._call(
            lambda: self._release_direct_input(
                session_id, source_id=source_id
            )
        )

    async def _release_direct_input(
        self, session_id: str, *, source_id: str | None = None
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        released_buttons = 0
        released_keys = 0
        for tab in tuple(session.tabs.values()):
            released = await self._release_tab_direct_input(
                tab, source_id=source_id
            )
            released_buttons += released["released_buttons"]
            released_keys += released["released_keys"]
        return {
            "ok": True,
            "released_buttons": released_buttons,
            "released_keys": released_keys,
        }

    @classmethod
    async def _release_tab_direct_input(
        cls, tab: _Tab, *, source_id: str | None = None
    ) -> dict[str, int]:
        released_buttons = 0
        released_keys = 0
        source_ids = (
            [source_id]
            if source_id is not None
            else list(tab.direct_inputs)
        )
        states = [
            tab.direct_inputs.pop(value)
            for value in source_ids
            if value in tab.direct_inputs
        ]
        if states:
            cls._invalidate_for_direct_input(tab)
        async with tab.input_lock:
            if not tab.page.is_closed():
                buttons = set().union(
                    *(state.mouse_buttons for state in states)
                )
                keys = set().union(*(state.keys for state in states))
                for button in buttons:
                    if cls._direct_button_held(tab, button):
                        continue
                    try:
                        await tab.page.mouse.up(button=button)
                        released_buttons += 1
                    except Exception:
                        pass
                for key in keys:
                    if cls._direct_key_held(tab, key):
                        continue
                    try:
                        await tab.page.keyboard.up(key)
                        released_keys += 1
                    except Exception:
                        pass
        return {
            "released_buttons": released_buttons,
            "released_keys": released_keys,
        }

    @staticmethod
    def _direct_button_held(tab: _Tab, button: str) -> bool:
        return any(
            button in state.mouse_buttons
            for state in tab.direct_inputs.values()
        )

    @staticmethod
    def _direct_key_held(tab: _Tab, key: str) -> bool:
        return any(key in state.keys for state in tab.direct_inputs.values())

    # -- helpers -----------------------------------------------------------------

    @staticmethod
    def _normalize_file_roots(
        values: list[str | Path] | tuple[str | Path, ...] | None,
    ) -> tuple[Path, ...]:
        candidates = (
            list(values)
            if values is not None
            else [Path.cwd(), Path.home() / "Downloads"]
        )
        roots: list[Path] = []
        for value in candidates:
            try:
                root = Path(value).expanduser().resolve()
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            if root not in roots:
                roots.append(root)
        return tuple(roots)

    @staticmethod
    def _path_is_within(path: Path, roots: tuple[Path, ...]) -> bool:
        for root in roots:
            try:
                if os.path.commonpath((str(path), str(root))) == str(root):
                    return True
            except ValueError:
                continue
        return False

    def _resolve_upload_path(self, session: _Session, value: str) -> Path:
        try:
            path = Path(value).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise browser_error(
                "FILE_NOT_FOUND", "Upload file does not exist"
            ) from exc
        if not path.is_file():
            raise browser_error(
                "INVALID_ARGUMENT", "Upload path must be a regular file"
            )
        if not self._path_is_within(path, session.allowed_file_roots):
            raise browser_error(
                "FILE_ACCESS_DENIED",
                "Upload path is outside the workspace and approved download roots",
            )
        return path

    def _resolve_download_path(
        self,
        session: _Session,
        destination: str | None,
        suggested_filename: str,
    ) -> Path:
        if not session.allowed_file_roots:
            raise browser_error(
                "FILE_ACCESS_DENIED", "No browser download directory is configured"
            )
        if destination:
            requested = Path(destination).expanduser()
            try:
                if requested.exists() and requested.is_dir():
                    requested = requested / suggested_filename
                parent = requested.parent.resolve(strict=True)
            except (OSError, RuntimeError, ValueError) as exc:
                raise browser_error(
                    "FILE_NOT_FOUND",
                    "The browser download directory does not exist",
                ) from exc
            target = (parent / requested.name).resolve()
        else:
            preferred = next(
                (
                    root
                    for root in session.allowed_file_roots
                    if root.name.casefold() == "downloads" and root.is_dir()
                ),
                session.allowed_file_roots[0],
            )
            if not preferred.is_dir():
                raise browser_error(
                    "FILE_NOT_FOUND",
                    "The browser download directory does not exist",
                )
            target = (preferred / suggested_filename).resolve()
        if not target.name or target.name in {".", ".."}:
            raise browser_error("INVALID_ARGUMENT", "Invalid download filename")
        if not self._path_is_within(target, session.allowed_file_roots):
            raise browser_error(
                "FILE_ACCESS_DENIED",
                "Download path is outside the configured browser roots",
            )
        if target.exists():
            stem, suffix = target.stem, target.suffix
            for index in range(1, 10_000):
                candidate = target.with_name(f"{stem} ({index}){suffix}")
                if not candidate.exists():
                    target = candidate
                    break
            else:
                raise browser_error(
                    "DOWNLOAD_FAILED", "Could not choose a unique filename"
                )
        return target

    @staticmethod
    def _tab_generation(tab: _Tab) -> tuple[int, int, int]:
        return (
            tab.document_generation,
            tab.viewport_generation,
            tab.input_generation,
        )

    @staticmethod
    def _snapshot_generation(record: _Snapshot) -> tuple[int, int, int]:
        return (
            record.document_generation,
            record.viewport_generation,
            record.input_generation,
        )

    @classmethod
    def _require_tab_generation(
        cls, tab: _Tab, expected: tuple[int, int, int]
    ) -> None:
        current = cls._tab_generation(tab)
        if current == expected:
            return
        tab.latest = None
        raise browser_error(
            "STALE_SNAPSHOT",
            "The page changed after the agent observed it; capture a fresh snapshot",
            expected_generation=list(expected),
            current_generation=list(current),
        )

    @staticmethod
    def _invalidate_for_direct_input(tab: _Tab) -> tuple[int, int, int]:
        # Increment before waiting for the low-level input lock. This lets a
        # human gesture cancel an agent action that is currently animating its
        # pointer without making the human wait for the full high-level action.
        tab.input_generation += 1
        tab.latest = None
        return (
            tab.document_generation,
            tab.viewport_generation,
            tab.input_generation,
        )

    def _validate_page_point(
        self, tab: _Tab, x: Any, y: Any
    ) -> dict[str, float]:
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
        ):
            raise browser_error(
                "INVALID_ARGUMENT", "Browser coordinates must be numbers"
            )
        viewport = tab.page.viewport_size or {
            "width": self.viewport[0],
            "height": self.viewport[1],
        }
        point = {"x": float(x), "y": float(y)}
        if not (
            0 <= point["x"] < viewport["width"]
            and 0 <= point["y"] < viewport["height"]
        ):
            raise browser_error(
                "INVALID_ARGUMENT", "Browser coordinates are outside the viewport"
            )
        return point

    @staticmethod
    def _invalid_drag_point() -> dict[str, float]:
        raise browser_error(
            "INVALID_ARGUMENT", "Every drag path item must contain x and y"
        )

    def _emit_coordinate_visual(
        self,
        session: _Session,
        tab: _Tab,
        action_id: str,
        kind: str,
        phase: str,
        point: dict[str, float],
        frame_id: str | None,
    ) -> None:
        session.event_sequence += 1
        viewport = tab.page.viewport_size or {
            "width": self.viewport[0],
            "height": self.viewport[1],
        }
        self._emit(
            {
                "type": "browser_action_visual",
                "version": 1,
                "session_id": session.session_id,
                "action_id": action_id,
                "tab_id": tab.tab_id,
                "snapshot_id": (
                    tab.latest.snapshot_id if tab.latest is not None else ""
                ),
                "frame_id": frame_id,
                "sequence": session.event_sequence,
                "phase": phase,
                "kind": kind,
                "target": {
                    "ref": "coordinate",
                    "x": point["x"],
                    "y": point["y"],
                },
                "viewport": {
                    **viewport,
                    "dpr": session.device_scale_factor,
                },
            }
        )

    def _active_tab(self, session: _Session) -> _Tab:
        if session.active_tab_id is None:
            raise browser_error("TAB_NOT_FOUND", "No active browser tab")
        return self._require_tab(session, session.active_tab_id)

    @staticmethod
    def _require_tab(session: _Session, tab_id: str | None) -> _Tab:
        tab = session.tabs.get(tab_id or "")
        if tab is None or tab.page.is_closed():
            raise browser_error("TAB_NOT_FOUND", "Browser tab does not exist")
        return tab

    @staticmethod
    def _validate_url(url: str) -> None:
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise browser_error("INVALID_URL", "URL is invalid") from exc
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise browser_error(
                "INVALID_URL", "Browser Use accepts only absolute HTTP(S) URLs"
            )
        if parsed.username or parsed.password:
            raise browser_error(
                "INVALID_URL", "Credentials in browser URLs are not allowed"
            )

    def _emit_visual(
        self,
        session: _Session,
        tab: _Tab,
        action_id: str,
        snapshot_id: str,
        ref: str,
        kind: str,
        phase: str,
        box: dict[str, float] | None,
        frame_id: str | None,
    ) -> None:
        session.event_sequence += 1
        viewport = tab.page.viewport_size or {
            "width": self.viewport[0],
            "height": self.viewport[1],
        }
        target: dict[str, Any] = {"ref": ref}
        if box is not None:
            target.update(
                {
                    "x": box["x"] + box["width"] / 2,
                    "y": box["y"] + box["height"] / 2,
                    "box": box,
                }
            )
        self._emit(
            {
                "type": "browser_action_visual",
                "version": 1,
                "session_id": session.session_id,
                "action_id": action_id,
                "tab_id": tab.tab_id,
                "snapshot_id": snapshot_id,
                "frame_id": frame_id,
                "sequence": session.event_sequence,
                "phase": phase,
                "kind": kind,
                "target": target,
                "viewport": {
                    **viewport,
                    "dpr": session.device_scale_factor,
                },
            }
        )

    @staticmethod
    def _raise_playwright(exc: Exception, default_code: str) -> None:
        if exc.__class__.__name__ == "TimeoutError":
            raise browser_error(
                "ACTION_TIMEOUT", "Browser action timed out"
            ) from exc
        raise browser_error(default_code, str(exc)) from exc


# Name retained for callers that prefer to emphasize registry ownership.
BrowserRuntimeManager = BrowserRuntime


def _navigation_url_key(
    value: str,
) -> tuple[str, str, int | None, str, str] | None:
    """Canonical request identity for matching a direct address-bar request."""

    try:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname:
            return None
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.casefold()
    if (scheme, port) in {("http", 80), ("https", 443)}:
        port = None
    return (
        scheme,
        parsed.hostname.casefold(),
        port,
        parsed.path or "/",
        parsed.query,
    )


def _browser_target_classifications(
    action: str,
    descriptor: str,
    *,
    element_type: str,
    autocomplete: str,
) -> tuple[str, ...]:
    """Infer only target-declared sensitive classes, never inspect typed values."""

    if action not in {"browser_fill", "browser_select", "browser_press"}:
        return ()
    normalized_type = element_type.strip().casefold()
    autocomplete_tokens = {
        token.casefold()
        for token in autocomplete.replace(",", " ").split()
        if token
    }
    labels: set[str] = set()
    if normalized_type == "password" or autocomplete_tokens & {
        "current-password",
        "new-password",
        "one-time-code",
        "username",
    }:
        labels.add("authentication")
    if autocomplete_tokens & {
        "name",
        "honorific-prefix",
        "given-name",
        "additional-name",
        "family-name",
        "honorific-suffix",
        "nickname",
        "email",
        "tel",
        "tel-country-code",
        "tel-national",
        "street-address",
        "address-line1",
        "address-line2",
        "address-line3",
        "address-level1",
        "address-level2",
        "postal-code",
        "country",
        "country-name",
        "bday",
    }:
        labels.add("personal")
    if any(token.startswith("cc-") for token in autocomplete_tokens):
        labels.add("financial")

    if _AUTH_TARGET_RE.search(descriptor):
        labels.add("authentication")
    if _PERSONAL_TARGET_RE.search(descriptor):
        labels.add("personal")
    if _FINANCIAL_TARGET_RE.search(descriptor):
        labels.add("financial")
    if _HEALTH_TARGET_RE.search(descriptor):
        labels.add("health")
    return tuple(sorted(labels))


def _sanitize_snapshot(raw: str) -> str:
    """Remove current values from editable controls while preserving occupancy."""

    lines: list[str] = []
    for line in raw.splitlines():
        match = _INPUT_ROLE_RE.match(line)
        if match:
            had_value = ":" in line[match.end(1) :]
            line = match.group(1) + (
                " [value=non-empty]" if had_value else " [value=empty]"
            )
        lines.append(line)
    return "\n".join(lines)


def _chunk_snapshot(content: str, *, max_chars: int) -> list[str]:
    cap = max(256, min(int(max_chars or 32_768), 32_768))
    if not content:
        return [""]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in content.splitlines(keepends=True):
        if current and size + len(line) > cap:
            chunks.append("".join(current).rstrip("\n"))
            current, size = [], 0
        if len(line) > cap:
            if current:
                chunks.append("".join(current).rstrip("\n"))
                current, size = [], 0
            for offset in range(0, len(line), cap):
                chunks.append(line[offset : offset + cap].rstrip("\n"))
            continue
        current.append(line)
        size += len(line)
    if current:
        chunks.append("".join(current).rstrip("\n"))
    return chunks or [""]


def _number(event: dict[str, Any], key: str, *, default: float | None = None) -> float:
    value = event.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise browser_error("INVALID_ARGUMENT", f"{key} must be a number")
    value = float(value)
    if not -100_000 <= value <= 100_000:
        raise browser_error("INVALID_ARGUMENT", f"{key} is out of range")
    return value
