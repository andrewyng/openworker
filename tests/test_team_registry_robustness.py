"""TeamRegistry: a corrupt or unfamiliar teams.json must not brick server startup.

The registry is the roster the wake plumbing walks every tick, and
``SessionManager.__init__`` builds it — so a single unreadable ``teams.json`` raised
straight out of the constructor and the server did not start. Three ways in:

1. A torn file → ``JSONDecodeError``; valid JSON that is not an object (``[]``,
   ``null``, a string) → ``AttributeError`` on ``.get``.
2. A row this build cannot construct: an unknown field (a later build writing
   ``strategy``/``reasons``/``integrations``, which open PR #765 adds to ``Team``),
   a missing required one, or a row that is not an object at all → ``TypeError``/
   ``AttributeError`` out of the comprehension.
3. ``_save`` wrote in place, so a crash mid-write produced the torn file those loads
   then meet.

A team row is dropped, not the whole roster: losing one team must not lose the other
n. Whatever is dropped keeps its bytes in ``teams.json.corrupt`` (never deleted,
never overwritten by a later save) and the warning names the reason — the shape the
risk-override, connection and parked stores use.
"""

from __future__ import annotations

import json
import logging

from coworker.teams.registry import TeamRegistry

_WORKER = {"actor": "w1", "persona": "p", "session_id": "S1"}
_TEAM = {
    "team_id": "t1",
    "space": "s1",
    "lead_session": "L1",
    "lead_actor": "lead",
    "workers": [_WORKER],
}
_TEAM2 = {**_TEAM, "team_id": "t2", "lead_session": "L2", "lead_actor": "lead2"}


def _write(tmp_path, body: str):
    path = tmp_path / "teams.json"
    path.write_text(body, encoding="utf-8")
    return path


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


def test_a_good_file_still_loads(tmp_path, caplog):
    path = _write(tmp_path, json.dumps({"teams": [_TEAM, _TEAM2]}))
    with caplog.at_level(logging.WARNING, logger="coworker.teams.registry"):
        reg = TeamRegistry(path)
    assert [t.team_id for t in reg.all()] == ["t1", "t2"]
    assert reg.get("t1").workers[0].actor == "w1"
    assert _warnings(caplog) == []


def test_a_torn_file_starts_empty_and_keeps_the_bytes(tmp_path, caplog):
    body = '{"team_id": "t1"'
    path = _write(tmp_path, body)
    with caplog.at_level(logging.WARNING, logger="coworker.teams.registry"):
        reg = TeamRegistry(path)
    assert reg.all() == []
    assert path.with_suffix(path.suffix + ".corrupt").read_text(encoding="utf-8") == body
    assert any(str(path) in m for m in _warnings(caplog))


def test_valid_json_that_is_not_an_object_starts_empty_and_keeps_the_bytes(tmp_path, caplog):
    for body in ("[]", "null", '"hello"'):
        path = _write(tmp_path, body)
        with caplog.at_level(logging.WARNING, logger="coworker.teams.registry"):
            reg = TeamRegistry(path)
        assert reg.all() == [], body
        assert path.with_suffix(path.suffix + ".corrupt").read_text(encoding="utf-8") == body
        assert any(str(path) in m for m in _warnings(caplog))


def test_a_row_with_an_unknown_field_does_not_take_the_other_teams(tmp_path, caplog):
    """A build that added a field wrote it, and this one used to refuse the whole
    roster with `TypeError`. The field name is deliberately one no build is likely
    to add — `strategy` would work today but stop being unknown the moment the open
    PR that adds it to `Team` merges."""
    odd = {**_TEAM, "team_id": "odd", "field_from_a_future_build": "x"}
    path = _write(tmp_path, json.dumps({"teams": [_TEAM, odd, _TEAM2]}))
    with caplog.at_level(logging.WARNING, logger="coworker.teams.registry"):
        reg = TeamRegistry(path)

    assert [t.team_id for t in reg.all()] == ["t1", "t2"]
    spare = path.with_suffix(path.suffix + ".corrupt")
    assert "field_from_a_future_build" in spare.read_text(encoding="utf-8")
    assert any(str(path) in m for m in _warnings(caplog))


def test_a_row_missing_a_required_field_is_skipped_not_fatal(tmp_path, caplog):
    odd = {k: v for k, v in _TEAM.items() if k != "lead_actor"}
    odd["team_id"] = "odd"
    path = _write(tmp_path, json.dumps({"teams": [_TEAM, odd]}))
    with caplog.at_level(logging.WARNING, logger="coworker.teams.registry"):
        reg = TeamRegistry(path)
    assert [t.team_id for t in reg.all()] == ["t1"]
    assert any(str(path) in m for m in _warnings(caplog))


def test_a_row_that_is_not_an_object_is_skipped_not_fatal(tmp_path, caplog):
    path = _write(tmp_path, json.dumps({"teams": [_TEAM, "not a team", 7]}))
    with caplog.at_level(logging.WARNING, logger="coworker.teams.registry"):
        reg = TeamRegistry(path)
    assert [t.team_id for t in reg.all()] == ["t1"]
    assert any(str(path) in m for m in _warnings(caplog))


def test_teams_of_the_wrong_shape_starts_empty(tmp_path, caplog):
    path = _write(tmp_path, json.dumps({"teams": {"a": 1}}))
    with caplog.at_level(logging.WARNING, logger="coworker.teams.registry"):
        reg = TeamRegistry(path)
    assert reg.all() == []
    assert any(str(path) in m for m in _warnings(caplog))


def test_a_quarantined_file_does_not_block_a_fresh_save(tmp_path):
    path = _write(tmp_path, '{"team_id": "t1"')
    reg = TeamRegistry(path)
    team = reg.create(space="s1", lead_session="L1", lead_actor="lead", workers=[])

    assert TeamRegistry(path).get(team.team_id).space == "s1"
    assert path.with_suffix(path.suffix + ".corrupt").read_text(encoding="utf-8") == '{"team_id": "t1"'


def test_save_is_atomic_and_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "teams.json"
    reg = TeamRegistry(path)
    reg.create(space="s1", lead_session="L1", lead_actor="lead", workers=[])
    assert json.loads(path.read_text(encoding="utf-8"))["teams"]
    assert not path.with_suffix(".json.tmp").exists()


def test_a_failed_save_keeps_the_previous_file_and_the_in_memory_team(tmp_path, monkeypatch):
    body = json.dumps({"teams": [_TEAM]})
    path = _write(tmp_path, body)
    reg = TeamRegistry(path)

    def refuse(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(type(path), "replace", refuse)
    reg.set_paused("t1", True)  # must not raise into the wake gate
    assert reg.get("t1").paused is True
    # The file on disk is untouched — byte-identical to what was there before.
    assert path.read_text(encoding="utf-8") == body
    assert not path.with_suffix(".json.tmp").exists()
