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
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from .runner.executor import Executor

PROVIDER_ENV = "OPENWORKER_SANDBOX_PROVIDER"
DIRECT = "direct"
RUNNER_LOCAL = "runner-local"
OPENSHELL = "openshell"
SEATBELT = "seatbelt"
WINDOWS = "windows"

REGRANT_NOTICE = "The session's folders changed; the sandbox now covers the current list. Shells and variables were kept."
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
    """A workspace whose commands and file tools run in a sandbox, through the tool runner.

    `start=False` (a session's workspace): the sandbox is not made until something needs
    it, the first turn (TurnEngine starts it, so the session can say so) or, failing that,
    the first call to the runner. Opening a session, or picking its folder, builds nothing
    (seen 2026-09-28: every folder pick made a sandbox). The client and executor exist from
    the start, so the file tools are always routed to the runner, never run here."""

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
        start: bool = True,
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
        self._start_lock = threading.Lock()
        self.started = False
        self.hello: dict[str, Any] = {}
        self.client = RunnerClient(lambda: provider.open_runner())  # looked up when it connects
        self._executor = RunnerExecutor(
            self.client, cwd=str(Path(cwd).expanduser().resolve()), shell=shell, before_call=self.sync_roots, after_call=self.network_note
        )
        # Whether the agent can ask the person for a site (`request_network_access`): set by
        # the session's builder; false where nobody can answer (full access, no such tool).
        self.can_ask_network: Callable[[], bool] = lambda: False
        # Refusals up to here have been told to the agent. One that reaches us after its
        # command's result (OpenShell's log lags) is told with the next result, not lost.
        self._blocked_told = time.time()
        if start:
            self.ensure_started()
        else:
            self.client.starter = self.ensure_started

    def ensure_started(self) -> None:
        """Make the sandbox, connect to its runner and prove the wall. Once; a failure
        leaves nothing behind and the next call tries again."""
        with self._start_lock:
            if self.started:
                return
            registry = self.registry
            if registry is not None:
                registry.reap()  # sandboxes left behind by a server that is gone
                registry.check_room()
                # Reserve the name before the sandbox exists: engine builds run concurrently
                # (OPE-206), and another build's reap() would otherwise delete this one while
                # it is still provisioning, because it is not yet in the registry.
                self._record(state="creating")
            try:
                self.provider.create()
            except Exception:
                self.provider.destroy()  # a half-made sandbox may already hold copied credentials
                if registry is not None and self._registered:
                    registry.close(self._registered)
                    self._registered = None
                raise
            try:
                self.hello = self.client.connect()
                self._after_connect()
            except Exception:
                self.client.detach()
                self.provider.destroy()
                if registry is not None and self._registered:
                    registry.close(self._registered)
                    self._registered = None
                raise
            self._record()
            self.started = True
            self.client.starter = None

    def _record(self, state: str = "ready") -> None:
        """Enter this sandbox in the registry; after a restart that replaced it, under its
        new name. `state="creating"` reserves the name before the sandbox exists."""
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
            state=state,
        )

    def sync_roots(self) -> Optional[str]:
        """Restart the sandbox when the session's folders are no longer the ones it was
        started with (design ruling 15). Returns what to tell the agent, or None. Runs
        before every command, so it is also where a sandbox not yet started starts."""
        self.ensure_started()
        regrant = getattr(self.provider, "regrant", None)
        if regrant is None or self._live_roots is None:
            return None
        wanted = [{"path": str(r.path), "writable": bool(r.writable)} for r in self._live_roots]
        if not wanted or _same_roots(wanted, getattr(self.provider, "roots", [])):
            return None
        restarts = getattr(self.provider, "restarts_on_regrant", True)
        if restarts:
            self.client.detach()
            # Reserve the replacement's name before it exists, for the same reason as at
            # start: a concurrent build's reap() must not take it for an orphan.
            regrant(wanted, before_create=lambda: self._record(state="creating"))
            self.client.connect()  # a new runner: the client notes the restart
            self._after_connect()
        else:
            regrant(wanted)  # the same sandbox, re-granted in place (Windows: entries on folders)
            verify = getattr(self.provider, "verify", None)
            if verify is not None:
                verify(self.client)
        self._record()
        return RESTART_NOTICE if restarts else REGRANT_NOTICE

    def _after_connect(self) -> None:
        """What a provider does once it can talk to its runner: prove the wall is up, then
        put in what it could not put in before the runner existed (copies that must be
        owned by the sandbox's own account)."""
        verify = getattr(self.provider, "verify", None)
        if verify is not None:
            verify(self.client)  # e.g. every folder is really reachable inside
        provision = getattr(self.provider, "provision", None)
        if provision is not None:
            provision(self.client)

    @property
    def executor(self) -> Executor:
        return self._executor

    def context(self) -> str:
        """What the agent is told about this sandbox each turn (ruling 21)."""
        from .credentials import context_lines

        text = context_lines(getattr(self.provider, "copied", None))
        notes = getattr(self.provider, "notes", None) or []
        return "\n".join(part for part in [text, *notes, self._network_context()] if part)

    def remove_hosts(self, hosts: Sequence[str]) -> None:
        """The person took a site back from this session: the sandbox stops letting it out."""
        with self._start_lock:
            self.provider.remove_hosts(hosts)

    def _asking(self) -> bool:
        try:
            return bool(self.can_ask_network())
        except Exception:  # noqa: BLE001
            return False

    def _network_context(self) -> str:
        """The allowed sites, told to the agent each turn from the live list (OPE-219): which
        sites its commands reach, and what to do about one that is blocked. Says nothing
        when the sandbox lets every site out."""
        if getattr(self.provider, "profile", "") != "allowlist":
            return ""
        sites = ", ".join(_site_name(h) for h in getattr(self.provider, "extra_hosts", None) or []) or "none yet"
        ask = (
            "If the task needs a blocked site, call request_network_access with the host and a one-line reason, then run the command again once it is allowed. Ask only for sites the task needs."
            if self._asking()
            else "The allowed sites cannot be changed from this session. If the task needs a blocked site, tell the user; they can add it in Settings > Sandbox > Choose sites."
        )
        return (
            f"Commands in this session run in a sandbox. They reach only these sites: {sites}. "
            "A command that tries another site fails, and its result may name the sites the sandbox blocked. "
            f"{ask} Do not retry a blocked command unchanged, and do not look for another route to a blocked site."
        )

    @property
    def reports_blocked(self) -> bool:
        """Whether this sandbox can say what it blocked. Where it cannot, nothing may be read
        into an empty answer."""
        return bool(getattr(self.provider, "reports_blocked", False))

    def blocked_since(self, since: float) -> list[tuple[float, str]]:
        """(time, "host:port") of the connections this session's sandbox refused since then."""
        ask = getattr(self.provider, "blocked_since", None)
        return list(ask(since)) if ask is not None else []

    def network_note(self, started: float, result: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        """What joins a command's result when the sandbox blocked something: the sites, and
        a note that says plainly what is and is not known. The attempts are tied to the time
        the command ran, not to the command.

        Where the sandbox reports late (`blocked_lag`), a failed command waits that long
        for its refusal, and leaves as soon as one arrives; a command that succeeded does
        not wait. A refusal that still arrives after its result is told with the next one."""
        new = self._blocked_untold()
        lag = float(getattr(self.provider, "blocked_lag", 0.0) or 0.0)
        failed = result is not None and (result.get("exit_code") != 0 or bool(result.get("timed_out")) or bool(result.get("error")))
        if not new and lag and failed and self.reports_blocked:
            deadline = time.time() + lag
            while not new and time.time() < deadline:
                time.sleep(0.05)
                new = self._blocked_untold()
        if not new:
            return None
        self._blocked_told = max(when for when, _ in new)
        counts: dict[str, int] = {}
        for _, entry in new:
            counts[entry] = counts.get(entry, 0) + 1
        late = any(when < started - 0.5 for when, _ in new)
        return {"blocked_sites": list(counts), "network_note": network_note_text(counts, can_ask=self._asking(), late=late)}

    def _blocked_untold(self) -> list[tuple[float, str]]:
        return [item for item in self.blocked_since(self._blocked_told) if item[0] > self._blocked_told]

    def describe(self) -> dict[str, Any]:
        return {**self.provider.describe(), "runner": {k: self.hello.get(k) for k in ("runner_version", "os", "machine", "instance_id")}}

    def add_hosts(self, hosts: Sequence[str]) -> None:
        """The person allowed a site (OPE-219): the sandbox's network list grows at once, for
        commands as well as the web tools. Under the start lock, so a sandbox being made
        takes the list as it stands afterwards."""
        with self._start_lock:
            self.provider.add_hosts(hosts)

    def close(self) -> None:
        if not self.started:
            self.client.close()
            try:
                self.provider.destroy()  # no sandbox was made; its private folder may have been
            except Exception:
                pass
            return
        try:
            self._executor.close()
        finally:
            try:
                # Ask the daemon to leave by itself first: it ends its shells and removes
                # what only it can (its folder, copies it was handed). The provider's
                # destroy() is the hard stop behind it.
                self.client.call("runner.shutdown", timeout=5)
            except Exception:
                pass
            self.client.close()
            self.provider.destroy()
            if self.registry is not None and self._registered:
                self.registry.close(self._registered)


_NOTE_SITES = 10  # more than this many blocked sites in one command are counted, not listed


def _site_name(entry: str) -> str:
    """"github.com:443" reads as "github.com": the port only matters when it is not the web's."""
    return entry[:-4] if entry.endswith(":443") else entry


def network_note_text(counts: dict[str, int], *, can_ask: bool, late: bool = False) -> str:
    """The note itself (owner wording 2026-10-03: open about what the sandbox saw, and
    about not knowing whether it is what broke the command). `late`: some of the refusals
    happened before this command started and were only now reported."""
    lines = [f"  - {entry}" + (f" ({n} attempts)" if n > 1 else "") for entry, n in list(counts.items())[:_NOTE_SITES]]
    if len(counts) > _NOTE_SITES:
        lines.append(f"  - and {len(counts) - _NOTE_SITES} more")
    ask = (
        "If you think a blocked site caused the failure, ask the user to allow it with request_network_access(hosts, reason), then run the command again. Ask only for sites the task needs."
        if can_ask
        else "The allowed sites cannot be changed from this session. If a blocked site is needed, tell the user; they can add it in Settings > Sandbox > Choose sites."
    )
    return (
        "Note: this command ran in a sandbox, and the sandbox's network rules may have affected it.\n"
        + ("Since your previous command, the sandbox blocked connections to:\n" if late else "While it ran, the sandbox blocked connections to:\n")
        + "\n".join(lines)
        + "\n"
        "These attempts may have come from this command or from something else running in this session. " + ask
    )


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
    toolchains: Optional[list] = None,
    extra_hosts: Optional[list] = None,
    start: bool = True,
) -> Workspace:
    """The session's workspace for the configured provider. `credentials`: the machine's
    `sandbox_credentials` setting; the enabled entries are copied into the sandbox
    (design doc, section 11b). Ignored in `direct` mode, where nothing is hidden anyway. `direct` unless told otherwise.
    `extra_hosts`: the machine's `sandbox_network_hosts`, the sites an allow list lets through.
    `start=False`: make the sandbox on first use instead of now (a session's workspace).
    `toolchains`: the machine's `sandbox_toolchains` setting; the switched-on folders that
    exist are readable inside (Seatbelt, Windows full mode).
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
    from .network_profiles import check, clean_hosts, default_profile

    profile = check((network_profile or "").strip().lower() or default_profile())
    added = clean_hosts(extra_hosts)
    from . import toolchains as toolchain_list

    tool_dirs = toolchain_list.granted(toolchains)
    if name == SEATBELT:
        from .providers.seatbelt import SeatbeltProvider
        from .registry import SandboxRegistry

        return RunnerWorkspace(
            SeatbeltProvider(roots=listed, cwd=str(cwd), credentials=grants, profile=profile, tool_dirs=tool_dirs, extra_hosts=added),
            cwd=cwd,
            registry=SandboxRegistry(),
            session_id=session_id,
            agent=agent,
            live_roots=roots,
            start=start,
        )
    if name == OPENSHELL:
        from .providers.openshell import OpenShellProvider
        from .registry import SandboxRegistry

        label = "-".join(part for part in (session_id[:24], agent[:24]) if part)
        registry = SandboxRegistry()
        return RunnerWorkspace(
            OpenShellProvider(roots=listed, cwd=str(cwd), label=label, credentials=grants, profile=profile, extra_hosts=added, registry=registry.id),
            cwd=cwd,
            registry=registry,
            session_id=session_id,
            agent=agent,
            live_roots=roots,
            start=start,
        )
    if name == WINDOWS:
        from .providers.windows import WindowsProvider
        from .registry import SandboxRegistry

        return RunnerWorkspace(
            WindowsProvider(roots=listed, cwd=str(cwd), credentials=grants, profile=profile, tool_dirs=tool_dirs, extra_hosts=added),
            cwd=cwd,
            registry=SandboxRegistry(),
            session_id=session_id,
            agent=agent,
            live_roots=roots,
            start=start,
        )
    raise ValueError(f"unknown sandbox provider: {name!r} (known: {DIRECT}, {SEATBELT}, {WINDOWS}, {OPENSHELL}, {RUNNER_LOCAL})")
