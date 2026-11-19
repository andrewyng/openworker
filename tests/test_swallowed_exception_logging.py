"""Swallowed-exception logging (#31).

Each site below catches deliberately — the action it guards must survive the
failure — but the failure used to vanish. These tests pin the new contract:
`logger.warning(..., exc_info=True)` fires (so field debugging has a trail),
the guarded action still completes, and the parse helpers stay silent only for
the expected "not JSON" miss (anything else surfaces).
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from coworker import cloud
from coworker.audit import AuditStore
from coworker.connectors.base import MessageEvent, SessionSource
from coworker.inbox import InboxStore
from coworker.interactions import decode
from coworker.projects import resolve_board_space, resolve_memory_key
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import create_app
from coworker.server.app import _parse_json
from coworker.server.manager import SessionManager, _parse_inbox_json


class ScriptedProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


def _channel_event(text="deploy failed", chat_id="C1"):
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform="slack", chat_id=chat_id, user_name="bob", chat_type="channel"
        ),
    )


def _connect_slack(mgr):
    mgr.secrets.put(
        "slack:default",
        {"bot_token": "xoxb-test", "app_token": "xapp-test", "enabled": True},
    )


def _warned(caplog, fragment, logger_name):
    """The first WARNING+ record whose message contains `fragment`, else None."""
    for r in caplog.records:
        if (
            r.levelno >= logging.WARNING
            and r.name == logger_name
            and fragment in r.getMessage()
        ):
            return r
    return None


def _poll_warned(caplog, fragment, logger_name, timeout=3.0):
    """Background tasks may land after the response — poll for the record."""
    deadline = time.monotonic() + timeout
    while True:
        rec = _warned(caplog, fragment, logger_name)
        if rec is not None:
            return rec
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.02)


# -- app: gateway hot-reload / token restore / disconnect promote ----------------


def test_gateway_hot_reload_failure_is_logged_and_connect_still_ok(
    tmp_path, monkeypatch, caplog
):
    from coworker.connectors.descriptors import ValidationResult, get_descriptor

    desc = get_descriptor("telegram")
    monkeypatch.setattr(
        desc, "validate", lambda creds: ValidationResult(True, identity="@testbot")
    )

    async def _boom():
        raise RuntimeError("listener down")

    manager = SessionManager(workspace=tmp_path)
    monkeypatch.setattr(manager, "refresh_gateway", _boom)
    client = TestClient(create_app(manager))

    with caplog.at_level(logging.WARNING, logger="coworker.server.app"):
        r = client.post(
            "/v1/connectors/telegram/connect",
            json={"fields": {"bot_token": "T0K", "allowed_users": "u1"}},
        )
    assert r.json()["ok"] is True  # the save itself still succeeds
    rec = _warned(caplog, "hot-reload", "coworker.server.app")
    assert rec is not None and "telegram" in rec.getMessage()
    assert rec.exc_info and isinstance(rec.exc_info[1], RuntimeError)


def test_token_restore_failure_is_logged_and_sign_in_stands(
    tmp_path, monkeypatch, caplog
):
    manager = SessionManager(workspace=tmp_path)
    monkeypatch.setattr(
        cloud, "complete_login", lambda *a, **k: {"ok": True}
    )

    def _boom(*a, **k):
        raise RuntimeError("broker down")

    monkeypatch.setattr(cloud, "sync_connections", _boom)
    client = TestClient(create_app(manager))

    with caplog.at_level(logging.WARNING, logger="coworker.server.app"):
        r = client.get("/auth/callback", params={"code": "c", "state": "s"})
        assert r.status_code == 200 and "Signed in" in r.text
        rec = _poll_warned(caplog, "restore managed connections", "coworker.server.app")
    assert rec is not None
    assert rec.exc_info and isinstance(rec.exc_info[1], RuntimeError)


def test_disconnect_promote_failure_is_logged(tmp_path, monkeypatch, caplog):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider())

    async def _boom(session_id):
        raise RuntimeError("promote jammed")

    monkeypatch.setattr(manager, "promote_pending_prompts", _boom)
    client = TestClient(create_app(manager))

    with caplog.at_level(logging.WARNING, logger="coworker.server.app"):
        with client.websocket_connect("/ws/session/s1?agent=cowork") as ws:
            assert ws.receive_json()["type"] == "ready"
        rec = _poll_warned(caplog, "pending prompts", "coworker.server.app")
    assert rec is not None
    assert rec.exc_info and isinstance(rec.exc_info[1], RuntimeError)


# -- manager: the paths that act on the user --------------------------------------


def test_mirror_delivery_failure_is_logged(tmp_path, caplog):
    mgr = SessionManager(workspace=tmp_path)
    mgr.inbox_routing.set_binding("default", channel="telegram", target="42")

    class _Gateway:
        def supports_interactive(self, target):
            return False

        async def deliver(self, target, text):
            raise RuntimeError("telegram down")

    mgr.gateway = _Gateway()
    item = mgr.inbox.add_approval("s1", "Run it?")

    with caplog.at_level(logging.WARNING, logger="coworker.manager"):
        asyncio.run(mgr.mirror_inbox_item(item))  # must not raise

    rec = _warned(caplog, "mirror inbox item", "coworker.manager")
    assert rec is not None and item.id in rec.getMessage()
    assert rec.exc_info and isinstance(rec.exc_info[1], RuntimeError)


def test_channel_fanout_failure_is_logged_and_other_sessions_kept(
    tmp_path, monkeypatch, caplog
):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    _connect_slack(mgr)
    mgr.subscriptions.subscribe("sBad", "slack:C1")
    mgr.subscriptions.subscribe("sOk", "slack:C1")
    delivered: list[str] = []

    async def _flaky(session_id, message, *, source=None):
        if session_id == "sBad":
            raise RuntimeError("session gone")
        delivered.append(session_id)

    monkeypatch.setattr(mgr, "deliver_to_session", _flaky)

    with caplog.at_level(logging.WARNING, logger="coworker.manager"):
        asyncio.run(mgr._dispatch_inbound(_channel_event()))

    rec = _warned(caplog, "sBad", "coworker.manager")
    assert rec is not None and "deliver" in rec.getMessage()
    assert rec.exc_info and isinstance(rec.exc_info[1], RuntimeError)
    assert delivered == ["sOk"]  # the fan-out is not stopped by one bad session


def test_delete_session_interrupt_failure_is_logged(tmp_path, caplog):
    mgr = SessionManager(workspace=tmp_path)

    class _Engine:
        sandbox_workspace = None  # _close_sandbox has nothing to do

        def request_interrupt(self):
            raise RuntimeError("interrupt jammed")

    mgr._engines["s1"] = _Engine()

    with caplog.at_level(logging.WARNING, logger="coworker.manager"):
        mgr.delete_session("s1")  # must not raise

    rec = _warned(caplog, "interrupt the engine", "coworker.manager")
    assert rec is not None and "s1" in rec.getMessage()
    assert rec.exc_info and isinstance(rec.exc_info[1], RuntimeError)


def test_task_done_notify_failure_is_logged(tmp_path, monkeypatch, caplog):
    from coworker.connectors import senders

    mgr = SessionManager(workspace=tmp_path)
    mgr.secrets.put("telegram:default", {"bot_token": "x"})

    def _boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setitem(senders.DEFAULT_SENDERS, "telegram", _boom)
    task = SimpleNamespace(notify_target="telegram:123", title="T", id="t1")
    run = SimpleNamespace(result_text="done", session_id="__r1__", run_id="r1")

    with caplog.at_level(logging.WARNING, logger="coworker.manager"):
        asyncio.run(mgr._notify_task_done(task, run))  # must not raise

    rec = _warned(caplog, "could not notify telegram:123", "coworker.manager")
    assert rec is not None
    assert rec.exc_info and isinstance(rec.exc_info[1], RuntimeError)


# -- stores: the observer/export hooks --------------------------------------------


def test_inbox_on_change_failure_is_logged_and_item_still_added(tmp_path, caplog):
    def _boom(item):
        raise RuntimeError("observer down")

    store = InboxStore(path=str(tmp_path / "inbox.json"))
    store.on_change = _boom

    with caplog.at_level(logging.WARNING, logger="coworker.inbox"):
        item = store.add_approval("s1", "Run it?")

    assert store.get(item.id).state == "pending"  # the store is untouched
    rec = _warned(caplog, "on_change observer failed", "coworker.inbox")
    assert rec is not None and item.id in rec.getMessage()
    assert rec.exc_info and isinstance(rec.exc_info[1], RuntimeError)


def test_audit_export_hook_failure_is_logged_and_row_still_written(tmp_path, caplog):
    store = AuditStore(tmp_path / "a.db")

    def _boom(row_id):
        raise RuntimeError("export down")

    store.subscribe(_boom)

    with caplog.at_level(logging.WARNING, logger="coworker.audit"):
        store.append({"session_id": "s1", "tool": "bash", "stage": "finished"})

    assert [r["id"] for r in store.list_since(0)] == [1]  # the audited action stands
    rec = _warned(caplog, "export hook failed", "coworker.audit")
    assert rec is not None
    assert rec.exc_info and isinstance(rec.exc_info[1], RuntimeError)
    store.close()


# -- projects: rekey failures ------------------------------------------------------


def test_rekey_failures_are_logged_and_derived_key_returned(tmp_path, monkeypatch, caplog):
    from coworker import projects as projects_mod

    monkeypatch.setattr(projects_mod, "project_key", lambda ws: "git:acme")

    class _Store:
        def rekey_workspace(self, old, new):
            raise RuntimeError("db locked")

        def rekey_space(self, old, new):
            raise RuntimeError("db locked")

    with caplog.at_level(logging.WARNING, logger="coworker.projects"):
        assert resolve_memory_key(str(tmp_path), memory_store=_Store()) == "git:acme"
        assert resolve_board_space(str(tmp_path), team_store=_Store()) == "git:acme"

    for fragment in ("rekey workspace memory", "rekey board space"):
        rec = _warned(caplog, fragment, "coworker.projects")
        assert rec is not None
        assert rec.exc_info and isinstance(rec.exc_info[1], RuntimeError)


# -- parse helpers: silence is only for the expected miss --------------------------


def test_parse_helpers_stay_silent_for_expected_misses():
    # a resolution may legitimately be plain text — a miss, not a failure
    assert _parse_json("allow") == {}
    assert _parse_inbox_json("allow") == {}
    assert decode("not json") is None
    assert decode("x") is None
    # and a real payload still parses everywhere
    assert _parse_json('{"a": 1}') == {"a": 1}
    assert _parse_inbox_json('{"a": 1}') == {"a": 1}
    assert decode('{"id": "i1", "r": "approve"}') == ("i1", "approve")


def test_unexpected_parse_errors_surface_not_swallowed(monkeypatch):
    import json as json_mod

    def _bug(s):
        raise RuntimeError("bug")

    monkeypatch.setattr(json_mod, "loads", _bug)
    with pytest.raises(RuntimeError):
        _parse_json("x")
    with pytest.raises(RuntimeError):
        _parse_inbox_json("x")
    with pytest.raises(RuntimeError):
        decode("x")
