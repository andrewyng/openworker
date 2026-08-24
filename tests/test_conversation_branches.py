from coworker.conversations import ConversationStore
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app
from coworker.sessions import SessionRecord


class _Provider(ProviderClient):
    def complete(self, **kwargs):
        return AssistantTurn(text="ok")

    def capabilities(self, model):
        return ModelCapabilities()


def _record(session_id: str = "root") -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        workspace="",
        model="test-model",
        mode="ask",
        agent="cowork",
        messages=[
            {"role": "user", "content": "first", "ts": 10.0},
            {"role": "assistant", "content": "first answer", "ts": 11.0},
            {"role": "user", "content": "second", "ts": 12.0},
            {"role": "assistant", "content": "second answer", "ts": 13.0},
        ],
    )


def test_branch_keeps_original_and_clones_only_prefix(tmp_path):
    store = ConversationStore(tmp_path)
    store.save(_record())

    result = store.create_branch("root", user_ordinal=1, ts=12.0)

    assert result["ok"]
    child = store.load(result["session_id"])
    assert child is not None
    assert [m["content"] for m in child.messages] == ["first", "first answer"]
    assert [m["content"] for m in store.load("root").messages] == [
        "first", "first answer", "second", "second answer"
    ]
    # The still-empty clone never replaces the original in a visible conversation list.
    assert [r.session_id for r in store.list_visible()] == ["root"]


def test_branch_versions_persist_and_switch_active_variant(tmp_path):
    store = ConversationStore(tmp_path)
    store.save(_record())
    child = store.create_branch("root", user_ordinal=1, ts=12.0)["session_id"]

    context = store.branch_context(child)
    assert context[0]["user_ordinal"] == 1
    assert [v["session_id"] for v in context[0]["variants"]] == ["root", child]
    assert store.activate_branch(child)
    assert [r.session_id for r in store.list_visible()] == [child]

    # The tree is durable, not an in-memory UI-only relationship.
    reopened = ConversationStore(tmp_path)
    assert reopened.branch_context(child)[0]["variants"][0]["session_id"] == "root"


def test_branch_rejects_stale_message_and_discards_unaccepted_clone(tmp_path):
    store = ConversationStore(tmp_path)
    store.save(_record())
    assert not store.create_branch("root", user_ordinal=1, ts=99.0)["ok"]

    child = store.create_branch("root", user_ordinal=1, ts=12.0)["session_id"]
    assert store.discard_branch(child)
    assert store.load(child) is None


def test_branch_routes_create_read_activate_and_discard(tmp_path):
    from fastapi.testclient import TestClient

    manager = SessionManager(workspace=tmp_path, provider=_Provider())
    manager.session_store.save(_record("route-root"))
    client = TestClient(create_app(manager))

    created = client.post("/v1/sessions/route-root/branches", json={"user_ordinal": 1, "ts": 12.0})
    assert created.status_code == 200
    child = created.json()["session_id"]
    context = client.get(f"/v1/sessions/{child}/branches").json()["groups"]
    assert context[0]["variants"][1]["session_id"] == child
    assert client.post(f"/v1/sessions/{child}/branches/activate").json()["ok"]
    assert client.delete(f"/v1/sessions/{child}/branches/pending").json()["ok"] is False
