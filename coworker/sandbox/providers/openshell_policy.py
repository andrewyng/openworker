"""Render an OpenShell policy from OUR words: the session's folders, a user id, a network
profile. We always supply the whole policy and never rely on the one an image ships with.

What the spike showed (ocw-context/docs/sandbox-spike-openshell.md):
- a bind mount is not enough, the policy must list the path too (finding D);
- the sandbox must run with the uid and gid that own the folders (finding E);
- `landlock.compatibility: hard_requirement` makes a kernel without Landlock refuse to run.
"""

from __future__ import annotations

import copy
from typing import Any, Optional, Sequence

from .. import network_profiles

RUNNER_MOUNT = "/opt/openworker"
RUNTIME_DIR = "/tmp"  # the runner's socket and HOME live here; always read-write

# Read-only system paths every sandbox needs to run programs at all. With Landlock as a hard
# requirement EVERY listed path must exist in the image, or the sandbox refuses to start
# (seen with `/app`, which OpenShell's own default policy lists but the base image lacks).
# So this is the smallest set, all present in a merged-/usr Linux image; `/bin`, `/sbin`
# and `/lib64` are links into `/usr` there and need no entry of their own.
_SYSTEM_READ_ONLY = ["/usr", "/lib", "/etc", "/proc", "/dev/urandom", "/var/log"]

# NVIDIA's base image keeps its developer tools (a uv-managed Python that `python3` on PATH
# points to, a virtual environment) under `/sandbox`. Read-only, so the agent can use them.
_IMAGE_TOOLS_READ_ONLY = ["/sandbox"]

# The runner itself uses the system Python, which needs nothing outside `/usr`.
PYTHON = "/usr/bin/python3"

# Any program may use an allowed destination. The wall we want here is WHERE an agent's
# commands can connect, not which program does the connecting.
_ANY_BINARY = [{"path": "/**"}]


def _hosts(name: str, hosts: Sequence[str]) -> dict[str, Any]:
    return {"name": name, "endpoints": [{"host": h, "port": 443} for h in hosts], "binaries": [dict(b) for b in _ANY_BINARY]}


# The host lists are ours and shared by every provider (network_profiles.py); here they are
# only put into OpenShell's shape. Policy keys use underscores.
_POLICY_KEYS = {"code-hosts": "code_hosts", "package-registries": "packages", "search-apis": "search"}
PROFILES: dict[str, dict[str, dict[str, Any]]] = {
    profile: {_POLICY_KEYS[name]: _hosts(name, hosts) for name, hosts in groups.items()}
    for profile, groups in network_profiles.PROFILES.items()
}
DEFAULT_PROFILE = network_profiles.DEFAULT_PROFILE


def render(
    roots: Sequence[dict[str, Any]],
    *,
    profile: str = DEFAULT_PROFILE,
    uid: Optional[int] = None,
    gid: Optional[int] = None,
    home: Optional[str] = None,
    extra_hosts: Sequence[str] = (),
) -> dict[str, Any]:
    """`roots`: [{"path": absolute path, "writable": bool}], primary first. `home`: the
    sandbox's private home folder with the copied credentials (section 11b), read-write.
    `extra_hosts`: "host:port" entries the grants need; `*.example.com` is a wildcard."""
    if profile not in PROFILES:
        raise ValueError(f"unknown network profile {profile!r} (known: {', '.join(sorted(PROFILES))})")
    read_write = [RUNTIME_DIR, "/dev/null", "/dev/pts", *([home] if home else []), *[str(r["path"]) for r in roots if r.get("writable")]]
    read_only = [*_SYSTEM_READ_ONLY, *_IMAGE_TOOLS_READ_ONLY, RUNNER_MOUNT, *[str(r["path"]) for r in roots if not r.get("writable")]]
    policy: dict[str, Any] = {
        "version": 1,
        "filesystem_policy": {"include_workdir": False, "read_only": read_only, "read_write": read_write},
        "landlock": {"compatibility": "hard_requirement"},
        "process": {
            "run_as_user": str(uid) if uid is not None else "sandbox",
            "run_as_group": str(gid) if gid is not None else "sandbox",
        },
        "network_policies": copy.deepcopy(PROFILES[profile]),  # no shared objects: plain YAML, no anchors
    }
    if extra_hosts:
        endpoints = []
        for item in extra_hosts:
            host, _, port = str(item).rpartition(":")
            if host and port.isdigit():
                endpoints.append({"host": host, "port": int(port)})
        policy["network_policies"]["credentials"] = {"name": "shared-credentials", "endpoints": endpoints, "binaries": [dict(b) for b in _ANY_BINARY]}
    return policy


def mounts(roots: Sequence[dict[str, Any]], runner_dir: str, home: Optional[str] = None) -> dict[str, Any]:
    """Driver config for the Docker driver: every folder at the same absolute path it has on
    the machine, the runner's folder read-only, and the sandbox's private home read-write."""
    binds = [
        {"type": "bind", "source": str(r["path"]), "target": str(r["path"]), "read_only": not r.get("writable")}
        for r in roots
    ]
    binds.append({"type": "bind", "source": runner_dir, "target": RUNNER_MOUNT, "read_only": True})
    if home:
        binds.append({"type": "bind", "source": home, "target": home, "read_only": False})
    return {"docker": {"mounts": binds}}
