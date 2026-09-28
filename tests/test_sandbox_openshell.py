"""The OpenShell provider.

The first half runs anywhere: policy rendering, the hand-made stream messages, the refusal
messages. The second half needs a machine with OpenShell running and is skipped unless
`OPENWORKER_TEST_OPENSHELL=1` (it creates real sandboxes; see the design doc, section 11).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

from coworker.sandbox.providers import openshell, openshell_policy as policy, openshell_wire as wire

ROOTS = [{"path": "/home/sam/code/app", "writable": True}, {"path": "/home/sam/reference", "writable": False}]


# -- anywhere -----------------------------------------------------------------------------


def test_policy_lists_every_folder_and_nothing_else_of_the_machine():
    rendered = policy.render(ROOTS, uid=1000, gid=1000)
    fs = rendered["filesystem_policy"]
    assert "/home/sam/code/app" in fs["read_write"] and "/home/sam/code/app" not in fs["read_only"]
    assert "/home/sam/reference" in fs["read_only"] and "/home/sam/reference" not in fs["read_write"]
    assert policy.RUNNER_MOUNT in fs["read_only"]  # the agent cannot change the runner
    assert not any(p.startswith("/home/") and p not in {r["path"] for r in ROOTS} for p in fs["read_only"] + fs["read_write"])
    assert rendered["landlock"]["compatibility"] == "hard_requirement"  # never run open
    assert rendered["process"] == {"run_as_user": "1000", "run_as_group": "1000"}  # the folders' owner


def test_policy_is_plain_yaml_without_shared_objects():
    text = yaml.safe_dump(policy.render(ROOTS), sort_keys=False)
    assert "&id" not in text and "*id" not in text


def test_network_profiles():
    strict = policy.render(ROOTS, profile="strict")["network_policies"]
    standard = policy.render(ROOTS, profile="standard")["network_policies"]
    hosts = lambda p: {e["host"] for entry in p.values() for e in entry["endpoints"]}  # noqa: E731
    assert {"github.com", "pypi.org", "registry.npmjs.org"} <= hosts(strict)
    assert "api.tavily.com" not in hosts(strict) and "api.tavily.com" in hosts(standard)
    assert all(entry["binaries"] for entry in strict.values())  # OpenShell requires the field
    opened = policy.render(ROOTS, profile="open")["network_policies"]
    assert [e["host"] for e in opened["open"]["endpoints"]] == ["*"]  # any host; unproved against a gateway
    with pytest.raises(ValueError):
        policy.render(ROOTS, profile="wide-open")


def test_folders_are_mounted_at_the_same_absolute_path():
    mounts = policy.mounts(ROOTS, "/var/lib/openworker/sandbox")["docker"]["mounts"]
    assert {"type": "bind", "source": "/home/sam/code/app", "target": "/home/sam/code/app", "read_only": False} in mounts
    assert {"type": "bind", "source": "/home/sam/reference", "target": "/home/sam/reference", "read_only": True} in mounts
    assert {"type": "bind", "source": "/var/lib/openworker/sandbox", "target": policy.RUNNER_MOUNT, "read_only": True} in mounts


def test_stream_messages_match_the_protobuf_wire_format():
    # ExecSandboxInput{start: ExecSandboxRequest{sandbox_id:"abc", command:["bash","-c"]}}
    assert wire.encode_start("abc", ["bash", "-c"]).hex() == "0a0f0a0361626312046261736812022d63"
    assert wire.encode_stdin(b"hi\n").hex() == "1203" + b"hi\n".hex()
    assert wire.decode_event(bytes.fromhex("0a070a0568656c6c6f")) == ("stdout", b"hello", None)
    assert wire.decode_event(bytes.fromhex("12050a03657272")) == ("stderr", b"err", None)
    assert wire.decode_event(bytes.fromhex("1a020803")) == ("exit", None, 3)
    assert wire.decode_event(bytes.fromhex("1a00")) == ("exit", None, 0)
    big = b"x" * 70000  # a length that needs a three-byte varint
    kind, data, _ = wire.decode_event(wire._field(1, wire._field(1, big)))
    assert (kind, data) == ("stdout", big)


def test_missing_openshell_is_refused_with_a_message_a_person_can_act_on(monkeypatch):
    monkeypatch.setattr(openshell.shutil, "which", lambda name: None)
    with pytest.raises(openshell.OpenShellUnavailable) as err:
        openshell.preflight()
    assert "not installed" in str(err.value) and "sandbox setup" in str(err.value)


def _fake_cli(answers):
    """`subprocess.run` answering by the first argument after the program: `--version`,
    `status`, `gateway` (info), and `image` (docker image inspect)."""

    def run(argv, **kwargs):
        assert kwargs.get("stdin") == subprocess.DEVNULL  # an open stdin hangs the CLI
        out, code = answers[argv[1]]
        return subprocess.CompletedProcess(argv, code, out, "")

    return run


_OK_VERSION = (f"openshell {openshell.PINNED_VERSION}\n", 0)
_CONNECTED = ("Status: Connected", 0)
_DOCKER_GATEWAY = ('{"compute_drivers": [{"name": "docker"}], "status": "healthy"}', 0)


def test_another_version_or_a_stopped_gateway_is_refused(monkeypatch):
    monkeypatch.setattr(openshell.shutil, "which", lambda name: "/usr/bin/openshell")
    monkeypatch.setattr(openshell.subprocess, "run", _fake_cli({"--version": ("openshell 9.9.9\n", 0)}))
    with pytest.raises(openshell.OpenShellUnavailable, match="tested with"):
        openshell.preflight()
    monkeypatch.setattr(openshell.subprocess, "run", _fake_cli({"--version": _OK_VERSION, "status": ("Status: Disconnected", 1)}))
    with pytest.raises(openshell.OpenShellUnavailable, match="not running"):
        openshell.preflight()
    present = {"--version": _OK_VERSION, "status": _CONNECTED, "gateway": _DOCKER_GATEWAY, "image": ("[{...}]", 0)}
    monkeypatch.setattr(openshell.subprocess, "run", _fake_cli(present))
    assert openshell.preflight() == {"version": openshell.PINNED_VERSION}


def test_a_missing_base_image_is_refused_before_a_create_can_hang_on_it(monkeypatch):
    # OPE-205: the first `sandbox create` pulls about 5 GB; on a slow link that outlives the
    # create's timeout and the session hangs for ten minutes with no word about why. So the
    # image is checked up front, with a message that names the download.
    monkeypatch.setattr(openshell, "_gateway_is_remote", lambda: False)
    monkeypatch.setattr(openshell.shutil, "which", lambda name: f"/usr/bin/{name}")
    missing = {"--version": _OK_VERSION, "status": _CONNECTED, "gateway": _DOCKER_GATEWAY, "image": ("Error: No such image", 1)}
    monkeypatch.setattr(openshell.subprocess, "run", _fake_cli(missing))
    with pytest.raises(openshell.OpenShellImageMissing) as err:
        openshell.preflight()
    text = str(err.value)
    assert openshell.is_image_problem(text) and "sandbox setup" in text and f"docker pull {openshell.DEFAULT_IMAGE}" in text
    assert isinstance(err.value, openshell.OpenShellUnavailable)  # callers that refuse sessions need no new branch
    assert not openshell.is_image_problem("The OpenShell gateway is not running") and not openshell.is_image_problem(None)
    # The check is keyed on the gateway's driver, not the OS: with another driver (a Mac's
    # MicroVM, say) Docker cannot be asked, and the preflight passes as before.
    vm_gateway = ('{"compute_drivers": [{"name": "vm"}]}', 0)
    monkeypatch.setattr(openshell.subprocess, "run", _fake_cli({"--version": _OK_VERSION, "status": _CONNECTED, "gateway": vm_gateway}))
    assert openshell.image_present() is None
    assert openshell.preflight() == {"version": openshell.PINNED_VERSION}
    # No `docker` command on PATH: also "cannot tell", never a false refusal.
    monkeypatch.setattr(openshell.shutil, "which", lambda name: "/usr/bin/openshell" if name == "openshell" else None)
    monkeypatch.setattr(openshell.subprocess, "run", _fake_cli({"--version": _OK_VERSION, "status": _CONNECTED, "gateway": _DOCKER_GATEWAY}))
    assert openshell.image_present() is None
    # The environment's image is the one asked for.
    seen: list[str] = []
    monkeypatch.setattr(openshell.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(openshell.subprocess, "run", lambda argv, **kw: seen.append(argv[-1]) or _fake_cli({"gateway": _DOCKER_GATEWAY, "image": ("", 0)})(argv, **kw))
    monkeypatch.setenv("OPENWORKER_SANDBOX_IMAGE", "registry.example.com/team/agent:2")
    assert openshell.image_present() is True and seen[-1] == "registry.example.com/team/agent:2"


def test_the_image_check_follows_the_driver_and_stays_out_of_remote_gateways(monkeypatch):
    # Podman keeps its own image store, so it is asked with its own command; the same
    # image serves both, and both hang the first session the same way when it is absent.
    monkeypatch.setattr(openshell, "_gateway_is_remote", lambda: False)
    monkeypatch.setattr(openshell.shutil, "which", lambda name: f"/usr/bin/{name}")
    podman = ('{"compute_drivers": [{"name": "podman"}]}', 0)
    seen: list[list[str]] = []
    monkeypatch.setattr(openshell.subprocess, "run", lambda argv, **kw: seen.append(argv) or _fake_cli({"gateway": podman, "image": ("", 1)})(argv, **kw))
    assert openshell.image_tool() == "podman"
    assert openshell.image_present() is False and seen[-1][:3] == ["podman", "image", "inspect"]
    monkeypatch.setattr(openshell.subprocess, "run", _fake_cli({"--version": _OK_VERSION, "status": _CONNECTED, "gateway": podman, "image": ("", 1)}))
    with pytest.raises(openshell.OpenShellImageMissing, match="podman pull"):
        openshell.preflight()
    # A remote gateway keeps its images on another machine: a local store says nothing
    # about it, so the check answers "cannot tell" and never refuses.
    monkeypatch.setattr(openshell, "_gateway_is_remote", lambda: True)
    assert openshell.image_tool() is None and openshell.image_present() is None
    assert openshell.preflight() == {"version": openshell.PINNED_VERSION}


def test_registry_counts_caps_and_forgets(tmp_path, monkeypatch):
    from coworker.sandbox.registry import SandboxLimitReached, SandboxRegistry

    reg = SandboxRegistry(tmp_path / "registry.db")
    reg.record("ow-a", provider="openshell", session_id="s1", agent="lead", roots=ROOTS, profile="strict", enforcement="full")
    reg.record("ow-b", provider="openshell", session_id="s1", agent="worker")
    assert reg.count() == 2 and reg.find("s1", "worker")["name"] == "ow-b"
    assert reg.list()[0]["roots"] == ROOTS and reg.list()[0]["machine_id"] == "local"
    monkeypatch.setenv("OPENWORKER_SANDBOX_MAX", "2")
    with pytest.raises(SandboxLimitReached, match="limit is 2"):
        reg.check_room()
    reg.close("ow-a")
    reg.check_room()
    assert [r["name"] for r in reg.list()] == ["ow-b"]


def test_reap_leaves_a_sandbox_that_a_living_server_is_still_creating(tmp_path, monkeypatch):
    # Engine builds run concurrently (OPE-206). A sandbox that exists at the gateway but was
    # not yet connected used to look like an orphan to another build's reap() and got
    # deleted mid-provisioning ("The operation was cancelled: stream closed"). The name is
    # now reserved in the registry, state "creating", before the sandbox is created.
    from coworker.sandbox import registry as registry_mod

    reg = registry_mod.SandboxRegistry(tmp_path / "registry.db")
    reg.record("ow-building", provider="openshell", session_id="s1", state="creating")  # this server, alive
    monkeypatch.setattr(registry_mod, "_openshell_present", lambda: True)
    monkeypatch.setattr(openshell, "list_our_sandboxes", lambda: [{"name": "ow-building"}, {"name": "ow-orphan"}])
    deleted: list[str] = []
    monkeypatch.setattr(openshell, "_cli", lambda *args, **kw: deleted.append(args[2]))
    assert reg.reap() == ["ow-orphan"]
    assert deleted == ["ow-orphan"]  # the one being built by a live server is left alone
    assert reg.find("s1")["state"] == "creating"


def test_a_workspace_reserves_its_name_before_creating_the_sandbox(tmp_path):
    from coworker.sandbox import registry as registry_mod
    from coworker.sandbox.workspace import RunnerWorkspace

    reg = registry_mod.SandboxRegistry(tmp_path / "registry.db")
    seen: dict = {}

    class Provider:
        roots, profile = [{"path": str(tmp_path), "writable": True}], "strict"

        def describe(self):
            return {"provider": "openshell", "enforcement": "full", "sandbox": "ow-reserved"}

        def create(self):
            seen["row_at_create"] = reg.find("s9")  # what reap() would see while we provision
            raise RuntimeError("create failed on purpose")

        def destroy(self):
            seen["destroyed"] = True

    with pytest.raises(RuntimeError, match="on purpose"):
        RunnerWorkspace(Provider(), cwd=tmp_path, registry=reg, session_id="s9", agent="lead")
    assert seen["row_at_create"]["name"] == "ow-reserved" and seen["row_at_create"]["state"] == "creating"
    assert seen["destroyed"] and reg.find("s9") is None  # a failed create leaves no reservation behind


def test_the_private_folder_is_visible_to_the_gateway_and_to_no_sandbox(tmp_path, monkeypatch):
    # OPE-208: copied credentials used to go under /tmp, which the installer's gateway
    # (PrivateTmp=true) cannot see, so a session with a shared credential failed to create.
    # The folder is now under the state dir, in a sibling of `sandbox/`: `sandbox/` itself
    # is mounted read-only into every sandbox, so a copy there would leak across sessions.
    from coworker.sandbox import bundle

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bundle, "build_runner_zipapp", lambda: tmp_path / "sandbox" / "runner-x.pyz")
    monkeypatch.setattr(openshell, "build_runner_zipapp", lambda: tmp_path / "sandbox" / "runner-x.pyz")
    provider = openshell.OpenShellProvider(roots=ROOTS)
    private = Path(provider._tmp)
    assert private.parent == tmp_path / openshell.RUNTIME_DIR_NAME
    assert not str(private).startswith(str(tmp_path / "sandbox") + os.sep)  # not inside the mounted folder
    # Not in the system temp dir itself (the old place). The state dir under test may well
    # live under /tmp (pytest's default basetemp on CI), so compare parents, not prefixes.
    assert private.parent != Path(tempfile.gettempdir()).resolve() and private.parent.name == openshell.RUNTIME_DIR_NAME
    assert private.name.startswith(f"ow-openshell-{os.getpid()}-")
    if os.name != "nt":
        assert (private.parent.stat().st_mode & 0o777) == 0o700
    # A dead server's folders are swept; a live one's are kept.
    stale = private.parent / "ow-openshell-424242-abc"
    stale.mkdir()
    assert openshell.reap_runtime_dirs(lambda pid: pid == os.getpid()) == [stale.name]
    assert private.exists() and not stale.exists()
    provider.destroy = lambda: shutil.rmtree(private, ignore_errors=True)  # no gateway here
    provider.destroy()
    assert not private.exists()


def test_registry_reaps_rows_of_a_server_that_is_gone(tmp_path, monkeypatch):
    from coworker.sandbox import registry as registry_mod

    reg = registry_mod.SandboxRegistry(tmp_path / "registry.db")
    reg.record("owr-local-1", provider="runner-local", session_id="s1")
    reg.record("owr-local-2", provider="runner-local", session_id="s2")
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    with reg._connect() as db:
        db.execute("UPDATE sandboxes SET server_pid = ? WHERE name = ?", (gone.pid, "owr-local-1"))
    monkeypatch.setattr(registry_mod, "_openshell_present", lambda: False)
    assert reg.reap() == ["owr-local-1"]
    assert [r["name"] for r in reg.list()] == ["owr-local-2"]  # its server (this process) lives


# -- on a machine with OpenShell -----------------------------------------------------------

live = pytest.mark.skipif(os.environ.get("OPENWORKER_TEST_OPENSHELL") != "1", reason="needs a running OpenShell gateway")


@pytest.fixture
def folder():
    """A real folder outside the image's own paths, owned by this user."""
    path = Path(os.environ.get("OPENWORKER_TEST_FOLDER", "/home/sam/code")) / f"t-{os.getpid()}-{int(time.time())}"
    path.mkdir(parents=True)
    yield path
    subprocess.run(["rm", "-rf", str(path)], check=False)


def _open(folder, **kwargs):
    from coworker.roots import RootDir
    from coworker.sandbox.workspace import open_workspace

    roots = kwargs.pop("roots", None) or [RootDir(path=folder, writable=True)]
    return open_workspace(cwd=folder, provider="openshell", roots=roots, **kwargs), roots


@live
def test_a_session_in_a_real_sandbox(folder):
    (folder / "hello.txt").write_text("from the machine\n")
    ws, roots = _open(folder, session_id="s-test", agent="swe-lead")
    try:
        assert ws.describe()["enforcement"] == "full" and ws.describe()["runner"]["os"] == "Linux"
        row = ws.registry.find("s-test", "swe-lead")
        assert row["name"] == ws.provider.sandbox_name and row["enforcement"] == "full" and row["profile"] == "strict"
        ex = ws.executor
        assert ex.run("cat hello.txt && cd /tmp && export KEEP=yes")["exit_code"] == 0
        assert ex.run("echo $KEEP $PWD")["output"].split() == ["yes", "/tmp"]  # one persistent shell
        assert ex.run(f"echo made-inside > {folder}/out.txt")["exit_code"] == 0
        assert (folder / "out.txt").read_text() == "made-inside\n"  # same path, the real folder
        assert (folder / "out.txt").stat().st_uid == os.getuid()  # and the right owner
        outside = ex.run(f"ls {Path.home()} 2>&1; cat {Path.home()}/.ssh/authorized_keys 2>&1")
        assert "Permission denied" in outside["output"] or "No such file" in outside["output"]
        blocked = ex.run("curl -sS -m 8 -o /dev/null -w '%{http_code}' https://example.com; echo")
        assert blocked["output"].strip().endswith("000")  # not in the strict profile
        allowed = ex.run("curl -sS -m 15 -o /dev/null -w '%{http_code}' https://api.github.com/zen; echo")
        assert allowed["output"].strip().endswith("200")

        from coworker.agents.base import AgentContext
        from coworker import catalog

        tools = {t.__name__: t for t in catalog.expand(["code_files", "search"], AgentContext(workspace=folder, executor=ex, roots=roots, sandbox=ws))}
        tools["write_file"]("notes/a.md", "TODO: written by a tool\n")
        assert "written by a tool" in tools["read_file"]("notes/a.md")["content"]
        assert tools["grep"]("TODO")["count"] == 1
        with pytest.raises(PermissionError):
            tools["write_file"]("/etc/owned", "x")
    finally:
        ws.close()
    assert ws.registry.find("s-test") is None  # forgotten when the session's workspace closes
    assert ws.provider.sandbox_name not in subprocess.run(["openshell", "sandbox", "list"], stdin=subprocess.DEVNULL, capture_output=True, text=True).stdout


@live
def test_a_cut_stream_resumes_in_a_real_sandbox(folder):
    ws, _ = _open(folder)
    try:
        ex = ws.executor
        ex.run("cd /tmp && export KEEP=yes")
        box: dict = {}
        worker = threading.Thread(target=lambda: box.update(ex.run("sleep 3; echo finished-while-away", timeout=60)))
        worker.start()
        time.sleep(1.0)
        ws.client._transport.close()  # the gRPC stream is cut while the command runs
        worker.join(timeout=60)
        assert "finished-while-away" in box.get("output", "")
        assert ws.client.restarts == 0
        assert ex.run("echo $KEEP $PWD")["output"].split() == ["yes", "/tmp"]
    finally:
        ws.close()


@live
def test_two_agents_share_a_folder_and_a_read_only_folder_stays_read_only(folder):
    from coworker.roots import RootDir

    shared, reference = folder / "shared", folder / "reference"
    shared.mkdir()
    reference.mkdir()
    (reference / "spec.md").write_text("the spec\n")
    roots = [RootDir(path=shared, writable=True), RootDir(path=reference, writable=False)]
    lead, _ = _open(shared, roots=roots, session_id="s-team", agent="lead")
    worker, _ = _open(shared, roots=roots, session_id="s-team", agent="worker")
    try:
        assert lead.provider.sandbox_name != worker.provider.sandbox_name  # one sandbox per agent
        lead.executor.run("echo from-lead > handoff.txt")
        assert worker.executor.run("cat handoff.txt")["output"].strip() == "from-lead"
        assert worker.executor.run(f"cat {reference}/spec.md")["output"].strip() == "the spec"
        refused = worker.executor.run(f"echo x > {reference}/new.txt")
        assert refused["exit_code"] != 0 and not (reference / "new.txt").exists()
    finally:
        lead.close()
        worker.close()


@live
def test_a_sandbox_left_behind_by_a_dead_server_is_removed(folder):
    """A server that crashed cannot delete its sandbox. The next one does."""
    from coworker.sandbox.providers.openshell import OpenShellProvider, list_our_sandboxes
    from coworker.sandbox.registry import SandboxRegistry

    orphan = OpenShellProvider(roots=[{"path": str(folder), "writable": True}], label="orphan")
    orphan.create()  # created, never recorded by a living server
    names = lambda: {str(s.get("name")) for s in list_our_sandboxes()}  # noqa: E731
    assert orphan.sandbox_name in names()
    removed = SandboxRegistry().reap()
    assert orphan.sandbox_name in removed and orphan.sandbox_name not in names()


@live
def test_credential_grants_are_copied_into_a_real_sandbox(folder):
    """A fake `.ssh` and `.config/gh` under a made-up home are granted: inside, HOME is the
    copy, the files are readable, git and ssh are pointed at the copy, the grant's hosts are
    in the policy, and the real home stays hidden (design doc, section 11b)."""
    from coworker.sandbox import credentials as creds

    home = folder / "fakehome"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE KEY\n")
    (home / ".ssh" / "id_ed25519.pub").write_text("ssh-ed25519 AAAA test\n")
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".config" / "gh" / "hosts.yml").write_text("github.com:\n  oauth_token: gho_x\n")
    (home / ".gitconfig").write_text("[user]\n\tname = Sam\n\temail = sam@example.com\n")
    grants = creds.granted([{"name": "ssh", "enabled": True}, {"name": "gh", "enabled": True}], home=str(home))
    project = folder / "project"
    project.mkdir()
    from coworker.roots import RootDir
    from coworker.sandbox.providers.openshell import OpenShellProvider
    from coworker.sandbox.workspace import RunnerWorkspace

    provider = OpenShellProvider(roots=[{"path": str(project), "writable": True}], cwd=str(project), credentials=grants)
    ws = RunnerWorkspace(provider, cwd=project, live_roots=[RootDir(path=project, writable=True)])
    try:
        ex = ws.executor
        copy = provider.copied.home
        assert ex.run("echo $HOME")["output"].strip() == copy
        assert "PRIVATE KEY" in ex.run("cat ~/.ssh/id_ed25519")["output"]
        assert "gho_x" in ex.run('cat "$GH_CONFIG_DIR/hosts.yml"')["output"]
        assert "IdentityFile" in ex.run("cat ~/.ssh/config")["output"]
        assert "-F" in ex.run("echo $GIT_SSH_COMMAND")["output"]
        hidden = ex.run(f"ls {home}/.ssh 2>&1")["output"].lower()
        assert "denied" in hidden or "no such file" in hidden  # the real files stay hidden (not mounted at all)
        assert "SSH keys" in ws.context() and ws.describe()["credentials"][0]["name"] == "ssh"
        import yaml

        policy = yaml.safe_load((Path(provider._tmp) / "policy.yaml").read_text())
        hosts = {(e["host"], e["port"]) for e in policy["network_policies"]["credentials"]["endpoints"]}
        assert ("github.com", 22) in hosts and ("api.github.com", 443) in hosts
        assert copy in policy["filesystem_policy"]["read_write"]
    finally:
        ws.close()
    assert not Path(copy).exists()  # the copies died with the sandbox
