"""Models per coworker (connectors-across-machines spec §4).

A manifest's ordered `models:` list binds its sessions: the first entry the machine can
run is the default there, a requested model outside the list resolves back onto it, an
empty list means "any" (today's behaviour). `recommended_models` reads as an alias for
one release.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from coworker.personas.manifest import ManifestError, parse_manifest
from coworker.server import create_app
from tests.test_persona_connections import _mgr

MANIFEST = """---
id: picky
name: Picky
icon: ops
tagline: Runs on a short list
tools: [files]
models: [anthropic:claude-opus-4-8, openai:gpt-5.5]
---
Body.
"""


def test_models_key_parses_in_order_and_dedupes():
    m = parse_manifest(MANIFEST.replace("openai:gpt-5.5]", "openai:gpt-5.5, anthropic:claude-opus-4-8]"))
    assert m.models == ["anthropic:claude-opus-4-8", "openai:gpt-5.5"]
    assert m.recommended_models == m.models  # alias, one release


def test_recommended_models_is_read_as_an_alias():
    m = parse_manifest(MANIFEST.replace("models:", "recommended_models:"))
    assert m.models == ["anthropic:claude-opus-4-8", "openai:gpt-5.5"]


def test_both_keys_must_agree():
    text = MANIFEST.replace("---\nBody.", "recommended_models: [openai:gpt-5.5]\n---\nBody.")
    with pytest.raises(ManifestError):
        parse_manifest(text)



def test_resolve_prefers_the_first_runnable_entry(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    # The builtin ops coworker lists [anthropic:claude-opus-4-8, openai:gpt-5.5].
    assert mgr.persona_models("ops")[:2] == ["anthropic:claude-opus-4-8", "openai:gpt-5.5"]
    # Nothing configured: the list still binds — first entry, honest "No model" state.
    monkeypatch.setattr(mgr, "_provider_configured", lambda name: False)
    assert mgr.resolve_persona_model("ops") == "anthropic:claude-opus-4-8"
    assert mgr.persona_models_available("ops") == []
    # Only OpenAI configured: the first RUNNABLE entry wins over the list head.
    monkeypatch.setattr(mgr, "_provider_configured", lambda name: name == "openai")
    assert mgr.resolve_persona_model("ops") == "openai:gpt-5.5"
    assert mgr.persona_models_available("ops") == ["openai:gpt-5.5"]
    # A requested model ON the list is honoured even when unrunnable (user's pick).
    assert mgr.resolve_persona_model("ops", "anthropic:claude-opus-4-8") == "anthropic:claude-opus-4-8"
    # A requested model OFF the list resolves back onto it.
    assert mgr.resolve_persona_model("ops", "ollama:llama3") == "openai:gpt-5.5"


def test_no_list_means_any_model(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    assert mgr.persona_models("cowork") == []
    assert mgr.resolve_persona_model("cowork", "ollama:qwen3") == "ollama:qwen3"
    assert mgr.resolve_persona_model("cowork") == mgr.model


def test_personas_index_annotates_availability(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENWORKER_UNSHIPPED", "1")  # ops is ships:false
    mgr = _mgr(tmp_path, monkeypatch)
    monkeypatch.setattr(mgr, "_provider_configured", lambda name: name == "openai")
    client = TestClient(create_app(mgr))
    rows = {p["id"]: p for p in client.get("/v1/personas").json()["personas"]}
    assert rows["ops"]["models"][:2] == ["anthropic:claude-opus-4-8", "openai:gpt-5.5"]
    assert rows["ops"]["models_available"] == ["openai:gpt-5.5"]
    assert rows["cowork"]["models"] == [] and rows["cowork"]["models_available"] == []
    detail = client.get("/v1/personas/ops").json()
    assert detail["models"] == rows["ops"]["models"]
    assert detail["models_available"] == ["openai:gpt-5.5"]
    assert detail["recommended_models"] == detail["models"]  # old name, one release


def test_new_session_binds_to_the_list(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    monkeypatch.setattr(mgr, "_provider_configured", lambda name: name == "openai")
    engine = mgr.get_engine("s-ops", agent="ops")
    assert engine is not None and engine.model == "openai:gpt-5.5"
    plain = mgr.get_engine("s-plain", agent="cowork")
    assert plain is not None and plain.model == mgr.model


def test_socket_set_model_off_the_list_resolves_onto_it(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    monkeypatch.setattr(mgr, "_provider_configured", lambda name: name == "openai")
    client = TestClient(create_app(mgr))
    with client.websocket_connect("/ws/session/s-ws?agent=ops") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["data"]["model"] == "openai:gpt-5.5"
        ws.send_json({"type": "set_model", "model": "ollama:llama3"})
        # The rebind is a no-op (resolved back onto the same model): no notice frame.
    assert mgr.get_engine("s-ws").model == "openai:gpt-5.5"
