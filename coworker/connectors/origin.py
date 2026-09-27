"""The origin block — where an inbound message came from and how to answer it.

Spec (connectors-across-machines §11.3, 2026-09-05): every message a box delivers
to a session (mention spawn, subscription delivery, tag, configured event) opens
with one structured block built from the inbound source: platform, workspace,
channel, thread, sender, and the EXACT reply call — named by the platform's own
connector tool, marked pre-approved when the session holds the thread grant. The
opaque `platform:chat_id[:thread]` handle stays internal (thread map, grants) and
no longer appears in model-facing text.
"""

from __future__ import annotations

from typing import Any, Optional

from .base import SessionSource
from .slack_addr import split

PLATFORM_LABELS = {
    "slack": "Slack",
    "github": "GitHub",
    "telegram": "Telegram",
    "discord": "Discord",
    "teams": "Teams",
}


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, (platform or "chat").capitalize())


def _q(value: Optional[str]) -> str:
    return '"' + str(value or "").replace('"', '\\"') + '"'


def _github_parts(chat_id: str) -> tuple[str, str, str]:
    """`owner/repo#N` → (owner, repo, N); a bare repo has no number."""
    repo_part, _, number = (chat_id or "").partition("#")
    owner, _, repo = repo_part.partition("/")
    return owner, repo, number


def reply_call(src: SessionSource, thread: Optional[str] = None, frame: Optional[dict[str, Any]] = None) -> str:
    """The tool call that answers this message, spelled out with its arguments."""
    p = src.platform
    if p == "slack":
        team, channel = split(src.chat_id or "")
        team = src.team_id or team
        args = [f"workspace={_q(team or 'default')}", f"channel={_q(channel)}"]
        if thread:
            args.append(f"thread_ts={_q(thread)}")
        return f"slack_post_message({', '.join(args)}, text=…)"
    if p == "telegram":
        args = [f"chat_id={_q(src.chat_id)}"]
        if thread and thread != "1":
            args.append(f"thread_id={_q(thread)}")
        return f"telegram_send_message({', '.join(args)}, text=…)"
    if p == "github":
        owner, repo, number = _github_parts(src.chat_id or "")
        return f"github_reply(owner={_q(owner)}, repo={_q(repo)}, number={number or '…'}, body=…)"
    # Platforms without a connector tool keep the generic reply for now.
    target = f"{p}:{src.chat_id}" + (f":{thread}" if thread else "")
    return f"send_message(target={_q(target)}, text=…)"


def also_call(src: SessionSource, frame: Optional[dict[str, Any]] = None) -> Optional[str]:
    """A second tool the same grant covers: reviewing, on a GitHub pull request."""
    if src.platform != "github":
        return None
    f = frame or {}
    if not (f.get("is_pr") or str(f.get("kind") or "").startswith("pr_")):
        return None
    owner, repo, number = _github_parts(src.chat_id or "")
    return f"github_review(owner={_q(owner)}, repo={_q(repo)}, pull_number={number or '…'}, event=…, body=…)"


def where_line(src: SessionSource, thread: Optional[str] = None, frame: Optional[dict[str, Any]] = None) -> str:
    """`From Slack · workspace T… · #team (C…) · thread 1788….` — one line, ids kept
    so the model can copy them into the reply call."""
    p = src.platform
    label = platform_label(p)
    if p == "slack":
        team, channel = split(src.chat_id or "")
        team = src.team_id or team
        parts = [f"From {label}", f"workspace {team or 'default'}"]
        if src.chat_type == "dm":
            parts.append(f"DM ({channel})")
        else:
            name = src.chat_name or ""
            parts.append(f"#{name.lstrip('#')} ({channel})" if name and name != channel else f"channel {channel}")
        if thread:
            parts.append(f"thread {thread}")
        return " · ".join(parts)
    if p == "telegram":
        parts = [f"From {label}", f"chat {src.chat_id}" + (f" ({src.chat_name})" if src.chat_name and src.chat_name != src.chat_id else "")]
        if thread and thread != "1":
            parts.append(f"topic {thread}")
        return " · ".join(parts)
    if p == "github":
        owner, repo, number = _github_parts(src.chat_id or "")
        f = frame or {}
        noun = "pull request" if (f.get("is_pr") or str(f.get("kind") or "").startswith("pr_")) else "issue"
        parts = [f"From {label}", f"{owner}/{repo}"]
        if number:
            parts.append(f"{noun} #{number}" + (f' "{f.get("title")}"' if f.get("title") else ""))
        return " · ".join(parts)
    where = src.chat_name or src.chat_id or ""
    return f"From {label} · {where}" + (f" · thread {thread}" if thread else "")


def origin_block(
    src: SessionSource,
    *,
    thread: Optional[str] = None,
    frame: Optional[dict[str, Any]] = None,
    pre_approved: bool = True,
    judgement_only: bool = False,
) -> str:
    """The block that prefixes a delivered message. `pre_approved`: the session holds
    the thread grant (every spawn/tag/configured delivery). `judgement_only`: an
    untagged channel message — answering is optional and asks for approval."""
    who = src.user_name or src.user_id or "?"
    who_line = f"Sent by {who}" + (f" ({src.user_id})" if src.user_id and src.user_id != who else "")
    call = reply_call(src, thread, frame)
    also = also_call(src, frame)
    if judgement_only:
        answer = f"To answer (only if it clearly concerns your job): {call} — this asks for approval."
    elif pre_approved:
        answer = f"To answer: {call} — pre-approved for this thread" + (f"; {also} likewise." if also else ".")
    else:
        answer = f"To answer: {call} — this asks for approval."
    return "\n".join([where_line(src, thread, frame), who_line, answer])
