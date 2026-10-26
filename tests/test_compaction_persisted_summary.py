"""OPE-170 problem 2: the compaction summary survives in the transcript.

Before, the `compacted` notice carried only a generic sentence; the summary lived in the
engine's in-memory `compaction_state` (persisted by the app's session record, but absent
from `messages`, exported trajectories, and run records). Now the notice and the
COMPACTED event carry the compaction record — summary, working state, boundary, model,
whether it was the no-summary trim — so a saved session shows what was kept.
"""

from __future__ import annotations

import json

from coworker.events import EventType
from coworker.providers import AssistantTurn

from tests.test_compaction_engine import (
    SUMMARY,
    CompactingProvider,
    collect,
    long_history,
    make_engine,
)


def _compacted_notices(engine):
    return [m for m in engine.messages if m.get("role") == "notice" and m.get("kind") == "compacted"]


def test_compacted_notice_carries_the_summary_and_state(tmp_path):
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=long_history(), cap=400)
    events = collect(engine)

    notices = _compacted_notices(engine)
    assert len(notices) == 1
    rec = notices[0]["compaction"]
    assert rec["summary_text"] == SUMMARY
    assert isinstance(rec["working_state"], str)
    assert isinstance(rec["boundary_index"], int) and rec["boundary_index"] > 0
    assert rec["model_used"] == "gpt-5.5"
    assert rec["trimmed"] is False
    assert rec["user_messages"] and rec["user_messages"][0].startswith("request")
    assert rec == engine.compaction_state.as_dict()
    json.dumps(notices[0])  # persisted as JSON, so it must serialise

    compacted = next(e for e in events if e.type == EventType.COMPACTED)
    assert compacted.data["compaction"]["summary_text"] == SUMMARY
    assert "summarized" in compacted.data["text"]


def test_trim_fallback_notice_says_so_without_a_summary(tmp_path):
    provider = CompactingProvider(
        [AssistantTurn(text="done", finish_reason="stop")], summary_fails=2
    )
    engine = make_engine(tmp_path, provider, messages=long_history(), cap=400)
    events = collect(engine)

    notices = _compacted_notices(engine)
    assert len(notices) == 1
    rec = notices[0]["compaction"]
    assert rec["trimmed"] is True
    # The trim path stores a placeholder telling the model no summary exists, not a
    # summary; the record keeps it verbatim so readers see what the model saw.
    assert "no summary" in rec["summary_text"].lower()
    compacted = next(e for e in events if e.type == EventType.COMPACTED)
    assert "trimmed" in compacted.data["text"].lower()
    assert compacted.data["compaction"]["trimmed"] is True


def test_notice_never_reaches_a_provider(tmp_path):
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=long_history(), cap=400)
    collect(engine)
    assert _compacted_notices(engine)
    for call in provider.summary_calls:
        assert all(m.get("role") != "notice" for m in call["messages"])
    assert all(m.get("role") != "notice" for m in engine._outbound_messages())
