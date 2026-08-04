"""Tests for the GUI-driven `create_automation` path (the "New automation" / template flow).

No network and no LLM: this exercises validation + that a valid create lands in the task store
with a freshly provisioned scratch workspace.
"""

from __future__ import annotations

from pathlib import Path

from coworker.server.manager import SessionManager


def _manager(tmp_path, monkeypatch) -> SessionManager:
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(data_dir=tmp_path / "data")


def test_create_automation_success(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    out = manager.create_automation(
        {
            "title": "Morning news briefing",
            "instructions": "Search the web and write a 5-bullet briefing.",
            "cron": "0 8 * * *",
        }
    )
    assert out["ok"] is True
    task = out["task"]
    assert task["title"] == "Morning news briefing"
    assert task["schedule"] == "Every day at ~8:00 AM"
    # it really landed in the store and is bound to a fresh scratch workspace
    saved = manager.task_store.get(task["id"])
    assert saved is not None
    assert saved.agent == "cowork"
    assert saved.sources == []
    assert saved.delivery == {"kind": "app"}
    assert Path(saved.workspace).is_dir()


def test_create_automation_uses_selected_enabled_agent(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)

    out = manager.create_automation(
        {
            "title": "Daily conversation review",
            "instructions": "Summarize the latest discussion.",
            "cron": "0 8 * * *",
            "agent": "ops",
        }
    )

    assert out["ok"] is True
    assert out["task"]["agent"] == "ops"
    assert manager.task_store.get(out["task"]["id"]).agent == "ops"


def test_create_automation_rejects_unknown_or_disabled_agent(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    base = {
        "title": "Daily review",
        "instructions": "Summarize the latest discussion.",
        "cron": "0 8 * * *",
    }

    unknown = manager.create_automation({**base, "agent": "missing-agent"})
    assert unknown == {"ok": False, "error": "unknown agent: missing-agent"}

    manager.personas.set_enabled("ops", False)
    disabled = manager.create_automation({**base, "agent": "ops"})
    assert disabled == {"ok": False, "error": "agent disabled: ops"}


def test_update_automation_changes_to_an_enabled_agent(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    created = manager.create_automation(
        {
            "title": "Daily review",
            "instructions": "Summarize the latest discussion.",
            "cron": "0 8 * * *",
        }
    )

    out = manager.update_automation(created["task"]["id"], {"agent": "ops"})

    assert out["ok"] is True
    assert out["task"]["agent"] == "ops"
    assert manager.task_store.get(created["task"]["id"]).agent == "ops"

    rejected = manager.update_automation(
        created["task"]["id"], {"agent": "missing-agent"}
    )
    assert rejected == {"ok": False, "error": "unknown agent: missing-agent"}
    assert manager.task_store.get(created["task"]["id"]).agent == "ops"


def test_create_automation_persists_sources_and_delivery(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(manager, "_provision_scratch", lambda _session_id: str(scratch))
    monkeypatch.setattr(
        "coworker.server.manager.connector_list",
        lambda _secrets: [
            {"name": "github", "connected": True},
            {"name": "slack", "connected": True},
        ],
    )
    out = manager.create_automation(
        {
            "title": "Engineering digest",
            "instructions": "Summarize the latest changes.",
            "cron": "0 9 * * 1",
            "sources": ["github"],
            "delivery": {
                "kind": "channel",
                "connector": "slack",
                "target": "slack:C0123",
            },
        }
    )
    assert out["ok"] is True
    saved = manager.task_store.get(out["task"]["id"])
    assert saved is not None
    assert saved.sources == ["github"]
    assert saved.delivery == {
        "kind": "channel",
        "connector": "slack",
        "target": "slack:C0123",
    }


def test_create_automation_accepts_feishu_delivery(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(manager, "_provision_scratch", lambda _session_id: str(scratch))
    monkeypatch.setattr(
        "coworker.server.manager.connector_list",
        lambda _secrets: [{"name": "feishu", "connected": True}],
    )
    out = manager.create_automation(
        {
            "title": "Daily brief",
            "instructions": "Summarize the day.",
            "cron": "0 9 * * *",
            "delivery": {
                "kind": "channel",
                "connector": "feishu",
                "target": "feishu:oc_chat_123",
            },
        }
    )
    assert out["ok"] is True
    saved = manager.task_store.get(out["task"]["id"])
    assert saved is not None
    assert saved.delivery == {
        "kind": "channel",
        "connector": "feishu",
        "target": "feishu:oc_chat_123",
    }


def test_create_automation_rejects_unconnected_source(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "coworker.server.manager.connector_list",
        lambda _secrets: [{"name": "github", "connected": False}],
    )
    out = manager.create_automation(
        {
            "title": "Digest",
            "instructions": "Summarize changes.",
            "cron": "0 9 * * *",
            "sources": ["github"],
        }
    )
    assert out["ok"] is False
    assert "connect source" in out["error"]


def test_create_automation_invalid_cron(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    out = manager.create_automation(
        {
            "title": "Bad",
            "instructions": "do something",
            "cron": "not-a-cron",
        }
    )
    assert out["ok"] is False
    assert "invalid cron" in out["error"]
    assert manager.task_store.list() == []


def test_create_automation_missing_instructions(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    out = manager.create_automation(
        {
            "title": "No instructions",
            "instructions": "  ",
            "cron": "0 8 * * *",
        }
    )
    assert out["ok"] is False
    assert "instructions" in out["error"]
    assert manager.task_store.list() == []


def test_create_automation_requires_schedule(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    out = manager.create_automation(
        {"title": "No schedule", "instructions": "do something"}
    )
    assert out["ok"] is False
    assert manager.task_store.list() == []
