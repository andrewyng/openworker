"""OPE-58 — persona-carried skills and MCP scoping.

A persona bundle is a manifest + a sibling `skills/` dir. Bundle skills join the session
skill menu for THAT persona only (additive — never hiding the user's own), the manifest's
`skills:` list narrows which of them activate, and user disables/mutes always win. The
manifest's `mcp:` list scopes the persona's sessions to those raw MCP servers.
"""

from __future__ import annotations

from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server.manager import SessionManager
from coworker.sessions import SessionRecord

MANIFEST = """---
id: sec-review
name: Security Reviewer
icon: shield
tagline: Reviews code for security issues
family: code
tools: [code_files, search]
{extra}
---
You review code for security problems.
"""


class ScriptedProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


def _skill(base, name, description="a skill"):
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nDo the thing.\n",
        encoding="utf-8",
    )


def _mgr(tmp_path, monkeypatch) -> SessionManager:
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(workspace=tmp_path, provider=ScriptedProvider())


def _install(mgr, tmp_path, extra=""):
    src = tmp_path / "vendor"
    src.mkdir(exist_ok=True)
    (src / "sec-review.md").write_text(MANIFEST.format(extra=extra), encoding="utf-8")
    _skill(src / "skills", "semgrep-triage", "Triage semgrep findings")
    _skill(src / "skills", "secret-scan", "Run gitleaks and triage hits")
    mgr.personas.install_from_dir(src)
    mgr.personas.set_enabled("sec-review", True)


def _session(mgr, sid, agent):
    mgr.session_store.save(
        SessionRecord(session_id=sid, workspace="", model="m", mode="interactive", agent=agent)
    )


def test_bundle_skills_join_the_persona_sessions_menu(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _install(mgr, tmp_path)
    _session(mgr, "s-sec", "sec-review")
    _session(mgr, "s-cowork", "cowork")

    # The snapshot carried the skills/ dir; the persona's sessions see the bundle skills…
    names = mgr.effective_skill_names("s-sec")
    assert {"semgrep-triage", "secret-scan"} <= names
    # …other personas' sessions do not.
    assert "semgrep-triage" not in mgr.effective_skill_names("s-cowork")

    # The rail view labels them with the coworker scope.
    rows = {r["name"]: r for r in mgr.session_skills_view("s-sec")["skills"]}
    assert rows["semgrep-triage"]["scope"] == "coworker"


def test_manifest_skills_list_narrows_the_bundle(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _install(mgr, tmp_path, extra="skills: [semgrep-triage]")
    _session(mgr, "s1", "sec-review")

    names = mgr.effective_skill_names("s1")
    assert "semgrep-triage" in names
    assert "secret-scan" not in names
    assert "secret-scan" not in {
        r["name"] for r in mgr.session_skills_view("s1")["skills"]
    }


def test_user_disable_and_mute_win_over_bundle_skills(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _install(mgr, tmp_path)
    _session(mgr, "s1", "sec-review")

    # Settings disable beats the bundle.
    mgr.skill_store.set_enabled("semgrep-triage", False)
    assert "semgrep-triage" not in mgr.effective_skill_names("s1")
    mgr.skill_store.set_enabled("semgrep-triage", True)

    # A session mute beats it too.
    mgr.session_skills.set("s1", "secret-scan", False)
    assert "secret-scan" not in mgr.effective_skill_names("s1")


def test_users_own_copy_shadows_the_bundle_row(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _install(mgr, tmp_path)
    _session(mgr, "s1", "sec-review")

    mgr.skill_store.create(
        name="semgrep-triage", description="my customized copy", instructions="mine", scope="global"
    )
    rows = [r for r in mgr.session_skills_view("s1")["skills"] if r["name"] == "semgrep-triage"]
    assert len(rows) == 1 and rows[0]["scope"] != "coworker"


def test_persona_mcp_scope(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _install(mgr, tmp_path, extra="mcp: [semgrep-server]")
    # A declared list scopes; no list (builtin cowork) means no scoping.
    assert mgr.persona_mcp_scope("sec-review") == {"semgrep-server"}
    assert mgr.persona_mcp_scope("cowork") is None
    assert mgr.persona_mcp_scope("nope") is None
