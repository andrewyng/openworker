"""The `flag()` truth table, and the persona/session/skill flags at the API
boundary — the places a body's booleans used to be read with `bool()`.

`bool("false")` is `True`, so a client that sent `{"enabled": "false"}` got the
feature turned ON. Nothing validates a raw JSON body's types before the handler
reads it, so the string really does arrive.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from coworker.flags import flag
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app
from coworker.sessions import SessionRecord


class _StubProvider(ProviderClient):
    def complete(self, **kwargs):  # pragma: no cover - no turns in these tests
        raise NotImplementedError

    def capabilities(self, model):
        return ModelCapabilities()


def _client(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_StubProvider())
    return TestClient(create_app(manager)), manager


# -- the truth table ------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (1, True),
        ("true", True),
        ("TRUE", True),
        (" yes ", True),
        ("1", True),
        (False, False),
        (0, False),
        (None, False),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("", False),
        ("no", False),
        (2, False),
        ([], False),
    ],
)
def test_flag_reads_a_value_the_way_a_caller_means_it(value, expected):
    assert flag(value) is expected


def test_flag_never_inverts_a_stringified_false():
    """The whole bug in two lines: what `bool()` does with "false", and what a
    caller who typed it meant."""
    assert bool("false") is True
    assert flag("false") is False
    assert bool("0") is True
    assert flag("0") is False


# -- personas: POST /v1/personas/{id} -------------------------------------------


def _persona_id(manager):
    rows = manager.personas.list_all()
    if not rows:  # pragma: no cover - builtins always exist
        pytest.skip("no persona to toggle")
    return rows[0]["id"]


def test_persona_enabled_reads_a_string_flag_by_value(tmp_path):
    client, manager = _client(tmp_path)
    persona = _persona_id(manager)

    body = client.post(f"/v1/personas/{persona}", json={"enabled": "false"}).json()
    assert body["ok"] is True
    assert manager.personas.is_enabled(persona) is False

    client.post(f"/v1/personas/{persona}", json={"enabled": "true"})
    assert manager.personas.is_enabled(persona) is True


def test_persona_surfaced_reads_a_string_flag_by_value(tmp_path):
    client, manager = _client(tmp_path)
    persona = _persona_id(manager)

    client.post(f"/v1/personas/{persona}", json={"surfaced": "false"})
    assert manager.personas.is_surfaced(persona) is False

    client.post(f"/v1/personas/{persona}", json={"surfaced": "true"})
    assert manager.personas.is_surfaced(persona) is True


# -- skills: PATCH /v1/skills/{name} --------------------------------------------


def _skill(client):
    created = client.post(
        "/v1/skills",
        json={"name": "flag-check", "description": "d", "instructions": "i"},
    ).json()
    assert created["ok"] is True, created


def _skill_enabled(client, name="flag-check"):
    rows = {r["name"]: r for r in client.get("/v1/skills").json()["skills"]}
    return rows[name]["enabled"]


def test_skill_enabled_reads_a_string_flag_by_value(tmp_path):
    client, _manager = _client(tmp_path)
    _skill(client)

    body = client.patch("/v1/skills/flag-check", json={"enabled": "false"}).json()
    assert body["ok"] is True
    assert _skill_enabled(client) is False

    client.patch("/v1/skills/flag-check", json={"enabled": "true"})
    assert _skill_enabled(client) is True


# -- sessions: PATCH /v1/sessions/{id} ------------------------------------------


def test_session_pinned_and_archived_read_string_flags_by_value(tmp_path):
    client, manager = _client(tmp_path)
    for sid in ("flag-old", "flag-new"):
        manager.session_store.save(
            SessionRecord(
                session_id=sid,
                workspace=str(tmp_path),
                model="gpt-5.5",
                mode="interactive",
                messages=[{"role": "user", "content": sid}],
                agent="cowork",
            )
        )

    body = client.patch(
        "/v1/sessions/flag-old", json={"pinned": "false"}
    ).json()
    assert body["ok"] is True
    body = client.patch(
        "/v1/sessions/flag-new", json={"archived": "false"}
    ).json()
    assert body["ok"] is True

    by_id = {s["session_id"]: s for s in client.get("/v1/sessions").json()["sessions"]}
    assert by_id["flag-old"]["pinned"] is False
    assert by_id["flag-new"]["archived"] is False

    client.patch("/v1/sessions/flag-old", json={"pinned": "true"})
    by_id = {s["session_id"]: s for s in client.get("/v1/sessions").json()["sessions"]}
    assert by_id["flag-old"]["pinned"] is True
