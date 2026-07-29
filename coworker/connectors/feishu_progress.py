"""Feishu inbound progress reporting.

For Feishu-originated turns, mirror the local TurnEngine lifecycle back to Feishu:
react to the user's original message, maintain one progress card, then make sure a
final answer reaches the chat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from ..events import Event, EventType
from ..secrets import SecretStore
from .senders import (
    _patch_feishu_message,
    _react_feishu_message,
    _send_feishu,
    _send_feishu_interactive,
)

logger = logging.getLogger("coworker.connectors")


@dataclass
class _ToolEntry:
    name: str
    status: str = "running"
    detail: str = ""


@dataclass
class _ProgressState:
    card_message_id: str = ""
    phase: str = "running"
    percent: int = 0
    message: str = ""
    current_item: str = ""
    last_sent_at: float = 0.0
    last_render_hash: str = ""
    final_sent: bool = False
    last_assistant_text: str = ""
    tools: list[_ToolEntry] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.tools is None:
            self.tools = []


class FeishuRunProgressReporter:
    _MIN_PATCH_INTERVAL = 0.8
    _FORCE_PATCH_INTERVAL = 2.0
    _PERCENT_STEP = 5

    def __init__(
        self,
        *,
        secrets: SecretStore,
        source: dict[str, Any],
        session_id: str,
    ) -> None:
        self.source = source or {}
        self.session_id = session_id
        self.chat_id = str(self.source.get("channel_id") or "")
        self.original_message_id = str(self.source.get("message_id") or "")
        self.token = _feishu_token_from_secrets(secrets)
        self.state = _ProgressState()

    @classmethod
    def for_source(
        cls, *, secrets: SecretStore, source: Optional[dict[str, Any]], session_id: str
    ) -> Optional["FeishuRunProgressReporter"]:
        if not isinstance(source, dict):
            return None
        if source.get("connector") != "feishu":
            return None
        if not source.get("channel_id"):
            return None
        reporter = cls(secrets=secrets, source=source, session_id=session_id)
        return reporter if reporter.token else None

    async def ack_only(self) -> None:
        await self._react()

    async def start(self) -> None:
        await self._react()
        self.state.phase = "running"
        self.state.percent = 5
        self.state.message = "已收到，正在处理"
        self.state.current_item = ""
        card = self._build_card()
        result = await asyncio.to_thread(
            _send_feishu_interactive, self.token, self.chat_id, card
        )
        if result.ok and result.message_id:
            self.state.card_message_id = str(result.message_id)
            self._remember(card)
        else:
            logger.debug("feishu progress card create failed: %s", result.error)

    async def on_event(self, event: Event) -> None:
        if event.type is EventType.ASSISTANT_MESSAGE:
            text = str((event.data or {}).get("text") or "").strip()
            if text:
                self.state.last_assistant_text = text
            self._update("running", 25, "正在整理回复", "")
        elif event.type is EventType.TOOL_PROPOSED:
            name = str((event.data or {}).get("name") or "tool")
            self._record_tool(name, "pending", _brief_args((event.data or {}).get("arguments")))
            self._update("running", 35, f"准备执行：{name}", name)
        elif event.type is EventType.PERMISSION_REQUIRED:
            name = str((event.data or {}).get("name") or "tool")
            self._record_tool(name, "waiting", "等待用户授权")
            self._update("running", 40, f"等待授权：{name}", name)
        elif event.type is EventType.TOOL_STARTED:
            name = str((event.data or {}).get("name") or "tool")
            self._record_tool(name, "running", "执行中")
            self._update("running", 55, f"正在执行：{name}", name)
        elif event.type is EventType.TOOL_FINISHED:
            name = str((event.data or {}).get("name") or "tool")
            status = str((event.data or {}).get("status") or "")
            if name == "send_message" and status == "ok":
                self.state.final_sent = True
            percent = 78 if status == "ok" else 65
            msg = f"已完成：{name}" if status == "ok" else f"执行异常：{name}"
            detail = str((event.data or {}).get("result_preview") or (event.data or {}).get("reason") or "")
            self._record_tool(
                name,
                "ok" if status == "ok" else "error",
                _format_tool_detail(name, status, detail),
            )
            self._update("running" if status == "ok" else "failed", percent, msg, name)
        elif event.type is EventType.ITERATION_END:
            self._update("running", max(self.state.percent, 85), "继续处理后续步骤", "")
        elif event.type is EventType.ERROR:
            reason = str((event.data or {}).get("error") or "unknown error")
            await self.finish("failed", f"执行失败：{reason}")
            return
        elif event.type is EventType.INTERRUPTED:
            await self.finish("failed", "执行已中断")
            return
        elif event.type is EventType.TURN_END:
            await self._send_fallback_final()
            await self.finish("success", "已回复用户" if self.state.final_sent else "任务完成")
            return
        else:
            return
        await self._patch_if_needed()

    async def finish(self, phase: str, message: str) -> None:
        self._update(phase, 100 if phase == "success" else self.state.percent, message, "")
        await self._patch(force=True)

    async def fail_from_exception(self, exc: BaseException) -> None:
        await self.finish("failed", f"执行异常：{exc}")

    async def _react(self) -> None:
        if not self.original_message_id:
            return
        result = await asyncio.to_thread(
            _react_feishu_message, self.token, self.original_message_id, "THUMBSUP"
        )
        if not result.ok:
            logger.debug("feishu message reaction failed: %s", result.error)

    async def _send_fallback_final(self) -> None:
        if self.state.final_sent:
            return
        text = self.state.last_assistant_text.strip()
        if not text:
            return
        result = await asyncio.to_thread(_send_feishu, self.token, self.chat_id, text, None)
        if result.ok:
            self.state.final_sent = True
        else:
            logger.debug("feishu fallback final send failed: %s", result.error)

    def _update(self, phase: str, percent: int, message: str, current_item: str) -> None:
        self.state.phase = phase
        self.state.percent = max(self.state.percent, max(0, min(100, int(percent))))
        self.state.message = " ".join(str(message or "").split()) or self.state.message
        self.state.current_item = " ".join(str(current_item or "").split())

    def _record_tool(self, name: str, status: str, detail: str = "") -> None:
        clean_name = " ".join(str(name or "tool").split()) or "tool"
        detail = " ".join(str(detail or "").split())
        terminal = {"ok", "error", "denied", "interrupted"}
        for entry in reversed(self.state.tools):
            if entry.name == clean_name and entry.status not in terminal:
                entry.status = status
                if detail:
                    entry.detail = detail
                return
        self.state.tools.append(_ToolEntry(clean_name, status, detail))

    async def _patch_if_needed(self) -> None:
        if not self.state.card_message_id:
            return
        card = self._build_card()
        render_hash = json.dumps(card, ensure_ascii=False, sort_keys=True)
        now = time.monotonic()
        if render_hash == self.state.last_render_hash:
            return
        elapsed = now - self.state.last_sent_at
        if self.state.phase in {"success", "failed"}:
            await self._patch(force=True, card=card)
            return
        previous_percent = self._percent_from_hash_anchor()
        if self.state.percent - previous_percent >= self._PERCENT_STEP:
            await self._patch(force=True, card=card)
            return
        if elapsed >= self._FORCE_PATCH_INTERVAL:
            await self._patch(force=True, card=card)
            return
        if elapsed >= self._MIN_PATCH_INTERVAL:
            await self._patch(force=True, card=card)

    async def _patch(self, *, force: bool = False, card: Optional[dict[str, Any]] = None) -> None:
        if not self.state.card_message_id:
            return
        card = card or self._build_card()
        render_hash = json.dumps(card, ensure_ascii=False, sort_keys=True)
        if not force and render_hash == self.state.last_render_hash:
            return
        result = await asyncio.to_thread(
            _patch_feishu_message, self.token, self.state.card_message_id, card
        )
        if result.ok:
            self._remember(card)
        else:
            logger.debug("feishu progress card patch failed: %s", result.error)

    def _remember(self, card: dict[str, Any]) -> None:
        self.state.last_sent_at = time.monotonic()
        self.state.last_render_hash = json.dumps(card, ensure_ascii=False, sort_keys=True)

    def _percent_from_hash_anchor(self) -> int:
        # Kept simple: throttling also compares render hash and elapsed time. Returning the last
        # visible percent is unnecessary for correctness because percent is monotonic in state.
        return max(0, self.state.percent - self._PERCENT_STEP)

    def _build_card(self) -> dict[str, Any]:
        status = "success" if self.state.phase == "success" else "failed" if self.state.phase == "failed" else "running"
        title = {
            "running": "执行中",
            "success": "已完成",
            "failed": "执行失败",
        }[status]
        template = {"running": "blue", "success": "green", "failed": "red"}[status]
        elements: list[dict[str, Any]] = [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**当前状态**：{_status_text(status, self.state.message)}"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": "**工具调用**"}},
        ]
        if self.state.tools:
            for entry in self.state.tools[-8:]:
                elements.append(
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": _tool_entry_text(entry),
                        },
                    }
                )
        else:
            elements.append(
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": "运行中 暂无工具调用"},
                }
            )
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": f"run {self.session_id[:12]} · request {self.original_message_id or '-'}",
                        }
                    ],
                },
            ]
        )
        return {
            "config": {"wide_screen_mode": True},
            "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
            "elements": elements,
        }


def _feishu_token_from_secrets(secrets: SecretStore) -> Optional[str]:
    creds = secrets.get("feishu:default") or {}
    if not (creds.get("app_id") and creds.get("app_secret")):
        return None
    return json.dumps(
        {
            "app_id": creds["app_id"],
            "app_secret": creds["app_secret"],
            "base_url": creds.get("base_url") or "",
        }
    )


def _brief_args(value: Any) -> str:
    if not value:
        return ""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        text = str(value)
    return _truncate(text, 180)


def _truncate(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _status_text(status: str, message: str) -> str:
    if status == "success":
        return "已完成"
    if status == "failed":
        return _truncate(message or "执行失败", 160)
    return _truncate(message or "执行中", 160)


def _tool_entry_text(entry: _ToolEntry) -> str:
    label = {
        "pending": "准备",
        "waiting": "等待",
        "running": "运行中",
        "ok": "完成",
        "error": "失败",
        "denied": "拒绝",
        "interrupted": "中断",
    }.get(entry.status, entry.status or "运行中")
    text = f"{label} **{entry.name}**"
    if entry.detail:
        text += f"\n{_truncate(entry.detail, 260)}"
    return text


def _format_tool_detail(name: str, status: str, preview: str) -> str:
    if status != "ok":
        return _human_preview(preview) or "执行失败"
    data = _maybe_json(preview)
    if name == "send_message":
        if isinstance(data, dict) and data.get("ok"):
            return "已发送飞书回复"
        return "消息已发送"
    if name == "todo_write":
        if isinstance(data, dict):
            count = data.get("count")
            todos = data.get("todos")
            if isinstance(todos, list):
                status_counts: dict[str, int] = {}
                for todo in todos:
                    if not isinstance(todo, dict):
                        continue
                    key = str(todo.get("status") or "pending")
                    status_counts[key] = status_counts.get(key, 0) + 1
                parts = []
                if "done" in status_counts:
                    parts.append(f"{status_counts['done']} 项完成")
                if "in_progress" in status_counts:
                    parts.append(f"{status_counts['in_progress']} 项进行中")
                if "pending" in status_counts:
                    parts.append(f"{status_counts['pending']} 项待处理")
                total = count if isinstance(count, int) else len(todos)
                suffix = "，".join(parts) if parts else "已更新"
                return f"已更新待办：共 {total} 项，{suffix}"
            if isinstance(count, int):
                return f"已更新待办：共 {count} 项"
        return "已更新待办"
    return _human_preview(preview)


def _human_preview(preview: str) -> str:
    data = _maybe_json(preview)
    if isinstance(data, dict):
        if "error" in data:
            return _truncate(str(data.get("error") or ""), 260)
        keys = [k for k in data.keys() if not str(k).startswith("_")]
        if not keys:
            return ""
        compact = {k: data[k] for k in keys[:4] if k not in {"message_id", "target"}}
        if not compact:
            return "完成"
        return _truncate(json.dumps(compact, ensure_ascii=False, sort_keys=True), 260)
    if isinstance(data, list):
        return _truncate(f"{len(data)} 项结果", 260)
    return _truncate(preview, 260)


def _maybe_json(text: str) -> Any:
    text = str(text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None
