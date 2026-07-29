"""Stateless outbound senders — one-shot HTTP POSTs, no SDK, no live connection.

These power the `send_message` tool (and the super-agent's replies). Both Telegram and
Slack outbound are simple HTTP calls, so we use a synchronous `httpx` client and avoid the
heavy SDKs (those are only needed for the inbound listeners). Sync fits the ToolRegistry's
`execute` contract (the engine runs it in a thread).

A `Sender` is `(token, chat_id, text, thread_id) -> SendResult`. The registry is swappable so
tests inject fakes — no network.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Callable, Optional
from urllib.parse import quote, unquote

from .base import SendResult

Sender = Callable[[str, str, str, Optional[str]], SendResult]

_TIMEOUT = 30.0
_FEISHU_TOKEN_CACHE: dict[tuple[str, str, str], tuple[str, float]] = {}


def _slack_api_base() -> str:
    """Web API base URL. `SLACK_API_URL` (trailing slash) lets tests / the FakeSlack harness
    redirect outbound sends to a local fake. See platform/docs/FAKE-SLACK-SPEC.md."""
    return os.environ.get("SLACK_API_URL", "https://slack.com/api/")


def _send_telegram(
    token: str, chat_id: str, text: str, thread_id: Optional[str] = None
) -> SendResult:
    import httpx

    payload: dict = {"chat_id": chat_id, "text": text}
    # Telegram's General forum topic is thread_id "1", which sendMessage rejects → omit it.
    if thread_id and thread_id != "1":
        try:
            payload["message_thread_id"] = int(thread_id)
        except ValueError:
            pass
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:  # network / decode
        return SendResult(False, error=str(exc))
    if data.get("ok"):
        return SendResult(
            True, message_id=str(data.get("result", {}).get("message_id"))
        )
    return SendResult(False, error=data.get("description") or "telegram send failed")


def _send_slack(
    token: str, chat_id: str, text: str, thread_id: Optional[str] = None
) -> SendResult:
    import httpx

    from .slack_addr import split

    # A managed-relay chat_id is team-qualified ("T…/C…"); Slack's API wants the
    # bare channel. The per-team token is selected by the caller (send_message).
    _team, chat_id = split(chat_id)
    payload: dict = {"channel": chat_id, "text": text}
    if thread_id:
        payload["thread_ts"] = thread_id
    try:
        resp = httpx.post(
            f"{_slack_api_base()}chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:
        return SendResult(False, error=str(exc))
    if data.get("ok"):
        return SendResult(True, message_id=data.get("ts"))
    err = data.get("error") or "slack send failed"
    if err == "not_in_channel":
        err = "not_in_channel — invite @OpenWorker to the channel in Slack, then retry"
    return SendResult(False, error=err)


def _feishu_api_base(base_url: str | None = None) -> str:
    return (base_url or os.environ.get("FEISHU_API_URL") or "https://open.feishu.cn").rstrip("/")


def _feishu_tenant_token(app_id: str, app_secret: str, base_url: str) -> tuple[Optional[str], Optional[str]]:
    import httpx

    key = (base_url, app_id, app_secret)
    cached = _FEISHU_TOKEN_CACHE.get(key)
    now = time.time()
    if cached and cached[1] - 120 > now:
        return cached[0], None
    try:
        resp = httpx.post(
            f"{base_url}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:
        return None, str(exc)
    if resp.status_code >= 400 or data.get("code") not in (0, None):
        return None, str(data.get("msg") or data.get("error") or f"HTTP {resp.status_code}")
    token = data.get("tenant_access_token")
    if not token:
        return None, "feishu token response missing tenant_access_token"
    expire = float(data.get("expire") or 7200)
    _FEISHU_TOKEN_CACHE[key] = (str(token), now + expire)
    return str(token), None


def _feishu_auth(token: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    try:
        creds = json.loads(token)
    except json.JSONDecodeError:
        return None, None, "invalid feishu credentials"
    app_id = str(creds.get("app_id") or "")
    app_secret = str(creds.get("app_secret") or "")
    base_url = _feishu_api_base(creds.get("base_url"))
    tenant_token, err = _feishu_tenant_token(app_id, app_secret, base_url)
    if err or not tenant_token:
        return None, None, err or "feishu token failed"
    return base_url, tenant_token, None


def _send_feishu(
    token: str, chat_id: str, text: str, thread_id: Optional[str] = None
) -> SendResult:
    """Send a Feishu/Lark rich-text message.

    The `token` argument is a JSON-packed credential bundle produced by tools._resolve_token.
    Keeping the Sender interface unchanged avoids leaking app credentials into tool schemas while
    still letting us refresh tenant_access_token per request.
    """
    import httpx

    base_url, tenant_token, err = _feishu_auth(token)
    if err or not base_url or not tenant_token:
        return SendResult(False, error=err or "feishu token failed")
    payload = {
        "receive_id": chat_id,
        "msg_type": "post",
        "content": json.dumps(_feishu_markdown_post(text), ensure_ascii=False),
    }
    if thread_id:
        payload["reply_in_thread"] = True
    try:
        resp = httpx.post(
            f"{base_url}/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {tenant_token}"},
            json=payload,
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:
        return SendResult(False, error=str(exc))
    if resp.status_code < 400 and data.get("code") == 0:
        msg = data.get("data") or {}
        return SendResult(True, message_id=msg.get("message_id"))
    return SendResult(
        False,
        error=str(data.get("msg") or data.get("error") or f"feishu send failed ({resp.status_code})"),
    )


def _feishu_markdown_post(text: str) -> dict:
    content = str(text or "").strip() or " "
    return {"zh_cn": {"content": [[{"tag": "md", "text": content}]]}}


def _send_feishu_interactive(token: str, chat_id: str, card: dict) -> SendResult:
    import httpx

    base_url, tenant_token, err = _feishu_auth(token)
    if err or not base_url or not tenant_token:
        return SendResult(False, error=err or "feishu token failed")
    try:
        resp = httpx.post(
            f"{base_url}/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {tenant_token}"},
            json={
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            },
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:
        return SendResult(False, error=str(exc))
    if resp.status_code < 400 and data.get("code") == 0:
        msg = data.get("data") or {}
        return SendResult(True, message_id=msg.get("message_id"))
    return SendResult(
        False,
        error=str(data.get("msg") or data.get("error") or f"feishu card send failed ({resp.status_code})"),
    )


def _patch_feishu_message(token: str, message_id: str, card: dict) -> SendResult:
    import httpx

    base_url, tenant_token, err = _feishu_auth(token)
    if err or not base_url or not tenant_token:
        return SendResult(False, error=err or "feishu token failed")
    try:
        resp = httpx.patch(
            f"{base_url}/open-apis/im/v1/messages/{quote(message_id, safe='')}",
            headers={"Authorization": f"Bearer {tenant_token}"},
            json={"content": json.dumps(card, ensure_ascii=False)},
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:
        return SendResult(False, error=str(exc))
    if resp.status_code < 400 and data.get("code") == 0:
        msg = data.get("data") or {}
        return SendResult(True, message_id=msg.get("message_id") or message_id)
    return SendResult(
        False,
        error=str(data.get("msg") or data.get("error") or f"feishu card patch failed ({resp.status_code})"),
    )


def _react_feishu_message(token: str, message_id: str, emoji_type: str = "THUMBSUP") -> SendResult:
    import httpx

    if not message_id:
        return SendResult(False, error="missing feishu message_id")
    base_url, tenant_token, err = _feishu_auth(token)
    if err or not base_url or not tenant_token:
        return SendResult(False, error=err or "feishu token failed")
    try:
        resp = httpx.post(
            f"{base_url}/open-apis/im/v1/messages/{quote(message_id, safe='')}/reactions",
            headers={"Authorization": f"Bearer {tenant_token}"},
            json={"reaction_type": {"emoji_type": emoji_type}},
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:
        return SendResult(False, error=str(exc))
    if resp.status_code < 400 and data.get("code") == 0:
        return SendResult(True, message_id=message_id)
    return SendResult(
        False,
        error=str(data.get("msg") or data.get("error") or f"feishu reaction failed ({resp.status_code})"),
    )


def _slack_blocks(text: str, buttons) -> list[dict]:
    """A Block Kit message: a text section + a row of action buttons (action_id `ocw_<i>`,
    value = the encoded item id + resolution)."""
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    if buttons:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": b.label[:75]},
                        "value": b.value,
                        "action_id": f"ocw_{i}",
                    }
                    for i, b in enumerate(buttons)
                ],
            }
        )
    return blocks


def _send_slack_interactive(
    token: str, chat_id: str, text: str, buttons, thread_id: Optional[str] = None
) -> SendResult:
    import httpx

    from .slack_addr import split

    _team, chat_id = split(chat_id)
    payload: dict = {
        "channel": chat_id,
        "text": text,
        "blocks": _slack_blocks(text, buttons),
    }
    if thread_id:
        payload["thread_ts"] = thread_id
    try:
        resp = httpx.post(
            f"{_slack_api_base()}chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:
        return SendResult(False, error=str(exc))
    if data.get("ok"):
        return SendResult(True, message_id=data.get("ts"))
    return SendResult(False, error=data.get("error") or "slack send failed")


DEFAULT_SENDERS: dict[str, Sender] = {
    "telegram": _send_telegram,
    "slack": _send_slack,
    "feishu": _send_feishu,
}


# -- file upload (§34 / UX-016) --------------------------------------------------------
# A FileSender is (token, chat_id, thread_id, filename, data, title, comment) -> SendResult.
FileSender = Callable[
    [str, str, Optional[str], str, bytes, Optional[str], Optional[str]], SendResult
]


def _send_slack_file(
    token: str,
    chat_id: str,
    thread_id: Optional[str],
    filename: str,
    data: bytes,
    title: Optional[str] = None,
    comment: Optional[str] = None,
) -> SendResult:
    """files_upload_v2 (the only non-deprecated path): reserve an upload URL, PUT the
    bytes, then complete into the channel/thread. Slack renders its own previews for
    pdf/csv/images — that's the whole point of sending the file instead of a thumbnail.
    """
    import httpx

    from .slack_addr import split

    _team, chat_id = split(chat_id)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = httpx.post(
            f"{_slack_api_base()}files.getUploadURLExternal",
            headers=headers,
            data={"filename": filename, "length": str(len(data))},
            timeout=_TIMEOUT,
        )
        got = resp.json()
        if not got.get("ok"):
            return SendResult(
                False, error=got.get("error") or "slack upload-url failed"
            )
        up = httpx.post(
            got["upload_url"],
            files={"file": (filename, data)},
            timeout=max(_TIMEOUT, 120.0),
        )
        if up.status_code != 200:
            return SendResult(False, error=f"slack upload failed ({up.status_code})")
        complete: dict = {
            "files": [{"id": got["file_id"], "title": title or filename}],
            "channel_id": chat_id,
        }
        if thread_id:
            complete["thread_ts"] = thread_id
        if comment:
            complete["initial_comment"] = comment
        resp = httpx.post(
            f"{_slack_api_base()}files.completeUploadExternal",
            headers=headers,
            json=complete,
            timeout=_TIMEOUT,
        )
        data_out = resp.json()
    except Exception as exc:  # network / decode
        return SendResult(False, error=str(exc))
    if data_out.get("ok"):
        return SendResult(True, message_id=got["file_id"])
    return SendResult(False, error=data_out.get("error") or "slack file send failed")


def _send_feishu_file(
    token: str,
    chat_id: str,
    thread_id: Optional[str],
    filename: str,
    data: bytes,
    title: Optional[str] = None,
    comment: Optional[str] = None,
) -> SendResult:
    import httpx

    base_url, tenant_token, err = _feishu_auth(token)
    if err or not base_url or not tenant_token:
        return SendResult(False, error=err or "feishu token failed")
    headers = {"Authorization": f"Bearer {tenant_token}"}
    upload_name = (title or filename or "file").strip() or "file"
    try:
        resp = httpx.post(
            f"{base_url}/open-apis/im/v1/files",
            headers=headers,
            data={"file_type": _feishu_file_type(upload_name), "file_name": upload_name},
            files={"file": (filename or upload_name, data)},
            timeout=max(_TIMEOUT, 120.0),
        )
        uploaded = resp.json()
        if resp.status_code >= 400 or uploaded.get("code") != 0:
            return SendResult(
                False,
                error=str(
                    uploaded.get("msg")
                    or uploaded.get("error")
                    or f"feishu file upload failed ({resp.status_code})"
                ),
            )
        file_key = ((uploaded.get("data") or {}).get("file_key") or "").strip()
        if not file_key:
            return SendResult(False, error="feishu file upload missing file_key")
        if comment:
            _send_feishu(token, chat_id, comment, thread_id)
        payload = {
            "receive_id": chat_id,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
        }
        if thread_id:
            payload["reply_in_thread"] = True
        resp = httpx.post(
            f"{base_url}/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers=headers,
            json=payload,
            timeout=_TIMEOUT,
        )
        sent = resp.json()
    except Exception as exc:
        return SendResult(False, error=str(exc))
    if resp.status_code < 400 and sent.get("code") == 0:
        msg = sent.get("data") or {}
        return SendResult(True, message_id=msg.get("message_id") or file_key)
    return SendResult(
        False,
        error=str(
            sent.get("msg")
            or sent.get("error")
            or f"feishu file send failed ({resp.status_code})"
        ),
    )


def _feishu_file_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in {"mp4"}:
        return "mp4"
    if ext in {"mp3"}:
        return "mp3"
    if ext in {"pdf"}:
        return "pdf"
    if ext in {"doc", "docx"}:
        return "doc"
    if ext in {"xls", "xlsx", "csv"}:
        return "xls"
    if ext in {"ppt", "pptx"}:
        return "ppt"
    return "stream"


def _feishu_filename_from_headers(resp, fallback: str) -> str:
    header = (
        resp.headers.get("content-disposition")
        or resp.headers.get("Content-Disposition")
        or ""
    )
    if header:
        match = re.search(r"filename\*=UTF-8''([^;]+)", header, re.I)
        if match:
            try:
                return unquote(match.group(1))
            except Exception:
                pass
        match = re.search(r'filename="?([^";]+)"?', header, re.I)
        if match:
            return match.group(1)
    return fallback


def _download_feishu_resource(
    token: str,
    message_id: str,
    file_key: str,
    resource_type: str = "file",
) -> tuple[bytes, str]:
    import httpx

    base_url, tenant_token, err = _feishu_auth(token)
    if err or not base_url or not tenant_token:
        raise RuntimeError(err or "feishu token failed")
    message_part = quote(message_id, safe="")
    key_part = quote(file_key, safe="")
    resp = httpx.get(
        f"{base_url}/open-apis/im/v1/messages/{message_part}/resources/{key_part}",
        params={"type": resource_type},
        headers={"Authorization": f"Bearer {tenant_token}"},
        timeout=max(_TIMEOUT, 120.0),
    )
    if resp.status_code >= 400:
        try:
            data = resp.json()
            msg = (
                data.get("msg")
                or data.get("error")
                or f"feishu resource download failed ({resp.status_code})"
            )
        except Exception:
            msg = f"feishu resource download failed ({resp.status_code})"
        raise RuntimeError(str(msg))
    return resp.content, _feishu_filename_from_headers(resp, file_key)


DEFAULT_FILE_SENDERS: dict[str, FileSender] = {
    "slack": _send_slack_file,
    "feishu": _send_feishu_file,
}
