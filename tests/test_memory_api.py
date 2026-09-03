"""MEMORY-SPEC V1 — REST API + user journeys (memory screen, toast undo, on/off, rules).

The three UI moments (§5) rest on this surface: the toast's Undo is DELETE /v1/memory/{id},
the "What I remember about you" screen is GET/PATCH/DELETE /v1/memory, and the toggle +
User Rules textarea are GET/PUT /v1/memory/settings.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from coworker.memory.settings import MAX_USER_RULES_CHARS
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app


class _StubProvider(ProviderClient):
    def complete(self, **kwargs):  # pragma: no cover - engine never completes here
        raise NotImplementedError

    def capabilities(self, model):
        return ModelCapabilities()


def _fixture(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_StubProvider())
    return TestClient(create_app(manager)), manager


# -- the memory screen (§5.3): list · edit · delete · delete all ----------------


def test_memory_crud_journey(tmp_path):
    client, _ = _fixture(tmp_path)

    added = client.post(
        "/v1/memory", json={"content": "prefers short replies", "scope": "global"}
    ).json()
    listed = client.get("/v1/memory").json()["memory"]
    assert [m["content"] for m in listed] == ["prefers short replies"]
    # rows carry what the screen renders (plus summary/created_at for future use)
    assert {"id", "scope", "content", "summary", "created_at"} <= set(listed[0])

    # the user fixes the sentence in place
    patched = client.patch(
        f"/v1/memory/{added['id']}", json={"content": "prefers detailed replies"}
    ).json()
    assert patched["ok"] is True
    assert client.get("/v1/memory").json()["memory"][0]["content"] == "prefers detailed replies"

    # then deletes the row
    assert client.delete(f"/v1/memory/{added['id']}").json()["ok"] is True
    assert client.get("/v1/memory").json()["memory"] == []


def test_memory_edit_rejects_empty_and_unknown(tmp_path):
    client, _ = _fixture(tmp_path)
    added = client.post("/v1/memory", json={"content": "a fact"}).json()

    assert client.patch(f"/v1/memory/{added['id']}", json={"content": "  "}).json()["ok"] is False
    assert client.patch("/v1/memory/424242", json={"content": "x"}).json()["ok"] is False
    assert client.delete("/v1/memory/424242").json()["ok"] is False
    # empty adds are rejected too — a blank row on the screen would be meaningless
    assert client.post("/v1/memory", json={"content": "   "}).json()["ok"] is False


def test_delete_all_journey(tmp_path):
    """§5.3 'Forget everything': wipes every scope and reports the count."""
    client, _ = _fixture(tmp_path)
    client.post("/v1/memory", json={"content": "one", "scope": "global"})
    client.post("/v1/memory", json={"content": "two"})

    assert client.delete("/v1/memory").json() == {"ok": True, "deleted": 2}
    assert client.get("/v1/memory").json()["memory"] == []


def test_toast_undo_journey(tmp_path):
    """§5.1: the toast's [Undo] deletes exactly the row the save created."""
    client, manager = _fixture(tmp_path)
    kept = client.post("/v1/memory", json={"content": "keep me", "scope": "global"}).json()
    saved = client.post("/v1/memory", json={"content": "oops", "scope": "global"}).json()

    assert client.delete(f"/v1/memory/{saved['id']}").json()["ok"] is True
    remaining = client.get("/v1/memory").json()["memory"]
    assert [m["id"] for m in remaining] == [kept["id"]]


# -- settings (§4.3/§6): toggle + user rules ------------------------------------


def test_settings_roundtrip_and_partial_updates(tmp_path):
    client, _ = _fixture(tmp_path)
    assert client.get("/v1/memory/settings").json() == {"enabled": True, "user_rules": ""}

    # rules-only update leaves the toggle alone, and vice versa
    out = client.put("/v1/memory/settings", json={"user_rules": "Reply in Hindi"}).json()
    assert out == {"enabled": True, "user_rules": "Reply in Hindi"}
    out = client.put("/v1/memory/settings", json={"enabled": False}).json()
    assert out == {"enabled": False, "user_rules": "Reply in Hindi"}


def test_settings_path_never_parses_as_memory_id(tmp_path):
    client, _ = _fixture(tmp_path)
    assert client.get("/v1/memory/settings").status_code == 200
    # PATCH targets an integer id; "settings" must not match it
    assert client.patch("/v1/memory/settings", json={"content": "x"}).status_code == 422


def test_user_rules_clamped_server_side(tmp_path):
    """Security: a hostile/buggy client can't inflate every future system prompt."""
    client, _ = _fixture(tmp_path)
    client.put(
        "/v1/memory/settings", json={"user_rules": "r" * (MAX_USER_RULES_CHARS + 9_000)}
    )
    assert len(client.get("/v1/memory/settings").json()["user_rules"]) == MAX_USER_RULES_CHARS


# -- engine wiring (§4.3/§6): what a session actually gets ----------------------


def test_disabled_memory_refuses_writes_and_says_so(tmp_path):
    """§4.3: off = stop learning. The write tools stay registered (the switch can flip
    back on mid-conversation) but refuse, and the per-turn notice tells the model so it
    reports the truth instead of bluffing a save."""
    client, manager = _fixture(tmp_path)
    client.put("/v1/memory/settings", json={"enabled": False})

    engine = manager.get_engine("mem-off-session")
    assert engine is not None
    assert engine.registry.execute("remember", {"content": "x"})["saved"] is False
    assert engine.registry.execute("memory_forget", {"memory_id": 1})["deleted"] is False
    assert "Saving new memories is turned off" in engine.context_provider()


def test_existing_memories_stay_known_while_off(tmp_path):
    """§4.3 (owner decision 2026-07-28): off stops SAVING, it does not erase or
    silence. What the user already approved keeps working — the toggle's label says
    "remember NEW things", and "what I already know is kept" reads as still-in-use."""
    client, manager = _fixture(tmp_path)
    client.post("/v1/memory", json={"content": "kept fact", "scope": "global"})
    client.put("/v1/memory/settings", json={"enabled": False})

    engine = manager.get_engine("still-knows-session")
    assert "kept fact" in engine.messages[0]["content"]
    # ...and the screen still lists it, so the user can delete it if they want it gone
    assert [m["content"] for m in client.get("/v1/memory").json()["memory"]] == ["kept fact"]

    # turning saving back on restores the write tools for NEW sessions
    client.put("/v1/memory/settings", json={"enabled": True})
    engine2 = manager.get_engine("back-on-session")
    assert "remember" in engine2.registry.names()
    assert "kept fact" in engine2.messages[0]["content"]


def test_toggle_off_applies_to_a_running_session(tmp_path):
    """End to end for the live switch: a session built while saving was ON must stop
    saving the moment the user flips it off — no restart, no new conversation."""
    client, manager = _fixture(tmp_path)
    engine = manager.get_engine("live-switch-session")

    first = engine.registry.execute(
        "remember", {"content": "saved while on", "scope": "global"}
    )
    assert first["saved"] is True

    client.put("/v1/memory/settings", json={"enabled": False})
    blocked = engine.registry.execute(
        "remember", {"content": "must not persist", "scope": "global"}
    )
    assert blocked["saved"] is False
    contents = [m["content"] for m in client.get("/v1/memory").json()["memory"]]
    assert contents == ["saved while on"]


def test_user_rules_reach_new_sessions_and_outrank_memories(tmp_path):
    client, manager = _fixture(tmp_path)
    client.put("/v1/memory/settings", json={"user_rules": "Always reply in Hindi"})
    client.post("/v1/memory", json={"content": "prefers English", "scope": "global"})

    sys_prompt = manager.get_engine("rules-session").messages[0]["content"]
    assert "Always reply in Hindi" in sys_prompt
    assert "outrank" in sys_prompt  # the rules block states its precedence
    # rules are injected ABOVE learned memories (spec §6)
    assert sys_prompt.index("Always reply in Hindi") < sys_prompt.index("prefers English")


def test_agent_saves_reach_the_screen(tmp_path):
    """Journey: the agent's `remember` (with summary) lands in the same store the
    screen lists — one source of truth for chat and Settings."""
    client, manager = _fixture(tmp_path)
    engine = manager.get_engine("save-session")
    engine.registry.execute(
        "remember",
        {"content": "user is not technical — avoid jargon", "summary": "avoid jargon", "scope": "global"},
    )
    rows = client.get("/v1/memory").json()["memory"]
    assert [r["summary"] for r in rows] == ["avoid jargon"]
