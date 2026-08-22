"""Thin HTTP client for the local sidecar.

The MCP server runs ON the OpenWorker box and talks to 127.0.0.1 — it never opens a port of its
own. Reaching it from elsewhere is SSH's job (`ssh box openworker-mcp`), which is also what
authenticates the caller. That is the whole security model: no new listener, no second
credential system, and revoking access is revoking an SSH key.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_BASE = "http://127.0.0.1:8765"
_TIMEOUT = 30.0


class SidecarError(RuntimeError):
    pass


def _token(state_dir: Path, port: int) -> str:
    """The pinned sidecar token. `openworker-serve` writes it once and leaves it alone, so a
    server restart does not invalidate a live MCP session."""
    env = os.environ.get("COWORKER_API_TOKEN")
    if env:
        return env.strip()
    f = state_dir / f"sidecar-{port}.token"
    try:
        return f.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class Sidecar:
    def __init__(self, base: Optional[str] = None, state_dir: Optional[Path] = None) -> None:
        self.base = (base or os.environ.get("OPENWORKER_URL") or DEFAULT_BASE).rstrip("/")
        port = urllib.parse.urlparse(self.base).port or 8765
        if state_dir is None:
            from ..secrets import state_dir as resolve_state_dir

            state_dir = resolve_state_dir()
        self.token = _token(Path(state_dir), port)

    def request(self, method: str, path: str, payload: Any = None) -> Any:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "content-type": "application/json",
                "x-openworker-token": self.token,
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as res:
                body = res.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code == 401:
                raise SidecarError(
                    "the sidecar rejected the token — is this running on the OpenWorker host, "
                    f"and is {state_hint()} readable?"
                ) from exc
            raise SidecarError(f"{method} {path} failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise SidecarError(
                f"cannot reach OpenWorker at {self.base} — is openworker-server running? ({exc.reason})"
            ) from exc
        return json.loads(body) if body else {}

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: Any = None) -> Any:
        return self.request("POST", path, payload if payload is not None else {})


def state_hint() -> str:
    from ..secrets import state_dir

    return str(state_dir() / "sidecar-<port>.token")
