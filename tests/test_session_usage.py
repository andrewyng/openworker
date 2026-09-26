"""Token counting (connectors-across-machines spec §5): per-model totals persisted on the
session record, exposed on the sessions list, and one content-blind audit row per model
round-trip so exported token columns are honest. No dollars anywhere."""

from __future__ import annotations

import asyncio

import aisuite as ai

from coworker.engine import EventType, TurnEngine
from coworker.permissions import PermissionEngine
from coworker.sessions import SessionRecord, usage_totals
from coworker.tools import ToolRegistry
from tests.test_persona_connections import _mgr
from tests.test_token_usage import _UsageProvider


def test_usage_totals_folds_assistant_sidecars_by_model():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "a", "usage": {"model": "m1", "input": 10, "output": 2, "cache_read": 1, "cache_write": 0}},
        {"role": "assistant", "content": "b", "usage": {"model": "m1", "input": 5, "output": 1, "cache_read": 0, "cache_write": 3}},
        {"role": "assistant", "content": "c", "usage": {"model": "m2", "input": 7, "output": 7}},
        {"role": "assistant", "content": "no sidecar"},
        {"role": "assistant", "content": "junk", "usage": {"model": "m2", "input": "x", "output": -4}},
    ]
    t = usage_totals(msgs)
    assert t["m1"] == {"input": 15, "output": 3, "cache_read": 1, "cache_write": 3, "turns": 2}
    assert t["m2"] == {"input": 7, "output": 7, "cache_read": 0, "cache_write": 0, "turns": 2}
    assert usage_totals([]) == {}


def test_save_persists_totals_and_the_list_exposes_them(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    engine = mgr.get_engine("s-usage", agent="cowork")
    assert engine is not None
    engine.messages.append({"role": "user", "content": "hi"})
    engine.messages.append(
        {"role": "assistant", "content": "a", "usage": {"model": "openai:gpt-5.5", "input": 100, "output": 20, "cache_read": 5, "cache_write": 0}}
    )
    engine.messages.append(
        {"role": "assistant", "content": "b", "usage": {"model": "openai:gpt-5.5", "input": 50, "output": 10, "cache_read": 0, "cache_write": 0}}
    )
    mgr.save("s-usage", engine)
    # Round-trips through the store column…
    loaded = mgr.session_store.load("s-usage")
    assert loaded is not None
    assert loaded.usage == {"openai:gpt-5.5": {"input": 150, "output": 30, "cache_read": 5, "cache_write": 0, "turns": 2}}
    # …and the list row carries it for the Team panel roll-up.
    row = next(r for r in mgr.list_sessions() if r["session_id"] == "s-usage")
    assert row["usage"]["openai:gpt-5.5"]["input"] == 150
    # A re-save never double counts (folded from the transcript, not accumulated).
    mgr.save("s-usage", engine)
    assert mgr.session_store.load("s-usage").usage["openai:gpt-5.5"]["turns"] == 2
    # A record saved without messages keeps whatever it carried.
    mgr.session_store.save(SessionRecord(session_id="s-plain", workspace=str(tmp_path), model="m", mode="interactive"))
    assert mgr.session_store.load("s-plain").usage == {}


def test_engine_audits_one_usage_row_per_round_trip(tmp_path):
    rows: list[dict] = []
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    engine = TurnEngine(
        provider=_UsageProvider(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        audit_sink=rows.append,
    )
    engine.audit_context = {"session_id": "s1", "agent": "cowork", "workspace": str(tmp_path)}

    async def _collect():
        return [ev async for ev in engine.run("hello")]

    events = asyncio.run(_collect())
    assert any(ev.type == EventType.ASSISTANT_MESSAGE for ev in events)
    usage_rows = [r for r in rows if r.get("stage") == "usage"]
    assert len(usage_rows) == 1
    r = usage_rows[0]
    assert r["session_id"] == "s1" and r["tool"] == ""
    assert r["arguments"] == {"model": "gpt-5.5"}
    assert (r["tokens_in"], r["tokens_out"], r["cache_read"], r["cache_write"]) == (100, 20, 5, 0)
    # Content-blind: no message text rides the row.
    assert "hello" not in repr(r) and "done" not in repr(r)
