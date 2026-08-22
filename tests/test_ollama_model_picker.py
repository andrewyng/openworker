"""A connected local Ollama's pulled models must appear in the composer's model picker.

Ollama has no curated matrix — its models are whatever the user pulled locally — so
``get_settings`` has to surface the live ``/api/tags`` list. Otherwise a connected Ollama
offers nothing to select or run (the picker only ever shows matrix + user-added models).
"""

from unittest.mock import patch

from coworker.server.manager import SessionManager


def test_live_ollama_models_appear_in_picker(tmp_path):
    mgr = SessionManager(workspace=tmp_path)
    live = ["ollama:llama3:latest", "ollama:qwen2.5-coder:32b"]
    with patch.object(mgr, "_ollama_alive", return_value=True), patch.object(
        mgr, "_ollama_models", return_value=live
    ):
        models = mgr.get_settings()["models"]

    assert "ollama:llama3:latest" in models
    assert "ollama:qwen2.5-coder:32b" in models


def test_ollama_models_hidden_when_server_down(tmp_path):
    mgr = SessionManager(workspace=tmp_path)
    with patch.object(mgr, "_ollama_alive", return_value=False), patch.object(
        mgr, "_ollama_models", return_value=["ollama:llama3:latest"]
    ):
        models = mgr.get_settings()["models"]

    assert not any(m.startswith("ollama:") for m in models)


def test_live_ollama_model_not_duplicated_when_also_user_added(tmp_path):
    mgr = SessionManager(workspace=tmp_path)
    with patch.object(mgr, "_ollama_alive", return_value=True), patch.object(
        mgr, "_ollama_models", return_value=["ollama:llama3:latest"]
    ):
        mgr.add_model("ollama:llama3:latest")  # user also added it by hand
        models = mgr.get_settings()["models"]

    assert models.count("ollama:llama3:latest") == 1
