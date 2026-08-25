"""Safe Deep Agents harness construction for OpenWork supervisors.

The harness receives no Deep Agents filesystem backend. All tools are adapted through the
OpenWork guarded executor, while durable parent execution remains in :mod:`graph`.
"""

from __future__ import annotations

from typing import Any, Callable

from .model import OpenWorkChatModel
from .tools import OpenWorkToolAdapter


class DeepAgentHarness:
    @staticmethod
    def available() -> bool:
        try:
            import deepagents  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def create(
        *, provider: Any, model: str, registry: Any,
        execute: Callable[[str, dict[str, Any]], Any], instructions: str,
    ) -> Any:
        """Create a planner/delegator without granting Deep Agents direct filesystem access."""
        from deepagents import create_deep_agent

        adapter = OpenWorkToolAdapter(registry, execute)
        return create_deep_agent(
            model=OpenWorkChatModel(provider=provider, model=model),
            tools=adapter.as_langchain(),
            system_prompt=instructions,
            subagents=[],  # OpenWork owns specialist lifecycle and approval policy.
            permissions=[],
        )
