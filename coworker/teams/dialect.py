"""The BoardDialect seam — where "a board" stops meaning "our SQLite file".

A dialect is where the board of record LIVES, seen from a client's chair:
- LocalDialect: this machine's TeamStore/JournalStore, direct SQLite. For the
  standalone/headless case where the caller is the only writer.
- RemoteDialect: one wire protocol (the `/v1/board` HTTP API) to a board served
  elsewhere — the running OpenWorker sidecar on this machine, a teammate's machine,
  or a hosted board service later. Identity rides the token; the server binds it to
  an actor+role and the store enforces authority, so a remote client is safe by
  construction.

External trackers (Jira/Linear) are deliberately NOT dialects: making a pre-LLM
tracker the board of record means contorting our state machine and delivery cursors
onto its API. They join as MIRRORS instead — one more subscriber with a cursor over
the append-only event log, replaying events outward (decided 2026-08-16). The board
stays the abstraction and the source of truth.

Every front door — the `team-board` MCP server, the `ocw` CLI, remote OpenWorker
instances — bottoms out in this one verb surface. Dialect instances are
identity-bound: one actor per instance, matching the one-identity-per-process shape
of an external harness.

Cross-process write safety: the store's hash-chain append is read-head-then-write
under an in-process lock, so two processes must never write one SQLite file
directly. Rule: when a server is up, clients go remote; LocalDialect is for the
headless case where this process is the only writer.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from .journal import JournalStore
from .model import Actor, BoardError, Role
from .store import TeamStore


class BoardDialect(Protocol):
    """The verb surface a board client sees, identity already bound."""

    def whoami(self) -> dict[str, Any]: ...
    def spaces(self) -> list[str]: ...
    def list_items(
        self,
        space: str,
        *,
        state: Optional[str] = None,
        assignee: Optional[str] = None,
    ) -> list[dict[str, Any]]: ...
    def get_item(self, space: str, item_id: int) -> dict[str, Any]: ...
    def create_item(
        self,
        space: str,
        *,
        title: str,
        criteria: str,
        description: str = "",
        parent: Optional[int] = None,
        case: Optional[str] = None,
    ) -> dict[str, Any]: ...
    def transition(
        self,
        space: str,
        item_id: int,
        to: str,
        *,
        comment: str = "",
        refs: Optional[list[str]] = None,
    ) -> dict[str, Any]: ...
    def comment(
        self,
        space: str,
        item_id: int,
        body: str,
        *,
        refs: Optional[list[str]] = None,
    ) -> dict[str, Any]: ...
    def assign(self, space: str, item_id: int, assignee: str) -> dict[str, Any]: ...
    def claim(self, space: str, item_id: int) -> dict[str, Any]: ...
    def link(self, space: str, src: int, kind: str, dst: int) -> dict[str, Any]: ...
    def attach(
        self,
        space: str,
        item_id: int,
        data: bytes,
        filename: str,
        *,
        caption: str = "",
    ) -> dict[str, Any]: ...
    def attachment(self, space: str, stored: str) -> tuple[bytes, str]:
        """Read a blob referenced by an actor-visible item in ``space``."""
        ...
    def policy(self, space: str) -> dict[str, Any]: ...
    def set_policy(self, space: str, *, claims: str) -> dict[str, Any]: ...
    def pending(self, space: str, *, limit: int = 200) -> list[dict[str, Any]]: ...
    def consume(self, space: str, upto_seq: int) -> None: ...
    def journal_append(
        self,
        case: str,
        body: str,
        *,
        kind: str = "note",
        space: Optional[str] = None,
        item: Optional[int] = None,
        entities: Optional[list[str]] = None,
        refs: Optional[list[str]] = None,
    ) -> dict[str, Any]: ...
    def journal_read(
        self,
        case: str,
        *,
        item: Optional[int] = None,
        author: Optional[str] = None,
        kind: Optional[str] = None,
        entity: Optional[str] = None,
        include_raw: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...
    def journal_overview(self) -> list[dict[str, Any]]: ...


class LocalDialect:
    """Direct store access, one bound identity. The headless/standalone backing."""

    def __init__(
        self,
        store: TeamStore,
        journal: Optional[JournalStore],
        actor: Actor,
        *,
        attachments: Any = None,
    ) -> None:
        self.store = store
        self.journal = journal
        self.actor = actor
        self.attachments = attachments

    def whoami(self) -> dict[str, Any]:
        return {"actor": self.actor.id, "role": self.actor.role.value}

    def spaces(self) -> list[str]:
        return self.store.spaces()

    def list_items(
        self,
        space: str,
        *,
        state: Optional[str] = None,
        assignee: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return self.store.list_items(space, self.actor, state=state, assignee=assignee)

    def get_item(self, space: str, item_id: int) -> dict[str, Any]:
        return self.store.get_item(space, item_id, actor=self.actor)

    def create_item(
        self,
        space: str,
        *,
        title: str,
        criteria: str,
        description: str = "",
        parent: Optional[int] = None,
        case: Optional[str] = None,
    ) -> dict[str, Any]:
        return self.store.create_item(
            space,
            self.actor,
            title=title,
            criteria=criteria,
            description=description,
            parent=parent,
            case=case,
        )

    def transition(
        self,
        space: str,
        item_id: int,
        to: str,
        *,
        comment: str = "",
        refs: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        return self.store.transition(
            space, self.actor, item_id, to, comment=comment, refs=refs
        )

    def comment(
        self,
        space: str,
        item_id: int,
        body: str,
        *,
        refs: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        return self.store.comment(space, self.actor, item_id, body, refs=refs)

    def assign(self, space: str, item_id: int, assignee: str) -> dict[str, Any]:
        return self.store.assign(space, self.actor, item_id, assignee)

    def claim(self, space: str, item_id: int) -> dict[str, Any]:
        return self.store.claim(space, self.actor, item_id)

    def link(self, space: str, src: int, kind: str, dst: int) -> dict[str, Any]:
        return self.store.link(space, self.actor, src, kind, dst)

    def attach(
        self,
        space: str,
        item_id: int,
        data: bytes,
        filename: str,
        *,
        caption: str = "",
    ) -> dict[str, Any]:
        # Attach = store blob + an attributed attachment-comment event. Comment
        # authority IS attach authority (workers attach on their slice only).
        if self.attachments is None:
            raise BoardError("no attachment store is attached to this board")
        ref = self.attachments.put(data, filename)
        return self.store.attach_ref(
            space,
            self.actor,
            item_id,
            caption or f"attached {filename}",
            ref,
        )

    def attachment(self, space: str, stored: str) -> tuple[bytes, str]:
        if self.attachments is None:
            raise BoardError("no attachment store is attached to this board")
        self.store.require_attachment_access(space, self.actor, stored)
        path = self.attachments.path_for(stored)
        return path.read_bytes(), self.attachments.mime_for(stored)

    def policy(self, space: str) -> dict[str, Any]:
        return self.store.policy(space)

    def set_policy(self, space: str, *, claims: str) -> dict[str, Any]:
        return self.store.set_policy(space, self.actor, claims=claims)

    def pending(self, space: str, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.feed_for(space, self.actor.id, limit=limit)

    def consume(self, space: str, upto_seq: int) -> None:
        self.store.consume_feed(space, self.actor.id, int(upto_seq))

    def journal_append(
        self,
        case: str,
        body: str,
        *,
        kind: str = "note",
        space: Optional[str] = None,
        item: Optional[int] = None,
        entities: Optional[list[str]] = None,
        refs: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        self._need_journal()
        return self.journal.append(
            self.actor,
            case,
            body,
            kind=kind,
            space=space,
            item=item,
            entities=entities,
            refs=refs,
        )

    def journal_read(
        self,
        case: str,
        *,
        item: Optional[int] = None,
        author: Optional[str] = None,
        kind: Optional[str] = None,
        entity: Optional[str] = None,
        include_raw: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._need_journal()
        return self.journal.read(
            self.actor,
            case,
            item=item,
            author=author,
            kind=kind,
            entity=entity,
            include_raw=include_raw,
            limit=limit,
        )

    def journal_overview(self) -> list[dict[str, Any]]:
        self._need_journal()
        return self.journal.overview(self.actor)

    def _need_journal(self) -> None:
        if self.journal is None:
            raise BoardError("no journal store is attached to this board")


class RemoteDialect:
    """The `/v1/board` HTTP client. `base_url` is an OpenWorker sidecar or a hosted
    board service; the Bearer token carries identity — the server resolves it to an
    actor+role, so this client never states who it is, it proves it."""

    def __init__(
        self, base_url: str, token: str, *, client: Any = None, timeout: float = 30.0
    ) -> None:
        import httpx

        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
        )
        self._client.headers["Authorization"] = f"Bearer {token}"

    # -- plumbing --------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        response = self._client.get(
            path, params={k: v for k, v in (params or {}).items() if v is not None}
        )
        return self._unwrap(response)

    def _post(self, path: str, body: dict) -> Any:
        response = self._client.post(
            path, json={k: v for k, v in body.items() if v is not None}
        )
        return self._unwrap(response)

    @staticmethod
    def _unwrap(response: Any) -> Any:
        if response.status_code == 401:
            raise BoardError("board token was not accepted (401) — mint one with"
                             " `ocw board token` on the serving machine")
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            raise BoardError(
                str(data.get("error") or data.get("detail") or response.text)
            )
        return data

    # -- verbs -----------------------------------------------------------------

    def whoami(self) -> dict[str, Any]:
        return self._get("/v1/board/whoami")

    def spaces(self) -> list[str]:
        return self._get("/v1/board/spaces")["spaces"]

    def list_items(
        self,
        space: str,
        *,
        state: Optional[str] = None,
        assignee: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return self._get(
            "/v1/board/items",
            {"space": space, "state": state, "assignee": assignee},
        )["items"]

    def get_item(self, space: str, item_id: int) -> dict[str, Any]:
        return self._get("/v1/board/item", {"space": space, "id": item_id})

    def create_item(
        self,
        space: str,
        *,
        title: str,
        criteria: str,
        description: str = "",
        parent: Optional[int] = None,
        case: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._post(
            "/v1/board/items",
            {
                "space": space,
                "title": title,
                "criteria": criteria,
                "description": description,
                "parent": parent,
                "case": case,
            },
        )

    def transition(
        self,
        space: str,
        item_id: int,
        to: str,
        *,
        comment: str = "",
        refs: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        return self._post(
            "/v1/board/items/transition",
            {
                "space": space,
                "id": item_id,
                "to": to,
                "comment": comment,
                "refs": refs or [],
            },
        )

    def comment(
        self,
        space: str,
        item_id: int,
        body: str,
        *,
        refs: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        return self._post(
            "/v1/board/items/comment",
            {"space": space, "id": item_id, "body": body, "refs": refs or []},
        )

    def assign(self, space: str, item_id: int, assignee: str) -> dict[str, Any]:
        return self._post(
            "/v1/board/items/assign",
            {"space": space, "id": item_id, "assignee": assignee},
        )

    def claim(self, space: str, item_id: int) -> dict[str, Any]:
        return self._post("/v1/board/items/claim", {"space": space, "id": item_id})

    def link(self, space: str, src: int, kind: str, dst: int) -> dict[str, Any]:
        return self._post(
            "/v1/board/link", {"space": space, "src": src, "kind": kind, "dst": dst}
        )

    def attach(
        self,
        space: str,
        item_id: int,
        data: bytes,
        filename: str,
        *,
        caption: str = "",
    ) -> dict[str, Any]:
        import base64

        return self._post(
            "/v1/board/items/attach",
            {
                "space": space,
                "id": item_id,
                "filename": filename,
                "caption": caption,
                "data_b64": base64.b64encode(data).decode("ascii"),
            },
        )

    def attachment(self, space: str, stored: str) -> tuple[bytes, str]:
        response = self._client.get(
            "/v1/board/attachment", params={"space": space, "name": stored}
        )
        if response.status_code >= 400:
            self._unwrap(response)  # raises with the server's message
        return response.content, response.headers.get(
            "content-type", "application/octet-stream"
        )

    def policy(self, space: str) -> dict[str, Any]:
        return self._get("/v1/board/policy", {"space": space})

    def set_policy(self, space: str, *, claims: str) -> dict[str, Any]:
        return self._post("/v1/board/policy", {"space": space, "claims": claims})

    def pending(self, space: str, *, limit: int = 200) -> list[dict[str, Any]]:
        return self._get("/v1/board/pending", {"space": space, "limit": limit})[
            "events"
        ]

    def consume(self, space: str, upto_seq: int) -> None:
        self._post("/v1/board/consume", {"space": space, "upto_seq": int(upto_seq)})

    def journal_append(
        self,
        case: str,
        body: str,
        *,
        kind: str = "note",
        space: Optional[str] = None,
        item: Optional[int] = None,
        entities: Optional[list[str]] = None,
        refs: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        return self._post(
            "/v1/board/journal",
            {
                "case": case,
                "body": body,
                "kind": kind,
                "space": space,
                "item": item,
                "entities": entities or [],
                "refs": refs or [],
            },
        )

    def journal_read(
        self,
        case: str,
        *,
        item: Optional[int] = None,
        author: Optional[str] = None,
        kind: Optional[str] = None,
        entity: Optional[str] = None,
        include_raw: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self._get(
            "/v1/board/journal",
            {
                "case": case,
                "item": item,
                "author": author,
                "kind": kind,
                "entity": entity,
                "include_raw": "1" if include_raw else None,
                "limit": limit,
            },
        )["entries"]

    def journal_overview(self) -> list[dict[str, Any]]:
        return self._get("/v1/board/journal/cases")["cases"]

    def close(self) -> None:
        self._client.close()


def local_dialect(
    db_dir, *, actor: str = "user", role: str = "user"
) -> LocalDialect:
    """Open the state dir's stores directly as one bound identity — the headless
    backing for the CLI and MCP server when no OpenWorker server is running."""
    from pathlib import Path

    from .attachments import AttachmentStore

    base = Path(db_dir).expanduser()
    journal = JournalStore(base / "journal.db")
    store = TeamStore(base / "teams.db", journal=journal)
    return LocalDialect(
        store,
        journal,
        Actor(id=actor, role=Role(role)),
        attachments=AttachmentStore(base / "attachments"),
    )
