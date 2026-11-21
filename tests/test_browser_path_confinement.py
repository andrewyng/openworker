"""The browser connector's two filesystem tools must stay inside the session's roots.

The permission engine path-scopes only the built-in ``WRITE_TOOLS``, and only by reading
``arguments["path"]`` (``permissions.PermissionEngine.evaluate``). A connector tool that
touches local files is therefore invisible to it, in both directions:

  browser_upload_file   reads any readable path and hands it to the loaded page
  browser_screenshot    writes a PNG to any writable path, creating parent dirs

``email_tools`` already confines outgoing attachments to the granted roots; these tests pin
the same rule for the browser tools.
"""

from pathlib import Path
import tempfile as tempfile_mod

import pytest

from coworker.connectors.browser_automation import (
    _resolve_in_roots,
    make_browser_automation_tools,
)
from coworker.roots import RootDir


def _tools(roots):
    return {t.__name__: t for t in make_browser_automation_tools(roots=roots)}


@pytest.fixture
def session(tmp_path):
    scratch = tmp_path / "scratch"
    readonly = tmp_path / "readonly"
    outside = tmp_path / "outside"
    for d in (scratch, readonly, outside):
        d.mkdir()
    (readonly / "notes.txt").write_text("granted, read-only")
    (outside / "id_rsa").write_text("PRIVATE KEY")
    return {
        "roots": [RootDir(path=scratch, writable=True),
                  RootDir(path=readonly, writable=False)],
        "scratch": scratch,
        "readonly": readonly,
        "outside": outside,
    }


# -- the helper ---------------------------------------------------------------

def test_read_is_allowed_from_any_granted_root(session):
    resolved, err = _resolve_in_roots(
        str(session["readonly"] / "notes.txt"), session["roots"], need_write=False)
    assert err is None
    assert resolved == (session["readonly"] / "notes.txt").resolve()


def test_write_is_refused_into_a_read_only_root(session):
    resolved, err = _resolve_in_roots(
        str(session["readonly"] / "shot.png"), session["roots"], need_write=True)
    assert resolved is None
    assert "outside the session's writable directories" in err["error"]


def test_ungranted_path_is_refused(session):
    _, err = _resolve_in_roots(
        str(session["outside"] / "id_rsa"), session["roots"], need_write=False)
    assert err and "outside the session's directories" in err["error"]


def test_traversal_out_of_a_root_is_refused(session):
    escape = str(session["scratch"] / ".." / "outside" / "id_rsa")
    _, err = _resolve_in_roots(escape, session["roots"], need_write=False)
    assert err, "`..` must be resolved before the containment check, not after"


def test_symlink_pointing_out_of_a_root_is_refused(session):
    link = session["scratch"] / "innocent.txt"
    try:
        link.symlink_to(session["outside"] / "id_rsa")
    except (OSError, NotImplementedError):  # Windows without developer mode
        pytest.skip("symlinks unavailable")
    _, err = _resolve_in_roots(str(link), session["roots"], need_write=False)
    assert err, "resolve() must follow the symlink before containment is decided"


def test_no_roots_means_no_filesystem_access(session):
    _, err = _resolve_in_roots(str(session["outside"] / "id_rsa"), [], need_write=False)
    assert err and "no granted directories" in err["error"]


# -- the tools ----------------------------------------------------------------

def test_upload_refuses_a_path_outside_the_session(session):
    upload = _tools(session["roots"])["browser_upload_file"]
    out = upload("input[type=file]", str(session["outside"] / "id_rsa"))
    assert "error" in out and "outside the session" in out["error"]


def test_upload_refuses_before_reporting_whether_the_file_exists(session):
    """The refusal must not double as an existence oracle for ungranted paths."""
    upload = _tools(session["roots"])["browser_upload_file"]
    missing = upload("input", str(session["outside"] / "nope.txt"))
    present = upload("input", str(session["outside"] / "id_rsa"))
    # Same refusal for both: containment is decided before the file is stat'd, so the
    # error never distinguishes "exists but ungranted" from "does not exist".
    assert "outside the session" in missing["error"]
    assert "outside the session" in present["error"]
    assert "not found" not in missing["error"]


def test_screenshot_refuses_a_path_outside_the_session(session):
    shot = _tools(session["roots"])["browser_screenshot"]
    out = shot(str(session["outside"] / "evil.png"))
    assert "error" in out and "outside the session" in out["error"]


def test_screenshot_is_refused_before_the_browser_is_touched(session):
    """Containment must be decided by the tool, not incidentally by the browser layer.

    Previously the check did not exist and `out.parent.mkdir(parents=True)` sat inside the
    Playwright callback, so on a machine with Playwright installed a refused path still had
    its directory tree created. The refusal now happens first, which is observable here
    because the error is the containment error rather than Playwright's.
    """
    shot = _tools(session["roots"])["browser_screenshot"]
    out = shot(str(session["outside"] / "a" / "b" / "c.png"))
    assert "outside the session" in out["error"]
    assert "laywright" not in out["error"]
    assert not (session["outside"] / "a").exists()


def test_tools_are_constructible_without_roots(session):
    """CLI/direct callers pass no roots; construction must still work (and deny)."""
    upload = _tools(None)["browser_upload_file"]
    assert "error" in upload("input", str(session["outside"] / "id_rsa"))


# -- the default screenshot location ------------------------------------------

def test_default_screenshot_path_is_unpredictable_and_private(tmp_path, monkeypatch):
    """A fixed name in a shared temp dir is pre-creatable by another local user.

    On Linux /tmp is shared, so `coworker-browser-screenshot.png` could be planted as a
    symlink (the write then follows it) and the image — potentially a logged-in inbox — was
    left at the umask default. mkstemp gives a random name created 0600 with O_EXCL.
    """
    import re
    import stat

    shared = tmp_path / "shared_tmp"
    shared.mkdir()
    monkeypatch.setattr(tempfile_mod, "gettempdir", lambda: str(shared))

    shot = _tools([RootDir(path=tmp_path / "scratch", writable=True)])["browser_screenshot"]
    shot()  # no path -> default location
    shot()

    created = sorted(shared.iterdir())
    assert len(created) == 2, "each call must get its own file, not a shared fixed name"
    assert created[0].name != created[1].name

    for f in created:
        assert re.match(r"coworker-browser-screenshot\..+\.png$", f.name), f.name
        mode = stat.S_IMODE(f.stat().st_mode)
        assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0, f"{f.name} was {oct(mode)}"
