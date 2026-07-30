"""The browser tool: Browser Use CLI"""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

import aisuite as ai

from .browser_cli import available, call, emit, ensure_cloud_browser, run_code

_TAB_MARKER = "\U0001f434 "

# Screenshots the model can actually see; capped so a screenshot-happy session cannot
# flood the context
_MAX_IMAGES_PER_CALL = 2
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_IMAGE_PATH_RE = re.compile(r"(?<![\w/])(/[^\s'\"]+\.(?:png|jpe?g|webp))(?![\w])", re.IGNORECASE)
_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

# Any file the executed code printed the path of and wrote during the call: the model
# learns what deliverables exist, and the chat shows them
_FILE_RE = re.compile(r"(?<![\w/])(/[^\s'\"]+\.[A-Za-z0-9]{1,8})(?![\w])")
_MAX_FILES = 16

STEP_LABEL_LIMIT = 80


def _title(value: Any) -> str:
    text = str(value or "")
    return text[len(_TAB_MARKER):] if text.startswith(_TAB_MARKER) else text


def _clip(text: str, limit: int = STEP_LABEL_LIMIT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + " …"


def step_label(code: str) -> str:
    """The leading `#` comment the model wrote, else its first line of code."""
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            label = stripped.lstrip("#").strip()
            return _clip(label) if label else "Browser step"
        return _clip(stripped)
    return "Browser step"


def _collect_files(output: str, *, newer_than: float) -> list[dict[str, Any]]:
    import mimetypes

    found: list[dict[str, Any]] = []
    for match in dict.fromkeys(_FILE_RE.findall(output)):
        path = Path(match)
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < newer_than - 1 or not path.is_file():
            continue
        found.append(
            {
                "path": str(path),
                "name": path.name,
                "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "bytes": stat.st_size,
            }
        )
        if len(found) == _MAX_FILES:
            break
    return found


def _image_sidecar(paths: list[str], *, newer_than: float) -> list[str]:
    """Fresh, small-enough images as data URLs for the `_images` sidecar."""
    urls: list[str] = []
    for raw in dict.fromkeys(paths):
        path = Path(raw)
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < newer_than - 1 or stat.st_size > _MAX_IMAGE_BYTES:
            continue
        media = _MEDIA_TYPES.get(path.suffix.lower())
        if not media:
            continue
        urls.append(f"data:{media};base64," + base64.b64encode(path.read_bytes()).decode("ascii"))
        if len(urls) == _MAX_IMAGES_PER_CALL:
            break
    return urls


# -- GUI/server state (not tools) ------------------------------------------------------

_STATE: dict[str, Any] = {
    "open": False,
    "url": "",
    "title": "",
    "status": "closed",
    "last_action": "",
    "last_result": "",
    "last_error": "",
    "screenshot_data_url": "",
    "updated_at": None,
    "controls": [],
    # No local window on cloud, so live_url is the only view.
    "backend": "local",
    "live_url": "",
    "session_id": "",
}

_backend_ready = False


def _touch(**changes: Any) -> None:
    _STATE.update(changes)
    _STATE["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _note_install_if_needed() -> None:
    """First use on a machine without the CLI: the rail should say what the wait is."""
    if available() is None:
        _touch(
            status="installing",
            last_action="Installing the Browser Use CLI (one-time, ~30s)",
            last_result="running",
        )


def _ensure_backend() -> Optional[str]:
    """Start the configured cloud browser once per process; None on success."""
    global _backend_ready
    if _backend_ready:
        return None
    info = ensure_cloud_browser()
    _backend_ready = True
    if info.get("error"):
        return str(info["error"])
    _touch(
        backend=info.get("backend", "local"),
        live_url=info.get("liveUrl", ""),
        session_id=info.get("id", ""),
    )
    return None


def browser_state() -> dict[str, Any]:
    """Live page state for the GUI. Only once the agent has actually used the browser —
    the CLI drives the user's own Chrome, and the GUI polls this on a timer."""
    if not _STATE["last_action"]:
        return dict(_STATE)
    body = "_p = page_info()\n_out = {'url': _p.get('url', ''), 'title': _p.get('title', '')}\n" + emit("_out")
    page = call(body, timeout=45.0)
    if "error" in page:
        return dict(_STATE)
    _touch(open=True, status="open", url=page.get("url", ""), title=_title(page.get("title")))
    return dict(_STATE)


def browser_take_screenshot() -> dict[str, Any]:
    import tempfile

    out = Path(tempfile.gettempdir()) / "coworker-browser-state.png"
    result = call(
        "_path = capture_screenshot(path=" + json.dumps(str(out)) + ")\n_out = {'path': _path}\n" + emit("_out"),
        timeout=90.0,
    )
    if "error" in result:
        _touch(last_action="screenshot", last_result="error", last_error=str(result["error"]))
        return result
    png = Path(result["path"]).read_bytes()
    _touch(
        screenshot_data_url="data:image/png;base64," + base64.b64encode(png).decode("ascii"),
        last_action="screenshot",
        last_result="ok",
        last_error="",
    )
    browser_state()
    return {"ok": True, **dict(_STATE)}


def browser_close_session() -> dict[str, Any]:
    """Close the tab. The CLI's daemon (and any cloud browser) outlives us by design."""
    result = call("close_tab()\n_out = {'ok': True}\n" + emit("_out"), timeout=45.0)
    _touch(open=False, status="closed", url="", title="", controls=[])
    return result


# -- the tool --------------------------------------------------------------------------

_EXEC_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_exec",
        "description": (
            "Run Python code in a real web browser via the Browser Use CLI. Browser helpers "
            "are pre-imported: page_info(), new_tab(url), goto_url(url), js(expression), "
            "cdp(method, **params), click_at_xy(x, y), fill_input(selector, text), "
            "type_text(text), press_key(key), scroll(x, y, dy), wait_for_load(), "
            "wait_for_element(selector, timeout), wait_for_network_idle(), "
            "capture_screenshot(path, full), list_tabs(), switch_tab(id), close_tab(), "
            "upload_file(selector, path), http_get(url). Use print(...) for any data you "
            "need back — the tool returns what the code prints, and any screenshot whose "
            "path the code prints comes back as an image you can see. Your calls run in "
            "one persistent Python session: variables you assign survive to your next "
            "call, so batch a whole sub-procedure (navigate, wait, extract) per call and "
            "build on earlier results. If a call times out the session restarts (the "
            "browser survives); re-derive what you need from the page. Start `code` with "
            "a one-line `#` comment describing the step in plain, non-technical language "
            "(under 60 characters); it is shown as the step's label while the call runs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "timeout_seconds": {"type": "integer"},
            },
            "required": ["code"],
        },
    },
}


def make_browser_automation_tools(roots: Optional[list[Any]] = None) -> list[Callable[..., Any]]:
    def files_dir() -> Optional[Path]:
        """This chat's browser-files home: <primary scratch>/browser (email_tools convention).

        Living in the session's scratch dir buys the whole lifecycle for free: the
        Artifacts rail lists it, and deleting the chat deletes it.
        """
        primary = roots[0] if roots else None
        if primary is None or not getattr(primary, "writable", False):
            return None
        directory = Path(getattr(primary, "path")) / "browser"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def persist(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Copy files from outside the session's directories into files_dir — /tmp gets
        reaped, and the chat's artifacts must live and die with the chat."""
        import shutil as _shutil

        home = files_dir()
        if home is None:
            return files
        bases = [Path(getattr(r, "path")).resolve() for r in (roots or [])]
        out: list[dict[str, Any]] = []
        for entry in files:
            path = Path(entry["path"]).resolve()
            if any(base in path.parents or path == base for base in bases):
                out.append(entry)
                continue
            target = home / path.name
            counter = 1
            while target.exists() and target.resolve() != path:
                target = home / f"{path.stem}-{counter}{path.suffix}"
                counter += 1
            try:
                _shutil.copy2(path, target)
            except OSError:
                out.append(entry)
                continue
            out.append({**entry, "path": str(target), "name": target.name})
        return out

    def browser_exec(code: str, timeout_seconds: int = 120) -> dict[str, Any]:
        started = time.time()
        _note_install_if_needed()
        backend_error = _ensure_backend()
        if backend_error:
            return {"error": backend_error}
        result = run_code(code, timeout=float(max(1, min(int(timeout_seconds or 120), 600))))
        if "returncode" not in result:
            out = dict(result)
        else:
            out = {
                "ok": result.get("returncode") == 0,
                "stdout": (result.get("stdout") or "")[:20000],
                "stderr": (result.get("stderr") or "")[:4000],
            }
            if "error" in result:
                out["error"] = result["error"]
        label = step_label(code)
        if out.get("ok"):
            _touch(open=True, status="open", last_action=label, last_result="ok", last_error="")
        else:
            _touch(last_action=label, last_result="error", last_error=str(out.get("error", ""))[:500])
        images = _image_sidecar(_IMAGE_PATH_RE.findall(out.get("stdout") or ""), newer_than=started)
        if images:
            out["_images"] = images
        files = persist(_collect_files(out.get("stdout") or "", newer_than=started))
        if files:
            out["files"] = files
        display: dict[str, Any] = {"label": _clip(label), "connector": "browser"}
        if files:
            display["files"] = files
        out["_display"] = display
        return out

    browser_exec.__name__ = "browser_exec"
    browser_exec.__doc__ = _EXEC_SCHEMA["function"]["description"]
    browser_exec.__coworker_schema__ = _EXEC_SCHEMA
    from .tool_defs import approval_for_tool, kind_for_tool

    browser_exec.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="browser_exec",
        category="connector",
        # An ungated write (auto_approve): the risk classification stays honest.
        risk_level="medium" if kind_for_tool("browser_exec") == "write" else "low",
        capabilities=["browser"],
        requires_approval=approval_for_tool("browser_exec", default=False),
    )
    return [browser_exec]
