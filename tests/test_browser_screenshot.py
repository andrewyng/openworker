"""browser_screenshot must confine a caller-supplied destination to the session's writable
roots. The tool is registered read-kind (never approval-gated), so an unconfined path let a
model-controlled `path` overwrite any file on disk with PNG bytes. Confinement happens before
the browser is opened, so these cases assert without Playwright installed."""

from __future__ import annotations

from coworker.connectors.browser_automation import make_browser_automation_tools
from coworker.roots import RootDir


def _screenshot_tool(roots):
    tools = make_browser_automation_tools(roots=roots)
    tool = next(t for t in tools if t.__name__ == "browser_screenshot")
    return tool


def test_screenshot_rejects_path_outside_writable_roots(tmp_path):
    workspace = tmp_path / "scratch"
    workspace.mkdir()
    tool = _screenshot_tool([RootDir(path=workspace, writable=True)])

    # An absolute path outside every granted root is refused (would have overwritten it).
    outside = tmp_path / "secrets.json"
    outside.write_text("keep me")
    res = tool(path=str(outside))
    assert "error" in res and "outside" in res["error"]
    assert outside.read_text() == "keep me"  # untouched


def test_screenshot_rejects_traversal_escape(tmp_path):
    workspace = tmp_path / "scratch"
    workspace.mkdir()
    tool = _screenshot_tool([RootDir(path=workspace, writable=True)])
    res = tool(path="../escape.png")
    assert "error" in res and "outside" in res["error"]


def test_screenshot_rejects_when_no_writable_root(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    tool = _screenshot_tool([RootDir(path=ro, writable=False)])
    res = tool(path=str(ro / "shot.png"))
    assert "error" in res and "no writable" in res["error"]


def test_screenshot_accepts_path_inside_writable_root_then_reaches_browser(tmp_path):
    """A path inside a granted writable root passes confinement; it then proceeds to the
    browser call (which returns a Playwright setup error here — the point is it was NOT
    rejected by confinement, i.e. no 'outside'/'no writable' error)."""
    workspace = tmp_path / "scratch"
    workspace.mkdir()
    tool = _screenshot_tool([RootDir(path=workspace, writable=True)])
    res = tool(path=str(workspace / "shot.png"))
    assert "outside" not in str(res.get("error", ""))
    assert "no writable" not in str(res.get("error", ""))
