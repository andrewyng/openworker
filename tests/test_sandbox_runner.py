"""The tool runner: protocol, daemon, relay, client (design doc `sandbox-design.md`).

These run the real thing end to end: the runner packed into its single file, a daemon
process, an attach relay per pipe, and the client. No sandbox technology is involved
(`runner-local`), so what is tested here is the protocol and its delivery rules.
"""

from __future__ import annotations

import ast
import base64
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the tool runner needs Unix sockets")

from coworker.sandbox.bundle import build_runner_zipapp  # noqa: E402
from coworker.sandbox.client import RunnerClient  # noqa: E402
from coworker.sandbox.executor import RunnerExecutor  # noqa: E402
from coworker.sandbox.providers.runner_local import RunnerLocalProvider  # noqa: E402
from coworker.sandbox.runner import protocol as P  # noqa: E402
from coworker.sandbox.workspace import DirectWorkspace, RunnerWorkspace, open_workspace  # noqa: E402

RUNNER_DIR = Path(__file__).resolve().parents[1] / "coworker" / "sandbox" / "runner"


@pytest.fixture
def runner(tmp_path):
    """(provider, client) on a fresh daemon. The relay never leaves on silence here."""
    zipapp = build_runner_zipapp(tmp_path / "dist")
    provider = RunnerLocalProvider(cwd=tmp_path, runner_path=zipapp, relay_silence_seconds=0)
    provider.create()
    client = RunnerClient(provider.open_runner, ping_seconds=0.5)
    client.connect()
    yield provider, client
    client.close()
    provider.destroy()


# -- packaging --------------------------------------------------------------------------


def test_runner_uses_only_the_standard_library():
    """The runner is mounted into any image that has Python, so it may import nothing else."""
    allowed = set(sys.stdlib_module_names)
    for source in RUNNER_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue  # relative imports stay inside the package
            for name in names:
                if source.name == "toolcalls.py" and name.startswith("aisuite.toolkits."):
                    continue  # only when run from a checkout; the packed file carries its own copy
                assert name.split(".")[0] in allowed, f"{source.name} imports {name}"


def test_zipapp_is_one_file_named_by_content(tmp_path):
    first = build_runner_zipapp(tmp_path)
    assert first == build_runner_zipapp(tmp_path)  # reused, not rebuilt
    with zipfile.ZipFile(first) as zf:
        names = set(zf.namelist())
    assert "__main__.py" in names and "owrunner/daemon.py" in names and "owrunner/executor.py" in names


# -- framing ----------------------------------------------------------------------------


def test_a_frame_is_always_one_line():
    frame = P.encode(P.result("r-1", {"output": "line one\nline two\r\n\ttabbed ☃ \x00"}))
    assert frame.count(b"\n") == 1 and frame.endswith(b"\n")
    assert P.decode(frame)["result"]["output"] == "line one\nline two\r\n\ttabbed ☃ \x00"


def test_lines_that_are_not_frames_are_dropped():
    assert P.decode(b"Welcome to Ubuntu 24.04\n") is None
    assert P.decode(b"[1, 2, 3]\n") is None
    assert P.decode(b"{not json}\n") is None
    assert P.decode(b"\xff\xfe\n") is None


def test_multi_line_output_and_shell_syntax_survive_the_pipe(runner, tmp_path):
    _, client = runner
    ex = RunnerExecutor(client, cwd=str(tmp_path))
    assert ex.run("printf 'a\\nb\\nc\\n'")["output"].startswith("a\nb\nc\n")
    # Redirection, a pipeline and a heredoc go to a real bash unchanged.
    assert ex.run("printf 'x\\ny\\nx\\n' > out.log 2>&1")["output"].strip() == ""
    assert ex.run("sort out.log | uniq -c | sort -rn | head -1")["output"].split() == ["2", "x"]
    ex.run("cat > notes.md <<'EOF'\n# Notes\nsecond \"line\"\nEOF")
    assert (tmp_path / "notes.md").read_text() == '# Notes\nsecond "line"\n'


# -- the contract -----------------------------------------------------------------------


def test_hello_reports_the_runner_environment(runner):
    _, client = runner
    assert client.hello["protocol_version"] == P.PROTOCOL_VERSION
    assert client.hello["instance_id"] == client.instance_id
    assert client.hello["os"] and client.hello["shell"]


def test_a_client_with_another_protocol_version_is_refused(runner):
    _, client = runner
    with pytest.raises(P.RunnerError) as err:
        client.call("runner.hello", {"protocol_version": 999}, timeout=10)
    assert err.value.code == P.VERSION_MISMATCH


def test_results_look_the_same_as_the_in_process_executor(runner, tmp_path):
    """A session must look the same to the model in both modes: same keys, same values."""
    _, client = runner
    direct = DirectWorkspace(cwd=tmp_path)
    try:
        ours = RunnerExecutor(client, cwd=str(tmp_path)).run("echo hi; false")
        theirs = direct.executor.run("echo hi; false")
    finally:
        direct.close()
    assert ours == theirs


def test_each_shell_keeps_its_own_folder_and_variables(runner, tmp_path):
    _, client = runner
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    one = RunnerExecutor(client, cwd=str(tmp_path), shell="agent-1")
    two = RunnerExecutor(client, cwd=str(tmp_path), shell="agent-2")
    one.run("cd a && export WHO=one")
    two.run("cd b && export WHO=two")
    assert one.run("echo $WHO $(basename $PWD)")["output"].split() == ["one", "a"]
    assert two.run("echo $WHO $(basename $PWD)")["output"].split() == ["two", "b"]


def test_a_slow_shell_does_not_block_another(runner, tmp_path):
    _, client = runner
    slow = RunnerExecutor(client, cwd=str(tmp_path), shell="slow")
    fast = RunnerExecutor(client, cwd=str(tmp_path), shell="fast")
    worker = threading.Thread(target=lambda: slow.run("sleep 2"))
    worker.start()
    time.sleep(0.3)
    started = time.monotonic()
    assert fast.run("echo quick")["exit_code"] == 0
    assert time.monotonic() - started < 1.0
    worker.join()


def test_one_shell_runs_one_command_at_a_time(runner, tmp_path):
    _, client = runner
    ex = RunnerExecutor(client, cwd=str(tmp_path))
    worker = threading.Thread(target=lambda: ex.run("sleep 1.5"))
    worker.start()
    time.sleep(0.3)
    second = ex.run("echo too-soon")
    worker.join()
    assert "running another command" in second["error"] and second["exit_code"] is None


def test_live_output_is_a_view_and_the_result_is_the_record(runner, tmp_path):
    _, client = runner
    seen: list[str] = []
    ex = RunnerExecutor(client, cwd=str(tmp_path), on_output=seen.append)
    fast = ex.run("echo instant")
    assert fast["output"].startswith("instant") and seen == []  # too quick for a live frame
    slow = ex.run("for i in 1 2 3; do echo tick $i; sleep 0.4; done")
    assert "tick 1" in "".join(seen)  # the view arrived while it ran
    assert all(f"tick {i}" in slow["output"] for i in (1, 2, 3))  # the record is complete


def test_user_stop_ends_the_command_and_the_session_carries_on(runner, tmp_path):
    """Same behaviour as the in-process executor: Stop ends the command within moments and
    the session carries on in the same folder. (Today a Stop also restarts the shell, in
    both modes, so variables are lost. That is a separate, older bug; this test does not
    pin it.)"""
    _, client = runner
    (tmp_path / "work").mkdir()
    ex = RunnerExecutor(client, cwd=str(tmp_path))
    ex.run("cd work")
    threading.Timer(0.5, ex.interrupt_now).start()
    started = time.monotonic()
    stopped = ex.run("sleep 20", timeout=30)
    assert time.monotonic() - started < 10
    assert stopped["error"] == "interrupted by user"
    assert ex.run("basename $PWD")["output"].strip() == "work"


# -- delivery after a break -------------------------------------------------------------


def test_a_cut_stream_resumes_and_no_result_is_lost(runner, tmp_path):
    provider, client = runner
    ex = RunnerExecutor(client, cwd=str(tmp_path))
    ex.run("cd /tmp && export KEEP=yes")
    instance = client.instance_id
    box: dict = {}
    worker = threading.Thread(target=lambda: box.update(ex.run("sleep 1.5; echo finished-while-away")))
    worker.start()
    time.sleep(0.4)
    client._transport.proc.kill()  # the stream is cut while the command runs
    worker.join(timeout=20)
    assert "finished-while-away" in box.get("output", "")  # delivered after the resume
    assert client.instance_id == instance and client.restarts == 0  # same daemon
    assert ex.run("echo $KEEP $PWD")["output"].split() == ["yes", "/tmp"]  # same shell


def test_the_same_request_id_runs_once(runner, tmp_path):
    _, client = runner
    target = tmp_path / "count.txt"
    frame = P.encode(P.request("r-fixed", "shell.run", {"shell": "main", "command": f"echo x >> {target}", "cwd": str(tmp_path)}))
    for _ in range(3):
        client._send(client._transport, frame)
    deadline = time.monotonic() + 10
    while not target.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.5)
    assert target.read_text() == "x\n"


def test_a_restarted_sandbox_fails_the_running_command_and_reopens_in_the_last_folder(runner, tmp_path):
    provider, client = runner
    (tmp_path / "work").mkdir()
    ex = RunnerExecutor(client, cwd=str(tmp_path))
    ex.run("cd work && export GONE=soon")
    box: dict = {}
    worker = threading.Thread(target=lambda: box.update(ex.run("sleep 30", timeout=60)))
    worker.start()
    time.sleep(0.4)
    provider.restart_daemon()  # a new runner instance, as after a sandbox restart
    worker.join(timeout=30)
    assert "outcome of this command is unknown" in box["error"]  # never re-sent by the client
    assert client.restarts == 1
    after = ex.run("echo [$GONE] $(basename $PWD)")
    assert after["output"].split() == ["[]", "work"]  # variables lost, folder kept


def test_a_relay_left_behind_goes_away_by_itself(tmp_path):
    """Spike finding C: a provider may leave the relay running after a stream is cut."""
    import subprocess

    zipapp = build_runner_zipapp(tmp_path / "dist")
    provider = RunnerLocalProvider(cwd=tmp_path, runner_path=zipapp)
    provider.create()
    try:
        relay = subprocess.Popen(
            [sys.executable, str(zipapp), "attach", "--socket", provider.socket_path, "--silence-seconds", "1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert relay.wait(timeout=10) == 0  # nobody spoke: it left
    finally:
        provider.destroy()


# -- files ------------------------------------------------------------------------------


def test_files_are_read_in_pages_and_written_with_a_precondition(runner, tmp_path):
    _, client = runner
    big = tmp_path / "out.log"
    big.write_text("".join(f"line {i}\n" for i in range(1, 1001)))
    page = client.call("fs.read", {"path": str(big), "offset_line": 10, "limit_lines": 3}, timeout=10)
    assert page["text"] == "line 10\nline 11\nline 12\n"
    assert (page["start_line"], page["end_line"], page["total_lines"], page["eof"]) == (10, 12, 1000, False)

    raw = client.call("fs.read", {"path": str(big), "offset_bytes": 0, "limit_bytes": 6}, timeout=10)
    assert base64.b64decode(raw["data_b64"]) == b"line 1" and raw["eof"] is False

    note = tmp_path / "note.txt"
    written = client.call("fs.write", {"path": str(note), "text": "one\n"}, timeout=10)
    client.call("fs.write", {"path": str(note), "text": "two\n", "if_match_sha256": written["sha256"]}, timeout=10)
    with pytest.raises(P.RunnerError) as err:  # based on content that is no longer there
        client.call("fs.write", {"path": str(note), "text": "three\n", "if_match_sha256": written["sha256"]}, timeout=10)
    assert err.value.code == P.FS_ERROR and note.read_text() == "two\n"

    found = client.call("fs.search", {"path": str(tmp_path), "pattern": r"^line 99\d$"}, timeout=10)
    assert [m["line"] for m in found["matches"]] == list(range(990, 1000))


def test_a_relative_path_resolves_against_the_shells_folder(runner, tmp_path):
    _, client = runner
    (tmp_path / "sub").mkdir()
    RunnerExecutor(client, cwd=str(tmp_path), shell="agent-1").run("cd sub && echo hello > here.txt")
    got = client.call("fs.read", {"path": "here.txt", "shell": "agent-1"}, timeout=10)
    assert got["text"] == "hello\n"
    listing = client.call("fs.list", {"path": ".", "shell": "agent-1"}, timeout=10)
    assert [e["name"] for e in listing["entries"]] == ["here.txt"]


def test_proc_run_takes_an_argument_list_without_a_shell(runner, tmp_path):
    _, client = runner
    done = client.call("proc.run", {"argv": ["printf", "%s|", "a b", "$HOME"], "cwd": str(tmp_path)}, timeout=10)
    assert done["exit_code"] == 0 and done["stdout"] == "a b|$HOME|"  # no shell: nothing expanded


# -- choosing a workspace ---------------------------------------------------------------


def test_direct_is_the_default_and_reports_no_enforcement(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENWORKER_SANDBOX_PROVIDER", raising=False)
    ws = open_workspace(cwd=tmp_path)
    try:
        assert isinstance(ws, DirectWorkspace)
        assert ws.describe()["provider"] == "direct" and ws.describe()["enforcement"] == "none"
    finally:
        ws.close()


def test_the_provider_setting_selects_the_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENWORKER_SANDBOX_PROVIDER", "runner-local")
    ws = open_workspace(cwd=tmp_path)
    try:
        assert isinstance(ws, RunnerWorkspace) and ws.describe()["provider"] == "runner-local"
        assert ws.executor.run("echo via-runner")["output"].strip() == "via-runner"
    finally:
        ws.close()
    with pytest.raises(ValueError):
        open_workspace(cwd=tmp_path, provider="no-such-thing")


def test_the_server_binary_can_run_as_the_runner(tmp_path):
    """`openworker-server sandbox-runner ...` is how the packaged app starts the runner
    (coworker/sandbox/launch.py): the same daemon, without loading the server."""
    import subprocess
    import sys

    from coworker.sandbox import launch

    assert launch.maybe_run_runner(["--port", "1"]) is False
    with pytest.raises(SystemExit):
        launch.maybe_run_runner(["sandbox-runner", "--help"])
    # From source the runner is the zipapp under -S; frozen it is the binary itself.
    assert launch.runner_command(tmp_path / "r.pyz")[:2] == [sys.executable, "-S"]
    done = subprocess.run([sys.executable, "-m", "coworker.server.run", "sandbox-runner", "--help"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and "serve" in done.stdout and "attach" in done.stdout
