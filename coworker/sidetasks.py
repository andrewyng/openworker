"""Side tasks — hand a self-contained job to a separate chat and get only its report back.

A chat that needs a bounded piece of research ("how do basketball hoops sense a made
shot?") would otherwise spend its own context on every search result and page. A side task
runs in a chat of its own — same coworker, folder and model — in the background. When it
finishes, its summary lands in the parent chat as a display-only card the user reads and
chooses to add to the chat, redo or dismiss. Nothing enters the parent's context unless the
user adds it: the card is a `notice` message, which `_outbound_messages` never sends.

Two tools. The parent's `start_side_task` asks for approval (the user sees the title and
brief) and returns at once, so the parent's turn carries on. The child's `report_back`
sends the summary and ends the child's turn. The manager (server/manager.py) wires both:
it owns the sessions, the spawn and the delivery.
"""

from __future__ import annotations

from typing import Any, Callable

import aisuite as ai

# The parent's `notice` kind that carries a report card.
SIDE_TASK_REPORT_KIND = "side_task_report"
# The user's verdict on a card (added / dismissed / new). The transcript log is
# append-only, so a verdict is its own marker notice; the GUI folds it into the card.
SIDE_TASK_STATUS_KIND = "side_task_report_status"
# A quiet line in the parent when the USER started a side task from the composer's
# "+" menu (the model-started path leaves its tool call in the transcript instead).
SIDE_TASK_STARTED_KIND = "side_task_started"
# A report is a summary, not the write-up: the full report belongs in a file.
MAX_SUMMARY_CHARS = 4000
# Running side tasks per chat. A chat that fans out further is a team job.
MAX_RUNNING_SIDE_TASKS = 3

SIDE_TASK_STARTED_HINT = (
    "The side task runs in its own chat. Carry on with your own work — do not wait for it "
    "and do not start it again. Its report reaches the user as a card, and the user "
    "decides whether it comes into this chat."
)


def side_task_opening(title: str, brief: str, parent_title: str) -> str:
    """The child's first message: the brief, framed with how to finish."""
    return (
        f"You are running a side task started from the chat “{parent_title}”. Do the job "
        "below in this chat, on your own: the user is not watching and will read your "
        "report later.\n\n"
        f"# {title}\n\n{brief}\n\n"
        "When you are done, call `report_back` with a summary of about 15 lines at most — "
        "the findings and what they mean for the asker, not your search trail. If there is "
        "more to say than that, write the full report to a Markdown file in the shared "
        "folder (for example `side-tasks/<short-name>.md`) and pass its path as "
        "`report_path`. Then end your turn."
    )


def start_side_task_tool(start: Callable[[str, str], dict[str, Any]]) -> Any:
    """The parent's tool. `start(title, brief)` is the manager's spawn entry point."""

    def start_side_task(title: str, brief: str) -> dict:
        """Hand a self-contained job — a study, research, a comparison, a draft — to a
        separate chat that runs in the background on the same coworker, folder and model,
        so this chat's context stays clear for the main work. Use it whenever the user
        asks for something "in a separate chat", "as a side task", "in the background",
        "without cluttering this chat", or "in a new chat and report back": do NOT do that
        work here. `title` names the new chat in a few words. `brief` is everything the
        side chat needs and nothing more: the question, the context it must know (the
        project, constraints, decisions so far), and what to deliver. Returns at once;
        carry on with your own work. The report arrives as a card for the user, who
        decides whether it comes into this chat — never wait for it."""
        return start(str(title or "").strip(), str(brief or "").strip())

    return ai.tool(
        start_side_task,
        metadata=ai.ToolMetadata(
            category="side_task",
            risk_level="medium",
            # EXTERNAL class (risk.py): the call shows an approval card with the title and
            # brief, so the user sees — and can refuse — every side task before it starts.
            requires_approval=True,
            capabilities=["side_task"],
        ),
    )


def report_back_tool(report: Callable[[str, str], dict[str, Any]]) -> Any:
    """The child's tool. `report(summary, report_path)` is the manager's delivery."""

    def report_back(summary: str, report_path: str = "") -> dict:
        """Finish this side task: send `summary` back to the chat that started it. Keep it
        to about 15 lines — the findings and what they mean, not the search trail. If the
        full write-up is longer, save it as a Markdown file in the shared folder and pass
        its path as `report_path`. Call this once, then end your turn."""
        return report(str(summary or ""), str(report_path or ""))

    # A successful report ends the child's turn (engine: `_yield_for_wake`), like a sleep.
    report_back.__coworker_yields_turn__ = True
    return report_back
