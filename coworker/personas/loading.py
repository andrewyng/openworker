"""Third-party persona loading + install-time capability consent.

A persona is loaded from a local directory or a git URL. Because a persona ships no executable
code (it only references vetted catalog capabilities, connectors, and MCP servers), "installing"
one is a light trust event: we compute a **consent summary** of what it will be able to do
(tools, risk classes, connectors, MCP, messaging, recommended mode) and the user approves that
before the persona is enabled. Loading never writes risk overrides or elevates any mode.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Optional

from .manifest import PersonaManifest


def consent_summary(m: PersonaManifest) -> dict:
    """What a persona will be able to do — shown at install for the user to approve."""
    from ..catalog import risk_summary

    return {
        "id": m.id,
        "name": m.name,
        "description": m.description,
        "tools": list(m.tools),
        "risk": sorted(rc.value for rc in risk_summary(m.tools)),
        # "all" | [connector ids] | [] — the consent screen shows the actual names,
        # never a bare "uses connectors" bit (OPE-93).
        "connectors": "all" if m.connectors is True else list(m.connectors or ()),
        "mcp": list(m.mcp),
        "messaging": m.messaging,
        # "lead" personas can create and direct worker coworkers — the consent
        # screen says that plainly (capability firebreak as a manifest fact).
        "team": m.team,
        "recommended_mode": m.default_permission_mode,
        "recommended_models": list(m.recommended_models),
        # Recommended connectors/MCP with reasons + tiers — the consent screen shows
        # these so the user knows what the coworker hopes to use (sharing v1).
        "recommends": [
            {"kind": r.kind, "ref": r.ref, "reason": r.reason, "tier": r.tier}
            for r in m.recommends
        ],
        "version": m.version,
        "source": m.source,
        "builtin": m.builtin,
    }


def capability_set(m: PersonaManifest) -> set[str]:
    """The persona's capability surface as a flat comparable set — used to decide
    whether an update GREW capabilities (which requires re-consent; a same-or-smaller
    update keeps the user's enabled state)."""
    caps = {f"tool:{t}" for t in m.tools}
    caps |= {f"mcp:{s}" for s in m.mcp}
    # Per-connector caps (OPE-93): an update that ADDS a connector must grow the set and
    # re-trigger consent — the old single "connectors" bit hid exactly that change.
    if m.connectors is True:
        caps.add("connectors:all")
    else:
        caps |= {f"connector:{c}" for c in m.connectors or ()}
    if m.messaging:
        caps.add("messaging")
    # An update that turns a solo persona into a lead/worker must re-consent —
    # team capability changes who the coworker can direct or be directed by.
    if m.team:
        caps.add(f"team:{m.team}")
    return caps


def git_clone(
    url: str, dest: Path
) -> None:  # pragma: no cover - exercised via injection
    """Shallow-clone a persona repo. Injectable so tests don't touch the network."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        check=True,
        capture_output=True,
    )


def cache_dir_for(url: str, base: Path) -> Path:
    """A stable cache directory for a git URL (sanitized last path segment + short hash)."""
    import hashlib

    slug = url.rstrip("/").split("/")[-1].removesuffix(".git") or "persona"
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in slug)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return base / f"{slug}-{digest}"


def clone_persona_repo(
    url: str, base: Path, *, clone: Callable[[str, Path], None] = git_clone
) -> Path:
    """Clone (or reuse) a persona repo under ``base`` and return its directory."""
    dest = cache_dir_for(url, base)
    if not dest.is_dir():
        clone(url, dest)
    return dest
