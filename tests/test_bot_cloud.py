"""Dedicated runtime admission, deployment and browser persistence, without cloud calls."""

import asyncio
import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from coworker.bots import BotStore
from coworker.connectors.browser_automation import _BrowserController, _guard_route
from coworker.server import SessionManager, create_app


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COWORKER_DEDICATED_BOT_RUNTIME", "1")
    m = SessionManager(data_dir=tmp_path / "data")
    yield m
    asyncio.run(m.aclose())


def test_dedicated_runtime_accepts_exactly_one_bot_and_no_unrelated_engines(manager):
    assert manager.bots.runtime() == {"dedicated": True, "capacity": 1, "bot_count": 0}
    assert manager.get_engine("unrelated", agent="cowork") is None
    payload = {"name": "Researcher", "instructions": "Review research", "dedicated": True}

    def create():
        try:
            return manager.bots.create(payload)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        created = [b for b in pool.map(lambda _: create(), range(2)) if b]
    assert len(created) == 1
    bot = created[0]
    assert bot["execution"] == "dedicated"
    assert manager.bots.permits_session(bot["session_id"])
    assert not manager.bots.permits_session("another-bot")
    assert len(manager.session_store.list()) == 1
    assert manager.bots.runtime()["bot_count"] == 1


def test_dedicated_runtime_exposes_capability_and_rejects_second_create(manager):
    client = TestClient(create_app(manager))
    assert client.get("/v1/bots/runtime").json()["dedicated"] is True
    body = {"name": "Assistant", "instructions": "Review drafts", "dedicated": True}
    assert client.post("/v1/bots", json=body).status_code == 200
    assert client.post("/v1/bots", json=body).status_code == 400


def test_cloud_creation_cannot_silently_use_a_shared_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("COWORKER_DEDICATED_BOT_RUNTIME", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    try:
        with pytest.raises(ValueError, match="shared machine"):
            manager.bots.create({"name": "A", "instructions": "X", "dedicated": True})
        assert not manager.bots.store.list()
        assert not manager.session_store.list()
    finally:
        asyncio.run(manager.aclose())


def test_dedicated_state_survives_restart(tmp_path):
    path = tmp_path / "bots.db"
    bot = {"id": "one", "session_id": "one", "execution": "dedicated"}
    store = BotStore(path)
    store.save(bot)
    store.close()
    store = BotStore(path)
    assert store.for_session("one") == bot
    store.close()


def test_cloud_browser_uses_its_own_persistent_profile(manager, tmp_path, monkeypatch):
    context = MagicMock()
    context.new_page.return_value.is_closed.return_value = False
    chromium = MagicMock()
    chromium.launch_persistent_context.return_value = context
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: SimpleNamespace(start=lambda: SimpleNamespace(
        chromium=chromium, stop=lambda: None))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    controller = _BrowserController()
    try:
        page, error = controller.page()
        assert page is not None and error is None
        profile = tmp_path / "state" / "browser-profile"
        chromium.launch_persistent_context.assert_called_once_with(
            str(profile), headless=True, viewport={"width": 1280, "height": 900})
        assert profile.is_dir()
        context.route.assert_called_once_with("**/*", _guard_route)
        chromium.launch.assert_not_called()
    finally:
        controller.close()
        controller._executor.shutdown()


def deployment_module():
    path = Path(__file__).parents[1] / "deploy" / "bots" / "create.py"
    spec = importlib.util.spec_from_file_location("bot_deployment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_each_deployment_has_private_resources_without_host_or_docker_socket_mounts():
    module = deployment_module()
    a = module.deployment("researcher", "https://controller.example", "runtime:local")
    b = module.deployment("editor", "https://controller.example", "runtime:local")
    assert a["name"] != b["name"]  # Compose scopes volumes/networks by this project name.
    service = a["services"]["bot"]
    assert service["volumes"] == ["state:/state"]
    assert "ports" not in service and "privileged" not in service
    assert service["cap_drop"] == ["ALL"]
    assert service["read_only"] is True
    assert service["environment"]["COWORKER_DEDICATED_BOT_RUNTIME"] == "1"
    assert set(service["environment"]) == {
        "COWORKER_DEDICATED_BOT_RUNTIME", "COWORKER_STATE_DIR", "HOME", "BOT_RUNTIME_NAME", "BOT_CONTROLLER"}


@pytest.mark.parametrize("controller", [
    "http://controller.example", "https://user:password@controller.example",
    "https://controller.example/j/secret", "https://controller.example?token=secret",
])
def test_deployment_refuses_insecure_or_credential_bearing_controller(controller):
    with pytest.raises(ValueError, match="HTTPS origin"):
        deployment_module().deployment("researcher", controller, "runtime:local")
