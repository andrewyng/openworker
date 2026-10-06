"""The bot answers. Every time.

An inbound message used to have three fates: it matched a prompt tag, it was handed
to a designated session, or it was filed as unrouted and the sender told nothing.
That last one is most of them — and it is what a person meets when they type
"approve" without a tag, or "help", or ask what is waiting.
"""

from __future__ import annotations

from coworker.connectors.commands import (
    ChatContext,
    HELP,
    PendingItem,
    RunLine,
    handle_command,
    unmatched_reply,
)


def _ctx(pending=(), workers=("The Watcher",), routines=("Daily watch · 7am · ok",), runs=()):
    return ChatContext(
        pending=lambda: list(pending),
        workers=lambda: list(workers),
        routines=lambda: list(routines),
        runs=lambda: list(runs),
    )


def _item(**kw):
    base = dict(id="abc123", title="Run `web_search`?", detail="query: anything",
                worker="The Watcher", routine="Daily watch")
    base.update(kw)
    return PendingItem(**base)


def test_help_lists_the_commands_and_the_answer_lines():
    reply = handle_command("/help", _ctx())
    assert "/pending" in reply and "/status" in reply
    assert "approve [ow:...]" in reply and "always [ow:...]" in reply


def test_slash_is_optional_and_botname_suffix_is_ignored():
    assert handle_command("help", _ctx()) == HELP
    assert handle_command("/help@crew_bot", _ctx()) == HELP


def test_pending_names_the_worker_and_gives_the_exact_reply_lines():
    reply = handle_command("/pending", _ctx(pending=[_item()]))
    assert "The Watcher" in reply and "Daily watch" in reply
    # The three answers, ready to copy — nobody should have to assemble one.
    assert "approve [ow:abc123]" in reply
    assert "always [ow:abc123]" in reply
    assert "deny [ow:abc123]" in reply


def test_pending_says_so_when_nothing_waits():
    assert "Nothing is waiting" in handle_command("/pending", _ctx())


def test_a_question_is_not_offered_approve_or_deny():
    reply = handle_command("/pending", _ctx(pending=[_item(is_approval=False, title="Which one?")]))
    assert "approve [ow:" not in reply
    assert "[ow:abc123]" in reply


def test_status_covers_workers_routines_and_what_is_waiting():
    reply = handle_command("/status", _ctx(pending=[_item()]))
    assert "The Watcher" in reply and "Daily watch" in reply and "1 waiting" in reply


def test_runs_reports_recent_outcomes():
    reply = handle_command("/runs", _ctx(runs=[RunLine("Daily watch", "Mon 07:00", "ok")]))
    assert "Daily watch" in reply and "ok" in reply


def test_ordinary_text_is_not_a_command():
    assert handle_command("what is happening", _ctx()) is None


def test_unmatched_text_offers_the_waiting_prompt():
    reply = unmatched_reply("approve", _ctx(pending=[_item()]))
    assert "couldn't match" in reply
    assert "approve [ow:abc123]" in reply


def test_unmatched_text_with_an_empty_queue_falls_back_to_help():
    reply = unmatched_reply("approve", _ctx())
    assert "nothing is waiting" in reply.lower()
    assert "/pending" in reply
