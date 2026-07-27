"""SKILLS-SPEC §4.6 — REST endpoints, session mutes over HTTP, WS force-run framing.

Follows the codebase's API convention: validation failures return ``{"ok": False,
"error": …}`` bodies (not raw 4xx), matching every other /v1 management endpoint.
Engine integration runs on ScriptedProvider — no LLM, no network.
"""

from __future__ import annotations

import base64
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app


class ScriptedProvider(ProviderClient):
    """Queued turns + captured `messages` so tests can assert what the model saw."""

    def __init__(self, turns=None):
        self._turns = list(turns or [])
        self.seen: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.seen.append(messages)
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _client(tmp_path, turns=None):
    provider = ScriptedProvider(turns)
    manager = SessionManager(workspace=tmp_path, provider=provider)
    return TestClient(create_app(manager)), manager, provider


def _zip_b64(entries: dict[str, str]) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return base64.b64encode(buf.getvalue()).decode()


GREET = {
    "name": "greet",
    "description": "says hello",
    "instructions": "Say hello warmly.",
}


# -- CRUD -----------------------------------------------------------------------


def test_create_then_list_enriched(tmp_path):
    client, _m, _p = _client(tmp_path)
    assert client.post("/v1/skills", json=GREET).json()["ok"] is True
    rows = client.get("/v1/skills").json()["skills"]
    assert rows == [
        {
            "name": "greet",
            "description": "says hello",
            "instructions": "Say hello warmly.",
            "scope": "global",
            "source": "local",
            "enabled": True,
            "path": rows[0]["path"],
        }
    ]


def test_create_duplicate_and_blank_rejected(tmp_path):
    client, _m, _p = _client(tmp_path)
    client.post("/v1/skills", json=GREET)
    dup = client.post("/v1/skills", json=GREET).json()
    assert dup["ok"] is False and "already exists" in dup["error"]
    for bad in (
        {},
        {"name": "", "instructions": "x"},
        {"name": "ok-name", "instructions": "   "},
    ):
        res = client.post("/v1/skills", json=bad).json()
        assert res["ok"] is False and res["error"]


def test_patch_edit_and_toggle(tmp_path):
    client, _m, _p = _client(tmp_path)
    client.post("/v1/skills", json=GREET)
    assert (
        client.patch(
            "/v1/skills/greet", json={"description": "hi", "enabled": False}
        ).json()["ok"]
        is True
    )
    row = client.get("/v1/skills").json()["skills"][0]
    assert row["description"] == "hi" and row["enabled"] is False
    unknown = client.patch("/v1/skills/ghost", json={"description": "x"}).json()
    assert unknown["ok"] is False


def test_delete_and_unknown(tmp_path):
    client, _m, _p = _client(tmp_path)
    client.post("/v1/skills", json=GREET)
    assert client.delete("/v1/skills/greet").json()["ok"] is True
    assert client.get("/v1/skills").json()["skills"] == []
    assert client.delete("/v1/skills/greet").json()["ok"] is False


def test_move_happy_and_collision(tmp_path):
    client, _m, _p = _client(tmp_path)
    ws = tmp_path / "proj"
    ws.mkdir()
    client.post("/v1/skills", json=GREET)
    moved = client.post(
        "/v1/skills/greet/move", json={"scope": "project", "workspace": str(ws)}
    ).json()
    assert moved["ok"] is True and moved["skill"]["scope"] == "project"
    client.post("/v1/skills", json=GREET)  # recreate global → collision on move back
    res = client.post(
        "/v1/skills/greet/move", json={"scope": "global", "workspace": str(ws)}
    ).json()
    assert res["ok"] is False and "already exists" in res["error"]


def test_create_project_scope_requires_real_workspace(tmp_path):
    client, _m, _p = _client(tmp_path)
    res = client.post(
        "/v1/skills",
        json={**GREET, "scope": "project", "workspace": str(tmp_path / "nope")},
    ).json()
    assert res["ok"] is False and "workspace" in res["error"].lower()


def test_scratch_workspace_rejected_for_skill_writes(tmp_path):
    """A per-conversation scratch dir is not a project: create/move-into/confirm all refuse
    it at the manager chokepoint; moving OUT of one still works (the rescue path)."""
    client, manager, _p = _client(tmp_path)
    scratch_base = tmp_path / "scratchpads"
    manager.set_scratch_base(str(scratch_base))
    scratch_ws = scratch_base / "6d57038c-50d"
    (scratch_ws / ".coworker" / "skills").mkdir(parents=True)

    res = client.post(
        "/v1/skills",
        json={**GREET, "scope": "project", "workspace": str(scratch_ws)},
    ).json()
    assert res["ok"] is False and "temporary" in res["error"].lower()

    client.post("/v1/skills", json=GREET)  # global
    res = client.post(
        "/v1/skills/greet/move", json={"scope": "project", "workspace": str(scratch_ws)}
    ).json()
    assert res["ok"] is False and "temporary" in res["error"].lower()

    md = "---\nname: zipped\ndescription: d\n---\nbody\n"
    preview = client.post(
        "/v1/skills/upload", json={"data_b64": _zip_b64({"zipped/SKILL.md": md})}
    ).json()
    res = client.post(
        "/v1/skills/upload/confirm",
        json={"token": preview["token"], "scope": "project", "workspace": str(scratch_ws)},
    ).json()
    assert res["ok"] is False and "temporary" in res["error"].lower()

    # Rescue path: a skill already stranded in scratch can still move OUT to global.
    manager.skill_store.create(
        name="stranded", description="", instructions="x",
        scope="project", workspace=scratch_ws,
    )
    res = client.post(
        "/v1/skills/stranded/move",
        json={"scope": "global", "workspace": str(scratch_ws)},
    ).json()
    assert res["ok"] is True and res["skill"]["scope"] == "global"


def test_path_traversal_names_rejected(tmp_path):
    client, _m, _p = _client(tmp_path)
    # ".." must be encoded — the HTTP client itself normalizes a literal /.. away.
    assert client.delete("/v1/skills/%2e%2e").json()["ok"] is False
    res = client.post("/v1/skills", json={**GREET, "name": "..%2Fevil"}).json()
    assert res["ok"] is False


# -- upload + draft -----------------------------------------------------------------


def test_upload_preview_then_confirm(tmp_path):
    client, _m, _p = _client(tmp_path)
    md = "---\nname: greet\ndescription: says hello\n---\nSay hello.\n"
    preview = client.post(
        "/v1/skills/upload", json={"data_b64": _zip_b64({"greet/SKILL.md": md})}
    ).json()
    assert preview["ok"] is True and preview["name"] == "greet"
    assert client.get("/v1/skills").json()["skills"] == []  # preview installs nothing
    confirmed = client.post(
        "/v1/skills/upload/confirm", json={"token": preview["token"]}
    ).json()
    assert confirmed["ok"] is True
    row = client.get("/v1/skills").json()["skills"][0]
    assert row["source"] == "uploaded"


def test_upload_invalid_archive_friendly(tmp_path):
    client, _m, _p = _client(tmp_path)
    bad = client.post(
        "/v1/skills/upload",
        json={"data_b64": base64.b64encode(b"not a zip").decode(), "filename": "x.zip"},
    ).json()
    assert bad["ok"] is False and "zip" in bad["error"].lower()
    # A bare .md without frontmatter gets the md-specific guidance.
    bare = client.post(
        "/v1/skills/upload",
        json={"data_b64": base64.b64encode(b"no frontmatter").decode(), "filename": "a.md"},
    ).json()
    assert bare["ok"] is False and "frontmatter" in bare["error"].lower()
    assert client.post("/v1/skills/upload", json={}).json()["ok"] is False
    assert (
        client.post("/v1/skills/upload", json={"data_b64": "!!!"}).json()["ok"] is False
    )


def test_draft_returns_fields_and_survives_provider_failure(tmp_path):
    draft_json = (
        '{"name": "weekly-report", "description": "Monday report", '
        '"instructions": "1. Collect updates"}'
    )
    client, _m, _p = _client(tmp_path, [AssistantTurn(text=draft_json)])
    res = client.post("/v1/skills/draft", json={"description": "my monday report"}).json()
    assert res["ok"] is True and res["name"] == "weekly-report"
    assert res["instructions"].startswith("1.")
    # queue exhausted → provider raises → friendly error, never a 500
    res = client.post("/v1/skills/draft", json={"description": "again"}).json()
    assert res["ok"] is False and "draft" in res["error"].lower()
    assert client.post("/v1/skills/draft", json={}).json()["ok"] is False


def test_draft_revise_in_place(tmp_path):
    """§4.2 #3: a revision round sends the CURRENT form contents + feedback — stateless,
    the draft is the memory. The prompt must carry both."""
    revised = '{"name": "greet", "description": "shorter", "instructions": "1. Hi"}'
    client, _m, provider = _client(tmp_path, [AssistantTurn(text=revised)])
    res = client.post(
        "/v1/skills/draft",
        json={
            "current": {"name": "greet", "description": "long", "instructions": "1. Hello there"},
            "feedback": "shorter please",
        },
    ).json()
    assert res["ok"] is True and res["description"] == "shorter"
    prompt = str(provider.seen[-1][-1]["content"])
    assert "Hello there" in prompt  # current draft rode along…
    assert "shorter please" in prompt  # …with the feedback
    # feedback without a draft (and vice versa) → the friendly fresh-mode error
    assert client.post("/v1/skills/draft", json={"feedback": "x"}).json()["ok"] is False


# -- session mutes over HTTP ----------------------------------------------------------


def test_session_mute_roundtrip(tmp_path):
    client, _m, _p = _client(tmp_path)
    client.post("/v1/skills", json=GREET)
    view = client.get("/v1/sessions/s1/skills").json()["skills"]
    assert view == [
        {"name": "greet", "description": "says hello", "scope": "global", "enabled": True}
    ]
    after = client.post(
        "/v1/sessions/s1/skills", json={"skill": "greet", "enabled": False}
    ).json()["skills"]
    assert after[0]["enabled"] is False
    other = client.get("/v1/sessions/s2/skills").json()["skills"]
    assert other[0]["enabled"] is True  # mute is per-session
    cleared = client.post(
        "/v1/sessions/s1/skills", json={"skill": "greet", "clear": True}
    ).json()["skills"]
    assert cleared[0]["enabled"] is True
    assert (
        client.post("/v1/sessions/s1/skills", json={}).json()["ok"] is False
    )  # null input


# -- engine integration (ScriptedProvider, no LLM) --------------------------------------


def test_engine_catalog_respects_settings_disable(tmp_path):
    client, manager, _p = _client(tmp_path)
    client.post("/v1/skills", json=GREET)
    client.post(
        "/v1/skills", json={"name": "hidden", "description": "off", "instructions": "x"}
    )
    client.patch("/v1/skills/hidden", json={"enabled": False})

    from coworker.agent import build_engine
    from coworker.agents.registry import get_agent

    engine = build_engine(
        agent=get_agent("chat"),
        provider=ScriptedProvider(),
        skill_filter=manager.effective_skill_names("s1"),
    )
    system = engine.messages[0]["content"]
    assert "greet" in system
    assert "hidden" not in system


def _drain(ws):
    types = []
    while True:
        evt = ws.receive_json()
        types.append(evt["type"])
        if evt["type"] in {"turn_done", "input_rejected"}:
            return types, evt


def test_ws_force_run_frames_the_turn(tmp_path):
    """The display/model split (§4.1 #3): the provider gets the framing; the transcript
    (TURN_START + the persisted message's `_display`) gets the user's literal '/name …'."""
    client, _m, provider = _client(tmp_path, [AssistantTurn(text="done")])
    client.post("/v1/skills", json=GREET)
    with client.websocket_connect("/ws/session/s1?agent=chat") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "hello", "skill": "greet"})
        events = []
        while True:
            evt = ws.receive_json()
            events.append(evt)
            if evt["type"] == "turn_done":
                break
    framed = provider.seen[-1][-1]["content"]
    assert 'load_skill("greet")' in str(framed)
    assert "hello" in str(framed)
    start = next(e for e in events if e["type"] == "turn_start")
    assert start["data"]["display"] == "/greet hello"  # what the transcript shows
    assert "load_skill" in str(start["data"]["input"])  # what the model saw
    stored = client.get("/v1/sessions/s1/messages").json()["messages"]
    user = next(m for m in stored if m["role"] == "user")
    assert user["_display"] == "/greet hello"
    assert "load_skill" in str(user["content"])


def test_ws_force_run_unknown_and_muted_error_without_killing_socket(tmp_path):
    client, _m, provider = _client(tmp_path, [AssistantTurn(text="ok")])
    client.post("/v1/skills", json=GREET)
    client.post("/v1/sessions/s1/skills", json={"skill": "greet", "enabled": False})
    with client.websocket_connect("/ws/session/s1?agent=chat") as ws:
        assert ws.receive_json()["type"] == "ready"
        # unknown skill → visible rejection, no turn
        ws.send_json({"type": "user_message", "text": "x", "skill": "ghost"})
        evt = ws.receive_json()
        assert evt["type"] == "input_rejected"
        assert "not available" in evt["data"]["error"]
        # muted skill → same rejection (§4.6 #15: no silent auto-unmute)
        ws.send_json({"type": "user_message", "text": "x", "skill": "greet"})
        evt = ws.receive_json()
        assert evt["type"] == "input_rejected"
        # empty name → invalid frame
        ws.send_json({"type": "user_message", "text": "x", "skill": "  "})
        assert ws.receive_json()["type"] == "input_rejected"
        # socket still healthy: a normal message runs a turn
        ws.send_json({"type": "user_message", "text": "plain"})
        types, _ = _drain(ws)
        assert "turn_done" in types
    # No force-run framing ever reached the model (the catalog line in the system prompt
    # legitimately mentions load_skill — the invariant is about USER messages only).
    users = [
        str(m.get("content"))
        for msgs in provider.seen
        for m in msgs
        if m.get("role") == "user"
    ]
    assert all("Use the skill" not in u for u in users)
    assert any("plain" in u for u in users)
