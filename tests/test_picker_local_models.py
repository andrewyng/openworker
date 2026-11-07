"""OPE-260: the picker with every key removed and Ollama running.

Seen 2026-11-07: unticking an Ollama row (or its ✕) answered ok and changed nothing, because
the models a local server lists live are re-added on every read and only matrix rows were
hidden; and the composer sat on "No model" while five Ollama models were runnable, because
nothing moves the default when the key behind it is removed. Now a derived row — matrix or
live local — is hidden by id, the default is never removed from under the composer, the
default follows the first runnable model when its key goes, and settings say whether
anything at all can run (`any_model_ready`).
"""

from __future__ import annotations

import pytest

from coworker.server.manager import SessionManager

OLLAMA = ("qwen3:0.6b", "llama3.2:3b")


def _manager(tmp_path, monkeypatch, *, ollama=OLLAMA, keys_from_store=True):
    """A manager with no keys whose Ollama (when `ollama` is non-empty) answers with those
    models. With `keys_from_store`, a provider counts as configured exactly when a key is
    in the secret store — never from this machine's environment, so the tests read the
    same everywhere."""
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "TOGETHER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    live = {"alive": bool(ollama), "models": [f"ollama:{m}" for m in ollama]}
    monkeypatch.setattr(SessionManager, "_ollama_alive", lambda self: live["alive"])
    monkeypatch.setattr(SessionManager, "_ollama_models", lambda self: list(live["models"]))
    if keys_from_store:
        monkeypatch.setattr(
            SessionManager,
            "_provider_configured",
            lambda self, name: bool((self.secrets.get(f"provider:{name}") or {}).get("api_key")),
        )
    return SessionManager(data_dir=tmp_path / "data"), live


def _store_key(manager, provider: str) -> None:
    manager.secrets.put(f"provider:{provider}", {"api_key": f"test-{provider}-key"})


# -- unticking a live local model -----------------------------------------------------


def test_unticking_a_live_ollama_model_takes_it_out_of_the_picker(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch)
    assert "ollama:qwen3:0.6b" in manager.get_settings()["models"]

    res = manager.remove_model("ollama:qwen3:0.6b")
    assert res["ok"] is True
    assert "ollama:qwen3:0.6b" not in res["models"]
    assert "ollama:llama3.2:3b" in res["models"]  # only the unticked one leaves
    # It stays out on the next read (the live listing still names it).
    assert "ollama:qwen3:0.6b" not in manager.get_settings()["models"]
    assert "ollama:qwen3:0.6b" in manager._prefs["hidden_models"]


def test_ticking_it_again_brings_it_back(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch)
    manager.remove_model("ollama:qwen3:0.6b")
    res = manager.add_model("ollama:qwen3:0.6b")
    assert res["ok"] is True
    assert "ollama:qwen3:0.6b" in res["models"]
    assert "ollama:qwen3:0.6b" not in (manager._prefs.get("hidden_models") or [])
    # Twice in a row is still one hidden entry, then none.
    manager.remove_model("ollama:qwen3:0.6b")
    manager.remove_model("ollama:qwen3:0.6b")
    assert manager._prefs["hidden_models"].count("ollama:qwen3:0.6b") == 1
    manager.add_model("ollama:qwen3:0.6b")
    assert "ollama:qwen3:0.6b" in manager.get_settings()["models"]


def test_a_live_model_the_user_once_added_by_hand_is_hidden_too(tmp_path, monkeypatch):
    """Before OPE-260 a live model also stored in prefs was dropped from prefs and then
    came straight back from the live listing."""
    manager, _ = _manager(tmp_path, monkeypatch)
    manager.add_model("ollama:qwen3:0.6b")
    assert "ollama:qwen3:0.6b" in manager._prefs["models"]
    res = manager.remove_model("ollama:qwen3:0.6b")
    assert "ollama:qwen3:0.6b" not in res["models"]
    assert "ollama:qwen3:0.6b" not in manager._prefs["models"]
    assert "ollama:qwen3:0.6b" in manager._prefs["hidden_models"]


def test_a_hidden_live_model_stays_hidden_across_a_restart(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch)
    manager.remove_model("ollama:qwen3:0.6b")
    again = SessionManager(data_dir=tmp_path / "data")
    assert "ollama:qwen3:0.6b" not in again.get_settings()["models"]
    assert "ollama:llama3.2:3b" in again.get_settings()["models"]


def test_hiding_applies_to_the_other_local_servers_as_well(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch, ollama=())
    manager.secrets.put("provider:vllm", {"base_url": "http://127.0.0.1:8000/v1"})
    monkeypatch.setattr(SessionManager, "_local_server_alive", lambda self, name: name == "vllm")
    monkeypatch.setattr(
        SessionManager, "_local_server_models", lambda self, name: ["vllm:served-a", "vllm:served-b"]
    )
    assert {"vllm:served-a", "vllm:served-b"} <= set(manager.get_settings()["models"])
    res = manager.remove_model("vllm:served-a")
    assert "vllm:served-a" not in res["models"] and "vllm:served-b" in res["models"]
    assert "vllm:served-a" in manager.add_model("vllm:served-a")["models"]


def test_a_custom_cloud_id_is_still_simply_dropped_not_hidden(tmp_path, monkeypatch):
    """Custom ids live only in prefs; dropping them is enough and the hidden list stays
    clean (it is for rows that would otherwise come back)."""
    manager, _ = _manager(tmp_path, monkeypatch, ollama=())
    _store_key(manager, "openai")
    manager.add_model("my-fine-tune")
    assert "my-fine-tune" in manager.get_settings()["models"]
    res = manager.remove_model("my-fine-tune")
    assert "my-fine-tune" not in res["models"]
    assert "my-fine-tune" not in (manager._prefs.get("hidden_models") or [])


def test_a_matrix_row_is_hidden_as_before(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch, ollama=())
    _store_key(manager, "anthropic")
    assert "anthropic:claude-sonnet-5" in manager.get_settings()["models"]
    res = manager.remove_model("anthropic:claude-sonnet-5")
    assert "anthropic:claude-sonnet-5" not in res["models"]
    assert "anthropic:claude-sonnet-5" in manager._prefs["hidden_models"]


def test_the_default_model_cannot_be_removed(tmp_path, monkeypatch):
    """The composer must always be able to render the default, so removing it is refused
    with a reason instead of silently hiding-then-unhiding it."""
    manager, _ = _manager(tmp_path, monkeypatch)
    manager.set_default_model("ollama:qwen3:0.6b")
    res = manager.remove_model("ollama:qwen3:0.6b")
    assert res["ok"] is False
    assert "default" in res["error"]
    assert "ollama:qwen3:0.6b" in res["models"]
    assert "ollama:qwen3:0.6b" not in (manager._prefs.get("hidden_models") or [])
    # Make another model the default; now it can go.
    manager.set_default_model("ollama:llama3.2:3b")
    assert "ollama:qwen3:0.6b" not in manager.remove_model("ollama:qwen3:0.6b")["models"]


def test_removing_an_empty_id_is_refused(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch)
    assert manager.remove_model("")["ok"] is False
    assert manager.remove_model("   ")["ok"] is False


# -- the default follows the first runnable model when its key goes -------------------


def test_removing_the_key_behind_the_default_moves_it_to_a_live_ollama_model(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch)
    _store_key(manager, "anthropic")
    manager.set_default_model("anthropic:claude-fable-5")
    assert manager.get_settings()["model_ready"] is True

    out = manager.remove_provider("anthropic")
    assert out["ok"] is True
    assert out["default_model"] == "ollama:qwen3:0.6b"
    settings = manager.get_settings()
    assert settings["model"] == "ollama:qwen3:0.6b"
    assert settings["model_ready"] is True
    assert manager._prefs["default_model"] == "ollama:qwen3:0.6b"  # persisted for the next start


def test_another_keyed_provider_wins_over_ollama_in_picker_order(tmp_path, monkeypatch):
    """Picker order is curated rows first, then the live local models, so with an OpenAI
    key still present the default moves to an OpenAI row."""
    manager, _ = _manager(tmp_path, monkeypatch)
    _store_key(manager, "anthropic")
    _store_key(manager, "openai")
    manager.set_default_model("anthropic:claude-fable-5")
    out = manager.remove_provider("anthropic")
    moved = out["default_model"]
    assert ":" not in moved  # an OpenAI row (bare ids route to OpenAI)
    assert manager.get_settings()["model_ready"] is True


def test_when_nothing_can_run_the_default_stays_and_the_chip_is_honest(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch, ollama=())
    _store_key(manager, "anthropic")
    manager.set_default_model("anthropic:claude-fable-5")
    out = manager.remove_provider("anthropic")
    assert out["ok"] is True and "default_model" not in out
    settings = manager.get_settings()
    assert settings["model"] == "anthropic:claude-fable-5"
    assert settings["model_ready"] is False
    assert settings["any_model_ready"] is False


def test_a_default_on_another_provider_is_left_alone(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch)
    _store_key(manager, "anthropic")
    manager.set_default_model("ollama:llama3.2:3b")
    out = manager.remove_provider("anthropic")
    assert "default_model" not in out
    assert manager.get_settings()["model"] == "ollama:llama3.2:3b"


def test_a_default_still_configured_by_the_environment_is_not_moved(tmp_path, monkeypatch):
    """Removing the stored key while the variable still configures the provider changes
    nothing about what can run, so the default stays (the response already says
    `configured: true`)."""
    manager, _ = _manager(tmp_path, monkeypatch, keys_from_store=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    _store_key(manager, "anthropic")
    manager.set_default_model("anthropic:claude-fable-5")
    out = manager.remove_provider("anthropic")
    assert out["configured"] is True
    assert "default_model" not in out
    assert manager.get_settings()["model"] == "anthropic:claude-fable-5"


def test_the_moved_default_skips_hidden_rows(tmp_path, monkeypatch):
    """A model the user unticked is not what their sessions should silently start on."""
    manager, _ = _manager(tmp_path, monkeypatch)
    _store_key(manager, "anthropic")
    manager.set_default_model("anthropic:claude-fable-5")
    manager.remove_model("ollama:qwen3:0.6b")
    out = manager.remove_provider("anthropic")
    assert out["default_model"] == "ollama:llama3.2:3b"


# -- any_model_ready --------------------------------------------------------------------


def test_any_model_ready_says_whether_anything_can_run(tmp_path, monkeypatch):
    manager, live = _manager(tmp_path, monkeypatch)
    manager.model = "anthropic:claude-fable-5"  # a default nobody can run
    settings = manager.get_settings()
    assert settings["model_ready"] is False
    assert settings["any_model_ready"] is True  # the Ollama rows
    assert {"ollama:qwen3:0.6b", "ollama:llama3.2:3b"} <= set(settings["models"])

    live["alive"] = False
    settings = manager.get_settings()
    assert settings["model_ready"] is False
    assert settings["any_model_ready"] is False
    assert settings["models"] == ["anthropic:claude-fable-5"]  # the default alone, for the chip


def test_any_model_ready_is_true_whenever_the_default_runs(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path, monkeypatch)
    manager.set_default_model("ollama:qwen3:0.6b")
    settings = manager.get_settings()
    assert settings["model_ready"] is True and settings["any_model_ready"] is True


@pytest.mark.parametrize("model", ["ollama:qwen3:0.6b", "vllm:served", "lmstudio:qwen/x"])
def test_live_local_ids_are_recognised(tmp_path, monkeypatch, model):
    manager, _ = _manager(tmp_path, monkeypatch)
    assert manager._is_live_local_model(model) is True


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "anthropic:claude-fable-5", "my-fine-tune", "openrouter:z-ai/glm-5.3"])
def test_cloud_ids_are_not_live_local(tmp_path, monkeypatch, model):
    manager, _ = _manager(tmp_path, monkeypatch)
    assert manager._is_live_local_model(model) is False
