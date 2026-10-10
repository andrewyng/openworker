"""Per-model settings (coworker/model_config.py) and the recommended-settings table
(coworker/providers/recommended.py): what the user saves wins, the maker's
recommendation fills the rest, and both reach the provider call, the Ollama transport,
and compaction."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from coworker import model_config
from coworker.engine import TurnEngine
from coworker.providers import local_machine
from coworker.providers.ollama_context import OllamaContextTransport, to_native_chat
from coworker.providers.recommended import recommendation_for


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))


# -- the table ----------------------------------------------------------------------------


def test_the_table_matches_a_model_under_any_provider_prefix():
    for model in (
        "ollama:nemotron-3.5-lightning:30b",
        "ollama:nemotron-3.5-lightning:30b-mlx",
        "vllm:nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4",
    ):
        rec = recommendation_for(model)
        assert rec is not None and rec.name == "NVIDIA Nemotron 3.5 Lightning"
        assert rec.sampling == {"temperature": 1.0, "top_p": 0.95}
        assert rec.thinking_available and rec.thinking_default
        assert rec.max_output_tokens == 16000 and rec.context_for_agents == 65536
        assert rec.source.startswith("https://huggingface.co/nvidia/")
    assert recommendation_for("ollama:llama3.1:8b") is None
    assert recommendation_for("gpt-5.6-sol") is None


def test_every_table_entry_is_complete_and_sourced():
    from coworker.providers.recommended import _TABLE

    raw = json.loads(_TABLE.read_text())
    assert raw["version"] == 1
    for item in raw["models"]:
        assert item["match"] and item["name"] and item["source"].startswith("https://"), item
        assert isinstance(item["context_max"], int) and item["context_max"] > 0
        assert set(item["thinking"]) == {"available", "default"}


# -- the store ------------------------------------------------------------------------------


def test_saved_settings_are_checked_merged_and_kept(tmp_path):
    record, error = model_config.set("ollama:qwen3-coder:30b", {"context_size": 65536, "thinking": False})
    assert error is None and record == {"context_size": 65536, "thinking": False}
    record, error = model_config.set("ollama:qwen3-coder:30b", {"temperature": 0.2, "context_size": None})
    assert error is None and record == {"thinking": False, "temperature": 0.2}
    assert (tmp_path / "state" / "model_config.json").is_file()
    assert model_config.get("ollama:qwen3-coder:30b") == {"thinking": False, "temperature": 0.2}
    for bad in ({"context_size": 0}, {"context_size": "big"}, {"temperature": 3}, {"top_p": 0},
                {"compaction_threshold_pct": 0.99}, {"thinking": "yes"}, {"nope": 1}):
        _, error = model_config.set("m", bad)
        assert error, bad
    model_config.remove("ollama:qwen3-coder:30b")
    assert model_config.get("ollama:qwen3-coder:30b") == {}


def test_only_one_model_is_the_default():
    model_config.set("a", {"default": True})
    model_config.set("b", {"default": True})
    assert model_config.default_model() == "b"
    assert model_config.get("a") == {}


def test_effective_settings_say_where_each_value_came_from():
    model_config.set("ollama:nemotron-3.5-lightning:30b", {"max_output_tokens": 8000})
    eff = model_config.effective("ollama:nemotron-3.5-lightning:30b")
    assert eff["max_output_tokens"] == {"value": 8000, "from": "user"}
    assert eff["temperature"] == {"value": 1.0, "from": "recommended"}
    assert eff["thinking"] == {"value": True, "from": "recommended"}
    assert eff["recommendation"]["name"] == "NVIDIA Nemotron 3.5 Lightning"
    assert "temperature" not in model_config.effective("ollama:llama3.1:8b")


# -- reaching the provider call -------------------------------------------------------------


def test_provider_settings_come_from_the_user_then_the_recommendation():
    assert model_config.model_settings_for("ollama:llama3.1:8b") == {}
    assert model_config.model_settings_for("ollama:nemotron-3.5-lightning:30b") == {
        "max_tokens": 16000, "temperature": 1.0, "top_p": 0.95, "extra_body": {"think": True},
    }
    model_config.set("ollama:nemotron-3.5-lightning:30b", {"thinking": False, "temperature": 0.3})
    assert model_config.model_settings_for("ollama:nemotron-3.5-lightning:30b") == {
        "max_tokens": 16000, "temperature": 0.3, "top_p": 0.95, "extra_body": {"think": False},
    }
    # An effort level for a model with levels; no thinking switch rides along.
    model_config.set("gpt-5.6-sol", {"reasoning_effort": "high", "max_output_tokens": 32000})
    assert model_config.model_settings_for("gpt-5.6-sol") == {"max_tokens": 32000, "reasoning_effort": "high"}


def test_the_thinking_switch_reaches_ollamas_native_call():
    body = {"model": "m", "messages": [], "think": False, "temperature": 0.3, "top_p": 0.9, "max_tokens": 500}
    native = to_native_chat(body, 32768)
    assert native["think"] is False
    assert native["options"] == {"num_ctx": 32768, "num_predict": 500, "temperature": 0.3, "top_p": 0.9}


class _Inner(httpx.BaseTransport):
    def __init__(self):
        self.requests = []

    def handle_request(self, request):
        self.requests.append(request)
        if request.url.path.endswith("/api/show"):
            return httpx.Response(200, json={"model_info": {"general.architecture": "qwen3moe", "qwen3moe.context_length": 262144}})
        return httpx.Response(200, json={"model": "qwen3-coder:30b", "message": {"role": "assistant", "content": "ok"}, "done": True, "done_reason": "stop"})


def test_a_saved_context_size_wins_over_the_machine_rule():
    inner = _Inner()
    transport = OllamaContextTransport(inner=inner, memory_bytes=8 * local_machine.GB)
    req = lambda: httpx.Request("POST", "http://127.0.0.1:11434/v1/chat/completions", json={"model": "qwen3-coder:30b", "messages": []})
    transport.handle_request(req())
    sent = [json.loads(r.content) for r in inner.requests if r.url.path == "/api/chat"]
    assert sent[0]["options"]["num_ctx"] == 16384  # the 8 GB tier
    model_config.set("ollama:qwen3-coder:30b", {"context_size": 65536})
    transport.handle_request(req())
    sent = [json.loads(r.content) for r in inner.requests if r.url.path == "/api/chat"]
    assert sent[1]["options"]["num_ctx"] == 65536


def test_compaction_uses_the_saved_threshold_and_window():
    engine = SimpleNamespace(model="acme:unlisted-model", compaction_settings=None, _warned_context_fallback=False)
    before = TurnEngine._compaction_config(engine)
    assert before["context_window"] is None
    model_config.set("acme:unlisted-model", {"context_size": 100_000, "compaction_threshold_pct": 0.6})
    cfg = TurnEngine._compaction_config(engine)
    assert cfg["context_window"] == 100_000 and cfg["threshold_pct"] == 0.6


# -- the server's view ----------------------------------------------------------------------


def test_manager_saves_reads_and_removes_model_settings(tmp_path, monkeypatch):
    from coworker.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    out = mgr.set_model_config("ollama:qwen3-coder:30b", {"context_size": 32768, "default": True})
    assert out["ok"] and out["context_size"] == {"value": 32768, "from": "user"}
    assert mgr.model == "ollama:qwen3-coder:30b"  # default: true is the Make default button
    assert mgr.get_settings()["model_config"]["ollama:qwen3-coder:30b"]["context_size"] == 32768
    assert mgr.get_model_config("ollama:qwen3-coder:30b")["model"] == "ollama:qwen3-coder:30b"
    assert mgr.set_model_config("x", {"temperature": 9}) == {"ok": False, "error": "temperature must be between 0 and 2"}
    assert mgr.remove_model_config("ollama:qwen3-coder:30b") == {"ok": True}
    assert mgr.get_model_config()["models"] == {}


# -- the machine and the models on it -------------------------------------------------------


def test_fit_and_system_facts(monkeypatch):
    GB = local_machine.GB
    assert local_machine.fit_for(25 * GB, 96 * GB) == "runs_well"
    assert local_machine.fit_for(70 * GB, 96 * GB) == "tight"
    assert local_machine.fit_for(87 * GB, 96 * GB) == "too_large"
    assert local_machine.fit_for(None, 96 * GB) == "unknown"
    facts = local_machine.system_facts()
    for key in ("processor", "graphics", "kind", "memory_bytes", "storage_free_bytes", "runs_well_up_to_bytes"):
        assert key in facts
    assert facts["kind"] in ("apple_silicon", "nvidia", "dgx_spark", "jetson", "cpu")


def test_ollama_model_facts_read_tools_thinking_and_context(monkeypatch):
    from coworker.providers import ollama_facts

    tags = {"models": [
        {"name": "nemotron-3.5-lightning:30b", "size": 25 * local_machine.GB, "details": {"parameter_size": "30.5B", "quantization_level": "Q4_K_M"}},
        {"name": "gemma3:12b", "size": 8 * local_machine.GB, "details": {}},
        {"name": "nemotron-3-super:cloud", "size": 0, "remote_model": "nemotron-3-super:cloud", "remote_host": "https://ollama.com"},
    ]}
    shows = {
        "nemotron-3.5-lightning:30b": {"capabilities": ["completion", "tools", "thinking"], "model_info": {"general.architecture": "nemotron", "nemotron.context_length": 1048576}},
        "gemma3:12b": {"capabilities": ["completion", "vision"], "model_info": {"general.architecture": "gemma3", "gemma3.context_length": 131072}},
        "nemotron-3-super:cloud": {"capabilities": ["completion", "tools", "thinking"], "model_info": {}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags)
        name = json.loads(request.content)["model"]
        return httpx.Response(200, json=shows[name])

    fake = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: fake.get(url))
    monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None: fake.post(url, json=json))
    monkeypatch.setattr(local_machine, "model_memory_bytes", lambda: 32 * local_machine.GB)
    ollama_facts.forget_all()
    rows = {r["name"]: r for r in ollama_facts.model_facts("http://localhost:11434/v1", fresh=True)}
    lightning = rows["nemotron-3.5-lightning:30b"]
    assert lightning["tools"] and lightning["thinking"] and not lightning["vision"]
    assert lightning["context_max"] == 1048576 and lightning["context"] == 65536  # the 32 GB tier
    assert lightning["fit"] == "tight" and lightning["recommendation"] == "NVIDIA Nemotron 3.5 Lightning"  # 25 of 32 GB
    gemma = rows["gemma3:12b"]
    assert not gemma["tools"] and gemma["vision"] and gemma["fit"] == "runs_well"
    cloud = rows["nemotron-3-super:cloud"]
    assert cloud["remote"] and cloud["fit"] == "cloud"
    # A saved context size shows as the user's.
    model_config.set("ollama:nemotron-3.5-lightning:30b", {"context_size": 32768})
    row = {r["name"]: r for r in ollama_facts.model_facts("http://localhost:11434/v1", fresh=True)}["nemotron-3.5-lightning:30b"]
    assert row["context"] == 32768 and row["context_from"] == "user"


def test_only_tool_capable_ollama_models_join_the_picker(tmp_path, monkeypatch):
    from coworker.providers import ollama_facts
    from coworker.server.manager import SessionManager

    monkeypatch.setattr(ollama_facts, "model_facts", lambda base=None, fresh=False: [
        {"model": "ollama:qwen3-coder:30b", "tools": True},
        {"model": "ollama:gemma3:12b", "tools": False},
        {"model": "ollama:old-server-model", "tools": None},  # an older Ollama does not say
    ])
    mgr = SessionManager(data_dir=tmp_path)
    assert mgr._ollama_models() == ["ollama:qwen3-coder:30b", "ollama:old-server-model"]


def test_a_session_can_switch_thinking_for_itself(tmp_path):
    from types import SimpleNamespace

    from coworker.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    engine = SimpleNamespace(model="ollama:nemotron-3.5-lightning:30b", model_settings={"max_tokens": 16000, "extra_body": {"think": True}})
    mgr._engines["s1"] = engine
    got = mgr.session_model_settings("s1")
    assert (got["thinking"], got["reasoning_effort"]) == (None, None)  # at the model's default
    assert got["controls"]["thinking"] == {"support": "supported", "default": True}
    out = mgr.set_session_model_settings("s1", {"thinking": False})
    assert out["ok"] and out["thinking"] is False
    assert engine.model_settings["extra_body"] == {"think": False}
    assert engine.model_settings["max_tokens"] == 16000  # the rest stays
    # Nemotron has no effort levels: refused, nothing changes.
    assert mgr.set_session_model_settings("s1", {"reasoning_effort": "high"})["ok"] is False
    # Back to the model's own setting.
    out = mgr.set_session_model_settings("s1", {"thinking": None})
    assert out["thinking"] is None and engine.model_settings["extra_body"] == {"think": True}
    assert mgr.set_session_model_settings("nope", {"thinking": True}) == {"ok": False, "error": "session not running"}
    assert mgr.set_session_model_settings("s1", {"thinking": "yes"})["ok"] is False


def test_a_session_can_set_reasoning_effort_within_the_model_levels(tmp_path):
    from types import SimpleNamespace

    from coworker.server.manager import SessionManager

    mgr = SessionManager(data_dir=tmp_path)
    engine = SimpleNamespace(model="anthropic:claude-fable-5-1", model_settings={})
    mgr._engines["s1"] = engine
    controls = mgr.session_model_settings("s1")["controls"]
    assert controls["reasoning"] == {"support": "supported", "levels": ["low", "medium", "high", "xhigh", "max"], "default": "high"}
    assert controls["thinking"] == {"support": "not_supported"}
    out = mgr.set_session_model_settings("s1", {"reasoning_effort": "low"})
    assert out["ok"] and out["reasoning_effort"] == "low" and engine.model_settings["reasoning_effort"] == "low"
    assert mgr.set_session_model_settings("s1", {"reasoning_effort": "none"})["ok"] is False  # no Off
    assert mgr.set_session_model_settings("s1", {"thinking": False})["ok"] is False  # no switch
    out = mgr.set_session_model_settings("s1", {"reasoning_effort": None})
    assert out["reasoning_effort"] is None and "reasoning_effort" not in engine.model_settings
    # A level saved for the model becomes its default, and Reset goes back to it.
    model_config.set("anthropic:claude-fable-5-1", {"reasoning_effort": "max"})
    assert mgr.model_controls("anthropic:claude-fable-5-1")["reasoning"]["default"] == "max"
