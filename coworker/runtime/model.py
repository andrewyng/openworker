"""LangChain chat-model view over OpenWork's provider router.

It is intentionally a thin adapter: provider selection, credentials, native thinking and
usage remain owned by ``ProviderRouter``. This lets Deep Agents use the selected model without
receiving a secret store or constructing provider SDK clients itself.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr


class OpenWorkChatModel(BaseChatModel):
    model_name: str
    _provider: Any = PrivateAttr()
    _settings: dict[str, Any] = PrivateAttr(default_factory=dict)

    def __init__(self, *, provider: Any, model: str, settings: dict[str, Any] | None = None) -> None:
        super().__init__(model_name=model)
        self._provider = provider
        self._settings = dict(settings or {})

    @property
    def _llm_type(self) -> str:
        return "openwork-provider-router"

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs: Any) -> ChatResult:
        outbound = [
            {"role": self._role(message), "content": message.content}
            for message in messages
        ]
        settings = {**self._settings, **kwargs}
        if stop:
            settings["stop"] = stop
        turn = self._provider.complete(model=self.model_name, messages=outbound, **settings)
        tool_calls = [
            {"name": call.name, "args": call.arguments, "id": call.id, "type": "tool_call"}
            for call in turn.tool_calls
        ]
        message = AIMessage(
            content=turn.text or "", tool_calls=tool_calls,
            additional_kwargs={"reasoning": turn.reasoning, "usage": turn.usage.as_dict() if turn.usage else None},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    @staticmethod
    def _role(message: BaseMessage) -> str:
        kind = message.type
        if kind == "human":
            return "user"
        if kind == "ai":
            return "assistant"
        if kind == "tool":
            return "tool"
        return "system"
