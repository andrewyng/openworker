"""DM routing + super-agent retirement.

Inbound DMs auto-create/reuse a dedicated external-conversation session. The legacy
dm_session endpoint still exists as a fallback/manual control surface, but regular DM traffic
no longer needs a user-designated UI session.
"""

import asyncio
import base64

import pytest
from fastapi.testclient import TestClient

from coworker.connectors import feishu_event_to_event
from coworker.connectors.base import MessageEvent, SessionSource
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import create_app
from coworker.server.manager import SessionManager


class ScriptedProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


def _dm(text, chat_id="D1", user="bob"):
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform="slack", chat_id=chat_id, user_name=user, chat_type="dm"
        ),
    )


def _feishu_dm(text, chat_id="oc_1", user="ou_1", user_name=None):
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform="feishu",
            chat_id=chat_id,
            user_id=user,
            user_name=user_name or user,
            chat_type="dm",
        ),
    )


def _connect_slack(mgr):
    """Inbound delivery is gated on the connector being CONNECTED (§4.3). Tests used to pass
    by riding the developer's real Slack profile; with the isolated state dir (conftest) each
    test must connect its own."""
    mgr.secrets.put(
        "slack:default",
        {"bot_token": "xoxb-test", "app_token": "xapp-test", "enabled": True},
    )


def _connect_feishu(mgr):
    mgr.secrets.put(
        "feishu:default",
        {"app_id": "cli_test", "app_secret": "sec", "enabled": True},
    )


def test_dm_auto_routes_to_dedicated_session(tmp_path, monkeypatch):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    _connect_slack(mgr)
    delivered: list[tuple[str, str]] = []

    async def fake_deliver(session_id, message, *, source=None):
        delivered.append((session_id, message))

    monkeypatch.setattr(mgr, "deliver_to_session", fake_deliver)

    asyncio.run(mgr._dispatch_inbound(_dm("ping")))
    sid = delivered[0][0]
    assert sid != "sDM"
    assert (
        "ping" in delivered[0][1]
    )  # the tagged text carries the message + a reply handle
    assert mgr.unrouted.list() == []
    links = mgr.inbound_sessions.all()
    assert links == [
        {
            "route_key": "slack:dm:D1",
            "session_id": sid,
            "platform": "slack",
            "chat_type": "dm",
            "chat_id": "D1",
            "user_id": "",
            "user_name": "bob",
            "chat_name": "",
            "thread_id": "",
            "team_id": "",
            "origin": "slack",
            "origin_label": "bob",
            "created_at": links[0]["created_at"],
            "updated_at": links[0]["updated_at"],
        }
    ]
    record = mgr.session_store.load(sid)
    assert record is not None
    assert record.origin == "slack"
    assert record.origin_label == "bob"
    assert "slack:D1" in mgr._engines[sid].permissions.task_rules["send_message"]

    asyncio.run(mgr._dispatch_inbound(_dm("again")))
    assert delivered[-1][0] == sid


def test_dm_without_connected_connector_is_parked(tmp_path):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    assert mgr.dm_session() is None

    asyncio.run(mgr._dispatch_inbound(_dm("hello there")))
    parked = mgr.unrouted.list()
    assert len(parked) == 1
    assert parked[0]["text"] == "hello there"
    assert parked[0]["reason"] == "connector muted for inbound session"


def test_feishu_dm_auto_route_persists_across_manager_reload(tmp_path, monkeypatch):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    _connect_feishu(mgr)
    delivered: list[str] = []

    async def fake_deliver(session_id, message, *, source=None):
        delivered.append(session_id)

    monkeypatch.setattr(mgr, "deliver_to_session", fake_deliver)
    asyncio.run(mgr._dispatch_inbound(_feishu_dm("你好")))
    sid = delivered[0]
    assert "feishu:oc_1" in mgr._engines[sid].permissions.task_rules["send_message"]

    reborn = SessionManager(
        workspace=tmp_path, data_dir=mgr._data_base, provider=ScriptedProvider()
    )
    _connect_feishu(reborn)
    delivered2: list[str] = []

    async def fake_deliver2(session_id, message, *, source=None):
        delivered2.append(session_id)

    monkeypatch.setattr(reborn, "deliver_to_session", fake_deliver2)
    asyncio.run(reborn._dispatch_inbound(_feishu_dm("继续", user_name="Ada Feishu")))
    assert delivered2 == [sid]
    refreshed = reborn.inbound_sessions.all()[0]
    assert refreshed["route_key"] == "feishu:dm:oc_1"
    assert refreshed["user_name"] == "Ada Feishu"
    assert "feishu:oc_1" in reborn._engines[sid].permissions.task_rules["send_message"]
    record = reborn.session_store.load(sid)
    assert record.origin_label == "Ada Feishu"
    assert record.title == "Ada Feishu"


def test_dm_route_endpoints(tmp_path):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    client = TestClient(create_app(mgr))

    assert client.get("/v1/messaging/dm-route").json()["dm_session"] is None
    assert (
        client.post("/v1/messaging/dm-route", json={"session_id": "sX"}).json()[
            "dm_session"
        ]
        == "sX"
    )
    assert client.get("/v1/messaging/dm-route").json()["dm_session"] == "sX"
    # a falsy id clears it
    assert (
        client.post("/v1/messaging/dm-route", json={"session_id": ""}).json()[
            "dm_session"
        ]
        is None
    )


def test_feishu_dm_file_is_saved_to_session_record_dir(tmp_path, monkeypatch):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    _connect_feishu(mgr)
    delivered: list[tuple[str, str, dict | None]] = []

    async def fake_deliver(session_id, message, *, source=None):
        delivered.append((session_id, message, source))

    def fake_download(token, message_id, file_key, resource_type="file"):
        assert message_id == "om_file"
        assert file_key == "file_v3_1"
        assert resource_type == "file"
        return b"abc123", "report.csv"

    monkeypatch.setattr(
        "coworker.server.manager._download_feishu_resource", fake_download
    )
    monkeypatch.setattr(mgr, "deliver_to_session", fake_deliver)

    asyncio.run(
        mgr._dispatch_inbound(
            MessageEvent(
                text="report.csv",
                source=SessionSource(
                    platform="feishu",
                    chat_id="oc_1",
                    user_id="ou_1",
                    user_name="Ada",
                    chat_type="dm",
                ),
                message_id="om_file",
                attachments=[
                    {
                        "platform": "feishu",
                        "type": "file",
                        "resource_type": "file",
                        "key": "file_v3_1",
                        "filename": "report.csv",
                        "message_id": "om_file",
                    }
                ],
            )
        )
    )

    sid = delivered[0][0]
    assert "Downloaded files:" in delivered[0][1]
    saved = mgr.session_record_files_dir(sid) / "report.csv"
    assert saved.read_bytes() == b"abc123"
    assert any(
        r["path"] == str(mgr.session_record_files_dir(sid)) for r in mgr.get_roots(sid)
    )
    assert delivered[0][2]["attachments"][0]["saved_path"] == str(saved)


def test_feishu_dm_image_reaches_session_as_model_visible_content(tmp_path, monkeypatch):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    _connect_feishu(mgr)
    delivered: list[tuple[str, object, dict | None]] = []

    async def fake_deliver(session_id, message, *, source=None):
        delivered.append((session_id, message, source))

    image = b"\x89PNG\r\n\x1a\n" + b"image-bytes"

    def fake_download(token, message_id, image_key, resource_type="file"):
        assert message_id == "om_image"
        assert image_key == "img_v3_1"
        assert resource_type == "image"
        return image, "diagram.png"

    monkeypatch.setattr(
        "coworker.server.manager._download_feishu_resource", fake_download
    )
    monkeypatch.setattr(mgr, "deliver_to_session", fake_deliver)

    asyncio.run(
        mgr._dispatch_inbound(
            MessageEvent(
                text="User sent attachment: diagram.png",
                source=SessionSource(
                    platform="feishu",
                    chat_id="oc_1",
                    user_id="ou_1",
                    user_name="Ada",
                    chat_type="dm",
                ),
                message_id="om_image",
                attachments=[
                    {
                        "platform": "feishu",
                        "type": "image",
                        "resource_type": "image",
                        "key": "img_v3_1",
                        "filename": "diagram.png",
                        "message_id": "om_image",
                    }
                ],
            )
        )
    )

    content = delivered[0][1]
    assert isinstance(content, list)
    assert "Downloaded files:" not in content[0]["text"]
    image_url = content[1]["image_url"]["url"]
    assert content[1]["image_url"]["detail"] == "high"
    assert image_url.startswith("data:image/png;base64,")
    assert base64.b64decode(image_url.split(",", 1)[1]) == image
    assert delivered[0][2]["attachments"][0]["saved_name"] == "diagram.png"
    assert any(
        root["path"] == str(mgr.session_record_files_dir(delivered[0][0]))
        for root in mgr.get_roots(delivered[0][0])
    )


def test_feishu_post_image_reaches_session_as_model_visible_content(tmp_path, monkeypatch):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    _connect_feishu(mgr)
    delivered: list[object] = []

    async def fake_deliver(_session_id, message, *, source=None):
        delivered.append(message)

    monkeypatch.setattr(
        "coworker.server.manager._download_feishu_resource",
        lambda *_args: (b"\x89PNG\r\n\x1a\npost-image", "post.png"),
    )
    monkeypatch.setattr(mgr, "deliver_to_session", fake_deliver)
    event = feishu_event_to_event(
        {
            "event": {
                "sender": {"sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_id": "om_post_image",
                    "chat_id": "oc_1",
                    "chat_type": "p2p",
                    "message_type": "post",
                    "content": '{"zh_cn":{"content":[[{"tag":"text","text":"请识别"},{"tag":"img","image_key":"img_v3_post"}]]}}',
                },
            }
        }
    )
    assert event is not None

    asyncio.run(mgr._dispatch_inbound(event))

    content = delivered[0]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"


def test_dm_session_persists_across_manager_reload(tmp_path):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    mgr.set_dm_session("sKeep")
    # a fresh manager over the same data dir reloads the prefs-backed designation
    reborn = SessionManager(
        workspace=tmp_path, data_dir=mgr._data_base, provider=ScriptedProvider()
    )
    assert reborn.dm_session() == "sKeep"


def test_superagent_surface_is_gone(tmp_path):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    assert not hasattr(mgr, "superagent")
    assert not hasattr(mgr, "sa_register")
    client = TestClient(create_app(mgr))
    # the retired routes 404
    assert client.get("/v1/superagent").status_code == 404
