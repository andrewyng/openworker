"""`team-board` — the board and journal as an MCP server on stdio.

The way an external coding agent joins a team: its MCP config runs
`ocw board mcp --url … --token … --space …` (or `--db …` headless), it sees the
role-scoped board tools, and the user asks it to claim an item and work. Identity
and authority never live here: the dialect is already bound to one actor (token or
local flags), and every write is judged by the store/server — this file is a thin
adapter, safe to hand to any harness.

Tool results are JSON — raw data for the agent, not prose.
"""

from __future__ import annotations

from typing import Any, Optional

from .model import BoardError


def build(dialect, *, space: str):
    """Assemble the FastMCP server for one dialect+space. Split from serve() so
    tests can inspect the registered tool set without a transport."""
    from mcp.server.fastmcp import FastMCP

    who = dialect.whoami()
    role = who.get("role", "worker")
    mcp = FastMCP(
        "team-board",
        instructions=(
            f"A shared team work board (you are '{who.get('actor')}', role"
            f" {role}) plus the team journal. Items carry acceptance criteria —"
            " what gets verified before they can be done. Typical worker loop:"
            " board_list → board_claim an open item → board_move to in_progress →"
            " work, journal_append findings as you go → board_move to review with"
            " a hand-off comment and refs. Never mark items done — done is the"
            " verdict after review."
        ),
    )

    def _safe(func, *args, **kwargs) -> Any:
        try:
            result = func(*args, **kwargs)
            if func.__name__ in ("transition", "assign", "claim", "set_status", "comment", "link"):
                from .tools import mutation_receipt
                return mutation_receipt(result)
            return result
        except (BoardError, ValueError) as error:
            return {"error": str(error)}

    @mcp.tool()
    def board_list(state: str = "", assignee: str = "", after_item: int = 0, limit: int = 50) -> Any:
        """List work items on the board, optionally filtered by state
        (open/in_progress/blocked/review/done/canceled) or assignee."""
        result = _safe(
            dialect.list_items, space, state=state or None, assignee=assignee or None
        )
        if not isinstance(result, list):
            return result
        if isinstance(after_item, bool) or not isinstance(after_item, int) or after_item < 0:
            return {"error": "after_item must be a non-negative integer"}
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            return {"error": "limit must be between 1 and 100"}
        from .tools import item_snapshot
        items = [i for i in result if i["id"] > after_item]
        page = [item_snapshot(i, brief=True) for i in items[:limit]]
        for i in page:
            if i.get("proposal_ref"):
                i["proposal_ref"]["read_tool"] = "board_proposal"
        return {"items": page, "has_more": len(items) > limit,
                "next_after_item": page[-1]["id"] if page else after_item}

    @mcp.tool()
    def board_show(item: int) -> Any:
        """Current task details, not historical comments. Use board_comments for
        incremental handoffs and board_proposal for shared approved intent."""
        from .tools import item_snapshot
        result = _safe(dialect.get_item, space, item)
        snapshot = item_snapshot(result)
        if snapshot.get("proposal_ref"):
            snapshot["proposal_ref"]["read_tool"] = "board_proposal"
        return snapshot

    @mcp.tool()
    def board_comments(item: int, after_seq: int = 0, limit: int = 20) -> Any:
        """Complete new comments after a sequence. Follow next_after_seq while
        has_more. Zero replays; oversized comments use board_comment_text."""
        result = _safe(dialect.comment_page, space, item, after_seq=after_seq, limit=limit)
        for entry in result.get("comments", []):
            if entry.get("read_tool"):
                entry["read_tool"] = "board_comment_text"
        return result

    @mcp.tool()
    def board_comment_text(item: int, seq: int, offset: int = 0, max_chars: int = 12000) -> Any:
        """Read an exact comment in bounded pages; follow next_offset while has_more."""
        return _safe(dialect.comment_text, space, item, seq=seq, offset=offset, max_chars=max_chars)

    @mcp.tool()
    def board_proposal(item: int) -> Any:
        """Read shared approved proposal intent and external-action declarations;
        these are not permission grants. Read once or when context is missing."""
        result = _safe(dialect.get_item, space, item)
        return result if "error" in result else {"proposal": result.get("proposal"), "authority": "intent_not_access_grants"}

    @mcp.tool()
    def board_create(
        title: str,
        criteria: str,
        description: str = "",
        parent: Optional[int] = None,
        case: str = "",
    ) -> Any:
        """File a new work item (open, unassigned — work starts when it is
        assigned or claimed). `criteria` is the acceptance criteria — what gets
        verified before the item can be done; required."""
        return _safe(
            dialect.create_item,
            space,
            title=title,
            criteria=criteria,
            description=description,
            parent=parent,
            case=case or None,
        )

    @mcp.tool()
    def board_claim(item: int) -> Any:
        """Claim an open, unassigned item for yourself. First claim wins; the
        item becomes your assignment. Only claim work you can start on now."""
        return _safe(dialect.claim, space, item)

    @mcp.tool()
    def board_move(item: int, to: str, comment: str = "", refs: list[str] = []) -> Any:
        """Move a work item: in_progress when you start, blocked with the blocker
        as `comment`, review with a hand-off comment and artifact refs (branch,
        PR, file:line) when finished."""
        return _safe(
            dialect.transition, space, item, to, comment=comment, refs=list(refs or [])
        )

    @mcp.tool()
    def board_comment(item: int, body: str, refs: list[str] = [], needs_attention: bool = False) -> Any:
        """Comment on a work item — durable and attributed; answers that matter
        belong here. Routine notes are quiet; needs_attention=True wakes the lead
        for an explicit question/decision. Review transitions are the handoff.
        `refs` attach artifact pointers."""
        return _safe(dialect.comment, space, item, body, refs=list(refs or []), needs_attention=needs_attention)

    @mcp.tool()
    def board_attach(item: int, path: str, caption: str = "") -> Any:
        """Attach a screenshot or image (png/jpg/gif/webp, ≤10MB) from a local
        file to a work item — so the lead/reviewer can SEE what you did. Give it
        a caption saying what the image shows. Great with review hand-offs."""
        from .attachments import read_image_file

        try:
            data, name = read_image_file(path)
        except (BoardError, ValueError, OSError) as error:
            return {"error": str(error)}
        return _safe(
            dialect.attach,
            space,
            item,
            data,
            name,
            caption=caption,
        )

    @mcp.tool()
    def board_pending() -> Any:
        """Your unconsumed feed: every event on items assigned to you or filed
        by you — assignments, send-backs with feedback, comments from the lead
        or user, cancellations. Check at the start of a work session and before
        finishing; acknowledge with board_consume."""
        return _safe(dialect.pending, space)

    @mcp.tool()
    def board_consume(upto_seq: int) -> Any:
        """Acknowledge feed events up to a sequence number (from board_pending),
        so they are not re-delivered."""
        return _safe(lambda: (dialect.consume(space, upto_seq), {"ok": True})[1])

    if role == "worker":

        @mcp.tool()
        def board_set_status(item: int, text: str) -> Any:
            """Set one display-only progress line (at most 80 characters) on an item
            currently assigned to you. Does not change state or wake the lead."""
            return _safe(dialect.set_status, space, item, text)

    if role in ("lead", "user"):

        @mcp.tool()
        def board_assign(item: int, assignee: str) -> Any:
            """Assign a work item to a worker (or to yourself to reserve it)."""
            return _safe(dialect.assign, space, item, assignee)

        @mcp.tool()
        def board_link(src: int, kind: str, dst: int) -> Any:
            """Link two items: `parent` (dst becomes src's parent) or `blocks`
            (src blocks dst)."""
            return _safe(dialect.link, space, src, kind, dst)

        @mcp.tool()
        def board_policy(claims: str = "") -> Any:
            """Show the board's claim policy, or set it: `open` (workers may
            self-claim open items) or `lead-only`."""
            if claims:
                return _safe(dialect.set_policy, space, claims=claims)
            return _safe(dialect.policy, space)

    @mcp.tool()
    def journal_append(
        case: str,
        body: str,
        kind: str = "note",
        item: Optional[int] = None,
        entities: list[str] = [],
        refs: list[str] = [],
    ) -> Any:
        """Append to a journal case as you work: kind is finding, evidence,
        decision, note, or raw (a capture excerpt referencing a file).
        `entities` are the concrete things it is about (paths, resources, ids)."""
        return _safe(
            dialect.journal_append,
            case,
            body,
            kind=kind,
            space=space,
            item=item,
            entities=list(entities or []),
            refs=list(refs or []),
        )

    @mcp.tool()
    def journal_read(
        case: str,
        item: Optional[int] = None,
        author: str = "",
        kind: str = "",
        entity: str = "",
        include_raw: bool = False,
        limit: int = 50,
    ) -> Any:
        """Read a journal case, filtered by item, author, entry kind, or entity.
        Prefer narrow reads; raw captures are skipped unless asked."""
        return _safe(
            dialect.journal_read,
            case,
            item=item,
            author=author or None,
            kind=kind or None,
            entity=entity or None,
            include_raw=include_raw,
            limit=limit,
        )

    @mcp.tool()
    def journal_cases() -> Any:
        """The journal cases you can read, with entry counts."""
        return _safe(dialect.journal_overview)

    return mcp


def serve(dialect, *, space: str) -> None:
    build(dialect, space=space).run("stdio")
