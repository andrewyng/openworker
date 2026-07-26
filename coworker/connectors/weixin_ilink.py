"""WeChat ClawBot (iLink) HTTP client — QR login, long-poll inbound, text send.

Protocol base: https://ilinkai.weixin.qq.com (Tencent official). Independent of the
OpenClaw npm plugin; same wire format so OpenWorker can host a first-class connector.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from ..secrets import state_dir

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "1.0.3"
BOT_TYPE = "3"
_TIMEOUT_QR = 35.0
_TIMEOUT_UPDATES = 40.0
_TIMEOUT_SEND = 30.0


def _random_wechat_uin() -> str:
    """X-WECHAT-UIN: base64(decimal string of a random uint32)."""
    n = random.randint(0, 0xFFFFFFFF)
    return base64.b64encode(str(n).encode("ascii")).decode("ascii")


def _headers(bot_token: Optional[str] = None) -> dict[str, str]:
    h = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_wechat_uin(),
    }
    if bot_token:
        h["Authorization"] = f"Bearer {bot_token}"
    return h


def _base_url(override: Optional[str] = None) -> str:
    env = os.environ.get("WEIXIN_ILINK_BASE", "").rstrip("/")
    if override:
        return override.rstrip("/")
    if env:
        return env
    return DEFAULT_BASE


def _qr_png_data_url(payload: str) -> str:
    """Encode `payload` (iLink qrcode_img_content) into a PNG data URL for <img src>."""
    import io

    import qrcode

    img = qrcode.make(payload, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def get_bot_qrcode(*, base_url: Optional[str] = None) -> dict[str, Any]:
    """GET /ilink/bot/get_bot_qrcode?bot_type=3 → poll token + scan payload + PNG data URL.

    iLink returns `qrcode_img_content` as a **scan payload URL** (liteapp.weixin.qq.com/…),
    not an image. The GUI must render a QR of that string — we return `qrcode_data_url`.
    """
    import httpx

    base = _base_url(base_url)
    resp = httpx.get(
        f"{base}/ilink/bot/get_bot_qrcode",
        params={"bot_type": BOT_TYPE},
        headers=_headers(),
        timeout=_TIMEOUT_QR,
    )
    resp.raise_for_status()
    data = resp.json()
    qrcode = data.get("qrcode") or data.get("qrcode_token") or ""
    # Protocol field name is misleading: this is the string WeChat scans, not an <img> URL.
    img_content = (
        data.get("qrcode_img_content") or data.get("qrcode_url") or data.get("qrcode_content") or ""
    )
    data_url = _qr_png_data_url(img_content) if img_content else ""
    return {
        "qrcode": qrcode,
        "qrcode_img_content": img_content,
        # qrcode_url is the PNG data URL for <img src> (not the iLink scan payload).
        "qrcode_url": data_url,
        "qrcode_data_url": data_url,
        "raw": data,
    }


def get_qrcode_status(qrcode: str, *, base_url: Optional[str] = None) -> dict[str, Any]:
    """Poll QR status. On confirmed, returns bot_token / ids / baseurl."""
    import httpx

    base = _base_url(base_url)
    headers = {**_headers(), "iLink-App-ClientVersion": "1"}
    resp = httpx.get(
        f"{base}/ilink/bot/get_qrcode_status",
        params={"qrcode": qrcode},
        headers=headers,
        timeout=_TIMEOUT_QR,
    )
    resp.raise_for_status()
    data = resp.json()
    status = data.get("status") or "wait"
    # Normalize credential field names across observed response shapes.
    creds = data.get("credentials") or {}
    bot_token = (
        data.get("bot_token")
        or creds.get("bot_token")
        or data.get("token")
        or ""
    )
    out = {
        "status": status,
        "bot_token": bot_token,
        "ilink_bot_id": data.get("ilink_bot_id")
        or creds.get("ilink_bot_id")
        or data.get("bot_id")
        or "",
        "ilink_user_id": data.get("ilink_user_id")
        or creds.get("ilink_user_id")
        or data.get("user_id")
        or "",
        "baseurl": (data.get("baseurl") or data.get("base_url") or base).rstrip("/"),
        "raw": data,
    }
    return out


def get_updates(
    bot_token: str,
    get_updates_buf: str = "",
    *,
    base_url: Optional[str] = None,
) -> dict[str, Any]:
    """Long-poll getupdates. Returns msgs + next get_updates_buf."""
    import httpx

    base = _base_url(base_url)
    payload = {
        "get_updates_buf": get_updates_buf or "",
        "base_info": {"channel_version": CHANNEL_VERSION},
    }
    resp = httpx.post(
        f"{base}/ilink/bot/getupdates",
        headers=_headers(bot_token),
        json=payload,
        timeout=_TIMEOUT_UPDATES,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "ret": data.get("ret", 0),
        "msgs": data.get("msgs") or [],
        "get_updates_buf": data.get("get_updates_buf") or get_updates_buf or "",
        "raw": data,
    }


def extract_text(msg: dict[str, Any]) -> str:
    """Pull plain text from an inbound WeixinMessage item_list."""
    parts: list[str] = []
    for item in msg.get("item_list") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == 1 or "text_item" in item:
            text = (item.get("text_item") or {}).get("text") or ""
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()


def send_text(
    bot_token: str,
    to_user_id: str,
    text: str,
    context_token: str,
    *,
    base_url: Optional[str] = None,
) -> dict[str, Any]:
    """POST sendmessage with required routing fields. Raises/returns ret on failure."""
    import httpx

    if not context_token:
        return {
            "ok": False,
            "error": (
                "no context_token for this chat — ask the user to message "
                "the WeChat ClawBot once first"
            ),
        }
    base = _base_url(base_url)
    payload = {
        "msg": {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": f"bot-{uuid.uuid4().hex[:12]}",
            "message_type": 2,
            "message_state": 2,
            "context_token": context_token,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        },
        "base_info": {"channel_version": CHANNEL_VERSION},
    }
    try:
        resp = httpx.post(
            f"{base}/ilink/bot/sendmessage",
            headers=_headers(bot_token),
            json=payload,
            timeout=_TIMEOUT_SEND,
        )
        data = resp.json()
        status_code = resp.status_code
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    ret = data.get("ret", 0)
    if ret in (0, None) and status_code < 400:
        return {
            "ok": True,
            "message_id": data.get("msg_id") or data.get("client_id"),
            "raw": data,
        }
    err = data.get("errmsg") or data.get("error") or f"weixin send failed (ret={ret})"
    return {"ok": False, "error": err, "raw": data}


class ContextTokenStore:
    """Persist per-chat context_token under state_dir (required for outbound)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (state_dir() / "weixin_context_tokens.json")
        self._lock = threading.Lock()
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.path.is_file():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def get(self, chat_id: str) -> Optional[str]:
        with self._lock:
            return self._data.get(chat_id) or None

    def put(self, chat_id: str, token: str) -> None:
        if not chat_id or not token:
            return
        with self._lock:
            self._data[chat_id] = token
            self._save()

    def clear(self) -> None:
        with self._lock:
            self._data = {}
            if self.path.is_file():
                try:
                    self.path.unlink()
                except OSError:
                    pass


_STORE: Optional[ContextTokenStore] = None


def context_token_store() -> ContextTokenStore:
    global _STORE
    if _STORE is None:
        _STORE = ContextTokenStore()
    return _STORE
