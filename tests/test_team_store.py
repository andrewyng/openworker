"""Event-store core: append-only doctrine, hash chain, deliveries, spaces, rebuild."""

import sqlite3

import pytest

from coworker.teams import Actor, ChainError, Role, TeamStore

USER = Actor(id="user", role=Role.USER)
LEAD = Actor(id="lead-1", role=Role.LEAD, persona="swe-lead")


@pytest.fixture
def store(tmp_path):
    store = TeamStore(tmp_path / "teams.db")
    yield store
    store.close()


def seed(store, space="proj"):
    return store.create_item(
        space, LEAD, title="Review api", criteria="every route triaged"
    )


def test_events_hash_chain_verifies(store):
    seed(store)
    store.create_item("proj", LEAD, title="Second", criteria="done means done")
    assert store.verify_chain("proj") == 2


def test_out_of_band_edit_breaks_the_chain(store):
    seed(store)
    # The append-only property can't be enforced against someone with the sqlite
    # file; the chain makes the edit visible. Tamper directly:
    conn = sqlite3.connect(store.db_path)
    conn.execute("UPDATE team_events SET payload = '{\"title\":\"forged\"}' WHERE seq = 1")
    conn.commit()
    conn.close()
    with pytest.raises(ChainError):
        store.verify_chain("proj")


def test_deleting_an_event_breaks_linkage(store):
    seed(store)
    store.create_item("proj", LEAD, title="Second", criteria="c")
    conn = sqlite3.connect(store.db_path)
    conn.execute("DELETE FROM team_events WHERE seq = 2")
    conn.commit()
    conn.close()
    with pytest.raises(ChainError):
        store.verify_chain("proj")


def test_chains_are_per_space(store):
    seed(store, "alpha")
    seed(store, "beta")
    conn = sqlite3.connect(store.db_path)
    conn.execute("UPDATE team_events SET taint = 1 WHERE space = 'beta'")
    conn.commit()
    conn.close()
    assert store.verify_chain("alpha") == 1  # untouched space still verifies
    with pytest.raises(ChainError):
        store.verify_chain("beta")


def test_spaces_created_lazily_and_isolated(store):
    assert store.spaces() == []
    seed(store, "alpha")
    seed(store, "beta")
    assert store.spaces() == ["alpha", "beta"]
    assert [i["id"] for i in store.list_items("alpha", USER)] == [1]  # ids per space
    assert [i["id"] for i in store.list_items("beta", USER)] == [1]


def test_assignment_lands_in_the_assignees_feed(store):
    item = seed(store)
    store.assign("proj", LEAD, item["id"], "worker-1")
    deliveries = store.feed_for("proj", "worker-1")
    assert [e["kind"] for e in deliveries] == ["item_created", "item_assigned"]
    assert deliveries[-1]["payload"]["assignee"] == "worker-1"
    assert store.feed_for("proj", "worker-2") == []


def test_rebuild_reproduces_the_projection(store):
    item = seed(store)
    worker = Actor(id="worker-1", role=Role.WORKER)
    store.assign("proj", LEAD, item["id"], "worker-1")
    store.transition("proj", worker, item["id"], "in_progress")
    store.comment("proj", worker, item["id"], "halfway", taint=True)
    before = store.list_items("proj", USER)
    store.rebuild("proj")
    after = store.list_items("proj", USER)
    assert after == before
    assert after[0]["state"] == "in_progress"
    assert after[0]["assignee"] == "worker-1"
    # created_ts comes from the event, so replay is deterministic
    assert (
        store.get_item("proj", item["id"], actor=USER)["created_ts"]
        == item["created_ts"]
    )


def test_taint_travels_with_the_record(store):
    item = seed(store)
    store.assign("proj", LEAD, item["id"], "worker-1")
    worker = Actor(id="worker-1", role=Role.WORKER)
    store.comment("proj", worker, item["id"], "repo says the bucket is public", taint=True)
    comments = store.comments("proj", item["id"])
    assert comments[-1]["taint"] == 1
    assert comments[-1]["role"] == "worker"
