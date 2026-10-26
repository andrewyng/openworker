"""The sandbox's allowed sites hold the web tools too (OPE-219).

Reported on 0.3.0 (2026-10-02): with "Only the sites you allow" and only code hosts ticked,
the agent still searched the web. The list was a wall for commands inside the sandbox; the
web tools run in the OpenWorker process and never looked at it, and Bypass skipped their card.
"""

from __future__ import annotations

from coworker.permissions import Mode, PermissionEngine

SITES = ["github.com", "api.github.com:443", "*.githubusercontent.com"]


def engine(tmp_path, mode, **kw):
    return PermissionEngine(workspace_root=tmp_path, mode=mode, sandbox_sites=list(SITES), **kw)


def test_a_site_on_the_list_runs_without_a_card_in_every_asking_mode(tmp_path):
    for mode in (Mode.INTERACTIVE, Mode.AUTO_APPROVE, Mode.BYPASS_APPROVALS):
        eng = engine(tmp_path, mode)
        for url in ("https://github.com/a/b", "https://api.github.com/zen", "https://raw.githubusercontent.com/a/b/x"):
            d = eng.evaluate("web_fetch", {"url": url}, None)
            assert d.allowed and not d.needs_user, (mode, url, d.reason)
    # The wildcard covers subdomains only, as in the sandbox's own proxy.
    assert not engine(tmp_path, Mode.INTERACTIVE).evaluate("web_fetch", {"url": "https://githubusercontent.com/"}, None).allowed


def test_bypass_refuses_a_site_off_the_list_with_no_card(tmp_path):
    d = engine(tmp_path, Mode.BYPASS_APPROVALS).evaluate("web_fetch", {"url": "https://weather.com/today?x=1"}, None)
    assert not d.allowed and not d.needs_user  # a wall, like the folder wall: no mode removes it
    assert d.site == "weather.com" and "allowed sites" in d.reason


def test_asking_modes_raise_a_card_only_a_person_can_answer(tmp_path):
    for mode in (Mode.INTERACTIVE, Mode.AUTO_APPROVE):
        d = engine(tmp_path, mode).evaluate("web_fetch", {"url": "https://weather.com/today"}, None)
        assert not d.allowed and d.needs_user and d.human_only, mode  # never the reviewer
        assert d.site == "weather.com"


def test_the_wall_wins_over_the_approval_allowlist(tmp_path):
    # `allowed_domains` only waives the card; it is not the sandbox's list.
    eng = engine(tmp_path, Mode.INTERACTIVE, allowed_domains=["weather.com"])
    assert eng.evaluate("web_fetch", {"url": "https://weather.com/"}, None).needs_user


def test_session_and_always_choices(tmp_path):
    eng = engine(tmp_path, Mode.INTERACTIVE)
    eng.allow_domain_for_session("https://weather.com/today")
    assert eng.evaluate("web_fetch", {"url": "https://weather.com/other"}, None).allowed
    # A session grant holds in Bypass too: a person gave it.
    eng.mode = Mode.BYPASS_APPROVALS
    assert eng.evaluate("web_fetch", {"url": "https://www.weather.com/x"}, None).allowed

    saved: list[str] = []
    eng2 = engine(tmp_path, Mode.INTERACTIVE, grant_site=saved.append)
    eng2.allow_site_always("https://docs.python.org/3/")
    assert saved == ["docs.python.org"] and "docs.python.org" in eng2.sandbox_sites
    assert eng2.evaluate("web_fetch", {"url": "https://docs.python.org/3/library/"}, None).allowed
    assert eng2.evaluate("web_fetch", {"url": "https://python.org/"}, None).needs_user  # that host only

    # The store returns the list as it keeps it (with ports); the session takes that copy,
    # so the header chip and Settings show the same entries.
    eng3 = engine(tmp_path, Mode.INTERACTIVE, grant_site=lambda host: ["github.com:443", f"{host}:443"])
    eng3.allow_site_always("https://docs.python.org/3/")
    assert eng3.sandbox_sites == ["github.com:443", "docs.python.org:443"]
    assert eng3.evaluate("web_fetch", {"url": "https://docs.python.org/3/library/"}, None).allowed


def test_web_search_is_held_to_the_provider_host(tmp_path):
    eng = engine(tmp_path, Mode.BYPASS_APPROVALS, search_host=lambda: "api.search.brave.com")
    d = eng.evaluate("web_search", {"query": "weather"}, None)
    assert not d.allowed and not d.needs_user and d.site == "api.search.brave.com"
    eng.sandbox_sites.append("api.search.brave.com")
    assert eng.evaluate("web_search", {"query": "weather"}, None).allowed
    # In an asking mode an unlisted provider raises the card; with no known provider too.
    ask = engine(tmp_path, Mode.INTERACTIVE, search_host=lambda: "")
    d = ask.evaluate("web_search", {"query": "x"}, None)
    assert d.needs_user and d.human_only


def test_no_wall_without_a_site_list(tmp_path):
    # Not sandboxed, or "Allow everything": today's behaviour, untouched.
    assert PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS).evaluate("web_fetch", {"url": "https://weather.com/"}, None).allowed
    d = PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE).evaluate("web_fetch", {"url": "https://weather.com/"}, None)
    assert d.needs_user and not d.human_only and not d.site
    # An empty list is a wall with nothing ticked.
    empty = PermissionEngine(workspace_root=tmp_path, mode=Mode.BYPASS_APPROVALS, sandbox_sites=[])
    assert not empty.evaluate("web_fetch", {"url": "https://github.com/"}, None).allowed


def test_other_tools_are_untouched_by_the_wall(tmp_path):
    eng = engine(tmp_path, Mode.BYPASS_APPROVALS)
    assert eng.evaluate("run_shell", {"command": "curl https://weather.com"}, None).allowed  # the sandbox holds commands
    assert eng.evaluate("read_file", {"path": "a.txt"}, None).allowed


# -- the durable choice: server validation and the machine's list ---------------------------


def test_always_site_is_offered_only_for_egress_tools(tmp_path):
    from types import SimpleNamespace

    from coworker.engine import ApprovalOutcome
    from coworker.server.manager import SessionManager

    manager = SessionManager(workspace=str(tmp_path), data_dir=tmp_path / "state")
    request = lambda tool, args: SimpleNamespace(tool_name=tool, arguments=args, metadata=None)  # noqa: E731
    assert manager.approval_outcome("always_site", request("web_fetch", {"url": "https://weather.com/"}), "s1") is ApprovalOutcome.ALWAYS_SITE
    assert manager.approval_outcome("always_site", request("web_search", {"query": "x"}), "s2") is ApprovalOutcome.ALWAYS_SITE
    # A raw resolve for another tool degrades to a single allow, like every other grant.
    assert manager.approval_outcome("always_site", request("write_file", {"path": "a"}), "s3") is ApprovalOutcome.ONCE


def test_add_site_joins_the_machines_list_once(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    from coworker import config as app_config
    from coworker.sandbox import settings

    assert settings.add_site("weather.com") == ["weather.com:443"]
    hosts = app_config.load_config().sandbox_network_hosts
    assert hosts == ["weather.com:443"]
    settings.add_site("weather.com")
    assert app_config.load_config().sandbox_network_hosts == hosts  # no duplicate


# -- what the session header is told (OPE-218) ----------------------------------------------


def test_session_sandbox_summary(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from coworker.sandbox import settings

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    grant = SimpleNamespace(name="gh", title="GitHub CLI")
    provider = SimpleNamespace(
        name="openshell", profile="allowlist", extra_hosts=["github.com:443"],
        roots=[{"path": "/home/sam/code/app", "writable": True}], grants=[grant],
    )
    engine = SimpleNamespace(
        sandbox_workspace=SimpleNamespace(provider=provider, started=False),
        permissions=SimpleNamespace(sandbox_sites=["github.com:443", "weather.com"]),
    )
    info = settings.session_sandbox(engine)
    assert info["state"] == "sandboxed" and info["provider"] == "openshell" and info["network"] == "allowlist"
    assert info["sites"] == ["github.com:443", "weather.com"]  # the live list, with what the user added
    assert info["folders"] == [{"path": "/home/sam/code/app", "writable": True}] and info["logins"] == ["GitHub CLI"]
    assert info["started"] is False

    provider.profile = "open"
    engine.permissions.sandbox_sites = None
    assert settings.session_sandbox(engine)["sites"] == []  # any site: no list to show

    # A session with no sandbox: "off" when the machine has none either...
    plain = SimpleNamespace(sandbox_workspace=SimpleNamespace(), permissions=SimpleNamespace(sandbox_sites=None))
    assert settings.session_sandbox(plain) == {"state": "off"}
    # ...and "not sandboxed" when the machine is now set to use one (opened before the switch).
    (tmp_path / "config.toml").write_text('sandbox_provider = "openshell"\n')
    assert settings.session_sandbox(plain) == {"state": "not_sandboxed", "provider": "openshell"}


def test_a_session_or_always_choice_opens_the_site_on_the_running_sandbox(tmp_path):
    """The card's lasting choices reach commands too: the engine tells the sandbox. "Allow
    once" is one web-tool call and never touches it."""
    opened: list[str] = []
    eng = engine(tmp_path, Mode.INTERACTIVE, open_site=opened.append, grant_site=lambda host: [*SITES, f"{host}:443"])
    eng.allow_site_for_session("https://www.weather.com/today")
    assert opened == ["www.weather.com"]
    assert eng.evaluate("web_fetch", {"url": "https://weather.com/x"}, None).allowed
    assert "weather.com:443" not in eng.sandbox_sites  # this session only: nothing stored
    eng.allow_site_always("https://docs.python.org/3/")
    assert opened == ["www.weather.com", "docs.python.org"] and "docs.python.org:443" in eng.sandbox_sites
    # No sandbox hook (an engine without a sandbox): the choices still work for the web tools.
    plain = engine(tmp_path, Mode.INTERACTIVE)
    plain.allow_site_for_session("https://weather.com/")
    assert plain.evaluate("web_fetch", {"url": "https://weather.com/x"}, None).allowed
