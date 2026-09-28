"""Settings ▸ Sandbox readiness and the guided setup job (OPE-207).

The same steps serve the terminal (`openworker machine sandbox status|setup`) and the
page: `setup_cmd.steps()` is the checklist, `SetupJob` walks it and fixes each step it
can on this machine (the OpenShell install included, through sudo-without-password or
the system's password prompt), handing over ONE command for what it cannot. Never holding
a password, never lowering protection.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from coworker.sandbox import setup_cmd, setup_job
from coworker.sandbox.setup_cmd import Step


def _wait(job: setup_job.SetupJob, seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = job.state()
        if state["status"] != "running":
            return state
        time.sleep(0.02)
    raise AssertionError(f"job still running: {job.state()}")


def _steps(*rows: tuple[str, bool]) -> list[Step]:
    # A failing row the app may fix carries nothing; one it may not carries the command.
    return [Step(key, setup_cmd.ROWS[key], ok, fixable=key in setup_cmd.FIXABLE, command="" if ok or key in setup_cmd.FIXABLE else f"fix {key}") for key, ok in rows]


def test_steps_carry_keys_and_say_which_the_app_may_fix(monkeypatch):
    monkeypatch.setattr(setup_cmd.shutil, "which", lambda name: None)  # no docker, no openshell
    monkeypatch.setattr(setup_cmd.sys, "platform", "darwin")
    monkeypatch.setattr(setup_cmd, "openshell_problem", lambda fresh=False: "OpenShell is not installed")
    rows = {s.key: s for s in setup_cmd.steps()}
    assert not rows["docker"].ok and not rows["docker"].fixable and rows["docker"].docs == setup_cmd.DOCKER_INSTALL_URL
    # A Mac needs no administrator (Homebrew installs as the user): the app installs it.
    assert not rows["openshell"].ok and rows["openshell"].fixable and rows["openshell"].command == ""
    assert rows["openshell"].docs == setup_cmd.GUIDE_URL and rows["openshell"].hint == ""
    assert rows["bind_mounts"].fixable and rows["config"].fixable
    assert "linger" not in rows and "landlock" not in rows  # Linux-only rows
    assert "image" not in rows  # no gateway to ask
    assert [(w, ok) for w, ok, _ in setup_cmd.checks()] == [(s.what, s.ok) for s in setup_cmd.steps()]


def test_on_linux_the_install_is_the_apps_when_it_can_run_as_an_administrator(monkeypatch):
    monkeypatch.setattr(setup_cmd.shutil, "which", lambda name: None)
    monkeypatch.setattr(setup_cmd.sys, "platform", "linux")
    monkeypatch.setattr(setup_cmd.getpass, "getuser", lambda: "sam")
    monkeypatch.setattr(setup_cmd, "openshell_problem", lambda fresh=False: "OpenShell is not installed")
    monkeypatch.setattr(setup_cmd, "landlock_available", lambda: True)
    monkeypatch.setattr(setup_cmd, "_run", lambda argv, timeout=600: type("Done", (), {"returncode": 1, "stdout": ""})())  # no loginctl here
    # No way to run as an administrator without a password (WSL): ONE command handed over,
    # linger first, because the installer needs the user's systemd manager.
    monkeypatch.setattr(setup_cmd, "admin_prefix", lambda: None)
    rows = {s.key: s for s in setup_cmd.steps()}
    assert not rows["openshell"].fixable
    assert rows["openshell"].command == f"sudo loginctl enable-linger sam && {setup_cmd.installer_command()}"
    assert rows["openshell"].command.startswith("sudo loginctl enable-linger sam && curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | OPENSHELL_VERSION=v")
    assert rows["linger"].fixable and rows["linger"].command == "sudo loginctl enable-linger sam"
    assert rows["grpcio"].ok or rows["grpcio"].command.startswith("pip install")
    # With one (sudo without a password, or the desktop's prompt): the app installs it.
    monkeypatch.setattr(setup_cmd, "admin_prefix", lambda: ["sudo", "-n"])
    rows = {s.key: s for s in setup_cmd.steps()}
    assert rows["openshell"].fixable and rows["openshell"].command == ""
    # Another version than the pinned one is said, as a note, not a command.
    monkeypatch.setattr(setup_cmd.shutil, "which", lambda name: "/usr/bin/openshell" if name == "openshell" else None)
    monkeypatch.setattr(setup_cmd, "_run", lambda argv, timeout=600: type("Done", (), {"returncode": 0, "stdout": "openshell 0.0.1\n"})())
    rows = {s.key: s for s in setup_cmd.steps()}
    assert rows["openshell"].hint == "found 0.0.1, not the tested release" and rows["openshell"].fixable


def test_admin_prefix_prefers_passwordless_sudo_then_the_desktop_prompt_never_wsl(monkeypatch):
    monkeypatch.setattr(setup_cmd.sys, "platform", "linux")
    monkeypatch.setattr(setup_cmd.getpass, "getuser", lambda: "sam")
    monkeypatch.setattr(setup_cmd.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(setup_cmd, "_run", lambda argv, timeout=600: type("Done", (), {"returncode": 0, "stdout": ""})())
    assert setup_cmd.admin_prefix() == ["sudo", "-n"]
    # sudo wants a password: the desktop's prompt (pkexec) when there is a display and
    # this is not WSL, which has no polkit agent to show one.
    monkeypatch.setattr(setup_cmd, "_run", lambda argv, timeout=600: type("Done", (), {"returncode": 1, "stdout": ""})())
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(setup_cmd, "is_wsl", lambda: False)
    prefix = setup_cmd.admin_prefix()
    assert prefix is not None and prefix[:3] == ["pkexec", "env", "SUDO_USER=sam"]  # the installer sets up sam's gateway, not root's
    monkeypatch.setattr(setup_cmd, "is_wsl", lambda: True)
    assert setup_cmd.admin_prefix() is None
    monkeypatch.setattr(setup_cmd, "is_wsl", lambda: False)
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert setup_cmd.admin_prefix() is None  # headless: nothing can show a prompt
    monkeypatch.setattr(setup_cmd.sys, "platform", "darwin")
    assert setup_cmd.admin_prefix() is None  # a Mac needs none


def test_install_openshell_runs_the_installer_as_the_user_on_a_mac_and_as_an_administrator_on_linux(monkeypatch):
    ran: list[list[str]] = []
    monkeypatch.setattr(setup_cmd, "_stream", lambda argv, on_line, cancel: ran.append(argv) or (0, "done"))
    monkeypatch.setattr(setup_cmd.getpass, "getuser", lambda: "sam")
    monkeypatch.setattr(setup_cmd.sys, "platform", "darwin")
    assert setup_cmd.install_openshell() is None
    assert ran == [["sh", "-c", setup_cmd.installer_command()]]
    ran.clear()
    monkeypatch.setattr(setup_cmd.sys, "platform", "linux")
    monkeypatch.setattr(setup_cmd, "admin_prefix", lambda: ["sudo", "-n"])
    assert setup_cmd.install_openshell() is None
    assert ran == [["sudo", "-n", "sh", "-c", f"loginctl enable-linger sam && {setup_cmd.installer_command()}"]]
    monkeypatch.setattr(setup_cmd, "_stream", lambda argv, on_line, cancel: (1, "checksum mismatch"))
    assert setup_cmd.install_openshell() == "the installer exited with 1: checksum mismatch"
    monkeypatch.setattr(setup_cmd, "_stream", lambda argv, on_line, cancel: (None, ""))
    assert setup_cmd.install_openshell() == "installation cancelled"
    monkeypatch.setattr(setup_cmd, "admin_prefix", lambda: None)
    assert "sudo loginctl enable-linger sam && curl" in (setup_cmd.install_openshell() or "")


def test_the_job_installs_openshell_with_its_output_as_progress(monkeypatch):
    def fake_install(on_line, cancel):
        for line in ("downloading v0.0.116 release checksums...", "installing openshell...", "gateway registered"):
            on_line(line)
            time.sleep(0.02)
        return None

    monkeypatch.setattr(setup_cmd, "install_openshell", fake_install)
    reads = {"n": 0}

    def steps():
        reads["n"] += 1
        return [Step("docker", setup_cmd.ROWS["docker"], True), Step("openshell", setup_cmd.ROWS["openshell"], reads["n"] > 1, fixable=True, docs=setup_cmd.GUIDE_URL)]

    job = setup_job.SetupJob(steps)
    job.start()
    time.sleep(0.03)
    mid = job.state()
    assert mid["status"] == "running" and mid["progress"]["last_line"].startswith(("downloading", "installing"))
    assert {r["key"]: r["state"] for r in mid["rows"]}["openshell"] == "fixing"
    state = _wait(job)
    assert state["status"] == "done" and {r["key"]: r["state"] for r in state["rows"]}["openshell"] == "fixed"


def test_the_job_fixes_what_it_may_and_stops_at_what_needs_an_administrator(monkeypatch):
    fixed: list[str] = []
    monkeypatch.setattr(setup_cmd, "apply_bind_mounts", lambda: fixed.append("bind_mounts") or None)
    monkeypatch.setattr(setup_cmd, "apply_config", lambda: fixed.append("config"))
    calls = {"n": 0}

    def steps():
        calls["n"] += 1
        after = calls["n"] > 1  # the second read (the final re-check) sees the fixes
        return _steps(("docker", True), ("openshell", True), ("bind_mounts", after), ("gateway", True), ("grpcio", True), ("config", after))

    job = setup_job.SetupJob(steps)
    job.start()
    state = _wait(job)
    assert state["status"] == "done" and fixed == ["bind_mounts", "config"]
    by_key = {r["key"]: r for r in state["rows"]}
    assert by_key["bind_mounts"]["state"] == "fixed" and by_key["config"]["state"] == "fixed"
    assert by_key["docker"]["state"] == "ok"

    # An administrator step in the way: the job hands it over and does not go past it.
    fixed.clear()
    job = setup_job.SetupJob(lambda: _steps(("docker", True), ("openshell", False), ("bind_mounts", False), ("config", False)))
    job.start()
    state = _wait(job)
    assert state["status"] == "needs_you" and fixed == []  # nothing after the handover ran
    by_key = {r["key"]: r for r in state["rows"]}
    assert by_key["openshell"]["state"] == "needs_you" and by_key["openshell"]["command"] == "fix openshell"
    assert by_key["bind_mounts"]["state"] == "pending"


def test_the_download_reports_progress_and_can_be_cancelled(monkeypatch):
    def slow_pull(on_progress, cancel):
        p = setup_cmd.PullProgress(layers_total=8)
        for i in range(8):
            if cancel.is_set():
                return "download cancelled"
            p.layers_done = i
            p.last_line = f"layer{i}: Downloading"
            on_progress(p)
            time.sleep(0.02)
        return None

    monkeypatch.setattr(setup_cmd, "pull_image", slow_pull)
    job = setup_job.SetupJob(lambda: _steps(("docker", True), ("openshell", True), ("gateway", True), ("image", False)))
    job.start()
    time.sleep(0.05)
    mid = job.state()
    assert mid["status"] == "running" and mid["progress"]["layers_total"] == 8 and mid["progress"]["layers_done"] >= 1
    job.cancel()
    state = _wait(job)
    assert state["status"] == "cancelled"
    assert {r["key"]: r["state"] for r in state["rows"]}["image"] == "failed"

    # Uncancelled, with the final re-check reporting the image present: done.
    monkeypatch.setattr(setup_cmd, "pull_image", lambda on_progress, cancel: None)
    reads = {"n": 0}

    def steps():
        reads["n"] += 1
        return _steps(("docker", True), ("openshell", True), ("gateway", True), ("image", reads["n"] > 1))

    job = setup_job.SetupJob(steps)
    job.start()
    assert _wait(job)["status"] == "done"


def test_a_refused_linger_is_handed_over_and_the_walk_goes_on(monkeypatch):
    monkeypatch.setattr(setup_job.sys, "platform", "linux")
    monkeypatch.setattr(setup_cmd, "enable_linger", lambda: "sudo loginctl enable-linger sam")
    monkeypatch.setattr(setup_cmd, "apply_config", lambda: None)
    reads = {"n": 0}

    def steps():
        reads["n"] += 1
        return _steps(("docker", True), ("openshell", True), ("linger", False), ("config", reads["n"] > 1))

    job = setup_job.SetupJob(steps)
    job.start()
    state = _wait(job)
    by_key = {r["key"]: r for r in state["rows"]}
    assert by_key["linger"]["state"] == "needs_you" and "sudo loginctl" in by_key["linger"]["command"]
    assert by_key["config"]["state"] == "fixed"  # the walk went on past the handover
    assert state["status"] == "needs_you"  # not done: one row still needs the user


def test_pull_image_parses_docker_output_and_streams_progress(monkeypatch):
    lines = ["c6cd9b8593e5: Pulling fs layer\n", "a6c5096124c1: Pulling fs layer\n", "c6cd9b8593e5: Downloading\n", "c6cd9b8593e5: Pull complete\n", "a6c5096124c1: Pull complete\n", "Digest: sha256:abc\n", "Status: Downloaded newer image\n"]

    class Proc:
        stdout = iter(lines)

        def wait(self):
            return 0

    monkeypatch.setattr(setup_cmd.openshell, "image_tool", lambda: "docker")
    monkeypatch.setattr(setup_cmd.openshell, "sandbox_image", lambda: "img")
    argv: list[list[str]] = []
    monkeypatch.setattr(setup_cmd.subprocess, "Popen", lambda a, **kw: argv.append(a) or Proc())
    seen: list[tuple[int, int]] = []
    assert setup_cmd.pull_image(lambda p: seen.append((p.layers_done, p.layers_total)), threading.Event()) is None
    assert argv == [["docker", "pull", "img"]]
    assert seen[-1] == (2, 2) and (0, 2) in seen


def test_the_readiness_endpoint_and_the_job_endpoints(tmp_path, monkeypatch):
    from coworker.sandbox import settings
    from coworker.server import create_app
    from tests.test_persona_connections import _mgr

    monkeypatch.setattr(setup_cmd, "steps", lambda: _steps(("docker", True), ("openshell", False)))
    monkeypatch.setattr(settings.sys, "platform", "linux")
    monkeypatch.setattr(setup_job, "_job", None)
    client = TestClient(create_app(_mgr(tmp_path, monkeypatch)))
    ready = client.get("/v1/settings/sandbox/readiness").json()
    assert ready["supported"] and not ready["all_ok"]
    assert [(s["key"], s["ok"], s["fixable"], s["command"]) for s in ready["steps"]] == [("docker", True, False, ""), ("openshell", False, False, "fix openshell")]
    assert client.get("/v1/settings/sandbox/setup").json()["status"] == "idle"
    started = client.post("/v1/settings/sandbox/setup").json()
    assert started["status"] in ("running", "needs_you")
    deadline = time.monotonic() + 5
    while client.get("/v1/settings/sandbox/setup").json()["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    final = client.get("/v1/settings/sandbox/setup").json()
    assert final["status"] == "needs_you" and final["rows"][1]["state"] == "needs_you"
    assert client.post("/v1/settings/sandbox/setup/cancel").json()["status"] == "needs_you"  # nothing running: unchanged
    monkeypatch.setattr(settings.sys, "platform", "win32")
    assert client.get("/v1/settings/sandbox/readiness").json() == {"platform": "win32", "supported": False, "steps": [], "all_ok": False}
