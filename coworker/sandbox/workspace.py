"""The Workspace: the one thing tools use to touch files and run commands.

Two implementations:
- `DirectWorkspace`: today's behaviour, in this process. No runner, no JSON-RPC, no extra
  process. This is `direct` mode and the default, kept so that benchmark readings stay
  comparable. It reports enforcement `none`.
- `RunnerWorkspace`: a tool runner behind a provider (a sandbox, or `runner-local`).

Step 1 routes the shell through the workspace. The file, git and search tools follow in
step 2 (design doc, section 11).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from .runner.executor import Executor

PROVIDER_ENV = "OPENWORKER_SANDBOX_PROVIDER"
DIRECT = "direct"
RUNNER_LOCAL = "runner-local"
OPENSHELL = "openshell"
SEATBELT = "seatbelt"

RESTART_NOTICE = (
    "The sandbox was restarted because the session's folders changed. The shell started again: "
    "variables and background tasks from before are gone, files are untouched."
)


class Workspace(ABC):
    @property
    @abstractmethod
    def executor(self) -> Executor: ...

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Which provider this is and how much it enforces: `full`, `partial` or `none`."""

    def close(self) -> None:
        self.executor.close()


class DirectWorkspace(Workspace):
    def __init__(self, *, cwd: str | Path) -> None:
        from ..tools.shell import LocalExecutor  # here, not at the top: tools.shell imports us

        self._executor = LocalExecutor(cwd=cwd)

    @property
    def executor(self) -> Executor:
        return self._executor

    def describe(self) -> dict[str, Any]:
        return {"provider": DIRECT, "enforcement": "none", "reason": "commands run in the OpenWorker process, unconfined"}


class RunnerWorkspace(Workspace):
    def __init__(
        self,
        provider: Any,
        *,
        cwd: str | Path,
        shell: str = "main",
        registry: Any = None,
        session_id: str = "",
        agent: str = "",
        live_roots: Optional[list] = None,
    ) -> None:
        from .client import RunnerClient
        from .executor import RunnerExecutor

        self.provider = provider
        self.registry = registry
        self._registered: Optional[str] = None
        self._session_id, self._agent = session_id, agent
        # The session's own RootDir list. It changes while the session runs (a folder is
        # granted or taken away); a sandbox's walls are fixed when it starts.
        self._live_roots = live_roots
        if registry is not None:
            registry.reap()  # sandboxes left behind by a server that is gone
            registry.check_room()
        try:
            provider.create()
        except Exception:
            provider.destroy()  # a half-made sandbox may already hold copied credentials
            raise
        try:
            self.client = RunnerClient(provider.open_runner)
            self.hello = self.client.connect()
            verify = getattr(provider, "verify", None)
            if verify is not None:
                verify(self.client)  # e.g. every folder is really reachable inside
        except Exception:
            provider.destroy()
            raise
        self._executor = RunnerExecutor(
            self.client, cwd=str(Path(cwd).expanduser().resolve()), shell=shell, before_call=self.sync_roots
        )
        self._record()

    def _record(self) -> None:
        """Enter this sandbox in the registry; after a restart that replaced it, under its
        new name."""
        if self.registry is None:
            return
        info = self.provider.describe()
        name = str(info.get("sandbox") or f"{info['provider']}-{id(self):x}")
        if self._registered and self._registered != name:
            self.registry.close(self._registered)
        self._registered = name
        self.registry.record(
            name,
            provider=info["provider"],
            session_id=self._session_id,
            agent=self._agent,
            roots=getattr(self.provider, "roots", None),
            profile=getattr(self.provider, "profile", ""),
            enforcement=info.get("enforcement", ""),
        )

    def sync_roots(self) -> Optional[str]:
        """Restart the sandbox when the session's folders are no longer the ones it was
        started with (design ruling 15). Returns what to tell the agent, or None."""
        regrant = getattr(self.provider, "regrant", None)
        if regrant is None or self._live_roots is None:
            return None
        wanted = [{"path": str(r.path), "writable": bool(r.writable)} for r in self._live_roots]
        if not wanted or _same_roots(wanted, getattr(self.provider, "roots", [])):
            return None
        self.client.detach()
        regrant(wanted)
        self.client.connect()  # a new runner: the client notes the restart
        verify = getattr(self.provider, "verify", None)
        if verify is not None:
            verify(self.client)
        self._record()
        return RESTART_NOTICE

    @property
    def executor(self) -> Executor:
        return self._executor

    def context(self) -> str:
        """What the agent is told about this sandbox each turn (ruling 21)."""
        from .credentials import context_lines

        return context_lines(getattr(self.provider, "copied", None))

    def describe(self) -> dict[str, Any]:
        return {**self.provider.describe(), "runner": {k: self.hello.get(k) for k in ("runner_version", "os", "machine", "instance_id")}}

    def close(self) -> None:
        try:
            self._executor.close()
        finally:
            self.client.close()
            self.provider.destroy()
            if self.registry is not None and self._registered:
                self.registry.close(self._registered)


def _same_roots(a: list, b: list) -> bool:
    def key(roots: list) -> set:
        return {(os.path.realpath(str(r["path"])), bool(r.get("writable"))) for r in roots}

    return key(a) == key(b)


def provider_name(explicit: Optional[str] = None) -> str:
    return (explicit or os.environ.get(PROVIDER_ENV) or DIRECT).strip().lower()


def open_workspace(
    *,
    cwd: str | Path,
    provider: Optional[str] = None,
    roots: Optional[list] = None,
    session_id: str = "",
    agent: str = "",
    credentials: Optional[list] = None,
    network_profile: Optional[str] = None,
) -> Workspace:
    """The session's workspace for the configured provider. `credentials`: the machine's
    `sandbox_credentials` setting; the enabled entries are copied into the sandbox
    (design doc, section 11b). Ignored in `direct` mode, where nothing is hidden anyway. `direct` unless told otherwise.
    `roots`: the session's RootDir list (primary first); without it the workspace folder is
    the only, writable, root. `session_id` and `agent` say who the sandbox is for; they go
    into the registry and onto the sandbox as a label."""
    name = provider_name(provider)
    if name == DIRECT:
        return DirectWorkspace(cwd=cwd)
    if name == RUNNER_LOCAL:
        from .providers.runner_local import RunnerLocalProvider

        return RunnerWorkspace(RunnerLocalProvider(cwd=cwd), cwd=cwd)
    listed = [{"path": str(r.path), "writable": bool(r.writable)} for r in (roots or [])]
    listed = listed or [{"path": str(cwd), "writable": True}]
    from .credentials import granted

    grants = granted(credentials)
    from .network_profiles import DEFAULT_PROFILE, check

    profile = check((network_profile or "").strip().lower() or DEFAULT_PROFILE)
    if name == SEATBELT:
        from .providers.seatbelt import SeatbeltProvider
        from .registry import SandboxRegistry

        return RunnerWorkspace(
            SeatbeltProvider(roots=listed, cwd=str(cwd), credentials=grants, profile=profile),
            cwd=cwd,
            registry=SandboxRegistry(),
            session_id=session_id,
            agent=agent,
            live_roots=roots,
        )
    if name == OPENSHELL:
        from .providers.openshell import OpenShellProvider
        from .registry import SandboxRegistry

        label = "-".join(part for part in (session_id[:24], agent[:24]) if part)
        return RunnerWorkspace(
            OpenShellProvider(roots=listed, cwd=str(cwd), label=label, credentials=grants, profile=profile),
            cwd=cwd,
            registry=SandboxRegistry(),
            session_id=session_id,
            agent=agent,
            live_roots=roots,
        )
    raise ValueError(f"unknown sandbox provider: {name!r} (known: {DIRECT}, {SEATBELT}, {OPENSHELL}, {RUNNER_LOCAL})")
