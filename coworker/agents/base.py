"""Agent — a top-level surface (Code / Chat / Cowork).

An agent owns its system prompt + base toolset + whether it needs a workspace. Distinct
from a Skill: skills are Anthropic-format, loadable capabilities that ANY agent can pull
in (see coworker.skills).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from ..tools.todo import TodoList


@dataclass
class AgentContext:
    workspace: Optional[Path] = None
    executor: Optional[Any] = None
    todo: Optional[TodoList] = None
    # Shared, mutable list of RootDir the session may touch (primary scratch + added folders).
    # When None, tools fall back to the single `workspace` root. Held by reference so runtime
    # add/remove of folders is seen by the file tools built from it.
    roots: Optional[list] = None


@dataclass
class Agent:
    name: str
    title: str
    system_prompt: str
    tool_factory: Optional[Callable[[AgentContext], list]] = None
    # Traits that replace the old per-agent-name branching in build_engine / manager.
    # requires_folder: the session cannot start without a user-picked primary folder
    # (composer + engine gate; everything else starts on a scratch dir). subagents:
    # read-only explorer fan-out. scheduling: scheduled tasks + self-wake. messaging:
    # exposes send_message. connectors: loads the integration toolset — True = every
    # connected connector (general builtins only), a tuple = allowlist (session gets
    # declared ∩ connected; OPE-93), False = none. Defaults keep non-persona callers
    # behaving as before. (The old family/needs_workspace/workspace trio collapsed into
    # these — see ocw-context/docs/workspace-scratch-design.md.)
    requires_folder: bool = False
    subagents: bool = False
    scheduling: bool = False
    messaging: bool = False
    connectors: bool | tuple[str, ...] = False
    # Team identity: "lead" | "worker" | None (solo-only). Gates the board/journal
    # toolsets and staffing eligibility — solo personas are never team-staffable.
    team: Optional[str] = None

    def build_tools(self, context: AgentContext) -> list:
        return list(self.tool_factory(context)) if self.tool_factory else []
