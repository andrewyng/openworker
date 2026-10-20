"""Schema, coverage, and production-tool parity for every security corpus."""
from __future__ import annotations

import json

from scripts.build_layered_corpora import build_all
from scripts.corpus_schema import FIELD_ORDER, REVIEWER_FILES
from scripts.validate_layered_corpora import CORPUS_DIR, validate_all


def test_layered_corpora_are_valid_and_meet_size_floors():
    summary = validate_all()
    assert summary["permission_gate.jsonl"]["rows"] >= 120
    assert summary["reviewer_actions.jsonl"]["rows"] >= 120
    assert summary["action_sequences.jsonl"]["rows"] >= 60
    assert summary["benign.jsonl"]["rows"] >= 40
    assert summary["dangerous.jsonl"]["rows"] >= 30
    assert summary["injection.jsonl"]["rows"] >= 20
    assert summary["total"] >= 400


def test_committed_corpora_are_exactly_what_the_generator_produces():
    # The JSONL is build output. A hand edit that drifts from the generator would be
    # overwritten on the next build, so it must not exist: regenerate instead.
    for name, rows in build_all().items():
        on_disk = [
            json.loads(line)
            for line in (CORPUS_DIR / name).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert on_disk == rows, f"{name}: committed file differs from the generator output"


def test_every_reviewer_file_uses_the_one_schema():
    for name in REVIEWER_FILES:
        for line in (CORPUS_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            assert tuple(row) == FIELD_ORDER, f"{name}: {row.get('id')} has keys {tuple(row)}"
            assert isinstance(row["provenance"], dict)


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
