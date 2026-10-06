"""Slash commands for a bound chat — so the bot is somewhere you can ask things,
not just somewhere prompts arrive.

The rule this module exists to enforce: **an inbound message is never met with
silence.** A prompt mirrored to a phone is only half a conversation if the person
holding the phone can't ask what is waiting, can't tell whether their reply landed,
and gets nothing back when they type something the parser doesn't understand.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

HELP = """Commands:
/pending - what is waiting on you, and how to answer it
/status - workers, routines, and anything stuck
/runs - how the last few scheduled runs went
/help - this message

To answer a prompt, reply with its tag:
approve [ow:...] - just this once
always [ow:...] - and stop asking for that routine
deny [ow:...] - refuse it"""


@dataclass
class PendingItem:
    id: str
    title: str
    detail: str = ""
    worker: str = ""
    routine: str = ""
    is_approval: bool = True


@dataclass
class RunLine:
    routine: str
    when: str
    status: str


@dataclass
class ChatContext:
    """What the commands can see. A narrow view, passed in, so this module stays
    testable without a server and can't reach anything it wasn't handed."""

    pending: Callable[[], list[PendingItem]]
    workers: Callable[[], list[str]]
    routines: Callable[[], list[str]]
    runs: Callable[[], list[RunLine]]


def _pending_block(items: list[PendingItem]) -> str:
    if not items:
        return "Nothing is waiting on you."
    lines = [f"{len(items)} waiting:" if len(items) > 1 else "1 waiting:"]
    for it in items:
        who = it.worker or "a worker"
        where = f" · {it.routine}" if it.routine else ""
        lines.append(f"\n{who}{where}\n{it.title}")
        if it.detail:
            lines.append(it.detail)
        # The exact strings to send — never make someone assemble a reply.
        if it.is_approval:
            lines.append(f"approve [ow:{it.id}]   ·   always [ow:{it.id}]   ·   deny [ow:{it.id}]")
        else:
            lines.append(f"Reply with your answer plus [ow:{it.id}]")
    return "\n".join(lines)


def handle_command(text: str, ctx: ChatContext) -> Optional[str]:
    """The reply for a slash command, or None if this isn't one.

    Tolerant on purpose: a leading slash is optional and a @botname suffix is
    stripped, because that is what people actually type.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    word = raw.split()[0].lower().lstrip("/").split("@")[0]

    if word in {"help", "commands", "start"}:
        return HELP

    if word in {"pending", "waiting", "inbox", "queue"}:
        return _pending_block(ctx.pending())

    if word == "status":
        workers, routines = ctx.workers(), ctx.routines()
        pending = ctx.pending()
        lines = [
            f"{len(workers)} workers: {', '.join(workers)}" if workers else "No workers yet.",
        ]
        lines += routines if routines else ["No routines."]
        lines.append("")
        lines.append(_pending_block(pending))
        return "\n".join(lines)

    if word in {"runs", "history"}:
        runs = ctx.runs()
        if not runs:
            return "No scheduled runs recorded yet."
        return "\n".join(f"{r.routine} · {r.when} · {r.status}" for r in runs)

    return None


def unmatched_reply(text: str, ctx: ChatContext) -> str:
    """What to say when a message matched no command and no waiting prompt.

    Silence was the old behaviour: the message was filed as unrouted and the sender
    told nothing, which is indistinguishable from the bot being dead.
    """
    items = ctx.pending()
    if items:
        return (
            "I couldn't match that to anything. To answer what's waiting, send one of "
            "these lines (the tag is how I know which prompt you mean):\n\n"
            + _pending_block(items)
        )
    return f"I couldn't match that to anything, and nothing is waiting on you.\n\n{HELP}"
