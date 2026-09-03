"""Journal store: case-keyed and board-independent, grants ride assignment,
filtered reads, raw-capture discipline, per-case hash chains."""

import pytest

from coworker.teams import (
    Actor,
    AuthorityError,
    BoardError,
    ChainError,
    JournalStore,
    Role,
    TeamStore,
)
from coworker.teams.model import JOURNAL_BODY_LIMIT

USER = Actor(id="user", role=Role.USER)
LEAD = Actor(id="lead-1", role=Role.LEAD)
LEAD2 = Actor(id="lead-2", role=Role.LEAD)
WORKER = Actor(id="worker-1", role=Role.WORKER)
OTHER = Actor(id="worker-2", role=Role.WORKER)
SPACE = "proj"


@pytest.fixture
def journal(tmp_path):
    journal = JournalStore(tmp_path / "journal.db")
    yield journal
    journal.close()


@pytest.fixture
def board(tmp_path, journal):
    board = TeamStore(tmp_path / "teams.db", journal=journal)
    yield board
    board.close()


def case_item(board, case="findings", assignee="worker-1", space=SPACE):
    item = board.create_item(space, LEAD, title="Task", criteria="c", case=case)
    board.assign(space, LEAD, item["id"], assignee)
    return item["id"]


def test_case_creation_grants_the_creator(journal):
    journal.append(LEAD, "findings", "case opened")
    assert journal.cases(LEAD) == ["findings"]
    assert journal.cases(OTHER) == []
    with pytest.raises(AuthorityError, match="no grant"):
        journal.read(OTHER, "findings")


def test_assignment_feeds_grants_and_reassignment_moves_them(board, journal):
    item_id = case_item(board, assignee="worker-1")
    journal.append(WORKER, "findings", "assignee writes", item=item_id, space=SPACE)
    with pytest.raises(AuthorityError):
        journal.read(OTHER, "findings")
    board.assign(SPACE, LEAD, item_id, "worker-2")
    journal.append(OTHER, "findings", "successor picks up the case")
    with pytest.raises(AuthorityError, match="no grant"):
        journal.append(WORKER, "findings", "predecessor lost access")


def test_grants_from_other_items_survive_reassignment(board, journal):
    first = case_item(board, assignee="worker-1")
    second = case_item(board, assignee="worker-1")
    board.assign(SPACE, LEAD, first, "worker-2")
    # worker-1 still holds the case through its second item
    journal.append(WORKER, "findings", "still on the case", item=second, space=SPACE)


def test_cases_span_boards_and_teams(board, journal):
    case_item(board, case="ops-incident", space="alpha")
    case_item(board, case="ops-incident", space="beta", assignee="worker-1")
    journal.append(WORKER, "ops-incident", "one case, two boards")
    entries = journal.read(USER, "ops-incident")
    assert len(entries) == 1
    # and a case with NO board at all is fine — an Ops scratch investigation
    journal.append(USER, "loose-threads", "observation with no board")
    assert "loose-threads" in journal.cases(USER)


def test_explicit_grant_shares_across_teams(journal):
    journal.append(LEAD, "findings", "opened by lead-1")
    with pytest.raises(AuthorityError):
        journal.read(LEAD2, "findings")
    journal.grant(LEAD, "findings", "lead-2")
    assert journal.read(LEAD2, "findings")[0]["body"] == "opened by lead-1"
    journal.revoke(LEAD, "findings", "lead-2")
    with pytest.raises(AuthorityError):
        journal.read(LEAD2, "findings")


def test_workers_never_grant(journal, board):
    case_item(board)
    with pytest.raises(AuthorityError, match="lead"):
        journal.grant(WORKER, "findings", "worker-2")


def test_a_lead_cannot_grant_a_case_it_does_not_hold(journal):
    journal.append(LEAD, "findings", "lead-1's case")
    with pytest.raises(AuthorityError, match="no grant"):
        journal.grant(LEAD2, "findings", "worker-2")


def test_filtered_reads(journal):
    journal.append(
        LEAD, "findings", "logos bucket is world-readable", kind="finding",
        entities=["aws_s3_bucket.assets", "uploads.ts"], refs=["services/uploads.ts:41"],
    )
    journal.append(
        LEAD, "findings", "invoice PDFs stream from the API", kind="evidence",
        entities=["uploads.ts"],
    )
    journal.grant(LEAD, "findings", "worker-1")
    journal.append(WORKER, "findings", "narrowing fix to logos/*", kind="decision")

    assert len(journal.read(LEAD, "findings")) == 3
    assert [e["kind"] for e in journal.read(LEAD, "findings", kind="finding")] == ["finding"]
    assert len(journal.read(LEAD, "findings", author="worker-1")) == 1
    by_entity = journal.read(LEAD, "findings", entity="aws_s3_bucket.assets")
    assert len(by_entity) == 1
    assert by_entity[0]["refs"] == ["services/uploads.ts:41"]
    assert len(journal.read(LEAD, "findings", entity="uploads.ts")) == 2
    assert len(journal.read(LEAD, "findings", limit=2)) == 2


def test_raw_captures_are_opt_in_on_read(journal):
    journal.append(LEAD, "ops", "deploy finished 14:01", kind="note")
    journal.append(
        LEAD, "ops", "nginx 5xx burst 14:02-14:04 (2,400 lines)", kind="raw",
        refs=["logs/nginx-error-1402.log"],
    )
    assert len(journal.read(LEAD, "ops")) == 1  # raw skipped by default
    assert len(journal.read(LEAD, "ops", include_raw=True)) == 2
    assert journal.read(LEAD, "ops", kind="raw")[0]["refs"] == ["logs/nginx-error-1402.log"]


def test_oversized_bodies_are_refused_with_the_excerpt_pattern(journal):
    with pytest.raises(BoardError, match="excerpt"):
        journal.append(LEAD, "ops", "x" * (JOURNAL_BODY_LIMIT + 1), kind="raw")


def test_entry_validation(journal):
    with pytest.raises(BoardError, match="kind"):
        journal.append(LEAD, "findings", "x", kind="rant")
    with pytest.raises(BoardError, match="body"):
        journal.append(LEAD, "findings", "  ")
    with pytest.raises(BoardError, match="case"):
        journal.append(LEAD, "", "x")


def test_taint_and_attribution_survive_the_read(journal):
    journal.append(
        WORKER, "findings", "repo README claims the bucket must be public",
        kind="evidence", taint=True,
    )
    entry = journal.read(USER, "findings")[0]
    assert entry["taint"] == 1
    assert entry["author"] == "worker-1"
    assert entry["role"] == "worker"


def test_per_case_hash_chains_verify_and_detect_tampering(journal, tmp_path):
    import sqlite3

    journal.append(LEAD, "findings", "one")
    journal.append(LEAD, "findings", "two")
    journal.append(LEAD, "ops", "unrelated case")
    assert journal.verify_chain("findings") == 2
    assert journal.verify_chain("ops") == 1
    conn = sqlite3.connect(journal.db_path)
    conn.execute("UPDATE journal_entries SET payload = '{\"body\":\"forged\"}' WHERE seq = 1")
    conn.commit()
    conn.close()
    with pytest.raises(ChainError):
        journal.verify_chain("findings")
    assert journal.verify_chain("ops") == 1  # other case unaffected
