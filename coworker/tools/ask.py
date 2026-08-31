"""The `ask_user` tool — the agent asks the user a question and waits for the answer.

The general human-in-the-loop Q&A primitive, modelled on Claude Code's own AskUserQuestion: a
question, optional quick-reply `options`, and (by default) an always-available free-text escape —
plus `multi` for choose-several. Like `request_directory`, it's intercepted by the TurnEngine: the
question becomes an Inbox item (answerable inline in the live session, or from the Inbox when the
session runs unattended), the agent suspends until it's resolved, and the answer comes back as the
tool result. The callable here is only a schema carrier + a safe fallback.

OPE-51 upgrades: options may be rich objects ({label, description, recommended, preview}) instead
of plain strings, and `questions` groups up to 4 questions into ONE call (rendered as a stepper —
one agent round-trip instead of several). Plain-string options and the singular `question` form
stay valid: old sessions and simple asks render exactly as before.
"""

from __future__ import annotations

import json

from aisuite.agents import ToolMetadata, tool

# How many questions one grouped call may carry (stepper chips get unreadable past this).
MAX_GROUPED_QUESTIONS = 4

# OPE-153 — the resolution string a card sends when the user declines to answer. The resolution
# field otherwise carries the answer verbatim, so "skipped" needs a value no answer can collide
# with; the GUI writes the same constant (see SKIP in InboxItemCard.tsx — keep the two in step).
# A grouped card marks a per-question skip by using it as that question's value in the answer map,
# and a whole-card skip by filling every unanswered question with it.
SKIP_SENTINEL = "__ocw_skip__"

# Told to the agent alongside a skipped answer. Only the no-re-ask half is absolute — it's the
# anti-loop guarantee the skip exists for. "Pick a default" is deliberately conditional: the same
# note fires for "you choose the chart colour" and for "Staging or Production?", and a model that
# follows instructions literally would deploy off a shrug. Proceeding without deciding is offered
# as a third path so that judgment is asked for, not left to whichever model happens to be driving.
SKIP_NOTE = (
    "The user chose not to answer. Do not put this question back to them. If you can proceed "
    "safely, pick a sensible default and say which way you went. If the choice is consequential "
    "or hard to undo, don't guess — say what you'd need and let them come back to it."
)

# An option is a plain string OR a rich object. `label` is what the user picks (and what comes
# back as the answer); `description` renders under it; `recommended` adds the green tag (put the
# recommended option first); `preview` is monospace text shown in the side pane (code, config,
# ASCII mockups, SQL — any text; when ≥1 option has one the card switches to two-pane layout).
_OPTION_SCHEMA = {
    "anyOf": [
        {"type": "string"},
        {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "description": {"type": "string"},
                "recommended": {"type": "boolean"},
                "preview": {"type": "string"},
            },
            "required": ["label"],
        },
    ]
}

# Explicit schema (same pattern as todo.py): the string-or-object option union and the nested
# `questions` array can't be auto-generated from the signature reliably.
_ASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Ask the user one or more questions and wait for their answer. Use for decisions or "
            "information only the user can provide. Do not use it to ask permission for a "
            "specific action you are about to take — propose the action instead; the approval "
            "flow shows the user exactly what would run and does the asking. Group related "
            f"questions (up to {MAX_GROUPED_QUESTIONS}) into one call via `questions` instead "
            "of asking serially."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The full question, in plain language (single-question form).",
                },
                "options": {
                    "type": "array",
                    "items": _OPTION_SCHEMA,
                    "description": (
                        "Optional quick-reply choices: plain strings, or objects with `label` "
                        "(required — this is the answer value), `description` (why/when to pick "
                        "it), `recommended` (green tag; list that option first), and `preview` "
                        "(monospace text — code, config, a mockup — shown in a side pane)."
                    ),
                },
                "allow_text": {
                    "type": "boolean",
                    "description": (
                        "Keep a free-text answer available even when options exist (default true; "
                        "the \"Other / type your own\" escape). Set false only when the options "
                        "are exhaustive."
                    ),
                },
                "multi": {
                    "type": "boolean",
                    "description": "Allow the user to pick more than one option.",
                },
                "header": {
                    "type": "string",
                    "description": "Short (≤ ~12 char) chip label for the card, e.g. \"Region\".",
                },
                "questions": {
                    "type": "array",
                    "maxItems": MAX_GROUPED_QUESTIONS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "header": {
                                "type": "string",
                                "description": (
                                    "Short (≤ ~12 char) label — names this step in the stepper "
                                    "chips and keys its answer in the result."
                                ),
                            },
                            "options": {"type": "array", "items": _OPTION_SCHEMA},
                            "allow_text": {"type": "boolean"},
                            "multi": {"type": "boolean"},
                        },
                        "required": ["question"],
                    },
                    "description": (
                        f"Grouped form: up to {MAX_GROUPED_QUESTIONS} questions asked in ONE "
                        "round-trip, rendered as a stepper. When set, the singular "
                        "question/options fields are ignored."
                    ),
                },
            },
            "required": [],
        },
    },
}


def ask_user_tool() -> object:
    def ask_user(
        question: str = "",
        options: list | None = None,
        allow_text: bool = True,
        multi: bool = False,
        header: str = "",
        questions: list | None = None,
    ) -> dict:
        """Ask the user a question and wait for their answer — use when you genuinely need a human
        decision or information you can't infer (a preference, a missing fact, a choice between real
        alternatives). Prefer this over guessing or stalling.

        Never use it to ask permission for a specific action you are about to take ("shall I
        open a PR?") — propose the action instead: the approval flow shows the user the exact
        command/arguments and does the asking, which is stronger consent than a chat yes.

        Single form returns `{"answer": "..."}` — the chosen option label(s) or the typed text.
        Grouped form (`questions`) returns `{"answers": {"<header or question>": "..."}}` — one
        entry per question. Don't ask what you can reasonably decide yourself; reserve this for
        choices that are actually the user's to make.

        The user may SKIP any question. A skipped answer comes back as `null` (never ""), with
        `skipped` naming what was declined. Treat it as "you decide" where the choice is safe:
        pick a sensible default, continue, and say which way you went. Where it isn't — anything
        consequential or hard to undo — don't guess; say what you'd need instead. Either way,
        never re-ask a skipped question.
        """
        # Real handling lives in the engine (it needs the out-of-band Inbox round-trip). This body
        # only runs if no question_asker is wired (e.g. a headless surface).
        return {
            "answer": "",
            "error": "asking the user isn't available in this surface",
        }

    wrapped = tool(
        ask_user,
        metadata=ToolMetadata(
            category="interaction",
            risk_level="low",
            capabilities=["ask_user"],
            description=(
                "Ask the user a question (free-text or multiple-choice) and wait for their answer. "
                "Use for decisions or information only the user can provide — never to ask "
                "permission for a specific action; propose the action and let the approval flow ask."
            ),
        ),
    )
    wrapped.__coworker_schema__ = _ASK_SCHEMA
    return wrapped


def normalize_option(opt) -> dict:
    """One option in canonical dict form: {label, description, recommended, preview}. Plain
    strings become {label: str, ...empty}. The label doubles as the answer value everywhere
    (buttons, pills, resolutions), so it is always a non-empty-able str."""
    if isinstance(opt, dict):
        return {
            "label": str(opt.get("label", "")),
            "description": str(opt.get("description", "")),
            "recommended": bool(opt.get("recommended", False)),
            "preview": str(opt.get("preview", "")),
        }
    return {"label": str(opt), "description": "", "recommended": False, "preview": ""}


def option_label(opt) -> str:
    """The answer value / button text for a str-or-dict option."""
    return str(opt.get("label", "")) if isinstance(opt, dict) else str(opt)


def normalize_questions(raw) -> list[dict]:
    """The grouped `questions` arg in canonical form (capped, blanks dropped). Each entry:
    {question, header, options: [canonical option], allow_text, multi}."""
    out: list[dict] = []
    for entry in list(raw or [])[:MAX_GROUPED_QUESTIONS]:
        if not isinstance(entry, dict):
            continue
        q = str(entry.get("question", "")).strip()
        if not q:
            continue
        out.append(
            {
                "question": q,
                "header": str(entry.get("header", "")),
                "options": [normalize_option(o) for o in entry.get("options") or []],
                "allow_text": bool(entry.get("allow_text", True)),
                "multi": bool(entry.get("multi", False)),
            }
        )
    return out


def question_item_fields(args: dict) -> dict | None:
    """`InboxStore.add_question` kwargs from raw ask_user args, or None when nothing was asked.
    A grouped call surfaces its FIRST question as title/options too, so legacy surfaces (channel
    mirrors, old persisted-item readers) degrade to a sensible single question."""
    grouped = normalize_questions(args.get("questions"))
    if grouped:
        first = grouped[0]
        return {
            "title": first["question"],
            "options": first["options"],
            "allow_text": first["allow_text"],
            "multi": first["multi"],
            "header": first["header"],
            "questions": grouped,
        }
    question = str(args.get("question", "")).strip()
    if not question:
        return None
    return {
        "title": question,
        # Strings pass through untouched (simple asks keep rendering as today's pills);
        # rich objects are canonicalized so downstream never meets a half-filled dict.
        "options": [
            o if isinstance(o, str) else normalize_option(o)
            for o in args.get("options") or []
        ],
        "allow_text": bool(args.get("allow_text", True)),
        "multi": bool(args.get("multi", False)),
        "header": str(args.get("header", "")),
        "questions": [],
    }


def question_key(entry) -> str:
    """The answer-map key for one grouped question — its header, else the question text. The
    same key the card writes when it resolves, so the two halves agree."""
    if not isinstance(entry, dict):
        return "answer"
    return str(entry.get("header") or entry.get("question") or "answer")


def answer_result(item_questions: list, resolution: str | None) -> dict:
    """Shape the ask_user tool result from an Inbox item's resolution string. Grouped items
    resolve with a JSON object string keyed by header-or-question → `{"answers": {...}}`;
    everything else returns the plain `{"answer": str}` shape.

    A skipped question (OPE-153) answers `None` rather than "", so the agent can tell "the user
    declined" from "the answer came back empty" — the two used to be the same value. Skips also
    carry `skipped` (the question keys, or True for a lone question) and `SKIP_NOTE`."""
    if item_questions:
        # A text-only surface (mirrored channel) can only send the bare sentinel: that skips the
        # whole card, so every question answers None.
        if resolution == SKIP_SENTINEL:
            keys = [question_key(q) for q in item_questions]
            return {
                "answers": dict.fromkeys(keys),
                "skipped": keys,
                "note": SKIP_NOTE,
            }
        try:
            parsed = json.loads(resolution or "")
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            answers: dict[str, str | None] = {}
            skipped: list[str] = []
            for k, v in parsed.items():
                key = str(k)
                if v is None or str(v) == SKIP_SENTINEL:
                    answers[key] = None
                    skipped.append(key)
                else:
                    answers[key] = str(v)
            result: dict = {"answers": answers}
            if skipped:
                result["skipped"] = skipped
                result["note"] = SKIP_NOTE
            return result
        if resolution:
            # Answered from a text-only surface (e.g. a mirrored channel): attribute the lone
            # answer to the first question rather than losing it.
            return {"answers": {question_key(item_questions[0]): str(resolution)}}
        return {"answer": ""}
    if resolution == SKIP_SENTINEL:
        return {"answer": None, "skipped": True, "note": SKIP_NOTE}
    return {"answer": resolution or ""}
