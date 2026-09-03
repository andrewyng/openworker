"""Settings wiring for Auto-Approve (spec §1.5 / Part 6 step 3).

Covers the prefs-backed feature flag + shadow toggle: default off, REST round-trip,
persistence across a manager restart, config.toml fallback, and the build-engine override
so a flag flip takes effect on the next session build without a config edit.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from coworker.server.app import create_app
from coworker.server.manager import SessionManager


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolate config.toml: no hand-set flag leaking in from the dev machine.
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    return TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))


def test_flags_default_off(client):
    s = client.get("/v1/settings").json()
    assert s["auto_approve"] is False
    assert s["auto_approve_shadow"] is False


def test_set_auto_approve_roundtrip(client):
    r = client.post("/v1/settings/auto-approve", json={"auto_approve": True}).json()
    assert r["ok"] and r["auto_approve"] is True
    assert client.get("/v1/settings").json()["auto_approve"] is True
    # Off again.
    client.post("/v1/settings/auto-approve", json={"auto_approve": False})
    assert client.get("/v1/settings").json()["auto_approve"] is False


def test_shadow_is_independent_of_the_live_flag(client):
    client.post("/v1/settings/auto-approve-shadow", json={"auto_approve_shadow": True})
    s = client.get("/v1/settings").json()
    assert s["auto_approve"] is False  # untouched
    assert s["auto_approve_shadow"] is True


def test_flags_persist_across_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    c1 = TestClient(create_app(SessionManager(data_dir=data_dir)))
    c1.post("/v1/settings/auto-approve", json={"auto_approve": True})

    reborn = SessionManager(data_dir=data_dir)
    assert reborn.auto_approve() is True
    assert TestClient(create_app(reborn)).get("/v1/settings").json()["auto_approve"] is True


def test_prefs_falls_back_to_config_when_unset(tmp_path, monkeypatch):
    # No prefs key set → the manager reads config.toml. Point config at a file that enables it.
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "config.toml").write_text("auto_approve = true\n")
    monkeypatch.setenv("COWORKER_STATE_DIR", str(state))
    mgr = SessionManager(data_dir=tmp_path / "data")
    assert mgr.auto_approve() is True  # config value, no prefs entry
    # A prefs write then wins over config.
    mgr.set_auto_approve(False)
    assert mgr.auto_approve() is False


def test_build_engine_override_beats_config(tmp_path, monkeypatch):
    # build_engine's auto_approve arg (what the server passes) overrides the config value.
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker.agent import build_engine
    from coworker.agents.chat import chat_agent

    # config has it off by default; override to on → a reviewer is attached.
    engine = build_engine(agent=chat_agent(), auto_approve=True, auto_approve_shadow=False)
    assert engine.reviewer is not None
    engine2 = build_engine(agent=chat_agent(), auto_approve=False, auto_approve_shadow=False)
    assert engine2.reviewer is None


# -- metering (§1.7): durable reviewer stats from the audit store ------------------


def test_reviewer_stats_aggregates_by_stage(tmp_path):
    from coworker.audit import AuditStore

    store = AuditStore(tmp_path / "audit.db")
    sid = "s1"
    rows = [
        {"session_id": sid, "tool": "run_shell", "stage": "reviewer_verdict", "status": "allow", "tokens_in": 100, "tokens_out": 20},
        {"session_id": sid, "tool": "run_shell", "stage": "reviewer_verdict", "status": "allow", "tokens_in": 110, "tokens_out": 25},
        {"session_id": sid, "tool": "web_fetch", "stage": "reviewer_verdict", "status": "deny", "tokens_in": 90, "tokens_out": 30},
        {"session_id": sid, "tool": "write_file", "stage": "reviewer_verdict", "status": "unsure", "tokens_in": 80, "tokens_out": 15},
        {"session_id": sid, "tool": "write_file", "stage": "reviewer_shadow", "status": "allow", "tokens_in": 70, "tokens_out": 10},
        # Other sessions and stages must not leak in.
        {"session_id": "other", "tool": "run_shell", "stage": "reviewer_verdict", "status": "allow", "tokens_in": 999, "tokens_out": 999},
        {"session_id": sid, "tool": "run_shell", "stage": "finished", "status": "ok"},
    ]
    for r in rows:
        store.append(r)

    stats = store.reviewer_stats(sid)
    assert stats["live"] == {
        "checks": 4, "allow": 2, "deny": 1, "unsure": 1,
        "tokens_in": 380, "tokens_out": 90,
        "cache_read": 0, "cache_write": 0,
    }
    assert stats["shadow"]["checks"] == 1 and stats["shadow"]["allow"] == 1
    store.close()


def test_audit_migration_adds_columns_to_a_legacy_db(tmp_path):
    import sqlite3

    from coworker.audit import AuditStore

    # A pre-2026-08-12 database without the reviewer columns.
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            session_id TEXT, agent TEXT, workspace TEXT, connector TEXT, tool TEXT,
            stage TEXT, status TEXT, approval TEXT, args TEXT, result_preview TEXT,
            reason TEXT, resource TEXT)"""
    )
    conn.execute(
        "INSERT INTO audit_events (session_id, tool, stage, status) VALUES ('s1','t','finished','ok')"
    )
    conn.commit()
    conn.close()

    store = AuditStore(db)  # opening migrates
    store.append(
        {"session_id": "s1", "tool": "run_shell", "stage": "reviewer_verdict",
         "status": "allow", "tokens_in": 50, "tokens_out": 9, "call_id": "c1"}
    )
    stats = store.reviewer_stats("s1")
    assert stats["live"]["checks"] == 1 and stats["live"]["tokens_in"] == 50
    row = [r for r in store.list(session_id="s1") if r["stage"] == "reviewer_verdict"][0]
    assert row["call_id"] == "c1"
    store.close()


def test_reviewer_stats_endpoint(client):
    empty = client.get("/v1/sessions/nope/reviewer-stats").json()
    assert empty["live"]["checks"] == 0 and empty["shadow"]["checks"] == 0


def test_reviewer_stats_carry_the_cached_share(tmp_path):
    # The badge could only ever see FRESH tokens (~75 of a ~1,500-token check once the
    # provider caches the instruction prefix), so it under-reported cost by more the
    # longer a session ran. The cached share now rides every verdict row into the sums.
    from coworker.audit import AuditStore

    store = AuditStore(tmp_path / "audit.db")
    for _ in range(3):
        store.append(
            {
                "session_id": "s1",
                "tool": "run_shell",
                "stage": "reviewer_verdict",
                "status": "allow",
                "tokens_in": 75,
                "tokens_out": 60,
                "cache_read": 1400,
                "cache_write": 0,
            }
        )
    live = store.reviewer_stats("s1")["live"]
    assert live["checks"] == 3
    assert (live["tokens_in"], live["tokens_out"]) == (225, 180)
    assert (live["cache_read"], live["cache_write"]) == (4200, 0)


def test_existing_databases_gain_the_cache_columns(tmp_path):
    # A database created before 2026-08-22 has no cache columns. Opening it must migrate
    # in place — old rows read as zero, new rows record the real figures.
    import sqlite3

    from coworker.audit import AuditStore

    db = tmp_path / "audit.db"
    con = sqlite3.connect(db)
    con.execute(
        """
        CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            session_id TEXT, agent TEXT, workspace TEXT, connector TEXT,
            tool TEXT, stage TEXT, status TEXT, approval TEXT, args TEXT,
            result_preview TEXT, reason TEXT, resource TEXT, call_id TEXT,
            tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0
        )
        """
    )
    con.execute(
        "INSERT INTO audit_events (session_id, tool, stage, status, tokens_in, tokens_out)"
        " VALUES ('s1', 'run_shell', 'reviewer_verdict', 'allow', 100, 50)"
    )
    con.commit()
    con.close()

    store = AuditStore(db)  # migration happens on open
    store.append(
        {
            "session_id": "s1",
            "tool": "run_shell",
            "stage": "reviewer_verdict",
            "status": "allow",
            "tokens_in": 75,
            "tokens_out": 60,
            "cache_read": 1400,
            "cache_write": 25,
        }
    )
    live = store.reviewer_stats("s1")["live"]
    assert live["checks"] == 2
    assert (live["tokens_in"], live["tokens_out"]) == (175, 110)
    # The pre-migration row contributes zero cached, not garbage.
    assert (live["cache_read"], live["cache_write"]) == (1400, 25)
