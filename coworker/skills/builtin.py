"""Worker-owned, read-only skills distributed with the application."""

from pathlib import Path


BUILTIN_SCOPE = "builtin"
SKILL_CREATOR_NAME = "skill-creator"
RESERVED_SKILL_NAMES = {SKILL_CREATOR_NAME}


def builtin_skills_dir() -> Path:
    return Path(__file__).resolve().parent / "builtin"


def builtin_skill_dirs() -> list[Path]:
    root = builtin_skills_dir()
    return [root] if root.is_dir() else []
