"""A mirrored prompt must be answerable from the surface it arrived on.

`buttons_for` returns buttons for approvals and option questions, but only an adapter
that implements `send_interactive` can draw them — the base class quietly sends plain
text instead. Mirroring on the presence of buttons alone therefore produced, on every
adapter but Slack, a question with no buttons, no `[ow:id]` tag for the reply parser to
correlate against, and no instructions: an agent suspended on that prompt waits forever.
"""

from __future__ import annotations

import asyncio

from coworker.inbox import KIND_APPROVAL
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server.manager import SessionManager


class NoTurnsProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no model turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


class GatewayStub:
    """Stands in for a platform pair: one that draws buttons, one that cannot."""

    def __init__(self, interactive: bool) -> None:
        self._interactive = interactive
        self.texts: list[str] = []
        self.interactive_sends: list[tuple] = []

    def supports_interactive(self, target: str) -> bool:
        return self._interactive

    async def deliver(self, target, text):
        self.texts.append(text)

    async def deliver_interactive(self, target, text, buttons):
        self.interactive_sends.append((target, text, buttons))


def _manager(tmp_path) -> SessionManager:
    manager = SessionManager(data_dir=tmp_path / "data", provider=NoTurnsProvider())
    manager.inbox_routing.set_binding("default", channel="telegram", target="12345")
    return manager


def test_approval_without_buttons_carries_tag_and_instructions(tmp_path):
    manager = _manager(tmp_path)
    manager.gateway = GatewayStub(interactive=False)
    item = manager.inbox.add_approval("s1", "Run `web_fetch`?", body="url: https://example.com")

    asyncio.run(manager.mirror_inbox_item(item))

    assert manager.gateway.interactive_sends == []
    (text,) = manager.gateway.texts
    assert f"[ow:{item.id}]" in text, "the reply parser correlates on this tag"
    assert "approve" in text.lower() and "deny" in text.lower()
    assert item.kind == KIND_APPROVAL


def test_option_question_without_buttons_names_the_options(tmp_path):
    manager = _manager(tmp_path)
    manager.gateway = GatewayStub(interactive=False)
    item = manager.inbox.add_question("s1", "Which one?", options=["Blue", "Green"])

    asyncio.run(manager.mirror_inbox_item(item))

    (text,) = manager.gateway.texts
    assert f"[ow:{item.id}]" in text
    assert "Blue" in text and "Green" in text


def test_buttons_still_used_where_the_platform_draws_them(tmp_path):
    manager = _manager(tmp_path)
    manager.gateway = GatewayStub(interactive=True)
    item = manager.inbox.add_approval("s1", "Run it?")

    asyncio.run(manager.mirror_inbox_item(item))

    assert manager.gateway.texts == []
    assert len(manager.gateway.interactive_sends) == 1
