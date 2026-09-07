"""Unit tests for WeChat ClawBot (iLink) client + ContextTokenStore."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coworker.connectors import weixin_ilink as ilink
from coworker.connectors.setup import save_weixin_qr_credentials
from coworker.connectors.weixin_adapter import weixin_message_to_event
from coworker.secrets import SecretStore


def test_extract_text_from_item_list():
    msg = {
        "item_list": [
            {"type": 1, "text_item": {"text": "hello"}},
            {"type": 1, "text_item": {"text": "world"}},
        ]
    }
    assert ilink.extract_text(msg) == "hello\nworld"


def test_context_token_store_roundtrip(tmp_path: Path):
    store = ilink.ContextTokenStore(tmp_path / "tokens.json")
    assert store.get("u1") is None
    store.put("u1", "ctx-abc")
    assert store.get("u1") == "ctx-abc"
    store2 = ilink.ContextTokenStore(tmp_path / "tokens.json")
    assert store2.get("u1") == "ctx-abc"
    store2.clear()
    assert store2.get("u1") is None


def test_send_text_requires_context_token():
    out = ilink.send_text("tok", "user@im.wechat", "hi", "")
    assert out["ok"] is False
    assert "context_token" in out["error"]


def test_send_text_includes_required_fields():
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"ret": 0, "msg_id": "m1"}
        return m

    with patch("httpx.post", side_effect=fake_post):
        out = ilink.send_text(
            "bot-tok",
            "user@im.wechat",
            "hello",
            "ctx-1",
            base_url="https://ilink.test",
        )
    assert out["ok"] is True
    msg = captured["json"]["msg"]
    assert msg["to_user_id"] == "user@im.wechat"
    assert msg["context_token"] == "ctx-1"
    assert msg["message_type"] == 2
    assert msg["message_state"] == 2
    assert msg["from_user_id"] == ""
    assert msg["client_id"]
    assert captured["headers"]["Authorization"] == "Bearer bot-tok"
    assert captured["headers"]["AuthorizationType"] == "ilink_bot_token"


def test_get_updates_advances_buf():
    def fake_post(url, headers=None, json=None, timeout=None):
        m = MagicMock()
        m.status_code = 200
        m.raise_for_status = lambda: None
        m.json.return_value = {
            "ret": 0,
            "msgs": [{"from_user_id": "u@im.wechat", "message_type": 1}],
            "get_updates_buf": "cursor-2",
        }
        return m

    with patch("httpx.post", side_effect=fake_post):
        data = ilink.get_updates("tok", "cursor-1", base_url="https://ilink.test")
    assert data["get_updates_buf"] == "cursor-2"
    assert len(data["msgs"]) == 1


def test_weixin_message_to_event_and_stores_token(tmp_path: Path, monkeypatch):
    store = ilink.ContextTokenStore(tmp_path / "t.json")
    monkeypatch.setattr(ilink, "context_token_store", lambda: store)
    msg = {
        "from_user_id": "alice@im.wechat",
        "message_type": 1,
        "context_token": "CTX",
        "client_id": "c1",
        "item_list": [{"type": 1, "text_item": {"text": "分析一下"}}],
    }
    ev = weixin_message_to_event(msg)
    assert ev is not None
    assert ev.source.platform == "weixin"
    assert ev.source.chat_id == "alice@im.wechat"
    assert ev.source.chat_type == "dm"
    assert ev.text == "分析一下"
    assert store.get("alice@im.wechat") == "CTX"


def test_weixin_message_skips_bot_echo():
    assert (
        weixin_message_to_event(
            {
                "from_user_id": "bot@im.bot",
                "message_type": 2,
                "item_list": [{"type": 1, "text_item": {"text": "x"}}],
            }
        )
        is None
    )


def test_save_weixin_qr_credentials(tmp_path: Path, monkeypatch):
    secrets = SecretStore(path=tmp_path / "secrets.json")
    out = save_weixin_qr_credentials(
        secrets,
        bot_token="tok",
        ilink_bot_id="b@im.bot",
        ilink_user_id="u@im.wechat",
        baseurl="https://ilink.test/",
    )
    assert out["ok"] is True
    profile = secrets.get("weixin:default")
    assert profile["bot_token"] == "tok"
    assert profile["baseurl"] == "https://ilink.test"
    assert profile["mode"] == "qrcode"
    # QR scanner is pre-allowed (parity with Slack installer) so the first DM is not parked.
    assert profile["allowed_users"] == ["u@im.wechat"]


def test_descriptor_and_platforms():
    from coworker.connectors.config import PLATFORMS
    from coworker.connectors.descriptors import get_descriptor
    from coworker.connectors.catalog_copy import ACCESS

    assert "weixin" in PLATFORMS
    d = get_descriptor("weixin")
    assert d is not None
    assert d.auth == "qrcode"
    assert d.two_way is True
    assert d.channels is False
    assert "weixin" in ACCESS


def test_get_bot_qrcode_encodes_payload_as_png_data_url():
    """iLink qrcode_img_content is a scan URL — must become a data:image PNG, not used as img src."""
    payload = "https://liteapp.weixin.qq.com/q/7GiQu1?qrcode=abc&bot_type=3"

    def fake_get(url, params=None, headers=None, timeout=None):
        m = MagicMock()
        m.status_code = 200
        m.raise_for_status = lambda: None
        m.json.return_value = {"qrcode": "abc", "qrcode_img_content": payload, "ret": 0}
        return m

    with patch("httpx.get", side_effect=fake_get):
        out = ilink.get_bot_qrcode(base_url="https://ilink.test")
    assert out["qrcode"] == "abc"
    assert out["qrcode_img_content"] == payload
    assert out["qrcode_data_url"].startswith("data:image/png;base64,")
    assert out["qrcode_url"] == out["qrcode_data_url"]
    assert "liteapp.weixin.qq.com" not in out["qrcode_data_url"]
