"""The `propose_plan` tool — the agent presents its plan and asks to start executing.

Registered only when the session starts in plan mode. Like `request_directory`, it is
intercepted by the TurnEngine: it emits a PLAN_PROPOSED event and waits for the user's
out-of-band decision. Approval flips the live PermissionEngine out of plan mode (same
session, full exploration context kept); rejection returns the user's feedback so the
agent can revise the plan. The callable here is only a schema carrier + a safe fallback
for surfaces without an approver.
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata, tool


# OPE-237: with `auto_approve_plan_approval` on, the plan also carries its steps, so that
# in auto-approve an approved plan is evidence for exactly those actions. An explicit
# schema: an array of objects can't be generated from the signature. The key is `steps`
# (never `items`, which shadows a template engine's map method; see tools/todo.py).
_STEPS_DESCRIPTION = (
    "Present your plan to the user for approval. In plan mode, approval starts execution. "
    "In auto-approve mode, use it before work with several actions the user did not spell "
    "out (for example tasks from a file): give a one-line summary in `plan` and every "
    "action in `steps`. Each approved step then runs without an approval card for the "
    "rest of this request; anything not listed is reviewed as usual. Be exact: a step's "
    "target is only the file path, the exact command line, or the file name you will "
    "use, with nothing added; any explanation goes in `note`. Use `send` for a file "
    "leaving the machine and name its destination host plainly."
)

_STEPS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "propose_plan",
        "description": _STEPS_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "plan": {"type": "string", "description": "What you will do, in a sentence or two."},
                "steps": {
                    "type": "array",
                    "description": "Auto-approve mode: every action, exactly as you will perform it.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["write", "delete", "run", "send"],
                                "description": "write or delete a file, run a command, send a file or message somewhere",
                            },
                            "target": {
                                "type": "string",
                                "description": "write/delete: the file path only; run: the exact command line only; send: the file name only (or, for a message, what it is). No description here.",
                            },
                            "destination": {
                                "type": "string",
                                "description": "send only: the host (example.com) or the connector recipient",
                            },
                            "note": {
                                "type": "string",
                                "description": "optional: a few words for the user on what or why; never checked",
                            },
                        },
                        "required": ["kind", "target"],
                    },
                },
            },
            "required": ["plan"],
        },
    },
}


_NO_APPROVER = {"approved": False, "error": "plan approval isn't available in this surface"}


def propose_plan_tool(steps: bool = False) -> object:
    """The tool; `steps=True` (flag `auto_approve_plan_approval`) adds the step list."""

    # Real handling lives in the engine (it needs the out-of-band approval round-trip).
    # These bodies only run if no approver is wired (e.g. a headless surface).
    def propose_plan(plan: str) -> dict:
        """Present your implementation plan to the user for approval. Use this once you
        have explored enough to commit to an approach: summarize what you'll change, in
        which files, and how you'll verify it. If approved, the session switches out of
        read-only plan mode and you implement the plan; if rejected, revise it using the
        feedback in the result. Don't start describing implementation steps as if you
        were doing them — propose first.
        """
        return dict(_NO_APPROVER)

    def propose_plan_with_steps(plan: str, steps: list = None) -> dict:
        return dict(_NO_APPROVER)

    func = propose_plan
    if steps:
        func = propose_plan_with_steps
        func.__name__ = "propose_plan"
        func.__doc__ = _STEPS_DESCRIPTION
        func.__coworker_schema__ = _STEPS_SCHEMA

    return tool(
        func,
        metadata=ToolMetadata(
            category="planning",
            risk_level="low",
            capabilities=["plan"],
            description=(
                "Present the implementation plan for user approval; approval exits "
                "read-only plan mode and starts execution."
            ),
        ),
    )
