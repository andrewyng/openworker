"""CLI entry point. `coworker` launches the TUI; `coworker code` boots the code skill."""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

from .config import load_config
from .conversations import ConversationStore
from .memory import SQLiteMemoryStore
from .permissions import Mode
from .secrets import state_dir


def main(argv: Optional[list[str]] = None) -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser(
        prog="openworker", description="Agent coworker (TUI)."
    )
    parser.add_argument(
        "skill", nargs="?", default="code", help="skill to launch (default: code)"
    )
    parser.add_argument("--cwd", default=".", help="workspace directory")
    parser.add_argument(
        "--project",
        default=None,
        help="project name or path to start in (overrides --cwd)",
    )
    parser.add_argument(
        "--model", default=cfg.model, help="model id, e.g. openai gpt-5.5"
    )
    parser.add_argument(
        "--mode",
        default=cfg.mode,
        choices=["plan", "interactive", "auto"],
        help="permission mode",
    )
    parser.add_argument("--resume", default=None, help="resume a session id")
    args = parser.parse_args(argv)

    workspace = Path(args.cwd).expanduser().resolve()
    # Unified global store shared with the GUI/server (one place for all conversations).
    data_dir = state_dir()
    memory_store = SQLiteMemoryStore(data_dir / "coworker.db")
    session_store = ConversationStore(data_dir)
    if args.project:
        proj = _resolve_project(session_store, args.project)
        if proj is None:
            print(f"project not found: {args.project}", file=sys.stderr)
            raise SystemExit(1)
        workspace = Path(proj["path"]).resolve()
    session_store.touch_workspace(os.path.realpath(str(workspace)))

    resume_messages = None
    session_id = args.resume or uuid.uuid4().hex[:12]
    model, mode = args.model, args.mode
    if args.resume:
        record = session_store.load(args.resume)
        if record is not None:
            resume_messages = record.messages
            model, mode = record.model, record.mode

    from .tui.app import CoworkerApp

    app = CoworkerApp(
        workspace=workspace,
        model=model,
        mode=Mode(mode),
        memory_store=memory_store,
        session_store=session_store,
        session_id=session_id,
        resume_messages=resume_messages,
    )
    app.run()


def _resolve_project(
    session_store: "ConversationStore", ref: str
) -> Optional[dict]:
    """Resolve `--project` by project name or by filesystem path.

    A bare path that exists is used directly as the workspace (it will auto-bind to a
    registered project on first save); a name must match a registered project.
    """
    if ref.startswith(("~", "/")) or Path(ref).expanduser().exists():
        p = Path(ref).expanduser().resolve()
        if p.is_dir():
            return session_store.project_by_path(str(p)) or {
                "project_id": None,
                "name": p.name,
                "path": str(p),
                "n_sessions": 0,
            }
        return None
    for proj in session_store.list_projects():
        if proj["name"] == ref:
            return proj
    return None


if __name__ == "__main__":
    main()
