"""`list_files` shows folders as well as files (OPE-203).

The aisuite toolkit's listing returns files only, so a workspace whose top level holds
nothing but a subfolder lists as `[]` and the agent concludes the workspace is empty.
Ours lists folders too, marked with a trailing `/`.
"""

from __future__ import annotations

from coworker.agents.base import AgentContext
from coworker.catalog import expand
from coworker.tools.files import file_tools
from coworker.tools.todo import TodoList


def _list_files(ws):
    return {t.__name__: t for t in file_tools(str(ws))}["list_files"]


def _site(tmp_path):
    site = tmp_path / "personal-site"
    (site / "_includes").mkdir(parents=True)
    (site / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (site / "_includes" / "about.md").write_text("me", encoding="utf-8")
    return site


def test_a_workspace_of_subfolders_is_not_reported_empty(tmp_path):
    _site(tmp_path)
    assert _list_files(tmp_path)(path=".", recursive=False) == ["personal-site/"]


def test_recursive_listing_shows_folders_and_files(tmp_path):
    _site(tmp_path)
    assert _list_files(tmp_path)(path=".", recursive=True) == [
        "personal-site/",
        "personal-site/_includes/",
        "personal-site/_includes/about.md",
        "personal-site/index.html",
    ]


def test_pattern_and_subpath_apply(tmp_path):
    _site(tmp_path)
    tool = _list_files(tmp_path)
    assert tool(path="personal-site", pattern="*.html", recursive=False) == [
        "personal-site/index.html"
    ]
    assert tool(path="personal-site", recursive=False) == [
        "personal-site/_includes/",
        "personal-site/index.html",
    ]


def test_ignored_folders_stay_hidden(tmp_path):
    _site(tmp_path)
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (tmp_path / "node_modules" / "left-pad").mkdir(parents=True)
    listed = _list_files(tmp_path)(path=".", recursive=True)
    assert not any(entry.startswith((".git", "node_modules")) for entry in listed)
    assert "personal-site/" in listed


def test_max_results_caps_the_listing(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    assert len(_list_files(tmp_path)(path=".", max_results=3)) == 3


def test_paths_outside_the_session_are_refused(tmp_path):
    tool = _list_files(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    assert "error" in tool(path="..")
    assert "error" in tool(path="missing")


def test_catalog_registers_the_folder_aware_listing(tmp_path):
    ctx = AgentContext(workspace=tmp_path, executor=object(), todo=TodoList())
    for capability in ("code_files", "files"):
        listings = [t for t in expand([capability], ctx) if t.__name__ == "list_files"]
        assert len(listings) == 1, capability
        assert hasattr(listings[0], "__coworker_schema__"), capability
