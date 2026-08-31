"""OPE-51 — ask_user upgrades: rich options ({label, description, recommended, preview}),
grouped questions (one call, a stepper, one round-trip), and the {answer}/{answers} result
shapes. Back-compat is load-bearing: plain-string options and old persisted items must be
untouched by all of it."""

import asyncio
import json

from coworker.inbox import InboxItem, InboxStore
from coworker.interactions import buttons_for, decode
from coworker.server.manager import SessionManager
from coworker.tools.ask import (
    MAX_GROUPED_QUESTIONS,
    SKIP_NOTE,
    SKIP_SENTINEL,
    answer_result,
    ask_user_tool,
    normalize_option,
    normalize_questions,
    option_label,
    question_item_fields,
)

from test_durable_resume import ScriptedProvider, _run_until_pending, _text, _tool


# -- schema -------------------------------------------------------------------


def test_schema_advertises_rich_options_and_grouped_questions():
    fn = ask_user_tool().__coworker_schema__["function"]
    assert fn["name"] == "ask_user"
    props = fn["parameters"]["properties"]
    # options: string-or-object union, object requires `label`
    variants = props["options"]["items"]["anyOf"]
    assert {"type": "string"} in variants
    obj = next(v for v in variants if v.get("type") == "object")
    assert obj["required"] == ["label"]
    assert set(obj["properties"]) == {"label", "description", "recommended", "preview"}
    # grouped: capped, each entry requires `question`
    grouped = props["questions"]
    assert grouped["maxItems"] == MAX_GROUPED_QUESTIONS
    assert grouped["items"]["required"] == ["question"]


# -- normalization helpers ----------------------------------------------------


def test_normalize_option_and_label():
    assert normalize_option("Bar") == {
        "label": "Bar",
        "description": "",
        "recommended": False,
        "preview": "",
    }
    rich = normalize_option({"label": "Line", "recommended": True, "preview": "p"})
    assert rich["recommended"] is True and rich["preview"] == "p"
    assert option_label("Bar") == "Bar" and option_label({"label": "Line"}) == "Line"


def test_normalize_questions_caps_and_drops_blanks():
    entries = [{"question": f"Q{i}?"} for i in range(MAX_GROUPED_QUESTIONS + 2)]
    assert len(normalize_questions(entries)) == MAX_GROUPED_QUESTIONS
    assert normalize_questions([{"question": "  "}, "junk", {"header": "h"}]) == []


def test_question_item_fields_plain_strings_pass_through():
    fields = question_item_fields({"question": "Env?", "options": ["staging", "prod"]})
    assert fields["title"] == "Env?"
    assert fields["options"] == ["staging", "prod"]  # simple asks stay today's pills
    assert fields["questions"] == []


def test_question_item_fields_rich_options_canonicalized():
    fields = question_item_fields(
        {"question": "Env?", "options": [{"label": "staging", "recommended": True}]}
    )
    assert fields["options"] == [
        {"label": "staging", "description": "", "recommended": True, "preview": ""}
    ]


def test_question_item_fields_grouped_surfaces_first_question():
    fields = question_item_fields(
        {
            "questions": [
                {"question": "Chart style?", "header": "Chart", "options": ["Bar"]},
                {"question": "Colors?", "multi": True},
            ]
        }
    )
    assert fields["title"] == "Chart style?" and fields["header"] == "Chart"
    assert fields["options"][0]["label"] == "Bar"
    assert len(fields["questions"]) == 2 and fields["questions"][1]["multi"] is True


def test_question_item_fields_nothing_asked():
    assert question_item_fields({}) is None
    assert question_item_fields({"question": "  "}) is None
    assert question_item_fields({"questions": [{"question": ""}]}) is None


# -- result shaping -----------------------------------------------------------


def test_answer_result_shapes():
    assert answer_result([], "staging") == {"answer": "staging"}
    assert answer_result([], None) == {"answer": ""}
    grouped = [{"question": "Chart style?", "header": "Chart"}, {"question": "Colors?"}]
    res = answer_result(grouped, json.dumps({"Chart": "Bar", "Colors?": "Blue"}))
    assert res == {"answers": {"Chart": "Bar", "Colors?": "Blue"}}
    # a text-only surface answered with a bare string → attributed to the first question
    assert answer_result(grouped, "Bar") == {"answers": {"Chart": "Bar"}}
    assert answer_result(grouped, "") == {"answer": ""}  # engine reads this as denied


# -- skipping (OPE-153) -------------------------------------------------------


def test_skipped_single_question_answers_null_not_empty():
    """The whole point of the sentinel: "" (no answer came back) and a skip must not
    look alike to the agent."""
    res = answer_result([], SKIP_SENTINEL)
    assert res["answer"] is None
    assert res["skipped"] is True
    assert res["note"] == SKIP_NOTE
    assert answer_result([], "") == {"answer": ""}  # unchanged, still not a skip


def test_skipped_grouped_questions_name_what_was_declined():
    grouped = [{"question": "Chart style?", "header": "Chart"}, {"question": "Colors?"}]
    # one step skipped, one answered
    res = answer_result(grouped, json.dumps({"Chart": SKIP_SENTINEL, "Colors?": "Blue"}))
    assert res["answers"] == {"Chart": None, "Colors?": "Blue"}
    assert res["skipped"] == ["Chart"]
    assert res["note"] == SKIP_NOTE
    # whole card skipped from the card: every question carries the sentinel
    res = answer_result(grouped, json.dumps({"Chart": SKIP_SENTINEL, "Colors?": SKIP_SENTINEL}))
    assert res["answers"] == {"Chart": None, "Colors?": None}
    assert res["skipped"] == ["Chart", "Colors?"]
    # a JSON null means the same thing (defensive — a surface may serialize it that way)
    assert answer_result(grouped, json.dumps({"Chart": None}))["skipped"] == ["Chart"]


def test_bare_sentinel_skips_a_whole_grouped_card():
    """A text-only surface can't send the answer map — the bare sentinel skips everything
    rather than being filed as the first question's answer."""
    grouped = [{"question": "Chart style?", "header": "Chart"}, {"question": "Colors?"}]
    res = answer_result(grouped, SKIP_SENTINEL)
    assert res["answers"] == {"Chart": None, "Colors?": None}
    assert res["skipped"] == ["Chart", "Colors?"]


def test_fully_answered_card_carries_no_skip_keys():
    """No skip → no `skipped`/`note` noise in the agent's tool result."""
    grouped = [{"question": "Chart style?", "header": "Chart"}]
    assert answer_result(grouped, json.dumps({"Chart": "Bar"})) == {"answers": {"Chart": "Bar"}}
    assert answer_result([], "staging") == {"answer": "staging"}


# -- inbox persistence + back-compat ------------------------------------------


def test_inbox_round_trips_grouped_questions(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    fields = question_item_fields(
        {
            "questions": [
                {
                    "question": "Format?",
                    "header": "Format",
                    "options": [{"label": "Table", "preview": "| a | b |"}],
                },
                {"question": "Where to?"},
            ]
        }
    )
    item = store.add_question("s1", **fields)
    reloaded = InboxStore(tmp_path / "inbox.json").get(item.id)
    assert reloaded.header == "Format"
    assert reloaded.questions == item.questions
    assert reloaded.options[0]["preview"] == "| a | b |"


def test_old_persisted_items_still_load():
    # Items written before OPE-51 carry no header/questions keys and string options.
    old = InboxItem(
        id="x", session_id="s", kind="question", title="Env?", options=["staging"]
    )
    assert old.header == "" and old.questions == []


# -- channel buttons ----------------------------------------------------------


def test_buttons_use_rich_option_labels(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    item = store.add_question(
        "s1",
        "Env?",
        options=["staging", {"label": "prod", "description": "the real one"}],
    )
    btns = buttons_for(item)
    assert [b.label for b in btns] == ["staging", "prod", "Skip"]
    assert decode(btns[1].value) == (item.id, "prod")  # resolution IS the label


def test_mirrored_question_offers_a_skip_button(tmp_path):
    """OPE-153 — the channel gets the same way out the card has. Skip trails the options and
    carries the sentinel, which answer_result() turns into a null answer."""
    store = InboxStore(tmp_path / "inbox.json")
    item = store.add_question("s1", "Env?", options=["staging", "prod"])
    skip = buttons_for(item)[-1]
    assert skip.label == "Skip"
    assert decode(skip.value) == (item.id, SKIP_SENTINEL)
    # and that resolution is what the agent ends up seeing
    assert answer_result(item.questions, SKIP_SENTINEL)["answer"] is None


def test_questions_without_options_get_no_buttons(tmp_path):
    """A free-text question still mirrors as plain text — no lone Skip button, since the
    "(Open the app to respond.)" fallback is the only way to answer it at all."""
    store = InboxStore(tmp_path / "inbox.json")
    item = store.add_question("s1", "What should I name it?")
    assert buttons_for(item) == []


def test_grouped_questions_get_no_buttons(tmp_path):
    store = InboxStore(tmp_path / "inbox.json")
    fields = question_item_fields(
        {"questions": [{"question": "A?", "options": ["x"]}, {"question": "B?"}]}
    )
    item = store.add_question("s1", **fields)
    assert buttons_for(item) == []  # one button row can't answer 2+ questions


# -- full stack: grouped call → Inbox item → JSON resolution → {answers} ------


def test_grouped_ask_round_trip_through_manager(tmp_path):
    mgr = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(
            [
                _tool(
                    "ask_user",
                    {
                        "questions": [
                            {
                                "question": "Chart style?",
                                "header": "Chart",
                                "options": [
                                    {"label": "Bar", "recommended": True},
                                    "Line",
                                ],
                            },
                            {"question": "Which distribution?", "header": "Distribution"},
                        ]
                    },
                    "call_g",
                ),
                _text("Bar it is, stacked."),
            ]
        ),
    )
    sid = "grouped-q"

    async def scenario():
        engine = mgr.get_engine(sid, agent="cowork", workspace=str(tmp_path))
        item = await _run_until_pending(mgr, sid, engine)
        assert item.kind == "question" and item.tool_call_id == "call_g"
        assert item.title == "Chart style?" and len(item.questions) == 2
        await mgr.resolve_inbox(
            item.id, json.dumps({"Chart": "Bar", "Distribution": "Stacked"})
        )

    asyncio.run(scenario())
    # The tool result the model saw carries the parsed answers map.
    rec = mgr.session_store.load(sid)
    tool_msgs = [m for m in rec.messages if m.get("role") == "tool"]
    assert tool_msgs, "no tool result was recorded"
    payload = json.loads(tool_msgs[-1]["content"])
    assert payload == {"answers": {"Chart": "Bar", "Distribution": "Stacked"}}
    assert mgr.inbox.pending(sid) == []
