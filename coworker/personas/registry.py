"""Persona registry — the installed personas + their lifecycle state.

Unifies two sources behind one `id → Agent` resolver: the core surfaces (Cowork / Code)
wrap their existing agent builders (exact prompts preserved), and markdown manifests
(Ops today; third-party dirs in Phase 2) load through ``PersonaManifest``. Lifecycle —
installed → enabled → surfaced, plus a default — is persisted to a small JSON file.

A session is born from exactly one persona (recorded as ``SessionRecord.agent``); resolving an
id always returns its Agent even if the persona was later disabled, so live sessions keep
working. Disable/surface only affect what the *new-session* picker offers.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..agents.base import Agent
from ..agents.code import CODE_CAPABILITIES, code_agent
from ..agents.cowork import COWORK_CAPABILITIES, cowork_agent
from .manifest import PersonaManifest, load_manifest_file

DEFAULT_PERSONA_ID = "cowork"


def include_unshipped() -> bool:
    """Internal builds opt ships:false coworkers in (owner, 2026-08-21). A release
    build never sets this, so unshipped personas simply do not exist there."""
    return os.environ.get("OPENWORKER_UNSHIPPED", "").strip().lower() not in (
        "",
        "0",
        "false",
    )


@dataclass
class PersonaState:
    enabled: bool = True
    surfaced: bool = True


@dataclass
class PersonaEntry:
    id: str
    name: str
    icon: str = ""
    tagline: str = ""
    builtin: bool = True
    # Workspace/toolset traits (workspace-scratch-design.md): requires_folder is the
    # composer/engine gate on a user-picked primary folder — surfaced to the GUI, which
    # groups gated sessions by project. subagents/scheduling gate the matching toolsets.
    requires_folder: bool = False
    subagents: bool = False
    scheduling: bool = True
    tools: list[str] = field(default_factory=list)
    default_surfaced: bool = (
        True  # whether it shows in the picker before any user choice
    )
    # Whether it ships enabled before any user choice. Builtins default on (UX-029: the
    # composer picker is their front door) — except Code (owner call 2026-08-21: ships
    # disabled). Installed third-party personas always start disabled pending consent.
    default_enabled: bool = True
    # Distribution flag (owner, 2026-08-21): ships:false = absent from release builds.
    ships: bool = True
    # Settings-page grouping ("general" | "security") — cosmetic only.
    group: str = "general"
    _builder: Optional[Callable[[], Agent]] = None
    manifest: Optional[PersonaManifest] = None

    def agent(self) -> Agent:
        if self._builder is not None:
            return self._builder()
        assert self.manifest is not None
        return self.manifest.to_agent()


class PersonaRegistry:
    def __init__(
        self,
        *,
        builtin_dir: Optional[str | Path] = None,
        extra_dirs: Optional[list[str | Path]] = None,
        state_path: Optional[str | Path] = None,
        installed_dir: Optional[str | Path] = None,
    ) -> None:
        self.state_path = Path(state_path) if state_path else None
        # Managed area where installed personas are *snapshotted* (copied) at install time, so a
        # persona's definition is stable and self-contained — independent of the user's source dir.
        if installed_dir is not None:
            self.installed_dir: Optional[Path] = Path(installed_dir)
        elif self.state_path is not None:
            self.installed_dir = self.state_path.parent / "personas-installed"
        else:
            self.installed_dir = None
        self._entries: dict[str, PersonaEntry] = {}
        self._enabled: dict[str, bool] = {}
        self._surfaced: dict[str, bool] = {}
        # Sharing v1 (OPE-7): install provenance per installed persona —
        # {version, source, installed_at} — drives the "replaces vN" note on re-install.
        self._installed_meta: dict[str, dict] = {}
        self._default = DEFAULT_PERSONA_ID
        self._load_builtin(builtin_dir)
        for d in extra_dirs or []:
            self._load_dir(d, builtin=False)
        self._load_state()
        self._load_installed()  # re-load snapshots from prior installs

    # -- loading ----------------------------------------------------------------
    def _register_builder(
        self,
        id,
        name,
        icon,
        tagline,
        builder,
        tools,
        requires_folder=False,
        subagents=False,
        scheduling=True,
        default_surfaced=True,
        default_enabled=True,
        group="general",
    ) -> None:
        self._entries[id] = PersonaEntry(
            id=id,
            name=name,
            icon=icon,
            tagline=tagline,
            builtin=True,
            requires_folder=requires_folder,
            subagents=subagents,
            scheduling=scheduling,
            tools=list(tools),
            default_surfaced=default_surfaced,
            default_enabled=default_enabled,
            group=group,
            _builder=builder,
        )

    def _load_builtin(self, builtin_dir: Optional[str | Path]) -> None:
        # Core surfaces keep their exact prompts via the existing builders. Cowork (the
        # default) leads. Chat is GONE (owner call 2026-08-21; retired-but-listed since
        # 2026-08-11) — stray `persona=chat` session ids resolve to the default via
        # agent()'s unknown-id fallback. Code ships disabled + unsurfaced (same owner
        # call): OpenWorker is the launch generalist, but Code stays one checkbox away
        # as the only plain work-in-my-repo persona.
        self._register_builder(
            "cowork",
            "OpenWorker",
            "cowork",
            "Produce a deliverable — research, analysis, scripts",
            cowork_agent,
            COWORK_CAPABILITIES,
        )
        self._register_builder(
            "code",
            "Code",
            "code",
            "Work in a codebase — files, git, shell",
            code_agent,
            CODE_CAPABILITIES,
            requires_folder=True,
            subagents=True,
            scheduling=False,
            default_surfaced=False,
            default_enabled=False,
        )
        # Markdown-backed built-ins (Ops, …) — dogfood the manifest path.
        d = Path(builtin_dir) if builtin_dir else Path(__file__).parent / "builtin"
        self._load_dir(d, builtin=True)

    def _load_dir(self, directory: str | Path, *, builtin: bool) -> None:
        d = Path(directory)
        if not d.is_dir():
            return
        for md in sorted(d.glob("*.md")):
            self._register_manifest(
                load_manifest_file(md, builtin=builtin), builtin=builtin
            )
        # Bundle subdirs (OPE-58): <dir>/<id>/manifest.md with an optional sibling
        # skills/ folder — the same self-contained shape an install snapshot uses, so a
        # persona's skills live with it instead of leaking into a shared flat dir.
        for sub in sorted(p for p in d.iterdir() if p.is_dir()):
            md = sub / "manifest.md"
            if md.is_file():
                self._register_manifest(
                    load_manifest_file(md, builtin=builtin), builtin=builtin
                )

    def _register_manifest(self, m, *, builtin: bool) -> None:
        self._entries[m.id] = PersonaEntry(
            id=m.id,
            name=m.name,
            icon=m.icon,
            tagline=m.tagline,
            builtin=builtin,
            requires_folder=m.requires_folder,
            subagents=m.subagents,
            scheduling=m.scheduling,
            tools=list(m.tools),
            ships=m.ships,
            group=m.group,
            manifest=m,
            # Team workers never surface in the picker: they are purpose-built to be
            # STAFFED by a lead, not started solo (their prompts talk to a lead, not
            # a human). They stay enabled so the staffing gate can resolve them.
            default_surfaced=m.team != "worker",
        )

    def _load_installed(self) -> None:
        if not (self.installed_dir and self.installed_dir.is_dir()):
            return
        for sub in sorted(self.installed_dir.iterdir()):
            if sub.is_dir():
                self._load_dir(sub, builtin=False)

    def _load_state(self) -> None:
        if self.state_path and self.state_path.is_file():
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._enabled = dict(data.get("enabled", {}))
            self._surfaced = dict(data.get("surfaced", {}))
            self._installed_meta = dict(data.get("installed_meta", {}))
            self._default = data.get("default", DEFAULT_PERSONA_ID)

    def save(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "enabled": self._enabled,
                    "surfaced": self._surfaced,
                    "installed_meta": self._installed_meta,
                    "default": self._default,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # -- queries ----------------------------------------------------------------
    def _visible(self, e: PersonaEntry) -> bool:
        # Unshipped personas surface only on internal builds — except one a user
        # already enabled (an internal-build choice must not vanish under them).
        return e.ships or include_unshipped() or self._enabled.get(e.id) is True

    def ids(self) -> list[str]:
        return list(self._entries)

    def get(self, persona_id: str) -> Optional[PersonaEntry]:
        return self._entries.get(persona_id)

    def media_dir(self, persona_id: str) -> Optional[Path]:
        """The persona bundle's media/ folder (screenshots for the detail page), if any.
        Only manifest-backed personas have one — it sits beside their manifest.md."""
        entry = self._entries.get(persona_id)
        if entry is None or entry.manifest is None or not entry.manifest.source:
            return None
        d = Path(entry.manifest.source).parent / "media"
        return d if d.is_dir() else None

    def is_enabled(self, persona_id: str) -> bool:
        # Explicit state (either way) always wins. Absent a user choice, the entry's
        # default applies: builtins ship enabled — the composer picker is their front door
        # (UX-029, supersedes the 2026-07-09 Coworker-only default that fit the old hidden
        # ▾ menu) — except ones registered default-off (Code). Installed third-party
        # personas stay disabled until the user consents from the risk screen.
        if persona_id in self._enabled:
            return bool(self._enabled[persona_id])
        entry = self._entries.get(persona_id)
        if entry is not None and entry.builtin:
            return entry.default_enabled
        return persona_id == self._default or persona_id == DEFAULT_PERSONA_ID

    def is_surfaced(self, persona_id: str) -> bool:
        # User choice wins; otherwise the persona's default (Chat defaults hidden).
        if persona_id in self._surfaced:
            return self._surfaced[persona_id]
        entry = self._entries.get(persona_id)
        return entry.default_surfaced if entry else True

    def default_id(self) -> str:
        # The configured default if it's enabled, else cowork if present, else any enabled one.
        if self._default in self._entries and self.is_enabled(self._default):
            return self._default
        if DEFAULT_PERSONA_ID in self._entries and self.is_enabled(DEFAULT_PERSONA_ID):
            return DEFAULT_PERSONA_ID
        for pid in self._entries:
            if self.is_enabled(pid):
                return pid
        return DEFAULT_PERSONA_ID

    def agent(self, persona_id: Optional[str]) -> Agent:
        """Resolve a persona id to its Agent. Unknown ids fall back to the default persona;
        a known-but-disabled id still resolves (live sessions keep working)."""
        entry = self._entries.get(persona_id or "")
        if entry is None:
            entry = self._entries.get(self.default_id())
        if entry is None:
            raise KeyError(f"no persona to resolve for {persona_id!r}")
        return entry.agent()

    def sidebar(self) -> list[dict]:
        """Session surfaces for the new-session picker: enabled AND surfaced, in order."""
        out = []
        for e in self._entries.values():
            if self._visible(e) and self.is_enabled(e.id) and self.is_surfaced(e.id):
                out.append(
                    {
                        "name": e.id,
                        "title": e.name,
                        "requires_folder": e.requires_folder,
                        "icon": e.icon,
                        "tagline": e.tagline,
                        "default": e.id == self.default_id(),
                    }
                )
        return out

    def list_all(self) -> list[dict]:
        """Every installed persona + its lifecycle state — for the Personas settings panel."""
        return [
            {
                "id": e.id,
                "name": e.name,
                "icon": e.icon,
                "tagline": e.tagline,
                "requires_folder": e.requires_folder,
                "builtin": e.builtin,
                "tools": e.tools,
                "enabled": self.is_enabled(e.id),
                "surfaced": self.is_surfaced(e.id),
                "default": e.id == self.default_id(),
                "ships": e.ships,
                "group": e.group,
                "version": e.manifest.version if e.manifest else "",
                "installed_at": self._installed_meta.get(e.id, {}).get("installed_at", ""),
            }
            for e in self._entries.values()
            if self._visible(e)
        ]

    # -- mutations --------------------------------------------------------------
    def set_enabled(self, persona_id: str, enabled: bool) -> None:
        if persona_id not in self._entries:
            raise KeyError(persona_id)
        self._enabled[persona_id] = bool(enabled)
        if enabled:
            # Enabling implies surfacing (installs land unsurfaced, and "enabled but
            # invisible in the picker" is never what a user just asked for). They can
            # still untick "In picker" afterwards to hide it.
            self._surfaced[persona_id] = True
        self.save()

    def set_surfaced(self, persona_id: str, surfaced: bool) -> None:
        if persona_id not in self._entries:
            raise KeyError(persona_id)
        self._surfaced[persona_id] = bool(surfaced)
        self.save()

    def set_default(self, persona_id: str) -> None:
        if persona_id not in self._entries:
            raise KeyError(persona_id)
        self._default = persona_id
        self._enabled[persona_id] = True  # a default must be enabled
        self.save()

    def uninstall(self, persona_id: str) -> None:
        """Remove an installed persona: registry entry, lifecycle state, and its snapshot
        dir. Built-ins can't be uninstalled (disable them instead). Live sessions born
        from it resolve to the default persona afterwards (same as any unknown id)."""
        entry = self._entries.get(persona_id)
        if entry is None:
            raise KeyError(persona_id)
        if entry.builtin:
            raise ValueError(f"{persona_id} is built-in and cannot be deleted")
        del self._entries[persona_id]
        self._enabled.pop(persona_id, None)
        self._surfaced.pop(persona_id, None)
        if self._default == persona_id:
            self._default = DEFAULT_PERSONA_ID
        if self.installed_dir is not None:
            snap = self.installed_dir / persona_id
            if snap.is_dir():
                shutil.rmtree(snap)
        self.save()

    # -- install (third-party personas) -----------------------------------------
    def install_from_dir(self, directory: str | Path) -> list[dict]:
        """Install persona(s) from a local directory by **snapshotting** their manifests into our
        managed area (so the definition is stable, independent of the source dir). Returns a
        consent summary per persona; each lands **disabled + unsurfaced** pending the user's
        consent — the caller enables them only after the user approves the declared capabilities.

        NOTE: re-installing an updated persona overwrites the snapshot; live sessions on it simply
        resume with the new prompt/tools. We accept that for now (see PERSONAS.md)."""
        from .loading import consent_summary

        d = Path(directory)
        if not d.is_dir():
            raise FileNotFoundError(f"not a directory: {d}")
        mds = sorted(d.glob("*.md"))
        if not mds:
            raise FileNotFoundError(f"no persona manifests (*.md) in {d}")

        summaries: list[dict] = []
        for md in mds:
            m = load_manifest_file(md, builtin=False)  # validate before snapshotting
            replaces = self._replaces_of(m)
            snapshot = self._snapshot(md, m.id)
            installed = load_manifest_file(snapshot, builtin=False) if snapshot else m
            self._register_manifest(installed, builtin=False)
            # Consent rules (sharing v1): a fresh install always lands disabled pending
            # consent. An UPDATE keeps the user's enabled state — unless its capability
            # set GREW, which is a new decision, never a silent upgrade.
            if replaces is None or replaces.get("capabilities_grew"):
                self._enabled[m.id] = False
                self._surfaced[m.id] = False
            self._installed_meta[m.id] = {
                "version": installed.version,
                "source": str(md),
                "installed_at": self._now_stamp(),
            }
            summary = consent_summary(installed)
            summary["replaces"] = replaces
            summaries.append(summary)
        self.save()
        return summaries

    @staticmethod
    def _now_stamp() -> str:
        from datetime import date

        return date.today().isoformat()

    def _replaces_of(self, incoming) -> Optional[dict]:
        """When re-installing an already-installed persona id: what the new copy
        replaces ({version, installed_at, capabilities_grew}), else None."""
        from .loading import capability_set

        existing = self._entries.get(incoming.id)
        if existing is None or existing.builtin or existing.manifest is None:
            return None
        meta = self._installed_meta.get(incoming.id, {})
        grew = bool(capability_set(incoming) - capability_set(existing.manifest))
        return {
            "version": meta.get("version") or existing.manifest.version or "",
            "installed_at": meta.get("installed_at", ""),
            "capabilities_grew": grew,
        }

    def export_persona(self, persona_id: str, dest_dir: str | Path) -> dict:
        """Sharing v1 export: zip the persona's bundle (manifest + skills/) into
        ``dest_dir``. The zip's contents ARE the import format — extract or point the
        installer at it and the round trip is lossless."""
        import zipfile

        entry = self._entries.get(persona_id)
        if entry is None or entry.manifest is None or not entry.manifest.source:
            return {"ok": False, "error": "this coworker has no shareable bundle"}
        src_md = Path(entry.manifest.source)
        if not src_md.is_file():
            return {"ok": False, "error": "the coworker's bundle files are missing"}
        dest = Path(dest_dir).expanduser()
        if not dest.is_dir():
            return {"ok": False, "error": "destination folder does not exist"}
        version = entry.manifest.version
        zip_name = f"{persona_id}-coworker{('-v' + version) if version else ''}.zip"
        zip_path = dest / zip_name
        skills_dir = src_md.parent / "skills"
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(src_md, "manifest.md")
                if skills_dir.is_dir():
                    for p in sorted(skills_dir.rglob("*")):
                        if p.is_file():
                            zf.write(p, str(Path("skills") / p.relative_to(skills_dir)))
        except OSError as e:
            return {"ok": False, "error": f"could not write the archive: {e}"}
        return {"ok": True, "path": str(zip_path)}

    def install_from_zip(self, data: bytes, filename: str = "") -> list[dict]:
        """Install persona(s) from a shared bundle zip (the export format). The archive
        is extracted to a temp dir with a zip-slip guard, then installed like a local
        directory — landing disabled pending consent like every install."""
        import io
        import tempfile
        import zipfile

        with tempfile.TemporaryDirectory(prefix="ocw-persona-zip-") as tmp:
            root = Path(tmp)
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for info in zf.infolist():
                        name = info.filename
                        target = (root / name).resolve()
                        if not str(target).startswith(str(root.resolve())):
                            raise FileNotFoundError(f"unsafe path in archive: {name}")
                    zf.extractall(root)
            except zipfile.BadZipFile as e:
                raise FileNotFoundError(f"not a valid bundle archive: {e}") from e
            # Accept both layouts: files at the root, or a single wrapping folder
            # (how macOS zips a directory).
            candidates = [root, *[p for p in root.iterdir() if p.is_dir()]]
            for d in candidates:
                if list(d.glob("*.md")) or (d / "manifest.md").is_file():
                    return self.install_from_dir(d)
            raise FileNotFoundError(
                f"no persona manifest found in {filename or 'the archive'}"
            )

    def _snapshot(self, md: Path, persona_id: str) -> Optional[Path]:
        """Copy a manifest into the managed install area; return the snapshot path (or None if no
        managed area is configured, e.g. an ephemeral in-memory registry)."""
        if self.installed_dir is None:
            return None
        dest_dir = self.installed_dir / persona_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "manifest.md"
        shutil.copy2(md, dest)
        # Bundle shape (OPE-58 / sharing v1): a `skills/` dir next to the manifest travels
        # with the snapshot, so a persona's skills stay stable independent of the source.
        src_skills = md.parent / "skills"
        if src_skills.is_dir():
            shutil.copytree(src_skills, dest_dir / "skills", dirs_exist_ok=True)
        return dest

    def install_from_git(
        self, url: str, *, cache_base: Optional[str | Path] = None, clone=None
    ) -> list[dict]:
        """Clone a persona repo and install its personas (disabled pending consent)."""
        from .loading import clone_persona_repo, git_clone

        base = (
            Path(cache_base)
            if cache_base
            else (
                (self.state_path.parent if self.state_path else Path.cwd())
                / "persona-cache"
            )
        )
        dest = clone_persona_repo(url, base, clone=clone or git_clone)
        return self.install_from_dir(dest)


# -- module singleton (used by agents.get_agent / list_agents) ------------------
_singleton: Optional[PersonaRegistry] = None


def get_registry() -> PersonaRegistry:
    global _singleton
    if _singleton is None:
        from ..secrets import state_dir

        _singleton = PersonaRegistry(state_path=state_dir() / "personas.json")
    return _singleton


def set_registry(registry: PersonaRegistry) -> None:
    """Install a registry as the process singleton (the manager does this with its data dir)."""
    global _singleton
    _singleton = registry
