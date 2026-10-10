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
