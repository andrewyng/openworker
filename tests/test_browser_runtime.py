"""The browser tools pick up the Playwright that packaging/setup-browser-macos.sh installs
under the state folder (the packaged app does not bundle it)."""

from __future__ import annotations

import importlib
import sys

import pytest

from coworker.connectors import browser_automation as ba


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("OPENWORKER_BASE_DIR", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield tmp_path / "browser-runtime"
    sys.modules.pop("ow_fake_runtime_mod", None)


def test_installed_runtime_becomes_importable(runtime):
    (runtime / "site").mkdir(parents=True)
    (runtime / "browsers").mkdir()
    (runtime / "site" / "ow_fake_runtime_mod.py").write_text("VALUE = 42\n")

    ba._use_browser_runtime()
    ba._use_browser_runtime()

    assert importlib.import_module("ow_fake_runtime_mod").VALUE == 42
    assert sys.path.count(str(runtime / "site")) == 1
    assert sys.path[-1] == str(runtime / "site")  # appended: bundled modules win
    assert ba.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(runtime / "browsers")


def test_no_runtime_changes_nothing(runtime):
    before = list(sys.path)
    ba._use_browser_runtime()
    assert sys.path == before
    assert "PLAYWRIGHT_BROWSERS_PATH" not in ba.os.environ


def test_packaged_mac_error_points_to_setup_script(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    err = ba._BrowserController._setup_error(None, ModuleNotFoundError("playwright"))
    assert ba.SETUP_SCRIPT_URL in err["error"]
    assert "pip install" not in err["error"]


def test_source_install_error_keeps_pip_hint(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    err = ba._BrowserController._setup_error(None, ModuleNotFoundError("playwright"))
    assert "pip install playwright" in err["error"]
