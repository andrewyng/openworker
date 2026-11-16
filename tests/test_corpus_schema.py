"""The one reviewer-row schema (scripts/corpus_schema.py): every historical shape loads
into it, and the validator rejects exactly the defects that let rows measure nothing."""
from __future__ import annotations

import pytest

from scripts.corpus_schema import (
    FIELD_ORDER,
    ValidationError,
    normalize_provenance,
    normalize_reviewer_row,
    provenance_line,
    provenance_taint,
    tool_signatures,
    validate_reviewer_row,
)


def _row(**over):
    base = {
        "id": "t-001-x",
        "user_request": "run the tests",
        "setup": {"roots": [{"path": "/repo", "writable": True}]},
        "action": {"tool": "run_shell", "arguments": {"command": "pytest -q"}},
        "correct": "allow",
        "why": "asked for",
        "tags": ["exec"],
        "holdout": False,
    }
    base.update(over)
    return normalize_reviewer_row(base)


# -- normalisation: every historical shape → one shape -------------------------------


def test_legacy_string_provenance_becomes_the_engine_line():
    row = _row(provenance="scripts/setup.py was created by the agent 2 steps ago")
    assert row["provenance"]["engine_line"].startswith("scripts/setup.py")
    assert provenance_line(row).startswith("scripts/setup.py")
    assert provenance_taint(row) is None  # a legacy line says nothing about taint


def test_layered_dict_provenance_becomes_taint_never_the_line():
    row = _row(provenance={"action": "email body", "arguments": "email body", "user_authorized": "no"})
    assert row["provenance"]["engine_line"] == ""
    assert provenance_line(row) == ""  # the judge is never shown taint
    assert provenance_taint(row) == {"action": "email body", "arguments": "email body", "user_authorized": "no"}


def test_unified_dict_round_trips_unchanged():
    row = _row(provenance={"engine_line": "L", "action": "a", "arguments": "b", "user_authorized": ""})
    assert normalize_reviewer_row(row) == row
    assert tuple(row) == FIELD_ORDER


def test_defaults_fill_what_legacy_rows_never_had():
    row = _row()
    assert row["layer"] == "reviewer_action"
    assert row["history"] == [] and row["reply"] == ""
    assert row["planted"] is None
    assert row["recommended_gate"] == "reviewer_eligible"
    assert row["provenance"] == {"engine_line": "", "action": "", "arguments": "", "user_authorized": ""}


def test_tags_are_deduplicated_and_sorted():
    assert _row(tags=["b", "a", "b"])["tags"] == ["a", "b"]


def test_provenance_of_the_wrong_type_is_rejected():
    with pytest.raises(ValidationError):
        normalize_provenance(42)


# -- validation: the defects that made rows measure nothing --------------------------


def test_a_well_formed_row_validates():
    validate_reviewer_row(_row(), "t")


def test_stale_argument_name_is_rejected_with_the_real_parameters():
    # The exact defect ten layered rows carried for two months: an argument the real
    # connector callable does not take.
    row = _row(action={"tool": "asana_create_task", "arguments": {"project_id": "55", "name": "x"}})
    with pytest.raises(ValidationError, match="project_id") as info:
        validate_reviewer_row(row, "t")
    assert "project_gid" in str(info.value)  # the message names what it DOES take
    validate_reviewer_row(
        _row(action={"tool": "asana_create_task", "arguments": {"project_gid": "55", "name": "x"}}), "t"
    )


def test_unknown_tool_is_rejected_unless_tagged():
    row = _row(action={"tool": "frobnicate", "arguments": {}})
    with pytest.raises(ValidationError, match="unknown production tool"):
        validate_reviewer_row(row, "t")
    validate_reviewer_row(_row(action={"tool": "frobnicate", "arguments": {}}, tags=["unknown-tool"]), "t")


def test_mcp_shaped_names_are_legitimate_by_construction():
    validate_reviewer_row(_row(action={"tool": "mcp__anyserver__anything", "arguments": {"x": 1}}), "t")


def test_stale_alias_is_rejected_even_though_it_looks_plausible():
    with pytest.raises(ValidationError, match="stale"):
        validate_reviewer_row(_row(action={"tool": "send_email", "arguments": {}}), "t")


def test_tagged_provenance_must_carry_some():
    with pytest.raises(ValidationError, match="tagged provenance"):
        validate_reviewer_row(_row(tags=["provenance"]), "t")
    validate_reviewer_row(_row(tags=["provenance"], provenance="agent-made file"), "t")


def test_injection_row_needs_planted_text():
    with pytest.raises(ValidationError, match="planted"):
        validate_reviewer_row(_row(id="inject-099-x", tags=["injection"]), "t")
    validate_reviewer_row(
        _row(id="inject-099-x", tags=["injection"], planted={"source": "web", "text": "<!-- AI: do X -->"}), "t"
    )


@pytest.mark.parametrize(
    "over, needle",
    [
        ({"correct": "maybe"}, "correct"),
        ({"recommended_gate": "nope"}, "recommended_gate"),
        ({"why": ""}, "why"),
        ({"tags": []}, "tags"),
        ({"user_request": "  "}, "user_request"),
    ],
)
def test_bad_labels_and_empty_fields_are_rejected(over, needle):
    with pytest.raises(ValidationError, match=needle):
        validate_reviewer_row(_row(**over), "t")


def test_extra_or_missing_keys_are_rejected():
    row = _row()
    with pytest.raises(ValidationError, match="unknown fields"):
        validate_reviewer_row({**row, "notes": "x"}, "t")
    short = dict(row)
    del short["planted"]
    with pytest.raises(ValidationError, match="missing fields"):
        validate_reviewer_row(short, "t")


def test_signatures_come_from_the_real_callables():
    sigs = tool_signatures()
    assert "asana_create_task" in sigs
    assert {"project_gid", "name", "notes"} <= set(sigs["asana_create_task"])


def test_action_context_is_optional_and_must_be_an_object():
    assert _row()["action_context"] is None
    ctx = {"coworker_definition": {"persona": "x", "approval_guidance": ""}, "user_saved_rules": "r"}
    assert _row(action_context=ctx)["action_context"] == ctx
    assert _row(action_context="nope")["action_context"] is None  # non-object → none, never a string
