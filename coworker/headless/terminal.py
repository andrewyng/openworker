"""`openworker run` at a terminal: approval cards and questions as plain prompts.

A run with a person at the keyboard asks there, on standard error, so standard output
stays the coworker's answer and nothing else. Approvals and questions are separate
(coworker/unattended.py): `--auto-answer` answers the questions by rule and leaves the
approvals with the person.

    Approval needed: run a command
      npm install
      in ~/src/project
      [y] yes, once   [s] yes, for this session   [n] no
    >

The network card has no "always": a run reads this computer's allowed sites and never
changes them.

Folder requests and pinned tool installs are asked the same way; they go with the
questions, so `--auto-answer` answers them by rule too.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from ..engine import ApprovalOutcome, PermissionRequest
from ..permissions import NETWORK_ACCESS_TOOL

_VALUE_LIMIT = 400


def _short(text: str, limit: int = _VALUE_LIMIT) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _home_relative(path: str) -> str:
    try:
        home = str(Path.home())
    except Exception:  # noqa: BLE001
        return path
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def _ago(seconds: Any) -> str:
    try:
        n = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    if n < 60:
        return f"{n} second{'' if n == 1 else 's'} ago"
    minutes = n // 60
    return f"{minutes} minute{'' if minutes == 1 else 's'} ago"


def _site(entry: str) -> str:
    return entry[: -len(":443")] if entry.endswith(":443") else entry


def session_outcome(request: PermissionRequest) -> Optional[ApprovalOutcome]:
    """The grant "[s] yes, for this session" stands for on this card: the narrowest one the
    app's card would offer (this command, this site, this tool). None when the card has no
    session choice, and then the prompt offers yes and no only."""
    from ..server.manager import _grant_offered

    for outcome in (
        ApprovalOutcome.ALWAYS_COMMAND,
        ApprovalOutcome.ALWAYS_DOMAIN,
        ApprovalOutcome.THIS_RUN,
        ApprovalOutcome.ALWAYS_TOOL,
    ):
        if _grant_offered(outcome, request):
            return outcome
    return None


def _option_label(option: Any) -> str:
    if isinstance(option, dict):
        return str(option.get("label") or option.get("value") or "").strip()
    return str(option).strip()


class Terminal:
    """The prompts. `read_line` returns the typed line without its newline, or None at end
    of input; `write` prints one piece of text. Both are plain blocking calls."""

    def __init__(
        self,
        read_line: Callable[[], Optional[str]],
        write: Callable[[str], None],
        *,
        cwd: Optional[Path] = None,
    ) -> None:
        self._read_line = read_line
        self._write = write
        self.cwd = cwd
        # The card the engine announced last (PERMISSION_REQUIRED): what the approver is
        # about to be asked. It carries what the request alone does not: the network
        # request's sites and whether the sandbox blocked them.
        self.card: dict[str, Any] = {}
        # Adds a folder to the running session and returns its resolved path; raises with
        # the reason when it cannot. Set by the run once the engine exists.
        self.grant_folder: Optional[Callable[[Path, bool], Path]] = None
        self._lock = asyncio.Lock()

    @classmethod
    def attached(cls, cwd: Optional[Path] = None) -> Optional["Terminal"]:
        """A terminal the person can be asked on, or None (a pipe, a CI job, a harness)."""
        try:
            if not (sys.stdin.isatty() and sys.stderr.isatty()):
                return None
        except Exception:  # noqa: BLE001 - a closed stream is not a terminal
            return None

        def read_line() -> Optional[str]:
            line = sys.stdin.readline()
            return line.rstrip("\r\n") if line else None

        def write(text: str) -> None:
            sys.stderr.write(text)
            sys.stderr.flush()

        return cls(read_line, write, cwd=cwd)

    # -- plumbing -------------------------------------------------------------------------

    async def _prompt(self) -> Optional[str]:
        """One typed line. Read on a daemon thread, not the loop's pool: a run that ends
        while the prompt waits (Ctrl-C, the time limit) must not wait for Enter to exit."""
        self._write("> ")
        loop = asyncio.get_running_loop()
        typed: asyncio.Future[Optional[str]] = loop.create_future()

        def deliver(line: Optional[str]) -> None:
            if not typed.done():
                typed.set_result(line)

        def read() -> None:
            try:
                line = self._read_line()
            except Exception:  # noqa: BLE001 - a broken terminal reads as end of input
                line = None
            try:
                loop.call_soon_threadsafe(deliver, line)
            except RuntimeError:  # the run is over
                pass

        threading.Thread(target=read, name="openworker-run-prompt", daemon=True).start()
        return await typed

    def _say(self, *lines: str) -> None:
        self._write("\n".join(lines) + "\n")

    # -- approvals ------------------------------------------------------------------------

    def _describe(self, request: PermissionRequest) -> tuple[str, list[str]]:
        """The card's heading and body lines."""
        args = request.arguments or {}
        name = request.tool_name
        network = self.card.get("network_request") if name == NETWORK_ACCESS_TOOL else None
        if network:
            hosts = [h for h in network.get("hosts") or [] if isinstance(h, dict)]
            names = ", ".join(_site(str(h.get("host", ""))) for h in hosts)
            body = []
            if network.get("reason"):
                body.append(f"Reason: {_short(network['reason'])}")
            for h in hosts:
                seen = _ago(h.get("blocked_seconds_ago")) if h.get("blocked_seconds_ago") is not None else ""
                site = _site(str(h.get("host", "")))
                if seen:
                    body.append(f"The sandbox blocked {site} {seen}." if len(hosts) > 1 else f"The sandbox blocked this site {seen}.")
                elif network.get("evidence"):
                    body.append(f"No command has tried {site} yet." if len(hosts) > 1 else "No command has tried this site yet.")
            return f"let commands reach {names}", body
        wall = self.card.get("site_wall")
        if wall:
            body = [f"{name}: {_short(args.get('url') or args.get('query') or '')}".rstrip(": ")]
            body.append("This site is not on the sandbox's allowed sites.")
            return f"reach {_site(str(wall))}", body
        command = args.get("command")
        if isinstance(command, str) and command.strip():
            body = [_short(command.strip())]
            where = args.get("cwd") or (str(self.cwd) if self.cwd else "")
            if where:
                body.append(f"in {_home_relative(str(where))}")
            return "run a command", body
        body = []
        for key, value in args.items():
            text = value if isinstance(value, str) else json.dumps(value, default=str)
            body.append(f"{key}: {_short(text, 200)}")
        return f"use {name}", body[:8]

    async def approve(self, request: PermissionRequest) -> ApprovalOutcome:
        async with self._lock:
            heading, body = self._describe(request)
            reason = (request.reason or "").strip()
            network = request.tool_name == NETWORK_ACCESS_TOOL
            session = ApprovalOutcome.ALWAYS_DOMAIN if network else session_outcome(request)
            lines = ["", f"Approval needed: {heading}", *[f"  {line}" for line in body]]
            if reason and reason != "requires approval" and not network:
                lines.append(f"  Why it asks: {_short(reason, 200)}")
            if network:
                lines.append("  [s] yes, for this run   [n] no")
            elif session is not None:
                lines.append("  [y] yes, once   [s] yes, for this session   [n] no")
            else:
                lines.append("  [y] yes   [n] no")
            self._say(*lines)
            self.card = {}
            while True:
                typed = await self._prompt()
                if typed is None:  # the terminal went away: nobody can say yes
                    return ApprovalOutcome.DENY
                choice = typed.strip().lower()
                if choice in ("n", "no"):
                    return ApprovalOutcome.DENY
                if choice in ("s", "session") and session is not None:
                    return session
                if choice in ("y", "yes"):
                    # The network card's only yes is "for this run".
                    return session if network and session is not None else ApprovalOutcome.ONCE
                self._say("  Type " + ("s or n." if network else "y, s or n." if session is not None else "y or n."))

    # -- questions ------------------------------------------------------------------------

    async def _one_question(self, entry: dict[str, Any]) -> str:
        question = str(entry.get("question") or "").strip()
        options = [label for label in (_option_label(o) for o in entry.get("options") or []) if label]
        multi = bool(entry.get("multi") or entry.get("multiSelect"))
        free_text = entry.get("allow_text", True) is not False
        lines = [f"  {question}"]
        for i, label in enumerate(options, 1):
            lines.append(f"    {i}. {label}")
        if options and multi:
            lines.append("  Type the numbers, separated by commas" + (", or your own answer:" if free_text else ":"))
        elif options:
            lines.append("  Type a number" + (", or your own answer:" if free_text else ":"))
        else:
            lines.append("  Type your answer:")
        self._say(*lines)
        while True:
            typed = await self._prompt()
            if typed is None:
                return ""
            text = typed.strip()
            if not text:
                continue
            if options:
                picks = [p.strip() for p in text.split(",")] if multi else [text]
                if all(p.isdigit() and 1 <= int(p) <= len(options) for p in picks):
                    return ", ".join(options[int(p) - 1] for p in picks)
                if not free_text:
                    self._say(f"  Type a number from 1 to {len(options)}.")
                    continue
            return text

    async def ask(self, args: dict[str, Any], _call_id: Optional[str] = None) -> dict[str, Any]:
        """The engine's question hook: `{"answer": …}` for one question, `{"answers": {…}}`
        for a group, keyed by header or question as the other surfaces key them."""
        async with self._lock:
            grouped = [
                entry
                for entry in (args.get("questions") or [])
                if isinstance(entry, dict) and str(entry.get("question", "")).strip()
            ]
            self._say("", "The coworker asks:")
            if grouped:
                answers: dict[str, str] = {}
                for entry in grouped:
                    answers[str(entry.get("header") or entry.get("question"))] = await self._one_question(entry)
                return {"answers": answers}
            return {"answer": await self._one_question(args)}

    # -- folders and tool installs --------------------------------------------------------

    async def directory(self, args: dict[str, Any], _call_id: Optional[str] = None) -> dict[str, Any]:
        """The engine's folder-request hook: the coworker asks for another folder, to read
        or to read and write. The person says yes, yes but read only, no, or types a
        different folder. `grant_folder` (set by the run) adds it to the session."""
        async with self._lock:
            asked = str(args.get("path") or "").strip()
            writable = bool(args.get("writable", False))
            reason = str(args.get("reason") or "").strip()
            access = "to read and write" if writable else "to read"
            lines = ["", f"The coworker asks for a folder {access}" + (f": {_home_relative(asked)}" if asked else "")]
            if reason:
                lines.append(f"  Reason: {_short(reason, 200)}")
            if asked:
                keys = "[y] yes   " + ("[r] yes, read only   " if writable else "") + "[n] no"
                lines.append(f"  {keys}   or type another folder")
            else:
                lines.append("  Type the folder, or n for no")
            self._say(*lines)
            while True:
                typed = await self._prompt()
                if typed is None:
                    return {"granted": False, "reason": "the user declined the request"}
                text = typed.strip()
                choice = text.lower()
                if not text:
                    continue
                if choice in ("n", "no"):
                    return {"granted": False, "reason": "the user declined the request"}
                path, grant_writable = asked, writable
                if choice in ("y", "yes") and asked:
                    pass
                elif choice in ("r", "read", "read only") and asked and writable:
                    grant_writable = False
                elif choice in ("y", "yes", "r"):
                    self._say("  Type the folder, or n for no.")
                    continue
                else:
                    path = text
                folder = Path(path).expanduser()
                if not folder.is_dir():
                    self._say(f"  {_home_relative(str(folder))} is not a folder. Type another, or n for no.")
                    continue
                if self.grant_folder is None:
                    return {"granted": False, "error": "directory requests aren't available here"}
                try:
                    granted = self.grant_folder(folder, grant_writable)
                except Exception as exc:  # noqa: BLE001 - say why, and let the person choose again
                    self._say(f"  That folder cannot be given: {exc}. Type another, or n for no.")
                    continue
                return {"granted": True, "path": str(granted), "writable": grant_writable}

    async def tool(self, args: dict[str, Any], _call_id: Optional[str] = None) -> dict[str, Any]:
        """The engine's tool-install hook: only reached for a tool in the pinned catalog,
        so yes installs the verified build."""
        from .. import toolchain

        name = str(args.get("name") or "").strip()
        info = toolchain.describe(name) or {}
        async with self._lock:
            lines = ["", f"The coworker asks to install a tool: {name}" + (f" {info['version']}" if info.get("version") else "")]
            if info.get("summary"):
                lines.append(f"  {_short(info['summary'], 200)}")
            if args.get("reason"):
                lines.append(f"  Reason: {_short(args['reason'], 200)}")
            lines.append("  [y] install it   [n] no")
            self._say(*lines)
            while True:
                typed = await self._prompt()
                choice = (typed or "n").strip().lower() if typed is not None else "n"
                if choice in ("n", "no"):
                    return {"installed": False, "error": "the user declined the install"}
                if choice in ("y", "yes"):
                    break
                self._say("  Type y or n.")
            try:
                path = await asyncio.to_thread(toolchain.install, name)
            except Exception as exc:  # noqa: BLE001 - the coworker must hear why
                self._say(f"  The install failed: {exc}")
                return {"installed": False, "error": f"install failed: {exc}"}
            return {"installed": True, "path": path, "version": info.get("version", "")}
