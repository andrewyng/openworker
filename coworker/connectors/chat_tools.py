"""Chat connector tools — Slack and Telegram posting as ordinary connector tools.

Spec (connectors-across-machines §11, owner rulings 2026-09-05): posting to a chat
platform is a catalog tool gated by the persona's `connectors:` like every other
connector tool — a session with Slack enabled can post to Slack, and nothing else
decides it. The generic `send_message` / `send_file` pair (built for the retiring
super-agent personas) is being replaced by these; both coexist until §11.7 step 7.

Same senders underneath (`senders.py`): stateless HTTP posts, bot token read from the
SecretStore at call time, never in the model's context. The standing-rule target of
`slack_post_message` (`slack:<workspace>/<channel>[:<thread_ts>]`, see
`tool_defs._slack_thread`) is exactly the handle the inbound framing and the thread
grant use, so a session started for a thread posts back to it without asking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import aisuite as ai

from ..secrets import SecretStore
from .senders import DEFAULT_FILE_SENDERS, DEFAULT_SENDERS, FileSender, Sender
from .slack_addr import qualify
from .tools import (
    _MAX_FILE_BYTES,
    _render_html_png,
    _resolve_token,
    _resolve_within,
    _slack_channel_name_like,
)


def _meta(name: str, caps: list[str]) -> ai.ToolMetadata:
    return ai.ToolMetadata(
        name=name,
        category="connector",
        risk_level="medium",
        capabilities=caps,
        requires_approval=True,
    )


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def _attach(fn: Callable[..., Any], schema: dict[str, Any], caps: list[str]) -> Callable[..., Any]:
    name = schema["function"]["name"]
    fn.__name__ = name
    fn.__doc__ = schema["function"]["description"]
    fn.__coworker_schema__ = schema
    fn.__aisuite_tool_metadata__ = _meta(name, caps)
    return fn


# -- Slack workspace + channel resolution ------------------------------------------------


def slack_workspaces(secrets: SecretStore) -> list[str]:
    """Connected workspace ids: every managed `slack:team:<T…>` install, plus
    `default` when a manual single-workspace bot token exists."""
    from .config import _slack_team_profiles

    out = [team_id for team_id, _p in _slack_team_profiles(secrets)]
    if (secrets.get("slack:default") or {}).get("bot_token"):
        out.append("default")
    return out


def _pick_workspace(secrets: SecretStore, workspace: str) -> tuple[Optional[str], Optional[str]]:
    """(workspace, error). Empty → the only connected workspace; several → refuse
    rather than guess (spec §11.8)."""
    known = slack_workspaces(secrets)
    if not known:
        return None, "no Slack workspace is connected — connect Slack first"
    wanted = (workspace or "").strip()
    if not wanted:
        if len(known) == 1:
            return known[0], None
        return None, f"several Slack workspaces are connected ({', '.join(known)}) — pass `workspace`"
    if wanted not in known:
        return None, f"workspace {wanted!r} is not connected (connected: {', '.join(known)})"
    return wanted, None


def _resolve_channel_in(secrets: SecretStore, workspace: str, channel: str) -> tuple[Optional[str], Optional[str]]:
    """A channel id passes through; a NAME resolves within the workspace via the same
    cached roster the GUI's channel picker uses. (channel_id, error)."""
    from .slack_directory import list_channels

    raw = (channel or "").strip()
    if not raw:
        return None, "channel is required"
    if not _slack_channel_name_like(raw.lstrip("#")):
        return raw, None
    query = raw.lstrip("#").strip()
    r = list_channels(secrets, workspace, query, limit=50)
    if not r.get("ok"):
        return None, str(r.get("error") or f"could not list channels in {workspace}")
    hits = [c for c in (r.get("channels") or []) if str(c.get("name", "")).lower() == query.lower()]
    if not hits:
        return None, f"no channel named #{query} in workspace {workspace}"
    c = hits[0]
    if not c.get("is_member"):
        return None, f"found #{query}, but the bot isn't a member — invite @OpenWorker to #{query} in Slack, then retry"
    return str(c["id"]), None


def _slack_chat_id(workspace: str, channel_id: str) -> str:
    return channel_id if workspace == "default" else qualify(workspace, channel_id)


# -- tools ---------------------------------------------------------------------------------


def make_chat_tools(
    secrets: SecretStore,
    *,
    roots: Optional[list] = None,
    senders: Optional[dict[str, Sender]] = None,
    file_senders: Optional[dict[str, FileSender]] = None,
    render_html: Optional[Callable[[Path], bytes]] = None,
) -> list[Callable[..., Any]]:
    """Every chat connector's tools; `make_integration_tools` filters them down to the
    session's enabled connectors like the rest of the catalog."""
    senders = senders if senders is not None else DEFAULT_SENDERS
    file_senders = file_senders if file_senders is not None else DEFAULT_FILE_SENDERS
    render_html = render_html or _render_html_png
    bases = [Path(r.path) for r in (roots or []) if getattr(r, "path", None)]

    def slack_post_message(workspace: str = "", channel: str = "", thread_ts: str = "", text: str = "") -> dict[str, Any]:
        if not (text or "").strip():
            return {"error": "text is required"}
        ws, err = _pick_workspace(secrets, workspace)
        if err:
            return {"error": err}
        channel_id, err = _resolve_channel_in(secrets, ws, channel)
        if err:
            return {"error": err}
        chat_id = _slack_chat_id(ws, channel_id)
        token = _resolve_token(secrets, "slack", chat_id)
        if not token:
            return {"error": f"no bot token for workspace {ws} — connect it first"}
        sender = senders.get("slack")
        if sender is None:
            return {"error": "slack sending is not available"}
        from .attribution import sender_prefix

        result = sender(token, chat_id, sender_prefix(secrets, chat_id) + text, (thread_ts or "").strip() or None)
        if result.ok:
            return {
                "ok": True,
                "message_id": result.message_id,
                "workspace": ws,
                "channel": channel_id,
                "thread_ts": (thread_ts or "").strip() or None,
            }
        return {"error": result.error or "send failed"}

    def slack_upload_file(
        workspace: str = "",
        channel: str = "",
        thread_ts: str = "",
        path: str = "",
        title: str = "",
        comment: str = "",
        as_screenshot: bool = False,
    ) -> dict[str, Any]:
        ws, err = _pick_workspace(secrets, workspace)
        if err:
            return {"error": err}
        channel_id, err = _resolve_channel_in(secrets, ws, channel)
        if err:
            return {"error": err}
        chat_id = _slack_chat_id(ws, channel_id)
        if not bases:
            return {"error": "no workspace folders available to read from"}
        resolved = _resolve_within(path, bases)
        if resolved is None or not resolved.is_file():
            return {"error": "path is outside the folders this session can access (or missing)"}
        token = _resolve_token(secrets, "slack", chat_id)
        if not token:
            return {"error": f"no bot token for workspace {ws} — connect it first"}
        sender = file_senders.get("slack")
        if sender is None:
            return {"error": "slack file upload is not available"}
        if as_screenshot:
            if resolved.suffix.lower() not in (".html", ".htm"):
                return {"error": "as_screenshot only applies to .html files"}
            try:
                data = render_html(resolved)
            except Exception as exc:  # noqa: BLE001 — surfaced to the model as a tool error
                return {"error": f"could not render the page: {exc}"}
            filename = resolved.stem + ".png"
        else:
            if resolved.stat().st_size > _MAX_FILE_BYTES:
                return {"error": "file is larger than 50 MB"}
            data = resolved.read_bytes()
            filename = resolved.name
        note = comment or ""
        if note:
            from .attribution import sender_prefix

            note = sender_prefix(secrets, chat_id) + note
        result = sender(token, chat_id, (thread_ts or "").strip() or None, filename, data, title or None, note or None)
        if result.ok:
            return {"ok": True, "file_id": result.message_id, "workspace": ws, "channel": channel_id, "filename": filename}
        return {"error": result.error or "file send failed"}

    def telegram_send_message(chat_id: str = "", text: str = "", thread_id: str = "") -> dict[str, Any]:
        chat = (chat_id or "").strip()
        if not chat:
            return {"error": "chat_id is required"}
        if not (text or "").strip():
            return {"error": "text is required"}
        token = _resolve_token(secrets, "telegram", chat)
        if not token:
            return {"error": "no bot token for telegram — connect it first"}
        sender = senders.get("telegram")
        if sender is None:
            return {"error": "telegram sending is not available"}
        result = sender(token, chat, text, (thread_id or "").strip() or None)
        if result.ok:
            return {"ok": True, "message_id": result.message_id, "chat_id": chat}
        return {"error": result.error or "send failed"}

    workspace_prop = {
        "type": "string",
        "description": "Workspace id (T…) from the message you are answering, or 'default' for a manually connected bot. May be omitted when exactly one workspace is connected.",
    }
    channel_prop = {
        "type": "string",
        "description": "Channel id (C…/D…/G…) from the message you are answering, or a channel name like '#general' (resolved within the workspace).",
    }
    thread_prop = {
        "type": "string",
        "description": "Thread timestamp to reply in (the `thread_ts` of the message you are answering). Omit to post to the channel itself.",
    }
    return [
        _attach(
            slack_post_message,
            _schema(
                "slack_post_message",
                "Post a message to a Slack channel or thread. To answer a message you received, pass the workspace, channel and thread_ts it came with — that reply is pre-approved for the thread the session was started for. Plain assistant text is not delivered anywhere.",
                {"workspace": workspace_prop, "channel": channel_prop, "thread_ts": thread_prop, "text": {"type": "string", "description": "The message text."}},
                ["channel", "text"],
            ),
            ["messaging"],
        ),
        _attach(
            slack_upload_file,
            _schema(
                "slack_upload_file",
                "Upload a file from the session's folders into a Slack channel or thread. Slack previews pdf/csv/images itself — send the actual file. For .html set as_screenshot=true to send a rendered PNG. Uploads always ask for approval, even in a thread where text replies are pre-approved.",
                {
                    "workspace": workspace_prop,
                    "channel": channel_prop,
                    "thread_ts": thread_prop,
                    "path": {"type": "string", "description": "The file to send — workspace-relative, or absolute within an allowed folder."},
                    "title": {"type": "string", "description": "Display title (defaults to the filename)."},
                    "comment": {"type": "string", "description": "Short message posted with the file."},
                    "as_screenshot": {"type": "boolean", "description": "HTML only: render headless and send a PNG preview instead of the raw file."},
                },
                ["channel", "path"],
            ),
            ["messaging", "files"],
        ),
        _attach(
            telegram_send_message,
            _schema(
                "telegram_send_message",
                "Send a message to a Telegram chat. To answer a message you received, pass the chat_id it came with — that reply is pre-approved for the chat the session was started for.",
                {
                    "chat_id": {"type": "string", "description": "The chat id from the message you are answering."},
                    "text": {"type": "string", "description": "The message text."},
                    "thread_id": {"type": "string", "description": "Forum topic id, when answering inside a topic."},
                },
                ["chat_id", "text"],
            ),
            ["messaging"],
        ),
    ]
