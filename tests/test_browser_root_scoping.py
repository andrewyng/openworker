"""Browser upload/screenshot must respect the session's granted folders (OPE-122).

These two tools touch the local filesystem but classify EXTERNAL, so the permission
engine's root scoping — which only runs for WRITE_LOCAL — never sees them. Before this,
the only thing between `~/.ssh/id_rsa` and a web form was someone reading the approval
card. The checks live inside the tools; these pin them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from coworker.connectors.browser_automation import make_browser_automation_tools
from coworker.risk import RiskClass, classify
from coworker.roots import RootDir


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "budget.xlsx").write_text("cells", encoding="utf-8")
    outside = tmp_path / "home"
    outside.mkdir()
    (outside / "id_rsa").write_text("PRIVATE KEY", encoding="utf-8")
    return root, outside


def _tools(roots):
    return {t.__name__: t for t in make_browser_automation_tools(roots=roots)}


# -- why the tools must check for themselves ------------------------------------
def test_these_tools_are_not_root_scoped_by_the_engine():
    # The premise of this whole module: the engine's scoping is gated on WRITE_LOCAL, and
    # these classify EXTERNAL. If that ever changes, these in-tool checks become a second
    # layer rather than the only one — but the test should be revisited, not deleted.
    assert classify("browser_upload_file") is not RiskClass.WRITE_LOCAL
    assert classify("browser_screenshot") is not RiskClass.WRITE_LOCAL
    assert classify("write_file") is RiskClass.WRITE_LOCAL


# -- upload ---------------------------------------------------------------------
def test_upload_refuses_a_file_outside_the_granted_folders(workspace):
    root, outside = workspace
    upload = _tools([RootDir(path=root, writable=True)])["browser_upload_file"]

    result = upload("input[type=file]", str(outside / "id_rsa"))
    assert "error" in result and "outside the session" in result["error"]


def test_upload_refuses_a_traversal_that_lands_outside(workspace):
    root, outside = workspace
    upload = _tools([RootDir(path=root, writable=True)])["browser_upload_file"]

    result = upload("input[type=file]", str(root / ".." / "home" / "id_rsa"))
    assert "error" in result and "outside the session" in result["error"]


def test_upload_refuses_when_no_roots_were_granted(workspace):
    root, _ = workspace
    upload = _tools([])["browser_upload_file"]

    result = upload("input[type=file]", str(root / "budget.xlsx"))
    assert "error" in result  # fail closed: no roots means nothing may be uploaded


def test_upload_accepts_a_file_inside_a_granted_folder(workspace):
    root, _ = workspace
    upload = _tools([RootDir(path=root, writable=True)])["browser_upload_file"]

    # No browser is running, so this gets as far as the controller and reports that —
    # the point is that it passed the root check rather than being refused for scope.
    result = upload("input[type=file]", str(root / "budget.xlsx"))
    assert "outside the session" not in str(result.get("error", ""))


def test_upload_from_a_read_only_root_is_allowed(workspace):
    # Reading a file to upload needs read access, not write access.
    root, _ = workspace
    upload = _tools([RootDir(path=root, writable=False)])["browser_upload_file"]

    result = upload("input[type=file]", str(root / "budget.xlsx"))
    assert "outside the session" not in str(result.get("error", ""))


# -- screenshot -----------------------------------------------------------------
def test_screenshot_refuses_a_target_outside_the_granted_folders(workspace):
    root, outside = workspace
    shot = _tools([RootDir(path=root, writable=True)])["browser_screenshot"]

    result = shot(str(outside / "grab.png"))
    assert "error" in result and "outside the session" in result["error"]


def test_screenshot_refuses_a_read_only_root(workspace):
    root, _ = workspace
    shot = _tools([RootDir(path=root, writable=False)])["browser_screenshot"]

    result = shot(str(root / "grab.png"))
    assert "error" in result and "writable" in result["error"]


def test_screenshot_creates_no_directories_outside_the_roots(workspace):
    # The old code ran `out.parent.mkdir(parents=True)` before writing, so a refused path
    # could still leave folders behind — including somewhere like a Startup directory.
    root, outside = workspace
    shot = _tools([RootDir(path=root, writable=True)])["browser_screenshot"]

    target = outside / "startup" / "deep" / "grab.png"
    assert "error" in shot(str(target))
    assert not target.parent.exists()


def test_screenshot_target_inside_a_writable_root_passes_the_check(workspace):
    root, _ = workspace
    shot = _tools([RootDir(path=root, writable=True)])["browser_screenshot"]

    result = shot(str(root / "shots" / "grab.png"))
    assert "outside the session" not in str(result.get("error", ""))


def test_screenshot_without_a_path_still_uses_the_temp_default(workspace):
    # An unnamed target is not a place the user asked us to protect, and refusing it would
    # break the ordinary "just show me the page" case.
    root, _ = workspace
    shot = _tools([RootDir(path=root, writable=True)])["browser_screenshot"]

    result = shot()
    assert "outside the session" not in str(result.get("error", ""))
    assert "writable session directory" not in str(result.get("error", ""))
