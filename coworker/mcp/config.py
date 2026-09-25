"""MCP server config — the standard `mcpServers` JSON, layered global + workspace.

Global:    ~/.config/coworker/mcp.json
Workspace: <workspace>/.coworker/mcp.json   (overrides global on name clash,
           but only after the user trusts that workspace — same gate as
           repository `allowed_commands`)

Paste-compatible with Claude Desktop / Cursor / Codex. `${VAR}` refs in command/args/env/
url/headers are resolved at load time via the SecretStore (env + local `.env`). REST edits
target the **global** file.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from ..secrets import SecretStore, state_dir

_HTTP_TYPES = {"http", "https", "sse", "streamable-http", "streamable_http"}


@dataclass
class MCPServerDef:
    name: str
    transport: str  # "stdio" | "http"
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
    url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    include_tools: Optional[list[str]] = None
    exclude_tools: Optional[list[str]] = None
    requires_approval: bool = True
    # "oauth" → browser OAuth 2.1 + PKCE with Dynamic Client Registration (mcp/oauth.py).
    # HTTP transport only; tokens live in the SecretStore, never in this file.
    auth: Optional[str] = None


def global_mcp_path() -> Path:
    return state_dir() / "mcp.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _config_paths(
    workspace: Optional[str | Path], *, workspace_trusted: bool
) -> list[Path]:
    """Config files to merge. Workspace MCP is executable provenance (stdio spawn),
    so an untrusted repo's `.coworker/mcp.json` is never read — cloning alone must
    not be enough to define processes that run at session open.
    """
    paths = [global_mcp_path()]
    if workspace and workspace_trusted:
        paths.append(Path(workspace).expanduser() / ".coworker" / "mcp.json")
    return paths


def _parse(name: str, raw: dict[str, Any], secrets: SecretStore) -> MCPServerDef:
    raw = secrets.resolve(raw)  # resolve ${VAR} everywhere before building the def
    declared = str(raw.get("type", "")).lower()
    is_http = declared in _HTTP_TYPES or bool(raw.get("url"))
    return MCPServerDef(
        name=name,
        transport="http" if is_http else "stdio",
        command=raw.get("command"),
        args=list(raw.get("args", []) or []),
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        cwd=raw.get("cwd"),
        url=raw.get("url"),
        headers={str(k): str(v) for k, v in (raw.get("headers") or {}).items()},
        enabled=bool(raw.get("enabled", True)),
        include_tools=raw.get("include_tools"),
        exclude_tools=raw.get("exclude_tools"),
        requires_approval=bool(raw.get("requires_approval", True)),
        auth=(str(raw["auth"]).lower() if raw.get("auth") else None),
    )


def load_mcp_servers(
    workspace: Optional[str | Path] = None,
    *,
    secrets: Optional[SecretStore] = None,
    workspace_trusted: bool = False,
) -> list[MCPServerDef]:
    """Merge global + (when trusted) workspace `mcpServers` into parsed server defs.

    Only trusted workspaces contribute — the same consent boundary as repository
    ``allowed_commands`` — and **global wins on name clash**, so even a trusted repo
    cannot silently redefine a global server by reusing its name. ``${VAR}`` refs in
    a workspace def are resolved from the user's env, which is acceptable only because
    the workspace is trusted; untrusted workspaces are never read.
    """
    secrets = secrets or SecretStore()
    merged: dict[str, dict[str, Any]] = {}
    for path in _config_paths(workspace, workspace_trusted=workspace_trusted):
        for name, raw in (_read(path).get("mcpServers") or {}).items():
            if isinstance(raw, dict):
                merged.setdefault(name, raw)  # global first → global wins on clash
    return [_parse(name, raw, secrets) for name, raw in merged.items()]


# -- raw global-file mutation (REST) -------------------------------------------
def edited_server_config(
    raw: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """Validate a full replacement, restoring unchanged masked env/header values.

    Missing keys are deliberately removed. Only a mask at an existing key keeps
    its value; accepting new masks would silently save unusable credentials.
    """
    if not isinstance(raw, dict):
        raise ValueError("Configuration must be a JSON object")
    config = deepcopy(raw)
    for key in ("command", "url", "type", "cwd", "auth"):
        value = config.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
    kind = (config.get("type") or "").lower()
    if kind not in {"", "stdio", *_HTTP_TYPES}:
        raise ValueError("type must be stdio or http")
    url = config.get("url")
    http = bool(url) or kind in _HTTP_TYPES
    if http:
        if not url or not url.strip():
            raise ValueError("HTTP servers require a url")
        # Variable references are resolved by SecretStore at connection time.
        if "${" not in url:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("url must be an absolute http:// or https:// address")
    elif not (config.get("command") or "").strip():
        raise ValueError("Stdio servers require a command")
    if config.get("auth") not in (None, "", "oauth"):
        raise ValueError("auth must be oauth or omitted")
    if config.get("auth") == "oauth" and not http:
        raise ValueError("OAuth requires an HTTP server url")
    for key in ("enabled", "requires_approval"):
        if key in config and not isinstance(config[key], bool):
            raise ValueError(f"{key} must be true or false")
    for key in ("args", "include_tools", "exclude_tools"):
        value = config.get(key)
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(v, str) for v in value)
        ):
            raise ValueError(f"{key} must be an array of strings")
    for key in ("env", "headers"):
        values = config.get(key)
        if values is None:
            continue
        if not isinstance(values, dict) or not all(isinstance(v, str) for v in values.values()):
            raise ValueError(f"{key} must be an object with string values")
        previous = current.get(key) or {}
        for field, value in values.items():
            if value == "***":
                if field not in previous:
                    raise ValueError(f"Enter a value for the new {key} entry: {field}")
                values[field] = previous[field]
    return config


def read_global() -> dict[str, dict[str, Any]]:
    """Raw `mcpServers` map from the global file (no `${VAR}` resolution)."""
    return dict(_read(global_mcp_path()).get("mcpServers") or {})


def _write_global(servers: dict[str, dict[str, Any]]) -> None:
    path = global_mcp_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"mcpServers": servers}, indent=2), encoding="utf-8")
    tmp.replace(path)


def put_global_server(name: str, config: dict[str, Any]) -> None:
    servers = read_global()
    servers[name] = config
    _write_global(servers)


def patch_global_server(name: str, changes: dict[str, Any]) -> bool:
    servers = read_global()
    if name not in servers:
        return False
    merged = {**servers[name], **changes}
    # A None value DELETES the key (there is no other way to remove one through a
    # merge patch) — used by the OPE-136 trust migration to drop `requires_approval`.
    servers[name] = {k: v for k, v in merged.items() if v is not None}
    _write_global(servers)
    return True


def delete_global_server(name: str) -> bool:
    servers = read_global()
    if name not in servers:
        return False
    del servers[name]
    _write_global(servers)
    return True
