"""End-to-end coverage for the WeChat ClawBot connector checklist.

Covers the PR test plan without a live phone scan:
  1. QR login REST → PNG data URL → confirmed saves profile + allow-list
  2. Enabling WeChat on a session claims the DM route; inbound DM is delivered
  3. send_message to weixin:… uses the stored context_token
  4. Disconnect clears context tokens
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from coworker.connectors import weixin_ilink as ilink
from coworker.connectors.setup import disconnect_connector, save_weixin_qr_credentials
from coworker.connectors.tools import make_send_message_tool
from coworker.secrets import SecretStore
from coworker.server.app import create_app
from coworker.server.manager import SessionManager


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    monkeypatch.setenv("COWORKER_STATE_DIR", str(d))
    # Reset the process-wide context-token singleton so each test starts clean.
    ilink._STORE = None
    return d


def _client(tmp_path: Path) -> tuple[TestClient, SessionManager]:
    mgr = SessionManager(data_dir=tmp_path / "data")
    return TestClient(create_app(mgr)), mgr


def test_qr_login_rest_connects_and_preallows_scanner(tmp_path: Path, state_dir: Path):
    """Connectors → WeChat → QR confirmed → connected + allow-list."""
    client, _mgr = _client(tmp_path)
    payload = "https://liteapp.weixin.qq.com/q/7GiQu1?qrcode=abc&bot_type=3"

    def fake_get(url, params=None, headers=None, timeout=None):
        m = MagicMock()
        m.status_code = 200
        m.raise_for_status = lambda: None
        if "get_bot_qrcode" in url:
            m.json.return_value = {
                "qrcode": "abc",
                "qrcode_img_content": payload,
                "ret": 0,
            }
        else:
            m.json.return_value = {
                "status": "confirmed",
                "bot_token": "tok-bot",
                "ilink_bot_id": "bot@im.bot",
                "ilink_user_id": "user@im.wechat",
                "baseurl": "https://ilink.test",
            }
        return m

    with patch("httpx.get", side_effect=fake_get):
        qr = client.post("/v1/connectors/weixin/qrcode").json()
        assert qr["ok"] is True
        assert qr["qrcode"] == "abc"
        assert qr["qrcode_data_url"].startswith("data:image/png;base64,")

        st = client.post(
            "/v1/connectors/weixin/qrcode/status", json={"qrcode": "abc"}
        ).json()
        assert st["ok"] is True
        assert st["status"] == "confirmed"
        assert st["connected"] is True
        assert st["account"] == "bot@im.bot"

    listed = {c["name"]: c for c in client.get("/v1/connectors").json()["connectors"]}
    wx = listed["weixin"]
    assert wx["connected"] is True
    assert wx["allowed_users"] == ["user@im.wechat"]
    assert "tok-bot" not in client.get("/v1/connectors").text


@pytest.mark.asyncio
async def test_enable_weixin_claims_dm_route_and_delivers(
    tmp_path: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Enable WeChat on a session → inbound DM lands in that session."""
    from coworker.connectors.base import MessageEvent, SessionSource

    client, mgr = _client(tmp_path)
    save_weixin_qr_credentials(
        mgr.secrets,
        bot_token="tok",
        ilink_bot_id="bot@im.bot",
        ilink_user_id="alice@im.wechat",
        baseurl="https://ilink.test",
    )

    sid = "sess-weixin-dm"
    assert mgr.dm_session() is None
    r = client.post(
        f"/v1/sessions/{sid}/connections",
        json={"connector": "weixin", "enabled": True, "persona": "cowork"},
    ).json()
    assert r["ok"] is True
    assert r["dm_session"] == sid
    assert mgr.dm_session() == sid

    delivered: list[str] = []

    async def _capture(session_id: str, message: str, *, source=None):
        delivered.append(f"{session_id}:{message}")

    monkeypatch.setattr(mgr, "deliver_to_session", _capture)

    await mgr._dispatch_inbound(
        MessageEvent(
            text="你好",
            source=SessionSource(
                platform="weixin",
                chat_id="alice@im.wechat",
                user_id="alice@im.wechat",
                user_name="alice",
                chat_type="dm",
            ),
        )
    )
    assert len(delivered) == 1
    assert delivered[0].startswith(f"{sid}:")
    assert "你好" in delivered[0]


def test_send_message_weixin_uses_context_token(
    tmp_path: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Agent replies via send_message target weixin:…"""
    secrets = SecretStore(path=state_dir / "secrets.json")
    secrets.put(
        "weixin:default",
        {
            "bot_token": "tok",
            "baseurl": "https://ilink.test",
            "enabled": True,
            "allowed_users": ["alice@im.wechat"],
        },
    )
    # SecretStore() inside _send_weixin reads COWORKER_STATE_DIR.
    store = ilink.ContextTokenStore(state_dir / "weixin_context_tokens.json")
    store.put("alice@im.wechat", "CTX-1")
    monkeypatch.setattr(ilink, "context_token_store", lambda: store)

    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"ret": 0, "msg_id": "m99"}
        return m

    tool = make_send_message_tool(secrets)
    with patch("httpx.post", side_effect=fake_post):
        out = tool(target="weixin:alice@im.wechat", text="收到")
    assert out["ok"] is True
    assert out["target"] == "weixin:alice@im.wechat"
    assert captured["json"]["msg"]["context_token"] == "CTX-1"
    assert captured["json"]["msg"]["to_user_id"] == "alice@im.wechat"
    assert "ilink" in captured["url"]


def test_disconnect_clears_context_tokens(tmp_path: Path, state_dir: Path):
    """Disconnect WeChat clears stored context tokens."""
    secrets = SecretStore(path=state_dir / "secrets.json")
    save_weixin_qr_credentials(
        secrets,
        bot_token="tok",
        ilink_bot_id="bot@im.bot",
        ilink_user_id="alice@im.wechat",
    )
    store = ilink.context_token_store()
    store.put("alice@im.wechat", "CTX")
    assert store.get("alice@im.wechat") == "CTX"

    assert disconnect_connector(secrets, "weixin")["ok"] is True
    assert secrets.get("weixin:default") is None
    assert store.get("alice@im.wechat") is None


def test_get_qrcode_status_sends_ilink_client_version():
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["headers"] = headers
        m = MagicMock()
        m.status_code = 200
        m.raise_for_status = lambda: None
        m.json.return_value = {"status": "wait"}
        return m

    with patch("httpx.get", side_effect=fake_get):
        out = ilink.get_qrcode_status("abc", base_url="https://ilink.test")
    assert out["status"] == "wait"
    assert captured["headers"]["iLink-App-ClientVersion"] == "1"
