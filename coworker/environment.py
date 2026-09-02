"""Session environment context.

Saves the agent 3-4 discovery tool calls every session (pwd, uname, git status, git log)
by telling it up front where it is and what state the workspace is in.

Split in two on purpose, and the split is the whole point:

`environment_context` is STABLE — workspace, platform, folder scope — and lives in the
system prompt, which is the cached prefix. `environment_live` is VOLATILE — today's date
and the git snapshot — and is appended per turn, after the history, where a change costs
nothing.

They used to be one block in the system prompt, and it carried `git status --porcelain`
plus the last five commits. Every file the agent wrote changed those bytes, so the prefix
changed, so everything after it — conventions, user rules, memories, and the whole
conversation — was re-prefilled on essentially every turn of a build session. Measured on
this machine: vLLM was serving a 92% prefix-cache hit rate that this block was throwing
away exactly when the agent was busiest.

Moving the snapshot out also made it honest. It used to be labelled a session-start
snapshot the agent had to re-verify; now it is recomputed each turn and simply true.
"""

from __future__ import annotations

import platform as _platform
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Optional


def _git(workspace: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(workspace), *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _git_snapshot(workspace: Path) -> list[str]:
    if _git(workspace, "rev-parse", "--is-inside-work-tree") != "true":
        return ["Git: not a git repository"]

    lines = []
    branch = _git(workspace, "rev-parse", "--abbrev-ref", "HEAD") or "(unknown)"
    lines.append(f"Git branch: {branch}")

    status = _git(workspace, "status", "--porcelain")
    if status is not None:
        changed = status.splitlines()
        if not changed:
            lines.append("Git status: clean")
        else:
            shown = "\n".join(changed[:20])
            more = f"\n… and {len(changed) - 20} more" if len(changed) > 20 else ""
            lines.append(f"Git status ({len(changed)} changed):\n{shown}{more}")

    log = _git(workspace, "log", "-n5", "--pretty=format:%h %s")
    if log:
        lines.append(f"Recent commits:\n{log}")
    return lines


def environment_context(workspace: str | Path) -> str:
    """The STABLE half: identical byte-for-byte for the life of a session.

    Nothing here may depend on the clock or on anything the agent can change — this text
    is the head of the cached prefix. Volatile facts belong in `environment_live`.
    """
    ws = Path(workspace).expanduser().resolve()
    mac = _platform.mac_ver()[0]
    os_name = f"macOS {mac}" if mac else f"{_platform.system()} {_platform.release()}"
    lines = [
        f"Workspace: {ws}",
        f"Platform: {sys.platform} ({os_name})",
    ]
    body = "\n".join(lines)
    return (
        f"Environment:\n<environment>\n{body}\n</environment>\n"
        "Folder scope: work inside the workspace and any folders the user has granted. Do not "
        "read or list other locations (home directory sweeps, ~/Desktop, ~/Downloads, photo "
        "libraries, etc.) — not even via shell commands like find/ls/grep. On macOS every such "
        "touch fires an OS permission prompt the user can't connect to any action they took. "
        "If a task needs files elsewhere, ask first with request_directory."
    )


def environment_live(workspace: str | Path) -> str:
    """The VOLATILE half: recomputed every turn, appended after the history.

    Date and git state. Cheap to change here because nothing downstream is cached — and
    unlike the old session-start snapshot, what it says is true right now.
    """
    ws = Path(workspace).expanduser().resolve()
    lines = [f"Today's date: {date.today().isoformat()}", *_git_snapshot(ws)]
    body = "\n".join(lines)
    return f"<environment-now>\n{body}\n</environment-now>"
