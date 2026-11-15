"""Stamps: facts saved with every turn (observability E15a, E16; technical design §9).

`turn.stamp` has four optional keys: `machine` (where the work ran), `model` (which model
and provider key), `sandbox` (how protected) and `persona_fp` (which version of the coworker).
They are taken once at turn start.
"""

from __future__ import annotations

import hashlib
import socket
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

CLOUD_CONTROLLER = "https://machines.openworker.com"

_machine_lock = threading.Lock()
_machine: Optional[dict[str, Any]] = None


def machine_kind(controller: Optional[str], cloud_base: str = CLOUD_CONTROLLER) -> str:
    """`this_machine` when not joined to a controller; `cloud` for OpenWorker's cloud
    machines; `own_machine` for a remote machine you added (§9.1)."""
    if not controller:
        return "this_machine"
    return "cloud" if controller.rstrip("/") == cloud_base.rstrip("/") else "own_machine"


def app_version_or_unknown() -> str:
    """The OpenWorker version; `unknown`, never `0.0.0` (C6)."""
    try:
        from ..remote.channel import app_version

        v = app_version()
    except Exception:  # noqa: BLE001
        return "unknown"
    return "unknown" if (not v or v == "0.0.0") else v


def note_joined(controller: str, machine_id: str, name: Optional[str] = None) -> None:
    """The controller welcomed this machine: from now on its turns are stamped with the id the
    controller knows it by (`machines.id`), whatever was read before. So the Usage page's
    machine rows match the controller's list (Phase 3 V3-17), even for a process that stamped
    turns before it joined, or one whose state folder is not the default."""
    global _machine
    stamp = machine_stamp()
    with _machine_lock:
        _machine = {**stamp, "kind": machine_kind(controller), "id": machine_id or None,
                    "name": name or stamp.get("name")}


def machine_stamp() -> dict[str, Any]:
    """Where this server runs: read once per process."""
    global _machine
    with _machine_lock:
        if _machine is None:
            cfg: dict[str, Any] = {}
            try:
                from ..remote.joiner import load_remote_config
                from ..secrets import state_dir

                cfg = load_remote_config(state_dir()) or {}
            except Exception:  # noqa: BLE001
                cfg = {}
            try:
                host = socket.gethostname()
            except Exception:  # noqa: BLE001
                host = None
            _machine = {
                "kind": machine_kind(cfg.get("controller")),
                "id": cfg.get("machine_id") or None,
                "name": cfg.get("name") or host,
                "app_version": app_version_or_unknown(),
            }
        return dict(_machine)


def model_stamp(model: str) -> dict[str, Any]:
    """The turn's model and which provider key it used (§9.2)."""
    provider = model.split(":", 1)[0] if ":" in model else "openai"
    title = kind = None
    try:
        from ..providers.registry import get_descriptor

        d = get_descriptor(provider)
        if d is not None:
            title, kind = getattr(d, "title", None), getattr(d, "kind", None)
    except Exception:  # noqa: BLE001
        pass
    return {"id": model, "provider_title": title, "kind": kind}


def sandbox_stamp(workspace: Any) -> Optional[dict[str, Any]]:
    """The turn's sandbox (§9.3, per turn), from `describe()`; None when there is none."""
    describe = getattr(workspace, "describe", None)
    if describe is None:
        return None
    try:
        info = describe() or {}
    except Exception:  # noqa: BLE001
        return None
    runner = info.get("runner") or {}
    out = {
        "provider": info.get("provider"),
        "enforcement": info.get("enforcement"),
        "profile": getattr(getattr(workspace, "provider", None), "profile", None),
        "sites": _site_count(workspace),
        "runner_version": runner.get("runner_version"),
        "image": info.get("image"),
    }
    return {k: v for k, v in out.items() if v is not None}


def _site_count(workspace: Any) -> Optional[int]:
    """How many sites the sandbox lets through: its profile's own hosts, the machine's added
    hosts and the hosts the copied logins need (OpenShell). None for the `open` profile (any
    host) or a sandbox that keeps no list. No provider has an `allowed_sites` list, which the
    first version read, so this was always None (V2-28)."""
    from ..sandbox import network_profiles

    provider = getattr(workspace, "provider", None)
    profile = getattr(provider, "profile", None)
    extra = getattr(provider, "extra_hosts", None)
    if not isinstance(profile, str) or extra is None:
        return None
    try:
        if network_profiles.is_open(profile):
            return None
        copied = getattr(getattr(provider, "copied", None), "hosts", None) or []
        return len({*network_profiles.hosts(profile), *extra, *copied})
    except (ValueError, TypeError):
        return None


def persona_fingerprint(persona_id: str, manifest: str | bytes, skill_dirs: Iterable[Path] = ()) -> str:
    """Which version of this coworker ran (§9.4): sha256 of its id, its manifest text and
    its own skill files (sorted by relative path), first 6 hex characters. Not your own or
    the workspace's skills, memories, AGENTS.md, the model or the tools."""
    data = manifest.encode("utf-8") if isinstance(manifest, str) else manifest
    h = hashlib.sha256(("persona:" + persona_id + "\n").encode("utf-8") + data)
    for d in skill_dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for f in sorted((p for p in d.rglob("*") if p.is_file()), key=lambda p: p.relative_to(d).as_posix()):
            h.update(f.relative_to(d).as_posix().encode("utf-8") + b"\n" + f.read_bytes())
    return h.hexdigest()[:6]


def persona_files(manifest: str | bytes, skill_dirs: Iterable[Path] = (), *, max_bytes: int = 2_000_000) -> dict[str, bytes]:
    """The files a fingerprint covers (`persona_fingerprint`), by path: the manifest text as
    `manifest.md`, and each skill file as `skills/<dir>/<relative path>`. What the machine
    keeps per coworker version for "What changed" and "Show diff" (Phase 3 E25c, TD3-13).
    Stops adding files past `max_bytes`, so a coworker with large assets cannot fill the disk."""
    data = manifest.encode("utf-8") if isinstance(manifest, str) else manifest
    out: dict[str, bytes] = {"manifest.md": data}
    used = len(data)
    for d in skill_dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for f in sorted((p for p in d.rglob("*") if p.is_file()), key=lambda p: p.relative_to(d).as_posix()):
            b = f.read_bytes()
            if used + len(b) > max_bytes:
                return out
            out[f"skills/{f.relative_to(d).as_posix()}"] = b
            used += len(b)
    return out
