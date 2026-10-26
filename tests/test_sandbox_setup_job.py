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
    # The user installs it; the row points at the guide.
    assert not rows["openshell"].ok and not rows["openshell"].fixable and rows["openshell"].command == ""
    assert rows["openshell"].docs == setup_cmd.GUIDE_URL and rows["openshell"].hint == ""
    assert rows["bind_mounts"].fixable and rows["config"].fixable
    assert "linger" not in rows and "landlock" not in rows  # Linux-only rows
    assert "image" not in rows  # no gateway to ask
    assert [(w, ok) for w, ok, _ in setup_cmd.checks()] == [(s.what, s.ok) for s in setup_cmd.steps()]


def test_a_hung_check_counts_as_not_ok_instead_of_failing_the_checklist(monkeypatch):
    """`docker info` hangs while Docker Desktop is installed but not running (seen on a Mac,
    2026-09-28): the row says Docker is missing, and the checklist still loads."""
    import subprocess

    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(setup_cmd.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(setup_cmd.subprocess, "run", hang)
    monkeypatch.setattr(setup_cmd.sys, "platform", "darwin")
    monkeypatch.setattr(setup_cmd, "openshell_problem", lambda fresh=False: "OpenShell is not installed")
    rows = {s.key: s for s in setup_cmd.steps()}
    assert not rows["docker"].ok and not rows["openshell"].ok
    assert setup_cmd._run(["docker", "info"], 1).returncode == 124


def test_the_app_never_installs_openshell_it_points_at_the_guide(monkeypatch):
    """UX-053 v6: the user installs Docker and OpenShell; the row links to NVIDIA's guide."""
    monkeypatch.setattr(setup_cmd.shutil, "which", lambda name: None)
    monkeypatch.setattr(setup_cmd.sys, "platform", "linux")
    monkeypatch.setattr(setup_cmd.getpass, "getuser", lambda: "sam")
    monkeypatch.setattr(setup_cmd, "openshell_problem", lambda fresh=False: "OpenShell is not installed")
    monkeypatch.setattr(setup_cmd, "landlock_available", lambda: True)
    monkeypatch.setattr(setup_cmd, "_run", lambda argv, timeout=600: type("Done", (), {"returncode": 1, "stdout": ""})())  # no loginctl here
    monkeypatch.setattr(setup_cmd, "admin_prefix", lambda: ["sudo", "-n"])  # even where it could
    rows = {s.key: s for s in setup_cmd.steps()}
    assert not rows["openshell"].fixable and rows["openshell"].command == "" and rows["openshell"].docs == setup_cmd.GUIDE_URL
    assert rows["linger"].fixable and rows["linger"].command == "sudo loginctl enable-linger sam"
    # Another version than the pinned one is said, as a note.
    monkeypatch.setattr(setup_cmd.shutil, "which", lambda name: "/usr/bin/openshell" if name == "openshell" else None)
    monkeypatch.setattr(setup_cmd, "_run", lambda argv, timeout=600: type("Done", (), {"returncode": 0, "stdout": "openshell 0.0.1\n"})())
    rows = {s.key: s for s in setup_cmd.steps()}
    assert rows["openshell"].hint == "found 0.0.1, not the tested release" and not rows["openshell"].fixable

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


def test_the_folder_step_restarts_the_gateway_and_the_check_reads_the_config_in_use(tmp_path, monkeypatch):
    """Seen on a Mac, 2026-09-28: the setting was written but the Homebrew gateway kept the
    config it had started with, the check said yes, and sessions were refused."""
    from types import SimpleNamespace

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    ran: list[list[str]] = []
    listing = {"out": "/bin/zsh\n/opt/homebrew/opt/openshell/bin/openshell-gateway --config /opt/homebrew/var/openshell/gateway.toml\n"}

    def fake_run(argv, timeout=600):
        ran.append(argv)
        return SimpleNamespace(returncode=0, stdout=listing["out"] if argv[0] == "ps" else "", stderr="")

    monkeypatch.setattr(setup_cmd, "_run", fake_run)
    monkeypatch.setattr(setup_cmd.sys, "platform", "darwin")
    monkeypatch.setattr(setup_cmd.shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")
    # Started with Homebrew's own config: not in use yet, whatever is on disk.
    assert setup_cmd.gateway_config_in_use() == setup_cmd.Path("/opt/homebrew/var/openshell/gateway.toml")
    assert setup_cmd.apply_bind_mounts() is None
    assert ["brew", "services", "restart", "nvidia/openshell/openshell"] in ran
    ours = tmp_path / "openshell" / "gateway.toml"
    written = ours.read_text()
    assert "version = 2" in written and "allow_driver_config = true" in written and "enable_bind_mounts = true" in written
    assert "[openshell.drivers.docker.resource_admission]\nenabled = false" in written
    # Restarted: no --config, so `gateway.env` names the file, and the check is satisfied.
    listing["out"] = "/opt/homebrew/opt/openshell/bin/openshell-gateway\n"
    assert setup_cmd.gateway_config_in_use() == ours and setup_cmd._allows_bind_mounts(ours)
    # Running it again is fine: the setting is there, only the restart happens.
    ran.clear()
    assert setup_cmd.apply_bind_mounts() is None and ours.read_text().count("enable_bind_mounts") == 1
    assert ["brew", "services", "restart", "nvidia/openshell/openshell"] in ran
    # Linux restarts the user unit.
    monkeypatch.setattr(setup_cmd.sys, "platform", "linux")
    ran.clear()
    assert setup_cmd.restart_gateway() is None and ran == [["systemctl", "--user", "restart", "openshell-gateway"]]


def test_on_a_mac_the_docker_kernel_is_asked_for_landlock(monkeypatch):
    """Docker Desktop 28.0.4 (kernel 6.10.14-linuxkit) has no Landlock and every OpenShell
    sandbox exited at start (2026-09-28); 29.8.1 (7.0.14) has it."""
    from types import SimpleNamespace

    answers = {"probe": "-1\n", "image": 0}

    def fake_run(argv, timeout=600):
        if argv[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=answers["image"], stdout="", stderr="")
        if argv[:2] == ["docker", "run"]:
            assert "--network" in argv and "none" in argv  # a throwaway probe, no network
            return SimpleNamespace(returncode=0, stdout=answers["probe"], stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(setup_cmd, "_run", fake_run)
    monkeypatch.setattr(setup_cmd.sys, "platform", "darwin")
    monkeypatch.setattr(setup_cmd.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    assert setup_cmd.docker_landlock() is False
    answers["probe"] = "8\n"
    assert setup_cmd.docker_landlock() is True
    answers["image"] = 1  # the base image is not here yet: cannot ask, no row
    assert setup_cmd.docker_landlock() is None
    monkeypatch.setattr(setup_cmd.sys, "platform", "linux")
    assert setup_cmd.docker_landlock() is None  # Linux asks its own kernel (the `landlock` row)
    # In the checklist: a row right after Docker, with what to do.
    monkeypatch.setattr(setup_cmd.sys, "platform", "darwin")
    monkeypatch.setattr(setup_cmd, "docker_landlock", lambda: False)
    monkeypatch.setattr(setup_cmd, "openshell_problem", lambda fresh=False: "OpenShell is not installed")
    keys = [s.key for s in setup_cmd.steps()]
    row = {s.key: s for s in setup_cmd.steps()}["docker_landlock"]
    assert keys[:2] == ["docker", "docker_landlock"] and not row.ok and not row.fixable
    assert row.hint.startswith("Update Docker Desktop") and row.docs == setup_cmd.DOCKER_DESKTOP_URL


def test_folder_sharing_needs_all_three_gateway_settings(tmp_path):
    """OpenShell 0.1: driver config on, bind mounts on, resource admission off."""
    config = tmp_path / "gateway.toml"
    config.write_text(setup_cmd._BIND_MOUNTS)
    assert setup_cmd._allows_bind_mounts(config)
    for missing in ("allow_driver_config = true\n", "enable_bind_mounts = true\n", "enabled = false\n"):
        config.write_text(setup_cmd._BIND_MOUNTS.replace(missing, ""))
        assert not setup_cmd._allows_bind_mounts(config), missing
    # The file our setup wrote for OpenShell 0.0.x is not enough, and is not valid for 0.1.
    config.write_text("[openshell.drivers.docker]\nenable_bind_mounts = true\n")
    assert not setup_cmd._allows_bind_mounts(config)
    config.write_text("not toml [")
    assert not setup_cmd._allows_bind_mounts(config)


def test_a_gateway_config_with_other_settings_is_not_rewritten(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    theirs = tmp_path / "openshell" / "gateway.toml"
    theirs.parent.mkdir(parents=True)
    theirs.write_text("[openshell]\nversion = 2\n\n[openshell.gateway]\ncompute_driver = \"docker\"\n")
    said = setup_cmd.apply_bind_mounts()
    assert said and "by hand" in said and "allow_driver_config = true" in said
    assert "compute_driver" in theirs.read_text() and "enable_bind_mounts" not in theirs.read_text()


def test_a_mac_is_asked_whether_docker_desktops_host_networking_is_on(monkeypatch):
    """OpenShell 0.1 reaches its gateway from a host-network container; Docker Desktop
    has that off until it is ticked."""
    from pathlib import Path
    from types import SimpleNamespace

    ran: list[list[str]] = []
    answers = {"image": 0, "probe": 1}

    def fake_run(argv, timeout=600):
        ran.append(list(argv))
        if argv[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=answers["image"], stdout="", stderr="")
        if argv[:2] == ["docker", "run"]:
            assert argv[argv.index("--network") + 1] == "host"
            return SimpleNamespace(returncode=answers["probe"], stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    class Listening:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(setup_cmd, "_run", fake_run)
    monkeypatch.setattr(setup_cmd.sys, "platform", "darwin")
    monkeypatch.setattr(setup_cmd.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(setup_cmd.openshell, "_gateway", lambda: ("localhost:17670", Path("/nowhere")))
    monkeypatch.setattr(setup_cmd.socket, "create_connection", lambda *_a, **_k: Listening())
    assert setup_cmd.docker_host_network() is False  # the container could not reach it
    answers["probe"] = 0
    assert setup_cmd.docker_host_network() is True
    answers["image"] = 1  # no base image yet: cannot ask
    assert setup_cmd.docker_host_network() is None

    def refused(*_a, **_k):
        raise OSError("connection refused")

    answers["image"] = 0
    monkeypatch.setattr(setup_cmd.socket, "create_connection", refused)
    assert setup_cmd.docker_host_network() is None  # no gateway to reach: that is another row
    monkeypatch.setattr(setup_cmd.sys, "platform", "linux")
    assert setup_cmd.docker_host_network() is None  # Docker Engine on Linux has host networking

    # In the checklist: its own row, with where the setting is.
    monkeypatch.setattr(setup_cmd.sys, "platform", "darwin")
    monkeypatch.setattr(setup_cmd, "docker_landlock", lambda: True)
    monkeypatch.setattr(setup_cmd, "docker_host_network", lambda: False)
    monkeypatch.setattr(setup_cmd, "openshell_problem", lambda fresh=False: "OpenShell is not installed")
    row = {s.key: s for s in setup_cmd.steps()}["docker_host_network"]
    assert not row.ok and not row.fixable and "Enable host networking" in row.hint and row.docs == setup_cmd.DOCKER_HOST_NETWORK_URL

