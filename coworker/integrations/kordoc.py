"""Pinned, non-shell Kordoc runtime discovery for read-only document parsing."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

KORDOC_VERSION = "4.2.3"
KORDOC_MCP_TOOL_ALLOWLIST = [
    "detect_format",
    "parse_metadata",
    "parse_chunks",
    "parse_pages",
    "parse_table",
]

_KORDOC_ZIP_FORMAT_CORRECTIONS = frozenset({"docx", "xlsx"})

_NPM_ROOT_TIMEOUT_SECONDS = 5.0


def kordoc_metadata_needs_format_check(metadata: Any) -> bool:
    """Whether an upstream payload has the exact coarse-ZIP defect we correct."""
    if not isinstance(metadata, str):
        return False
    try:
        payload = json.loads(metadata)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("format"), str)
        and payload["format"].lower() == "hwpx"
    )


def normalize_kordoc_metadata_format(metadata: Any, detection: Any) -> Any:
    """Correct Kordoc 4.2.3's coarse ZIP format without changing bad results.

    ``parse_metadata`` dispatches DOCX/XLSX through the right parser but serializes
    its earlier generic ZIP classification (``hwpx``). ``detect_format`` performs
    the missing ZIP inspection, so use only its final, known format token. Any
    malformed/error/unknown response leaves the upstream metadata untouched.
    """
    if not kordoc_metadata_needs_format_check(metadata) or not isinstance(
        detection, str
    ):
        return metadata
    _, separator, detected = detection.rpartition(": ")
    detected = detected.strip().lower()
    if not separator or detected not in _KORDOC_ZIP_FORMAT_CORRECTIONS:
        return metadata
    payload = json.loads(metadata)
    payload["format"] = detected
    return json.dumps(payload, ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class KordocRuntime:
    """The canonical local paths for one compatible Kordoc installation."""

    node_executable: Path
    npm_root: Path
    package_dir: Path
    cli_path: Path
    mcp_path: Path
    version: str = KORDOC_VERSION


@dataclass(frozen=True)
class KordocRuntimeStatus:
    """Discovery result safe to surface without installation paths or process output."""

    state: Literal["not_installed", "incompatible", "ready"]
    error: Optional[str] = None
    version: Optional[str] = None
    runtime: Optional[KordocRuntime] = field(default=None, repr=False, compare=False)

    @property
    def ready(self) -> bool:
        return self.state == "ready" and self.runtime is not None

    def public(self) -> dict[str, str]:
        """Small status payload suitable for an API response or user message."""
        result = {"state": self.state}
        if self.error:
            result["error"] = self.error
        if self.version:
            result["version"] = self.version
        return result


def detect_kordoc_runtime(
    *,
    node_executable: str | Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> KordocRuntimeStatus:
    """Inspect the local pinned package without starting Kordoc or changing config.

    The required "npm root -g" query is run through the canonical Node executable
    and npm's JavaScript CLI, never through npm.cmd/npm.ps1 or npx.
    """
    node = _canonical_node(node_executable)
    if node is None:
        return _not_installed(
            "Node.js is unavailable. Install Node.js with npm, then install Kordoc 4.2.3 globally."
        )

    npm_cli = _npm_cli_for(node)
    if npm_cli is None:
        return _not_installed(
            "npm is unavailable next to Node.js. Reinstall Node.js with npm, then install Kordoc 4.2.3 globally."
        )

    npm_root = _global_npm_root(node, npm_cli, runner)
    if npm_root is None:
        return _not_installed(
            "Unable to locate global npm packages. Verify the Node.js and npm installation, then install Kordoc 4.2.3 globally."
        )

    package_dir = npm_root / "kordoc"
    package_json = package_dir / "package.json"
    if not package_json.is_file():
        return _not_installed(
            "Kordoc 4.2.3 is not installed globally. Install the pinned Kordoc package before using Korean document tools."
        )

    try:
        metadata = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _incompatible(
            "The installed Kordoc package is unreadable. Reinstall Kordoc 4.2.3 globally."
        )
    if not isinstance(metadata, dict):
        return _incompatible(
            "The installed Kordoc package is invalid. Reinstall Kordoc 4.2.3 globally."
        )
    version = str(metadata.get("version") or "").strip()
    if version != KORDOC_VERSION:
        shown = version if version else "unknown"
        return _incompatible(
            f"Kordoc {shown} is incompatible; install exact version {KORDOC_VERSION}.",
            version=version or None,
        )

    cli_path = package_dir / "dist" / "cli.js"
    mcp_path = package_dir / "dist" / "mcp.js"
    if not cli_path.is_file() or not mcp_path.is_file():
        return _incompatible(
            "The installed Kordoc package is incomplete. Reinstall Kordoc 4.2.3 globally.",
            version=version,
        )

    try:
        runtime = KordocRuntime(
            node_executable=node,
            npm_root=npm_root.resolve(strict=True),
            package_dir=package_dir.resolve(strict=True),
            cli_path=cli_path.resolve(strict=True),
            mcp_path=mcp_path.resolve(strict=True),
        )
    except OSError:
        return _incompatible(
            "The installed Kordoc package is incomplete. Reinstall Kordoc 4.2.3 globally.",
            version=version,
        )
    return KordocRuntimeStatus("ready", version=version, runtime=runtime)


def _canonical_node(node_executable: str | Path | None) -> Optional[Path]:
    if node_executable is not None:
        candidates = [str(node_executable)]
    elif os.name == "nt":
        candidates = ["node.exe", "node"]
    else:
        candidates = ["node"]
    for candidate in candidates:
        found = candidate if node_executable is not None else shutil.which(candidate)
        if not found:
            continue
        try:
            path = Path(found).expanduser().resolve(strict=True)
        except OSError:
            continue
        if path.is_file():
            return path
    return None


def _npm_cli_for(node: Path) -> Optional[Path]:
    """Return npm's JS entrypoint beside a Node installation, never a platform shim."""
    candidates: list[Path] = []
    if os.name == "nt":
        # A user-level npm installation can shadow nvm's bundled npm. Resolve its
        # JavaScript payload but never execute the .cmd/.ps1 shim itself; this is
        # the same prefix an interactive npm root -g invocation would use.
        shim = shutil.which("npm.cmd")
        if shim:
            candidates.append(
                Path(shim).parent
                / "node_modules"
                / "npm"
                / "bin"
                / "npm-cli.js"
            )
    candidates.extend(
        [
            node.parent / "node_modules" / "npm" / "bin" / "npm-cli.js",
            node.parent.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
        ]
    )
    for candidate in candidates:
        try:
            canonical = candidate.resolve(strict=True)
        except OSError:
            continue
        if canonical.is_file():
            return canonical
    return None


def _global_npm_root(
    node: Path,
    npm_cli: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> Optional[Path]:
    try:
        completed = runner(
            [str(node), str(npm_cli), "root", "-g"],
            capture_output=True,
            text=True,
            timeout=_NPM_ROOT_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    root_text = str(completed.stdout or "").strip()
    if not root_text or "\n" in root_text or "\r" in root_text:
        return None
    try:
        root = Path(root_text).expanduser().resolve(strict=True)
    except OSError:
        return None
    return root if root.is_dir() else None


def _not_installed(error: str) -> KordocRuntimeStatus:
    return KordocRuntimeStatus("not_installed", error=error)


def _incompatible(error: str, *, version: Optional[str] = None) -> KordocRuntimeStatus:
    return KordocRuntimeStatus("incompatible", error=error, version=version)
