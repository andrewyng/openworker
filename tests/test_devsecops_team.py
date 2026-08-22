"""The DevSecOps team bundle (eighteenth pass) — lead + three worker variants.

The team is worker-VARIANT manifests over the solo security personas' bundles:
prompts talk to a lead, tool bundles and skills carry over. Doctrine locks under
test: the capability firebreak (the lead carries no shell/git), workers never
surface in the picker, skills ship inside each bundle (OPE-58 shape), and the
secrets-are-radioactive rule appears in every prompt that can meet a secret.
"""

from __future__ import annotations

from pathlib import Path

from coworker.personas.registry import PersonaRegistry

ROSTER = ("appsec-worker", "secrets-worker", "posture-worker")


def _reg(tmp_path) -> PersonaRegistry:
    return PersonaRegistry(state_path=tmp_path / "personas.json")


def test_bundle_registers_with_team_traits(tmp_path):
    reg = _reg(tmp_path)
    lead = reg.get("devsecops-lead")
    assert lead.manifest.team == "lead"
    for pid in ROSTER:
        assert reg.get(pid).manifest.team == "worker"


def test_lead_carries_no_execution_tools(tmp_path):
    # The capability firebreak: leads coordinate; scanning/fixing needs shell+git,
    # which only the workers carry.
    reg = _reg(tmp_path)
    assert "shell" not in reg.get("devsecops-lead").tools
    assert "git" not in reg.get("devsecops-lead").tools
    for pid in ROSTER:
        assert {"shell", "git"} <= set(reg.get(pid).tools)


def test_workers_never_surface_lead_does(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENWORKER_UNSHIPPED", "1")  # teams are ships:false — internal builds
    reg = _reg(tmp_path)
    ids = [e["name"] for e in reg.sidebar()]
    assert "devsecops-lead" in ids
    assert not any(pid in ids for pid in ROSTER)


def test_worker_bundles_ship_their_skills(tmp_path):
    # OPE-58 bundle shape: the manifest's skills: list names folders that live in a
    # sibling skills/ dir — self-contained, no reach into the solo personas.
    reg = _reg(tmp_path)
    for pid in ROSTER:
        m = reg.get(pid).manifest
        assert m.skills, pid
        skills_dir = Path(m.source).parent / "skills"
        for skill in m.skills:
            assert (skills_dir / skill / "SKILL.md").is_file(), f"{pid}: {skill}"


def test_prompts_carry_the_load_bearing_rules(tmp_path):
    reg = _reg(tmp_path)
    lead_prompt = reg.get("devsecops-lead").manifest.system_prompt
    # Falsifiable-claims criteria convention + evidence-based review + the one-time
    # board chip are the lead-contract deltas of the sixteenth/seventeenth passes.
    assert "FALSIFIABLE" in lead_prompt
    assert "(board:)" in lead_prompt
    assert "evidence" in lead_prompt.lower()
    for pid in ("appsec-worker", "secrets-worker"):
        prompt = reg.get(pid).manifest.system_prompt
        assert "never print a discovered secret's value" in prompt.lower()
    # Workers talk to the lead, never the end user.
    for pid in ROSTER:
        assert "ask_user" in reg.get(pid).manifest.system_prompt  # the "never use" line


def test_posture_worker_is_read_only_on_cloud(tmp_path):
    prompt = _reg(tmp_path).get("posture-worker").manifest.system_prompt
    assert "read-only" in prompt
    assert "terraform apply" in prompt  # the never-apply rule is written down
