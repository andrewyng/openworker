from __future__ import annotations

import asyncio
import html
import re
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

pytest.importorskip("playwright")

from coworker.browser import (
    BrowserRuntime,
    BrowserRuntimeError,
    make_browser_tools,
)
from coworker.browser.runtime import _chrome_compatible_user_agent
from coworker.browser_security import BrowserProxyHost


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/download":
            encoded = b"openworker browser download"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header(
                "Content-Disposition", 'attachment; filename="browser-report.txt"'
            )
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        if self.path == "/redirect-cross-host":
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://localhost:{self.server.server_port}/page2",
            )
            self.end_headers()
            return
        if self.path == "/page2":
            body = "<title>Page two</title><h1>Page two</h1><a href='/'>Home</a>"
        elif self.path == "/popup":
            body = "<title>Popup</title><button>Popup action</button>"
        elif self.path == "/set":
            body = """
                <title>Auth</title>
                <script>
                  document.cookie = "ow_session=alpha; path=/";
                  localStorage.setItem("ow_local", "alpha");
                </script>
                <p>auth set</p>
            """
        elif self.path == "/inspect":
            body = """
                <title>Inspect</title>
                <p id="cookies"></p><p id="local"></p>
                <script>
                  cookies.textContent = "cookies=" + document.cookie;
                  local.textContent = "local=" + (localStorage.getItem("ow_local") || "");
                </script>
            """
        elif self.path == "/identity":
            request_user_agent = html.escape(
                self.headers.get("User-Agent", ""), quote=True
            )
            body = f"""
                <title>Browser identity</title>
                <p id="requestUserAgent">{request_user_agent}</p>
                <p id="userAgent"></p><p id="webdriver"></p>
                <script>
                  userAgent.textContent = navigator.userAgent;
                  webdriver.textContent = "webdriver=" + navigator.webdriver;
                </script>
            """
        else:
            body = """
                <title>Browser fixture</title>
                <a href="/page2">Next page</a>
                <a id="direct-external" href="http://localhost:__PORT__/page2">Direct external</a>
                <button id="clickme" onclick="this.textContent='Clicked'">Click me</button>
                <input aria-label="Secret input" onkeydown="if(event.key==='Enter') document.querySelector('#key').textContent='Pressed'">
                <input aria-label="Search query" role="searchbox">
                <form><input aria-label="Account password" type="password"><button type="submit">Place order</button></form>
                <p id="key">Not pressed</p>
                <select aria-label="Color"><option value="red">Red</option><option value="blue">Blue</option></select>
                <button onclick="window.open('/popup','_blank')">Open popup</button>
                <button onmouseenter="this.textContent='Hovered'">Hover me</button>
                <button onclick="console.warn('fixture warning')">Log warning</button>
                <a href="/download">Download report</a>
                <input aria-label="Attachments" type="file" multiple>
                <button onclick="alert('Hello from alert'); alertResult.textContent='alert=done'">Show alert</button>
                <button onclick="confirmResult.textContent='confirm=' + confirm('Are you sure?')">Show confirm</button>
                <button onclick="promptResult.textContent='prompt=' + prompt('Your name?', 'Anonymous')">Show prompt</button>
                <p id="alertResult">alert=pending</p>
                <p id="confirmResult">confirm=pending</p>
                <p id="promptResult">prompt=pending</p>
                <p id="pointer">pointer=unset</p>
                <script>
                  addEventListener("mouseup", event => {
                    pointer.textContent = `pointer=${event.clientX},${event.clientY}`;
                  });
                </script>
                <div style="height:1800px"></div><p>Bottom</p>
            """
            body = body.replace("__PORT__", str(self.server.server_port))
        encoded = ("<!doctype html><html><body>" + body + "</body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture(scope="module")
def fixture_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def runtime():
    # Most behavior tests do not need Retina-sized frame payloads.
    value = BrowserRuntime(
        headless=True,
        channel="chromium",
        device_scale_factor=1,
    ).start()
    try:
        yield value
    finally:
        value.close()


def _ref(snapshot: dict, label: str) -> str:
    line = next(line for line in snapshot["snapshot"].splitlines() if label in line)
    match = re.search(r"\[ref=([A-Za-z0-9_-]+)\]", line)
    assert match
    return match.group(1)


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    index = 2
    start_of_frame = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while index < len(data) - 9:
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        length = struct.unpack(">H", data[index : index + 2])[0]
        if marker in start_of_frame:
            height, width = struct.unpack(
                ">HH", data[index + 3 : index + 7]
            )
            return width, height
        index += length
    raise AssertionError("JPEG dimensions were not found")


def test_ref_actions_are_snapshot_scoped_and_values_are_redacted(runtime, fixture_url):
    events: list[dict] = []
    token = runtime.subscribe(events.append, session_id="one")
    runtime.create_session("one")
    snap = runtime.navigate("one", fixture_url)
    state_event = next(
        event for event in reversed(events) if event["type"] == "browser_state"
    )
    assert state_event["tabs"]
    assert state_event["tabs"][0]["tab_id"] == snap["tab_id"]

    input_ref = _ref(snap, "Secret input")
    filled = runtime.fill(
        "one", snap["tab_id"], snap["snapshot_id"], input_ref, "top secret"
    )
    assert "top secret" not in filled["snapshot"]
    assert "[value=non-empty]" in filled["snapshot"]

    with pytest.raises(BrowserRuntimeError) as stale:
        runtime.click("one", snap["tab_id"], snap["snapshot_id"], _ref(snap, "Click me"))
    assert stale.value.code == "STALE_SNAPSHOT"
    assert "latest_snapshot" in stale.value.details

    current = stale.value.details["latest_snapshot"]
    clicked = runtime.click(
        "one",
        current["tab_id"],
        current["snapshot_id"],
        _ref(current, "Click me"),
    )
    assert "Clicked" in clicked["snapshot"]
    selected = runtime.select(
        "one",
        clicked["tab_id"],
        clicked["snapshot_id"],
        _ref(clicked, "Color"),
        "blue",
    )
    pressed = runtime.press(
        "one",
        selected["tab_id"],
        selected["snapshot_id"],
        _ref(selected, "Secret input"),
        "Enter",
    )
    assert "Pressed" in pressed["snapshot"]
    hovered = runtime.hover(
        "one",
        pressed["tab_id"],
        pressed["snapshot_id"],
        _ref(pressed, "Hover me"),
    )
    assert "Hovered" in hovered["snapshot"]
    assert runtime.scroll(
        "one",
        tab_id=hovered["tab_id"],
        delta_y=600,
    )["ok"]
    phases = [
        event["phase"]
        for event in events
        if event["type"] == "browser_action_visual"
        and event["kind"] == "click"
    ]
    assert phases == ["move", "down", "up", "completed"]
    assert any(event["type"] == "browser_frame" for event in events)
    visuals = [
        event
        for event in events
        if event["type"] == "browser_action_visual"
        and event["kind"] == "click"
    ]
    assert visuals[0]["frame_id"] != visuals[-1]["frame_id"]
    post_frame_index = next(
        index
        for index, event in enumerate(events)
        if event["type"] == "browser_frame"
        and event["frame_id"] == visuals[-1]["frame_id"]
    )
    completed_index = events.index(visuals[-1])
    assert post_frame_index < completed_index
    runtime.unsubscribe(token)


def test_live_target_policy_distinguishes_routine_and_consequential_actions(
    runtime, fixture_url
):
    runtime.create_session("policy")
    snapshot = runtime.navigate("policy", fixture_url)

    def arguments(label: str, **extra):
        return {
            "tab_id": snapshot["tab_id"],
            "snapshot_id": snapshot["snapshot_id"],
            "ref": _ref(snapshot, label),
            **extra,
        }

    routine_click = runtime.classify_action(
        "policy", "browser_click", arguments("Click me")
    )
    assert routine_click == {
        "requires_confirmation": False,
        "reasons": [],
    }

    submit = runtime.classify_action(
        "policy", "browser_click", arguments("Place order")
    )
    assert submit["requires_confirmation"] is True
    assert "form_submission" in submit["reasons"]

    search = runtime.classify_action(
        "policy",
        "browser_fill",
        arguments("Search query", value="capybara"),
    )
    assert search["requires_confirmation"] is False

    password = runtime.classify_action(
        "policy",
        "browser_fill",
        arguments("Account password", value="never-persist-this"),
    )
    assert password["requires_confirmation"] is True
    assert "credential_disclosure" in password["reasons"]
    assert "sensitive_input" in password["reasons"]


def test_navigation_history_tabs_and_popup(runtime, fixture_url):
    frames: list[dict] = []
    runtime.subscribe(
        frames.append, session_id="tabs", event_types={"browser_frame"}
    )
    runtime.create_session("tabs")
    first = runtime.navigate("tabs", fixture_url)
    assert frames
    first_frame_count = len(frames)
    second = runtime.click(
        "tabs", first["tab_id"], first["snapshot_id"], _ref(first, "Next page")
    )
    assert second["title"] == "Page two"
    assert len(frames) >= first_frame_count + 2  # pre-click and post-click
    page_two_tab = next(tab for tab in runtime.tabs("tabs")["tabs"] if tab["active"])
    assert page_two_tab["can_go_back"] is True
    back = runtime.history("tabs", "back")
    assert back["title"] == "Browser fixture"
    after_back_tab = next(tab for tab in runtime.tabs("tabs")["tabs"] if tab["active"])
    assert after_back_tab["can_go_forward"] is True

    popup = runtime.click(
        "tabs",
        back["tab_id"],
        back["snapshot_id"],
        _ref(back, "Open popup"),
    )
    tabs = runtime.tabs("tabs")
    assert len(tabs["tabs"]) == 2
    popup_tab = next(tab for tab in tabs["tabs"] if tab["title"] == "Popup")
    selected = runtime.select_tab("tabs", popup_tab["tab_id"])
    assert selected["title"] == "Popup"
    closed = runtime.close_tab("tabs", popup_tab["tab_id"])
    assert len(closed["tabs"]) == 1
    assert popup["ok"]


def test_navigation_guard_rechecks_redirects_and_direct_user_navigation_bypasses_it(
    runtime, fixture_url
):
    events: list[dict] = []
    runtime.subscribe(
        events.append,
        session_id="site-guard",
        event_types={"browser_navigation_blocked"},
    )
    runtime.create_session(
        "site-guard",
        navigation_guard=lambda url: urlsplit(url).hostname == "127.0.0.1",
    )

    with pytest.raises(BrowserRuntimeError) as blocked:
        runtime.navigate("site-guard", fixture_url + "/redirect-cross-host")
    assert blocked.value.code == "NAVIGATION_FAILED"
    assert events
    assert events[-1]["reason"] == "site_permission_required"
    assert events[-1]["url"].startswith("http://localhost:")

    redirected = runtime.user_navigate(
        "site-guard", fixture_url + "/redirect-cross-host"
    )
    assert redirected["title"] == "Page two"
    assert redirected["url"].startswith("http://localhost:")

    result = runtime.user_navigate(
        "site-guard",
        fixture_url.replace("127.0.0.1", "localhost") + "/page2",
    )
    assert result["title"] == "Page two"

    # A direct gesture is not a broad grace period. An agent navigation made
    # immediately afterwards must still pass the persisted hostname policy.
    runtime.dispatch_input(
        "site-guard", {"type": "mouse_move", "x": 10, "y": 10}
    )
    with pytest.raises(BrowserRuntimeError) as after_user_event:
        runtime.navigate(
            "site-guard",
            fixture_url.replace("127.0.0.1", "localhost"),
        )
    assert after_user_event.value.code == "NAVIGATION_FAILED"


def test_direct_pointer_navigation_has_event_scoped_provenance(
    runtime, fixture_url
):
    runtime.create_session(
        "direct-link",
        navigation_guard=lambda url: urlsplit(url).hostname == "127.0.0.1",
    )
    runtime.navigate("direct-link", fixture_url)

    async def link_box():
        session = runtime._require_session("direct-link")
        tab = runtime._active_tab(session)
        return await tab.page.locator("#direct-external").bounding_box()

    box = runtime._call(link_box)
    assert box is not None
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    runtime.dispatch_input(
        "direct-link",
        {
            "type": "pointer",
            "phase": "down",
            "button": 0,
            "x": x,
            "y": y,
            "_source_id": "viewport-a",
        },
    )
    runtime.dispatch_input(
        "direct-link",
        {
            "type": "pointer",
            "phase": "up",
            "button": 0,
            "x": x,
            "y": y,
            "_source_id": "viewport-a",
        },
    )

    async def wait_for_direct_page():
        session = runtime._require_session("direct-link")
        tab = runtime._active_tab(session)
        await tab.page.wait_for_url(re.compile(r"^http://localhost:\d+/page2$"))

    runtime._call(wait_for_direct_page)
    assert runtime.snapshot("direct-link")["title"] == "Page two"


def test_navigation_guard_preserves_authenticated_proxy_credentials(
    runtime, fixture_url
):
    proxy_host = BrowserProxyHost()
    try:
        proxy = proxy_host.create_session(
            "proxy-guard",
            local_origin_grants=[fixture_url],
        )
        runtime.create_session(
            "proxy-guard",
            proxy=proxy,
            navigation_guard=lambda url: (
                urlsplit(url).hostname == "127.0.0.1"
            ),
        )

        result = runtime.navigate("proxy-guard", fixture_url)

        assert result["title"] == "Browser fixture"
    finally:
        runtime.close_session("proxy-guard")
        proxy_host.close()


def test_context_isolation_storage_state_and_profile_lease(runtime, fixture_url):
    runtime.create_session("auth-a", profile_id="saved")
    runtime.navigate("auth-a", fixture_url + "/set")
    a = runtime.navigate("auth-a", fixture_url + "/inspect")
    assert "ow_session=alpha" in a["snapshot"]
    assert "local=alpha" in a["snapshot"]
    state = runtime.storage_state("auth-a")
    assert any(cookie["name"] == "ow_session" for cookie in state["cookies"])

    with pytest.raises(BrowserRuntimeError) as leased:
        runtime.create_session("auth-c", profile_id="saved")
    assert leased.value.code == "PROFILE_IN_USE"

    runtime.create_session("auth-b")
    b = runtime.navigate("auth-b", fixture_url + "/inspect")
    assert "ow_session=alpha" not in b["snapshot"]
    assert "local=alpha" not in b["snapshot"]

    runtime.close_session("auth-a")
    assert runtime.create_session("auth-c", profile_id="saved")["ok"]


def test_context_uses_real_chrome_version_without_hiding_automation(
    runtime, fixture_url
):
    runtime.create_session("identity")
    identity = runtime.navigate("identity", fixture_url + "/identity")

    assert "HeadlessChrome/" not in identity["snapshot"]
    assert len(
        re.findall(
            r"\bChrome/\d+\.\d+\.\d+\.\d+\b", identity["snapshot"]
        )
    ) >= 2
    assert "webdriver=true" in identity["snapshot"]


@pytest.mark.parametrize(
    ("original", "browser_version", "expected"),
    [
        (
            "Mozilla/5.0 (Macintosh) HeadlessChrome/149.0.0.0 Safari/537.36",
            "149.0.7827.55",
            "Mozilla/5.0 (Macintosh) Chrome/149.0.0.0 Safari/537.36",
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0) Chrome/149.0.0.0 Safari/537.36",
            "149.0.7827.55",
            "Mozilla/5.0 (Windows NT 10.0) Chrome/149.0.0.0 Safari/537.36",
        ),
        ("Mozilla/5.0 Firefox/150.0", "149.0.7827.55", None),
        ("Mozilla/5.0 HeadlessChrome/148.0.0.0", "149.0.7827.55", None),
        ("", "149.0.7827.55", None),
    ],
)
def test_chrome_compatible_user_agent_only_rewrites_headless_product(
    original, browser_version, expected
):
    assert (
        _chrome_compatible_user_agent(
            original, browser_version=browser_version
        )
        == expected
    )


def test_user_and_agent_share_browser_without_control_mode(runtime, fixture_url):
    runtime.create_session("shared")
    snap = runtime.navigate("shared", fixture_url)
    assert runtime.state("shared")["capabilities"]["shared_input"] is True
    assert "takeover" not in runtime.state("shared")
    assert runtime.set_takeover("shared", True) == {
        "ok": True,
        "shared_input": True,
        "deprecated": True,
    }
    assert runtime.dispatch_input(
        "shared", {"type": "mouse_move", "x": 10, "y": 10}
    )["ok"]
    with pytest.raises(BrowserRuntimeError) as stale:
        runtime.click(
            "shared",
            snap["tab_id"],
            snap["snapshot_id"],
            _ref(snap, "Click me"),
        )
    assert stale.value.code == "STALE_SNAPSHOT"
    fresh = runtime.snapshot("shared")
    clicked = runtime.click(
        "shared",
        fresh["tab_id"],
        fresh["snapshot_id"],
        _ref(fresh, "Click me"),
    )
    assert "Clicked" in clicked["snapshot"]
    assert runtime.dispatch_input(
        "shared",
        {"type": "pointer", "phase": "down", "button": 0, "x": 50, "y": 60},
    )["type"] == "mouse_down"
    assert runtime.dispatch_input(
        "shared",
        {"type": "pointer", "phase": "up", "button": 0, "x": 50, "y": 60},
    )["type"] == "mouse_up"
    assert runtime.dispatch_input(
        "shared",
        {"type": "key", "phase": "down", "key": "Shift"},
    )["type"] == "key_down"
    assert runtime.dispatch_input(
        "shared",
        {"type": "key", "phase": "up", "key": "Shift"},
    )["type"] == "key_up"
    assert "pointer=50,60" in runtime.snapshot("shared")["snapshot"]
    user_page = runtime.user_navigate("shared", fixture_url + "/page2")
    assert user_page["title"] == "Page two"
    assert runtime.user_history("shared", "back")["title"] == "Browser fixture"


def test_direct_input_does_not_wait_for_agent_operation_lock(runtime, fixture_url):
    runtime.create_session("shared-lock")
    runtime.navigate("shared-lock", fixture_url)
    entered = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []

    async def hold_agent_lock() -> None:
        session = runtime._require_session("shared-lock")
        async with session.operation_lock:
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)

    def hold() -> None:
        try:
            runtime._call(hold_agent_lock, timeout=5)
        except BaseException as exc:
            failures.append(exc)

    holder = threading.Thread(target=hold)
    holder.start()
    assert entered.wait(timeout=2)
    failsafe = threading.Timer(2, release.set)
    failsafe.start()
    try:
        result = runtime.dispatch_input(
            "shared-lock", {"type": "mouse_move", "x": 12, "y": 12}
        )
        assert result["ok"] is True
        assert not release.is_set()
    finally:
        release.set()
        failsafe.cancel()
        holder.join(timeout=2)
    assert not failures


def test_direct_input_cleanup_releases_stuck_buttons_and_keys(runtime, fixture_url):
    runtime.create_session("input-cleanup")
    runtime.user_navigate("input-cleanup", fixture_url)
    runtime.dispatch_input(
        "input-cleanup",
        {"type": "pointer", "phase": "down", "button": 0, "x": 20, "y": 20},
    )
    runtime.dispatch_input(
        "input-cleanup",
        {"type": "key", "phase": "down", "key": "Shift"},
    )

    assert runtime.release_direct_input("input-cleanup") == {
        "ok": True,
        "released_buttons": 1,
        "released_keys": 1,
    }
    assert runtime.release_direct_input("input-cleanup") == {
        "ok": True,
        "released_buttons": 0,
        "released_keys": 0,
    }


def test_direct_input_cleanup_is_scoped_to_one_viewport(runtime, fixture_url):
    runtime.create_session("input-sources")
    runtime.user_navigate("input-sources", fixture_url)
    for source_id in ("viewport-a", "viewport-b"):
        runtime.dispatch_input(
            "input-sources",
            {
                "type": "pointer",
                "phase": "down",
                "button": 0,
                "x": 20,
                "y": 20,
                "_source_id": source_id,
            },
        )
        runtime.dispatch_input(
            "input-sources",
            {
                "type": "key",
                "phase": "down",
                "key": "Shift",
                "_source_id": source_id,
            },
        )

    assert runtime.release_direct_input(
        "input-sources", source_id="viewport-a"
    ) == {"ok": True, "released_buttons": 0, "released_keys": 0}
    assert runtime.release_direct_input(
        "input-sources", source_id="viewport-b"
    ) == {"ok": True, "released_buttons": 1, "released_keys": 1}


@pytest.mark.parametrize("change", ["pointer", "resize", "navigation"])
def test_direct_changes_cancel_waiting_ref_action(runtime, fixture_url, change):
    session_id = f"stale-ref-{change}"
    runtime.create_session(session_id)
    snapshot = runtime.navigate(session_id, fixture_url)
    move_ready = threading.Event()
    action: dict[str, str] = {}
    errors: list[BaseException] = []

    def listener(event: dict) -> None:
        if (
            event["type"] == "browser_action_visual"
            and event["phase"] == "move"
        ):
            action["action_id"] = event["action_id"]
            action["frame_id"] = event["frame_id"]
            move_ready.set()

    token = runtime.subscribe(listener, session_id=session_id)

    def agent_click() -> None:
        try:
            runtime.click(
                session_id,
                snapshot["tab_id"],
                snapshot["snapshot_id"],
                _ref(snapshot, "Click me"),
            )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=agent_click)
    worker.start()
    assert move_ready.wait(timeout=5)
    if change == "pointer":
        runtime.dispatch_input(
            session_id, {"type": "mouse_move", "x": 4, "y": 4}
        )
    elif change == "resize":
        runtime.dispatch_input(
            session_id,
            {"type": "resize", "width": 900, "height": 700, "dpr": 2},
        )
    else:
        runtime.user_navigate(session_id, fixture_url + "/page2")
    runtime.acknowledge_cursor(
        session_id, action["action_id"], frame_id=action["frame_id"]
    )
    worker.join(timeout=5)
    runtime.unsubscribe(token)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], BrowserRuntimeError)
    assert errors[0].code == "STALE_SNAPSHOT"
    current = runtime.snapshot(session_id)
    if change == "navigation":
        assert current["title"] == "Page two"
    else:
        assert "Click me" in current["snapshot"]


def test_direct_input_cancels_waiting_coordinate_click(runtime, fixture_url):
    runtime.create_session("stale-coordinate")
    snapshot = runtime.navigate("stale-coordinate", fixture_url)

    async def button_box():
        session = runtime._require_session("stale-coordinate")
        tab = runtime._active_tab(session)
        return await tab.page.locator("#clickme").bounding_box()

    box = runtime._call(button_box)
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    move_ready = threading.Event()
    action: dict[str, str] = {}
    errors: list[BaseException] = []

    def listener(event: dict) -> None:
        if (
            event["type"] == "browser_action_visual"
            and event["phase"] == "move"
        ):
            action["action_id"] = event["action_id"]
            action["frame_id"] = event["frame_id"]
            move_ready.set()

    token = runtime.subscribe(listener, session_id="stale-coordinate")

    def agent_click() -> None:
        try:
            runtime.coordinate_click(
                "stale-coordinate", snapshot["tab_id"], x, y
            )
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=agent_click)
    worker.start()
    assert move_ready.wait(timeout=5)
    runtime.dispatch_input(
        "stale-coordinate", {"type": "mouse_move", "x": 4, "y": 4}
    )
    runtime.acknowledge_cursor(
        "stale-coordinate",
        action["action_id"],
        frame_id=action["frame_id"],
    )
    worker.join(timeout=5)
    runtime.unsubscribe(token)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], BrowserRuntimeError)
    assert errors[0].code == "STALE_SNAPSHOT"
    assert "Click me" in runtime.snapshot("stale-coordinate")["snapshot"]


def test_resize_is_noninteractive_and_settles_to_hidpi_frame(fixture_url):
    runtime = BrowserRuntime(
        headless=True,
        channel="chromium",
        viewport=(400, 300),
        device_scale_factor=2,
    ).start()
    events: list[dict] = []
    try:
        runtime.subscribe(events.append, session_id="responsive")
        runtime.create_session("responsive")
        runtime.navigate("responsive", fixture_url)

        result = runtime.dispatch_input(
            "responsive",
            {"type": "resize", "width": 520, "height": 360, "dpr": 2},
        )

        assert result == {
            "ok": True,
            "type": "resize",
            "width": 520,
            "height": 360,
            "requested_dpr": 2,
            "dpr": 2,
        }
        frame = [
            event for event in events if event["type"] == "browser_frame"
        ][-1]
        assert frame["width"] == 1040
        assert frame["height"] == 720
        assert frame["metadata"] == {
            "dpr": 2,
            "device_scale_factor": 2,
            "viewport_width": 520,
            "viewport_height": 360,
        }
        assert _jpeg_dimensions(frame["data"]) == (1040, 720)
        assert runtime.snapshot("responsive")["viewport"] == {
            "width": 520,
            "height": 360,
            "dpr": 2,
        }
    finally:
        runtime.close()


def test_screenshot_and_pagination(runtime, fixture_url):
    runtime.create_session("image")
    runtime.navigate("image", fixture_url)
    shot = runtime.screenshot("image", image_format="jpeg")
    assert shot["mime_type"] == "image/jpeg"
    assert shot["data"].startswith(b"\xff\xd8")

    page = runtime.snapshot("image", max_chars=256)
    seen = page["snapshot"]
    while page["continuation"]:
        page = runtime.snapshot_more("image", page["continuation"])
        seen += page["snapshot"]
    assert "Bottom" in seen


def test_advanced_coordinate_viewport_visibility_tabs_console_and_dom(
    runtime, fixture_url
):
    runtime.create_session("advanced")
    snap = runtime.navigate("advanced", fixture_url)

    # The first selector is intentionally ambiguous; read-only DOM access never
    # guesses among multiple elements.
    with pytest.raises(BrowserRuntimeError) as ambiguous:
        runtime.dom_evaluate(
            "advanced",
            snap["tab_id"],
            "query.box",
            args={"selector": "button"},
        )
    assert ambiguous.value.code == "LOCATOR_NOT_UNIQUE"

    click_box = runtime.dom_evaluate(
        "advanced",
        snap["tab_id"],
        "query.box",
        args={"selector": "#clickme"},
    )["value"]
    x = click_box["x"] + click_box["width"] / 2
    y = click_box["y"] + click_box["height"] / 2
    classified = runtime.classify_action(
        "advanced",
        "browser_coordinate_click",
        {"tab_id": snap["tab_id"], "x": x, "y": y},
    )
    assert classified["requires_confirmation"] is False
    clicked = runtime.coordinate_click("advanced", snap["tab_id"], x, y)
    assert "Clicked" in clicked["snapshot"]

    assert runtime.set_visibility("advanced", False) == {
        "ok": True,
        "visible": False,
    }
    assert runtime.state("advanced")["visible"] is False
    viewport = runtime.set_viewport(
        "advanced", width=640, height=480
    )
    assert (viewport["width"], viewport["height"], viewport["dpr"]) == (
        640,
        480,
        1,
    )

    warning = runtime.click(
        "advanced",
        viewport["tab_id"],
        viewport["snapshot_id"],
        _ref(viewport, "Log warning"),
    )
    logs = runtime.console_logs(
        "advanced", warning["tab_id"], levels=["warning"], filter_text="fixture"
    )
    assert logs["entries"][-1]["text"] == "fixture warning"
    assert runtime.dom_evaluate(
        "advanced", warning["tab_id"], "document.title"
    )["value"] == "Browser fixture"

    runtime.navigate("advanced", fixture_url + "/page2", new_tab=True)
    tabs = runtime.tabs("advanced")
    active = tabs["active_tab_id"]
    finalized = runtime.finalize_tabs("advanced", [active])
    assert len(finalized["tabs"]) == 1
    assert finalized["active_tab_id"] == active

    assert runtime.clipboard("advanced", "write", text="Capybara")["length"] == 8
    assert runtime.clipboard("advanced", "read")["text"] == "Capybara"


def test_file_transfer_and_developer_mode(runtime, fixture_url, tmp_path):
    upload = tmp_path / "upload.txt"
    upload.write_text("hello", encoding="utf-8")
    runtime.create_session(
        "transfer",
        allowed_file_roots=[tmp_path],
    )
    snap = runtime.navigate("transfer", fixture_url)
    uploaded = runtime.upload(
        "transfer",
        snap["tab_id"],
        snap["snapshot_id"],
        _ref(snap, "Attachments"),
        [str(upload)],
    )
    assert uploaded["files"] == ["upload.txt"]

    downloaded = runtime.download(
        "transfer",
        uploaded["tab_id"],
        uploaded["snapshot_id"],
        _ref(uploaded, "Download report"),
        destination=str(tmp_path),
    )
    assert downloaded["filename"] == "browser-report.txt"
    assert (tmp_path / "browser-report.txt").read_text(
        encoding="utf-8"
    ) == "openworker browser download"
    with pytest.raises(BrowserRuntimeError) as gated:
        runtime.cdp(
            "transfer",
            downloaded["tab_id"],
            "Runtime.evaluate",
            params={"expression": "1 + 1", "returnByValue": True},
        )
    assert gated.value.code == "DEVELOPER_MODE_REQUIRED"

    runtime.create_session("developer", developer_mode=True)
    dev = runtime.navigate("developer", fixture_url)
    result = runtime.cdp(
        "developer",
        dev["tab_id"],
        "Runtime.evaluate",
        params={"expression": "1 + 1", "returnByValue": True},
    )
    assert result["result"]["result"]["value"] == 2


def test_invalid_urls_and_unknown_session_are_deterministic(runtime):
    with pytest.raises(BrowserRuntimeError) as missing:
        runtime.state("does-not-exist")
    assert missing.value.code == "SESSION_NOT_FOUND"

    runtime.create_session("url")
    for value in ("file:///etc/passwd", "javascript:alert(1)", "http://u:p@x/"):
        with pytest.raises(BrowserRuntimeError) as invalid:
            runtime.navigate("url", value)
        assert invalid.value.code == "INVALID_URL"


def test_tool_factory_is_session_bound_flat_and_non_parallel(runtime, fixture_url):
    runtime.create_session("tools")
    tools = make_browser_tools(runtime, "tools")
    by_name = {tool.__name__: tool for tool in tools}
    assert {
        "browser_open_url",
        "browser_select_surface",
        "browser_history",
        "browser_snapshot",
        "browser_snapshot_more",
        "browser_screenshot",
        "browser_click",
        "browser_fill",
        "browser_press",
        "browser_select",
        "browser_hover",
        "browser_scroll",
        "browser_tabs",
        "browser_select_tab",
        "browser_close_tab",
        "browser_dialog",
        "browser_set_visibility",
        "browser_set_viewport",
        "browser_finalize_tabs",
        "browser_coordinate_move",
        "browser_coordinate_click",
        "browser_coordinate_drag",
        "browser_type_text",
        "browser_keypress",
        "browser_clipboard",
        "browser_console_logs",
        "browser_download",
        "browser_upload",
        "browser_cdp",
        "browser_dom_evaluate",
        "browser_close",
        "browser_surfaces",
        "browser_documentation",
    } == set(by_name)
    assert not {
        "browser_eval",
        "browser_click_coordinates",
    } & set(by_name)
    for tool in tools:
        assert tool.__aisuite_tool_metadata__.risk_level == "medium"
        params = tool.__coworker_schema__["function"]["parameters"]
        assert params["additionalProperties"] is False
        assert "session_id" not in params["properties"]
        assert "selector" not in params["properties"]
    assert by_name["browser_open_url"](fixture_url)["title"] == "Browser fixture"
    assert by_name["browser_tabs"]()["ok"]
    assert by_name["browser_dialog"].__aisuite_tool_metadata__.requires_approval
    assert by_name["browser_upload"].__aisuite_tool_metadata__.requires_approval
    assert by_name["browser_cdp"].__aisuite_tool_metadata__.requires_approval
    assert by_name["browser_select_surface"].__coworker_browser_non_launching__
    assert by_name["browser_surfaces"].__coworker_browser_non_launching__
    assert by_name["browser_documentation"].__coworker_browser_non_launching__


def test_browser_documentation_is_non_launching_and_matches_tool_schemas(runtime):
    tools = make_browser_tools(runtime, "documentation-only")
    by_name = {tool.__name__: tool for tool in tools}

    surfaces = by_name["browser_surfaces"]()
    assert surfaces["ok"]
    assert "never a user request" in surfaces["ambient_ui_state_policy"]
    by_surface = {item["surface"]: item for item in surfaces["surfaces"]}
    assert by_surface["iab"]["available"] is True
    assert set(by_surface["iab"]["tools"]) == set(by_name)
    assert by_surface["chrome"]["available"] is False
    assert set(by_surface) == {"iab", "chrome"}

    docs = by_name["browser_documentation"](surface="iab")
    assert docs["ok"] and docs["available"]
    assert docs["topic"] == "complete"
    assert "ambient browser/UI state" in docs["documentation"]
    assert "There is no take-control or return-control mode" in docs["documentation"]
    catalog = {item["name"]: item for item in docs["tools"]}
    assert set(catalog) == set(by_name)
    for name, tool in by_name.items():
        schema = tool.__coworker_schema__["function"]["parameters"]
        assert catalog[name]["parameters"] == schema
        assert catalog[name]["required"] == schema["required"]

    chrome = by_name["browser_documentation"](surface="chrome")
    assert chrome == {
        "ok": True,
        "surface": "chrome",
        "label": "Google Chrome",
        "available": False,
        "message": (
            "The chrome surface is not registered in this OpenWorker runtime. "
            "Do not substitute another surface."
        ),
        "tools": [],
        "capability_families": [],
        "available_topics": [
            "capabilities",
            "tools",
            "workflow",
            "shared-use",
            "sign-in",
            "safety",
            "errors",
        ],
    }
    with pytest.raises(BrowserRuntimeError) as missing:
        runtime.state("documentation-only")
    assert missing.value.code == "SESSION_NOT_FOUND"


def test_browser_documentation_reports_connected_external_surface(runtime):
    tools = make_browser_tools(
        runtime,
        "external-documentation-only",
        surface_available=lambda surface: surface == "chrome",
    )
    by_name = {tool.__name__: tool for tool in tools}

    surfaces = by_name["browser_surfaces"]()
    by_surface = {item["surface"]: item for item in surfaces["surfaces"]}
    assert by_surface["chrome"]["available"] is True
    assert set(by_surface) == {"iab", "chrome"}
    assert set(by_surface["chrome"]["tools"]) == {
        "browser_select_surface",
        "browser_tabs",
        "browser_select_tab",
        "browser_snapshot",
        "browser_screenshot",
        "browser_click",
        "browser_fill",
        "browser_press",
        "browser_scroll",
        "browser_surfaces",
        "browser_documentation",
        "browser_close",
    }
    docs = by_name["browser_documentation"](surface="chrome")
    assert docs["available"] is True
    assert docs["surface"] == "chrome"
    assert {
        item["name"] for item in docs["tools"]
    } == set(by_surface["chrome"]["tools"])
    with pytest.raises(BrowserRuntimeError) as missing:
        runtime.state("external-documentation-only")
    assert missing.value.code == "SESSION_NOT_FOUND"


def test_cursor_ack_paces_visuals_and_post_action_frame(runtime, fixture_url):
    runtime.create_session("paced")
    snap = runtime.navigate("paced", fixture_url)
    move_ready = threading.Event()
    events: list[dict] = []
    action: dict[str, str] = {}

    def listener(event: dict) -> None:
        events.append(event)
        if (
            event["type"] == "browser_action_visual"
            and event["phase"] == "move"
        ):
            action["action_id"] = event["action_id"]
            action["frame_id"] = event["frame_id"]
            move_ready.set()

    token = runtime.subscribe(listener, session_id="paced")
    result: dict[str, dict] = {}

    def click() -> None:
        result["value"] = runtime.click(
            "paced",
            snap["tab_id"],
            snap["snapshot_id"],
            _ref(snap, "Click me"),
        )

    worker = threading.Thread(target=click)
    worker.start()
    assert move_ready.wait(timeout=5)
    # The real viewport acknowledges after its 240 ms cursor transition.
    time.sleep(0.24)
    ack = runtime.acknowledge_cursor(
        "paced", action["action_id"], frame_id=action["frame_id"]
    )
    assert ack == {"ok": True, "accepted": True}
    worker.join(timeout=5)
    assert not worker.is_alive()
    completed = next(
        event
        for event in events
        if event["type"] == "browser_action_visual"
        and event["phase"] == "completed"
    )
    post_frame = next(
        event
        for event in events
        if event["type"] == "browser_frame"
        and event["frame_id"] == completed["frame_id"]
    )
    assert post_frame["data"].startswith(b"\xff\xd8")
    assert "Clicked" in result["value"]["snapshot"]
    runtime.unsubscribe(token)


def test_screencast_hands_off_to_popup_and_selected_tab(runtime, fixture_url):
    frames: list[dict] = []
    runtime.subscribe(
        frames.append, session_id="stream", event_types={"browser_frame"}
    )
    runtime.create_session("stream")
    first = runtime.navigate("stream", fixture_url)
    original_tab_id = first["tab_id"]
    runtime.start_screencast("stream")
    runtime.click(
        "stream",
        original_tab_id,
        first["snapshot_id"],
        _ref(first, "Open popup"),
    )

    deadline = time.monotonic() + 5
    popup_tab_id = ""
    while time.monotonic() < deadline:
        tabs = runtime.tabs("stream")
        active = next((tab for tab in tabs["tabs"] if tab["active"]), None)
        if active and active["tab_id"] != original_tab_id:
            popup_tab_id = active["tab_id"]
            break
        time.sleep(0.02)
    assert popup_tab_id

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(
        frame["tab_id"] == popup_tab_id for frame in frames
    ):
        time.sleep(0.02)
    assert any(frame["tab_id"] == popup_tab_id for frame in frames)

    marker = len(frames)
    runtime.select_tab("stream", original_tab_id)
    assert any(
        frame["tab_id"] == original_tab_id for frame in frames[marker:]
    )
    assert runtime.tabs("stream")["active_tab_id"] == original_tab_id


def test_dialog_alert_confirm_and_prompt_recovery(runtime, fixture_url):
    events: list[dict] = []
    runtime.subscribe(
        events.append,
        session_id="dialogs",
        event_types={"browser_dialog", "browser_state"},
    )
    runtime.create_session("dialogs")
    snap = runtime.navigate("dialogs", fixture_url)

    with pytest.raises(BrowserRuntimeError) as alert_open:
        runtime.click(
            "dialogs",
            snap["tab_id"],
            snap["snapshot_id"],
            _ref(snap, "Show alert"),
        )
    assert alert_open.value.code == "DIALOG_OPEN"
    assert alert_open.value.details["dialog_type"] == "alert"
    assert alert_open.value.details["message"] == "Hello from alert"
    pending_state = runtime.state("dialogs")
    assert pending_state["dialog"] == {
        "tab_id": snap["tab_id"],
        "dialog_type": "alert",
        "message": "Hello from alert",
        "default_value": "",
    }
    state_event = next(
        event
        for event in reversed(events)
        if event["type"] == "browser_state" and event["dialog"] is not None
    )
    assert state_event["dialog"] == pending_state["dialog"]
    with pytest.raises(BrowserRuntimeError) as modal_snapshot:
        runtime.snapshot("dialogs")
    assert modal_snapshot.value.code == "DIALOG_OPEN"

    alert = runtime.dialog("dialogs", "accept")
    assert alert["dialog"] == {"action": "accept", "type": "alert"}
    assert "alert=done" in alert["snapshot"]
    assert runtime.state("dialogs")["dialog"] is None

    with pytest.raises(BrowserRuntimeError) as confirm_open:
        runtime.click(
            "dialogs",
            alert["tab_id"],
            alert["snapshot_id"],
            _ref(alert, "Show confirm"),
        )
    assert confirm_open.value.code == "DIALOG_OPEN"
    confirm = runtime.dialog("dialogs", "dismiss")
    assert confirm["dialog"] == {"action": "dismiss", "type": "confirm"}
    assert "confirm=false" in confirm["snapshot"]

    with pytest.raises(BrowserRuntimeError) as prompt_open:
        runtime.click(
            "dialogs",
            confirm["tab_id"],
            confirm["snapshot_id"],
            _ref(confirm, "Show prompt"),
        )
    assert prompt_open.value.code == "DIALOG_OPEN"
    prompt = runtime.dialog("dialogs", "accept", prompt_text="Capybara")
    assert prompt["dialog"] == {"action": "accept", "type": "prompt"}
    assert "prompt=Capybara" in prompt["snapshot"]

    dialog_events = [
        event for event in events if event["type"] == "browser_dialog"
    ]
    assert [event["dialog_type"] for event in dialog_events] == [
        "alert",
        "confirm",
        "prompt",
    ]
    assert dialog_events[-1]["default_value"] == "Anonymous"
    with pytest.raises(BrowserRuntimeError) as missing:
        runtime.dialog("dialogs", "dismiss")
    assert missing.value.code == "DIALOG_NOT_FOUND"
    with pytest.raises(BrowserRuntimeError) as invalid:
        runtime.dialog("dialogs", "continue")
    assert invalid.value.code == "INVALID_ARGUMENT"
