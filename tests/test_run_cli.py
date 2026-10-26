"""`openworker run` as a person uses it: this computer's settings are read and never
changed, the session is saved under the state folder by workspace path, approvals and
questions are asked in the terminal, and the exit code says whether the task finished.

The sealed benchmark run (`--isolated`) is covered in test_headless_run.py."""

from __future__ import annotations

import asyncio
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from coworker.engine import ApprovalOutcome, PermissionRequest
from coworker.headless.runner import allowed_sites, parse_args, workspace_folder_name
from coworker.headless.terminal import Terminal

ROOT = Path(__file__).resolve().parents[1]

WRITE_THEN_DONE = [
    {"text": "I will create the file.", "tool_calls": [{"name": "write_file", "arguments": {"path": "hello.txt", "content": "hello"}}]},
    {"text": "The file is written. Done."},
]


def _env(machine: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "COWORKER_SCRATCH_BASE", "OPENWORKER_BASE_DIR"):
        env.pop(name, None)
    env["COWORKER_STATE_DIR"] = str(machine)  # "this computer's" state folder
    return env


def _command(tmp_path: Path, script: list[dict], *extra: str) -> tuple[list[str], Path]:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    path = tmp_path / "script.json"
    path.write_text(json.dumps(script), encoding="utf-8")
    return [
        sys.executable, "-m", "coworker.cli", "run", "--prompt", "Create hello.txt.",
        "--workspace", str(ws), "--scripted", str(path), "--timeout-seconds", "120", *extra,
    ], ws


def _run(tmp_path: Path, script: list[dict], *extra: str, machine: Path | None = None):
    machine = machine or tmp_path / "machine"
    machine.mkdir(exist_ok=True)
    cmd, ws = _command(tmp_path, script, *extra)
    proc = subprocess.run(cmd, env=_env(machine), capture_output=True, text=True, timeout=300)
    return proc, ws, machine


def _session_folders(machine: Path, ws: Path) -> list[Path]:
    base = machine / "sessions" / workspace_folder_name(ws.resolve())
    return sorted(p for p in base.iterdir()) if base.is_dir() else []


# -- the saved session ------------------------------------------------------------------


def test_a_run_is_saved_under_the_state_folder_by_workspace_path(tmp_path: Path) -> None:
    proc, ws, machine = _run(tmp_path, WRITE_THEN_DONE, "--model", "anthropic/claude-sonnet-5", "--approval-mode", "bypass-approvals")
    assert proc.returncode == 0, proc.stderr
    assert (ws / "hello.txt").read_text(encoding="utf-8") == "hello"
    (folder,) = _session_folders(machine, ws)
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["outcome"] == "completed" and summary["args"]["isolated"] is False
    assert folder.name == summary["run_id"]
    for name in ("events.jsonl", "messages.json", "trajectory.json", "audit.db"):
        assert (folder / name).exists(), name
    # Both exist from the start: a sandbox is handed the run's folders when it is made.
    assert (folder / "scratch").is_dir() and (folder / "tool-output").is_dir()
    # Standard output is the coworker's answer and nothing else.
    assert proc.stdout == "The file is written. Done.\n"
    assert "outcome=completed" in proc.stderr and "write_file: hello.txt" in proc.stderr


def test_the_workspace_folder_name_is_the_path_with_dashes() -> None:
    assert workspace_folder_name(Path("/Users/me/src/my app")) == "-Users-me-src-my-app"
    long = workspace_folder_name(Path("/" + "/".join(["folder"] * 60)))
    assert len(long) < 200 and long != workspace_folder_name(Path("/" + "/".join(["folder"] * 61)))


def test_this_computers_settings_are_read_and_not_changed(tmp_path: Path) -> None:
    machine = tmp_path / "machine"
    machine.mkdir()
    (machine / "config.toml").write_text('model = "anthropic:claude-sonnet-5"\n', encoding="utf-8")
    before = {p.name: p.read_bytes() for p in machine.iterdir() if p.is_file()}

    proc, ws, _ = _run(tmp_path, WRITE_THEN_DONE, "--approval-mode", "bypass-approvals", machine=machine)  # no --model
    assert proc.returncode == 0, proc.stderr
    (folder,) = _session_folders(machine, ws)
    assert json.loads((folder / "summary.json").read_text(encoding="utf-8"))["args"]["model"] == "anthropic:claude-sonnet-5"
    assert {p.name: p.read_bytes() for p in machine.iterdir() if p.is_file()} == before
    # Beside the settings, the run left only its own session.
    assert sorted(p.name for p in machine.iterdir() if p.is_dir()) == ["sessions"]


def test_a_run_has_the_connectors_this_computer_has_connected(tmp_path: Path) -> None:
    """The write is refused here (mode ask, nobody to ask), which is the proof the
    connector's tool was there to be called; nothing leaves the computer."""
    machine = tmp_path / "machine"
    machine.mkdir()
    (machine / "secrets.json").write_text(json.dumps({"github:default": {"token": "ghp_test", "enabled": True}}), encoding="utf-8")
    script = [
        {"text": "", "tool_calls": [{"name": "github_create_issue", "arguments": {"owner": "acme", "repo": "app", "title": "A title", "body": "A body"}}]},
        {"text": "It was refused."},
    ]
    proc, ws, _ = _run(tmp_path, script, "--model", "anthropic/claude-sonnet-5", machine=machine)
    assert proc.returncode == 0, proc.stderr
    (folder,) = _session_folders(machine, ws)
    answers = json.loads((folder / "answers.json").read_text(encoding="utf-8"))
    assert [row["tool"] for row in answers["rows"]] == ["github_create_issue"]
    assert json.loads((machine / "secrets.json").read_text(encoding="utf-8")) == {"github:default": {"token": "ghp_test", "enabled": True}}


def test_two_runs_in_one_workspace_are_two_sessions(tmp_path: Path) -> None:
    for _ in range(2):
        proc, ws, machine = _run(tmp_path, WRITE_THEN_DONE, "--model", "anthropic/claude-sonnet-5", "--approval-mode", "bypass-approvals")
        assert proc.returncode == 0, proc.stderr
    assert len(_session_folders(machine, ws)) == 2


# -- nobody at a terminal ---------------------------------------------------------------


def test_the_default_mode_asks_and_with_no_terminal_the_card_is_refused(tmp_path: Path) -> None:
    proc, ws, machine = _run(tmp_path, WRITE_THEN_DONE, "--model", "anthropic/claude-sonnet-5")
    assert proc.returncode == 0, proc.stderr  # the model's turn finished; the write was refused
    assert not (ws / "hello.txt").exists()
    assert "no terminal is attached" in proc.stderr and "--approval-mode auto-approve" in proc.stderr
    (folder,) = _session_folders(machine, ws)
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["args"]["approval_mode"] == "ask" and summary["args"]["auto_answer"] is True
    assert summary["answers"]["cards_refused"] == 1


def test_with_no_terminal_a_question_is_answered_by_rule(tmp_path: Path) -> None:
    script = [
        {"text": "", "tool_calls": [{"name": "ask_user", "arguments": {"question": "Which colour?", "options": ["red", "blue"]}}]},
        {"text": "Going with the safe one."},
    ]
    proc, ws, machine = _run(tmp_path, script, "--model", "anthropic/claude-sonnet-5", "--approval-mode", "bypass-approvals")
    assert proc.returncode == 0, proc.stderr
    (folder,) = _session_folders(machine, ws)
    assert json.loads((folder / "summary.json").read_text(encoding="utf-8"))["answers"]["questions_answered"] == 1


def test_a_run_that_stops_early_exits_3_and_says_why(tmp_path: Path) -> None:
    fail = {"error": "Invalid API key provided", "error_type": "AuthenticationError"}
    proc, ws, machine = _run(tmp_path, [fail], "--model", "anthropic/claude-sonnet-5", "--approval-mode", "bypass-approvals")
    assert proc.returncode == 3
    assert "outcome=model_error" in proc.stderr and "Invalid API key provided" in proc.stderr
    assert proc.stdout == ""  # no answer was given
    (folder,) = _session_folders(machine, ws)
    assert (folder / "trajectory.json").exists()  # the record is written all the same


# -- usage errors -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra, said",
    [
        (["--isolated", "--model", "anthropic/claude-sonnet-5"], "--isolated needs --out"),
        (["--isolated", "--out", "OUT"], "--isolated needs --model"),
        (["--model", "anthropic/claude-sonnet-5", "--allow-site", "*.example.com"], "not a site name"),
        (["--model", "anthropic/claude-sonnet-5", "--allow-site", "10.0.0.1"], "not a site name"),
        (["--model", "anthropic/claude-sonnet-5", "--allow-sites-file", "missing.txt"], "cannot read --allow-sites-file"),
    ],
)
def test_a_command_line_that_cannot_run_stops_with_exit_code_2(tmp_path: Path, extra: list[str], said: str) -> None:
    extra = [str(tmp_path / "out") if x == "OUT" else x for x in extra]
    proc, ws, machine = _run(tmp_path, WRITE_THEN_DONE, *extra)
    assert proc.returncode == 2
    assert said in proc.stderr and proc.stdout == ""
    assert not (machine / "sessions").exists()  # nothing was started, nothing is left behind


def test_allowed_sites_come_from_the_flag_and_the_file(tmp_path: Path) -> None:
    listed = tmp_path / "sites.txt"
    listed.write_text("# package registries\nregistry.npmjs.org\n\nfiles.pythonhosted.org:443  # wheels\ninternal.example.com:8443\n", encoding="utf-8")
    args = parse_args(["--prompt", "x", "--allow-site", "example.com", "--allow-site", "registry.npmjs.org", "--allow-sites-file", str(listed)])
    assert allowed_sites(args) == [
        "example.com:443", "registry.npmjs.org:443", "files.pythonhosted.org:443", "internal.example.com:8443",
    ]
    assert allowed_sites(parse_args(["--prompt", "x"])) == []


def test_allow_site_says_so_when_no_list_holds_the_run(tmp_path: Path) -> None:
    proc, _ws, _machine = _run(
        tmp_path, WRITE_THEN_DONE, "--model", "anthropic/claude-sonnet-5", "--approval-mode", "bypass-approvals", "--allow-site", "example.com",
    )
    assert proc.returncode == 0, proc.stderr
    assert "--allow-site has no effect here" in proc.stderr


# -- the prompts ------------------------------------------------------------------------


class _Keys:
    """A terminal for the prompts: the lines the person types, and what was printed."""

    def __init__(self, *typed: str) -> None:
        self.typed = list(typed)
        self.printed = ""

    def terminal(self, cwd: Path | None = None) -> Terminal:
        return Terminal(lambda: self.typed.pop(0) if self.typed else None, self._write, cwd=cwd)

    def _write(self, text: str) -> None:
        self.printed += text


def _request(tool: str, arguments: dict, reason: str = "requires approval") -> PermissionRequest:
    return PermissionRequest(tool_name=tool, arguments=arguments, metadata=None, reason=reason)


def test_a_command_card_offers_once_this_session_and_no() -> None:
    home = Path.home()
    for typed, outcome in (("y", ApprovalOutcome.ONCE), ("s", ApprovalOutcome.ALWAYS_COMMAND), ("n", ApprovalOutcome.DENY)):
        keys = _Keys(typed)
        got = asyncio.run(keys.terminal(cwd=home / "src" / "project").approve(_request("run_shell", {"command": "npm install"})))
        assert got is outcome
        assert "Approval needed: run a command\n  npm install\n  in ~/src/project\n" in keys.printed
        assert "[y] yes, once   [s] yes, for this session   [n] no" in keys.printed
        assert "requires approval" not in keys.printed  # the engine's filler reason is not shown


def test_an_unknown_key_asks_again_and_a_closed_terminal_is_a_no() -> None:
    keys = _Keys("maybe", "", "Y")
    assert asyncio.run(keys.terminal().approve(_request("run_shell", {"command": "ls"}))) is ApprovalOutcome.ONCE
    assert keys.printed.count("Type y, s or n.") == 2

    assert asyncio.run(_Keys().terminal().approve(_request("run_shell", {"command": "ls"}))) is ApprovalOutcome.DENY


def test_a_file_write_card_shows_its_arguments_and_the_reason() -> None:
    keys = _Keys("s")
    request = _request("write_file", {"path": "notes.md", "content": "x" * 900}, reason="writes outside the workspace")
    assert asyncio.run(keys.terminal().approve(request)) is ApprovalOutcome.ALWAYS_TOOL
    assert "Approval needed: use write_file" in keys.printed and "path: notes.md" in keys.printed
    assert "Why it asks: writes outside the workspace" in keys.printed
    assert "x" * 300 not in keys.printed  # a long value is cut


def test_the_network_card_offers_this_run_only() -> None:
    keys = _Keys("y")
    term = keys.terminal()
    term.card = {
        "network_request": {
            "reason": "npm install needs the package registry.",
            "evidence": True,
            "hosts": [{"host": "registry.npmjs.org:443", "blocked_seconds_ago": 4}],
        }
    }
    request = _request("request_network_access", {"hosts": ["registry.npmjs.org"], "reason": "…"})
    assert asyncio.run(term.approve(request)) is ApprovalOutcome.ALWAYS_DOMAIN  # this run; never "always"
    assert "Approval needed: let commands reach registry.npmjs.org\n" in keys.printed
    assert "Reason: npm install needs the package registry." in keys.printed
    assert "The sandbox blocked this site 4 seconds ago." in keys.printed
    assert "[s] yes, for this run   [n] no" in keys.printed and "once" not in keys.printed

    keys = _Keys("n")
    term = keys.terminal()
    term.card = {"network_request": {"reason": "", "evidence": True, "hosts": [{"host": "example.org:8443", "blocked_seconds_ago": None}]}}
    assert asyncio.run(term.approve(request)) is ApprovalOutcome.DENY
    assert "let commands reach example.org:8443" in keys.printed and "No command has tried this site yet." in keys.printed


def test_a_question_takes_a_number_or_typed_text() -> None:
    question = {"question": "Which database should the migration target?", "options": ["staging", "production"]}
    keys = _Keys("2")
    assert asyncio.run(keys.terminal().ask(dict(question))) == {"answer": "production"}
    assert "The coworker asks:\n  Which database should the migration target?\n    1. staging\n    2. production\n" in keys.printed
    assert "Type a number, or your own answer:" in keys.printed

    assert asyncio.run(_Keys("", "neither, use a copy").terminal().ask(dict(question))) == {"answer": "neither, use a copy"}
    assert asyncio.run(_Keys("the blue one").terminal().ask({"question": "Which colour?"})) == {"answer": "the blue one"}

    keys = _Keys("other", "1")
    assert asyncio.run(keys.terminal().ask({**question, "allow_text": False})) == {"answer": "staging"}
    assert "Type a number from 1 to 2." in keys.printed


def test_grouped_questions_are_asked_in_turn_and_keyed_by_header() -> None:
    keys = _Keys("1, 2", "weekly")
    got = asyncio.run(
        keys.terminal().ask(
            {
                "questions": [
                    {"header": "Regions", "question": "Which regions?", "options": [{"label": "EU"}, {"label": "US"}], "multi": True},
                    {"question": "How often?"},
                ]
            }
        )
    )
    assert got == {"answers": {"Regions": "EU, US", "How often?": "weekly"}}


def test_a_folder_request_is_asked_and_yes_gives_the_folder(tmp_path: Path) -> None:
    exports = tmp_path / "exports"
    exports.mkdir()
    given: list[tuple[Path, bool]] = []

    def terminal(keys: _Keys) -> Terminal:
        term = keys.terminal()
        term.grant_folder = lambda folder, writable: given.append((folder, writable)) or folder.resolve()
        return term

    request = {"path": str(exports), "writable": True, "reason": "the report reads last month's exports"}
    keys = _Keys("y")
    got = asyncio.run(terminal(keys).directory(dict(request)))
    assert got == {"granted": True, "path": str(exports.resolve()), "writable": True}
    assert f"The coworker asks for a folder to read and write: {exports}" in keys.printed
    assert "Reason: the report reads last month's exports" in keys.printed
    assert "[y] yes   [r] yes, read only   [n] no   or type another folder" in keys.printed

    # Read only, although it asked to write.
    assert asyncio.run(terminal(_Keys("r")).directory(dict(request)))["writable"] is False
    # A different folder, typed; one that does not exist is asked again.
    other = tmp_path / "other"
    other.mkdir()
    keys = _Keys(str(tmp_path / "missing"), str(other))
    assert asyncio.run(terminal(keys).directory(dict(request)))["path"] == str(other.resolve())
    assert "is not a folder" in keys.printed
    assert given == [(exports, True), (exports, False), (other, True)]

    # No, and a closed terminal, both decline and give nothing.
    for keys in (_Keys("n"), _Keys()):
        assert asyncio.run(terminal(keys).directory(dict(request))) == {"granted": False, "reason": "the user declined the request"}
    assert len(given) == 3

    # A read request has no read-only choice; a request with no path asks for one.
    keys = _Keys("n")
    asyncio.run(terminal(keys).directory({"path": str(exports)}))
    assert "to read:" in keys.printed and "[r]" not in keys.printed
    keys = _Keys("y", str(exports))
    assert asyncio.run(terminal(keys).directory({"reason": "needs the data"}))["granted"] is True
    assert "Type the folder, or n for no" in keys.printed


def test_a_tool_install_is_asked_and_yes_installs_the_pinned_build(monkeypatch: pytest.MonkeyPatch) -> None:
    from coworker import toolchain

    monkeypatch.setattr(toolchain, "describe", lambda name: {"version": "1.2.3", "summary": "a scanner"})
    installed: list[str] = []
    monkeypatch.setattr(toolchain, "install", lambda name: installed.append(name) or f"/tools/{name}")

    keys = _Keys("what", "y")
    got = asyncio.run(keys.terminal().tool({"name": "scanner", "reason": "to check the lockfile"}))
    assert got == {"installed": True, "path": "/tools/scanner", "version": "1.2.3"} and installed == ["scanner"]
    assert "The coworker asks to install a tool: scanner 1.2.3" in keys.printed and "[y] install it   [n] no" in keys.printed

    got = asyncio.run(_Keys("n").terminal().tool({"name": "scanner"}))
    assert got["installed"] is False and installed == ["scanner"]


# -- approvals are separate from questions --------------------------------------------


def test_with_auto_answer_the_cards_still_reach_the_person() -> None:
    from coworker.engine import TurnEngine

    engine = TurnEngine.__new__(TurnEngine)
    engine.attendance = lambda: "auto"
    engine.cards_reach_person = None
    assert engine._auto_answering() and engine._nobody_answers_cards()  # the app's "auto": both
    engine.cards_reach_person = lambda: True
    assert engine._auto_answering() and not engine._nobody_answers_cards()
    engine.attendance = lambda: "attended"
    engine.cards_reach_person = None
    assert not engine._nobody_answers_cards()


# -- at a real terminal -----------------------------------------------------------------


def _at_a_terminal(tmp_path: Path, script: list[dict], typed: list[tuple[str, str]], *extra: str):
    """Run with a terminal on standard input and standard error (standard output stays a
    pipe). `typed`: (text to wait for, line to type) in order. Returns (exit code,
    what the terminal showed, standard output, workspace)."""
    import pty

    machine = tmp_path / "machine"
    machine.mkdir(exist_ok=True)
    cmd, ws = _command(tmp_path, script, "--model", "anthropic/claude-sonnet-5", *extra)
    master, slave = pty.openpty()
    proc = subprocess.Popen(cmd, env=_env(machine), stdin=slave, stderr=slave, stdout=subprocess.PIPE)
    os.close(slave)
    shown = ""
    waiting = list(typed)
    deadline = time.time() + 120
    try:
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:  # the run closed its end
                    break
                if not chunk:
                    break
                shown += chunk.decode("utf-8", errors="replace")
            if waiting and waiting[0][0] in shown and shown.rstrip().endswith(">"):
                os.write(master, (waiting.pop(0)[1] + "\n").encode("utf-8"))
            if proc.poll() is not None and not ready:
                break
        out = proc.stdout.read().decode("utf-8") if proc.stdout else ""
        return proc.wait(timeout=30), shown, out, ws
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(master)


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_at_a_terminal_the_approval_is_asked_and_yes_runs_the_call(tmp_path: Path) -> None:
    code, shown, out, ws = _at_a_terminal(tmp_path, WRITE_THEN_DONE, [("Approval needed: use write_file", "y")])
    assert code == 0, shown
    assert (ws / "hello.txt").read_text(encoding="utf-8") == "hello"
    assert "path: hello.txt" in shown and "no terminal is attached" not in shown
    assert out == "The file is written. Done.\n"


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_at_a_terminal_no_refuses_the_call(tmp_path: Path) -> None:
    code, shown, _out, ws = _at_a_terminal(tmp_path, WRITE_THEN_DONE, [("Approval needed: use write_file", "n")])
    assert code == 0, shown
    assert not (ws / "hello.txt").exists()


QUESTION_THEN_WRITE = [
    {"text": "", "tool_calls": [{"name": "ask_user", "arguments": {"question": "Which colour?", "options": ["red", "blue"]}}]},
    {"text": "", "tool_calls": [{"name": "write_file", "arguments": {"path": "hello.txt", "content": "hello"}}]},
    {"text": "Done."},
]


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_at_a_terminal_a_question_is_asked_there(tmp_path: Path) -> None:
    code, shown, _out, ws = _at_a_terminal(
        tmp_path, QUESTION_THEN_WRITE, [("Which colour?", "2")], "--approval-mode", "bypass-approvals",
    )
    assert code == 0, shown
    assert "The coworker asks:" in shown and "2. blue" in shown
    machine = tmp_path / "machine"
    (folder,) = _session_folders(machine, ws)
    messages = json.loads((folder / "messages.json").read_text(encoding="utf-8"))
    assert any(m.get("role") == "tool" and "blue" in str(m.get("content")) for m in messages)
    assert json.loads((folder / "summary.json").read_text(encoding="utf-8"))["args"]["auto_answer"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_auto_answer_at_a_terminal_answers_the_question_and_still_asks_the_approval(tmp_path: Path) -> None:
    code, shown, _out, ws = _at_a_terminal(
        tmp_path, QUESTION_THEN_WRITE, [("Approval needed: use write_file", "y")], "--auto-answer",
    )
    assert code == 0, shown
    assert "The coworker asks:" not in shown  # answered by rule
    assert (ws / "hello.txt").exists()  # the approval was the person's, and they said yes
    (folder,) = _session_folders(tmp_path / "machine", ws)
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert summary["answers"]["questions_answered"] == 1 and summary["answers"]["cards_refused"] == 0


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_ctrl_c_at_a_prompt_ends_the_run_and_leaves_the_record(tmp_path: Path) -> None:
    import pty
    import signal

    machine = tmp_path / "machine"
    machine.mkdir()
    cmd, ws = _command(tmp_path, WRITE_THEN_DONE, "--model", "anthropic/claude-sonnet-5")
    master, slave = pty.openpty()
    proc = subprocess.Popen(cmd, env=_env(machine), stdin=slave, stderr=slave, stdout=subprocess.PIPE)
    os.close(slave)
    try:
        shown = ""
        deadline = time.time() + 60
        while "Approval needed" not in shown or not shown.rstrip().endswith(">"):
            assert time.time() < deadline, shown
            if select.select([master], [], [], 0.2)[0]:
                shown += os.read(master, 4096).decode("utf-8", errors="replace")
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=30) == 130  # without anyone pressing Enter
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(master)
    assert not (ws / "hello.txt").exists()
    (folder,) = _session_folders(machine, ws)
    assert json.loads((folder / "summary.json").read_text(encoding="utf-8"))["outcome"] == "interrupted"



FOLDER_THEN_WRITE = [
    {"text": "", "tool_calls": [{"name": "request_directory", "arguments": {"path": "EXPORTS", "writable": True, "reason": "to save the report there"}}]},
    {"text": "", "tool_calls": [{"name": "write_file", "arguments": {"path": "EXPORTS/report.txt", "content": "done"}}]},
    {"text": "Saved."},
]


def _folder_script(exports: Path) -> list[dict]:
    return json.loads(json.dumps(FOLDER_THEN_WRITE).replace("EXPORTS", str(exports)))


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")
def test_at_a_terminal_a_folder_request_is_asked_and_the_folder_can_then_be_written(tmp_path: Path) -> None:
    exports = tmp_path / "exports"
    exports.mkdir()
    code, shown, _out, _ws = _at_a_terminal(
        tmp_path, _folder_script(exports), [("The coworker asks for a folder", "y")], "--approval-mode", "bypass-approvals",
    )
    assert code == 0, shown
    assert "to save the report there" in shown
    assert (exports / "report.txt").read_text(encoding="utf-8") == "done"


def test_with_auto_answer_a_folder_request_is_declined_by_rule(tmp_path: Path) -> None:
    exports = tmp_path / "exports"
    exports.mkdir()
    proc, ws, machine = _run(
        tmp_path, _folder_script(exports), "--model", "anthropic/claude-sonnet-5", "--approval-mode", "bypass-approvals", "--auto-answer",
    )
    assert proc.returncode == 0, proc.stderr
    assert not (exports / "report.txt").exists()
    (folder,) = _session_folders(machine, ws)
    assert json.loads((folder / "summary.json").read_text(encoding="utf-8"))["answers"]["directory_requests_declined"] == 1


# -- a failing model call ---------------------------------------------------------------


def test_the_root_cause_is_the_bottom_of_the_chain() -> None:
    from coworker.headless.runner import root_cause_text

    ProxyError = type("ProxyError", (Exception,), {})
    try:
        try:
            try:
                raise ProxyError("403 Forbidden")
            except Exception as low:
                raise RuntimeError("httpx failed") from low
        except Exception as mid:
            raise ConnectionError("Connection error.") from mid
    except Exception as top:
        assert root_cause_text(top) == "the proxy answered 403 Forbidden"

    try:
        try:
            raise OSError("[Errno 111] Connection refused")
        except Exception as low:
            raise ConnectionError("Connection error.") from low
    except Exception as top:
        assert root_cause_text(top) == "OSError: [Errno 111] Connection refused"

    assert root_cause_text(ValueError("nothing underneath")) == ""


REFUSED_BY_A_PROXY = {"error": "Connection error.", "error_type": "APIConnectionError", "cause": "403 Forbidden", "cause_type": "ProxyError"}


def test_the_retry_line_says_why_the_model_call_failed(tmp_path: Path) -> None:
    machine = tmp_path / "machine"
    machine.mkdir()
    cmd, ws = _command(tmp_path, [REFUSED_BY_A_PROXY, {"text": "Recovered."}], "--model", "anthropic/claude-sonnet-5")
    env = {**_env(machine), "OPENWORKER_RUN_RETRY_DELAYS": "0"}
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    assert "provider error (APIConnectionError: Connection error. Cause: the proxy answered 403 Forbidden); retry 1/1 in 0s" in proc.stderr
    (folder,) = _session_folders(machine, ws)
    events = [json.loads(line) for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    (retry,) = [e for e in events if e["type"] == "provider_retry"]
    assert retry["data"]["cause"] == "the proxy answered 403 Forbidden"

    # When the retries run out, the closing line says why as well.
    second = tmp_path / "second"
    second.mkdir()
    cmd, _ws = _command(second, [REFUSED_BY_A_PROXY, REFUSED_BY_A_PROXY], "--model", "anthropic/claude-sonnet-5")
    proc = subprocess.run(cmd, env={**_env(machine), "OPENWORKER_RUN_RETRY_DELAYS": "0"}, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 3
    assert proc.stderr.rstrip().splitlines()[-1].endswith("Cause: the proxy answered 403 Forbidden")


@pytest.mark.skipif(sys.platform == "win32", reason="sends a stop signal")
def test_a_stop_signal_ends_the_run_during_the_wait_between_retries(tmp_path: Path) -> None:
    import signal

    machine = tmp_path / "machine"
    machine.mkdir()
    cmd, ws = _command(tmp_path, [REFUSED_BY_A_PROXY, {"text": "never reached"}], "--model", "anthropic/claude-sonnet-5")
    env = {**_env(machine), "OPENWORKER_RUN_RETRY_DELAYS": "600"}
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        shown = ""
        deadline = time.time() + 60
        while "retry 1/1 in 600s" not in shown:
            assert time.time() < deadline, shown
            if select.select([proc.stderr], [], [], 0.2)[0]:
                shown += os.read(proc.stderr.fileno(), 4096).decode("utf-8", errors="replace")
        time.sleep(0.5)  # it is now waiting out the 600 seconds
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == 130  # it stopped; it did not wait
    finally:
        if proc.poll() is None:
            proc.kill()
    (folder,) = _session_folders(machine, ws)
    assert json.loads((folder / "summary.json").read_text(encoding="utf-8"))["outcome"] == "interrupted"

