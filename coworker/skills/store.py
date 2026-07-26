"""Skill management — CRUD over skill folders + per-session mutes (SKILLS-SPEC §4).

Scope = folder location (folder-is-truth): global skills live in ``state_dir()/skills``,
project skills in ``<workspace>/.coworker/skills``. There is no database; every operation
is a folder + ``SKILL.md`` operation, which keeps project skills shareable via git for free.

Disable state is deliberately NOT a marker inside the skill folder: project folders travel
with the repo and one user's disable must not be committed to teammates. It lives in the
personal ``state_dir()/skills-settings.json`` instead.

Uploads are staged (parse → preview → confirm) so the user always reviews exactly what will
be saved before anything lands in a scope dir. Staged content sits under
``state_dir()/skills-staged/<token>`` until confirmed or discarded.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any, Optional

from ..secrets import state_dir
from .base import Skill, _parse_skill

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MAX_NAME = 64
GLOBAL_SCOPE = "global"
PROJECT_SCOPE = "project"


def validate_name(name: str) -> str:
    """Skill names become folder names — reject anything that could escape the scope dir."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Skill name is required.")
    if len(name) > _MAX_NAME:
        raise ValueError(f"Skill name too long (limit {_MAX_NAME} characters).")
    if ".." in name or "/" in name or "\\" in name or not _NAME_RE.match(name):
        raise ValueError(
            "Skill name may only contain letters, digits, dots, dashes, and underscores."
        )
    return name


def _frontmatter_source(md: Path) -> str:
    """Read the optional ``source:`` frontmatter key (``uploaded`` etc.). Absent → created here."""
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    for line in text[3:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            if key.strip().lower() == "source":
                return value.strip()
    return ""


def _write_skill_md(
    folder: Path, *, name: str, description: str, instructions: str, source: str = ""
) -> None:
    lines = ["---", f"name: {name}", f"description: {description}"]
    if source:
        lines.append(f"source: {source}")
    lines += ["---", "", instructions.strip(), ""]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


class SkillStore:
    """Folder-backed skill CRUD across the global + project scopes."""

    def __init__(self, global_dir: Optional[str | Path] = None) -> None:
        self.global_dir = Path(global_dir) if global_dir else state_dir() / "skills"
        self._settings_path = state_dir() / "skills-settings.json"
        self._staging_dir = state_dir() / "skills-staged"
        self._lock = threading.Lock()

    # -- scope dirs ---------------------------------------------------------------
    def project_dir(self, workspace: str | Path) -> Path:
        return Path(workspace).expanduser().resolve() / ".coworker" / "skills"

    def _base(self, scope: str, workspace: Optional[str | Path]) -> Path:
        if scope == GLOBAL_SCOPE:
            return self.global_dir
        if scope == PROJECT_SCOPE:
            if not workspace:
                raise ValueError("A workspace is required for a project-scoped skill.")
            ws = Path(workspace).expanduser()
            if not ws.is_dir():
                raise ValueError(f"Unknown workspace: {workspace}")
            return self.project_dir(ws)
        raise ValueError(f"Unknown scope: {scope}")

    def _folder_of(self, base: Path, name: str) -> Path:
        """The skill's folder, guarded against escaping its scope dir (symlinked folders
        that resolve elsewhere are treated as absent rather than followed)."""
        folder = base / name
        try:
            resolved = folder.resolve()
            base_resolved = base.resolve()
        except OSError:
            raise ValueError(f"Unreadable skill folder: {name}")
        if base_resolved not in resolved.parents and resolved != base_resolved / name:
            raise ValueError(f"Skill folder escapes its scope: {name}")
        return folder

    # -- queries ------------------------------------------------------------------
    def find(
        self, name: str, workspace: Optional[str | Path] = None
    ) -> tuple[Path, str]:
        """Locate a skill by name, most-local first (project before global) — mirrors the
        loader's collision precedence so management operates on the copy the model sees."""
        name = validate_name(name)
        if workspace:
            project = self.project_dir(Path(workspace).expanduser())
            if (project / name / "SKILL.md").is_file():
                return self._folder_of(project, name), PROJECT_SCOPE
        if (self.global_dir / name / "SKILL.md").is_file():
            return self._folder_of(self.global_dir, name), GLOBAL_SCOPE
        raise ValueError(f"Unknown skill: {name}")

    def rows(self, workspace: Optional[str | Path] = None) -> list[dict[str, Any]]:
        """Enriched listing for the Settings screen: scope, source, enabled. Global first,
        then project (a project row with a colliding name is the effective copy)."""
        disabled = self.disabled_names()
        out: list[dict[str, Any]] = []
        seen: dict[str, int] = {}
        scopes: list[tuple[Path, str]] = [(self.global_dir, GLOBAL_SCOPE)]
        if workspace:
            scopes.append((self.project_dir(Path(workspace).expanduser()), PROJECT_SCOPE))
        for base, scope in scopes:
            if not base.is_dir():
                continue
            for sub in sorted(base.iterdir()):
                md = sub / "SKILL.md"
                if not md.is_file():
                    continue
                skill = _parse_skill(md)
                row = {
                    "name": skill.name,
                    "description": skill.description,
                    "instructions": skill.instructions,  # Settings editor prefill
                    "scope": scope,
                    "source": _frontmatter_source(md) or "local",
                    "enabled": skill.name not in disabled,
                    "path": str(sub),
                }
                if skill.name in seen:  # project copy shadows the global one
                    out[seen[skill.name]] = row
                else:
                    seen[skill.name] = len(out)
                    out.append(row)
        return out

    # -- mutations ----------------------------------------------------------------
    def create(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        scope: str = GLOBAL_SCOPE,
        workspace: Optional[str | Path] = None,
        source: str = "",
    ) -> dict[str, Any]:
        name = validate_name(name)
        description = (description or "").strip()
        if not (instructions or "").strip():
            raise ValueError("Skill instructions are required.")
        base = self._base(scope, workspace)
        folder = self._folder_of(base, name)
        if (folder / "SKILL.md").is_file():
            raise ValueError(f"A skill named '{name}' already exists in that scope.")
        _write_skill_md(
            folder,
            name=name,
            description=description,
            instructions=instructions,
            source=source,
        )
        return {"name": name, "scope": scope, "path": str(folder)}

    def update(
        self,
        name: str,
        *,
        description: Optional[str] = None,
        instructions: Optional[str] = None,
        workspace: Optional[str | Path] = None,
    ) -> dict[str, Any]:
        """Rewrite SKILL.md fields in place; sibling resource files are untouched."""
        folder, scope = self.find(name, workspace)
        current = _parse_skill(folder / "SKILL.md")
        if instructions is not None and not instructions.strip():
            raise ValueError("Skill instructions are required.")
        _write_skill_md(
            folder,
            name=current.name,
            description=(
                description if description is not None else current.description
            ),
            instructions=(
                instructions if instructions is not None else current.instructions
            ),
            source=_frontmatter_source(folder / "SKILL.md"),
        )
        return {"name": current.name, "scope": scope}

    def delete(self, name: str, workspace: Optional[str | Path] = None) -> None:
        folder, _scope = self.find(name, workspace)
        if folder.is_symlink():  # never follow a link out of the scope dir
            folder.unlink()
            return
        shutil.rmtree(folder)

    def move(
        self,
        name: str,
        *,
        to_scope: str,
        workspace: Optional[str | Path] = None,
    ) -> dict[str, Any]:
        folder, from_scope = self.find(name, workspace)
        if from_scope == to_scope:
            return {"name": name, "scope": to_scope}
        target_base = self._base(to_scope, workspace)
        target = self._folder_of(target_base, name)
        if (target / "SKILL.md").is_file():
            raise ValueError(
                f"A skill named '{name}' already exists in the target scope."
            )
        target_base.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder), str(target))
        return {"name": name, "scope": to_scope}

    # -- enable / disable (personal, survives restarts) -----------------------------
    def disabled_names(self) -> set[str]:
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            return {str(n) for n in data.get("disabled", [])}
        except (OSError, ValueError):
            return set()

    def set_enabled(self, name: str, enabled: bool) -> None:
        name = validate_name(name)
        with self._lock:
            disabled = self.disabled_names()
            if enabled:
                disabled.discard(name)
            else:
                disabled.add(name)
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(
                json.dumps({"disabled": sorted(disabled)}, indent=2),
                encoding="utf-8",
            )

    # -- uploads: stage → preview → confirm -----------------------------------------
    def stage_upload(self, data: bytes) -> dict[str, Any]:
        """Unpack an uploaded zip into the staging area and return the parsed preview.
        Nothing is installed until :meth:`confirm_upload`."""
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError("Not a valid .zip archive.")
        names = [n for n in archive.namelist() if not n.endswith("/")]
        for entry in names:
            p = Path(entry)
            if p.is_absolute() or ".." in p.parts or (p.parts and ":" in p.parts[0]):
                raise ValueError("Archive contains unsafe paths.")
        # SKILL.md at the root, or inside exactly one top-level folder.
        md_entries = [n for n in names if Path(n).name == "SKILL.md"]
        roots = {Path(n).parts[0] if len(Path(n).parts) > 1 else "" for n in md_entries}
        if not md_entries or len(roots) != 1:
            raise ValueError("Archive must contain exactly one skill (one SKILL.md).")
        root = roots.pop()
        token = uuid.uuid4().hex
        staged = self._staging_dir / token
        staged.mkdir(parents=True, exist_ok=True)
        for entry in names:
            parts = Path(entry).parts
            rel = Path(*parts[1:]) if root and parts[0] == root else Path(entry)
            if not str(rel):
                continue
            target = staged / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
        skill = _parse_skill(staged / "SKILL.md")
        name = skill.name if skill.name else staged.name
        try:
            validate_name(name)
        except ValueError:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        extras = sorted(
            str(p.relative_to(staged))
            for p in staged.rglob("*")
            if p.is_file() and p.name != "SKILL.md"
        )
        return {
            "token": token,
            "name": name,
            "description": skill.description,
            "instructions": skill.instructions,
            "files": extras,
        }

    def confirm_upload(
        self,
        token: str,
        *,
        scope: str = GLOBAL_SCOPE,
        workspace: Optional[str | Path] = None,
    ) -> dict[str, Any]:
        staged = self._staging_dir / str(token)
        if not (staged / "SKILL.md").is_file():
            raise ValueError("Unknown or expired upload.")
        skill = _parse_skill(staged / "SKILL.md")
        name = validate_name(skill.name)
        base = self._base(scope, workspace)
        folder = self._folder_of(base, name)
        if (folder / "SKILL.md").is_file():
            raise ValueError(f"A skill named '{name}' already exists in that scope.")
        base.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), str(folder))
        # Stamp provenance so the Settings screen can distinguish uploaded from local.
        if not _frontmatter_source(folder / "SKILL.md"):
            _write_skill_md(
                folder,
                name=name,
                description=skill.description,
                instructions=skill.instructions,
                source="uploaded",
            )
        return {"name": name, "scope": scope, "path": str(folder)}

    def discard_upload(self, token: str) -> None:
        staged = self._staging_dir / str(token)
        shutil.rmtree(staged, ignore_errors=True)


class SessionSkillStore:
    """``{session_id: {skill: bool}}`` — per-session mutes only; an absent entry means the
    session inherits (enabled unless disabled in Settings). Mirrors SessionConnectionStore."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, bool]] = {}
        self._load()

    def _load(self) -> None:
        if self.path and self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            self._rows = {
                sid: {str(s): bool(v) for s, v in (row or {}).items()}
                for sid, row in data.get("sessions", {}).items()
            }

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"sessions": self._rows}, indent=2), encoding="utf-8"
        )

    def get(self, session_id: str) -> dict[str, bool]:
        return dict(self._rows.get(session_id, {}))

    def set(self, session_id: str, skill: str, enabled: bool) -> None:
        with self._lock:
            self._rows.setdefault(session_id, {})[skill] = bool(enabled)
            self._save()

    def clear(self, session_id: str, skill: str) -> None:
        with self._lock:
            row = self._rows.get(session_id)
            if row and skill in row:
                del row[skill]
                if not row:
                    del self._rows[session_id]
                self._save()

    def remove_session(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._rows:
                del self._rows[session_id]
                self._save()


def effective_skills(
    *,
    names: set[str],
    disabled: set[str],
    session_overrides: dict[str, bool],
) -> set[str]:
    """The single source of truth for a session's skill menu (SKILLS-SPEC §3): any-off-wins.
    A Settings disable removes the skill everywhere — a session override can NOT resurrect
    it. Absent any opinion, a skill is on."""
    out: set[str] = set()
    for name in names:
        if name in disabled:
            continue
        if not session_overrides.get(name, True):
            continue
        out.add(name)
    return out
