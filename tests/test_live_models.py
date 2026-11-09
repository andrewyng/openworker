"""OPE-252 layer 1: the provider's own model list, asked with the user's key.

`providers/live_models.py` builds the request per provider, reads the three answer shapes
in use and keeps only chat models; the manager caches one listing per provider for a day,
works out what is new since the check before, merges the listing with the curated catalog
for the provider page, refuses an id the key cannot call, and writes the listing's facts
for picker models the catalog lacks so the engine and `capabilities_for` see them. Every
test is offline: the shared probe client answers from a fake transport.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from coworker.providers import live_models, probe_http
from coworker.providers.capabilities import capabilities_for
from coworker.providers.registry import DESCRIPTORS, get_descriptor
from coworker.server.manager import SessionManager

OPENROUTER = get_descriptor("openrouter")
OPENROUTER_REC = OPENROUTER.recommended_model  # a catalog row, listed by the fake provider

# What the fake OpenRouter answers: the recommended row, a model the catalog does not know
# (with facts), a model nobody knows anything about, and two rows that are not chat models.
OPENROUTER_LISTING = {
    "data": [
        {"id": OPENROUTER_REC, "name": "GLM 5.3", "created": 1_760_000_000, "context_length": 1_048_576,
         "architecture": {"input_modalities": ["text"]}, "supported_parameters": ["tools"]},
        {"id": "openai/gpt-6-astra", "name": "OpenAI: GPT-6 Astra", "created": 1_788_000_000, "context_length": 922_000,
         "architecture": {"input_modalities": ["text", "image"]}, "supported_parameters": ["tools", "tool_choice"]},
        {"id": "lab/mystery", "name": "Mystery", "created": 1_790_000_000},
        {"id": "openai/text-embedding-4", "name": "Embeddings", "created": 1_700_000_000},
        {"id": "openai/whisper-2", "name": "Whisper", "created": 1_700_000_000},
    ]
}


# -- fixtures --------------------------------------------------------------------------------


@pytest.fixture
def manager(tmp_path, monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "TOGETHER_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(SessionManager, "_ollama_alive", lambda self: False)
    monkeypatch.setattr(SessionManager, "_ollama_models", lambda self: [])
    monkeypatch.setattr(
        SessionManager,
        "_provider_configured",
        lambda self, name: bool((self.secrets.get(f"provider:{name}") or {}).get("api_key")),
    )
    m = SessionManager(data_dir=tmp_path / "data")
    yield m
    live_models.use_facts_path(None)


@pytest.fixture
def fake_provider(monkeypatch):
    """The shared probe client answers from here; `asked` records every URL and header."""
    asked: list[tuple[str, dict[str, str]]] = []
    answers: dict[str, object] = {"openrouter.ai": OPENROUTER_LISTING}

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append((str(request.url), dict(request.headers)))
        body = answers.get(request.url.host)
        if body is None:
            return httpx.Response(404, json={"error": "no such host in the fake"})
        if isinstance(body, Exception):
            raise body
        if isinstance(body, int):
            return httpx.Response(body, json={"error": {"message": "nope"}})
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body)

    monkeypatch.setattr(probe_http, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    return {"asked": asked, "answers": answers}


def _key(manager, provider="openrouter"):
    manager.secrets.put(f"provider:{provider}", {"api_key": f"test-{provider}-key"})


# -- parsing the answers ---------------------------------------------------------------------


def test_openai_shape_keeps_ids_and_dates_and_drops_non_chat_rows():
    rows = live_models.parse_listing("openai", {"object": "list", "data": [
        {"id": "gpt-6-astra", "object": "model", "created": 1_788_000_000, "owned_by": "openai"},
        {"id": "gpt-5.5", "object": "model", "created": 1_745_000_000, "owned_by": "openai"},
        {"id": "text-embedding-4", "created": 1_700_000_000},
        {"id": "tts-2", "created": 1_700_000_000},
        {"id": "dall-e-4", "created": 1_700_000_000},
        {"id": "gpt-realtime-2.1", "created": 1_799_000_000},
    ]})
    assert [r.id for r in rows] == ["gpt-6-astra", "gpt-5.5"]  # newest first
    assert rows[0].name == "gpt-6-astra" and rows[0].created == "2026-08-29"
    assert rows[0].context is None and rows[0].tools is None and rows[0].vision is None


def test_anthropic_shape_uses_display_names_and_iso_dates():
    rows = live_models.parse_listing("anthropic", {"data": [
        {"type": "model", "id": "claude-opus-5-5", "display_name": "Claude Opus 5.5", "created_at": "2026-09-22T00:00:00Z"},
        {"type": "model", "id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5", "created_at": "2025-10-15T00:00:00Z"},
    ], "has_more": False})
    assert [(r.id, r.name, r.created) for r in rows] == [
        ("claude-opus-5-5", "Claude Opus 5.5", "2026-09-22"),
        ("claude-haiku-4-5", "Claude Haiku 4.5", "2025-10-15"),
    ]


def test_gemini_shape_strips_the_prefix_and_keeps_generate_content_models_only():
    rows = live_models.parse_listing("gemini", {"models": [
        {"name": "models/gemini-3-pro", "displayName": "Gemini 3 Pro", "inputTokenLimit": 2_097_152, "outputTokenLimit": 65_536,
         "supportedGenerationMethods": ["generateContent", "countTokens"]},
        {"name": "models/text-embedding-005", "displayName": "Embedding", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/imagen-4", "displayName": "Imagen", "supportedGenerationMethods": ["predict"]},
    ]})
    assert [(r.id, r.name, r.context) for r in rows] == [("gemini-3-pro", "Gemini 3 Pro", 2_097_152)]


def test_openrouter_shape_carries_context_tools_and_images():
    rows = {r.id: r for r in live_models.parse_listing("openrouter", OPENROUTER_LISTING)}
    assert set(rows) == {OPENROUTER_REC, "openai/gpt-6-astra", "lab/mystery"}
    astra = rows["openai/gpt-6-astra"]
    assert (astra.context, astra.tools, astra.vision, astra.name) == (922_000, True, True, "OpenAI: GPT-6 Astra")
    assert rows["lab/mystery"].context is None and rows["lab/mystery"].tools is None


def test_together_answers_a_bare_list_typed_by_kind():
    rows = live_models.parse_listing("together", [
        {"id": "moonshotai/Kimi-K3", "display_name": "Kimi K3", "type": "chat", "context_length": 1_048_576, "created": 1_750_000_000},
        {"id": "black-forest-labs/FLUX", "display_name": "Flux", "type": "image", "created": 1_750_000_000},
        {"id": "togethercomputer/m2-bert", "display_name": "M2", "type": "embedding", "created": 1_750_000_000},
    ])
    assert [(r.id, r.context) for r in rows] == [("moonshotai/Kimi-K3", 1_048_576)]


def test_mistral_and_cohere_shapes():
    mistral = live_models.parse_listing("mistral", {"data": [
        {"id": "mistral-large-3", "name": "Large 3", "max_context_length": 262_144, "capabilities": {"function_calling": True, "vision": False}, "created": 1_760_000_000},
    ]})
    assert (mistral[0].context, mistral[0].tools, mistral[0].vision) == (262_144, True, False)
    cohere = live_models.parse_listing("cohere", {"models": [
        {"name": "command-a-plus-05-2026", "endpoints": ["chat"], "context_length": 256_000},
        {"name": "embed-v5", "endpoints": ["embed"], "context_length": 512},
        {"name": "rerank-v4", "endpoints": ["rerank"]},
    ]})
    assert [(r.id, r.context) for r in cohere] == [("command-a-plus-05-2026", 256_000)]


def test_groq_shape_and_garbage_are_tolerated():
    assert live_models.parse_listing("groq", {"data": [{"id": "llama-4-maverick", "context_window": 131_072}]})[0].context == 131_072
    assert live_models.parse_listing("openai", {"data": "nope"}) == []
    assert live_models.parse_listing("openai", {"data": [1, "x", {"id": ""}, {"name": 5}]}) == []
    assert live_models.parse_listing("openai", "not json shaped") == []
    assert live_models.parse_listing("openai", {"data": [{"id": "gpt-5.5", "context_length": -1}, {"id": "gpt-5.5"}]})[0].context is None


# -- building the request --------------------------------------------------------------------


def test_anthropic_and_gemini_requests_carry_the_key_their_way():
    a = live_models.listing_request("anthropic", {"api_key": "sk-ant-1"}, get_descriptor("anthropic"))
    assert a["url"] == "https://api.anthropic.com/v1/models"
    assert a["headers"]["x-api-key"] == "sk-ant-1" and "anthropic-version" in a["headers"]
    g = live_models.listing_request("gemini", {"api_key": "AIza-1"}, get_descriptor("gemini"))
    assert g["url"].startswith("https://generativelanguage.googleapis.com/v1beta/models")
    assert g["params"]["key"] == "AIza-1" and g["headers"] == {}


def test_openai_compatible_vendors_ask_their_default_base_or_the_saved_one(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    o = live_models.listing_request("openai", {"api_key": "sk-1"}, get_descriptor("openai"))
    assert o["url"] == "https://api.openai.com/v1/models" and o["headers"]["Authorization"] == "Bearer sk-1"
    t = live_models.listing_request("together", {"api_key": "tg-1"}, get_descriptor("together"))
    assert t["url"] == "https://api.together.xyz/v1/models"
    custom = live_models.listing_request("together", {"api_key": "tg-1", "base_url": "https://proxy.example/v1/"}, get_descriptor("together"))
    assert custom["url"] == "https://proxy.example/v1/models"


def test_the_environment_key_stands_in_when_none_is_stored(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    a = live_models.listing_request("anthropic", {}, get_descriptor("anthropic"))
    assert a["headers"]["x-api-key"] == "sk-env"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert live_models.listing_request("anthropic", {}, get_descriptor("anthropic"))["headers"]["x-api-key"] == ""


def test_azure_asks_its_own_endpoint_and_the_account_sign_in_asks_openrouter():
    az = live_models.listing_request("azure", {"endpoint": "https://my-res.openai.azure.com", "api_key": "az-1"}, get_descriptor("azure"))
    assert az["url"].startswith("https://my-res.openai.azure.com") and az["url"].endswith("/models")
    assert live_models.listing_request("azure", {"api_key": "az-1"}, get_descriptor("azure")) is None
    acct = live_models.listing_request("openrouter-account", {"api_key": "or-acct"}, get_descriptor("openrouter-account"))
    assert acct["url"] == "https://openrouter.ai/api/v1/models" and acct["headers"]["Authorization"] == "Bearer or-acct"


def test_providers_without_a_list_say_so():
    for name in ("openai-codex", "bedrock", "vertex", "ark", "ark-agent-plan-cn"):
        assert live_models.listing_request(name, {"api_key": "x"}, get_descriptor(name)) is None
        listing = live_models.fetch(name, {"api_key": "x"}, get_descriptor(name))
        assert listing.ok is False and "curated rows" in listing.error
    assert live_models.listing_request("ollama", {}, get_descriptor("ollama")) is None
    assert live_models.listing_request("nope", {}, None) is None


def test_every_keyed_provider_either_has_a_request_or_a_stated_reason():
    for d in DESCRIPTORS:
        if d.kind == "local":
            continue
        req = live_models.listing_request(d.name, {"api_key": "k", "endpoint": "https://r.openai.azure.com"}, d)
        assert req is not None or d.name in live_models.NO_LISTING, d.name


# -- fetching ----------------------------------------------------------------------------------


def test_fetch_reads_the_listing_through_the_shared_client(fake_provider):
    listing = live_models.fetch("openrouter", {"api_key": "or-1"}, OPENROUTER)
    assert listing.ok and [m.id for m in listing.models][:2] == ["lab/mystery", "openai/gpt-6-astra"]
    url, headers = fake_provider["asked"][0]
    assert url == "https://openrouter.ai/api/v1/models" and headers["authorization"] == "Bearer or-1"


def test_fetch_never_raises(fake_provider):
    fake_provider["answers"]["openrouter.ai"] = 401
    assert live_models.fetch("openrouter", {"api_key": "bad"}, OPENROUTER).error == "The provider refused the key."
    fake_provider["answers"]["openrouter.ai"] = 503
    assert "HTTP 503" in live_models.fetch("openrouter", {"api_key": "k"}, OPENROUTER).error
    fake_provider["answers"]["openrouter.ai"] = "<html>not json</html>"
    assert "not JSON" in live_models.fetch("openrouter", {"api_key": "k"}, OPENROUTER).error
    fake_provider["answers"]["openrouter.ai"] = httpx.ConnectError("no route")
    assert "Could not reach" in live_models.fetch("openrouter", {"api_key": "k"}, OPENROUTER).error


# -- the manager: cache, new, rows -----------------------------------------------------------


def test_an_unconfigured_provider_answers_the_curated_rows_without_a_request(manager, fake_provider):
    out = manager.provider_catalog("openrouter")
    assert out["listing"] == "none" and out["configured"] is False
    assert fake_provider["asked"] == []
    ids = [r["id"] for r in out["rows"]]
    assert OPENROUTER_REC in ids and all(r["verified"] == "catalog" for r in out["rows"])
    assert all(r["on_key"] is None for r in out["rows"])


def test_a_provider_without_a_list_answers_the_curated_rows_and_the_reason(manager, fake_provider):
    _key(manager, "bedrock")
    out = manager.provider_catalog("bedrock")
    assert out["listing"] == "none" and "curated rows" in out["reason"]
    assert out["rows"] and fake_provider["asked"] == []


def test_the_first_look_merges_the_listing_with_the_catalog(manager, fake_provider):
    _key(manager)
    out = manager.provider_catalog("openrouter")
    assert out["listing"] == "live" and out["error"] is None and out["count"] == 3
    rows = {r["id"]: r for r in out["rows"]}
    rec = rows[OPENROUTER_REC]
    assert rec["verified"] == "catalog" and rec["recommended"] is True and rec["in_picker"] is True and rec["on_key"] is True
    from coworker.providers.matrix import MATRIX

    assert rec["label"] == MATRIX[f"openrouter:{OPENROUTER_REC}"].label  # the catalog's label, not the listing's
    astra = rows["openai/gpt-6-astra"]
    assert astra["verified"] == "provider" and astra["context"] == 922_000 and astra["vision"] is True
    assert astra["in_picker"] is False and astra["label"] == "OpenAI: GPT-6 Astra" and astra["released"] == "2026-08-29"
    assert rows["lab/mystery"]["verified"] == "unverified" and rows["lab/mystery"]["context"] is None
    assert "openai/text-embedding-4" not in rows
    # nothing is "new" on the first look: the user never saw a list to compare with
    assert out["new"] == [] and manager.provider_new_models("openrouter") == 0
    # a curated picker row the key cannot call is said so
    assert rows["moonshotai/kimi-k3"]["on_key"] is False
    assert "openrouter:moonshotai/kimi-k3" in out["not_on_key"]
    assert out["rows"][0]["id"] == OPENROUTER_REC  # recommended first when nothing is new


def test_the_listing_is_cached_for_a_day_and_refresh_asks_again(manager, fake_provider):
    _key(manager)
    manager.provider_catalog("openrouter")
    manager.provider_catalog("openrouter")
    assert len(fake_provider["asked"]) == 1
    manager.provider_catalog("openrouter", refresh=True)
    assert len(fake_provider["asked"]) == 2
    # the cache survives a restart (a new manager on the same data dir)
    again = SessionManager(data_dir=manager._data_base)
    again.secrets = manager.secrets
    again.provider_catalog("openrouter")
    assert len(fake_provider["asked"]) == 2


def test_new_models_are_those_the_listing_gained_since_the_check_before(manager, fake_provider):
    _key(manager)
    manager.provider_catalog("openrouter")
    # the provider adds a model; the check before is old enough to be a baseline
    fake_provider["answers"]["openrouter.ai"] = {"data": [*OPENROUTER_LISTING["data"], {"id": "lab/brand-new", "name": "Brand New", "created": 1_799_000_000}]}
    manager._load_catalogs()["openrouter"]["fetched_at"] -= 2 * 3600
    out = manager.provider_catalog("openrouter", refresh=True)
    assert out["new"] == ["openrouter:lab/brand-new"]
    assert out["rows"][0]["id"] == "lab/brand-new" and out["rows"][0]["new"] is True
    assert manager.provider_new_models("openrouter") == 1
    row = next(p for p in manager.get_providers() if p["name"] == "openrouter")
    assert row["new_models"] == 1
    # a second refresh inside the hour keeps the badge; one after the hour moves on
    assert manager.provider_catalog("openrouter", refresh=True)["new"] == ["openrouter:lab/brand-new"]
    manager._load_catalogs()["openrouter"]["fetched_at"] -= 2 * 3600
    assert manager.provider_catalog("openrouter", refresh=True)["new"] == []


def test_a_failed_refetch_keeps_the_last_listing_and_says_why(manager, fake_provider):
    _key(manager)
    manager.provider_catalog("openrouter")
    fake_provider["answers"]["openrouter.ai"] = 503
    out = manager.provider_catalog("openrouter", refresh=True)
    assert out["listing"] == "live" and "HTTP 503" in out["error"]
    assert {r["id"] for r in out["rows"]} >= {OPENROUTER_REC, "openai/gpt-6-astra"}


def test_a_failed_first_fetch_falls_back_to_the_curated_rows(manager, fake_provider):
    _key(manager)
    fake_provider["answers"]["openrouter.ai"] = 401
    out = manager.provider_catalog("openrouter")
    assert out["listing"] == "error" and out["error"] == "The provider refused the key."
    assert all(r["verified"] == "catalog" for r in out["rows"]) and out["rows"]


def test_an_unknown_provider_is_refused(manager):
    assert manager.provider_catalog("nope")["ok"] is False


# -- adding a model the key cannot call ------------------------------------------------------


def test_add_model_refuses_an_id_the_key_cannot_call_and_names_the_closest(manager, fake_provider):
    _key(manager)
    manager.provider_catalog("openrouter")
    out = manager.add_model("openrouter:openai/gpt-6-astro")
    assert out["ok"] is False and out["reason"] == "not_on_key"
    assert out["error"] == "Not available on this key: openai/gpt-6-astro"
    assert out["suggestions"] == ["openai/gpt-6-astra"]
    assert "openrouter:openai/gpt-6-astro" not in manager.get_settings()["models"]


def test_add_model_accepts_an_id_the_key_lists_whatever_its_case(manager, fake_provider):
    _key(manager)
    manager.provider_catalog("openrouter")
    assert manager.add_model("openrouter:openai/gpt-6-astra")["ok"] is True
    assert manager.add_model("openrouter:OpenAI/GPT-6-Astra")["ok"] is True
    assert "openrouter:openai/gpt-6-astra" in manager.get_settings()["models"]


def test_add_model_is_not_gated_without_a_listing(manager, fake_provider):
    _key(manager, "anthropic")  # configured, list never asked
    assert manager.add_model("anthropic:claude-opus-5-5")["ok"] is True
    _key(manager, "bedrock")  # no list to ask
    assert manager.add_model("bedrock:claude/anthropic.claude-opus-5-v1:0")["ok"] is True
    assert manager.add_model("ollama:qwen3:0.6b")["ok"] is True  # local servers are never gated here


# -- the facts reach the engine and capabilities_for -------------------------------------------


def test_a_ticked_live_model_gets_its_facts_from_the_listing(manager, fake_provider):
    _key(manager)
    manager.provider_catalog("openrouter")
    manager.add_model("openrouter:openai/gpt-6-astra")
    manager.add_model("openrouter:lab/mystery")
    facts = json.loads((manager._data_base / "live_facts.json").read_text(encoding="utf-8"))
    assert facts["openrouter:openai/gpt-6-astra"] == {"name": "OpenAI: GPT-6 Astra", "context": 922_000, "tools": True, "vision": True}
    assert facts["openrouter:lab/mystery"] == {"name": "Mystery"}
    assert f"openrouter:{OPENROUTER_REC}" not in facts  # the catalog already vouches for it
    settings = manager.get_settings()
    assert settings["model_labels"]["openrouter:openai/gpt-6-astra"] == "OpenAI: GPT-6 Astra"
    assert settings["model_context_windows"]["openrouter:openai/gpt-6-astra"] == 922_000
    assert "openrouter:lab/mystery" not in settings["model_context_windows"]
    assert settings["model_verification"]["openrouter:openai/gpt-6-astra"] == "provider"
    assert settings["model_verification"]["openrouter:lab/mystery"] == "unverified"
    assert settings["model_verification"][f"openrouter:{OPENROUTER_REC}"] == "catalog"
    assert live_models.context_window_for("openrouter:openai/gpt-6-astra") == 922_000
    caps = capabilities_for("openrouter:openai/gpt-6-astra")
    assert caps.tools is True and caps.vision is True
    # a model with no facts keeps the heuristics
    assert capabilities_for("openrouter:lab/mystery").vision is False


def test_unticking_a_live_model_drops_its_facts(manager, fake_provider):
    _key(manager)
    manager.provider_catalog("openrouter")
    manager.add_model("openrouter:openai/gpt-6-astra")
    manager.remove_model("openrouter:openai/gpt-6-astra")
    manager._rewrite_live_facts()
    assert "openrouter:openai/gpt-6-astra" not in live_models.all_facts()


def test_the_facts_file_is_tolerant(tmp_path):
    live_models.use_facts_path(tmp_path / "facts.json")
    try:
        assert live_models.all_facts() == {}
        (tmp_path / "facts.json").write_text("not json", encoding="utf-8")
        assert live_models.facts_for("x") == {}
        (tmp_path / "facts.json").write_text('{"a": {"context": 5}, "b": 3}', encoding="utf-8")
        assert live_models.all_facts() == {"a": {"context": 5}}
        assert live_models.context_window_for("a") == 5 and live_models.context_window_for("b") is None
    finally:
        live_models.use_facts_path(None)


# -- the engine: window order and the overflow learner -------------------------------------------


def _engine(tmp_path, model, provider=None):
    import aisuite as ai

    from coworker.engine import TurnEngine
    from coworker.permissions import PermissionEngine
    from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from coworker.tools import ToolRegistry

    class _OneTurn(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return AssistantTurn(text="done", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    return TurnEngine(provider=provider or _OneTurn(), registry=registry, permissions=PermissionEngine(workspace_root=tmp_path), model=model)


def test_a_saved_window_beats_the_catalog_and_the_catalog_beats_the_listing(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker import model_config

    live_models.use_facts_path(tmp_path / "facts.json")
    try:
        live_models.write_facts({"openrouter:lab/new": {"context": 300_000}, "anthropic:claude-fable-5-1": {"context": 10}})
        assert _engine(tmp_path, "openrouter:lab/new")._compaction_config()["context_window"] == 300_000
        assert _engine(tmp_path, "anthropic:claude-fable-5-1")._compaction_config()["context_window"] == 1_000_000
        model_config.set("anthropic:claude-fable-5-1", {"context_size": 50_000})
        assert _engine(tmp_path, "anthropic:claude-fable-5-1")._compaction_config()["context_window"] == 50_000
    finally:
        live_models.use_facts_path(None)


def test_the_provider_overflow_error_teaches_the_window(tmp_path, monkeypatch):
    """A model nobody has a figure for ran on the 128k guess; the provider refused a long
    prompt and named 200,000 as its limit. The limit is saved as the model's window."""
    import asyncio

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker import model_config
    from coworker.providers import ModelCapabilities, ProviderClient

    class _Overflowing(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            raise RuntimeError("Error code: 400 - prompt is too long: 213456 tokens > 200000 maximum")

        def capabilities(self, model):
            return ModelCapabilities()

    engine = _engine(tmp_path, "anthropic:claude-unlisted-9", _Overflowing())

    async def run():
        return [ev async for ev in engine.run("hello")]

    asyncio.run(run())
    assert model_config.context_size_for("anthropic:claude-unlisted-9") == 200_000
    assert engine._compaction_config()["context_window"] == 200_000


def test_a_window_already_at_or_below_the_limit_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker import model_config

    engine = _engine(tmp_path, "anthropic:claude-haiku-4-5")  # catalog: 200,000
    engine._learn_window_from(RuntimeError("prompt is too long: 250000 tokens > 200000 maximum"))
    assert model_config.context_size_for("anthropic:claude-haiku-4-5") is None
    engine._learn_window_from(RuntimeError("context_length_exceeded"))  # no figure named
    assert model_config.context_size_for("anthropic:claude-haiku-4-5") is None
    engine._learn_window_from(RuntimeError("This model's maximum context length is 128000 tokens."))
    assert model_config.context_size_for("anthropic:claude-haiku-4-5") == 128_000  # a smaller limit than the catalog's wins


def test_the_catalog_route_is_mounted(manager, fake_provider):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app

    _key(manager)
    client = TestClient(create_app(manager))
    out = client.get("/v1/providers/openrouter/catalog").json()
    assert out["listing"] == "live" and len(fake_provider["asked"]) == 1
    client.get("/v1/providers/openrouter/catalog?refresh=true")
    assert len(fake_provider["asked"]) == 2
    assert client.get("/v1/providers/nope/catalog").json()["ok"] is False
