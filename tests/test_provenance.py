"""Agent-authored / downloaded-then-executed provenance (OPE-114 §1).

The reviewer never sees file contents, so `python scripts/setup.py` cannot be judged from
its text. These cover the one fact that makes it judgeable: did the agent itself create
that file, and how long ago.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from coworker import provenance as prov
from coworker.provenance import DOWNLOADED, WRITTEN, SessionFiles


@pytest.fixture()
def files(tmp_path) -> SessionFiles:
    return SessionFiles(tmp_path)


def _wrote(files: SessionFiles, path: str, step: int) -> None:
    files.record("write_file", {"path": path, "content": "x"}, {"ok": True}, step=step)


# -- the core case -------------------------------------------------------------
def test_agent_written_script_is_flagged_when_run(files):
    _wrote(files, "scripts/setup.py", 12)
    match = files.match("run_shell", {"command": "python scripts/setup.py"}, step=15)
    assert match is not None
    assert match.render() == "scripts/setup.py was created by the agent 3 steps ago"
    assert not match.downloaded


def test_pre_existing_file_produces_no_fact(files):
    _wrote(files, "scripts/setup.py", 12)
    # A file this session never created is invisible — no fact, no extra caution.
    assert files.match("run_shell", {"command": "python tools/other.py"}, step=15) is None


def test_step_distance_reads_naturally(files):
    _wrote(files, "a.py", 5)
    assert "just now" in files.match("run_shell", {"command": "python a.py"}, step=5).render()
    assert "1 step ago" in files.match("run_shell", {"command": "python a.py"}, step=6).render()


# -- path normalisation --------------------------------------------------------
@pytest.mark.parametrize("spelling", ["a.py", "./a.py", "sub/../a.py"])
def test_one_file_one_key_however_it_is_spelled(files, tmp_path, spelling):
    _wrote(files, "a.py", 3)
    assert files.match("run_shell", {"command": f"python {spelling}"}, step=4) is not None


def test_absolute_spelling_matches_the_relative_write(files, tmp_path):
    _wrote(files, "a.py", 3)
    absolute = (tmp_path / "a.py").as_posix()
    assert files.match("run_shell", {"command": f"python {absolute}"}, step=4) is not None


# -- what counts as "created" --------------------------------------------------
def test_patch_and_diff_writes_register(files):
    # Exercises the existing blob parsing in write_paths(): a patch names its files in the
    # body, not in a path argument.
    files.record(
        "apply_patch",
        {"patch": "*** Begin Patch\n*** Update File: build.sh\n*** End Patch"},
        {"ok": True},
        step=2,
    )
    assert files.match("run_shell", {"command": "bash build.sh"}, step=3) is not None


def test_failed_calls_are_never_recorded(files):
    # The engine only records on status == "ok"; a write that raised left nothing to run.
    # Guard the contract at the call site the engine relies on.
    paths, origin = prov.created_paths("write_file", {"path": "a.py"}, {"ok": True})
    assert (paths, origin) == (["a.py"], WRITTEN)


def test_reads_and_unrelated_tools_create_nothing():
    assert prov.created_paths("read_file", {"path": "a.py"}, {"ok": True}) == ([], "")
    assert prov.created_paths("ask_user", {"question": "?"}, None) == ([], "")


# -- downloads -----------------------------------------------------------------
@pytest.mark.parametrize(
    "command,expected",
    [
        ("curl -o tool.sh https://x.io/a", ["tool.sh"]),
        ("curl -O https://x.io/a/tool.sh", ["tool.sh"]),
        ("wget -O tool.sh https://x.io/a", ["tool.sh"]),
        ("Invoke-WebRequest https://x.io/a -OutFile tool.ps1", ["tool.ps1"]),
    ],
)
def test_shell_fetchers_record_their_output_path(command, expected):
    assert prov.created_paths("run_shell", {"command": command}, None) == (
        expected,
        DOWNLOADED,
    )


def test_curl_capital_o_does_not_swallow_the_url():
    # `-o FILE` and `-O` are different curl flags; folding their case would record the URL
    # itself as the downloaded file.
    paths, _ = prov.created_paths(
        "run_shell", {"command": "curl -O https://x.io/a/tool.sh"}, None
    )
    assert paths == ["tool.sh"]


def test_plain_fetch_without_an_output_flag_records_nothing():
    assert prov.created_paths("run_shell", {"command": "curl https://x.io/a"}, None) == (
        [],
        "",
    )


def test_tools_that_report_their_target_in_the_result(files):
    files.record("github_clone", {"owner": "o", "repo": "r"}, {"ok": True, "path": "vendor/r"}, step=4)
    match = files.match("run_shell", {"command": "bash vendor/r/install.sh"}, step=5)
    assert match is None  # a file INSIDE the clone is not the clone itself
    match = files.match("run_shell", {"command": "bash vendor/r"}, step=5)
    assert match is not None and match.downloaded


# -- command parsing -----------------------------------------------------------
@pytest.mark.parametrize(
    "command,target",
    [
        ("python scripts/setup.py", "scripts/setup.py"),
        ("bash deploy.sh", "deploy.sh"),
        ("./run.sh", "./run.sh"),
        ("node build.js", "build.js"),
        ("cd /tmp && python scripts/setup.py", "scripts/setup.py"),
    ],
)
def test_paths_are_found_wherever_they_sit_in_the_command(command, target):
    assert target in prov.command_paths(command)


def test_flags_and_urls_are_not_paths():
    found = prov.command_paths("curl --silent https://x.io/a.py")
    assert "--silent" not in found and "https://x.io/a.py" not in found


def test_implicit_targets_are_checked(files):
    # `make deploy` never names the Makefile, so without the table it would look like it
    # touches nothing.
    _wrote(files, "Makefile", 2)
    assert files.match("run_shell", {"command": "make deploy"}, step=3) is not None
    _wrote(files, "package.json", 4)
    assert files.match("run_shell", {"command": "npm run build"}, step=5) is not None


def test_newest_creation_wins(files):
    _wrote(files, "a.py", 2)
    _wrote(files, "b.py", 9)
    match = files.match("run_shell", {"command": "python a.py && python b.py"}, step=10)
    assert match.path == "b.py"  # the one whose contents the agent controlled most recently


def test_download_over_a_written_path_becomes_a_download(files):
    _wrote(files, "tool.sh", 2)
    files.record("run_shell", {"command": "curl -o tool.sh https://x.io/a"}, None, step=6)
    assert files.match("run_shell", {"command": "bash tool.sh"}, step=7).downloaded


# -- the documented blind spot -------------------------------------------------
def test_transitive_imports_are_a_known_miss(files):
    # The agent writes a helper and runs a pre-existing entry point that imports it. No
    # cheap analysis finds this; the limit is asserted so it stays a known gap rather than
    # an assumed capability.
    _wrote(files, "helper.py", 3)
    assert files.match("run_shell", {"command": "python main.py"}, step=4) is None


# -- the rendered line ---------------------------------------------------------
def test_fact_is_fixed_vocabulary_and_never_carries_content(files):
    files.record(
        "write_file",
        {"path": "a.py", "content": "SECRET_TOKEN = 'sk-live-abc123'"},
        {"ok": True},
        step=1,
    )
    rendered = files.match("run_shell", {"command": "python a.py"}, step=2).render()
    assert "sk-live" not in rendered and "SECRET_TOKEN" not in rendered
    assert rendered == "a.py was created by the agent 1 step ago"
