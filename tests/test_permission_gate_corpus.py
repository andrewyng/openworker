"""Execution coverage for the deterministic permission-gate corpus."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from coworker.permissions import Decision, PermissionEngine
from scripts.eval_permission_gate import (
    CORPUS_PATH,
    decision_label,
    main,
    run_permission_gate,
)
from scripts.validate_layered_corpora import ValidationError


def _rows() -> list[dict]:
    return [
        json.loads(line)
        for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (Decision(True), "allow_without_reviewer"),
        (Decision(False, needs_user=True), "reviewer_eligible"),
        (Decision(False, needs_user=True, human_only=True), "human_only"),
        (Decision(False), "hard_deny"),
    ],
)
def test_decision_label_preserves_every_gate_outcome(decision, expected):
    assert decision_label(decision) == expected


def test_every_permission_gate_row_executes_through_current_policy(monkeypatch):
    rows = _rows()
    evaluated_tools: list[str] = []
    real_evaluate = PermissionEngine.evaluate

    def recording_evaluate(self, tool_name, arguments, metadata=None):
        evaluated_tools.append(tool_name)
        return real_evaluate(self, tool_name, arguments, metadata)

    monkeypatch.setattr(PermissionEngine, "evaluate", recording_evaluate)
    report = run_permission_gate()

    assert report.row_count == 120
    assert evaluated_tools == [row["action"]["tool"] for row in rows]
    assert report.current_mismatches == ()
    assert {result.actual for result in report.results} == {
        "allow_without_reviewer",
        "reviewer_eligible",
        "human_only",
        "hard_deny",
    }
    assert {result.id for result in report.secure_differences} == {
        row["id"] for row in rows if row["expected_current"] != row["expected_secure"]
    }
    assert report.passed


def test_declared_engine_setup_replays_allowlists_rules_and_overrides():
    report = run_permission_gate()
    by_id = {result.id: result for result in report.results}

    for row_id in (
        "gate-099-global-command-allowlist",
        "gate-100-global-domain-allowlist",
        "gate-101-standing-message-rule",
        "gate-103-mcp-relaxed",
    ):
        assert by_id[row_id].actual == "allow_without_reviewer"


def test_current_mismatch_fails_the_report_and_cli(tmp_path, capsys):
    rows = _rows()
    rows[0]["expected_current"] = "hard_deny"
    rows[0]["expected_secure"] = "hard_deny"
    corpus = tmp_path / "permission_gate.jsonl"
    _write_rows(corpus, rows)

    report = run_permission_gate(corpus)
    assert not report.passed
    assert [result.id for result in report.current_mismatches] == [rows[0]["id"]]
    assert report.current_mismatches[0].actual == "allow_without_reviewer"
    assert main(["--corpus", str(corpus)]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["current_mismatches"][0]["id"] == rows[0]["id"]


def test_known_secure_differences_are_reported_without_failing(capsys):
    report = run_permission_gate()

    assert report.secure_differences
    assert report.passed
    assert main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["current_mismatches"] == []
    assert len(output["secure_differences"]) == len(report.secure_differences)


@pytest.mark.parametrize(
    ("setup_update", "message"),
    [
        ({"permission_engnie": {"allowed_commands": ["pytest"]}}, "unknown setup fields"),
        ({"permission_engine": {"magic_grant": True}}, "unknown permission_engine fields"),
    ],
)
def test_unknown_setup_is_rejected_by_api_and_cli(
    tmp_path, capsys, setup_update, message
):
    rows = _rows()
    rows[0]["setup"].update(setup_update)
    corpus = tmp_path / "permission_gate.jsonl"
    _write_rows(corpus, rows)

    with pytest.raises(ValidationError, match=message):
        run_permission_gate(corpus)
    assert main(["--corpus", str(corpus)]) == 1
    error = json.loads(capsys.readouterr().err)
    assert message in error["error"]


def test_invalid_root_access_type_is_rejected(tmp_path):
    rows = _rows()
    rows[0]["setup"]["roots"][0]["writable"] = "yes"
    corpus = tmp_path / "permission_gate.jsonl"
    _write_rows(corpus, rows)

    with pytest.raises(ValidationError, match="writable must be a boolean"):
        run_permission_gate(corpus)
