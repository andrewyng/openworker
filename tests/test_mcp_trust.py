"""OPE-136 §4 — durable per-tool MCP trust: a rule in the user-local override store
waives the approval card (and ONLY the card), survives sessions, and is revocable.

The mode matrix here is the executable twin of test_mcp_floor's legacy-flag matrix:
identical in every cell except who granted the trust.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from coworker.overrides import RiskOverrideStore
from coworker.permissions import Mode, PermissionEngine

MCP_META = SimpleNamespace(requires_approval=True, category="mcp")
TOOL = "mcp__atlassian__getJiraIssue"


def engine(mode: Mode, tmp_path, store: RiskOverrideStore) -> PermissionEngine:
    return PermissionEngine(
        workspace_root=tmp_path,
        mode=mode,
        trust_overrides=store.trusted,
        grant_trust=store.set_trust,
    )


# -- the store: parse, match, persist, revoke -------------------------------------
def test_trust_rules_parse_persist_and_revoke(tmp_path):
    path = tmp_path / "ro.json"
    store = RiskOverrideStore(path)
    store.set_trust(TOOL)
    store.set_trust(TOOL)  # dedupe
    assert store.trusted(TOOL)
    assert not store.trusted("mcp__atlassian__deleteIssue")

    reloaded = RiskOverrideStore(path)  # survives a reload — it's a file, not RAM
    assert reloaded.trusted(TOOL)
    assert reloaded.trust_patterns() == [TOOL]

    reloaded.revoke_trust(TOOL)
    assert not reloaded.trusted(TOOL)
    assert not RiskOverrideStore(path).trusted(TOOL)  # the revoke persisted too


def test_trust_accepts_hand_written_globs_and_bare_strings(tmp_path):
    # The card writes exact names; hand-editing may use globs or bare strings.
    path = tmp_path / "ro.json"
    path.write_text(
        json.dumps({"trust": ["mcp__notes__*", {"pattern": TOOL}]}), encoding="utf-8"
    )
    store = RiskOverrideStore(path)
    assert store.trusted("mcp__notes__anything")
    assert store.trusted(TOOL)
    assert not store.trusted("mcp__other__x")


def test_trust_rules_never_touch_classification(tmp_path):
    # Trust waives the card; the CLASS is welded on (the OPE-136 floor).
    from coworker.risk import RiskClass, classify

    store = RiskOverrideStore(tmp_path / "ro.json")
    store.set_trust(TOOL)
    assert classify(TOOL, MCP_META, store.resolver()) is RiskClass.EXTERNAL


# -- test-plan #15: durable trust survives an engine rebuild ----------------------
def test_durable_trust_survives_an_engine_rebuild(tmp_path):
    path = tmp_path / "ro.json"
    store_a = RiskOverrideStore(path)
    eng_a = engine(Mode.INTERACTIVE, tmp_path, store_a)
    assert not eng_a.evaluate(TOOL, {}, MCP_META).allowed  # asks before any grant

    # The card's "Always allow this tool" lands through the engine's grant hook.
    eng_a.grant_trust_for_tool(TOOL)
    assert eng_a.evaluate(TOOL, {}, MCP_META).allowed  # this session, immediately

    # A brand-new session: new store instance, new engine — same file.
    eng_b = engine(Mode.INTERACTIVE, tmp_path, RiskOverrideStore(path))
    d = eng_b.evaluate(TOOL, {}, MCP_META)
    assert d.allowed and d.reason == "trusted MCP tool (user trust rule)"


# -- test-plan #16: the mode matrix for a rule-trusted tool -----------------------
def trusted_store(tmp_path) -> RiskOverrideStore:
    store = RiskOverrideStore(tmp_path / "ro.json")
    store.set_trust(TOOL)
    return store


def test_matrix_ask_mode_waives_the_card(tmp_path):
    d = engine(Mode.INTERACTIVE, tmp_path, trusted_store(tmp_path)).evaluate(
        TOOL, {}, MCP_META
    )
    assert d.allowed and d.reason == "trusted MCP tool (user trust rule)"


def test_matrix_discuss_still_denies(tmp_path):
    d = engine(Mode.DISCUSS, tmp_path, trusted_store(tmp_path)).evaluate(
        TOOL, {}, MCP_META
    )
    assert not d.allowed and not d.needs_user


def test_matrix_auto_approve_still_routes_to_the_reviewer(tmp_path):
    # §1.5 v1: user trust does not skip the judge — the decision falls through to
    # needs_user (reviewer-eligible, never human_only).
    d = engine(Mode.AUTO_APPROVE, tmp_path, trusted_store(tmp_path)).evaluate(
        TOOL, {}, MCP_META
    )
    assert not d.allowed and d.needs_user and not d.human_only


def test_matrix_bypass_unchanged(tmp_path):
    assert engine(Mode.BYPASS_APPROVALS, tmp_path, trusted_store(tmp_path)).evaluate(
        TOOL, {}, MCP_META
    ).allowed


# -- test-plan #17 (server side): the grant is offered only where the card shows it
def test_always_trust_grant_is_mcp_only():
    from coworker.engine import ApprovalOutcome
    from coworker.server.manager import _grant_offered

    def req(name: str, category: str):
        return SimpleNamespace(
            tool_name=name,
            metadata=SimpleNamespace(requires_approval=True, category=category),
            arguments={},
        )

    offered = ApprovalOutcome.ALWAYS_TRUST
    assert _grant_offered(offered, req("mcp__srv__tool", "mcp"))
    # A raw API caller must not mint durable trust for a connector or a built-in —
    # the server validates, exactly like the other grants.
    assert not _grant_offered(offered, req("email_send", "connector"))
    assert not _grant_offered(offered, req("run_shell", ""))


def test_remove_server_revokes_its_trust_rules(tmp_path, monkeypatch):
    """Owner-hit 2026-08-30: Remove wiped config, tokens, and connection — but trust
    rules live in risk_overrides.json and survived, so a future server added under
    the SAME NAME inherited don't-ask rules sight unseen. GONE means gone: delete
    revokes every rule under the server's prefix; broader globs and other servers'
    rules stay; sign-out (tokens only) deliberately does not do this."""
    from types import SimpleNamespace

    from coworker.server import manager as manager_mod
    from coworker.server.manager import SessionManager

    store = RiskOverrideStore(tmp_path / "ro.json")
    store.set_trust("mcp__atlassian__searchJiraIssuesUsingJql")
    store.set_trust("mcp__atlassian__*")  # a hand-written glob scoped to this server
    store.set_trust("mcp__other__keepMe")  # a different server's rule
    store.set_trust("mcp__*")  # a broader glob — NOT this server's rule

    monkeypatch.setattr(manager_mod, "delete_global_server", lambda _n: True)
    import coworker.mcp.oauth as mcp_oauth

    monkeypatch.setattr(mcp_oauth, "sign_out", lambda _n, _s: None)

    fake = SimpleNamespace(
        _mcp_errors={},
        _mcp_auth_hints=set(),
        _prefs={},
        _save_prefs=lambda: None,
        _clear_mcp_notified=lambda _n: None,
        mcp=SimpleNamespace(_conns={}),
        _loop=None,
        secrets=None,
        _override_store=lambda: RiskOverrideStore(tmp_path / "ro.json"),
    )
    result = SessionManager.delete_mcp(fake, "atlassian")
    assert result["ok"]

    survivors = RiskOverrideStore(tmp_path / "ro.json").trust_patterns()
    assert survivors == ["mcp__other__keepMe", "mcp__*"]


def test_engine_fallback_without_a_store_degrades_to_session_scope(tmp_path):
    # Ephemeral engines (tests, embedded uses) have no store wired: the grant falls
    # back to the session set rather than silently doing nothing.
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE)
    eng.grant_trust_for_tool(TOOL)
    assert TOOL in eng.session_allow_tools


# -- test-plan #18 (config side): the legacy flag's migration primitive -----------
def test_patch_global_server_none_deletes_the_key(tmp_path, monkeypatch):
    from coworker.mcp import config as mcp_config

    monkeypatch.setattr(mcp_config, "global_mcp_path", lambda: tmp_path / "mcp.json")
    mcp_config.put_global_server(
        "jira", {"url": "https://x/mcp", "requires_approval": False}
    )
    assert mcp_config.read_global()["jira"]["requires_approval"] is False
    mcp_config.patch_global_server("jira", {"requires_approval": None})
    assert "requires_approval" not in mcp_config.read_global()["jira"]
    assert mcp_config.read_global()["jira"]["url"] == "https://x/mcp"  # rest intact
