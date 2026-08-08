"""A chat reply must resolve an Inbox item the same way the in-app surfaces do.

Directory and plan prompts carry their answer as a JSON payload, but a chat reply only ever
produces the bare words `allow` / `deny`. Those used to be stored verbatim, parse to `{}`, and
read as a refusal — so "approve" consumed the prompt and told the agent it was rejected.
"""

from __future__ import annotations

from coworker.connectors.base import MessageEvent, SessionSource
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server.manager import (
    SessionManager,
    _parse_inbox_json,
    _reply_answers,
)


class NoTurnsProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no model turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path) -> SessionManager:
    return SessionManager(data_dir=tmp_path / "data", provider=NoTurnsProvider())


def _reply(item_id: str, text: str = "approve") -> MessageEvent:
    return MessageEvent(
        text=f"{text} [ow:{item_id}]",
        source=SessionSource("telegram", "C1", user_id="U1"),
    )


async def test_plan_reply_is_not_consumed_into_a_silent_rejection(tmp_path):
    manager = _manager(tmp_path)
    item = manager.inbox.add_plan("s1", "Approve the plan?", body="step 1")

    assert await manager._resolve_inbox_reply(_reply(item.id)) is True

    # Left pending, so it can still be answered in the app — never stored as a bare "allow"
    # that `_parse_inbox_json` would hand the plan approver as `{}` (i.e. "rejected").
    assert manager.inbox.get(item.id).state == "pending"
    assert not _parse_inbox_json(manager.inbox.get(item.id).resolution or "").get(
        "approved"
    )
    assert manager.inbox.get(item.id).resolution != "allow"


async def test_directory_reply_is_not_consumed_into_a_silent_refusal(tmp_path):
    manager = _manager(tmp_path)
    item = manager.inbox.add_directory(
        "s1",
        "Grant access to a folder?",
        body="to read the notes",
        data={"path": "/home/u/notes", "writable": False},
    )

    assert await manager._resolve_inbox_reply(_reply(item.id)) is True
    assert manager.inbox.get(item.id).state == "pending"
    assert manager.inbox.get(item.id).resolution != "allow"


async def test_structured_payload_reply_still_resolves(tmp_path):
    """The JSON payload the in-app surfaces send is still accepted, from any surface."""
    manager = _manager(tmp_path)
    item = manager.inbox.add_directory(
        "s1", "Grant access to a folder?", data={"path": "/home/u/docs"}
    )

    payload = '{"granted": true}'
    assert await manager._resolve_inbox_reply(_reply(item.id, payload)) is True

    resolved = manager.inbox.get(item.id)
    assert resolved.state == "resolved"
    assert _parse_inbox_json(resolved.resolution).get("granted") is True


def test_reply_answers_gate_is_kind_scoped():
    """The unit behind the fix: only directory/plan demand a structured payload."""

    class _Item:
        def __init__(self, kind):
            self.kind = kind

    assert _reply_answers(_Item("approval"), "allow") is True
    assert _reply_answers(_Item("question"), "us-east-1") is True
    assert _reply_answers(_Item("notification"), "allow") is True

    assert _reply_answers(_Item("plan"), "allow") is False
    assert _reply_answers(_Item("plan"), "deny") is False
    assert _reply_answers(_Item("directory"), "allow") is False
    assert _reply_answers(_Item("plan"), '{"approved": true}') is True
    assert _reply_answers(_Item("directory"), '{"granted": true}') is True


async def test_approval_and_question_replies_are_unchanged(tmp_path):
    manager = _manager(tmp_path)
    approval = manager.inbox.add_approval("s1", "Run `ls`?")
    question = manager.inbox.add_question("s1", "Which region?")

    assert await manager._resolve_inbox_reply(_reply(approval.id)) is True
    assert manager.inbox.get(approval.id).resolution == "allow"

    assert await manager._resolve_inbox_reply(_reply(question.id, "us-east-1")) is True
    assert manager.inbox.get(question.id).resolution == "us-east-1"


async def test_non_reply_message_is_not_consumed(tmp_path):
    manager = _manager(tmp_path)
    event = MessageEvent(
        text="just chatting", source=SessionSource("telegram", "C1", user_id="U1")
    )
    assert await manager._resolve_inbox_reply(event) is False


async def test_reply_durably_resumes_a_session_that_is_not_running(tmp_path):
    """A reply is a resolution surface like the button — it must rebuild an evicted engine."""
    manager = _manager(tmp_path)
    item = manager.inbox.add_approval("s1", "Run `ls`?", tool_call_id="call-1")

    resumed: list[str] = []

    async def _spy(resolved_item) -> None:
        resumed.append(resolved_item.id)

    manager._durable_resume = _spy  # type: ignore[method-assign]

    assert await manager._resolve_inbox_reply(_reply(item.id)) is True
    assert manager.inbox.get(item.id).resolution == "allow"
    assert resumed == [item.id], "the reply path skipped durable resume"
