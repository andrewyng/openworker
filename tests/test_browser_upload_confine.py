"""browser_upload_file must confine the source path to a granted session root.

The tool is kind=write → EXTERNAL risk, and Mode.AUTO returns "full access" with no
path check — so a model-controlled path could upload ~/.config/coworker/secrets.json
(or an SSH key) through a page file input. Confinement runs before the browser opens,
so these cases assert without Playwright.
"""

from __future__ import annotations

from coworker.connectors.browser_automation import make_browser_automation_tools
from coworker.roots import RootDir


def _upload_tool(roots):
    tools = make_browser_automation_tools(roots=roots)
    return next(t for t in tools if t.__name__ == "browser_upload_file")


def test_upload_rejects_path_outside_granted_roots(tmp_path):
    workspace = tmp_path / "scratch"
    workspace.mkdir()
    secrets = tmp_path / "secrets.json"
    secrets.write_text('{"api_key": "sk-secret"}')

    tool = _upload_tool([RootDir(path=workspace, writable=True)])
    res = tool(target="#file", path=str(secrets))
    assert "error" in res and "outside" in res["error"]
    assert secrets.read_text() == '{"api_key": "sk-secret"}'  # never read for upload


def test_upload_rejects_traversal_escape(tmp_path):
    workspace = tmp_path / "scratch"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    tool = _upload_tool([RootDir(path=workspace, writable=True)])
    res = tool(target="#file", path="../secret.txt")
    assert "error" in res and "outside" in res["error"]


def test_upload_rejects_when_no_roots(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("x")
    tool = _upload_tool([])
    res = tool(target="#file", path=str(f))
    assert "error" in res and "no granted" in res["error"]


def test_upload_allows_readable_root_not_only_writable(tmp_path):
    """Upload is a read/exfil boundary — a read-only granted root is still fine."""
    ro = tmp_path / "ro"
    ro.mkdir()
    doc = ro / "doc.txt"
    doc.write_text("ok")
    tool = _upload_tool([RootDir(path=ro, writable=False)])
    res = tool(target="#file", path=str(doc))
    # Confinement passed; Playwright isn't installed here, so we get a browser setup
    # error — not an "outside" / "no granted" rejection.
    assert "outside" not in str(res.get("error", ""))
    assert "no granted" not in str(res.get("error", ""))


def test_upload_accepts_path_inside_writable_root_then_reaches_browser(tmp_path):
    workspace = tmp_path / "scratch"
    workspace.mkdir()
    doc = workspace / "doc.txt"
    doc.write_text("ok")
    tool = _upload_tool([RootDir(path=workspace, writable=True)])
    res = tool(target="#file", path=str(doc))
    assert "outside" not in str(res.get("error", ""))
    assert "no granted" not in str(res.get("error", ""))
