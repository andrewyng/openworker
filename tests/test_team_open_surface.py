"""OPE-100 — the board as an open surface: claim + policy knob, the BoardDialect
seam (local and remote), token-bound identity on `/v1/board`, and the MCP/CLI
front doors."""

import base64
import json

import pytest

from coworker.teams import Actor, AuthorityError, BoardError, JournalStore, Role, TeamStore
from coworker.teams.dialect import LocalDialect, RemoteDialect, local_dialect
from coworker.teams.tokens import BoardTokens

USER = Actor(id="user", role=Role.USER)
LEAD = Actor(id="lead-1", role=Role.LEAD, persona="swe-lead")
NIA = Actor(id="nia", role=Role.WORKER, persona="swe-worker")
WEBB = Actor(id="webb", role=Role.WORKER, persona="swe-worker")


@pytest.fixture
def store(tmp_path):
    journal = JournalStore(tmp_path / "journal.db")
    store = TeamStore(tmp_path / "teams.db", journal=journal)
    yield store
    store.close()
    journal.close()


def seed(store, space="proj", case=None):
    return store.create_item(
        space, LEAD, title="Build the API", criteria="routes pass tests", case=case
    )


# ------------------------------------------------------------------ claim verb


def test_claim_self_assigns_an_open_item(store):
    item = seed(store)
    claimed = store.claim("proj", NIA, item["id"])
    assert claimed["assignee"] == "nia"
    assert claimed["state"] == "open"  # claiming is not starting


def test_second_claim_loses_cleanly(store):
    item = seed(store)
    store.claim("proj", NIA, item["id"])
    with pytest.raises(BoardError, match="already claimed by nia"):
        store.claim("proj", WEBB, item["id"])


def test_claim_requires_open_and_unassigned(store):
    item = seed(store)
    store.assign("proj", LEAD, item["id"], "nia")
    # an assigned item is not claimable, even while still open
    with pytest.raises(BoardError, match="already claimed by nia"):
        store.claim("proj", WEBB, item["id"])
    store.transition("proj", NIA, item["id"], "in_progress")
    # and a non-open item never is
    with pytest.raises(BoardError, match="only open items"):
        store.claim("proj", WEBB, item["id"])


def test_lead_only_policy_blocks_worker_claims(store):
    item = seed(store)
    store.set_policy("proj", LEAD, claims="lead-only")
    with pytest.raises(AuthorityError, match="lead-only"):
        store.claim("proj", NIA, item["id"])
    # flipping back re-opens the queue
    store.set_policy("proj", USER, claims="open")
    assert store.claim("proj", NIA, item["id"])["assignee"] == "nia"


def test_policy_defaults_open_and_validates(store):
    assert store.policy("proj") == {"claims": "open"}
    with pytest.raises(BoardError):
        store.set_policy("proj", LEAD, claims="anarchy")
    with pytest.raises(AuthorityError):
        store.set_policy("proj", NIA, claims="lead-only")


def test_claim_feeds_the_lead_subscription(store):
    item = seed(store)
    store.claim("proj", NIA, item["id"])
    subs = store.subscribed_events("proj", "lead-1")
    claims = [e for e in subs if e["kind"] == "item_assigned"]
    assert len(claims) == 1
    assert claims[0]["actor"] == "nia"
    assert claims[0]["payload"]["claimed"] is True


def test_lead_assigns_are_not_subscription_news(store):
    item = seed(store)
    store.assign("proj", USER, item["id"], "nia")
    subs = store.subscribed_events("proj", "lead-1")
    assert not [e for e in subs if e["kind"] == "item_assigned"]


def test_claim_feeds_journal_grants_like_assignment(store):
    item = seed(store, case="case-alpha")
    store.claim("proj", NIA, item["id"])
    # nia can now read the case it was granted through the claim
    assert "case-alpha" in store.journal.cases(NIA)


def test_workers_see_the_claimable_pool(store):
    """A pull queue nobody can see is not a queue (drill-caught 2026-08-16):
    an unassigned worker must be able to DISCOVER open items to claim them."""
    item = seed(store)
    mine = store.create_item("proj", NIA, title="Mine", criteria="c")
    # WEBB sees every open unassigned item — including NIA's filing (claimable)
    assert {i["id"] for i in store.list_items("proj", WEBB)} == {
        item["id"],
        mine["id"],
    }
    # …but only while claims are open; under lead-only the slice rule stands
    store.set_policy("proj", LEAD, claims="lead-only")
    assert store.list_items("proj", WEBB) == []
    # own filings stay visible regardless of policy
    assert {i["id"] for i in store.list_items("proj", NIA)} == {mine["id"]}
    # a claimed item leaves the pool for everyone else
    store.set_policy("proj", LEAD, claims="open")
    store.claim("proj", NIA, item["id"])
    assert {i["id"] for i in store.list_items("proj", WEBB)} == {mine["id"]}


# ------------------------------------------------------------------ dialects


def test_local_dialect_binds_identity(tmp_path):
    dialect = local_dialect(tmp_path, actor="nia", role="worker")
    assert dialect.whoami() == {"actor": "nia", "role": "worker"}
    with pytest.raises(AuthorityError):
        dialect.assign("proj", 1, "webb")  # workers never assign


def test_local_dialect_full_worker_loop(tmp_path):
    lead = local_dialect(tmp_path, actor="lead-1", role="lead")
    item = lead.create_item(
        "proj", title="Build it", criteria="tests pass", case="case-b"
    )
    worker = LocalDialect(lead.store, lead.journal, NIA)
    claimed = worker.claim("proj", item["id"])
    assert claimed["assignee"] == "nia"
    worker.transition("proj", item["id"], "in_progress")
    worker.journal_append("case-b", "found the flaky fixture", kind="finding")
    worker.transition("proj", item["id"], "review", comment="branch ready")
    shown = worker.get_item("proj", item["id"])
    assert shown["state"] == "review"
    assert [e["body"] for e in worker.journal_read("case-b")] == [
        "found the flaky fixture"
    ]


# ------------------------------------------------------------- HTTP board API


@pytest.fixture
def api(tmp_path, monkeypatch):
    """The real FastAPI app over a real manager state dir, driven in-process."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COWORKER_API_TOKEN", "sidecar-secret")
    from coworker.permissions import Mode
    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    manager = SessionManager(
        workspace=None,
        data_dir=tmp_path / "state",
        model="openai:gpt-test",
        mode=Mode("interactive"),
    )
    from fastapi.testclient import TestClient

    app = create_app(manager)
    client = TestClient(app, base_url="http://board.test")
    yield client, manager, app
    client.close()


def _tokens(manager) -> BoardTokens:
    return manager.board_tokens


def test_board_api_requires_a_token(api):
    client, _, _ = api
    response = client.get("/v1/board/items", params={"space": "proj"})
    assert response.status_code == 401
    assert "board token" in response.json()["error"]


def test_board_api_rejects_the_sidecar_token_as_a_board_token(api):
    client, _, _ = api
    response = client.get(
        "/v1/board/whoami",
        headers={"Authorization": "Bearer sidecar-secret"},
    )
    assert response.status_code == 401


def test_token_binds_identity_and_store_enforces_authority(api):
    client, manager, app = api
    lead_token = _tokens(manager).mint("lead-1", "lead")
    nia_token = _tokens(manager).mint("nia", "worker")
    lead = {"Authorization": f"Bearer {lead_token}"}
    nia = {"Authorization": f"Bearer {nia_token}"}

    assert client.get("/v1/board/whoami", headers=nia).json() == {
        "actor": "nia",
        "role": "worker",
    }

    created = client.post(
        "/v1/board/items",
        headers=lead,
        json={"space": "proj", "title": "Build it", "criteria": "tests pass"},
    )
    assert created.status_code == 200
    item_id = created.json()["id"]

    # a worker token cannot assign — 403 from the store's authority check
    denied = client.post(
        "/v1/board/items/assign",
        headers=nia,
        json={"space": "proj", "id": item_id, "assignee": "nia"},
    )
    assert denied.status_code == 403

    # but it can claim, then work the item
    claimed = client.post(
        "/v1/board/items/claim", headers=nia, json={"space": "proj", "id": item_id}
    )
    assert claimed.status_code == 200
    assert claimed.json()["assignee"] == "nia"

    moved = client.post(
        "/v1/board/items/transition",
        headers=nia,
        json={"space": "proj", "id": item_id, "to": "in_progress"},
    )
    assert moved.status_code == 200

    # bad input is a 400 with the store's message, not a 500
    bad = client.post(
        "/v1/board/items/transition",
        headers=nia,
        json={"space": "proj", "id": item_id, "to": "done"},
    )
    assert bad.status_code == 403 or bad.status_code == 400


def test_board_item_reads_hide_foreign_worker_items(api):
    client, manager, _ = api
    lead_token = _tokens(manager).mint("lead-1", "lead")
    nia_token = _tokens(manager).mint("nia", "worker")
    webb_token = _tokens(manager).mint("webb", "worker")
    lead = {"Authorization": f"Bearer {lead_token}"}
    nia = {"Authorization": f"Bearer {nia_token}"}
    webb = {"Authorization": f"Bearer {webb_token}"}

    def create(title, *, description=""):
        response = client.post(
            "/v1/board/items",
            headers=lead,
            json={
                "space": "proj",
                "title": title,
                "description": description,
                "criteria": "done",
            },
        )
        assert response.status_code == 200
        return response.json()

    created = create(
        "Webb private investigation", description="credential rotation details"
    )
    assigned_response = client.post(
        "/v1/board/items/assign",
        headers=lead,
        json={"space": "proj", "id": created["id"], "assignee": "webb"},
    )
    assert assigned_response.status_code == 200

    mine = create("Nia task")
    assert client.post(
        "/v1/board/items/assign",
        headers=lead,
        json={"space": "proj", "id": mine["id"], "assignee": "nia"},
    ).status_code == 200
    claimable = create("Available")
    linked = create("Linked dependency")
    assert client.post(
        "/v1/board/link",
        headers=lead,
        json={
            "space": "proj",
            "src": linked["id"],
            "kind": "blocks",
            "dst": mine["id"],
        },
    ).status_code == 200

    denied = client.get(
        "/v1/board/item",
        headers=nia,
        params={"space": "proj", "id": created["id"]},
    )
    assert denied.status_code == 404
    assert "Webb private investigation" not in denied.text
    assert "credential rotation details" not in denied.text

    for item_id in (mine["id"], claimable["id"], linked["id"]):
        assert client.get(
            "/v1/board/item",
            headers=nia,
            params={"space": "proj", "id": item_id},
        ).status_code == 200

    assert client.post(
        "/v1/board/items/claim",
        headers=webb,
        json={"space": "proj", "id": claimable["id"]},
    ).status_code == 200
    assert client.get(
        "/v1/board/item",
        headers=nia,
        params={"space": "proj", "id": claimable["id"]},
    ).status_code == 404

    held = create("Held")
    assert client.post(
        "/v1/board/policy",
        headers=lead,
        json={"space": "proj", "claims": "lead-only"},
    ).status_code == 200
    assert client.get(
        "/v1/board/item",
        headers=nia,
        params={"space": "proj", "id": held["id"]},
    ).status_code == 404
    assert client.get(
        "/v1/board/item",
        headers=nia,
        params={"space": "proj", "id": mine["id"]},
    ).status_code == 200

    allowed = client.get(
        "/v1/board/item",
        headers=lead,
        params={"space": "proj", "id": created["id"]},
    )
    assert allowed.status_code == 200
    assert allowed.json()["title"] == "Webb private investigation"


def test_board_item_reads_return_not_found_for_missing_items(api):
    client, manager, _ = api
    nia_token = _tokens(manager).mint("nia", "worker")
    lead_token = _tokens(manager).mint("lead-1", "lead")
    user_token = _tokens(manager).mint("user", "user")
    nia = {"Authorization": f"Bearer {nia_token}"}
    lead = {"Authorization": f"Bearer {lead_token}"}
    user = {"Authorization": f"Bearer {user_token}"}

    for headers in (nia, lead, user):
        missing = client.get(
            "/v1/board/item",
            headers=headers,
            params={"space": "proj", "id": 999},
        )
        assert missing.status_code == 404


def test_board_item_create_hides_missing_and_foreign_parents(api):
    client, manager, _ = api
    lead_token = _tokens(manager).mint("lead-1", "lead")
    nia_token = _tokens(manager).mint("nia", "worker")
    lead = {"Authorization": f"Bearer {lead_token}"}
    nia = {"Authorization": f"Bearer {nia_token}"}

    hidden_response = client.post(
        "/v1/board/items",
        headers=lead,
        json={"space": "proj", "title": "Webb task", "criteria": "done"},
    )
    assert hidden_response.status_code == 200
    hidden = hidden_response.json()
    assert client.post(
        "/v1/board/items/assign",
        headers=lead,
        json={"space": "proj", "id": hidden["id"], "assignee": "webb"},
    ).status_code == 200
    before = manager.team_store.event_count("proj")

    for parent in (hidden["id"], 999):
        denied = client.post(
            "/v1/board/items",
            headers=nia,
            json={
                "space": "proj",
                "title": "Probe",
                "criteria": "must not be created",
                "parent": parent,
            },
        )
        assert denied.status_code == 404

    assert manager.team_store.event_count("proj") == before


def test_remote_dialect_round_trip(api):
    client, manager, app = api
    lead_token = _tokens(manager).mint("lead-1", "lead")
    nia_token = _tokens(manager).mint("nia", "worker")
    from fastapi.testclient import TestClient

    lead = RemoteDialect(
        "http://board.test",
        lead_token,
        client=TestClient(app, base_url="http://board.test"),
    )
    nia = RemoteDialect(
        "http://board.test",
        nia_token,
        client=TestClient(app, base_url="http://board.test"),
    )

    item = lead.create_item(
        "proj", title="Remote item", criteria="works over the wire", case="case-r"
    )
    assert lead.policy("proj") == {"claims": "open"}
    claimed = nia.claim("proj", item["id"])
    assert claimed["assignee"] == "nia"
    nia.transition("proj", item["id"], "in_progress")
    nia.journal_append("case-r", "wire finding", kind="finding", item=item["id"])
    nia.transition("proj", item["id"], "review", comment="ready", refs=["branch:x"])

    # the lead sees the review, verifies, and closes
    shown = lead.get_item("proj", item["id"])
    assert shown["state"] == "review"
    assert "branch:x" in shown["refs"]
    entries = lead.journal_read("case-r")
    assert entries[0]["body"] == "wire finding"
    done = lead.transition("proj", item["id"], "done")
    assert done["state"] == "done"

    # errors surface as BoardError with the server's message
    with pytest.raises(BoardError, match="only open items"):
        nia.claim("proj", item["id"])

    foreign = lead.create_item(
        "proj", title="Webb-only task", criteria="visible only to webb"
    )
    lead.assign("proj", foreign["id"], "webb")
    with pytest.raises(BoardError, match="no visible item"):
        nia.get_item("proj", foreign["id"])

    # policy flip over the wire blocks the next worker claim
    second = lead.create_item("proj", title="Held back", criteria="c")
    lead.set_policy("proj", claims="lead-only")
    with pytest.raises(BoardError, match="lead-only"):
        nia.claim("proj", second["id"])


def test_pending_and_consume_over_the_wire(api):
    client, manager, app = api
    lead_token = _tokens(manager).mint("lead-1", "lead")
    nia_token = _tokens(manager).mint("nia", "worker")
    from fastapi.testclient import TestClient

    lead = RemoteDialect(
        "http://board.test",
        lead_token,
        client=TestClient(app, base_url="http://board.test"),
    )
    nia = RemoteDialect(
        "http://board.test",
        nia_token,
        client=TestClient(app, base_url="http://board.test"),
    )
    item = lead.create_item("proj", title="Queued", criteria="c")
    lead.assign("proj", item["id"], "nia")
    events = nia.pending("proj")
    assert events and events[-1]["kind"] == "item_assigned"
    nia.consume("proj", events[-1]["seq"])
    assert nia.pending("proj") == []
    # a comment ANSWER arrives through the same feed — no addressing anywhere
    lead.comment("proj", item["id"], "start with the v2 endpoint")
    follow = nia.pending("proj")
    assert [e["kind"] for e in follow] == ["item_commented"]


# ------------------------------------------------------------------ attachments

PNG = b"\x89PNG\r\n\x1a\n" + b"drill-bytes"


def test_attachment_store_is_content_addressed(tmp_path):
    from coworker.teams.attachments import AttachmentStore, stored_name

    store = AttachmentStore(tmp_path / "attachments")
    ref = store.put(PNG, "shot.png")
    assert ref.startswith("attachment://") and ref.endswith("#shot.png")
    # identical bytes dedupe to the same file, whatever the filename says
    assert stored_name(store.put(PNG, "other.png")) == stored_name(ref)
    data = store.path_for(stored_name(ref)).read_bytes()
    assert data == PNG
    assert store.mime_for(stored_name(ref)) == "image/png"


def test_attachment_store_validates(tmp_path):
    from coworker.teams.attachments import AttachmentStore

    store = AttachmentStore(tmp_path / "attachments")
    with pytest.raises(BoardError, match="images only"):
        store.put(b"#!/bin/sh", "run.sh")
    with pytest.raises(BoardError, match="does not look like"):
        store.put(b"not a png at all", "fake.png")
    with pytest.raises(BoardError, match="not an attachment name"):
        store.path_for("../../etc/passwd")


def test_attach_over_the_wire_and_fetch(api):
    client, manager, app = api
    from fastapi.testclient import TestClient

    lead_token = _tokens(manager).mint("lead-1", "lead")
    nia_token = _tokens(manager).mint("nia", "worker")
    lead = RemoteDialect(
        "http://board.test",
        lead_token,
        client=TestClient(app, base_url="http://board.test"),
    )
    nia = RemoteDialect(
        "http://board.test",
        nia_token,
        client=TestClient(app, base_url="http://board.test"),
    )
    item = lead.create_item("proj", title="With screenshot", criteria="c")
    nia.claim("proj", item["id"])
    result = nia.attach(
        "proj", item["id"], PNG, "after.png", caption="statements page, dark mode"
    )
    assert result["ref"].startswith("attachment://")

    # the ref lands on the item as a comment with the caption
    shown = lead.get_item("proj", item["id"])
    assert result["ref"] in shown["refs"]
    assert shown["comments"][-1]["body"] == "statements page, dark mode"

    # and the lead can fetch the bytes back
    from coworker.teams.attachments import stored_name

    stored = stored_name(result["ref"])
    data, mime = lead.attachment("proj", stored)
    assert data == PNG and mime == "image/png"
    assert nia.attachment("proj", stored) == (PNG, "image/png")
    with pytest.raises(BoardError, match="attachment not found"):
        lead.attachment("another-space", stored)

    # a worker cannot attach to an item outside its slice
    other = lead.create_item("proj", title="Not nia's", criteria="c")
    lead.assign("proj", other["id"], "someone-else")
    with pytest.raises(BoardError, match="assigned items"):
        nia.attach("proj", other["id"], PNG, "sneaky.png")


def test_board_attachment_read_hides_foreign_worker_reference(api):
    client, manager, app = api
    from fastapi.testclient import TestClient
    from coworker.teams.attachments import stored_name

    lead_token = _tokens(manager).mint("lead-1", "lead")
    nia_token = _tokens(manager).mint("nia", "worker")
    lead = {"Authorization": f"Bearer {lead_token}"}
    nia = {"Authorization": f"Bearer {nia_token}"}

    item_response = client.post(
        "/v1/board/items",
        headers=lead,
        json={"space": "proj", "title": "Webb evidence", "criteria": "c"},
    )
    assert item_response.status_code == 200
    item = item_response.json()
    assert client.post(
        "/v1/board/items/assign",
        headers=lead,
        json={"space": "proj", "id": item["id"], "assignee": "webb"},
    ).status_code == 200
    attached = client.post(
        "/v1/board/items/attach",
        headers=lead,
        json={
            "space": "proj",
            "id": item["id"],
            "filename": "private.png",
            "data_b64": base64.b64encode(PNG).decode("ascii"),
        },
    )
    assert attached.status_code == 200

    stored = stored_name(attached.json()["ref"])
    denied = client.get(
        "/v1/board/attachment",
        headers=nia,
        params={"space": "proj", "name": stored},
    )

    assert denied.status_code == 404
    assert denied.json() == {"error": "attachment not found"}

    remote = RemoteDialect(
        "http://board.test",
        nia_token,
        client=TestClient(app, base_url="http://board.test"),
    )
    with pytest.raises(BoardError, match="attachment not found"):
        remote.attachment("proj", stored)
    remote.close()


def test_board_attachment_read_requires_token(api):
    client, _, _ = api

    response = client.get(
        "/v1/board/attachment",
        params={"space": "proj", "name": f"{'0' * 64}.png"},
    )

    assert response.status_code == 401
    assert "board token required" in response.json()["error"]


def test_board_attachment_read_rejects_malformed_name(api):
    client, manager, _ = api
    lead = {
        "Authorization": f"Bearer {_tokens(manager).mint('lead-1', 'lead')}"
    }

    response = client.get(
        "/v1/board/attachment",
        headers=lead,
        params={"space": "proj", "name": "../../private.png"},
    )

    assert response.status_code == 400
    assert "not an attachment name" in response.json()["error"]


def test_board_attachment_read_hides_unreferenced_blob(api):
    client, manager, _ = api
    from coworker.teams.attachments import stored_name

    lead = {
        "Authorization": f"Bearer {_tokens(manager).mint('lead-1', 'lead')}"
    }
    ref = manager.attachment_store.put(PNG, "orphan.png")

    response = client.get(
        "/v1/board/attachment",
        headers=lead,
        params={"space": "proj", "name": stored_name(ref)},
    )

    assert response.status_code == 404
    assert response.json() == {"error": "attachment not found"}


def test_board_attachment_read_hides_missing_referenced_blob(api):
    client, manager, _ = api
    from coworker.teams.attachments import stored_name

    lead = {
        "Authorization": f"Bearer {_tokens(manager).mint('lead-1', 'lead')}"
    }
    item = client.post(
        "/v1/board/items",
        headers=lead,
        json={"space": "proj", "title": "Evidence", "criteria": "c"},
    ).json()
    attached = client.post(
        "/v1/board/items/attach",
        headers=lead,
        json={
            "space": "proj",
            "id": item["id"],
            "filename": "missing.png",
            "data_b64": base64.b64encode(PNG).decode("ascii"),
        },
    ).json()
    stored = stored_name(attached["ref"])
    manager.attachment_store.path_for(stored).unlink()

    response = client.get(
        "/v1/board/attachment",
        headers=lead,
        params={"space": "proj", "name": stored},
    )

    assert response.status_code == 404
    assert response.json() == {"error": "attachment not found"}


def test_local_attachment_read_tracks_worker_visibility(tmp_path):
    from coworker.teams.attachments import stored_name

    lead = local_dialect(tmp_path, actor="lead-1", role="lead")
    worker = LocalDialect(
        lead.store, lead.journal, NIA, attachments=lead.attachments
    )
    claimable = lead.create_item("proj", title="Available", criteria="c")
    result = lead.attach("proj", claimable["id"], PNG, "available.png")
    stored = stored_name(result["payload"]["refs"][0])

    assert worker.attachment("proj", stored) == (PNG, "image/png")

    lead.set_policy("proj", claims="lead-only")
    with pytest.raises(BoardError, match="attachment not found"):
        worker.attachment("proj", stored)

    assigned = lead.create_item("proj", title="Assigned", criteria="c")
    lead.assign("proj", assigned["id"], "nia")
    assigned_ref = lead.attach(
        "proj", assigned["id"], PNG, "assigned.png"
    )["payload"]["refs"][0]
    assert worker.attachment("proj", stored_name(assigned_ref)) == (
        PNG,
        "image/png",
    )

    with pytest.raises(BoardError, match="attachment not found"):
        lead.attachment("another-space", stored)


def test_local_attachment_read_allows_a_visible_deduplicated_reference(tmp_path):
    from coworker.teams.attachments import stored_name

    lead = local_dialect(tmp_path, actor="lead-1", role="lead")
    worker = LocalDialect(
        lead.store, lead.journal, NIA, attachments=lead.attachments
    )
    foreign = lead.create_item("proj", title="Webb evidence", criteria="c")
    lead.assign("proj", foreign["id"], "webb")
    foreign_ref = lead.attach("proj", foreign["id"], PNG, "foreign.png")["payload"][
        "refs"
    ][0]
    with pytest.raises(BoardError, match="attachment not found"):
        worker.attachment("proj", stored_name(foreign_ref))

    mine = worker.create_item("proj", title="Nia evidence", criteria="c")
    own_ref = worker.attach("proj", mine["id"], PNG, "mine.png")["payload"]["refs"][
        0
    ]

    assert stored_name(own_ref) == stored_name(foreign_ref)
    assert worker.attachment("proj", stored_name(own_ref)) == (PNG, "image/png")

    lead.store.rebuild("proj")
    assert worker.attachment("proj", stored_name(own_ref)) == (PNG, "image/png")

    assert lead.store.rekey_space("proj", "moved") is True
    assert worker.attachment("moved", stored_name(own_ref)) == (PNG, "image/png")


def test_local_attachment_read_rejects_forged_comment_and_transition_refs(tmp_path):
    from coworker.teams.attachments import stored_name

    lead = local_dialect(tmp_path, actor="lead-1", role="lead")
    worker = LocalDialect(
        lead.store, lead.journal, NIA, attachments=lead.attachments
    )
    foreign = lead.create_item("proj", title="Webb evidence", criteria="c")
    lead.assign("proj", foreign["id"], "webb")
    comment_ref = lead.attach(
        "proj", foreign["id"], PNG, "foreign-comment.png"
    )["payload"]["refs"][0]
    transition_ref = lead.attach(
        "proj", foreign["id"], PNG + b"transition", "foreign-transition.png"
    )["payload"]["refs"][0]

    mine = lead.create_item("proj", title="Nia task", criteria="c")
    lead.assign("proj", mine["id"], "nia")
    worker.comment("proj", mine["id"], "found this", refs=[comment_ref])
    worker.transition(
        "proj",
        mine["id"],
        "in_progress",
        refs=[transition_ref],
    )

    with pytest.raises(BoardError, match="attachment not found"):
        worker.attachment("proj", stored_name(comment_ref))
    with pytest.raises(BoardError, match="attachment not found"):
        worker.attachment("proj", stored_name(transition_ref))


def test_legacy_attachment_refs_are_grandfathered_across_rebuild(tmp_path):
    import sqlite3

    from coworker.teams.attachments import AttachmentStore, stored_name

    db_path = tmp_path / "teams.db"
    attachments = AttachmentStore(tmp_path / "attachments")
    store = TeamStore(db_path)
    item = store.create_item("proj", LEAD, title="Legacy", criteria="c")
    ref = attachments.put(PNG, "legacy.png")
    # Before provenance markers, attach was an ordinary comment carrying a ref.
    store.comment("proj", LEAD, item["id"], "attached legacy.png", refs=[ref])
    store.close()
    with sqlite3.connect(db_path) as connection:
        # Simulate an old DB—or an interrupted upgrade whose projection table
        # exists but whose atomic migration marker was never committed.
        connection.execute(
            "DELETE FROM team_migrations WHERE name = 'attachment_refs_v1'"
        )

    reopened = TeamStore(db_path)
    lead = LocalDialect(reopened, None, LEAD, attachments=attachments)
    stored = stored_name(ref)

    assert lead.attachment("proj", stored) == (PNG, "image/png")
    later = reopened.create_item("proj", LEAD, title="Later", criteria="c")
    forged_ref = attachments.put(PNG + b"later", "later.png")
    reopened.comment(
        "proj", LEAD, later["id"], "ordinary ref", refs=[forged_ref]
    )
    reopened.close()

    reopened = TeamStore(db_path)
    lead = LocalDialect(reopened, None, LEAD, attachments=attachments)
    with pytest.raises(BoardError, match="attachment not found"):
        lead.attachment("proj", stored_name(forged_ref))
    reopened.rebuild("proj")
    assert lead.attachment("proj", stored) == (PNG, "image/png")
    assert reopened.rekey_space("proj", "moved") is True
    assert lead.attachment("moved", stored) == (PNG, "image/png")
    reopened.close()


def test_local_attachment_read_allows_a_directly_linked_item(tmp_path):
    from coworker.teams.attachments import stored_name

    lead = local_dialect(tmp_path, actor="lead-1", role="lead")
    worker = LocalDialect(
        lead.store, lead.journal, NIA, attachments=lead.attachments
    )
    mine = lead.create_item("proj", title="Nia task", criteria="c")
    lead.assign("proj", mine["id"], "nia")
    dependency = lead.create_item("proj", title="Dependency", criteria="c")
    lead.assign("proj", dependency["id"], "webb")
    ref = lead.attach("proj", dependency["id"], PNG, "dependency.png")["payload"][
        "refs"
    ][0]

    with pytest.raises(BoardError, match="attachment not found"):
        worker.attachment("proj", stored_name(ref))

    lead.link("proj", dependency["id"], "blocks", mine["id"])
    assert worker.attachment("proj", stored_name(ref)) == (PNG, "image/png")


def test_attach_rejects_bad_payloads_over_the_wire(api):
    client, manager, app = api
    lead_token = _tokens(manager).mint("lead-1", "lead")
    headers = {"Authorization": f"Bearer {lead_token}"}
    client.post(
        "/v1/board/items",
        headers=headers,
        json={"space": "proj", "title": "T", "criteria": "c"},
    )
    bad = client.post(
        "/v1/board/items/attach",
        headers=headers,
        json={"space": "proj", "id": 1, "filename": "x.png", "data_b64": "!!!"},
    )
    assert bad.status_code == 400
    assert "base64" in bad.json()["error"]


# ------------------------------------------------------------------ tokens


def test_tokens_are_hash_stored_and_revocable(tmp_path):
    tokens = BoardTokens(tmp_path / "board-tokens.json")
    token = tokens.mint("nia", "worker", label="laptop")
    # plaintext never touches disk
    assert token not in (tmp_path / "board-tokens.json").read_text()
    actor = tokens.resolve(token)
    assert (actor.id, actor.role) == ("nia", Role.WORKER)
    assert tokens.resolve("owb_forged") is None
    assert tokens.revoke(token[:12]) == 1
    assert tokens.resolve(token) is None


def test_token_mint_validates_role(tmp_path):
    tokens = BoardTokens(tmp_path / "board-tokens.json")
    with pytest.raises(ValueError):
        tokens.mint("nia", "admin")


# ------------------------------------------------------------------ MCP server


def test_mcp_tool_surface_is_role_scoped(tmp_path):
    import anyio

    from coworker.teams.mcp_server import build

    worker = build(
        local_dialect(tmp_path, actor="nia", role="worker"), space="proj"
    )
    lead = build(
        local_dialect(tmp_path, actor="lead-1", role="lead"), space="proj"
    )

    def names(server):
        return {tool.name for tool in anyio.run(server.list_tools)}

    worker_names = names(worker)
    lead_names = names(lead)
    assert "board_claim" in worker_names
    assert "board_attach" in worker_names
    assert "board_assign" not in worker_names
    assert "board_policy" not in worker_names
    assert {"board_assign", "board_link", "board_policy"} <= lead_names
    assert "journal_append" in worker_names


def test_mcp_worker_loop_through_call_tool(tmp_path):
    import anyio

    from coworker.teams.mcp_server import build

    lead_dialect = local_dialect(tmp_path, actor="lead-1", role="lead")
    item = lead_dialect.create_item("proj", title="Via MCP", criteria="c")
    worker = build(
        LocalDialect(lead_dialect.store, lead_dialect.journal, NIA), space="proj"
    )

    def call(name, arguments):
        return anyio.run(lambda: worker.call_tool(name, arguments))

    call("board_claim", {"item": item["id"]})
    call("board_move", {"item": item["id"], "to": "in_progress"})
    payload = json.loads(call("board_show", {"item": item["id"]})[0].text)
    assert (payload["id"], payload["assignee"]) == (item["id"], "nia")
    shown = lead_dialect.get_item("proj", item["id"])
    assert (shown["assignee"], shown["state"]) == ("nia", "in_progress")


def test_mcp_board_show_does_not_return_a_foreign_item(tmp_path):
    import anyio

    from coworker.teams.mcp_server import build

    lead = local_dialect(tmp_path, actor="lead-1", role="lead")
    foreign = lead.create_item(
        "proj", title="Private Webb task", criteria="not visible to nia"
    )
    lead.assign("proj", foreign["id"], "webb")
    worker = build(LocalDialect(lead.store, lead.journal, NIA), space="proj")

    result = anyio.run(
        lambda: worker.call_tool("board_show", {"item": foreign["id"]})
    )
    payload = json.loads(result[0].text)

    assert "error" in payload
    assert "Private Webb task" not in payload["error"]


# ------------------------------------------------------------------ CLI


def test_cli_headless_flow(tmp_path, capsys):
    from coworker.teams.cli import main

    space_args = ["--db", str(tmp_path), "--space", "proj"]
    assert main(
        ["board", "create", "CLI item", "--criteria", "prints", *space_args,
         "--actor", "lead-1", "--role", "lead"]
    ) == 0
    capsys.readouterr()
    assert main(
        ["board", "claim", "1", *space_args, "--actor", "nia", "--role", "worker"]
    ) == 0
    assert "claimed #1" in capsys.readouterr().out
    assert main(
        ["board", "show", "1", *space_args, "--actor", "nia", "--role", "worker"]
    ) == 0
    assert "CLI item" in capsys.readouterr().out
    assert main(["board", "list", *space_args, "--json"]) == 0
    items = json.loads(capsys.readouterr().out)
    assert [(i["id"], i["assignee"]) for i in items] == [(1, "nia")]
    # a losing claim exits 1 with the store's message on stderr
    assert main(
        ["board", "claim", "1", *space_args, "--actor", "webb", "--role", "worker"]
    ) == 1
    assert "already claimed by nia" in capsys.readouterr().err
    # policy knob round-trips
    assert main(["board", "policy", "--claims", "lead-only", *space_args]) == 0
    assert "lead-only" in capsys.readouterr().out
    # journal append + read
    assert main(
        ["journal", "append", "case-cli", "found it", "--kind", "finding",
         *space_args]
    ) == 0
    capsys.readouterr()
    assert main(["journal", "read", "case-cli", *space_args]) == 0
    assert "found it" in capsys.readouterr().out


def test_cli_worker_cannot_show_a_foreign_item(tmp_path, capsys):
    from coworker.teams.cli import main

    space_args = ["--db", str(tmp_path), "--space", "proj"]
    lead_args = [*space_args, "--actor", "lead-1", "--role", "lead"]
    worker_args = [*space_args, "--actor", "nia", "--role", "worker"]

    assert main(
        ["board", "create", "Private Webb task", "--criteria", "c", *lead_args]
    ) == 0
    capsys.readouterr()
    assert main(["board", "assign", "1", "webb", *lead_args]) == 0
    capsys.readouterr()

    assert main(["board", "show", "1", *worker_args]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "no visible item" in output.err
    assert "Private Webb task" not in output.err


def test_cli_worker_cannot_download_a_foreign_attachment(tmp_path, capsys):
    from coworker.teams.attachments import stored_name
    from coworker.teams.cli import main

    lead = local_dialect(tmp_path, actor="lead-1", role="lead")
    foreign = lead.create_item("proj", title="Private Webb task", criteria="c")
    lead.assign("proj", foreign["id"], "webb")
    ref = lead.attach("proj", foreign["id"], PNG, "private.png")["payload"]["refs"][
        0
    ]
    output = tmp_path / "stolen.png"

    result = main(
        [
            "board",
            "attachment",
            stored_name(ref),
            "--out",
            str(output),
            "--db",
            str(tmp_path),
            "--space",
            "proj",
            "--actor",
            "nia",
            "--role",
            "worker",
        ]
    )

    assert result == 1
    assert not output.exists()
    assert "attachment not found" in capsys.readouterr().err


def test_cli_authorized_attachment_download(tmp_path, capsys):
    from coworker.teams.attachments import stored_name
    from coworker.teams.cli import main

    lead = local_dialect(tmp_path, actor="lead-1", role="lead")
    item = lead.create_item("proj", title="Evidence", criteria="c")
    ref = lead.attach("proj", item["id"], PNG, "evidence.png")["payload"]["refs"][
        0
    ]
    output = tmp_path / "downloaded.png"

    result = main(
        [
            "board",
            "attachment",
            stored_name(ref),
            "--out",
            str(output),
            "--db",
            str(tmp_path),
            "--space",
            "proj",
            "--actor",
            "lead-1",
            "--role",
            "lead",
        ]
    )

    assert result == 0
    assert output.read_bytes() == PNG
    assert str(output) in capsys.readouterr().out


def test_cli_token_mint_and_list(tmp_path, capsys):
    from coworker.teams.cli import main

    assert main(
        ["board", "token", "mint", "--actor", "nia", "--role", "worker",
         "--label", "laptop", "--db", str(tmp_path)]
    ) == 0
    token = capsys.readouterr().out.strip()
    assert token.startswith("owb_")
    assert main(["board", "token", "list", "--db", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "nia" in out and "laptop" in out and token not in out
