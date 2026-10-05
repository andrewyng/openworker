"""Obsidian connector: vault validation, note resolution (paths/titles/wikilinks),
search/tags/frontmatter, backlinks, daily notes, sandboxed writes, and the
obsidian:// hand-off. All against a real tmp vault — no Obsidian needed."""

from __future__ import annotations

import json

import pytest

from coworker.connectors import obsidian_tools as ob
from coworker.connectors.descriptors import get_descriptor


@pytest.fixture()
def vault(tmp_path):
    v = tmp_path / "MyVault"
    (v / ".obsidian").mkdir(parents=True)
    (v / "Projects").mkdir()
    (v / "Daily").mkdir()
    (v / "Launch Plan.md").write_text(
        "---\ntags: [launch, planning]\n---\n"
        "# Launch Plan\nShip [[Pricing]] before the keynote. #urgent\n",
        encoding="utf-8",
    )
    (v / "Projects" / "Pricing.md").write_text(
        "Tiered pricing draft. See [[Launch Plan]].\n", encoding="utf-8"
    )
    (v / "Daily" / "2026-07-20.md").write_text("- stood up\n", encoding="utf-8")
    (v / ".obsidian" / "daily-notes.json").write_text(
        json.dumps({"folder": "Daily", "format": "YYYY-MM-DD"}), encoding="utf-8"
    )
    (v / ".obsidian" / "hidden.md").write_text("never index me", encoding="utf-8")
    return v


def test_descriptor_validates_real_vault(vault, tmp_path):
    d = get_descriptor("obsidian")
    assert d is not None and d.auth == "folder"
    ok = d.validate({"vault_path": str(vault)})
    assert ok.ok and ok.identity == "MyVault"
    assert not d.validate({"vault_path": str(tmp_path / "nope")}).ok
    plain = tmp_path / "plain"
    plain.mkdir()
    res = d.validate({"vault_path": str(plain)})
    assert not res.ok and ".obsidian" in (res.error or "")


def test_resolve_by_path_title_and_wikilink(vault):
    by_path = ob.resolve_note(vault, "Projects/Pricing.md")
    by_title = ob.resolve_note(vault, "pricing")
    by_link = ob.resolve_note(vault, "[[Pricing|the pricing note]]")
    assert by_path == by_title == by_link
    assert ob.resolve_note(vault, "No Such Note") is None


def test_search_scores_title_tag_content(vault):
    top = ob.search_notes(vault, "pricing")["notes"][0]
    assert top["title"] == "Pricing"  # title hit outranks the content mention
    tagged = ob.search_notes(vault, "launch", tag="urgent")
    assert [n["title"] for n in tagged["notes"]] == ["Launch Plan"]
    assert ob.search_notes(vault, "hidden")["count"] == 0  # .obsidian never indexed


def test_read_note_frontmatter_tags_links(vault):
    note = ob.read_note(vault, "Launch Plan")
    assert note["frontmatter"]["tags"] == ["launch", "planning"]
    assert set(note["tags"]) == {"launch", "planning", "urgent"}
    assert note["links"] == ["Pricing"]
    assert "keynote" in note["content"]


def test_backlinks(vault):
    result = ob.backlinks(vault, "Pricing")
    assert result["count"] == 1 and result["backlinks"][0]["title"] == "Launch Plan"


def test_daily_note_honors_vault_config(vault):
    assert "stood up" in ob.daily_note(vault, "2026-07-20")["content"]
    missing = ob.daily_note(vault, "2026-07-19")
    assert (
        "no daily note" in missing["error"]
        and "Daily/2026-07-19.md" in missing["error"]
    )
    assert "invalid date" in ob.daily_note(vault, "today")["error"]


def test_write_modes_and_sandbox(vault):
    appended = ob.write_note(vault, "Launch Plan", "New line.")
    assert appended["ok"] and appended["mode"] == "append"
    assert (vault / "Launch Plan.md").read_text().endswith("New line.")

    created = ob.write_note(vault, "Inbox/Idea", "A thought.", mode="append")
    assert created["ok"] and created["path"] == "Inbox/Idea.md"  # append→create

    dup = ob.write_note(vault, "Pricing", "x", mode="create")
    assert "already exists" in dup["error"]

    escape = ob.write_note(vault, "../outside", "nope")
    assert "escapes the vault" in escape["error"]


def test_open_in_obsidian_builds_url(vault, monkeypatch):
    seen = {}
    monkeypatch.setattr(ob, "_launch", lambda url: seen.setdefault("url", url) and None)
    result = ob.open_in_obsidian(vault, "Pricing")
    assert result["ok"] and result["opened"] == "Projects/Pricing"
    assert seen["url"] == "obsidian://open?vault=MyVault&file=Projects/Pricing"
    assert "not found" in ob.open_in_obsidian(vault, "ghost")["error"]


def test_open_in_obsidian_launch_failure_is_reported(vault, monkeypatch):
    monkeypatch.setattr(ob, "_launch", lambda url: "no handler for obsidian://")
    result = ob.open_in_obsidian(vault, "Pricing")
    assert "could not open Obsidian" in result["error"] and result["url"]


def test_integration_tools_wire_and_guard(vault, tmp_path, monkeypatch):
    from coworker.connectors.integration_tools import make_integration_tools
    from coworker.secrets import SecretStore

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    secrets = SecretStore()
    tools = {t.__name__: t for t in make_integration_tools(secrets)}
    for name in (
        "obsidian_search_notes",
        "obsidian_read_note",
        "obsidian_list_notes",
        "obsidian_backlinks",
        "obsidian_daily_note",
        "obsidian_write_note",
        "open_in_obsidian",
    ):
        assert name in tools, name

    # Not connected → a visible error, never a crash.
    assert "error" in tools["obsidian_search_notes"]("pricing")

    secrets.put("obsidian:default", {"vault_path": str(vault), "enabled": True})
    hits = tools["obsidian_search_notes"]("pricing")
    assert hits["notes"][0]["title"] == "Pricing"

    # Vault moved after connect → reconnect hint, not a stack trace.
    secrets.put("obsidian:default", {"vault_path": str(tmp_path / "gone")})
    assert "reconnect" in tools["obsidian_read_note"]("Pricing")["error"]
