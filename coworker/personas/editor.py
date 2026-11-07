"""Structured authoring over the standard manifest + install/consent pipeline."""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Any

import yaml

from .loading import consent_summary
from .manifest import ManifestError, PersonaManifest, parse_manifest
from .registry import PersonaRegistry

TEXT_FIELDS = {
    "id",
    "name",
    "icon",
    "tagline",
    "description",
    "system_prompt",
    "team",
    "approval_guidance",
    "default_permission_mode",
    "version",
    "group",
}
LIST_FIELDS = {"tools", "connectors", "models", "skills", "mcp"}
BOOL_FIELDS = {"requires_folder", "subagents", "scheduling"}
_SAVE_LOCK = Lock()


def definition_for(
    registry: PersonaRegistry, persona_id: str, connector_ids: list[str]
) -> dict[str, Any]:
    entry = registry.get(persona_id)
    if entry is None:
        raise ValueError(f"unknown persona: {persona_id}")
    if entry.manifest:
        data = asdict(entry.manifest)
    else:
        agent = entry.agent()
        data = asdict(
            PersonaManifest(
                id=entry.id,
                name=entry.name,
                icon=entry.icon,
                tagline=entry.tagline,
                system_prompt=agent.system_prompt,
                tools=list(entry.tools),
                connectors=agent.connectors,
                requires_folder=agent.requires_folder,
                subagents=agent.subagents,
                scheduling=agent.scheduling,
                team=agent.team,
                approval_guidance=agent.approval_guidance,
            )
        )
    # Shared personas cannot use the built-in-only wildcard. Copying a generalist
    # freezes an explicit list, so future connectors are not silently granted.
    connectors = data["connectors"]
    data["connectors"] = (
        list(connector_ids) if connectors is True else list(connectors or ())
    )
    data["team"] = data["team"] or ""
    if data["default_permission_mode"] == "auto":
        data["default_permission_mode"] = "bypass-approvals"
    if not data["skills"] and entry.manifest and entry.manifest.source:
        # Legacy bundles with no explicit allowlist activate every bundled skill.
        data["skills"] = sorted(
            p.parent.name
            for p in (Path(entry.manifest.source).parent / "skills").glob("*/SKILL.md")
        )
    return {
        key: value
        for key, value in data.items()
        if key in TEXT_FIELDS | LIST_FIELDS | BOOL_FIELDS | {"recommends"}
    }


def validate_definition(data: Any) -> tuple[PersonaManifest, str]:
    if not isinstance(data, dict):
        raise ManifestError("definition must be an object")
    unknown = data.keys() - (TEXT_FIELDS | LIST_FIELDS | BOOL_FIELDS | {"recommends"})
    if unknown:
        raise ManifestError(f"unknown editor fields: {', '.join(sorted(unknown))}")
    for key in TEXT_FIELDS & data.keys():
        if not isinstance(data[key], str):
            raise ManifestError(f"{key} must be text")
        limit = 100_000 if key == "system_prompt" else 10_000
        if len(data[key]) > limit:
            raise ManifestError(f"{key} is too long (maximum {limit} characters)")
    for key in ("id", "name", "system_prompt"):
        if not data.get(key, "").strip():
            raise ManifestError(f"{key} is required")
    for key in BOOL_FIELDS & data.keys():
        if type(data[key]) is not bool:
            raise ManifestError(f"{key} must be a boolean")
    for key in LIST_FIELDS & data.keys():
        value = data[key]
        if not isinstance(value, list) or any(
            not isinstance(v, str) or not v.strip() for v in value
        ):
            raise ManifestError(f"{key} must be a list of non-empty strings")
    for skill in data.get("skills", []):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}", skill):
            raise ManifestError("skills must be bundle skill names, not paths")
    if data.get("version") and not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", data["version"]
    ):
        raise ManifestError(
            "version must use letters, numbers, dots, hyphens or underscores"
        )
    recommendations = data.get("recommends", [])
    if not isinstance(recommendations, list):
        raise ManifestError("recommends must be a list")
    raw_recommendations = []
    for rec in recommendations:
        if (
            not isinstance(rec, dict)
            or not isinstance(rec.get("kind"), str)
            or rec["kind"] not in {"connector", "mcp"}
        ):
            raise ManifestError("each recommendation needs a connector or mcp kind")
        for key in ("ref", "reason", "tier"):
            if not isinstance(rec.get(key, ""), str):
                raise ManifestError(f"recommendation {key} must be text")
        raw_recommendations.append(
            {
                rec["kind"]: rec.get("ref", ""),
                "reason": rec.get("reason", ""),
                "tier": rec.get("tier", "optional"),
            }
        )
    meta = {key: value for key, value in data.items() if key != "system_prompt"}
    meta["recommends"] = raw_recommendations
    # Use YAML's serializer, never interpolate user input into frontmatter keys.
    markdown = (
        "---\n"
        + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
        + "---\n"
        + data["system_prompt"].strip()
        + "\n"
    )
    return parse_manifest(markdown, builtin=False), markdown


def prepare_definition(
    registry: PersonaRegistry, body: dict
) -> tuple[PersonaManifest, str, Path | None]:
    manifest, markdown = validate_definition(body.get("definition"))
    editing_id, template_id = body.get("editing_id"), body.get("template_id")
    if editing_id is not None:
        if not isinstance(editing_id, str) or not editing_id:
            raise ValueError("editing_id must name an installed custom persona")
        entry = registry.get(editing_id)
        if entry is None:
            raise ValueError(f"unknown persona: {editing_id}")
        if entry.builtin:
            raise ValueError("built-in coworkers can only be copied")
        if manifest.id != editing_id:
            raise ValueError("an existing coworker's id cannot be changed")
        if template_id is not None:
            raise ValueError("an edit cannot replace its source bundle")
    else:
        if registry.get(manifest.id) is not None:
            raise ValueError(f"a coworker with id '{manifest.id}' already exists")
        if template_id is not None and (
            not isinstance(template_id, str) or registry.get(template_id) is None
        ):
            raise ValueError("unknown template")
        entry = registry.get(template_id) if template_id else None
    source = (
        Path(entry.manifest.source).parent
        if entry and entry.manifest and entry.manifest.source
        else None
    )
    # The form can select bundled skills from its source, not reference arbitrary paths.
    for skill in manifest.skills:
        if source is None or not (source / "skills" / skill / "SKILL.md").is_file():
            raise ValueError(f"skill '{skill}' is not bundled with this template")
    return manifest, markdown, source


def preview_definition(registry: PersonaRegistry, body: dict) -> dict:
    manifest, markdown, _ = prepare_definition(registry, body)
    summary = consent_summary(manifest)
    summary["replaces"] = registry._replaces_of(manifest)
    return {"ok": True, "consent": summary, "markdown": markdown}


def save_definition(registry: PersonaRegistry, body: dict) -> dict:
    # Keep duplicate-ID validation and persistence together for simultaneous saves.
    with _SAVE_LOCK:
        return _save_definition(registry, body)


def _save_definition(registry: PersonaRegistry, body: dict) -> dict:
    manifest, markdown, source = prepare_definition(registry, body)
    if registry.installed_dir is None:
        raise ValueError("persona storage is not configured")
    with tempfile.TemporaryDirectory(prefix="ocw-persona-editor-") as tmp:
        root = Path(tmp)
        (root / "manifest.md").write_text(markdown, encoding="utf-8")
        if source:
            for skill in manifest.skills:
                shutil.copytree(source / "skills" / skill, root / "skills" / skill)
        summaries = registry.install_from_dir(root, replace_skills=True)
    return {"ok": True, "consent": summaries, "personas": registry.list_all()}
