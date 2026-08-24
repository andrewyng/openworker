"""Event model — the contract between the turn engine and any surface (TUI/GUI/IDE).

No token streaming in v1, so granularity is per-message/per-tool. Streaming later adds
`assistant_delta` / `tool_output_delta` without changing the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    TURN_START = "turn_start"
    ASSISTANT_DELTA = "assistant_delta"
    REASONING_DELTA = "reasoning_delta"  # model thinking text (display-only, never replayed)
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_PROPOSED = "tool_proposed"
    PERMISSION_REQUIRED = "permission_required"
    DIRECTORY_REQUESTED = "directory_requested"  # agent asks the user to grant a folder
    TOOL_REQUESTED = "tool_requested"  # agent asks for a missing CLI tool (scanner, etc.)
    QUESTION_REQUESTED = (
        "question_requested"  # agent asks the user a free-text/multiple-choice question
    )
    PLAN_PROPOSED = (
        "plan_proposed"  # agent presents a plan for approval (plan mode exit)
    )
    TEAM_PROPOSED = (
        "team_proposed"  # a lead proposes a worker roster (the staffing gate)
    )
    ITEMS_PROPOSED = (
        "items_proposed"  # a lead proposes work items (the decomposition gate);
        # unlike propose_plan this is mode-independent — approval creates the items
    )
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    ITERATION_END = "iteration_end"
    TURN_END = "turn_end"
    ERROR = "error"
    INTERRUPTED = "interrupted"
    COMPACTING = "compacting"  # compaction started — surfaces show a transient signal
    COMPACTED = "compacted"  # outbound history was compacted (summary or trim)


@dataclass
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
