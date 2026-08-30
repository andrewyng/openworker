"""`readiness()` — whether the rail's "Browser" section is telling the truth.

The connector declares `auth: "none"`, so everything upstream reports it connected. This probe
is the only thing standing between that and a rail that advertises a capability whose first
tool call dies. A probe that answers "ready" for an install that cannot launch is worse than no
probe at all: it moves the lie one step later, where it costs a whole turn to discover.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright")

from coworker.connectors.browser_automation import (  # noqa: E402
    _chromium_revision,
    readiness,
)


@pytest.fixture
def cache(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "ms-playwright"
    d.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(d))
    monkeypatch.setenv("DISPLAY", ":0")  # the headless-box branch is a separate concern
    return d


def _install(cache: Path, build: str, binary: str = "chrome-linux64/chrome") -> Path:
    exe = cache / build / binary
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    return exe


def test_a_real_install_is_ready(cache):
    _install(cache, f"chromium-{_chromium_revision()}")
    out = readiness()
    assert out["ready"] and out["browsers"] and out["fix"] == []


def test_a_headless_shell_only_install_is_not_ready(cache):
    # `playwright install --only-shell chromium` leaves this and nothing else. The directory
    # name starts with "chromium", so a name glob called it ready; the shell cannot launch
    # headed, and page() launches headed.
    _install(
        cache,
        f"chromium_headless_shell-{_chromium_revision()}",
        "chrome-headless-shell-linux64/chrome-headless-shell",
    )
    out = readiness()
    assert not out["ready"] and not out["browsers"]
    assert "chromium_headless_shell" in out["detail"]
    assert out["fix"] == ["python -m playwright install chromium"]


def test_a_build_left_behind_by_a_package_upgrade_is_not_ready(cache):
    # Upgrading the playwright package without re-running the installer: the old revision is
    # still on disk, the driver now asks for a newer one, and every launch fails with
    # "Executable doesn't exist at .../chromium-<new>/chrome-linux64/chrome".
    stale = str(int(_chromium_revision() or "1234") - 100)
    _install(cache, f"chromium-{stale}")
    out = readiness()
    assert not out["ready"] and not out["browsers"]
    assert f"chromium-{stale}" in out["detail"] and _chromium_revision() in out["detail"]


def test_an_empty_cache_says_nothing_is_downloaded(cache):
    out = readiness()
    assert not out["ready"] and "no Chromium build is downloaded yet" in out["detail"]


def _no_user_session(monkeypatch):
    """No display in this process AND none to recover from the systemd user manager.

    Deleting the env vars is no longer enough on its own: since 2026-08-30 the probe asks
    the user manager too, because the server that runs it is started at boot and never has a
    DISPLAY of its own. "Headless" now means both doors are shut.
    """
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    class _Empty:
        stdout = ""

    monkeypatch.setattr(
        "coworker.desktop.subprocess.run", lambda *a, **k: _Empty()
    )


def test_a_headed_binary_with_no_display_is_still_not_ready(cache, monkeypatch):
    _install(cache, f"chromium-{_chromium_revision()}")
    _no_user_session(monkeypatch)
    out = readiness()
    # The binary is real; nothing can display it. Both halves have to hold to claim ready.
    assert not out["ready"] and out["browsers"] and "display" in out["detail"]


def test_a_display_the_process_never_inherited_still_counts(cache, monkeypatch):
    """The 2026-08-30 case: the server is started by systemd at boot, so it has no DISPLAY of
    its own for the whole uptime — while the user's screen is right there. Reading os.environ
    alone reported "no display" on a box that could display it perfectly well."""
    _install(cache, f"chromium-{_chromium_revision()}")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    class _Session:
        stdout = "LANG=en_US.UTF-8\nDISPLAY=:1\nXAUTHORITY=/run/user/1000/gdm/Xauthority\n"

    monkeypatch.setattr(
        "coworker.desktop.subprocess.run", lambda *a, **k: _Session()
    )
    out = readiness()
    assert out["ready"], out["detail"]
