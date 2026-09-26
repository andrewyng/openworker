"""Chat connector tools (connectors-across-machines spec §11, 2026-09-05): Slack and
Telegram posting as ordinary connector tools gated by `connectors:`, the origin block
that names the reply call, and the thread grant that pre-approves it. No network."""

from __future__ import annotations

import pytest

from coworker.connectors.base import SendResult, SessionSource
from coworker.connectors.chat_tools import make_chat_tools, slack_workspaces
from coworker.connectors.origin import origin_block, reply_call, where_line
from coworker.connectors.tool_defs import rule_eligible, standing_target_for
from coworker.permissions import PermissionEngine, standing_rule_candidate
from coworker.secrets import SecretStore


class _Meta:
    def __init__(self, category="connector", requires_approval=True):
        self.category = category
        self.requires_approval = requires_approval


def _tools(secrets, record):
    def sender(token, chat_id, text, thread_id=None):
        record.append({"token": token, "chat_id": chat_id, "text": text, "thread_id": thread_id})
        return SendResult(True, message_id="99")

    return {t.__name__: t for t in make_chat_tools(secrets, senders={"slack": sender, "telegram": sender})}


# -- catalog + standing-rule targets ---------------------------------------------------


def test_chat_tools_are_catalog_entries_with_composite_targets():
    assert rule_eligible("slack_post_message") and rule_eligible("telegram_send_message")
    assert not rule_eligible("slack_upload_file")  # uploads are never covered by a thread grant
    assert standing_target_for("slack_post_message", {"workspace": "T1", "channel": "C1", "thread_ts": "1.2"}) == "slack:T1/C1:1.2"
    assert standing_target_for("slack_post_message", {"workspace": "default", "channel": "C1"}) == "slack:C1"
    assert standing_target_for("slack_post_message", {"channel": "C1", "thread_ts": "1.2"}) == "slack:C1:1.2"
    assert standing_target_for("slack_post_message", {"workspace": "T1"}) is None
    assert standing_target_for("telegram_send_message", {"chat_id": "123"}) == "telegram:123"
    assert standing_target_for("telegram_send_message", {"chat_id": "123", "thread_id": "7"}) == "telegram:123:7"
    meta = _Meta()
    assert standing_rule_candidate("slack_post_message", {"workspace": "T1", "channel": "C1", "thread_ts": "1.2", "text": "x"}, meta) == "slack:T1/C1:1.2"


def test_thread_grant_matches_the_platform_tool_on_that_thread_only(tmp_path):
    e = PermissionEngine(workspace_root=tmp_path, task_rules={"slack_post_message": {"slack:T1/C1:1.2"}})
    meta = _Meta()
    hit = e.evaluate("slack_post_message", {"workspace": "T1", "channel": "C1", "thread_ts": "1.2", "text": "hi"}, meta)
    assert hit.allowed and hit.rule == "slack_post_message → slack:T1/C1:1.2"
    # The channel itself (no thread), another thread, another workspace: all ask.
    for args in (
        {"workspace": "T1", "channel": "C1", "text": "hi"},
        {"workspace": "T1", "channel": "C1", "thread_ts": "9.9", "text": "hi"},
        {"workspace": "T2", "channel": "C1", "thread_ts": "1.2", "text": "hi"},
    ):
        d = e.evaluate("slack_post_message", args, meta)
        assert not d.allowed and d.needs_user, args
    # Uploads never ride the text grant.
    up = e.evaluate("slack_upload_file", {"workspace": "T1", "channel": "C1", "thread_ts": "1.2", "path": "a.pdf"}, meta)
    assert not up.allowed


# -- the tools ---------------------------------------------------------------------------


def test_slack_post_message_uses_the_workspace_token_and_threads(tmp_path):
    secrets = SecretStore(tmp_path / "s.json")
    secrets.put("slack:team:T1", {"bot_token": "xoxb-t1"})
    record: list = []
    tools = _tools(secrets, record)
    out = tools["slack_post_message"](workspace="T1", channel="C1", thread_ts="1.2", text="hello")
    assert out == {"ok": True, "message_id": "99", "workspace": "T1", "channel": "C1", "thread_ts": "1.2"}
    assert record == [{"token": "xoxb-t1", "chat_id": "T1/C1", "text": "hello", "thread_id": "1.2"}]
    # A single connected workspace may be left implicit.
    out = tools["slack_post_message"](channel="C2", text="top level")
    assert out["ok"] and record[-1]["chat_id"] == "T1/C2" and record[-1]["thread_id"] is None
    # Metadata: a connector write that gates.
    t = tools["slack_post_message"]
    assert t.__aisuite_tool_metadata__.category == "connector" and t.__aisuite_tool_metadata__.requires_approval is True
    assert t.__coworker_schema__["function"]["parameters"]["required"] == ["channel", "text"]


def test_slack_post_message_refuses_to_guess_between_workspaces(tmp_path):
    secrets = SecretStore(tmp_path / "s.json")
    secrets.put("slack:team:T1", {"bot_token": "xoxb-t1"})
    secrets.put("slack:default", {"bot_token": "xoxb-manual", "mode": "socket"})
    assert sorted(slack_workspaces(secrets)) == ["T1", "default"]
    record: list = []
    tools = _tools(secrets, record)
    err = tools["slack_post_message"](channel="C1", text="x")["error"]
    assert "several Slack workspaces" in err and "T1" in err and record == []
    # Named explicitly: the manual install posts with a bare channel id + its own token.
    out = tools["slack_post_message"](workspace="default", channel="C1", text="x")
    assert out["ok"] and record[-1] == {"token": "xoxb-manual", "chat_id": "C1", "text": "x", "thread_id": None}
    assert "not connected" in tools["slack_post_message"](workspace="T9", channel="C1", text="x")["error"]
    assert "no Slack workspace" in _tools(SecretStore(tmp_path / "empty.json"), [])["slack_post_message"](channel="C1", text="x")["error"]


def test_slack_channel_name_resolves_within_the_workspace(tmp_path, monkeypatch):
    secrets = SecretStore(tmp_path / "s.json")
    secrets.put("slack:team:T1", {"bot_token": "xoxb-t1"})
    seen: list = []

    def fake_list(secrets_, team_id, query="", limit=25, **_kw):
        seen.append((team_id, query))
        return {"ok": True, "channels": [{"id": "C77", "name": "general", "is_member": True}, {"id": "C78", "name": "generalities", "is_member": False}]}

    monkeypatch.setattr("coworker.connectors.slack_directory.list_channels", fake_list)
    record: list = []
    tools = _tools(secrets, record)
    assert tools["slack_post_message"](workspace="T1", channel="#general", text="hi")["channel"] == "C77"
    assert seen == [("T1", "general")] and record[-1]["chat_id"] == "T1/C77"
    assert "no channel named #nope" in tools["slack_post_message"](workspace="T1", channel="nope", text="hi")["error"]


def test_telegram_send_message(tmp_path):
    secrets = SecretStore(tmp_path / "s.json")
    record: list = []
    tools = _tools(secrets, record)
    assert "connect it first" in tools["telegram_send_message"](chat_id="123", text="x")["error"]
    secrets.put("telegram:default", {"bot_token": "T0K"})
    out = tools["telegram_send_message"](chat_id="123", text="hello", thread_id="7")
    assert out == {"ok": True, "message_id": "99", "chat_id": "123"}
    assert record == [{"token": "T0K", "chat_id": "123", "text": "hello", "thread_id": "7"}]
    assert "chat_id is required" in tools["telegram_send_message"](text="x")["error"]


# -- assembly: the connector gate is the only gate -------------------------------------


class _StubProvider:
    def complete(self, **_kw):  # pragma: no cover
        from coworker.providers import AssistantTurn

        return AssistantTurn()

    def capabilities(self, _model):  # pragma: no cover
        from coworker.providers.base import ModelCapabilities

        return ModelCapabilities()

    def stream(self, **_kw):  # pragma: no cover
        from coworker.providers.base import StreamChunk

        yield StreamChunk(turn=self.complete())


def _engine(agent, secrets, tmp_path):
    from coworker.agent import build_engine

    return build_engine(agent=agent, workspace=tmp_path, provider=_StubProvider(), secrets=secrets)


def test_slack_in_connectors_is_the_whole_condition(tmp_path):
    from coworker.agents.base import Agent

    secrets = SecretStore(tmp_path / "s.json")
    secrets.put("slack:default", {"bot_token": "xoxb", "app_token": "xapp", "mode": "socket"})
    lead = Agent(name="lead", title="Lead", system_prompt="x", connectors=("github", "slack"))
    names = _engine(lead, secrets, tmp_path).registry.names()
    assert {"slack_post_message", "slack_upload_file", "send_message", "send_file"} <= set(names)
    assert "telegram_send_message" not in names  # telegram isn't connected
    # The old `messaging` trait decides nothing: GitHub-only stays chat-less…
    reviewer = Agent(name="rev", title="Reviewer", system_prompt="x", connectors=("github",), messaging=True)
    names = _engine(reviewer, secrets, tmp_path).registry.names()
    assert "slack_post_message" not in names and "send_message" not in names
    # …and `connectors: true` (every connected connector) gets the chat tools.
    general = Agent(name="gen", title="General", system_prompt="x", connectors=True)
    assert "slack_post_message" in _engine(general, secrets, tmp_path).registry.names()


def test_manifest_can_chat_is_derived_from_connectors():
    from coworker.personas.loading import capability_set
    from coworker.personas.manifest import parse_manifest

    m = parse_manifest("---\nid: a\nname: A\nconnectors: [github, slack]\nmessaging: false\n---\nprompt")
    assert m.can_chat is True and m.to_agent().messaging is True and "messaging" in capability_set(m)
    m = parse_manifest("---\nid: b\nname: B\nconnectors: [github]\nmessaging: true\n---\nprompt")
    assert m.can_chat is False and m.to_agent().messaging is False and "messaging" not in capability_set(m)


# -- the origin block -------------------------------------------------------------------


def test_origin_block_slack_thread():
    src = SessionSource(platform="slack", chat_id="T07/C0B", user_id="U1", user_name="Rohit", chat_name="team", chat_type="channel", team_id="T07")
    block = origin_block(src, thread="1788.5")
    assert block.splitlines() == [
        "From Slack · workspace T07 · #team (C0B) · thread 1788.5",
        "Sent by Rohit (U1)",
        'To answer: slack_post_message(workspace="T07", channel="C0B", thread_ts="1788.5", text=…) — pre-approved for this thread.',
    ]
    dm = SessionSource(platform="slack", chat_id="D9", user_id="U1", user_name="U1", chat_type="dm")
    assert where_line(dm) == "From Slack · workspace default · DM (D9)"
    judged = origin_block(src, thread="1788.5", judgement_only=True)
    assert judged.endswith("— this asks for approval.") and "only if it clearly concerns your job" in judged


def test_origin_block_telegram_and_github():
    tg = SessionSource(platform="telegram", chat_id="123", user_id="7", user_name="Ann", chat_name="Ann", chat_type="dm")
    assert reply_call(tg) == 'telegram_send_message(chat_id="123", text=…)'
    assert where_line(tg, "1") == "From Telegram · chat 123 (Ann)"  # the General topic is not a topic
    gh = SessionSource(platform="github", chat_id="acme/site#42", user_id="octocat", user_name="octocat", chat_name="acme/site#42", chat_type="channel")
    assert where_line(gh, frame={"kind": "pr_open", "title": "Add retry"}) == 'From GitHub · acme/site · pull request #42 "Add retry"'
    assert where_line(gh, frame={"kind": "issue_open"}) == "From GitHub · acme/site · issue #42"
    assert reply_call(gh) == 'github_reply(owner="acme", repo="site", number=42, body=…)'
    block = origin_block(gh, frame={"is_pr": True})
    assert block.endswith('body=…) — pre-approved for this thread; github_review(owner="acme", repo="site", pull_number=42, event=…, body=…) likewise.')
    assert "github_review" not in origin_block(gh, frame={"kind": "issue_open"})
