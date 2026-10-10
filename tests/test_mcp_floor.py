"""The MCP floor (OPE-136): no config value may drop an `mcp__*` tool below EXTERNAL.

Before this floor, `requires_approval: false` in mcp.json reclassified a whole server's
tools to READ — which skipped not just the approval card but the Discuss-mode denial, the
Auto-approve reviewer, and the audit trail, in one config line. The flag now only ever
waives the CARD (the gate's trusted-MCP branch); the class is welded on.

Pinned the same way OPE-111's catalog floor is pinned: these tests are the invariant.
"""
from __future__ import annotations

import json

import pytest

from coworker.permissions import Mode, PermissionEngine
from coworker.risk import RiskClass, classify


class Meta:
    """The shape mcp/tools.py stamps on every MCP callable."""

    def __init__(self, requires_approval: bool, category: str = "mcp") -> None:
        self.requires_approval = requires_approval
        self.category = category


# -- the pinned invariant ---------------------------------------------------------
# No config value, override rule, or metadata may classify an mcp-category tool below
# EXTERNAL. If a refactor reopens the trapdoor, this is the test that goes red.
@pytest.mark.parametrize(
    "metadata",
    [Meta(True), Meta(False), None],
    ids=["flag_true", "flag_false_the_trapdoor", "no_metadata_fails_closed"],
)
def test_mcp_tools_never_classify_below_external(metadata):
    assert classify("mcp__anyserver__anytool", metadata) is RiskClass.EXTERNAL


def test_a_loosening_override_cannot_beat_the_floor():
    # Even a rule the loader somehow let through (e.g. a generic glob) is neutralized
    # by classify's tighten-only comparison.
    assert (
        classify("mcp__srv__tool", Meta(False), overrides=lambda name: RiskClass.READ)
        is RiskClass.EXTERNAL
    )


def test_a_tightening_override_still_works():
    assert (
        classify("mcp__srv__tool", Meta(True), overrides=lambda name: RiskClass.EXEC)
        is RiskClass.EXEC
    )


# -- the floor is keyed on the mcp family, nothing wider --------------------------
def test_non_mcp_metadata_tools_keep_the_relaxed_path():
    # A plugin/aisuite tool that declares itself approval-free is still READ — the
    # floor must not quietly tighten every third-party tool in the app.
    assert classify("plugin_notes_search", Meta(False, category="plugin")) is RiskClass.READ


def test_catalog_backed_connector_reads_stay_free():
    # The §42 one-click connectors share the mcp__ naming but are relabeled
    # category="connector" at wiring (server/manager.py) because the catalog pins
    # their kinds first-hand — §36's "connector reads never gate" keeps applying.
    assert (
        classify("mcp__jira__getJiraIssue", Meta(False, category="connector"))
        is RiskClass.READ
    )
    # Catalog WRITES keep the OPE-111 floor regardless of the flag.
    assert (
        classify("mcp__jira__createJiraIssue", Meta(False, category="connector"))
        is RiskClass.EXTERNAL
    )


def test_the_reverse_name_collision_is_closed():
    # A CUSTOM server that reuses a catalog read name (server nicknamed "jira") keeps
    # category "mcp" and lands on the floor — it does not inherit the catalog's
    # first-party read verdict.
    assert classify("mcp__jira__getJiraIssue", Meta(False)) is RiskClass.EXTERNAL


def test_the_name_coincidence_no_longer_matters():
    # Pre-floor, mcp__atlassian__createJiraIssue (unknown to the catalog) dropped to
    # READ on flag false while its mcp__jira__ twin survived via catalog string luck.
    assert classify("mcp__atlassian__createJiraIssue", Meta(False)) is RiskClass.EXTERNAL


# -- the override loader refuses what classify would silently ignore ---------------
def test_explicit_mcp_loosening_rules_are_rejected_at_load(tmp_path):
    from coworker.overrides import RiskOverrideStore

    path = tmp_path / "risk_overrides.json"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {"pattern": "mcp__notion__*", "risk": "read"},  # refused
                    {"pattern": "mcp__srv__tool", "risk": "exec"},  # tighten: fine
                    {"pattern": "plugin_*", "risk": "read"},  # non-MCP: fine
                ]
            }
        ),
        encoding="utf-8",
    )
    store = RiskOverrideStore(path)
    assert store.resolve("mcp__notion__get_page") is None
    assert store.resolve("mcp__srv__tool") is RiskClass.EXEC
    assert store.resolve("plugin_notes") is RiskClass.READ
    assert len(store.rejected) == 1
    pattern, reason = store.rejected[0]
    assert pattern == "mcp__notion__*"
    assert "trust rule" in reason  # points at the sanctioned alternative


# -- the mode matrix: what the flag now means at the gate --------------------------
# requires_approval:false = legacy TRUST: waives the card in Ask-for-approval, and
# NOTHING else. One test per cell of the behavior table.
def engine(mode: Mode, tmp_path) -> PermissionEngine:
    return PermissionEngine(workspace_root=tmp_path, mode=mode)


def test_matrix_discuss_denies_trusted_mcp(tmp_path):
    d = engine(Mode.DISCUSS, tmp_path).evaluate("mcp__jira__createIssue", {}, Meta(False))
    assert not d.allowed and not d.needs_user  # denied outright, no card offered


def test_matrix_plan_denies_trusted_mcp(tmp_path):
    d = engine(Mode.PLAN, tmp_path).evaluate("mcp__jira__createIssue", {}, Meta(False))
    assert not d.allowed


def test_matrix_ask_mode_waives_the_card_for_trusted_mcp(tmp_path):
    d = engine(Mode.INTERACTIVE, tmp_path).evaluate(
        "mcp__jira__createIssue", {}, Meta(False)
    )
    assert d.allowed
    assert d.reason == "trusted MCP tool (server marked don't-ask)"


def test_matrix_ask_mode_still_asks_on_the_default_flag(tmp_path):
    d = engine(Mode.INTERACTIVE, tmp_path).evaluate(
        "mcp__jira__createIssue", {}, Meta(True)
    )
    assert not d.allowed and d.needs_user and not d.human_only  # reviewer-eligible ask


def test_matrix_auto_approve_routes_trusted_mcp_to_the_reviewer(tmp_path):
    # v1 keeps §1.5 conservative: server-config trust does not skip the judge. The
    # decision falls through to needs_user, which is exactly what the engine hands to
    # the reviewer (and never human_only — the reviewer MAY judge it).
    d = engine(Mode.AUTO_APPROVE, tmp_path).evaluate(
        "mcp__jira__createIssue", {}, Meta(False)
    )
    assert not d.allowed and d.needs_user and not d.human_only


def test_matrix_bypass_runs_trusted_and_untrusted_alike(tmp_path):
    e = engine(Mode.BYPASS_APPROVALS, tmp_path)
    assert e.evaluate("mcp__jira__createIssue", {}, Meta(False)).allowed
    assert e.evaluate("mcp__jira__createIssue", {}, Meta(True)).allowed


# -- the gate-order invariant ------------------------------------------------------
# Everything ABOVE the bypass branch in evaluate() survives Bypass mode; read-only
# modes deny before the human-only floors get a say. Line order IS the floor
# hierarchy — this pins it.
def test_gate_order_persistent_authority_survives_bypass(tmp_path):
    d = engine(Mode.BYPASS_APPROVALS, tmp_path).evaluate("save_skill", {"name": "x"}, None)
    assert not d.allowed and d.needs_user and d.human_only


def test_gate_order_write_fence_survives_bypass(tmp_path):
    # tmp_path.parent is absolute and out-of-root on every OS; a literal "C:/..." is
    # only absolute on Windows — POSIX reads it as a relative dir named "C:", which
    # _candidate() then resolves INTO the workspace root.
    outside = str(tmp_path.parent / "outside" / "anywhere.txt")
    d = engine(Mode.BYPASS_APPROVALS, tmp_path).evaluate(
        "write_file", {"path": outside, "content": "x"}, None
    )
    assert not d.allowed  # refused, not asked — the fence has no mode switch


def test_gate_order_read_only_mode_beats_the_human_only_floor(tmp_path):
    # In Discuss there is nothing to grant, so no card — the read-only denial fires
    # before the persistent-authority branch ("Read-only modes still hard-deny above
    # this", permissions.py).
    d = engine(Mode.DISCUSS, tmp_path).evaluate("save_skill", {"name": "x"}, None)
    assert not d.allowed and not d.needs_user
