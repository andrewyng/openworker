"""Finding (and optionally installing) the CLI tools a coworker's skills drive.

Two problems, deliberately kept apart (OPE-82):

* **The user's own toolchain** — aws, kubectl, terraform, gh, node. The whole point is
  *their* installed, configured, credentialed copy, so we only ever LOCATE these. The
  desktop shell hands us the login shell's PATH at spawn (OPE-83); `resolve()` is the
  belt-and-braces for every other launch path (headless, systemd, a double-clicked
  binary) — it also searches the dirs launchd's PATH never covers.
* **Tools a skill fundamentally IS** — the scanners behind the security bundles. Those
  we can install and PIN, so a security review is reproducible instead of depending on
  whatever version the user's package manager happened to ship.

Everything returns an ABSOLUTE path: once resolved, invocation never depends on PATH
again, so a tool found here works even if the caller's environment is bare.

Nothing here downloads anything on its own. `install()` runs only when the user has
approved it (via `request_tool`, OPE-85) — fetching an executable is a supply-chain
decision, so it is pinned by version, verified by SHA-256, and never implicit.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

from .secrets import state_dir

# Dirs that hold user-installed CLIs but never appear in launchd's PATH. Mirrors
# KNOWN_TOOL_DIRS in the desktop shell (src-tauri/src/lib.rs) — keep the two in step.
_KNOWN_DIRS: tuple[str, ...] = (
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/local/sbin",
    "/opt/local/bin",
    "~/.local/bin",
    "~/.cargo/bin",
    "~/go/bin",
)


def managed_dir() -> Path:
    """Where we keep tools we installed ourselves (never the user's own copies)."""
    return state_dir() / "tools"


def bin_dir() -> Path:
    """One stable dir of links to the current pinned binaries. Binaries themselves live
    in versioned dirs; this is what goes on a shell's PATH, so a tool installed mid-
    session is picked up by the already-running shell without a respawn."""
    return managed_dir() / "bin"


def _platform_key() -> str:
    """`<os>_<arch>` using the naming the upstream release assets use."""
    system = {"darwin": "darwin", "linux": "linux", "win32": "windows"}.get(
        sys.platform, sys.platform
    )
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    return f"{system}_{arch}"


@dataclass(frozen=True)
class Download:
    url: str
    sha256: str
    # Path of the binary inside the archive; None when the asset IS the binary.
    member: Optional[str] = None


@dataclass(frozen=True)
class ManagedTool:
    name: str
    version: str
    # platform key -> download
    downloads: dict[str, Download]
    summary: str


# Pinned scanner registry. Versions and digests are copied from the upstream release's
# own checksum manifest; bumping a tool means bumping the digest in the same commit.
#
# Not every scanner belongs here: semgrep is distributed as a Python package (pip/brew),
# so we resolve the user's install rather than half-managing a copy. tfsec is absent on
# purpose — it's deprecated upstream and `trivy config` is its successor.
MANAGED: dict[str, ManagedTool] = {
    "gitleaks": ManagedTool(
        name="gitleaks",
        version="8.30.1",
        summary="scans git history and the working tree for committed secrets",
        downloads={
            "darwin_arm64": Download(
                url="https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_darwin_arm64.tar.gz",
                sha256="b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5",
                member="gitleaks",
            ),
            "darwin_amd64": Download(
                url="https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_darwin_x64.tar.gz",
                sha256="dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709",
                member="gitleaks",
            ),
            "linux_amd64": Download(
                url="https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz",
                sha256="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
                member="gitleaks",
            ),
        },
    ),
    "trivy": ManagedTool(
        name="trivy",
        version="0.74.0",
        summary="scans IaC/config, container images, and filesystems for misconfigurations and vulnerabilities",
        downloads={
            "darwin_arm64": Download(
                url="https://github.com/aquasecurity/trivy/releases/download/v0.74.0/trivy_0.74.0_macOS-ARM64.tar.gz",
                sha256="1caada5e0e2091909357c7525d3aa76f4b660b13821bc143b190c7483e31cc11",
                member="trivy",
            ),
            "darwin_amd64": Download(
                url="https://github.com/aquasecurity/trivy/releases/download/v0.74.0/trivy_0.74.0_macOS-64bit.tar.gz",
                sha256="472816f6888dda689d075c30254d4210b4d1035acf365aa72332f584c2f60485",
                member="trivy",
            ),
            "linux_amd64": Download(
                url="https://github.com/aquasecurity/trivy/releases/download/v0.74.0/trivy_0.74.0_Linux-64bit.tar.gz",
                sha256="2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a",
                member="trivy",
            ),
        },
    ),
    "osv-scanner": ManagedTool(
        name="osv-scanner",
        version="2.5.0",
        summary="checks dependency lockfiles against the OSV vulnerability database",
        downloads={
            "darwin_arm64": Download(
                url="https://github.com/google/osv-scanner/releases/download/v2.5.0/osv-scanner_darwin_arm64",
                sha256="fff5a2e351b7f0a60001e87cbf862e82fb82e2792d368b533fec7a5865a73da2",
            ),
            "darwin_amd64": Download(
                url="https://github.com/google/osv-scanner/releases/download/v2.5.0/osv-scanner_darwin_amd64",
                sha256="baef4f4a4ce2924a9241869c36d4bd9d6c04b632cae6637a0f6347ab9272eb16",
            ),
            "linux_amd64": Download(
                url="https://github.com/google/osv-scanner/releases/download/v2.5.0/osv-scanner_linux_amd64",
                sha256="edcfc41d257db36148f065055655fe3fcfc434b0b423ea67468a84c207524e0c",
            ),
        },
    ),
}


def _managed_path(tool: ManagedTool) -> Path:
    exe = tool.name + (".exe" if sys.platform == "win32" else "")
    return managed_dir() / tool.name / tool.version / exe


def resolve(name: str) -> Optional[str]:
    """Absolute path to `name`, or None. PATH first (the user's choice wins), then the
    dirs a GUI launch can't see, then anything we installed ourselves."""
    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())

    for raw in _KNOWN_DIRS:
        candidate = Path(raw).expanduser() / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())

    tool = MANAGED.get(name)
    if tool:
        managed = _managed_path(tool)
        if managed.is_file() and os.access(managed, os.X_OK):
            return str(managed)
    return None


def have(name: str) -> bool:
    return resolve(name) is not None


def missing(names: Iterable[str]) -> list[str]:
    """Which of `names` we can't find — what a skill checks before promising a scan."""
    return [n for n in names if not have(n)]


def installable(name: str) -> bool:
    """Whether we could install this ourselves (i.e. it's pinned for this platform)."""
    tool = MANAGED.get(name)
    return bool(tool and _platform_key() in tool.downloads)


def describe(name: str) -> Optional[dict[str, str]]:
    """What to show the user when asking permission to install (OPE-85)."""
    tool = MANAGED.get(name)
    if not tool:
        return None
    dl = tool.downloads.get(_platform_key())
    if not dl:
        return None
    parsed = urlparse(dl.url)
    path_parts = [p for p in parsed.path.split("/") if p]
    return {
        "name": tool.name,
        "version": tool.version,
        "summary": tool.summary,
        "url": dl.url,
        "sha256": dl.sha256,
        # Publisher, human-readable ("github.com/aquasecurity") — for the consent card.
        "source": parsed.netloc + (f"/{path_parts[0]}" if path_parts else ""),
    }


def _verify(blob: bytes, expected: str) -> None:
    actual = hashlib.sha256(blob).hexdigest()
    if actual != expected:
        raise ValueError(
            f"checksum mismatch: expected {expected}, got {actual} — refusing to install"
        )


def install(name: str, *, timeout: int = 120) -> str:
    """Install a pinned tool and return its absolute path.

    Only ever called after the user approves the request. The download is verified
    against the pinned digest BEFORE anything is written to its final location, so a
    tampered or truncated artifact never becomes an executable on disk.
    """
    tool = MANAGED.get(name)
    if not tool:
        raise KeyError(f"{name} is not a managed tool")
    dl = tool.downloads.get(_platform_key())
    if not dl:
        raise KeyError(f"{name} has no pinned build for {_platform_key()}")

    target = _managed_path(tool)
    if target.is_file() and os.access(target, os.X_OK):
        return str(target)

    with urllib.request.urlopen(dl.url, timeout=timeout) as resp:  # noqa: S310 - pinned URL
        blob = resp.read()
    _verify(blob, dl.sha256)

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        if dl.member:
            archive = tmp_path / "asset.tar.gz"
            archive.write_bytes(blob)
            with tarfile.open(archive) as tf:
                extracted = tf.extractfile(dl.member)
                if extracted is None:
                    raise ValueError(f"{dl.member} missing from {name} archive")
                payload = extracted.read()
        else:
            payload = blob

        staged = tmp_path / "binary"
        staged.write_bytes(payload)
        staged.chmod(staged.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        shutil.move(str(staged), str(target))

    _link_into_bin(tool, target)
    return str(target)


def _link_into_bin(tool: ManagedTool, target: Path) -> None:
    """Expose the versioned binary under the stable bin dir (PATH-friendly name)."""
    link = bin_dir() / target.name
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)
    except OSError:
        # Filesystems without symlinks (some Windows setups): a copy serves the same role.
        shutil.copy2(target, link)
