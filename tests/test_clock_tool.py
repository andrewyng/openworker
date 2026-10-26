"""OPE-192: the time is a tool (`current_time`), not a line in the per-turn context block.
The block must be byte-identical turn to turn so the provider's prompt cache keeps
working; a value that changes by itself has no place in it."""

from __future__ import annotations

from datetime import datetime, timezone

from coworker.clock import clock_tools, current_time


def test_current_time_reports_local_and_utc_for_the_same_instant():
    before = datetime.now(timezone.utc)
    out = current_time()
    after = datetime.now(timezone.utc)
    local = datetime.fromisoformat(out["local"])
    utc = datetime.fromisoformat(out["utc"].replace("Z", "+00:00"))
    assert local.tzinfo is not None and utc.tzinfo is not None
    assert local == utc  # same instant, two renderings
    assert before.replace(microsecond=0) <= utc <= after
    assert out["weekday"] == local.strftime("%A")
    assert isinstance(out["timezone"], str)


def test_clock_tools_exposes_exactly_current_time():
    assert [t.__name__ for t in clock_tools()] == ["current_time"]


def test_current_time_is_registered_for_every_persona(tmp_path, monkeypatch):
    from coworker.agent import build_engine
    from coworker.agents.registry import get_agent
    from coworker.permissions import Mode

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COWORKER_SCRATCH_BASE", str(tmp_path / "scratch"))
    ws = tmp_path / "ws"
    ws.mkdir()
    for persona in ("cowork", "code"):
        engine = build_engine(agent=get_agent(persona), workspace=ws, model="gpt-5.5", mode=Mode("bypass-approvals"))
        assert "current_time" in set(engine.registry.names()), persona


def test_context_block_carries_no_clock_and_is_byte_stable(tmp_path, monkeypatch):
    from coworker.agent import build_engine
    from coworker.agents.registry import get_agent
    from coworker.permissions import Mode

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COWORKER_SCRATCH_BASE", str(tmp_path / "scratch"))
    ws = tmp_path / "ws"
    ws.mkdir()
    engine = build_engine(agent=get_agent("cowork"), workspace=ws, model="gpt-5.5", mode=Mode("bypass-approvals"))
    first = engine.context_provider()
    assert "Now:" not in first
    assert str(ws) in first  # the folders are still there
    assert engine.context_provider() == first  # byte-identical turn to turn


def test_environment_block_points_at_the_tool(tmp_path):
    from coworker.environment import environment_context

    block = environment_context(tmp_path)
    assert "Today's date:" in block and "current_time" in block
