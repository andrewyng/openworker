"""Bots use real sessions and the normal permission runtime; no model service needed."""

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coworker.bots import BotStore
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="Work reviewed.", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    m = SessionManager(data_dir=tmp_path / "data", provider=Provider())
    yield m
    asyncio.run(m.aclose())


def create(manager, **fields):
    return manager.bots.create(dict(name="Researcher", instructions="Track the existing research.", **fields))


async def drain(manager):
    await manager.bots.tick()
    await asyncio.gather(*list(manager.bots._tasks))


def test_two_delegations_continue_one_persistent_conversation(manager):
    bot = create(manager)
    for prompt in ("Review the first task", "Continue from the previous result"):
        manager.bots.store.enqueue(bot["id"], prompt)
        asyncio.run(drain(manager))
    record = manager.session_store.load(bot["session_id"])
    assert record.workspace == bot["workspace"]
    assert [m["content"] for m in record.messages if m.get("role") == "user"] == [
        "Review the first task", "Continue from the previous result"]
    assert all(j["state"] == "done" for j in manager.bots.store.jobs(bot["id"]))
    assert "Track the existing research." in manager._user_rules_for(bot["session_id"])
    assert manager.unattended.attendance(bot["session_id"]) == "inbox"
    assert record.mode == "interactive"


def test_bot_workspaces_and_instructions_are_distinct(manager):
    a = create(manager)
    b = manager.bots.create({"name": "Editor", "instructions": "Edit drafts"})
    assert a["workspace"] != b["workspace"]
    assert Path(a["workspace"]).is_dir()
    assert "Edit drafts" not in manager._user_rules_for(a["session_id"])
    assert "Track the existing research" not in manager._user_rules_for(b["session_id"])


def test_pause_holds_queue_and_blocks_connector_delivery(manager):
    bot = create(manager)
    manager.bots.store.enqueue(bot["id"], "First task")
    manager.bots.set_enabled(bot["id"], False)
    asyncio.run(drain(manager))
    assert manager.bots.store.jobs(bot["id"])[0]["state"] == "queued"
    assert asyncio.run(manager.deliver_to_session(bot["session_id"], "A connector event")) is False
    manager.bots.set_enabled(bot["id"], True)
    asyncio.run(drain(manager))
    assert manager.bots.store.jobs(bot["id"])[0]["state"] == "done"


def test_busy_bot_defers_jobs_instead_of_steering(manager):
    bot = create(manager)
    manager.bots.store.enqueue(bot["id"], "Later")
    manager.mark_running(bot["session_id"])
    asyncio.run(drain(manager))
    assert manager.bots.store.jobs(bot["id"])[0]["state"] == "queued"
    manager.mark_idle(bot["session_id"])
    asyncio.run(drain(manager))
    assert manager.bots.store.jobs(bot["id"])[0]["state"] == "done"


def test_restart_preserves_bots_and_queue_but_does_not_replay_inflight(tmp_path):
    path = tmp_path / "bots.db"
    store = BotStore(path)
    bot = {"id": "a", "session_id": "s", "name": "Assistant"}
    store.save(bot)
    first = store.enqueue("a", "Possibly sent an email")
    second = store.enqueue("a", "Still pending")
    assert store.claim("a")["id"] == first
    store.close()
    store = BotStore(path)
    assert store.get("a") == bot
    states = {j["id"]: j["state"] for j in store.jobs("a")}
    assert states == {first: "interrupted", second: "queued"}
    assert store.claim("a")["id"] == second
    store.close()


def test_checkin_catches_up_once_in_same_session(manager):
    bot = create(manager, cron="0 9 * * *", timezone="Asia/Taipei")
    bot["next_check"] = 1
    manager.bots.store.save(bot)
    asyncio.run(drain(manager))
    asyncio.run(drain(manager))
    jobs = manager.bots.store.jobs(bot["id"])
    assert len(jobs) == 1
    assert jobs[0]["trigger"] == "check-in"
    assert jobs[0]["state"] == "done"
    assert manager.bots.store.get(bot["id"])["next_check"] > 1
    assert manager.session_store.load(bot["session_id"]).message_count > 0


def test_background_errors_are_not_reported_as_finished(manager, monkeypatch):
    bot = create(manager)
    manager.bots.store.enqueue(bot["id"], "Do work")

    async def fail(*args, on_error, **kwargs):
        on_error("Provider unavailable")
        return True

    monkeypatch.setattr(manager, "deliver_to_session", fail)
    asyncio.run(drain(manager))
    job = manager.bots.store.jobs(bot["id"])[0]
    assert job["state"] == "error"
    assert job["error"] == "Provider unavailable"


def test_pause_after_claim_does_not_run(manager):
    bot = create(manager)
    manager.bots.store.enqueue(bot["id"], "Do work")

    async def run():
        await manager.bots.tick()  # claims synchronously, delivery starts on next loop step
        manager.bots.set_enabled(bot["id"], False)
        await asyncio.gather(*list(manager.bots._tasks))

    asyncio.run(run())
    assert manager.bots.store.jobs(bot["id"])[0]["state"] == "interrupted"
    assert not manager.session_store.load(bot["session_id"]).messages


@pytest.mark.parametrize("payload", [
    {}, {"name": "A", "instructions": ""}, {"name": [], "instructions": "X"},
    {"name": "A", "instructions": "X", "cron": "bad"},
    {"name": "A", "instructions": "X", "cron": "* * * * * *"},
    {"name": "A", "instructions": "X", "timezone": "not-a-timezone"},
])
def test_invalid_create_does_not_persist(manager, payload):
    client = TestClient(create_app(manager))
    assert client.post("/v1/bots", json=payload).status_code == 400
    assert manager.bots.store.list() == []


def test_rest_lifecycle_and_token_guard(manager, monkeypatch):
    monkeypatch.setenv("COWORKER_API_TOKEN", "test-only-token")
    client = TestClient(create_app(manager))
    assert client.get("/v1/bots").status_code == 401
    client.headers.update({"X-OpenWorker-Token": "test-only-token"})
    result = client.post("/v1/bots", json={"name": "Assistant", "instructions": "Review drafts"})
    assert result.status_code == 200
    bot = result.json()["bot"]
    assert client.get("/v1/bots").json()["bots"] == [bot]
    assert client.post(f"/v1/bots/{bot['id']}/tasks", json={"prompt": "Review this draft"}).status_code == 202
    assert client.get(f"/v1/bots/{bot['id']}").json()["jobs"][0]["state"] == "queued"
    assert client.patch(f"/v1/bots/{bot['id']}", json={"enabled": "false"}).status_code == 400
    assert client.patch(f"/v1/bots/{bot['id']}", json={"enabled": False}).json()["bot"]["enabled"] is False
    assert client.post("/v1/bots/missing/tasks", json={"prompt": "Task"}).status_code == 404
    assert client.post(f"/v1/bots/{bot['id']}/tasks", json={"prompt": []}).status_code == 400
