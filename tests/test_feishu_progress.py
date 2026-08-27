import asyncio

from coworker.connectors.base import MessageEvent, SendResult, SessionSource
from coworker.connectors.adapters import feishu_card_action_to_interaction
import coworker.connectors.feishu_progress as progress_mod
from coworker.inbound_sessions import InboundSessionLink
from coworker.interactions import encode
from coworker.events import Event, EventType
import coworker.connectors.tools as tools_mod
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from coworker.server.manager import SessionManager


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _connect_feishu(mgr):
    mgr.secrets.put(
        "feishu:default",
        {"app_id": "cli_test", "app_secret": "sec", "enabled": True},
    )


def _feishu_event(text="你好"):
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform="feishu",
            chat_id="oc_1",
            user_id="ou_1",
            user_name="Ada",
            chat_type="dm",
        ),
        message_id="om_in",
    )


def test_feishu_inbound_reacts_updates_card_and_keeps_final_send(monkeypatch, tmp_path):
    calls: list[tuple] = []

    def fake_react(_token, message_id, emoji_type="THUMBSUP"):
        calls.append(("react", message_id, emoji_type))
        return SendResult(True, message_id=message_id)

    def fake_card(_token, chat_id, card):
        calls.append(("card", chat_id, card["header"]["title"]["content"], card))
        return SendResult(True, message_id="om_card")

    def fake_patch(_token, message_id, card):
        calls.append(
            (
                "patch",
                message_id,
                card["header"]["title"]["content"],
                card,
            )
        )
        return SendResult(True, message_id=message_id)

    def fake_progress_send(_token, chat_id, text, thread_id=None):
        calls.append(("progress_final", chat_id, text))
        return SendResult(True, message_id="om_fallback")

    def fake_tool_send(_token, chat_id, text, thread_id=None):
        calls.append(("tool_send", chat_id, text))
        return SendResult(True, message_id="om_reply")

    monkeypatch.setattr(progress_mod, "_react_feishu_message", fake_react)
    monkeypatch.setattr(progress_mod, "_send_feishu_interactive", fake_card)
    monkeypatch.setattr(progress_mod, "_patch_feishu_message", fake_patch)
    monkeypatch.setattr(progress_mod, "_send_feishu", fake_progress_send)
    monkeypatch.setitem(tools_mod.DEFAULT_SENDERS, "feishu", fake_tool_send)

    provider = ScriptedProvider(
        [
            AssistantTurn(
                tool_calls=[
                    ToolCall(
                        id="call_send",
                        name="send_message",
                        arguments={"target": "feishu:oc_1", "text": "你好，我在。"},
                    )
                ]
            ),
            AssistantTurn(text="已回复。"),
        ]
    )
    mgr = SessionManager(workspace=tmp_path, provider=provider)
    _connect_feishu(mgr)

    asyncio.run(mgr._dispatch_inbound(_feishu_event()))

    assert ("react", "om_in", "THUMBSUP") in calls
    assert any(call[0] == "card" and call[1] == "oc_1" and call[2] == "执行中" for call in calls)
    assert ("tool_send", "oc_1", "你好，我在。") in calls
    assert not [call for call in calls if call[0] == "progress_final"]
    assert any(call[0] == "patch" and call[2] == "已完成" for call in calls)
    rendered = "\n".join(
        element.get("text", {}).get("content", "")
        for call in calls
        if call[0] == "patch"
        for element in call[3].get("elements", [])
        if isinstance(element, dict)
    )
    assert "**工具调用**" in rendered
    assert "完成 **send_message**" in rendered


def test_feishu_inbound_fallback_sends_assistant_text(monkeypatch, tmp_path):
    calls: list[tuple] = []

    monkeypatch.setattr(
        progress_mod,
        "_react_feishu_message",
        lambda _token, message_id, emoji_type="THUMBSUP": SendResult(True, message_id=message_id),
    )
    monkeypatch.setattr(
        progress_mod,
        "_send_feishu_interactive",
        lambda _token, chat_id, card: SendResult(True, message_id="om_card"),
    )
    monkeypatch.setattr(
        progress_mod,
        "_patch_feishu_message",
        lambda _token, message_id, card: SendResult(True, message_id=message_id),
    )

    def fake_progress_send(_token, chat_id, text, thread_id=None):
        calls.append(("progress_final", chat_id, text))
        return SendResult(True, message_id="om_fallback")

    monkeypatch.setattr(progress_mod, "_send_feishu", fake_progress_send)

    mgr = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider([AssistantTurn(text="这是最终回复。")]),
    )
    _connect_feishu(mgr)

    asyncio.run(mgr._dispatch_inbound(_feishu_event()))

    assert calls == [("progress_final", "oc_1", "这是最终回复。")]


def test_feishu_progress_formats_tool_results_for_humans(tmp_path):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    _connect_feishu(mgr)
    reporter = progress_mod.FeishuRunProgressReporter.for_source(
        secrets=mgr.secrets,
        session_id="session123456789",
        source={
            "connector": "feishu",
            "kind": "dm",
            "channel_id": "oc_1",
            "message_id": "om_in",
        },
    )
    assert reporter is not None

    asyncio.run(
        reporter.on_event(
            Event(
                EventType.TOOL_FINISHED,
                {
                    "name": "todo_write",
                    "status": "ok",
                    "result_preview": '{"count": 2, "todos": [{"content": "\\u56de\\u590d\\u7528\\u6237", "status": "done"}, {"content": "\\u7b49\\u5f85", "status": "in_progress"}]}',
                },
            )
        )
    )
    asyncio.run(
        reporter.on_event(
            Event(
                EventType.TOOL_FINISHED,
                {
                    "name": "send_message",
                    "status": "ok",
                    "result_preview": '{"ok": true, "message_id": "om_x100", "target": "feishu:oc_1"}',
                },
            )
        )
    )
    card = reporter._build_card()
    rendered = "\n".join(
        element.get("text", {}).get("content", "")
        for element in card["elements"]
        if isinstance(element, dict)
    )

    assert "已更新待办：共 2 项，1 项完成，1 项进行中" in rendered
    assert "已发送飞书回复" in rendered
    assert "message_id" not in rendered
    assert "feishu:oc_1" not in rendered
    assert "\\u56de" not in rendered


def test_feishu_card_action_maps_to_interaction():
    value = encode("item1", "allow")
    event = {
        "event": {
            "operator": {"open_id": "ou_1"},
            "context": {
                "open_chat_id": "oc_1",
                "open_message_id": "om_card",
            },
            "action": {"value": {"ocw_value": value}},
        }
    }

    interaction = feishu_card_action_to_interaction(event)

    assert interaction is not None
    assert interaction.platform == "feishu"
    assert interaction.chat_id == "oc_1"
    assert interaction.message_id == "om_card"
    assert interaction.user_id == "ou_1"
    assert interaction.value == value


def test_feishu_dm_session_mirrors_approval_without_inbox_binding(tmp_path):
    deliveries = []

    class GatewayStub:
        async def deliver_interactive(self, *args):
            deliveries.append(args)

        async def deliver(self, *args):
            deliveries.append(args)

    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    mgr.gateway = GatewayStub()
    mgr.inbound_sessions.upsert(
        InboundSessionLink(
            route_key="feishu:dm:oc_1",
            session_id="s1",
            platform="feishu",
            chat_type="dm",
            chat_id="oc_1",
            user_id="ou_1",
        )
    )
    item = mgr.inbox.add_approval("s1", "Run `write_file`?", body="path: a.txt")

    asyncio.run(mgr.mirror_inbox_item(item))

    assert len(deliveries) == 1
    target, body, buttons = deliveries[0]
    assert target == "feishu:oc_1"
    assert "Run `write_file`?" in body
    assert [button.label for button in buttons] == ["Approve", "Deny"]


def test_feishu_dm_session_mirrors_directory_as_interactive_card(tmp_path):
    deliveries = []

    class GatewayStub:
        async def deliver_interactive(self, *args):
            deliveries.append(args)

        async def deliver(self, *args):
            deliveries.append(args)

    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    mgr.gateway = GatewayStub()
    mgr.inbound_sessions.upsert(
        InboundSessionLink(
            route_key="feishu:dm:oc_1",
            session_id="s1",
            platform="feishu",
            chat_type="dm",
            chat_id="oc_1",
            user_id="ou_1",
        )
    )
    item = mgr.inbox.add_directory("s1", "Grant access to a folder?", body="path: /home/xihe")

    asyncio.run(mgr.mirror_inbox_item(item))

    assert len(deliveries) == 1
    target, body, buttons = deliveries[0]
    assert target == "feishu:oc_1"
    assert "Grant access to a folder?" in body
    assert [button.label for button in buttons] == ["Grant", "Deny"]


def test_feishu_card_click_resolves_only_owning_dm_session(tmp_path):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    mgr.inbound_sessions.upsert(
        InboundSessionLink(
            route_key="feishu:dm:oc_1",
            session_id="s1",
            platform="feishu",
            chat_type="dm",
            chat_id="oc_1",
            user_id="ou_1",
        )
    )
    item = mgr.inbox.add_approval("s1", "Run it?")

    async def scenario():
        await mgr._on_interaction(
            feishu_card_action_to_interaction(
                {
                    "event": {
                        "operator": {"open_id": "ou_1"},
                        "context": {
                            "open_chat_id": "oc_other",
                            "open_message_id": "om_card",
                        },
                        "action": {"value": {"ocw_value": encode(item.id, "allow")}},
                    }
                }
            )
        )
        assert mgr.inbox.get(item.id).state == "pending"
        await mgr._on_interaction(
            feishu_card_action_to_interaction(
                {
                    "event": {
                        "operator": {"open_id": "ou_1"},
                        "context": {
                            "open_chat_id": "oc_1",
                            "open_message_id": "om_card",
                        },
                        "action": {"value": {"ocw_value": encode(item.id, "allow")}},
                    }
                }
            )
        )

    asyncio.run(scenario())
    assert mgr.inbox.get(item.id).resolution == "allow"


def test_feishu_card_click_updates_prompt_with_actor_and_choice(tmp_path):
    updates = []

    class GatewayStub:
        async def reject_interaction(self, event, text=""):
            raise AssertionError("expected allowed Feishu click")

        async def update_message(self, *args):
            updates.append(args)

    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    mgr.gateway = GatewayStub()
    mgr.inbound_sessions.upsert(
        InboundSessionLink(
            route_key="feishu:dm:oc_1",
            session_id="s1",
            platform="feishu",
            chat_type="dm",
            chat_id="oc_1",
            user_id="ou_1",
        )
    )
    item = mgr.inbox.add_directory("s1", "Grant access to a folder?")

    asyncio.run(
        mgr._on_interaction(
            feishu_card_action_to_interaction(
                {
                    "event": {
                        "operator": {"open_id": "ou_1"},
                        "context": {
                            "open_chat_id": "oc_1",
                            "open_message_id": "om_card",
                        },
                        "action": {
                            "value": {
                                "ocw_value": encode(item.id, '{"granted": true}')
                            }
                        },
                    }
                }
            )
        )
    )

    assert mgr.inbox.get(item.id).state == "resolved"
    assert updates == [
        (
            "feishu",
            "oc_1",
            "om_card",
            "Grant access to a folder?\n✅ granted - by ou_1",
        )
    ]
