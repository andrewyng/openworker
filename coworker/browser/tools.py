"""Session-bound model tool factory for :mod:`coworker.browser`.

Only stable tab/snapshot/ref targeting is exposed.  The trusted session id is
captured by closure and therefore cannot be supplied by page content or a model.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

import aisuite as ai

from .documentation import (
    DOCUMENTATION_TOPICS,
    SURFACE_LABELS,
    browser_documentation_document,
    browser_surfaces_document,
)
from .errors import BrowserRuntimeError
from .runtime import BrowserRuntime


def _schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def _attach(
    fn: Callable[..., dict[str, Any]],
    schema: dict[str, Any],
    *,
    requires_approval: bool,
) -> Callable[..., dict[str, Any]]:
    name = schema["function"]["name"]
    fn.__name__ = name
    fn.__doc__ = schema["function"]["description"]
    fn.__coworker_schema__ = schema
    # Every Browser Use tool is deliberately non-low-risk so TurnEngine never
    # parallelizes reads with navigation or another browser action.
    fn.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name=name,
        category="connector",
        risk_level="medium",
        capabilities=["browser"],
        requires_approval=requires_approval,
    )
    return fn


def _safe(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except BrowserRuntimeError as exc:
        return exc.to_dict()


_EXTERNAL_SURFACE_TOOLS = frozenset(
    {
        "browser_select_surface",
        "browser_tabs",
        "browser_select_tab",
        "browser_snapshot",
        "browser_screenshot",
        "browser_click",
        "browser_fill",
        "browser_press",
        "browser_scroll",
        "browser_surfaces",
        "browser_documentation",
        "browser_close",
    }
)


def make_browser_tools(
    runtime: BrowserRuntime,
    session_id: str,
    *,
    surface_available: Callable[[str], bool] | None = None,
) -> list[Callable[..., dict[str, Any]]]:
    """Build flat-schema tools bound to one trusted conversation id."""

    bound = runtime.bind(session_id)
    tools: list[Callable[..., dict[str, Any]]] = []

    def add(
        fn: Callable[..., dict[str, Any]],
        name: str,
        description: str,
        properties: dict[str, Any],
        required: list[str],
        *,
        write: bool,
        non_launching: bool = False,
    ) -> None:
        attached = _attach(
            fn,
            _schema(name, description, properties, required),
            requires_approval=write,
        )
        if non_launching:
            # SessionManager uses this marker to keep pure capability and
            # documentation calls from creating a Playwright context.
            attached.__coworker_browser_non_launching__ = True

        def browser_confirmation(
            arguments: dict[str, Any], *, _name: str = name
        ) -> dict[str, Any]:
            if _name == "browser_dialog":
                return {
                    "requires_confirmation": True,
                    "reasons": ["javascript_dialog"],
                }
            if _name in {"browser_upload", "browser_download", "browser_cdp"}:
                return {
                    "requires_confirmation": True,
                    "reasons": [
                        {
                            "browser_upload": "local_file_disclosure",
                            "browser_download": "local_file_write",
                            "browser_cdp": "developer_mode_command",
                        }[_name]
                    ],
                }
            if _name in {
                "browser_click",
                "browser_fill",
                "browser_press",
                "browser_select",
                "browser_coordinate_click",
                "browser_coordinate_drag",
                "browser_type_text",
                "browser_keypress",
            }:
                return bound.classify_action(_name, arguments)
            return {"requires_confirmation": False, "reasons": []}

        # TurnEngine consumes this trusted, session-bound preflight before it
        # decides whether the task's one Browser Use grant is sufficient.
        attached.__coworker_browser_confirmation__ = browser_confirmation
        tools.append(attached)

    def browser_select_surface(surface: str) -> dict[str, Any]:
        # SessionManager owns the trusted surface selection and intercepts this
        # non-launching tool before it can touch either browser runtime.
        return {"ok": True, "surface": surface}

    add(
        browser_select_surface,
        "browser_select_surface",
        (
            "Choose the browser surface for subsequent tools: iab for the "
            "isolated in-app browser, or a connected Chrome extension."
        ),
        {
            "surface": {
                "type": "string",
                "enum": list(SURFACE_LABELS),
            }
        },
        ["surface"],
        write=False,
        non_launching=True,
    )

    def browser_open_url(
        url: str, wait_until: str = "domcontentloaded", new_tab: bool = False
    ) -> dict[str, Any]:
        return _safe(
            lambda: bound.navigate(
                url, wait_until=wait_until, new_tab=bool(new_tab)
            )
        )

    add(
        browser_open_url,
        "browser_open_url",
        "Open an HTTP(S) URL and return a fresh accessibility snapshot.",
        {
            "url": {"type": "string"},
            "wait_until": {
                "type": "string",
                "enum": ["commit", "domcontentloaded", "load", "networkidle"],
            },
            "new_tab": {"type": "boolean"},
        },
        ["url"],
        write=True,
    )

    def browser_history(direction: str) -> dict[str, Any]:
        return _safe(lambda: bound.history(direction))

    add(
        browser_history,
        "browser_history",
        "Go back, forward, or reload, then return a fresh snapshot.",
        {
            "direction": {
                "type": "string",
                "enum": ["back", "forward", "reload"],
            }
        },
        ["direction"],
        write=True,
    )

    def browser_snapshot(
        tab_id: str = "", max_chars: int = 32768
    ) -> dict[str, Any]:
        return _safe(
            lambda: bound.snapshot(
                tab_id=tab_id or None, max_chars=max_chars
            )
        )

    add(
        browser_snapshot,
        "browser_snapshot",
        "Read the current page accessibility snapshot and stable element refs.",
        {
            "tab_id": {"type": "string"},
            "max_chars": {"type": "integer", "minimum": 256, "maximum": 32768},
        },
        [],
        write=False,
    )

    def browser_snapshot_more(cursor: str) -> dict[str, Any]:
        return _safe(lambda: bound.snapshot_more(cursor))

    add(
        browser_snapshot_more,
        "browser_snapshot_more",
        "Read the next chunk of the current accessibility snapshot.",
        {"cursor": {"type": "string"}},
        ["cursor"],
        write=False,
    )

    def browser_screenshot(
        tab_id: str = "", image_format: str = "jpeg", quality: int = 75
    ) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            result = bound.screenshot(
                tab_id=tab_id or None,
                image_format=image_format,
                quality=quality,
            )
            data = result.pop("data")
            result["image_base64"] = base64.b64encode(data).decode("ascii")
            return result

        return _safe(run)

    add(
        browser_screenshot,
        "browser_screenshot",
        "Capture the visible browser viewport for visual verification.",
        {
            "tab_id": {"type": "string"},
            "image_format": {"type": "string", "enum": ["jpeg", "png"]},
            "quality": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        [],
        write=False,
    )

    target_properties = {
        "tab_id": {"type": "string"},
        "snapshot_id": {"type": "string"},
        "ref": {"type": "string"},
    }

    def browser_click(
        tab_id: str, snapshot_id: str, ref: str
    ) -> dict[str, Any]:
        return _safe(lambda: bound.click(tab_id, snapshot_id, ref))

    add(
        browser_click,
        "browser_click",
        "Click one element from the supplied tab and fresh snapshot.",
        dict(target_properties),
        ["tab_id", "snapshot_id", "ref"],
        write=True,
    )

    def browser_fill(
        tab_id: str, snapshot_id: str, ref: str, value: str
    ) -> dict[str, Any]:
        return _safe(lambda: bound.fill(tab_id, snapshot_id, ref, value))

    add(
        browser_fill,
        "browser_fill",
        "Replace the contents of one editable element.",
        {**target_properties, "value": {"type": "string"}},
        ["tab_id", "snapshot_id", "ref", "value"],
        write=True,
    )

    def browser_press(
        tab_id: str, snapshot_id: str, ref: str, key: str
    ) -> dict[str, Any]:
        return _safe(lambda: bound.press(tab_id, snapshot_id, ref, key))

    add(
        browser_press,
        "browser_press",
        "Press a keyboard key on one referenced element.",
        {**target_properties, "key": {"type": "string"}},
        ["tab_id", "snapshot_id", "ref", "key"],
        write=True,
    )

    def browser_select(
        tab_id: str, snapshot_id: str, ref: str, value: str
    ) -> dict[str, Any]:
        return _safe(lambda: bound.select(tab_id, snapshot_id, ref, value))

    add(
        browser_select,
        "browser_select",
        "Select one option value in a referenced select element.",
        {**target_properties, "value": {"type": "string"}},
        ["tab_id", "snapshot_id", "ref", "value"],
        write=True,
    )

    def browser_hover(
        tab_id: str, snapshot_id: str, ref: str
    ) -> dict[str, Any]:
        return _safe(lambda: bound.hover(tab_id, snapshot_id, ref))

    add(
        browser_hover,
        "browser_hover",
        "Hover over one referenced element.",
        dict(target_properties),
        ["tab_id", "snapshot_id", "ref"],
        write=False,
    )

    def browser_scroll(
        delta_x: float = 0,
        delta_y: float = 0,
        tab_id: str = "",
        snapshot_id: str = "",
        ref: str = "",
    ) -> dict[str, Any]:
        return _safe(
            lambda: bound.scroll(
                delta_x=delta_x,
                delta_y=delta_y,
                tab_id=tab_id or None,
                snapshot_id=snapshot_id or None,
                ref=ref or None,
            )
        )

    add(
        browser_scroll,
        "browser_scroll",
        "Scroll the viewport, or scroll at a referenced element.",
        {
            "delta_x": {"type": "number"},
            "delta_y": {"type": "number"},
            **target_properties,
        },
        [],
        write=True,
    )

    def browser_tabs() -> dict[str, Any]:
        return _safe(bound.tabs)

    add(
        browser_tabs,
        "browser_tabs",
        "List open browser tabs and their opaque tab ids.",
        {},
        [],
        write=False,
    )

    def browser_select_tab(tab_id: str) -> dict[str, Any]:
        return _safe(lambda: bound.select_tab(tab_id))

    add(
        browser_select_tab,
        "browser_select_tab",
        "Select a browser tab and return its fresh snapshot.",
        {"tab_id": {"type": "string"}},
        ["tab_id"],
        write=True,
    )

    def browser_close_tab(tab_id: str) -> dict[str, Any]:
        return _safe(lambda: bound.close_tab(tab_id))

    add(
        browser_close_tab,
        "browser_close_tab",
        "Close one browser tab.",
        {"tab_id": {"type": "string"}},
        ["tab_id"],
        write=True,
    )

    def browser_dialog(
        action: str, prompt_text: str | None = None
    ) -> dict[str, Any]:
        return _safe(lambda: bound.dialog(action, prompt_text=prompt_text))

    add(
        browser_dialog,
        "browser_dialog",
        (
            "Accept or dismiss the current tab's pending JavaScript dialog. "
            "This always requires approval."
        ),
        {
            "action": {"type": "string"},
            "prompt_text": {"type": "string"},
        },
        ["action"],
        write=True,
    )

    def browser_set_visibility(visible: bool) -> dict[str, Any]:
        return _safe(lambda: bound.set_visibility(bool(visible)))

    add(
        browser_set_visibility,
        "browser_set_visibility",
        "Show or hide the shared in-app Browser without changing page state.",
        {"visible": {"type": "boolean"}},
        ["visible"],
        write=False,
    )

    def browser_set_viewport(
        width: int | None = None,
        height: int | None = None,
        dpr: float | None = None,
        reset: bool = False,
    ) -> dict[str, Any]:
        return _safe(
            lambda: bound.set_viewport(
                width=width,
                height=height,
                dpr=dpr,
                reset=bool(reset),
            )
        )

    add(
        browser_set_viewport,
        "browser_set_viewport",
        (
            "Override the active tab's responsive CSS viewport, or reset it. "
            "The current session DPR is fixed and may only be echoed back."
        ),
        {
            "width": {"type": "integer", "minimum": 320, "maximum": 7680},
            "height": {"type": "integer", "minimum": 240, "maximum": 4320},
            "dpr": {"type": "number", "minimum": 0.5, "maximum": 3},
            "reset": {"type": "boolean"},
        },
        [],
        write=False,
    )

    def browser_finalize_tabs(
        keep_tab_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return _safe(lambda: bound.finalize_tabs(list(keep_tab_ids or [])))

    add(
        browser_finalize_tabs,
        "browser_finalize_tabs",
        (
            "Close intermediate tabs and retain only the supplied deliverable "
            "tabs. An empty list retains the active tab."
        ),
        {
            "keep_tab_ids": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 50,
            }
        },
        [],
        write=True,
    )

    coordinate_properties = {
        "tab_id": {"type": "string"},
        "x": {"type": "number"},
        "y": {"type": "number"},
    }

    def browser_coordinate_move(
        tab_id: str, x: float, y: float
    ) -> dict[str, Any]:
        return _safe(lambda: bound.coordinate_move(tab_id, x, y))

    add(
        browser_coordinate_move,
        "browser_coordinate_move",
        "Move the visible agent pointer to viewport coordinates without clicking.",
        dict(coordinate_properties),
        ["tab_id", "x", "y"],
        write=False,
    )

    def browser_coordinate_click(
        tab_id: str,
        x: float,
        y: float,
        button: str = "left",
        click_count: int = 1,
    ) -> dict[str, Any]:
        return _safe(
            lambda: bound.coordinate_click(
                tab_id,
                x,
                y,
                button=button,
                click_count=click_count,
            )
        )

    add(
        browser_coordinate_click,
        "browser_coordinate_click",
        (
            "Click exact viewport coordinates when a semantic ref cannot express "
            "the requested action. The live element under the point is checked "
            "for consequential behavior before execution."
        ),
        {
            **coordinate_properties,
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
            },
            "click_count": {"type": "integer", "minimum": 1, "maximum": 3},
        },
        ["tab_id", "x", "y"],
        write=True,
    )

    def browser_coordinate_drag(
        tab_id: str, path: list[dict[str, float]]
    ) -> dict[str, Any]:
        return _safe(lambda: bound.coordinate_drag(tab_id, path))

    add(
        browser_coordinate_drag,
        "browser_coordinate_drag",
        (
            "Drag through a 2-100 point viewport path. The live destination "
            "element is checked for consequential behavior before execution."
        ),
        {
            "tab_id": {"type": "string"},
            "path": {
                "type": "array",
                "minItems": 2,
                "maxItems": 100,
                "items": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    },
                    "required": ["x", "y"],
                    "additionalProperties": False,
                },
            },
        },
        ["tab_id", "path"],
        write=True,
    )

    def browser_type_text(tab_id: str, text: str) -> dict[str, Any]:
        return _safe(lambda: bound.type_text(tab_id, text))

    add(
        browser_type_text,
        "browser_type_text",
        (
            "Insert text into the currently focused element. Sensitive target "
            "types are checked before execution."
        ),
        {
            "tab_id": {"type": "string"},
            "text": {"type": "string"},
        },
        ["tab_id", "text"],
        write=True,
    )

    def browser_keypress(tab_id: str, keys: list[str]) -> dict[str, Any]:
        return _safe(lambda: bound.keypress(tab_id, keys))

    add(
        browser_keypress,
        "browser_keypress",
        "Press 1-100 Playwright key names on the focused element in order.",
        {
            "tab_id": {"type": "string"},
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 100,
            },
        },
        ["tab_id", "keys"],
        write=True,
    )

    def browser_clipboard(
        action: str, text: str | None = None
    ) -> dict[str, Any]:
        return _safe(lambda: bound.clipboard(action, text=text))

    add(
        browser_clipboard,
        "browser_clipboard",
        (
            "Read, write, or paste the isolated session clipboard. This never "
            "reads or writes the operating-system clipboard."
        ),
        {
            "action": {
                "type": "string",
                "enum": ["read", "write", "paste"],
            },
            "text": {"type": "string"},
        },
        ["action"],
        write=True,
    )

    def browser_console_logs(
        tab_id: str,
        levels: list[str] | None = None,
        filter: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        return _safe(
            lambda: bound.console_logs(
                tab_id,
                levels=levels,
                filter_text=filter,
                limit=limit,
            )
        )

    add(
        browser_console_logs,
        "browser_console_logs",
        "Read recent console messages and page errors from one tab.",
        {
            "tab_id": {"type": "string"},
            "levels": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "log",
                        "debug",
                        "info",
                        "warning",
                        "error",
                    ],
                },
            },
            "filter": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        ["tab_id"],
        write=False,
    )

    def browser_download(
        tab_id: str,
        snapshot_id: str,
        ref: str,
        destination: str | None = None,
    ) -> dict[str, Any]:
        return _safe(
            lambda: bound.download(
                tab_id,
                snapshot_id,
                ref,
                destination=destination,
            )
        )

    add(
        browser_download,
        "browser_download",
        (
            "Click one fresh ref that must produce a download, then save it "
            "inside an approved browser file root. Always asks at action time."
        ),
        {
            **target_properties,
            "destination": {"type": "string"},
        },
        ["tab_id", "snapshot_id", "ref"],
        write=True,
    )

    def browser_upload(
        tab_id: str,
        snapshot_id: str,
        ref: str,
        paths: list[str],
    ) -> dict[str, Any]:
        return _safe(lambda: bound.upload(tab_id, snapshot_id, ref, paths))

    add(
        browser_upload,
        "browser_upload",
        (
            "Attach approved local files to one fresh file-input ref. Local "
            "file disclosure always asks at action time."
        ),
        {
            **target_properties,
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 20,
            },
        },
        ["tab_id", "snapshot_id", "ref", "paths"],
        write=True,
    )

    def browser_cdp(
        tab_id: str, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return _safe(lambda: bound.cdp(tab_id, method, dict(params or {})))

    add(
        browser_cdp,
        "browser_cdp",
        (
            "Send one raw Chrome DevTools Protocol command in Browser Developer "
            "Mode. Every command asks at action time."
        ),
        {
            "tab_id": {"type": "string"},
            "method": {"type": "string"},
            "params": {
                "type": "object",
                "additionalProperties": True,
            },
        },
        ["tab_id", "method"],
        write=True,
    )

    def browser_dom_evaluate(
        tab_id: str,
        expression: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _safe(
            lambda: bound.dom_evaluate(
                tab_id, expression, args=dict(args or {})
            )
        )

    add(
        browser_dom_evaluate,
        "browser_dom_evaluate",
        (
            "Run one fixed read-only DOM query. This is not arbitrary JavaScript. "
            "Expressions: document.title, location.href, "
            "document.body.innerText, document.documentElement.lang, query.text, "
            "query.html, query.value, query.attribute, query.count, query.box, "
            "or query.style."
        ),
        {
            "tab_id": {"type": "string"},
            "expression": {
                "type": "string",
                "enum": [
                    "document.title",
                    "location.href",
                    "document.body.innerText",
                    "document.documentElement.lang",
                    "query.text",
                    "query.html",
                    "query.value",
                    "query.attribute",
                    "query.count",
                    "query.box",
                    "query.style",
                ],
            },
            "args": {
                "type": "object",
                "additionalProperties": True,
            },
        },
        ["tab_id", "expression"],
        write=False,
    )

    def browser_close() -> dict[str, Any]:
        return _safe(bound.close)

    add(
        browser_close,
        "browser_close",
        (
            "Stop this task's use of the selected browser surface. The isolated "
            "context closes; the Chrome extension connection remains available."
        ),
        {},
        [],
        write=True,
    )

    def current_surfaces() -> dict[str, tuple[Callable[..., Any], ...]]:
        # The list is intentionally resolved when the documentation tool is
        # invoked, after every current tool has been attached. Future surfaces
        # can be supplied here without rewriting the documentation renderer.
        surfaces: dict[str, tuple[Callable[..., Any], ...]] = {
            "iab": tuple(tools)
        }
        if surface_available is not None:
            external_tools = tuple(
                tool
                for tool in tools
                if tool.__name__ in _EXTERNAL_SURFACE_TOOLS
            )
            for surface in ("chrome",):
                try:
                    if surface_available(surface):
                        surfaces[surface] = external_tools
                except Exception:
                    # Documentation must stay read-only and fail closed when a
                    # transient extension status check cannot be completed.
                    continue
        return surfaces

    def browser_surfaces() -> dict[str, Any]:
        return browser_surfaces_document(current_surfaces())

    add(
        browser_surfaces,
        "browser_surfaces",
        (
            "List supported browser surface selectors and exact runtime "
            "availability without opening a browser."
        ),
        {},
        [],
        write=False,
        non_launching=True,
    )

    def browser_documentation(
        surface: str = "iab", topic: str = ""
    ) -> dict[str, Any]:
        return browser_documentation_document(
            surface, topic, current_surfaces()
        )

    add(
        browser_documentation,
        "browser_documentation",
        (
            "Load authoritative runtime-generated Browser Use documentation "
            "for one surface without opening a browser."
        ),
        {
            "surface": {
                "type": "string",
                "enum": list(SURFACE_LABELS),
            },
            "topic": {
                "type": "string",
                "enum": ["", *DOCUMENTATION_TOPICS],
            },
        },
        [],
        write=False,
        non_launching=True,
    )

    return tools
