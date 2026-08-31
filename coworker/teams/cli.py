"""`ocw` — the board and journal from any shell, for any harness.

The board is an open surface (OPE-100): the same role-scoped verbs the in-app
agents get, usable by an external agent CLI, a script, or a human. Point it at a
running OpenWorker server (same machine or remote) or straight at a state dir.

Backing resolution, in order:
1. `--url` + `--token` (or OCW_BOARD_URL / OCW_BOARD_TOKEN) — a remote board.
2. `--db DIR` — direct SQLite in that state dir (headless; you are the only writer).
3. A running local server, discovered via its sidecar token files — the CLI mints
   itself a local user token on first use. This is preferred over direct SQLite
   whenever a server is up: two processes must never write one board file.
4. Direct SQLite on the default state dir (nothing else is running).

`ocw board mcp` serves the same surface as an MCP server on stdio — the way to
hand a board to an external coding agent: point the agent's MCP config at
`ocw board mcp --url … --token … --space …` and ask it to claim a work item.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from .model import BoardError, space_for_workspace
from .store import CLAIM_POLICIES

_STATES = ("open", "in_progress", "blocked", "review", "done", "canceled")


def main(argv: Optional[list[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except BoardError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocw", description="OpenWorker team board + journal CLI."
    )
    sub = parser.add_subparsers(dest="group")

    board = sub.add_parser("board", help="work-item board verbs")
    board_sub = board.add_subparsers(dest="cmd")

    def cmd(name: str, func, help: str, parent=board_sub):
        p = parent.add_parser(name, help=help)
        _backing_args(p)
        p.set_defaults(func=func, cmd=name)
        return p

    p = cmd("list", _cmd_list, "list items")
    p.add_argument("--state", choices=_STATES, default="")
    p.add_argument("--assignee", default="")
    p.add_argument("--mine", action="store_true", help="only items assigned to me")

    p = cmd("show", _cmd_show, "one item, with comments")
    p.add_argument("id", type=int)

    p = cmd("create", _cmd_create, "file a new item (open, unassigned)")
    p.add_argument("title")
    p.add_argument("--criteria", required=True, help="acceptance criteria")
    p.add_argument("--description", default="")
    p.add_argument("--parent", type=int, default=None)
    p.add_argument("--case", default="")

    p = cmd("claim", _cmd_claim, "claim an open, unassigned item for yourself")
    p.add_argument("id", type=int)

    p = cmd("move", _cmd_move, "transition an item")
    p.add_argument("id", type=int)
    p.add_argument("to", choices=_STATES[1:] + ("open",))
    p.add_argument("--comment", default="")
    p.add_argument("--ref", action="append", default=[], dest="refs")

    p = cmd("comment", _cmd_comment, "comment on an item")
    p.add_argument("id", type=int)
    p.add_argument("body")
    p.add_argument("--ref", action="append", default=[], dest="refs")

    p = cmd("assign", _cmd_assign, "assign an item (lead/user)")
    p.add_argument("id", type=int)
    p.add_argument("assignee")

    p = cmd("attach", _cmd_attach, "attach a screenshot/image to an item")
    p.add_argument("id", type=int)
    p.add_argument("file", help="image file (png/jpg/gif/webp, ≤10MB)")
    p.add_argument("--caption", default="")

    p = cmd("attachment", _cmd_attachment, "download an attachment by ref or name")
    p.add_argument("ref", help="attachment:// ref or <sha256>.<ext> name")
    p.add_argument("-o", "--out", default="", help="output path (default: basename)")

    p = cmd("link", _cmd_link, "link two items")
    p.add_argument("src", type=int)
    p.add_argument("kind", choices=("parent", "blocks"))
    p.add_argument("dst", type=int)

    p = cmd("policy", _cmd_policy, "show or set the board's claim policy")
    p.add_argument("--claims", choices=CLAIM_POLICIES, default="")

    p = cmd("pending", _cmd_pending, "my unconsumed deliveries (assignments etc.)")
    p.add_argument("--consume", action="store_true", help="advance my cursor")
    p.add_argument("--limit", type=int, default=50)

    cmd("spaces", _cmd_spaces, "list known board spaces")

    # `token` manages the serving machine's registry file directly — it takes no
    # backing/identity flags of its own (minting is what CREATES identities).
    p = board_sub.add_parser(
        "token", help="mint/list/revoke board join tokens (serving machine)"
    )
    p.add_argument("action", choices=("mint", "list", "revoke"))
    p.add_argument("--actor", default="", help="callname the token binds (mint)")
    p.add_argument(
        "--role", choices=("worker", "lead", "user"), default="worker"
    )
    p.add_argument("--label", default="", help="what this token is for (mint)")
    p.add_argument("--prefix", default="", help="token prefix to revoke")
    p.add_argument("--db", default="", help="state dir holding the registry")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_token, cmd="token")

    p = cmd("mcp", _cmd_mcp, "serve this board over MCP on stdio")

    journal = sub.add_parser("journal", help="journal case verbs")
    journal_sub = journal.add_subparsers(dest="cmd")

    p = cmd("cases", _cmd_cases, "cases I can read", parent=journal_sub)

    p = cmd("read", _cmd_read, "read a case (filtered)", parent=journal_sub)
    p.add_argument("case")
    p.add_argument("--item", type=int, default=None)
    p.add_argument("--author", default="")
    p.add_argument("--kind", default="")
    p.add_argument("--entity", default="")
    p.add_argument("--raw", action="store_true", dest="include_raw")
    p.add_argument("--limit", type=int, default=50)

    p = cmd("append", _cmd_append, "append an entry to a case", parent=journal_sub)
    p.add_argument("case")
    p.add_argument("body")
    p.add_argument(
        "--kind",
        choices=("finding", "evidence", "decision", "note", "raw"),
        default="note",
    )
    p.add_argument("--item", type=int, default=None)
    p.add_argument("--entity", action="append", default=[], dest="entities")
    p.add_argument("--ref", action="append", default=[], dest="refs")

    return parser


def _backing_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--url", default=os.environ.get("OCW_BOARD_URL", ""))
    p.add_argument("--token", default=os.environ.get("OCW_BOARD_TOKEN", ""))
    p.add_argument("--db", default="", help="state dir for direct (headless) access")
    p.add_argument("--actor", dest="local_actor", default="user")
    p.add_argument("--role", dest="local_role", default="user")
    p.add_argument(
        "--space",
        default=os.environ.get("OCW_BOARD_SPACE", ""),
        help="board space (default: this directory's workspace)",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")


# ------------------------------------------------------------------ backing


def _space(args) -> str:
    return args.space or space_for_workspace(Path.cwd())


def _dialect(args):
    from .dialect import RemoteDialect, local_dialect

    if args.url:
        if not args.token:
            raise BoardError("--token (or OCW_BOARD_TOKEN) is required with --url")
        return RemoteDialect(args.url, args.token)
    if args.db:
        return local_dialect(args.db, actor=args.local_actor, role=args.local_role)
    server = _discover_server()
    if server is not None:
        return RemoteDialect(server, _local_cli_token())
    from ..secrets import state_dir

    return local_dialect(state_dir(), actor=args.local_actor, role=args.local_role)


def _discover_server() -> Optional[str]:
    """A running local server, found via its per-port sidecar token files."""
    import httpx

    from ..secrets import state_dir

    ports = []
    try:
        for path in state_dir().glob("sidecar-*.token"):
            try:
                ports.append(int(path.stem.split("-")[1]))
            except (IndexError, ValueError):
                continue
    except OSError:
        return None
    for port in sorted(ports, reverse=True):
        url = f"http://127.0.0.1:{port}"
        try:
            if httpx.get(f"{url}/v1/health", timeout=1.5).status_code == 200:
                return url
        except httpx.HTTPError:
            continue
    return None


def _local_cli_token() -> str:
    """The CLI's own user token against the local server. Minted once into the
    shared registry; the plaintext is cached user-only in the state dir — the
    user's own credential on the user's own machine, same pattern as the sidecar
    token file."""
    from ..secrets import state_dir, write_private_text

    from .tokens import BoardTokens

    cache = state_dir() / "ocw-cli.token"
    tokens = BoardTokens(state_dir() / "board-tokens.json")
    try:
        cached = cache.read_text().strip()
        if cached and tokens.resolve(cached) is not None:
            return cached
    except OSError:
        pass
    token = tokens.mint("user", "user", label="local ocw CLI")
    write_private_text(cache, token + "\n")
    return token


# ------------------------------------------------------------------ board cmds


def _cmd_list(args) -> int:
    dialect = _dialect(args)
    assignee = args.assignee or (dialect.whoami()["actor"] if args.mine else "")
    items = dialect.list_items(
        _space(args), state=args.state or None, assignee=assignee or None
    )
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    if not items:
        print("no items")
        return 0
    for item in items:
        who = f" @{item['assignee']}" if item["assignee"] else ""
        print(f"#{item['id']:<4} {item['state']:<12}{who:<14} {item['title']}")
    return 0


def _cmd_show(args) -> int:
    item = _dialect(args).get_item(_space(args), args.id)
    if args.json:
        print(json.dumps(item, indent=2))
        return 0
    print(f"#{item['id']} {item['title']}  [{item['state']}]")
    if item["assignee"]:
        print(f"assignee: {item['assignee']}")
    print(f"created by: {item['creator']}")
    if item["description"]:
        print(f"\n{item['description']}")
    print(f"\nDone when: {item['criteria']}")
    if item.get("refs"):
        print("refs: " + ", ".join(item["refs"]))
    for link in item.get("links") or []:
        print(f"link: {link['kind']} #{link['item']}")
    for comment in item.get("comments") or []:
        print(f"\n[{comment['ts']}] {comment['author']}: {comment['body']}")
    return 0


def _cmd_create(args) -> int:
    item = _dialect(args).create_item(
        _space(args),
        title=args.title,
        criteria=args.criteria,
        description=args.description,
        parent=args.parent,
        case=args.case or None,
    )
    print(json.dumps(item, indent=2) if args.json else f"created #{item['id']}")
    return 0


def _cmd_claim(args) -> int:
    item = _dialect(args).claim(_space(args), args.id)
    print(
        json.dumps(item, indent=2)
        if args.json
        else f"claimed #{item['id']} — it's yours; move it to in_progress when you start"
    )
    return 0


def _cmd_move(args) -> int:
    item = _dialect(args).transition(
        _space(args), args.id, args.to, comment=args.comment, refs=args.refs
    )
    print(json.dumps(item, indent=2) if args.json else f"#{item['id']} → {item['state']}")
    return 0


def _cmd_comment(args) -> int:
    _dialect(args).comment(_space(args), args.id, args.body, refs=args.refs)
    print("ok" if not args.json else json.dumps({"ok": True}))
    return 0


def _cmd_assign(args) -> int:
    item = _dialect(args).assign(_space(args), args.id, args.assignee)
    print(
        json.dumps(item, indent=2)
        if args.json
        else f"#{item['id']} → @{item['assignee']}"
    )
    return 0


def _cmd_attach(args) -> int:
    source = Path(args.file).expanduser()
    if not source.is_file():
        print(f"error: no such file: {source}", file=sys.stderr)
        return 1
    result = _dialect(args).attach(
        _space(args), args.id, source.read_bytes(), source.name, caption=args.caption
    )
    ref = result.get("ref") or next(
        (r for r in (result.get("payload") or {}).get("refs", [])), ""
    )
    print(json.dumps(result, indent=2) if args.json else f"attached → {ref}")
    return 0


def _cmd_attachment(args) -> int:
    from .attachments import stored_name

    stored = stored_name(args.ref) or args.ref
    data, _mime = _dialect(args).attachment(_space(args), stored)
    out = Path(args.out) if args.out else Path(
        args.ref.rsplit("#", 1)[-1] if "#" in args.ref else stored
    )
    out.write_bytes(data)
    print(str(out))
    return 0


def _cmd_link(args) -> int:
    _dialect(args).link(_space(args), args.src, args.kind, args.dst)
    print("ok" if not args.json else json.dumps({"ok": True}))
    return 0


def _cmd_policy(args) -> int:
    dialect = _dialect(args)
    policy = (
        dialect.set_policy(_space(args), claims=args.claims)
        if args.claims
        else dialect.policy(_space(args))
    )
    print(json.dumps(policy) if args.json else f"claims: {policy['claims']}")
    return 0


def _cmd_pending(args) -> int:
    dialect = _dialect(args)
    events = dialect.pending(_space(args), limit=args.limit)
    if args.json:
        print(json.dumps(events, indent=2))
    else:
        for event in events:
            print(f"[{event['seq']}] {event['kind']} #{event.get('item_id')}"
                  f" from {event['actor']}: {json.dumps(event['payload'])}")
        if not events:
            print("nothing pending")
    if args.consume and events:
        dialect.consume(_space(args), events[-1]["seq"])
    return 0


def _cmd_spaces(args) -> int:
    spaces = _dialect(args).spaces()
    print(json.dumps(spaces) if args.json else "\n".join(spaces) or "no spaces")
    return 0


def _cmd_token(args) -> int:
    from ..secrets import state_dir

    from .tokens import BoardTokens

    tokens = BoardTokens(
        (Path(args.db).expanduser() if args.db else state_dir()) / "board-tokens.json"
    )
    if args.action == "mint":
        if not args.actor:
            print("error: --actor is required to mint", file=sys.stderr)
            return 1
        token = tokens.mint(args.actor, args.role, label=args.label)
        print(token)
        print(
            f"# binds actor '{args.actor}' as {args.role}; shown once — store it"
            " in the client's config (OCW_BOARD_TOKEN)",
            file=sys.stderr,
        )
        return 0
    if args.action == "revoke":
        removed = tokens.revoke(args.prefix)
        print(f"revoked {removed} token(s)")
        return 0
    entries = tokens.entries()
    if args.json:
        print(json.dumps(entries, indent=2))
        return 0
    for entry in entries:
        label = f"  ({entry['label']})" if entry["label"] else ""
        print(f"{entry['prefix']}…  {entry['actor']:<16} {entry['role']:<8}{label}")
    if not entries:
        print("no tokens")
    return 0


def _cmd_mcp(args) -> int:
    from .mcp_server import serve

    serve(_dialect(args), space=_space(args))
    return 0


# ------------------------------------------------------------------ journal cmds


def _cmd_cases(args) -> int:
    cases = _dialect(args).journal_overview()
    if args.json:
        print(json.dumps(cases, indent=2))
        return 0
    for case in cases:
        print(
            f"{case.get('case', '?'):<28} {case.get('entries', 0)} entries"
            + (f"  (last {case['last_ts']})" if case.get("last_ts") else "")
        )
    if not cases:
        print("no cases")
    return 0


def _cmd_read(args) -> int:
    entries = _dialect(args).journal_read(
        args.case,
        item=args.item,
        author=args.author or None,
        kind=args.kind or None,
        entity=args.entity or None,
        include_raw=args.include_raw,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(entries, indent=2))
        return 0
    for entry in entries:
        print(f"[{entry['ts']}] {entry['author']} {entry['kind']}:"
              f" {entry.get('body') or ''}")
    if not entries:
        print("no entries")
    return 0


def _cmd_append(args) -> int:
    _dialect(args).journal_append(
        args.case,
        args.body,
        kind=args.kind,
        space=_space(args),
        item=args.item,
        entities=args.entities,
        refs=args.refs,
    )
    print("ok" if not args.json else json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
