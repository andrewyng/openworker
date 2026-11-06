"""Development-only shared skill contract against the checked-in Python reference."""
import json
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
from coworker.skills.base import _parse_skill, SkillLoader, skill_catalog_text, skill_tools

fixtures = json.loads(Path(__file__).with_name("skills-contract.json").read_text())
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    for group in ["shared", "differences"]:
        for case in fixtures[group]:
            folder = root / case["folder"]
            folder.mkdir(exist_ok=True)
            path = folder / "SKILL.md"
            path.write_text(case["text"])
            skill = _parse_skill(path)
            if group == "shared":
                for key in ["name", "description", "instructions", "allowed_tools"]:
                    assert getattr(skill, key) == case[key], (case, key)
            else:
                for key in ["name", "description", "allowed_tools"]:
                    if "python_" + key in case:
                        assert getattr(skill, key) == case["python_" + key], (case, key)
    path.write_text("---\nname: report\ndescription: Report procedure\n---\nBODY_NOT_IN_CATALOG")
    loader = SkillLoader([root])
    assert "BODY_NOT_IN_CATALOG" not in skill_catalog_text(loader)
print(f"PASS: {len(fixtures['shared'])} shared skill parsing cases; {len(fixtures['differences'])} explicit Python/native differences; lazy catalog")
