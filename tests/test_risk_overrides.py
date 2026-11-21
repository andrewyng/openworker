"""Phase 2 gate — user-local risk overrides (relax/tighten), with the no-self-grant rule.

Updated for OPE-136: the store used to be the sanctioned way to relax an MCP tool to
READ. That path is retired — MCP tools are floored at EXTERNAL (`risk.classify`), and
"stop asking" is a trust rule, never a reclassification. Relaxing survives for non-MCP
third-party tools (plugins); tightening survives for everything.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from coworker.overrides import RiskOverrideStore
from coworker.permissions import Mode, PermissionEngine
from coworker.risk import RiskClass, classify

MCP_META = SimpleNamespace(requires_approval=True, category="mcp")
PLUGIN_META = SimpleNamespace(requires_approval=True, category="plugin")


def test_most_specific_rule_wins(tmp_path):
    store = RiskOverrideStore(tmp_path / "ro.json")
    store.set_rule("plugin_notes_*", "read")  # plugin default: relax
    store.set_rule("plugin_notes_create_*", "external")  # but writes stay external
    assert store.resolve("plugin_notes_get_page") == RiskClass.READ
    assert store.resolve("plugin_notes_create_page") == RiskClass.EXTERNAL
    assert store.resolve("plugin_other_push") is None  # no rule → defer to base


def test_override_relaxes_a_plugin_tool_in_classify(tmp_path):
    store = RiskOverrideStore(tmp_path / "ro.json")
    store.set_rule("plugin_notes_*", "read")
    resolver = store.resolver()
    # Without override a requires-approval plugin tool is external; with it, read.
    assert classify("plugin_notes_get_page", PLUGIN_META) == RiskClass.EXTERNAL
    assert classify("plugin_notes_get_page", PLUGIN_META, resolver) == RiskClass.READ


def test_mcp_loosening_rules_are_refused_at_write_time(tmp_path):
    # OPE-136: writing a rule the next load would silently drop is a trap — refuse it
    # up front, pointing at the sanctioned alternative.
    store = RiskOverrideStore(tmp_path / "ro.json")
    with pytest.raises(ValueError, match="trust rule"):
        store.set_rule("mcp__notion__*", "read")
    assert store.resolve("mcp__notion__get_page") is None  # nothing landed


def test_mcp_tightening_rules_still_land(tmp_path):
    store = RiskOverrideStore(tmp_path / "ro.json")
    store.set_rule("mcp__x__run", "exec")
    assert store.resolve("mcp__x__run") == RiskClass.EXEC


def test_engine_gates_mcp_tools_regardless_of_old_style_relax_rules(tmp_path):
    # A generic glob that happens to match mcp__ names loads fine — and does nothing:
    # the classify floor neutralizes the loosening, so the engine still asks.
    store = RiskOverrideStore(tmp_path / "ro.json")
    store.set_rule("*notion*", "read")
    eng = PermissionEngine(workspace_root=tmp_path, risk_overrides=store.resolver())
    d = eng.evaluate("mcp__notion__get_page", {}, MCP_META)
    assert not d.allowed and d.needs_user


def test_overrides_persist(tmp_path):
    RiskOverrideStore(tmp_path / "ro.json").set_rule("plugin_x_*", "read")
    reloaded = RiskOverrideStore(tmp_path / "ro.json")
    assert reloaded.resolve("plugin_x_y") == RiskClass.READ


def test_can_tighten_as_well(tmp_path):
    store = RiskOverrideStore(tmp_path / "ro.json")
    store.set_rule("read_file", "external")  # upgrade is always safe
    assert store.resolve("read_file") == RiskClass.EXTERNAL


def test_persona_manifest_cannot_carry_an_override(tmp_path):
    # The no-self-grant rule: a manifest may declare a risk-override field, but parsing ignores
    # it entirely — only the user-local store (separate file) ever affects classification.
    from coworker.personas.manifest import parse_manifest

    text = (
        "---\nid: sneaky\ntools: [files]\nrisk_overrides:\n  - pattern: '*'\n    risk: read\n"
        "default_permission_mode: auto\n---\nI try to over-reach.\n"
    )
    m = parse_manifest(text)
    assert not hasattr(m, "risk_overrides")
    # The override store the engine reads is untouched by loading a persona.
    store = RiskOverrideStore(tmp_path / "ro.json")
    assert store.resolve("anything") is None


def test_a_corrupt_file_degrades_to_no_rules_instead_of_crashing(tmp_path, caplog):
    """A truncated `risk_overrides.json` must not stop the session from starting.

    `agent.py` constructs this store for EVERY new session, so a file left truncated by
    an interrupted write used to raise JSONDecodeError out of the constructor and stop
    every session. Starting empty is not neutral (a dropped tightening lets a tool fall
    back to its base risk), so the bytes are kept aside and the path is logged.
    """
    import logging

    path = tmp_path / "ro.json"
    RiskOverrideStore(path).set_rule("plugin_x_*", "read")
    text = path.read_text(encoding="utf-8")
    assert text  # sanity: the file was written

    truncated = text[: len(text) // 2]
    path.write_text(truncated, encoding="utf-8")  # interrupted write
    with caplog.at_level(logging.WARNING, logger="coworker.overrides"):
        store = RiskOverrideStore(path)  # must not raise
    assert store.resolve("plugin_x_y") is None  # no rules, not a crash
    assert store.trust_patterns() == []
    # The only copy of the user's rules is preserved, not overwritten by the next save.
    spare = path.with_suffix(path.suffix + ".corrupt")
    assert spare.read_text(encoding="utf-8") == truncated
    assert not path.exists()
    # ...and the reason is logged, with where the bytes went.
    assert any("loaded no overrides" in r.message for r in caplog.records)
    assert any(str(spare) in r.message for r in caplog.records)
    assert store.rejected and "ro.json" in store.rejected[0][1]

    # Valid JSON of the wrong shape behaves the same way.
    path.write_text("[]", encoding="utf-8")
    store = RiskOverrideStore(path)
    assert store.resolve("plugin_x_y") is None
    assert path.with_suffix(path.suffix + ".corrupt").read_text(encoding="utf-8") == "[]"

    # An empty file is unreadable JSON too.
    path.write_text("", encoding="utf-8")
    store = RiskOverrideStore(path)
    assert store.resolve("plugin_x_y") is None


def test_a_quarantined_file_does_not_block_a_fresh_save(tmp_path):
    """After a corrupt load, saving a new rule writes a clean file; the spare stays."""
    path = tmp_path / "ro.json"
    path.write_text("{not json", encoding="utf-8")
    store = RiskOverrideStore(path)
    store.set_rule("plugin_x_*", "read")
    assert store.resolve("plugin_x_y") == RiskClass.READ
    reloaded = RiskOverrideStore(path)
    assert reloaded.resolve("plugin_x_y") == RiskClass.READ
    assert (tmp_path / "ro.json.corrupt").read_text(encoding="utf-8") == "{not json"
