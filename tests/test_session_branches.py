"""Live-follow side sessions: durable ancestry and context composition."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from coworker.conversations import ConversationStore
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app
from coworker.sessions import SessionRecord


class RecordingProvider(ProviderClient):
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append([dict(message) for message in messages])
        return AssistantTurn(text=self.replies.pop(0), finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _record(tmp_path, session_id: str, messages: list[dict]) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        workspace=str(tmp_path),
        model="gpt-5.5",
        mode="interactive",
        messages=messages,
        title=session_id,
        agent="chat",
    )


def test_branch_relationships_persist(tmp_path):
    store = ConversationStore(tmp_path / "state")
    for session_id in ("main", "child", "grandchild", "sibling"):
        store.save(_record(tmp_path, session_id, []))

    child = store.create_branch("child", "main")
    store.create_branch("grandchild", "child")
    store.create_branch("sibling", "main")

    assert child.parent_session_id == "main"
    assert child.mode == "follow"
    assert store.branch_for("child") == child
    assert [b.child_session_id for b in store.branches_from("main")] == [
        "child",
        "sibling",
    ]
    assert store.has_active_children("main") is True

    assert store.delete("grandchild") is True
    assert store.branch_for("grandchild") is None


def test_side_session_follows_latest_parent_without_copying_history(tmp_path):
    provider = RecordingProvider(["side one", "side two"])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    parent_messages = [
        {"role": "system", "content": "parent system"},
        {"role": "user", "content": "main question"},
        {"role": "assistant", "content": "main answer"},
    ]
    manager.session_store.save(_record(tmp_path, "main", parent_messages))

    created = manager.create_side_session("main")
    assert created["ok"] is True
    child_id = created["session"]["session_id"]
    assert created["branch"]["parent_session_id"] == "main"
    assert created["session"]["mode"] == "discuss"
    # Keep the manager's unrelated background auto-title call out of this provider trace.
    manager.session_store.rename(child_id, "Side chat")

    engine = manager.get_engine(child_id)
    assert engine is not None

    async def run(text: str) -> None:
        assert manager.try_mark_running(child_id)
        try:
            async for _event in engine.run(text):
                pass
            manager.save(child_id, engine)
        finally:
            manager.mark_idle(child_id)

    asyncio.run(run("side question one"))
    first = provider.calls[0]
    assert first[0]["role"] == "system"
    assert "parent system" not in [m.get("content") for m in first]
    assert [m.get("content") for m in first[1:3]] == [
        "main question",
        "main answer",
    ]
    assert first[-1]["content"].startswith("side question one")

    parent = manager.session_store.load("main")
    assert parent is not None
    parent.messages.extend(
        [
            {"role": "user", "content": "new main fact"},
            {"role": "assistant", "content": "new main answer"},
        ]
    )
    manager.session_store.save(parent)

    asyncio.run(run("side question two"))
    second_contents = [m.get("content") for m in provider.calls[1]]
    assert second_contents.index("new main fact") < second_contents.index(
        "side question one"
    )
    assert second_contents[-1].startswith("side question two")

    persisted_child = manager.session_store.load(child_id)
    assert persisted_child is not None
    local_contents = [m.get("content") for m in persisted_child.messages]
    assert "main question" not in local_contents
    assert "new main fact" not in local_contents
    assert "side question one" in local_contents
    assert "side question two" in local_contents


def test_side_session_ignores_parent_mid_turn_checkpoint(tmp_path):
    provider = RecordingProvider(["child answer"])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    manager.session_store.save(
        _record(
            tmp_path,
            "main",
            [
                {"role": "user", "content": "committed question"},
                {"role": "assistant", "content": "committed answer"},
            ],
        )
    )
    child_id = manager.create_side_session("main")["session"]["session_id"]
    manager.session_store.rename(child_id, "Side chat")

    parent = manager.session_store.load("main")
    assert parent is not None
    parent.messages.append({"role": "user", "content": "half-finished parent turn"})
    manager.session_store.save(parent, committed=False)

    engine = manager.get_engine(child_id)
    assert engine is not None

    async def run() -> None:
        async for _event in engine.run("child question"):
            pass

    asyncio.run(run())
    contents = [message.get("content") for message in provider.calls[0]]
    assert "committed question" in contents
    assert "committed answer" in contents
    assert "half-finished parent turn" not in contents


def test_side_session_rest_api(tmp_path):
    manager = SessionManager(
        workspace=tmp_path, provider=RecordingProvider(["unused"])
    )
    manager.session_store.save(
        _record(
            tmp_path,
            "main",
            [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"},
            ],
        )
    )
    client = TestClient(create_app(manager))

    created = client.post("/v1/sessions/main/branches", json={}).json()
    assert created["ok"] is True
    child_id = created["session"]["session_id"]
    assert created["session"]["parent_session_id"] == "main"

    branches = client.get("/v1/sessions/main/branches").json()["branches"]
    assert [item["session"]["session_id"] for item in branches] == [child_id]

    relation = client.get(f"/v1/sessions/{child_id}/branch").json()["branch"]
    assert relation["parent_session_id"] == "main"

    listed = {
        item["session_id"]: item
        for item in client.get("/v1/sessions").json()["sessions"]
    }
    assert listed[child_id]["parent_session_id"] == "main"
