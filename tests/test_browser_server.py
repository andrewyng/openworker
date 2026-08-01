from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from coworker.automation import Schedule, ScheduledTask
from coworker.browser_security.vault import (
    EncryptedBrowserProfileVault,
    InMemoryKeyProtector,
)
from coworker.browser_security.destination import is_explicit_local_origin
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.permissions import PermissionEngine
from coworker.server import SessionManager, create_app
from coworker.server.manager import _grants_of


class _Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


class _Bound:
    def __init__(self, runtime: "_Runtime", session_id: str) -> None:
        self.runtime = runtime
        self.session_id = session_id

    def __getattr__(self, name: str):
        method = getattr(self.runtime, name)
        return lambda *args, **kwargs: method(
            self.session_id, *args, **kwargs
        )


class _Runtime:
    """Small transport fake; the runtime core has its own Playwright tests."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.subscribers: dict[
            str, tuple[Callable[[dict[str, Any]], Any], str | None]
        ] = {}
        self.closed = False
        self.last_create_options: dict[str, Any] = {}
        self.inputs: list[tuple[str, dict[str, Any]]] = []
        self.input_releases: list[tuple[str, str | None]] = []
        self._counter = 0

    def bind(self, session_id: str) -> _Bound:
        return _Bound(self, session_id)

    def create_session(self, session_id: str, **options) -> dict[str, Any]:
        self.last_create_options = dict(options)
        self.sessions.setdefault(
            session_id,
            {
                "url": "about:blank",
                "title": "New tab",
                "storage": options.get("storage_state")
                or {"cookies": [], "origins": []},
            },
        )
        state = self.state(session_id)
        self.emit(session_id, {"type": "browser_state", **state})
        return state

    def state(self, session_id: str) -> dict[str, Any]:
        item = self.sessions[session_id]
        return {
            "ok": True,
            "session_id": session_id,
            "status": "open",
            "capabilities": {"shared_input": True},
            "active_tab_id": "tab_1",
            "tabs": [
                {
                    "tab_id": "tab_1",
                    "url": item["url"],
                    "title": item["title"],
                    "active": True,
                }
            ],
        }

    def navigate(self, session_id: str, url: str, **_kwargs) -> dict[str, Any]:
        self.sessions[session_id]["url"] = url
        self.sessions[session_id]["title"] = "Example"
        return {
            "ok": True,
            "session_id": session_id,
            "tab_id": "tab_1",
            "snapshot_id": "snap_1",
            "url": url,
            "snapshot": "- heading \"Example\" [ref=h1]",
        }

    user_navigate = navigate

    def history(self, session_id: str, direction: str) -> dict[str, Any]:
        return {"ok": True, "session_id": session_id, "direction": direction}

    user_history = history

    def snapshot(self, session_id: str, **_kwargs) -> dict[str, Any]:
        return self.navigate(session_id, self.sessions[session_id]["url"])

    def snapshot_more(self, session_id: str, cursor: str) -> dict[str, Any]:
        return {"ok": True, "session_id": session_id, "continuation": None}

    def screenshot(self, session_id: str, **_kwargs) -> dict[str, Any]:
        self._counter += 1
        event = {
            "type": "browser_frame",
            "version": 1,
            "session_id": session_id,
            "tab_id": "tab_1",
            "frame_id": f"frame_{self._counter}",
            "sequence": self._counter,
            "mime_type": "image/jpeg",
            "width": 1280,
            "height": 900,
            "metadata": {
                "viewport_width": 1280,
                "viewport_height": 900,
                "dpr": 1,
            },
            "data": b"jpeg-frame",
        }
        self.emit(session_id, event)
        return {"ok": True, **event}

    def click(self, session_id: str, *_args) -> dict[str, Any]:
        return self.snapshot(session_id)

    fill = click
    press = click
    select = click
    hover = click

    def scroll(self, session_id: str, **_kwargs) -> dict[str, Any]:
        return self.snapshot(session_id)

    def tabs(self, session_id: str) -> dict[str, Any]:
        return self.state(session_id)

    def select_tab(self, session_id: str, _tab_id: str) -> dict[str, Any]:
        return self.snapshot(session_id)

    def close_tab(self, session_id: str, _tab_id: str) -> dict[str, Any]:
        return self.state(session_id)

    def storage_state(self, session_id: str) -> dict[str, Any]:
        return self.sessions[session_id]["storage"]

    def set_takeover(self, session_id: str, active: bool) -> dict[str, Any]:
        return {"ok": True, "shared_input": True, "deprecated": True}

    def dispatch_input(
        self, session_id: str, event: dict[str, Any]
    ) -> dict[str, Any]:
        self.inputs.append((session_id, dict(event)))
        return {"ok": True, "type": event["type"], "actor": "user"}

    def release_direct_input(
        self, session_id: str, *, source_id: str | None = None
    ) -> dict[str, Any]:
        self.input_releases.append((session_id, source_id))
        return {"ok": True, "released_buttons": 0, "released_keys": 0}

    def acknowledge_cursor(
        self, _session_id: str, _action_id: str, *, frame_id=None
    ) -> dict[str, Any]:
        return {"ok": True, "accepted": True}

    def dialog(
        self,
        session_id: str,
        action: str,
        *,
        prompt_text: str | None = None,
    ) -> dict[str, Any]:
        self.sessions[session_id]["last_dialog"] = {
            "action": action,
            "prompt_text": prompt_text,
        }
        return {
            "ok": True,
            "session_id": session_id,
            "action": action,
            "snapshot_id": "snap_after_dialog",
        }

    def start_screencast(self, _session_id: str) -> dict[str, Any]:
        return {"ok": True}

    def close_session(self, session_id: str) -> dict[str, Any]:
        self.sessions.pop(session_id, None)
        self.emit(
            session_id,
            {
                "type": "browser_state",
                "session_id": session_id,
                "status": "closed",
            },
        )
        return {"ok": True, "session_id": session_id, "closed": True}

    def subscribe(
        self, callback, *, session_id=None, event_types=None
    ) -> str:
        token = f"sub_{len(self.subscribers) + 1}"
        self.subscribers[token] = (callback, session_id)
        return token

    def unsubscribe(self, token: str) -> None:
        self.subscribers.pop(token, None)

    def emit(self, session_id: str, event: dict[str, Any]) -> None:
        for callback, selected_session in list(self.subscribers.values()):
            if selected_session in {None, session_id}:
                callback(dict(event))

    def close(self) -> None:
        self.closed = True
        self.sessions.clear()


class _ProxyHost:
    def __init__(self) -> None:
        self.sessions: dict[str, list[str]] = {}
        self.closed_sessions: list[str] = []
        self.closed = False
        self.fail_create = False

    def create_session(
        self, session_id: str, *, local_origin_grants=()
    ) -> dict[str, str]:
        if self.fail_create:
            raise RuntimeError("proxy unavailable")
        self.sessions[session_id] = [
            origin
            for origin in local_origin_grants
            if is_explicit_local_origin(origin)
        ]
        return {
            "server": "http://127.0.0.1:43123",
            "username": "openworker",
            "password": "test-proxy-token",
        }

    def grant_local_origin(self, session_id: str, url: str) -> None:
        if is_explicit_local_origin(url):
            self.sessions[session_id].append(url)

    def close_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
        self.closed_sessions.append(session_id)

    def close(self) -> None:
        self.closed = True
        self.sessions.clear()


def _manager(tmp_path):
    runtime = _Runtime()
    proxy_host = _ProxyHost()
    vault = EncryptedBrowserProfileVault(
        tmp_path / "browser-vault",
        key_protector=InMemoryKeyProtector(b"k" * 32),
    )
    return (
        SessionManager(
            workspace=tmp_path,
            provider=_Provider(),
            browser_runtime=runtime,
            browser_profile_vault=vault,
            browser_proxy_host=proxy_host,
        ),
        runtime,
    )


def test_browser_tools_exist_only_on_attended_live_engines(tmp_path):
    manager, runtime = _manager(tmp_path)
    background = manager.get_engine(
        "background", agent="cowork", enable_browser_tools=False
    )
    assert background is not None
    assert "browser_open_url" not in background.registry.names()

    callback = lambda _message: None
    manager.register_session_client("live", callback)
    live = manager.get_engine(
        "live", agent="cowork", enable_browser_tools=True
    )
    assert live is not None
    assert "browser_open_url" in live.registry.names()
    assert "browser_dialog" in live.registry.names()
    docs = live.registry.execute(
        "browser_documentation", {"surface": "iab", "topic": "capabilities"}
    )
    assert docs["ok"] is True
    assert "live" not in runtime.sessions
    opened = live.registry.execute(
        "browser_open_url", {"url": "https://example.com"}
    )
    assert opened["url"] == "https://example.com"
    assert "live" in runtime.sessions
    assert runtime.last_create_options["proxy"]["server"].startswith(
        "http://127.0.0.1:"
    )
    assert callable(runtime.last_create_options["navigation_guard"])
    assert runtime.last_create_options["developer_mode"] is False
    assert runtime.last_create_options["allowed_file_roots"][0].endswith(
        "/Downloads"
    )
    assert str(tmp_path.resolve()) in runtime.last_create_options[
        "allowed_file_roots"
    ]
    assert manager.browser_proxy_host.sessions["live"] == []

    manager.browser_navigate("live", "http://127.0.0.1:4173/demo")
    assert manager.browser_proxy_host.sessions["live"] == [
        "http://127.0.0.1:4173/demo"
    ]

    manager.unregister_session_client("live", callback)
    stopped = live.registry.execute(
        "browser_snapshot", {"max_chars": 1000}
    )
    assert stopped["error"] == "ATTENDED_SESSION_REQUIRED"
    assert "live" not in runtime.sessions

    task = ScheduledTask(
        title="scheduled",
        instructions="do work",
        schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=str(tmp_path),
    )
    scheduled = manager._build_task_engine(task, session_id="task-run")
    assert "browser_open_url" not in scheduled.registry.names()
    assert "browser_dialog" not in scheduled.registry.names()
    manager._close_all_browser_sessions()
    assert manager.browser_proxy_host.closed is True


def test_browser_task_session_grant_survives_engine_reload(tmp_path):
    original_permissions = PermissionEngine(workspace_root=tmp_path)
    original_permissions.allow_browser_for_session()
    grants = _grants_of(SimpleNamespace(permissions=original_permissions))
    assert grants["browser"] is True

    restored_permissions = PermissionEngine(workspace_root=tmp_path)
    SessionManager._apply_grants(
        SimpleNamespace(permissions=restored_permissions),
        grants,
    )
    assert restored_permissions.browser_session_allowed is True


def test_legacy_browser_connector_override_does_not_control_browser_use(tmp_path):
    manager, _runtime = _manager(tmp_path)
    callback = lambda _message: None
    manager.register_session_client("legacy", callback)
    engine = manager.get_engine(
        "legacy", agent="cowork", enable_browser_tools=True
    )
    assert engine is not None
    assert "browser_open_url" in engine.registry.names()

    # Older builds may have persisted this connector-shaped override. Browser
    # Use is now a built-in capability, so the stale value is ignored.
    manager.session_connections.set("legacy", "browser", False)
    assert (
        manager.get_engine(
            "legacy", agent="cowork", enable_browser_tools=True
        )
        is engine
    )
    assert "browser_open_url" in engine.registry.names()
    assert "browser" not in manager.effective_connectors("legacy", "cowork")
    manager._close_all_browser_sessions()


def test_browser_session_fails_closed_when_proxy_cannot_start(tmp_path):
    manager, runtime = _manager(tmp_path)
    callback = lambda _message: None
    manager.register_session_client("proxy-failure", callback)
    manager.browser_proxy_host.fail_create = True

    result = manager.browser_set_takeover("proxy-failure", True)

    assert result["ok"] is False
    assert result["error"] == "RuntimeError"
    assert "proxy unavailable" in result["message"]
    assert "proxy-failure" not in runtime.sessions
    assert "proxy-failure" not in manager._browser_sessions
    manager._close_all_browser_sessions()


def test_profile_toggle_recreates_context_and_releases_lease(tmp_path):
    manager, runtime = _manager(tmp_path)
    callback = lambda _message: None
    manager.register_session_client("profile-toggle", callback)
    manager._ensure_browser_session("profile-toggle")

    enabled = manager.update_browser_profile(remember_signins=True)

    assert enabled["remember_signins"] is True
    assert enabled["has_saved_data"] is True
    assert "profile-toggle" not in runtime.sessions
    assert manager._browser_profile_leases == {}
    assert "profile-toggle" in manager.browser_proxy_host.closed_sessions

    manager._ensure_browser_session("profile-toggle")
    assert runtime.last_create_options["profile_id"] == "default"
    assert runtime.last_create_options["storage_state"] == {
        "cookies": [],
        "origins": [],
    }

    disabled = manager.update_browser_profile(remember_signins=False)
    assert disabled["remember_signins"] is False
    assert "profile-toggle" not in runtime.sessions
    assert manager._browser_profile_leases == {}

    manager._ensure_browser_session("profile-toggle")
    assert runtime.last_create_options["profile_id"] is None
    assert runtime.last_create_options["storage_state"] is None
    manager._close_all_browser_sessions()


def test_browser_rest_lifecycle_and_encrypted_profile_controls(tmp_path):
    manager, runtime = _manager(tmp_path)
    callback = lambda _message: None
    manager.register_session_client("s1", callback)

    with TestClient(create_app(manager)) as client:
        state = client.get(
            "/v1/browser/state", params={"session_id": "s1"}
        ).json()
        assert state["open"] is False

        remembered = client.post(
            "/v1/browser/profile", json={"remember_signins": True}
        ).json()
        assert remembered["remember_signins"] is True

        opened = client.post(
            "/v1/browser/open", json={"session_id": "s1"}
        ).json()
        assert opened["ok"] is True
        assert opened["open"] is True
        assert "s1" in runtime.sessions

        control = client.post(
            "/v1/browser/control",
            json={"session_id": "s1", "takeover": True},
        )
        assert control.headers["Deprecation"] == "true"
        control = control.json()
        assert control["ok"] is True
        assert control["shared_input"] is True
        assert control["deprecated"] is True
        assert "s1" in runtime.sessions

        history = client.post(
            "/v1/browser/history",
            json={"session_id": "s1", "action": "reload"},
        ).json()
        assert history["direction"] == "reload"

        dialog = client.post(
            "/v1/browser/dialog",
            json={
                "session_id": "s1",
                "action": "accept",
                "prompt_text": "typed only for the active prompt",
            },
        ).json()
        assert dialog["ok"] is True
        assert dialog["action"] == "accept"
        assert runtime.sessions["s1"]["last_dialog"] == {
            "action": "accept",
            "prompt_text": "typed only for the active prompt",
        }

        closed = client.post(
            "/v1/browser/close", json={"session_id": "s1"}
        ).json()
        assert closed["closed"] is True
        assert client.get("/v1/browser/profile").json()["has_saved_data"] is True

        cleared = client.post(
            "/v1/browser/profile", json={"clear_browser_data": True}
        ).json()
        assert cleared["has_saved_data"] is False


def test_browser_settings_rest_round_trip_and_profile_compatibility(tmp_path):
    manager, _runtime = _manager(tmp_path)

    with TestClient(create_app(manager)) as client:
        defaults = client.get("/v1/browser/settings").json()
        assert defaults["site_access_mode"] == "ask"
        assert defaults["allowed_hosts"] == []
        assert defaults["blocked_hosts"] == []
        assert defaults["remember_signins"] is False
        assert defaults["ask_download_location"] is False
        assert defaults["developer_mode"] is False

        updated = client.post(
            "/v1/browser/settings",
            json={
                "site_access_mode": "auto",
                "allowed_hosts": [
                    "Example.COM",
                    "https://allowed.example/a",
                ],
                "blocked_hosts": ["blocked.example"],
                "remember_signins": True,
                "download_directory": "~/Downloads/Agent",
                "ask_download_location": True,
                "developer_mode": True,
            },
        ).json()
        assert updated["ok"] is True
        assert updated["site_access_mode"] == "auto"
        assert updated["allowed_hosts"] == [
            "allowed.example",
            "example.com",
        ]
        assert updated["blocked_hosts"] == ["blocked.example"]
        assert updated["remember_signins"] is True
        assert updated["download_directory"].endswith("/Downloads/Agent")
        assert updated["ask_download_location"] is True
        assert updated["developer_mode"] is True
        # Existing profile endpoint remains a compatible view/update path.
        assert client.get("/v1/browser/profile").json()["remember_signins"] is True
        assert client.post(
            "/v1/browser/profile", json={"remember_signins": False}
        ).json()["remember_signins"] is False
        assert client.get("/v1/browser/settings").json()["remember_signins"] is False

        invalid = client.post(
            "/v1/browser/settings",
            json={"site_access_mode": "sometimes"},
        ).json()
        assert invalid["ok"] is False
        assert invalid["error"] == "INVALID_BROWSER_SETTINGS"

    restarted, _ = _manager(tmp_path)
    persisted = restarted.browser_settings()
    assert persisted["site_access_mode"] == "auto"
    assert persisted["allowed_hosts"] == [
        "allowed.example",
        "example.com",
    ]
    assert persisted["developer_mode"] is True


def test_browser_dialog_rejects_detached_conversation(tmp_path):
    manager, runtime = _manager(tmp_path)
    with TestClient(create_app(manager)) as client:
        result = client.post(
            "/v1/browser/dialog",
            json={"session_id": "detached", "action": "dismiss"},
        ).json()
        assert result["error"] == "ATTENDED_SESSION_REQUIRED"
        assert "detached" not in runtime.sessions


def test_browser_websocket_streams_frame_metadata_then_binary(tmp_path):
    manager, _runtime = _manager(tmp_path)
    with TestClient(create_app(manager)) as client:
        with client.websocket_connect(
            "/ws/session/visible?agent=cowork"
        ) as session_ws:
            assert session_ws.receive_json()["type"] == "ready"
            manager._ensure_browser_session("visible")
            with client.websocket_connect("/ws/browser/visible") as browser_ws:
                state = browser_ws.receive_json()
                assert state["open"] is True
                metadata = browser_ws.receive_json()
                assert metadata["type"] == "browser_frame"
                assert "data" not in metadata
                assert browser_ws.receive_bytes() == b"jpeg-frame"


def test_browser_websocket_allows_viewport_sync_with_shared_input(tmp_path):
    manager, runtime = _manager(tmp_path)
    with TestClient(create_app(manager)) as client:
        with client.websocket_connect(
            "/ws/session/responsive?agent=cowork"
        ) as session_ws:
            assert session_ws.receive_json()["type"] == "ready"
            manager._ensure_browser_session("responsive")
            with client.websocket_connect(
                "/ws/browser/responsive"
            ) as browser_ws:
                assert browser_ws.receive_json()["open"] is True
                assert browser_ws.receive_json()["type"] == "browser_frame"
                browser_ws.receive_bytes()
                browser_ws.send_json(
                    {
                        "type": "resize",
                        "width": 960,
                        "height": 700,
                        "dpr": 2,
                    }
                )
                browser_ws.send_json({"type": "not-a-browser-command"})
                assert (
                    browser_ws.receive_json()["error"]
                    == "INVALID_BROWSER_MESSAGE"
                )

    session_id, stored_event = runtime.inputs[-1]
    event = dict(stored_event)
    assert session_id == "responsive"
    source_id = event.pop("_source_id")
    assert source_id.startswith("viewport_")
    assert event == {
        "type": "resize",
        "width": 960,
        "height": 700,
        "dpr": 2,
    }
    assert runtime.input_releases[-1] == ("responsive", source_id)


def test_direct_input_fast_path_does_not_read_agent_locked_state(tmp_path):
    manager, runtime = _manager(tmp_path)
    callback = lambda _message: None
    manager.register_session_client("shared-fast-path", callback)
    manager._ensure_browser_session("shared-fast-path")

    def blocked_state(_session_id: str):
        raise AssertionError("direct input must not wait for runtime.state")

    runtime.state = blocked_state
    result = manager.browser_dispatch_input(
        "shared-fast-path",
        {"type": "pointer", "action": "move", "x": 4, "y": 8},
    )

    assert result["ok"] is True
    assert result["actor"] == "user"


def test_browser_websocket_allows_frame_rate_pointer_motion(tmp_path):
    manager, _runtime = _manager(tmp_path)
    with TestClient(create_app(manager)) as client:
        with client.websocket_connect(
            "/ws/session/pointer?agent=cowork"
        ) as session_ws:
            assert session_ws.receive_json()["type"] == "ready"
            manager._ensure_browser_session("pointer")
            with client.websocket_connect("/ws/browser/pointer") as browser_ws:
                assert browser_ws.receive_json()["open"] is True
                assert browser_ws.receive_json()["type"] == "browser_frame"
                browser_ws.receive_bytes()
                # The chat socket's 30/10s budget must not be reused here:
                # requestAnimationFrame-coalesced pointer motion can reach 60/s.
                for x in range(40):
                    browser_ws.send_json(
                        {
                            "type": "pointer",
                            "phase": "move",
                            "x": x,
                            "y": 20,
                            "button": 0,
                            "buttons": 0,
                        }
                    )
                # Fast typing produces a key-down/up pair per character and
                # must use the input budget, not the low-rate chrome-command
                # budget (160 events is only 80 quickly typed characters).
                for index in range(160):
                    browser_ws.send_json(
                        {
                            "type": "key",
                            "phase": "down" if index % 2 == 0 else "up",
                            "key": "a",
                            "code": "KeyA",
                        }
                    )
                browser_ws.send_json({"type": "not-a-browser-command"})
                error = browser_ws.receive_json()
                assert error["error"] == "INVALID_BROWSER_MESSAGE"


def test_pointer_motion_never_checkpoints_the_saved_profile(tmp_path):
    manager, _runtime = _manager(tmp_path)
    callback = lambda _message: None
    manager.register_session_client("motion", callback)
    manager._ensure_browser_session("motion")
    scheduled: list[str] = []
    manager._schedule_browser_profile_persist = scheduled.append

    for x in range(40):
        assert manager.browser_dispatch_input(
            "motion",
            {
                "type": "pointer",
                "action": "move",
                "x": x,
                "y": 5,
                "button": "left",
            },
        )["ok"]
    assert scheduled == []

    manager.browser_dispatch_input(
        "motion",
        {
            "type": "pointer",
            "phase": "up",
            "x": 40,
            "y": 5,
            "button": "left",
        },
    )
    assert scheduled == ["motion"]

    manager.browser_dispatch_input(
        "motion",
        {
            "type": "key",
            "phase": "up",
            "key": "Enter",
            "code": "Enter",
        },
    )
    assert scheduled == ["motion", "motion"]
    manager._close_all_browser_sessions()


def test_browser_websocket_rejects_without_live_conversation(tmp_path):
    manager, _runtime = _manager(tmp_path)
    with TestClient(create_app(manager)) as client:
        try:
            with client.websocket_connect("/ws/browser/detached"):
                raise AssertionError("detached browser websocket was accepted")
        except Exception:
            pass
