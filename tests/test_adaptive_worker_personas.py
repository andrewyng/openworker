"""Built-in workers and their localized Skills for adaptive Cowork staffing."""

from coworker.personas.registry import PersonaRegistry
from coworker.skills import SkillLoader
from coworker.skills.builtin import builtin_skills_dir


WORKERS = {
    "goal-planner": "goal-planning",
    "automation-smart-agent": "automation-design",
    "challenges": "flow-nexus-challenges",
    "collective-intelligence": "evidence-synthesis",
}


def test_adaptive_workers_are_hidden_but_available_for_team_staffing():
    registry = PersonaRegistry()

    for persona_id, skill_name in WORKERS.items():
        entry = registry.get(persona_id)
        assert entry is not None
        assert entry.manifest is not None
        assert entry.manifest.team == "worker"
        assert entry.default_surfaced is False
        assert registry.is_enabled(persona_id)
        assert skill_name in entry.manifest.skills


def test_challenges_worker_is_scoped_to_flow_nexus():
    registry = PersonaRegistry()
    manifest = registry.get("challenges").manifest

    assert manifest.mcp == ["flow-nexus"]
    assert [(r.kind, r.ref, r.tier) for r in manifest.recommends] == [
        ("mcp", "flow-nexus", "core")
    ]


def test_adaptive_worker_skills_have_localized_entrypoints():
    folder = builtin_skills_dir()
    for skill_name in WORKERS.values():
        localized = [
            SkillLoader([folder], locale=locale).get(skill_name)
            for locale in ("en-US", "pt-BR", "es-ES")
        ]
        assert all(skill is not None and skill.description for skill in localized)
