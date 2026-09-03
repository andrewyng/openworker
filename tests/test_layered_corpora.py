"""Schema, coverage, and production-tool parity for the layered security corpora."""
from __future__ import annotations

from scripts.validate_layered_corpora import validate_all


def test_layered_corpora_are_valid_and_meet_size_floors():
    summary = validate_all()
    assert summary["permission_gate.jsonl"]["rows"] >= 120
    assert summary["reviewer_actions.jsonl"]["rows"] >= 120
    assert summary["action_sequences.jsonl"]["rows"] >= 60
    assert summary["total"] >= 300


def test_layered_corpora_have_balanced_decision_classes():
    summary = validate_all()
    assert set(summary["permission_gate.jsonl"]["labels"]) == {
        "allow_without_reviewer",
        "reviewer_eligible",
        "human_only",
        "hard_deny",
    }
    assert set(summary["reviewer_actions.jsonl"]["labels"]) == {"allow", "ask", "deny"}
    assert set(summary["action_sequences.jsonl"]["labels"]) == {"allow", "ask", "deny"}


def test_layered_corpora_span_many_tools_and_tags():
    summary = validate_all()
    assert summary["permission_gate.jsonl"]["tools"] >= 30
    assert summary["reviewer_actions.jsonl"]["tools"] >= 45
    assert summary["action_sequences.jsonl"]["tools"] >= 40
    assert summary["permission_gate.jsonl"]["tags"] >= 50
    assert summary["reviewer_actions.jsonl"]["tags"] >= 50
    assert summary["action_sequences.jsonl"]["tags"] >= 35
