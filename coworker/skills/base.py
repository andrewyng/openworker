"""Skill loading — Anthropic SKILL.md format with progressive disclosure.

A skill is a folder containing `SKILL.md` (YAML frontmatter: name, description,
optional allowed-tools) + a markdown body of instructions + optional resources/scripts.

Progressive disclosure: at session start only the catalog (name + description) is injected
into the agent's context; the full body is loaded on demand via the `load_skill` tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import aisuite as ai
import yaml


@dataclass
class Skill:
    name: str
    description: str
    instructions: str = ""  # full body — loaded on demand
    path: Optional[str] = None  # exact, absolute SKILL.md path
    source: str = "global"
    raw_content: str = ""
    allowed_tools: list[str] = field(default_factory=list)

    @property
    def resources_path(self) -> Optional[str]:
        return str(Path(self.path).parent) if self.path else None


@dataclass(frozen=True)
class SkillRoot:
    path: Path
    source: str = "global"


def skill_roots(workspace: Optional[str | Path] = None) -> list[SkillRoot]:
    """Return skill roots in override order: shared, OpenWorker global, project."""
    from ..secrets import state_dir

    roots = [
        SkillRoot(Path.home() / ".agents" / "skills", "shared"),
        SkillRoot(state_dir() / "skills", "global"),
    ]
    if workspace is not None:
        roots.append(
            SkillRoot(
                Path(workspace).expanduser().resolve() / ".coworker" / "skills",
                "project",
            )
        )
    return roots


class SkillLoader:
    def __init__(self, dirs: list[str | Path | SkillRoot]) -> None:
        self._skills: dict[str, Skill] = {}
        self._skills_by_path: dict[str, Skill] = {}
        self._all: list[Skill] = []
        for item in dirs:
            root = item if isinstance(item, SkillRoot) else SkillRoot(Path(item))
            self._discover(Path(root.path), root.source)

    def _discover(self, directory: Path, source: str) -> None:
        if not directory.is_dir():
            return
        for sub in sorted(directory.iterdir()):
            md = sub / "SKILL.md"
            if md.is_file():
                skill = _parse_skill(md, source=source)
                self._all.append(skill)
                self._skills_by_path[skill.path or ""] = skill
                self._skills[skill.name] = skill

    def names(self) -> list[str]:
        return list(self._skills)

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def resolve(self, name: str, path: str | Path) -> Optional[Skill]:
        exact = str(Path(path).expanduser().resolve())
        skill = self._skills_by_path.get(exact)
        return skill if skill is not None and skill.name == name else None

    def get_path(self, path: str | Path) -> Optional[Skill]:
        return self._skills_by_path.get(str(Path(path).expanduser().resolve()))

    def catalog(self) -> list[dict]:
        return [
            {"name": s.name, "description": s.description}
            for s in self._skills.values()
        ]

    def catalog_all(self) -> list[dict]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "path": s.path,
                "source": s.source,
            }
            for s in self._all
        ]


def _parse_skill(md: Path, *, source: str = "global") -> Skill:
    md = md.expanduser().resolve()
    text = md.read_text(encoding="utf-8")
    name, description, allowed, body = md.parent.name, "", [], text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            frontmatter = text[3:end]
            body = text[end + 4 :].lstrip("\n")
            try:
                metadata = yaml.safe_load(frontmatter) or {}
            except yaml.YAMLError:
                metadata = {}
            if isinstance(metadata, dict):
                parsed_name = metadata.get("name")
                parsed_description = metadata.get("description")
                parsed_allowed = metadata.get(
                    "allowed-tools", metadata.get("allowed_tools", [])
                )
                if parsed_name:
                    name = str(parsed_name)
                if parsed_description is not None:
                    description = str(parsed_description)
                if isinstance(parsed_allowed, str):
                    allowed = [
                        tool.strip()
                        for tool in parsed_allowed.split(",")
                        if tool.strip()
                    ]
                elif isinstance(parsed_allowed, list):
                    allowed = [
                        str(tool).strip()
                        for tool in parsed_allowed
                        if str(tool).strip()
                    ]
    return Skill(
        name=name,
        description=description,
        instructions=body.strip(),
        path=str(md),
        source=source,
        raw_content=text,
        allowed_tools=allowed,
    )


def skill_catalog_text(loader: SkillLoader) -> str:
    catalog = loader.catalog()
    if not catalog:
        return ""
    lines = [f"- {c['name']}: {c['description']}" for c in catalog]
    return (
        "Available skills — call load_skill(name) to load one's full instructions when "
        "it's relevant to the task:\n" + "\n".join(lines)
    )


def skill_tools(loader: SkillLoader) -> list:
    def load_skill(name: str) -> dict:
        """Load a skill's full instructions + resources path by name. Call this when a
        skill from the catalog is relevant to the current task."""
        skill = loader.get(name)
        if skill is None:
            return {"error": f"unknown skill: {name}", "available": loader.names()}
        return {
            "name": skill.name,
            "instructions": skill.instructions,
            "resources_path": skill.resources_path,
        }

    return [
        ai.tool(
            load_skill,
            metadata=ai.ToolMetadata(
                category="skills", risk_level="low", capabilities=["load_skill"]
            ),
        )
    ]
