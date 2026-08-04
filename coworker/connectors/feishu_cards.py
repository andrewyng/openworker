"""Feishu/Lark interactive card rendering for Inbox prompts."""

from __future__ import annotations

from typing import Any


def _truncate(text: str, limit: int = 900) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def prompt_card(text: str, buttons) -> dict[str, Any]:
    """Render a compact Feishu card for a mirrored Inbox item."""
    actions: list[dict[str, Any]] = []
    for i, button in enumerate(buttons or []):
        label = str(getattr(button, "label", "") or "")[:40]
        value = str(getattr(button, "value", "") or "")
        kind = "danger" if label.lower() in {"deny", "reject", "no"} else "primary"
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": _local_label(label)},
                "type": kind,
                "value": {"ocw_value": value},
                "name": f"ocw_{i}",
            }
        )

    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": _truncate(text)},
        }
    ]
    if actions:
        elements.append({"tag": "action", "actions": actions})

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "yellow",
            "title": {"tag": "plain_text", "content": "需要审批"},
        },
        "elements": elements,
    }


def resolved_card(text: str) -> dict[str, Any]:
    """Render the post-click state for a Feishu prompt card."""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": "已处理"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": _truncate(text)},
            }
        ],
    }


def submitted_card(text: str) -> dict[str, Any]:
    """Immediate card replacement returned from a Feishu button callback."""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "已提交"},
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": _truncate(text)},
            }
        ],
    }


def _local_label(label: str) -> str:
    lowered = label.lower()
    if lowered == "approve":
        return "批准"
    if lowered == "deny":
        return "拒绝"
    return label
