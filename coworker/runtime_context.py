"""Bounded, secret-free runtime discovery. No subprocesses or config-file reads."""
from __future__ import annotations

import shutil
from pathlib import Path

import aisuite as ai


def capture(workspace, roots) -> dict:
    root = Path(workspace) if workspace else None

    def present(name):
        if root is None:
            return False
        candidate = root
        for part in Path(name).parts:
            candidate /= part
            if candidate.is_symlink():
                return False
        return candidate.exists()

    return {
        "workspace": str(root) if root else None,
        "working_folders": [{"path": str(path), "writable": writable} for path, writable in roots],
        "tools_available": {name: shutil.which(name) is not None for name in ("git", "python3", "node", "npm", "uv")},
        "project_entries": {
            name: present(name)
            for name in (".venv", "node_modules", "web/node_modules", "pyproject.toml", "package.json", "web/package.json", "tests", "web/e2e")
        },
        "configuration_values": "not exposed",
    }


def runtime_context_tool(permissions):
    def runtime_context() -> dict:
        """Discover this session's working folders, tool availability and project
        environment presence without reading .env, credentials, shell profiles or home
        caches. Use this before environment/tooling checks. Facts are not access grants.
        For tests, use the project's configured runner; do not print configuration or
        credentials. Database endpoints and credentials are intentionally not returned.
        """
        return capture(permissions.workspace_root, permissions._resolved_roots())

    return ai.tool(runtime_context, metadata=ai.ToolMetadata(category="runtime", risk_level="low"))
