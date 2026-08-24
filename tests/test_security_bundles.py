"""Phase C (OPE-61) — the shipped security coworker bundles.

Three builtin bundles (security, cloud-posture, dep-audit) live as self-contained dirs
(manifest.md + skills/) under personas/builtin/. Each is code-family, drives OSS
scanners via the vetted catalog only, and its skills reach only its own sessions.
"""

from __future__ import annotations

from pathlib import Path

from coworker.personas.registry import PersonaRegistry
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server.manager import SessionManager
from coworker.sessions import SessionRecord

BUNDLES = {
    "security": {"semgrep-review", "secret-scan", "security-fix-pr"},
    "cloud-posture": {"iac-scan", "aws-posture"},
    "dep-audit": {"dependency-audit", "safe-upgrade-pr"},
}


class ScriptedProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


def _reg(tmp_path) -> PersonaRegistry:
    return PersonaRegistry(state_path=tmp_path / "personas.json")


def test_bundles_register_as_enabled_code_builtins(tmp_path):
    reg = _reg(tmp_path)
    for pid in BUNDLES:
        entry = reg.get(pid)
        assert entry is not None and entry.builtin
        assert entry.requires_folder  # folder pick at send, like Code
        assert reg.is_enabled(pid) is True  # in the picker out of the box
        agent = reg.agent(pid)  # catalog-expanded tools materialize
        assert agent.requires_folder and agent.subagents


def test_bundle_skill_folders_match_their_manifests(tmp_path):
    reg = _reg(tmp_path)
    for pid, expected in BUNDLES.items():
        m = reg.get(pid).manifest
        assert set(m.skills) == expected
        skills_dir = Path(m.source).parent / "skills"
        on_disk = {p.name for p in skills_dir.iterdir() if (p / "SKILL.md").is_file()}
        # Every listed skill exists; nothing ships unlisted.
        assert on_disk == expected


def test_bundle_skills_stay_with_their_persona(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    for i, (pid, expected) in enumerate(BUNDLES.items()):
        sid = f"s{i}"
        mgr.session_store.save(
            SessionRecord(session_id=sid, workspace="", model="m", mode="interactive", agent=pid)
        )
        names = mgr.effective_skill_names(sid)
        assert expected <= names
        # No leakage from the sibling bundles.
        others = set().union(*(v for k, v in BUNDLES.items() if k != pid))
        assert not (others & names)


def test_prompts_carry_the_positioning_guardrails(tmp_path):
    # "Drive scanners, never replace them" + safe-ops language is the product stance —
    # a reworded manifest that drops it should fail loudly, not ship quietly.
    reg = _reg(tmp_path)
    for pid in BUNDLES:
        prompt = reg.get(pid).manifest.system_prompt.lower()
        assert "todo_write" in prompt
        assert "drive" in prompt  # drives scanners; value is judgment/remediation
    assert "read-only" in reg.get("cloud-posture").manifest.system_prompt.lower()
    assert "never print a discovered secret" in reg.get("security").manifest.system_prompt.lower()


def test_security_prompt_forbids_silently_skipping_a_check(tmp_path):
    """OPE-85, owner-hit 2026-08-13: with gitleaks unavailable the review silently dropped
    its git-history secret scan — the check didn't fail, it vanished. For a security tool,
    "no tool" rendering as "clean" is the worst possible outcome, so the contract lives in
    the prompt and is pinned here."""
    reg = _reg(tmp_path)
    prompt = reg.get("security").manifest.system_prompt.lower()
    assert "never silently skip" in prompt
    assert "coverage" in prompt  # every review reports what ran and what didn't
    assert "request_tool" in prompt  # asking is the first option, not skipping


def test_scanner_skills_offer_a_fallback_instead_of_stopping(tmp_path):
    """The skills used to say "if missing … and STOP", which is precisely the instruction
    that produced the vanished check. A missing tool must lead to request_tool or a manual
    equivalent — never to a dropped step."""
    import coworker.personas as personas_pkg

    root = Path(personas_pkg.__file__).parent / "builtin" / "security" / "skills"
    secret_scan = (root / "secret-scan" / "SKILL.md").read_text()
    semgrep = (root / "semgrep-review" / "SKILL.md").read_text()

    for body in (secret_scan, semgrep):
        assert "request_tool" in body
        assert "STOP" not in body

    # The history sweep is the check that actually went missing — it must survive without
    # gitleaks, and the no-printing rule must survive the manual path too.
    assert "git log -p" in secret_scan
    assert "REDACTED" in secret_scan


def test_cloud_posture_drives_trivy_config_not_deprecated_tfsec(tmp_path):
    """tfsec was folded into trivy upstream and is maintenance-only; recommending it
    sends request_tool (and users) after a dead tool. `trivy config` is the successor.
    The only tfsec mention allowed in the bundle is the deprecation ban itself."""
    import coworker.personas as personas_pkg

    root = Path(personas_pkg.__file__).parent / "builtin" / "cloud-posture"
    assert "tfsec" not in (root / "manifest.md").read_text()

    skill = (root / "skills" / "iac-scan" / "SKILL.md").read_text()
    assert "trivy config" in skill
    assert "request_tool" in skill  # missing scanner → ask, never a dropped scan
    for line in skill.splitlines():
        if "tfsec" in line:
            assert "deprecated" in line, f"stray tfsec mention: {line!r}"


def test_bundles_offer_a_report_page_rather_than_assuming_one(tmp_path):
    """A long findings list is a document people re-read and share, so the bundles offer a
    self-contained HTML report — but ASK first (owner call 2026-08-14). Assuming it would
    burn tokens on a page nobody wanted; skipping it leaves the deliverable trapped in chat."""
    reg = _reg(tmp_path)
    for pid in BUNDLES:
        prompt = reg.get(pid).manifest.system_prompt.lower()
        assert "ask_user" in prompt, pid  # opt-in, not automatic
        assert "self-contained" in prompt, pid  # opens anywhere, offline
        assert "artifact:" in prompt, pid  # linked the way the GUI can open it
        # The counts ride in the question so the user chooses with the gist in hand.
        assert "headline counts" in prompt, pid


def test_report_page_inherits_the_secret_and_evidence_rules(tmp_path):
    """A file gets forwarded and hosted — a value leaked there travels further than one in
    chat, so the page must not become a loophole around the no-secrets rule."""
    import re

    def flat(pid: str) -> str:
        # Prompts are hand-wrapped prose; collapse whitespace so a reflow can't
        # break these assertions (or hide a deleted rule).
        return re.sub(r"\s+", " ", reg.get(pid).manifest.system_prompt.lower())

    reg = _reg(tmp_path)
    security = flat("security")
    assert "never a secret's value" in security
    assert "coverage note reproduced in full" in security
    for pid in ("cloud-posture", "dep-audit"):
        assert "evidence per claim" in flat(pid), pid
