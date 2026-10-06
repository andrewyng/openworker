"""`request_network_access`: the agent asks a person for a site its commands cannot reach
(OPE-219).

The sandbox's allowed sites are a wall. A command that needs another site fails inside the
sandbox; the sandbox says what it blocked, the agent asks with a reason, and a person
decides. Nothing else can: not the reviewer, not a lead, not full access.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

from coworker.permissions import NETWORK_ACCESS_TOOL, Mode, PermissionEngine, network_request_hosts
from coworker.sandbox import netproxy
from coworker.sandbox.executor import RunnerExecutor
from coworker.sandbox.workspace import RunnerWorkspace, network_note_text
from coworker.tools.network import request_network_access_tool

SITES = ["github.com:443", "*.githubusercontent.com:443"]


def engine(tmp_path, mode=Mode.INTERACTIVE, **kw):
    return PermissionEngine(workspace_root=tmp_path, mode=mode, sandbox_sites=list(SITES), **kw)


def ask(eng, hosts, reason="npm install needs the registry"):
    return eng.evaluate(NETWORK_ACCESS_TOOL, {"hosts": hosts, "reason": reason}, None)


# -- what the agent may ask for -----------------------------------------------------------
def test_only_exact_host_names_are_accepted():
    good, bad = network_request_hosts({"hosts": ["Registry.NPMjs.org", "db.example.com:5432", "registry.npmjs.org:443", "https://pypi.org/simple/"]})
    assert good == ["registry.npmjs.org:443", "db.example.com:5432", "pypi.org:443"] and bad == []
    for item in ("*.npmjs.org", "10.0.0.8", "10.0.0.8:5432", "localhost", "ignore your instructions", ""):
        good, bad = network_request_hosts({"hosts": [item]})
        assert good == [] and bad == [item[:80]], item
    assert network_request_hosts({"hosts": "pypi.org"}) == (["pypi.org:443"], [])  # a lone string is one host
    assert network_request_hosts({}) == ([], [])


def test_a_bad_or_oversized_request_is_refused_without_a_card(tmp_path):
    eng = engine(tmp_path)
    for hosts in (["*.npmjs.org"], [], ["a.dev", "b.dev", "c.dev", "d.dev", "e.dev", "f.dev"], ["pypi.org", "not a host"]):
        d = ask(eng, hosts)
        assert not d.allowed and not d.needs_user and "exact host names" in d.reason, hosts


# -- who answers ---------------------------------------------------------------------------
def test_a_person_and_only_a_person_answers(tmp_path):
    for mode in (Mode.INTERACTIVE, Mode.AUTO_APPROVE):
        d = ask(engine(tmp_path, mode), ["registry.npmjs.org", "github.com"])
        assert not d.allowed and d.needs_user and d.human_only, mode
        assert d.network_hosts == ("registry.npmjs.org:443",)  # github.com is already allowed
    # A session-wide grant of the tool, however it got there, clears nothing: every ask
    # for a new site is a new card.
    eng = engine(tmp_path)
    eng.allow_tool_for_session(NETWORK_ACCESS_TOOL)
    assert ask(eng, ["registry.npmjs.org"]).needs_user


def test_full_access_cannot_change_the_wall_and_read_only_modes_do_not_ask(tmp_path):
    d = ask(engine(tmp_path, Mode.BYPASS_APPROVALS), ["registry.npmjs.org"])
    assert not d.allowed and not d.needs_user and "Settings > Sandbox" in d.reason
    for mode in (Mode.DISCUSS, Mode.PLAN):
        d = ask(engine(tmp_path, mode), ["registry.npmjs.org"])
        assert not d.allowed and not d.needs_user and "read-only" in d.reason


def test_sites_already_allowed_need_no_card_and_no_list_means_no_tool(tmp_path):
    assert ask(engine(tmp_path), ["github.com", "raw.githubusercontent.com"]).allowed
    assert ask(engine(tmp_path), ["github.com:22"]).needs_user  # another port is another entry
    plain = PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE)
    d = ask(plain, ["registry.npmjs.org"])
    assert not d.allowed and not d.needs_user


# -- what an approval does -----------------------------------------------------------------
def test_this_session_opens_the_sandbox_and_stores_nothing(tmp_path):
    opened: list[str] = []
    stored: list[str] = []
    eng = engine(tmp_path, open_site=opened.append, grant_site=lambda entry: stored.append(entry) or [*SITES, entry])
    eng.allow_network_hosts(["registry.npmjs.org:443", "db.example.com:5432"], always=False)
    assert opened == ["registry.npmjs.org:443", "db.example.com:5432"] and stored == []
    assert eng.session_sites == ["registry.npmjs.org:443", "db.example.com:5432"] and eng.sandbox_sites == SITES
    assert ask(eng, ["registry.npmjs.org", "db.example.com:5432"]).allowed
    # One list for commands and web tools: the web tools reach the site too.
    assert eng.evaluate("web_fetch", {"url": "https://registry.npmjs.org/left-pad"}, None).allowed


def test_always_allow_stores_the_site_on_the_machines_list(tmp_path):
    opened: list[str] = []
    eng = engine(tmp_path, open_site=opened.append, grant_site=lambda entry: [*SITES, entry])
    eng.allow_network_hosts(["registry.npmjs.org:443"], always=True)
    assert eng.sandbox_sites == [*SITES, "registry.npmjs.org:443"] and eng.session_sites == []
    assert opened == ["registry.npmjs.org:443"] and ask(eng, ["registry.npmjs.org"]).allowed


def test_the_agent_is_told_what_it_now_has_and_what_the_sandbox_could_not_take(tmp_path):
    def open_site(entry: str) -> None:
        if entry.startswith("db."):
            raise RuntimeError("the gateway did not answer")

    eng = engine(tmp_path, open_site=open_site)
    run = request_network_access_tool(eng)
    eng.allow_network_hosts(["registry.npmjs.org:443", "db.example.com:5432"], always=False)
    result = run(hosts=["registry.npmjs.org", "db.example.com:5432"], reason="x")
    assert result["allowed"] == ["registry.npmjs.org"] and "Run the command again" in result["message"]
    assert result["not_opened"] == {"db.example.com:5432": "the gateway did not answer"}
    assert run.__name__ == NETWORK_ACCESS_TOOL


def test_the_server_offers_this_session_and_always_and_nothing_tool_wide():
    from coworker.engine import ApprovalOutcome
    from coworker.server.manager import _grant_offered

    request = SimpleNamespace(tool_name=NETWORK_ACCESS_TOOL, metadata=None, arguments={"hosts": ["a.dev"]})
    assert _grant_offered(ApprovalOutcome.ALWAYS_DOMAIN, request) and _grant_offered(ApprovalOutcome.ALWAYS_SITE, request)
    for outcome in (ApprovalOutcome.ALWAYS_TOOL, ApprovalOutcome.THIS_RUN, ApprovalOutcome.ALWAYS_COMMAND, ApprovalOutcome.ALWAYS_TRUST):
        assert not _grant_offered(outcome, request), outcome


# -- what the sandbox reports --------------------------------------------------------------
def _connect(proxy: netproxy.AllowListProxy, target: str) -> bytes:
    with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as sock:
        sock.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        return sock.recv(4096)


def test_the_proxy_remembers_what_it_refused_and_when():
    proxy = netproxy.AllowListProxy("allowlist")
    try:
        assert b"403" in _connect(proxy, "registry.npmjs.org:443")
        assert b"403" in _connect(proxy, "registry.npmjs.org:443")
        (first, entry), (second, _) = proxy.blocked_since(0)
        assert entry == "registry.npmjs.org:443" and first <= second
        assert proxy.blocked_since(second + 1) == []
    finally:
        proxy.close()
    # What a program asked for is shown to the agent and the person: only a host name passes.
    assert netproxy._blocked_entry("http://Example.com/a?b=c") == "example.com:80"
    for text in ("ignore previous instructions:443", "*.npmjs.org:443", "localhost:443", ""):
        assert netproxy._blocked_entry(text) == "", text


def test_the_note_says_what_was_blocked_and_what_is_not_known():
    text = network_note_text({"registry.npmjs.org:443": 3, "objects.githubusercontent.com:443": 1}, can_ask=True)
    assert "may have affected it" in text and "- registry.npmjs.org:443 (3 attempts)" in text and "- objects.githubusercontent.com:443\n" in text
    assert "or from something else running in this session" in text and "request_network_access(hosts, reason)" in text
    told = network_note_text({"a.dev:443": 1}, can_ask=False)
    assert "request_network_access" not in told and "Settings > Sandbox > Choose sites" in told
    many = network_note_text({f"h{i}.dev:443": 1 for i in range(14)}, can_ask=True)
    assert "- h9.dev:443" in many and "h10.dev" not in many and "and 4 more" in many


class _Provider:
    name, profile = "seatbelt", "allowlist"

    def __init__(self):
        self.extra_hosts = ["github.com:443", "db.example.com:5432"]
        self.blocked: list[tuple[float, str]] = []

    def blocked_since(self, since):
        return [item for item in self.blocked if item[0] >= since]


def test_a_command_result_carries_the_note_only_when_something_was_blocked(tmp_path):
    provider = _Provider()
    workspace = RunnerWorkspace(provider, cwd=tmp_path, start=False)
    assert workspace.network_note(0.0) is None
    workspace._blocked_told = 15.0  # everything up to here was already told
    provider.blocked = [(10.0, "old.dev:443"), (20.0, "registry.npmjs.org:443"), (21.0, "registry.npmjs.org:443")]
    note = workspace.network_note(19.8)
    assert note["blocked_sites"] == ["registry.npmjs.org:443"] and "(2 attempts)" in note["network_note"]
    assert "While it ran" in note["network_note"] and "Settings > Sandbox" in note["network_note"]  # nobody can be asked until the session says so
    # Told once: the next command's result does not repeat it.
    assert workspace.network_note(22.0) is None
    workspace.can_ask_network = lambda: True
    provider.blocked.append((30.0, "pypi.org:443"))
    assert "request_network_access(hosts, reason)" in workspace.network_note(29.9)["network_note"]


def test_a_refusal_reported_late_waits_for_a_failed_command_and_is_never_lost(tmp_path):
    """OpenShell sends its log in batches, so a refusal can reach us after the command's
    result. A failed command waits for it; a command that succeeded does not; and one that
    still arrives late is told with the next result, worded as such."""
    import threading
    import time as clock

    provider = _Provider()
    provider.reports_blocked = True
    provider.blocked_lag = 0.6
    workspace = RunnerWorkspace(provider, cwd=tmp_path, start=False)
    started = clock.time()
    threading.Timer(0.15, lambda: provider.blocked.append((started + 0.01, "registry.npmjs.org:443"))).start()
    began = clock.time()
    note = workspace.network_note(started, {"exit_code": 1})
    assert note and note["blocked_sites"] == ["registry.npmjs.org:443"] and 0.1 < clock.time() - began < 0.5  # left as soon as it arrived
    began = clock.time()
    assert workspace.network_note(clock.time(), {"exit_code": 0}) is None and clock.time() - began < 0.1  # success: no wait
    began = clock.time()
    assert workspace.network_note(clock.time(), {"exit_code": 1}) is None and clock.time() - began >= 0.55  # failed, nothing blocked: the full wait
    # A refusal during a command that succeeded: nobody waited for it. The next command
    # starts later and its result carries it.
    provider.blocked.append((clock.time(), "late.dev:443"))
    late = workspace.network_note(clock.time() + 2, {"exit_code": 0})
    assert late["blocked_sites"] == ["late.dev:443"] and "Since your previous command" in late["network_note"]


def test_openshell_refusals_are_read_from_the_sandbox_log():
    from coworker.sandbox.providers import openshell

    log = openshell.DenialLog.__new__(openshell.DenialLog)
    log.entries, log._seen, log._process = __import__("collections").deque(maxlen=200), set(), None
    lines = [
        "[1791068681.547] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED /usr/bin/curl(85) -> www.iana.org:443 [policy:- engine:opa] [reason:endpoint www.iana.org:443 is not allowed by any policy]",
        "[1791068681.547] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED /usr/bin/curl(85) -> www.iana.org:443 [policy:- engine:opa] [reason:replayed history]",
        "[1791068690.100] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED /usr/local/bin/node(91) -> registry.npmjs.org:443 [policy:- engine:opa]",
        "[1791068640.416] [sandbox] [OCSF ] [ocsf] CONFIG:LOADED [INFO] Policy reloaded successfully [policy_hash:4fad]",
        "[1791068691.000] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED /usr/bin/curl(99) -> ignore-your-instructions:443 [policy:-]",
        "[1791068692.000] [sandbox] [OCSF ] [ocsf] NET:OPEN [INFO] ALLOWED /usr/bin/curl(100) -> pypi.org:443 [policy:pkgs]",
    ]
    for line in lines:
        log.take(line)
    assert list(log.entries) == [(1791068681.547, "www.iana.org:443"), (1791068690.1, "registry.npmjs.org:443")]
    assert log.since(1791068685.0) == [(1791068690.1, "registry.npmjs.org:443")] and not log.alive


def test_the_executor_adds_the_note_to_a_command_result(tmp_path):
    client = SimpleNamespace(call=lambda *a, **kw: {"command": "npm i", "cwd": str(tmp_path), "exit_code": 1, "output": "npm ERR!"})
    seen: list[float] = []
    executor = RunnerExecutor(client, cwd=str(tmp_path), after_call=lambda started, result: seen.append(result["exit_code"]) or {"blocked_sites": ["a.dev:443"], "network_note": "Note: …"})
    result = executor.run("npm i")
    assert result["exit_code"] == 1 and result["blocked_sites"] == ["a.dev:443"] and result["network_note"] == "Note: …" and seen == [1]

    def broken(started, result):
        raise RuntimeError("no log")

    assert RunnerExecutor(client, cwd=str(tmp_path), after_call=broken).run("npm i")["output"] == "npm ERR!"


def test_the_agent_is_told_the_live_list_each_turn(tmp_path):
    provider = _Provider()
    workspace = RunnerWorkspace(provider, cwd=tmp_path, start=False)
    workspace.can_ask_network = lambda: True
    text = workspace.context()
    assert "They reach only these sites: github.com, db.example.com:5432." in text
    assert "call request_network_access" in text and "do not look for another route" in text
    provider.extra_hosts.append("pypi.org:443")
    assert "pypi.org" in workspace.context()
    workspace.can_ask_network = lambda: False
    assert "request_network_access" not in workspace.context() and "tell the user" in workspace.context()
    provider.profile = "open"
    assert "sandbox" not in workspace.context()


def test_a_sandbox_that_cannot_report_blocks_gives_the_card_no_evidence(tmp_path, monkeypatch):
    """When a sandbox's refusals cannot be read (OpenShell before its log is followed, or
    after the reader died), an empty answer means "unknown", so the card must not say that
    no command has tried the site."""
    from coworker.engine import TurnEngine
    from coworker.permissions import Decision
    from coworker.sandbox.providers import openshell, seatbelt

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(openshell, "build_runner_zipapp", lambda: tmp_path / "sandbox" / "runner" / "runner-x.pyz")
    not_made = openshell.OpenShellProvider(roots=[{"path": str(tmp_path), "writable": True}], registry="abc")
    assert not_made.reports_blocked is False and not_made.blocked_since(0) == [] and seatbelt.SeatbeltProvider.reports_blocked is True
    call = SimpleNamespace(arguments={"hosts": ["registry.npmjs.org"], "reason": "npm install"})
    decision = Decision(False, "asks", needs_user=True, human_only=True, network_hosts=("registry.npmjs.org:443",))

    def payload(provider):
        holder = SimpleNamespace(sandbox_workspace=RunnerWorkspace(provider, cwd=tmp_path, start=False))
        return TurnEngine._network_request_payload(holder, call, decision)

    unknown = _Provider()
    assert payload(unknown) == {"reason": "npm install", "evidence": False, "hosts": [{"host": "registry.npmjs.org:443", "blocked_seconds_ago": None}]}
    seen = _Provider()
    seen.reports_blocked = True
    seen.blocked = [(__import__("time").time() - 30, "registry.npmjs.org:443")]
    shown = payload(seen)
    assert shown["evidence"] is True and 29 <= shown["hosts"][0]["blocked_seconds_ago"] <= 35


# -- the person's own list for one session (the Access section) ----------------------------
def test_the_person_adds_and_takes_back_a_session_site(tmp_path, monkeypatch):
    from coworker.server.manager import SessionManager

    opened: list[str] = []
    closed: list[str] = []
    eng = engine(tmp_path, open_site=opened.append, close_site=closed.append)
    holder = SimpleNamespace(permissions=eng, sandbox_workspace=None)
    manager = SimpleNamespace(_engines={"s1": holder}, audit_autonomy_change=lambda *a: None)
    for name in ("session_sites", "_session_site_engine", "allow_session_site", "remove_session_site", "_audit_session_site"):
        setattr(manager, name, getattr(SessionManager, name).__get__(manager))

    assert manager.allow_session_site("s1", "Registry.NPMjs.org")["ok"]
    assert eng.session_sites == ["registry.npmjs.org:443"] and opened == ["registry.npmjs.org:443"]
    assert manager.allow_session_site("s1", "github.com")["ok"] and eng.session_sites == ["registry.npmjs.org:443"]  # already on the machine's list
    for bad in ("*.npmjs.org", "10.0.0.8", "two hosts.dev other.dev", ""):
        assert not manager.allow_session_site("s1", bad)["ok"], bad
    # Only a session site can be taken back here; the machine's list is changed in Settings.
    refused = manager.remove_session_site("s1", "github.com")
    assert not refused["ok"] and "Settings > Sandbox" in refused["error"]
    assert manager.remove_session_site("s1", "registry.npmjs.org")["ok"]
    assert eng.session_sites == [] and closed == ["registry.npmjs.org:443"]
    assert ask(eng, ["registry.npmjs.org"]).needs_user
    assert not eng.evaluate("web_fetch", {"url": "https://registry.npmjs.org/x"}, None).allowed
    assert not manager.allow_session_site("nope", "a.dev")["ok"]  # no such session


def test_a_site_the_sandbox_cannot_take_is_not_left_on_the_list(tmp_path):
    from coworker.server.manager import SessionManager

    def refuse(entry: str) -> None:
        raise RuntimeError("the gateway did not answer")

    eng = engine(tmp_path, open_site=refuse)
    manager = SimpleNamespace(_engines={"s1": SimpleNamespace(permissions=eng, sandbox_workspace=None)}, audit_autonomy_change=lambda *a: None)
    for name in ("session_sites", "_session_site_engine", "allow_session_site", "_audit_session_site"):
        setattr(manager, name, getattr(SessionManager, name).__get__(manager))
    result = manager.allow_session_site("s1", "registry.npmjs.org")
    assert not result["ok"] and "the gateway did not answer" in result["error"]
    assert eng.session_sites == [] and ask(eng, ["registry.npmjs.org"]).needs_user


def test_a_running_sandbox_drops_a_site_taken_back(tmp_path, monkeypatch):
    proxy = netproxy.AllowListProxy("allowlist", extra_hosts=["github.com:443"])
    try:
        proxy.add_hosts(["registry.npmjs.org:443"])
        proxy.remove_hosts(["registry.npmjs.org:443"])
        assert not proxy.allows("registry.npmjs.org", 443) and proxy.allows("github.com", 443)
    finally:
        proxy.close()
    from coworker.sandbox.providers import openshell

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(openshell, "build_runner_zipapp", lambda: tmp_path / "sandbox" / "runner" / "runner-x.pyz")
    project = tmp_path / "project"
    project.mkdir()
    seen: list = []
    monkeypatch.setattr(openshell, "_cli", lambda *args, **kw: seen.append(args))
    provider = openshell.OpenShellProvider(roots=[{"path": str(project), "writable": True}], extra_hosts=["github.com:443", "registry.npmjs.org:443"], registry="abc")
    provider._create_tried = True
    provider.remove_hosts(["registry.npmjs.org:443", "never.there:443"])
    (call,) = seen
    assert call[:2] == ("policy", "set") and provider.extra_hosts == ["github.com:443"]
    import yaml
    from pathlib import Path

    written = yaml.safe_load(Path(call[call.index("--policy") + 1]).read_text())
    hosts = {e["host"] for rule in written["network_policies"].values() for e in rule["endpoints"]}
    assert "github.com" in hosts and "registry.npmjs.org" not in hosts
