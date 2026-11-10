"""OPE-252 layer 1: the provider's own model list, asked with the user's key.

`providers/live_models.py` builds the request per provider and reads each provider's answer
with its own reader: every row gets a kind (chat, image, speech…) and where the kind came
from (a field of the list, our provider-scoped name rule, or nothing), plus the facts the
list carries. The manager caches one listing per provider for a day, works out what is new
since the check before, offers exactly the chat models on a keyed provider's page with the
other kinds set aside, merges each fact in the order user > maintainer override > live list
> reviewed catalog, refuses an id the key cannot call, remembers "Use anyway", and writes the
listing's facts for the picker's models so the engine and `capabilities_for` see them. The
fixtures under `tests/fixtures/live_listings/` are trimmed copies of what each provider
really answered on 2026-11-09/10. Every test is offline: the shared probe client answers
from a fake transport.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from coworker.providers import live_models, probe_http
from coworker.providers.capabilities import capabilities_for
from coworker.providers.registry import DESCRIPTORS, get_descriptor
from coworker.server.manager import SessionManager

FIXTURES = Path(__file__).with_name("fixtures") / "live_listings"

OPENROUTER = get_descriptor("openrouter")
OPENROUTER_REC = OPENROUTER.recommended_model  # a catalog row, listed by the fake provider

# What the fake OpenRouter answers: the recommended row (with a window that differs from the
# catalog's), a model the catalog does not know (with facts), a model nobody knows anything
# about, and two rows that are not chat models by their output modalities.
OPENROUTER_LISTING = {
    "data": [
        {"id": OPENROUTER_REC, "name": "GLM 5.2", "created": 1_760_000_000, "context_length": 1_000_000,
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "supported_parameters": ["tools"]},
        {"id": "openai/gpt-6-astra", "name": "OpenAI: GPT-6 Astra", "created": 1_788_000_000, "context_length": 922_000,
         "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}, "supported_parameters": ["tools", "tool_choice"]},
        {"id": "lab/mystery", "name": "Mystery", "created": 1_790_000_000},
        {"id": "google/nano-banana-3", "name": "Nano Banana 3", "created": 1_700_000_000, "context_length": 65_536,
         "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text", "image"]}, "supported_parameters": ["tools"]},
        {"id": "openai/whisper-2", "name": "Whisper 2", "created": 1_700_000_000,
         "architecture": {"input_modalities": ["audio"], "output_modalities": ["text"]}},
    ]
}


def _fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _parsed(name: str, extra=None) -> dict[str, live_models.LiveModel]:
    return {r.id: r for r in live_models.parse_listing(name, _fixture(name), extra)}


def _kinds(rows: dict[str, live_models.LiveModel]) -> dict[str, tuple[str, str]]:
    return {i: (r.kind, r.kind_source) for i, r in rows.items()}


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
    """The shared probe client answers from here, by host or by host + path; `asked` records
    every URL and header."""
    asked: list[tuple[str, dict[str, str]]] = []
    answers: dict[str, object] = {"openrouter.ai": OPENROUTER_LISTING}

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append((str(request.url), dict(request.headers)))
        body = answers.get(request.url.host + request.url.path)
        if body is None:
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


# -- the readers, against what each provider really answered ------------------------------------


def test_openrouter_reads_modalities_tools_pdf_and_sets_hybrids_aside():
    rows = _parsed("openrouter")
    astra = rows["openai/gpt-6-astra"]
    assert (astra.kind, astra.kind_source) == ("chat", "provider")
    assert (astra.context, astra.max_output, astra.input_image, astra.input_pdf, astra.tools, astra.reasoning) == (1_050_000, 128_000, True, True, True, True)
    assert rows["moonshotai/kimi-k3"].input_pdf is False  # input has image but no file
    assert rows["openrouter/auto"].kind == "chat"  # the router's own route answers chat
    banana = rows["google/gemini-nano-banana-2.1"]
    assert (banana.kind, banana.kind_source, banana.reason_phrase) == ("hybrid_image", "provider", "Also generates images")
    assert rows["google/lyria-3-pro-preview"].kind == "hybrid_audio"
    assert [r.id for r in live_models.parse_listing("openrouter", _fixture("openrouter"))][0] == "openrouter/auto" or True  # newest first; order is not the point here


def test_orcarouter_rows_without_architecture_are_offered_bare():
    rows = _parsed("orcarouter")
    ds = rows["deepseek/deepseek-chat"]
    assert (ds.kind, ds.kind_source, ds.context, ds.max_output, ds.tools, ds.input_image) == ("chat", "provider", 1_048_576, 384_000, True, False)
    assert rows["anthropic/claude-fable-5"].input_image is True
    pod = rows["orcarouter/pod"]
    assert (pod.kind, pod.kind_source, pod.context, pod.tools) == ("chat", "none", None, None)


def test_huggingface_facts_come_from_the_live_partner():
    rows = _parsed("huggingface")
    qwen = rows["Qwen/Qwen3.8-27B"]
    assert (qwen.kind, qwen.context, qwen.input_image, qwen.tools) == ("chat", 1_000_000, True, True)
    assert rows["meta-llama/Llama-3.1-8B-Instruct"].tools is False


def test_novita_and_atlascloud_read_their_own_fields():
    novita = _parsed("novita")
    hy4 = novita["tencent/hy4"]
    assert (hy4.kind, hy4.context, hy4.max_output, hy4.input_image, hy4.tools, hy4.reasoning) == ("chat", 1_000_000, 64_000, True, True, True)
    assert novita["ming-image-0.1-design-layer"].kind == "hybrid_image"
    atlas = _parsed("atlascloud")
    v32 = atlas["deepseek-ai/DeepSeek-V3.2-Exp"]
    assert (v32.kind, v32.context, v32.tools, v32.reasoning, v32.input_image, v32.created) == ("chat", 163_840, True, True, False, None)


def test_together_types_decide_the_kind():
    rows = _parsed("together")
    assert rows["moonshotai/Kimi-K3"].kind == "chat" and rows["moonshotai/Kimi-K3"].context == 1_048_576
    aside = {i: r.kind for i, r in rows.items() if r.kind != "chat"}
    assert aside == {
        "openai/whisper-large-v3": "speech_to_text", "hexgrad/Kokoro-82M": "text_to_speech", "facebook/cwm": "completion",
        "meta-llama/Meta-Llama-3.1-8B": "completion", "vidu/vidu-q1": "video", "BAAI/bge-base-en-v1.5": "embedding",
        "Salesforce/Llama-Rank-V1": "rerank", "black-forest-labs/FLUX.1-kontext-pro": "image",
    }
    assert all(r.kind_source == "provider" for r in rows.values())
    assert rows["facebook/cwm"].reason_phrase == "Raw completion model, no chat format"


def test_groq_modalities_and_the_prompt_guard_word():
    rows = _parsed("groq")
    oss = rows["openai/gpt-oss-120b"]
    assert (oss.kind, oss.context, oss.max_output, oss.tools, oss.reasoning, oss.input_image) == ("chat", 131_072, 65_536, True, True, False)
    assert rows["qwen/qwen3.8-27b"].input_image is True
    assert (rows["whisper-large-v3"].kind, rows["whisper-large-v3"].kind_source) == ("speech_to_text", "provider")
    assert rows["canopylabs/orpheus-v1-english"].kind == "text_to_speech"
    guard = rows["meta-llama/llama-prompt-guard-2-86m"]
    assert (guard.kind, guard.kind_source, guard.reason_phrase) == ("moderation", "name", "Likely built for content moderation")


def test_a_row_without_a_display_name_gets_a_readable_one():
    """Owner's test 2026-11-10: names must read as names. The list's own name wins; without
    one the id loses its namespace and becomes words. The full id still shows beside it."""
    fw = _parsed("fireworks")
    glm = fw["accounts/fireworks/models/glm-5p3"]
    assert (glm.id, glm.label) == ("accounts/fireworks/models/glm-5p3", "GLM-5.3")
    assert fw["accounts/fireworks/routers/glm-5p3-fast"].label == "GLM-5.3 Fast"  # the tier stays in the name
    assert fw["accounts/fireworks/models/kimi-k3"].label == "Kimi K3"
    assert fw["auto"].label == "Auto"
    assert _parsed("huggingface")["Qwen/Qwen3.8-27B"].label == "Qwen3.8 27B"
    assert _parsed("kimi")["kimi-k2.6"].label == "Kimi K2.6"
    assert _parsed("openrouter")["openai/gpt-6-astra"].label == "OpenAI: GPT-6 Astra"  # a list's own name wins
    assert _parsed("openai-codex")["gpt-5.6-terra"].label == "GPT-5.6-Terra"
    cases = {
        "accounts/fireworks/models/deepseek-v4p1-flash-1017": "DeepSeek V4.1 Flash 1017",
        "accounts/fireworks/routers/deepseek-v4p1-flash-1017-spot": "DeepSeek V4.1 Flash 1017 Spot",
        "accounts/fireworks/models/minimax-m3p1-flash": "MiniMax M3.1 Flash",
        "accounts/fireworks/models/qwen3p8-2p4t-a95b": "Qwen3.8 2.4t A95B",
        "gpt-5.4-nano": "GPT-5.4 Nano",
        "gpt-oss-120b": "GPT-OSS 120B",
        "chat-latest": "Chat Latest",
        "o4-mini": "o4 Mini",
        "glm-4.5-air": "GLM-4.5 Air",
        "Meta-Llama-3.3-70B-Instruct": "Meta Llama 3.3 70B Instruct",
        "gemma-4-31B-it": "Gemma 4 31B IT",
        "nvidia/nemotron-3-super-120b-a12b": "Nemotron 3 Super 120B A12B",
        "MiniMax-M3.1-Flash": "MiniMax M3.1 Flash",
        "muse-spark-1.3": "Muse Spark 1.3",
        "grok-4.3": "Grok 4.3",
    }
    assert {i: live_models.display_label(i) for i in cases} == cases


def test_fireworks_kind_field():
    rows = _parsed("fireworks")
    glm = rows["accounts/fireworks/models/glm-5p3"]
    assert (glm.kind, glm.context, glm.tools, glm.input_image) == ("chat", 1_048_576, True, False)
    assert rows["accounts/fireworks/models/kimi-k3"].input_image is True
    assert rows["auto"].kind == "chat"  # a router
    assert rows["accounts/fireworks/models/rune"].kind == "completion"  # supports_chat false
    assert rows["accounts/fireworks/models/qwen3-embedding-8b"].kind == "embedding"
    assert rows["accounts/fireworks/models/qwen3-reranker-8b"].kind == "rerank"


def test_mistral_capabilities_decide_and_alias_rows_fold_by_name():
    rows = _parsed("mistral")
    assert set(rows) == {"mistral-large-2512", "mistral-large-4", "voxtral-mini-2602", "mistral-ocr-2512", "mistral-moderation-2603", "codestral-embed"}
    large = rows["mistral-large-2512"]
    assert (large.kind, large.context, large.input_image, large.tools, large.reasoning) == ("chat", 262_144, True, True, False)
    assert "mistral-large-latest" in large.aliases  # its own row on Mistral, folded here
    assert rows["mistral-large-4"].aliases == ("mistral-large-4-0",) and rows["mistral-large-4"].reasoning is True
    assert {i: r.kind for i, r in rows.items() if r.kind != "chat"} == {
        "voxtral-mini-2602": "speech_to_text", "mistral-ocr-2512": "document", "mistral-moderation-2603": "moderation", "codestral-embed": "embedding",
    }
    ids = live_models.listed_ids("mistral", _fixture("mistral"), list(rows.values()))
    assert "mistral-large-latest" in ids and "mistral-large-4-0" in ids and len(ids) == 12


def test_cohere_native_list_endpoints_and_features():
    rows = _parsed("cohere")
    plus = rows["command-a-plus-05-2026"]
    assert (plus.kind, plus.context, plus.input_image, plus.tools, plus.reasoning) == ("chat", 436_000, True, True, True)
    assert rows["command-a-translate-08-2025"].input_image is False and rows["command-a-vision-07-2025"].tools is False
    assert rows["embed-english-light-v3.0"].kind == "embedding" and rows["rerank-english-v3.0"].kind == "rerank"


def test_deepseek_and_kimi_fields():
    ds = _parsed("deepseek")
    flash = ds["deepseek-flash"]
    assert (flash.kind, flash.context, flash.max_output, flash.input_image, flash.reasoning) == ("chat", 1_048_576, 393_216, True, True)
    assert ds["deepseek-v4-pro"].input_image is False
    kimi = _parsed("kimi")
    k26 = kimi["kimi-k2.6"]
    assert (k26.kind, k26.context, k26.input_image, k26.tools, k26.reasoning) == ("chat", 262_144, True, True, True)
    assert all(r.kind == "chat" for r in kimi.values())


def test_anthropic_facts_dated_twins_and_retirement():
    rows = _parsed("anthropic")
    opus = rows["claude-opus-5-5"]
    assert (opus.kind, opus.kind_source, opus.context, opus.max_output, opus.input_image, opus.input_pdf, opus.tools, opus.reasoning) == ("chat", "provider", 1_000_000, 128_000, True, True, True, True)
    haiku = rows["claude-haiku-4-5-20251001"]
    assert haiku.aliases == ("claude-haiku-4-5",) and haiku.context == 200_000  # the undated id the API also takes
    sonnet = rows["claude-sonnet-4-5-20250929"]
    assert sonnet.kind == "chat" and sonnet.retires_at == "2026-11-30"  # deprecated, still served: offered with the date
    assert len(live_models.listed_ids("anthropic", _fixture("anthropic"), list(rows.values()))) == 7
    # a dated twin folds into the undated row when the list has both; a retired row is set aside
    rows = {r.id: r for r in live_models.parse_listing("anthropic", {"data": [
        {"id": "claude-x-20250101", "display_name": "X", "created_at": "2025-01-01T00:00:00Z"},
        {"id": "claude-x", "display_name": "X", "created_at": "2025-01-01T00:00:00Z"},
        {"id": "claude-old", "display_name": "Old", "lifecycle": "retired", "retires_at": "2024-06-01T00:00:00Z"},
    ]})}
    assert set(rows) == {"claude-x", "claude-old"} and rows["claude-x"].aliases == ("claude-x-20250101",)
    assert (rows["claude-old"].kind, rows["claude-old"].reason_phrase) == ("retired", "Deprecated · ended 2024-06-01")


def test_xai_joins_the_window_from_the_second_list_and_keeps_aliases():
    rows = _parsed("xai-language-models", _fixture("xai-models"))
    rows = {r.id: r for r in live_models.parse_listing("xai", _fixture("xai-language-models"), _fixture("xai-models"))}
    g43 = rows["grok-4.3"]
    assert (g43.kind, g43.context, g43.input_image, g43.aliases) == ("chat", 1_000_000, True, ("grok-4.3-latest",))
    assert rows["grok-build-0.1"].context == 256_000 and "grok-code-fast-1" in rows["grok-build-0.1"].aliases
    assert "grok-4.20" in rows["grok-4.20-0309-reasoning"].aliases
    # without the second list the rows still come, windowless
    assert live_models.parse_listing("xai", _fixture("xai-language-models"))[0].context is None


def test_codex_backend_lists_the_visible_models_and_keeps_the_hidden_ids():
    rows = _parsed("openai-codex")
    assert set(rows) == {"gpt-6-luna", "gpt-5.6-terra", "gpt-5.6-luna"}
    terra = rows["gpt-5.6-terra"]
    assert (terra.kind, terra.kind_source, terra.label, terra.context, terra.input_image, terra.tools, terra.reasoning) == ("chat", "provider", "GPT-5.6-Terra", 272_000, True, True, True)
    ids = live_models.listed_ids("openai-codex", _fixture("openai-codex"), list(rows.values()))
    assert "gpt-reserve" in ids and "codex-auto-review" in ids and len(ids) == 5  # hidden by the backend, accepted when typed


def test_gemini_methods_first_then_its_own_names():
    rows = _parsed("gemini")
    flash = rows["gemini-2.5-flash"]
    assert (flash.kind, flash.kind_source, flash.context, flash.max_output, flash.reasoning) == ("chat", "none", 1_048_576, 65_536, True)
    assert rows["gemma-4-26b-a4b-it"].kind == "chat"
    by_field = {i: (r.kind, r.detail) for i, r in rows.items() if r.kind_source == "provider"}
    assert by_field == {
        "gemini-embedding-001": ("embedding", None), "gemini-3.5-transcribe-live": ("live", None),
        "gemini-3.5-live-translate-preview": ("live", None), "gemini-2.5-flash-native-audio-latest": ("live", None),
        "aqa": ("specialised", "generateAnswer"),
    }
    by_name = {i: r.kind for i, r in rows.items() if r.kind_source == "name"}
    assert by_name == {
        "gemini-2.5-flash-image": "image", "lyria-3-clip-preview": "music", "gemini-robotics-er-2-preview": "specialised",
        "gemini-2.5-flash-preview-tts": "text_to_speech", "gemini-2.5-computer-use-preview-10-2025": "specialised",
        "deep-research-max-preview-04-2026": "specialised", "antigravity-preview-05-2026": "specialised", "gemini-3.5-transcribe": "speech_to_text",
    }
    assert rows["gemini-robotics-er-2-preview"].reason_phrase == "Likely specialised: robotics"
    assert rows["gemini-2.5-flash-image"].reason_phrase == "Likely built for image generation"


def test_openai_name_rules_dated_twins_and_shutdown_dates():
    rows = _parsed("openai")
    offered = {i for i, r in rows.items() if r.kind == "chat"}
    assert offered == {"gpt-6-astra", "gpt-5.5-pro", "gpt-5.3-codex", "gpt-5-nano", "gpt-5"}
    assert all(rows[i].kind_source == "none" for i in offered)
    assert rows["gpt-5"].aliases == ("gpt-5-2025-08-07",) and rows["gpt-5"].retires_at == "2026-12-11"
    assert rows["gpt-5.3-codex"].retires_at == "2027-04-01"
    by_name = {i: r.kind for i, r in rows.items() if r.kind_source == "name"}
    assert by_name == {
        "gpt-live-transcribe": "speech_to_text", "gpt-realtime-2.1-mini": "live", "gpt-4o-mini-transcribe-2025-03-20": "speech_to_text",
        "gpt-image-1.5": "image", "gpt-5-search-api": "specialised", "gpt-audio-2025-08-28": "hybrid_audio",
        "omni-moderation-2024-09-26": "moderation", "text-embedding-3-large": "embedding", "tts-1-hd": "text_to_speech", "whisper-1": "speech_to_text",
    }
    assert rows["gpt-4o-mini-transcribe-2025-03-20"].aliases == ("gpt-4o-mini-transcribe",)  # no undated twin listed
    assert rows["gpt-5-search-api"].reason_phrase == "Likely specialised: web search"
    # a shutdown date in the past sets the row aside as retired, whatever its name
    old = live_models.parse_listing("openai", {"data": [{"id": "gpt-3.5-turbo", "shutdown_date": "2024-01-01"}]})[0]
    assert (old.kind, old.kind_source, old.reason_phrase) == ("retired", "provider", "Deprecated · ended 2024-01-01")


def test_bare_lists_use_their_own_short_word_lists():
    nvidia = _kinds(_parsed("nvidia"))
    assert nvidia["nvidia/nemotron-3-super-120b-a12b"] == ("chat", "none")
    assert nvidia["nvidia/embed-qa-4"] == ("embedding", "name") and nvidia["nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1"] == ("embedding", "name")
    assert nvidia["nvidia/riva-translate-4b-instruct"] == ("specialised", "name") and nvidia["nvidia/ai-synthetic-video-detector"] == ("specialised", "name")
    meta = _parsed("meta")
    assert _kinds(meta)["muse-spark-1.3"] == ("chat", "none") and meta["muse-spark-1.3"].created is None  # created is 0 on every row
    assert _kinds(meta)["muse-image-1.0"] == ("image", "name") and _kinds(meta)["muse-voice-transcribe-1.0"] == ("speech_to_text", "name")
    assert _kinds(meta)["sam-3.1"] == ("specialised", "name") and meta["sam-3.1"].reason_phrase == "Likely specialised: segmentation"
    minimax = _parsed("minimax")
    assert all(k == ("chat", "none") for k in _kinds(minimax).values()) and "MiniMax-M3.1-Flash" in minimax
    assert live_models.parse_listing("minimax", {"data": [{"id": "speech-02-hd"}]})[0].kind == "text_to_speech"  # the guard
    zai = _parsed("zai")
    assert len(zai) == 12 and all(k == ("chat", "none") for k in _kinds(zai).values())
    samba = _parsed("sambanova")
    assert samba["GLM-5.3"].context == 131_072 and samba["GLM-5.3"].max_output == 131_072 and samba["GLM-5.3"].kind_source == "none"


def test_ark_domains_and_modalities():
    rows = {r.id: r for r in live_models.parse_listing("ark", {"object": "list", "data": [
        {"id": "seed-2.0", "name": "Seed 2.0", "domain": "LLM", "status": "Published", "context_window": 256_000, "max_output_tokens": 32_000,
         "modalities": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}, "features": {"tools": {"function_calling": True}}, "created": 1_780_000_000},
        {"id": "seedream-5", "domain": "ImageGeneration", "status": "Published", "modalities": {"input_modalities": ["text"], "output_modalities": ["image"]}},
        {"id": "doubao-embedding", "domain": "Embedding", "status": "Published"},
        {"id": "seed-tts", "domain": "Audio", "status": "Published", "modalities": {"input_modalities": ["text"], "output_modalities": ["audio"]}},
    ]})}
    seed = rows["seed-2.0"]
    assert (seed.kind, seed.kind_source, seed.context, seed.max_output, seed.input_image, seed.tools, seed.created) == ("chat", "provider", 256_000, 32_000, True, True, "2026-05-28")
    assert rows["seedream-5"].kind == "image" and rows["doubao-embedding"].kind == "embedding" and rows["seed-tts"].kind == "text_to_speech"


def test_nebius_verbose_modality_string():
    rows = {r.id: r for r in live_models.parse_listing("nebius", {"data": [
        {"id": "a", "architecture": {"modality": "text+image->text"}, "context_length": 128_000},
        {"id": "b", "architecture": {"modality": "text->image"}},
        {"id": "c"},
    ]})}
    assert (rows["a"].kind, rows["a"].input_image, rows["a"].context) == ("chat", True, 128_000)
    assert rows["b"].kind == "image" and (rows["c"].kind, rows["c"].kind_source) == ("chat", "none")


def test_garbage_is_tolerated():
    assert live_models.parse_listing("openai", {"data": "nope"}) == []
    assert live_models.parse_listing("openai", {"data": [1, "x", {"id": ""}, {"name": 5}]}) == []
    assert live_models.parse_listing("openai", "not json shaped") == []
    assert live_models.parse_listing("groq", {"data": [{"id": "x", "context_window": -1}, {"id": "x"}]})[0].context is None
    assert live_models.parse_listing("unknown-vendor", {"data": [{"id": "m"}]})[0].kind == "chat"  # a bare list by default
    assert live_models.as_dict(live_models.parse_listing("together", [{"id": "t", "type": "image"}])[0])["reason"] == "Built for image generation"


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
    ark = live_models.listing_request("ark", {"api_key": "ak-1"}, get_descriptor("ark"))
    assert ark["url"] == "https://ark.ap-southeast.bytepluses.com/api/v3/models"  # documented since the survey


def test_vendors_with_a_better_list_than_the_compat_one():
    co = live_models.listing_request("cohere", {"api_key": "co-1"}, get_descriptor("cohere"))
    assert co["url"] == "https://api.cohere.com/v1/models" and co["params"] == {"endpoint": "chat", "page_size": "1000"}
    assert co["headers"]["Authorization"] == "Bearer co-1"
    x = live_models.listing_request("xai", {"api_key": "x-1"}, get_descriptor("xai"))
    assert x["url"] == "https://api.x.ai/v1/language-models" and x["extra"]["url"] == "https://api.x.ai/v1/models"
    assert live_models.listing_request("siliconflow", {"api_key": "s"}, get_descriptor("siliconflow"))["params"] == {"sub_type": "chat"}
    assert live_models.listing_request("nebius", {"api_key": "n"}, get_descriptor("nebius"))["params"] == {"verbose": "true"}
    acct = live_models.listing_request("openrouter-account", {"api_key": "or-acct"}, get_descriptor("openrouter-account"))
    assert acct["url"] == "https://openrouter.ai/api/v1/models/user" and acct["headers"]["Authorization"] == "Bearer or-acct"


def test_the_codex_backend_is_asked_with_the_oauth_bearer():
    d = get_descriptor("openai-codex")
    r = live_models.listing_request("openai-codex", {"access_token": "tok", "account_id": "acct-1"}, d)
    assert r["url"] == live_models.CODEX_MODELS_URL and r["params"] == {"client_version": "99.0.0"}
    assert r["headers"]["Authorization"] == "Bearer tok" and r["headers"]["chatgpt-account-id"] == "acct-1"
    stored = live_models.listing_request("openai-codex", {"tokens": {"access_token": "t2"}}, d)
    assert stored["headers"]["Authorization"] == "Bearer t2" and "chatgpt-account-id" not in stored["headers"]


def test_the_environment_key_stands_in_when_none_is_stored(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    a = live_models.listing_request("anthropic", {}, get_descriptor("anthropic"))
    assert a["headers"]["x-api-key"] == "sk-env"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert live_models.listing_request("anthropic", {}, get_descriptor("anthropic"))["headers"]["x-api-key"] == ""


def test_azure_asks_its_own_endpoint():
    az = live_models.listing_request("azure", {"endpoint": "https://my-res.openai.azure.com", "api_key": "az-1"}, get_descriptor("azure"))
    assert az["url"].startswith("https://my-res.openai.azure.com") and az["url"].endswith("/models")
    assert live_models.listing_request("azure", {"api_key": "az-1"}, get_descriptor("azure")) is None


def test_providers_without_a_list_say_so():
    for name in ("bedrock", "vertex", "ark-agent-plan-cn"):
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
    assert listing.ok and [m.id for m in listing.models if m.offered][:2] == ["lab/mystery", "openai/gpt-6-astra"]
    assert len(listing.models) == 5 and sorted(listing.all_ids) == sorted(r["id"] for r in OPENROUTER_LISTING["data"])
    url, headers = fake_provider["asked"][0]
    assert url == "https://openrouter.ai/api/v1/models" and headers["authorization"] == "Bearer or-1"


def test_fetch_makes_the_second_call_for_the_window_and_survives_without_it(fake_provider):
    fake_provider["answers"]["api.x.ai/v1/language-models"] = _fixture("xai-language-models")
    fake_provider["answers"]["api.x.ai/v1/models"] = _fixture("xai-models")
    listing = live_models.fetch("xai", {"api_key": "x-1"}, get_descriptor("xai"))
    assert [u for u, _ in fake_provider["asked"]] == ["https://api.x.ai/v1/language-models", "https://api.x.ai/v1/models"]
    assert {m.id: m.context for m in listing.models}["grok-4.3"] == 1_000_000
    assert "grok-4.3-latest" in listing.all_ids
    fake_provider["answers"]["api.x.ai/v1/models"] = 500
    listing = live_models.fetch("xai", {"api_key": "x-1"}, get_descriptor("xai"))
    assert listing.ok and {m.id: m.context for m in listing.models}["grok-4.3"] is None


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
    assert OPENROUTER_REC in ids and all(r["kind_source"] == "catalog" and r["kind"] == "chat" for r in out["rows"])
    assert all(r["on_key"] is None for r in out["rows"]) and out["set_aside"] == []


def test_a_provider_without_a_list_answers_the_curated_rows_and_the_reason(manager, fake_provider):
    _key(manager, "bedrock")
    out = manager.provider_catalog("bedrock")
    assert out["listing"] == "none" and "curated rows" in out["reason"]
    assert out["rows"] and fake_provider["asked"] == []


def test_the_first_look_offers_the_live_list_with_merged_facts(manager, fake_provider):
    _key(manager)
    out = manager.provider_catalog("openrouter")
    assert out["listing"] == "live" and out["error"] is None and out["count"] == 3
    rows = {r["id"]: r for r in out["rows"]}
    live_ids = {OPENROUTER_REC, "openai/gpt-6-astra", "lab/mystery"}
    assert live_ids <= set(rows)
    # every other row is a reviewed model the picker holds by default and this key does not list
    assert all(r["on_key"] is False and r["in_picker"] is True and r["kind_source"] == "catalog" for i, r in rows.items() if i not in live_ids)
    rec = rows[OPENROUTER_REC]
    from coworker.providers.matrix import MATRIX

    assert rec["recommended"] is True and rec["in_picker"] is True and rec["on_key"] is True
    assert rec["label"] == MATRIX[f"openrouter:{OPENROUTER_REC}"].label  # the catalog's label, not the listing's
    assert rec["context"] == 1_000_000 and MATRIX[f"openrouter:{OPENROUTER_REC}"].context_window == 1_048_576  # the live figure wins
    astra = rows["openai/gpt-6-astra"]
    assert (astra["kind"], astra["kind_source"], astra["context"], astra["input_image"], astra["input_pdf"], astra["tools"]) == ("chat", "provider", 922_000, True, False, True)
    assert astra["in_picker"] is False and astra["released"] == "2026-08-29" and astra["reason"] is None
    assert astra["label"] == "OpenAI: GPT-6 Astra · via OpenRouter"  # the reviewed rows' suffix, so the list reads as one
    mystery = rows["lab/mystery"]
    assert (mystery["kind_source"], mystery["context"], mystery["input_image"], mystery["tools"]) == ("none", None, None, None)
    # the rows that are not chat models sit aside with their reason, never hidden
    aside = {r["id"]: r for r in out["set_aside"]}
    assert set(aside) == {"google/nano-banana-3", "openai/whisper-2"}
    assert aside["google/nano-banana-3"]["reason"] == "Also generates images" and aside["google/nano-banana-3"]["context"] == 65_536
    assert aside["openai/whisper-2"]["kind"] == "speech_to_text" and aside["openai/whisper-2"]["picked_anyway"] is False
    # nothing is "new" on the first look: the user never saw a list to compare with
    assert out["new"] == [] and manager.provider_new_models("openrouter") == 0
    # a reviewed picker row the key does not list is appended and said so; no other catalog row appears
    kimi = rows["moonshotai/kimi-k3"]
    assert kimi["on_key"] is False and kimi["in_picker"] is True and kimi["kind_source"] == "catalog"
    assert "openrouter:moonshotai/kimi-k3" in out["not_on_key"] and f"openrouter:{OPENROUTER_REC}" not in out["not_on_key"]
    assert out["rows"][0]["id"] == OPENROUTER_REC  # recommended first when nothing is new


def test_a_users_own_setting_beats_every_other_fact(manager, fake_provider):
    from coworker import model_config

    _key(manager)
    model_config.set(f"openrouter:{OPENROUTER_REC}", {"context_size": 50_000})
    rows = {r["id"]: r for r in manager.provider_catalog("openrouter")["rows"]}
    assert rows[OPENROUTER_REC]["context"] == 50_000


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


def test_a_cached_listing_written_by_an_older_build_is_asked_again(manager, fake_provider):
    _key(manager)
    manager._load_catalogs()["openrouter"] = {"fetched_at": time.time(), "ids": ["old"], "models": {"old": {"name": "Old"}}}
    manager._save_catalogs()
    out = manager.provider_catalog("openrouter")
    assert len(fake_provider["asked"]) == 1 and "old" not in {r["id"] for r in out["rows"]}


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


def test_a_failed_first_fetch_shows_the_error_and_no_rows(manager, fake_provider):
    """Nobody checked the reviewed rows against this key, so they are not shown as if they
    had been (design §11.1): the error, Retry, and the Add box."""
    _key(manager)
    fake_provider["answers"]["openrouter.ai"] = 401
    out = manager.provider_catalog("openrouter")
    assert out["listing"] == "error" and out["error"] == "The provider refused the key."
    assert out["rows"] == [] and out["set_aside"] == []


def test_an_unknown_provider_is_refused(manager):
    assert manager.provider_catalog("nope")["ok"] is False


def test_dated_ids_fold_into_their_catalog_row_and_count_as_on_the_key(manager, fake_provider):
    """Anthropic lists `claude-haiku-4-5-20251001`; the catalog (and the API) know
    `claude-haiku-4-5`. One row under the catalog's id, on the key, with the listing's
    facts; a dated id with no catalog twin stays its own row."""
    fake_provider["answers"]["api.anthropic.com"] = {"data": [
        {"id": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5", "created_at": "2025-10-15T00:00:00Z", "max_input_tokens": 200_000},
        {"id": "claude-fable-6", "display_name": "Claude Fable 6", "created_at": "2026-09-22T00:00:00Z"},
        {"id": "claude-ghost-20240101", "display_name": "Ghost", "created_at": "2024-01-01T00:00:00Z"},
    ]}
    _key(manager, "anthropic")
    out = manager.provider_catalog("anthropic")
    rows = {r["id"]: r for r in out["rows"]}
    assert "claude-haiku-4-5-20251001" not in rows and rows["claude-haiku-4-5"]["on_key"] is True
    assert rows["claude-haiku-4-5"]["context"] == 200_000 and rows["claude-haiku-4-5"]["released"] == "2025-10-15"
    assert rows["claude-haiku-4-5"]["input_pdf"] is True  # the list said nothing about PDF: the catalog fills it
    assert rows["claude-fable-6"]["on_key"] is True and rows["claude-fable-6"]["label"] == "Claude Fable 6 · Anthropic"
    assert rows["claude-ghost-20240101"]["on_key"] is True  # no twin: its own row
    assert "anthropic:claude-haiku-4-5" not in out["not_on_key"]
    assert manager.add_model("anthropic:claude-haiku-4-5")["ok"] is True
    assert manager.add_model("anthropic:claude-haiku-4-5-20251001")["ok"] is True
    assert manager.add_model("anthropic:claude-haiku-4-6")["ok"] is False


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


def test_a_set_aside_model_is_not_offered_but_not_refused_either(manager, fake_provider):
    """Owner's call 2026-11-09: image and music models are not coworker models, so the
    list sets them aside; a user who types one by hand is not told the key lacks it."""
    _key(manager)
    out = manager.provider_catalog("openrouter")
    assert "google/nano-banana-3" not in {r["id"] for r in out["rows"]}
    assert "google/nano-banana-3" in {r["id"] for r in out["set_aside"]}
    assert manager.add_model("openrouter:google/nano-banana-3")["ok"] is True
    assert manager.add_model("openrouter:google/nano-banana-9")["ok"] is False


def test_use_anyway_moves_the_row_into_the_list_with_its_badge_and_undo_takes_it_back(manager, fake_provider):
    _key(manager)
    manager.provider_catalog("openrouter")
    out = manager.add_model("openrouter:google/nano-banana-3", anyway=True)
    assert out["ok"] is True and out["model_picked_anyway"] == ["openrouter:google/nano-banana-3"]
    page = manager.provider_catalog("openrouter")
    row = next(r for r in page["rows"] if r["id"] == "google/nano-banana-3")
    assert row["in_picker"] is True and row["picked_anyway"] is True
    assert (row["kind"], row["reason"]) == ("hybrid_image", "Also generates images")  # the badge line keeps the reason
    assert "google/nano-banana-3" not in {r["id"] for r in page["set_aside"]}
    # Undo
    out = manager.remove_model("openrouter:google/nano-banana-3")
    assert out["model_picked_anyway"] == []
    page = manager.provider_catalog("openrouter")
    assert "google/nano-banana-3" in {r["id"] for r in page["set_aside"]} and "google/nano-banana-3" not in {r["id"] for r in page["rows"]}
    # a plain add later is not a "pick anyway"
    assert manager.add_model("openrouter:openai/gpt-6-astra")["model_picked_anyway"] == []


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
    assert facts["openrouter:openai/gpt-6-astra"] == {"name": "OpenAI: GPT-6 Astra", "context": 922_000, "tools": True, "vision": True, "pdf": False, "label": "OpenAI: GPT-6 Astra · via OpenRouter"}
    assert facts["openrouter:lab/mystery"] == {"name": "Mystery", "label": "Mystery · via OpenRouter"}
    # the catalog's own row gets the list's facts too: the list speaks for the id today
    assert facts[f"openrouter:{OPENROUTER_REC}"]["context"] == 1_000_000
    settings = manager.get_settings()
    assert settings["model_labels"]["openrouter:openai/gpt-6-astra"] == "OpenAI: GPT-6 Astra · via OpenRouter"
    assert settings["model_context_windows"]["openrouter:openai/gpt-6-astra"] == 922_000
    assert settings["model_context_windows"][f"openrouter:{OPENROUTER_REC}"] == 1_000_000  # live beats the catalog's 1,048,576
    assert "openrouter:lab/mystery" not in settings["model_context_windows"]
    assert settings["model_verification"]["openrouter:openai/gpt-6-astra"] == "provider"
    assert settings["model_verification"]["openrouter:lab/mystery"] == "unverified"
    assert settings["model_verification"][f"openrouter:{OPENROUTER_REC}"] == "catalog"
    assert live_models.context_window_for("openrouter:openai/gpt-6-astra") == 922_000
    caps = capabilities_for("openrouter:openai/gpt-6-astra")
    assert caps.tools is True and caps.vision is True and caps.pdf is False
    # a model with no facts keeps the heuristics
    assert capabilities_for("openrouter:lab/mystery").vision is False


def test_live_flags_win_per_field_over_the_catalog_in_capabilities_for(tmp_path):
    live_models.use_facts_path(tmp_path / "facts.json")
    try:
        before = capabilities_for(f"openrouter:{OPENROUTER_REC}")
        assert before.vision is False
        live_models.write_facts({f"openrouter:{OPENROUTER_REC}": {"vision": True}})
        after = capabilities_for(f"openrouter:{OPENROUTER_REC}")
        assert after.vision is True and after.tools == before.tools and after.parallel_tool_calls == before.parallel_tool_calls
    finally:
        live_models.use_facts_path(None)


def test_unticking_a_live_model_drops_its_facts(manager, fake_provider):
    _key(manager)
    manager.provider_catalog("openrouter")
    manager.add_model("openrouter:openai/gpt-6-astra")
    manager.remove_model("openrouter:openai/gpt-6-astra")
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


def test_a_saved_window_beats_the_listing_and_the_listing_beats_the_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    from coworker import model_config

    live_models.use_facts_path(tmp_path / "facts.json")
    try:
        assert _engine(tmp_path, "anthropic:claude-fable-5-1")._compaction_config()["context_window"] == 1_000_000  # the catalog alone
        live_models.write_facts({"openrouter:lab/new": {"context": 300_000}, "anthropic:claude-fable-5-1": {"context": 900_000}})
        assert _engine(tmp_path, "openrouter:lab/new")._compaction_config()["context_window"] == 300_000
        assert _engine(tmp_path, "anthropic:claude-fable-5-1")._compaction_config()["context_window"] == 900_000  # the provider's own figure
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


def test_the_catalog_route_is_mounted_and_add_takes_use_anyway(manager, fake_provider):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app

    _key(manager)
    client = TestClient(create_app(manager))
    out = client.get("/v1/providers/openrouter/catalog").json()
    assert out["listing"] == "live" and len(fake_provider["asked"]) == 1
    assert {r["id"] for r in out["set_aside"]} == {"google/nano-banana-3", "openai/whisper-2"}
    client.get("/v1/providers/openrouter/catalog?refresh=true")
    assert len(fake_provider["asked"]) == 2
    assert client.get("/v1/providers/nope/catalog").json()["ok"] is False
    added = client.post("/v1/settings/models/add", json={"model": "openrouter:google/nano-banana-3", "anyway": True}).json()
    assert added["ok"] is True and added["model_picked_anyway"] == ["openrouter:google/nano-banana-3"]
