from __future__ import annotations

from coworker.connectors.browser_automation import (
    _BrowserController,
    make_browser_automation_tools,
)


class _FakePage:
    def __init__(self, url: str, title: str) -> None:
        self.url = url
        self._title = title
        self._closed = False
        self._handlers: dict[str, list] = {}
        self.brought_to_front = False

    def on(self, event: str, callback) -> None:
        self._handlers.setdefault(event, []).append(callback)

    def is_closed(self) -> bool:
        return self._closed

    def title(self) -> str:
        return self._title

    def bring_to_front(self) -> None:
        self.brought_to_front = True

    def evaluate(self, _script: str) -> dict:
        return {
            "title": self._title,
            "url": self.url,
            "text": self._title,
            "controls": [],
        }

    def close(self) -> None:
        self._closed = True
        for callback in self._handlers.get("close", []):
            callback(self)


class _FakeContext:
    def __init__(self, *pages: _FakePage) -> None:
        self.pages = list(pages)


def _controller_with_pages(*pages: _FakePage) -> _BrowserController:
    controller = _BrowserController()
    controller._context = _FakeContext(*pages)
    for page in pages:
        controller._activate_page(page)
    return controller


def test_new_popup_becomes_active_for_subsequent_browser_calls():
    first = _FakePage("https://example.com/start", "Start")
    popup = _FakePage("https://example.com/result", "Result")
    controller = _controller_with_pages(first)

    controller._context.pages.append(popup)
    controller._activate_page(popup)

    assert controller.call("probe", lambda page: {"url": page.url}) == {
        "url": "https://example.com/result"
    }
    assert controller.tabs() == {
        "tabs": [
            {
                "index": 0,
                "active": False,
                "url": "https://example.com/start",
                "title": "Start",
            },
            {
                "index": 1,
                "active": True,
                "url": "https://example.com/result",
                "title": "Result",
            },
        ]
    }


def test_switch_tab_changes_target_and_brings_it_to_front():
    first = _FakePage("https://example.com/one", "One")
    second = _FakePage("https://example.com/two", "Two")
    controller = _controller_with_pages(first, second)

    assert controller.switch_tab(0) == {
        "ok": True,
        "index": 0,
        "url": "https://example.com/one",
        "title": "One",
    }
    assert first.brought_to_front is True
    assert controller.call("probe", lambda page: {"url": page.url})["url"].endswith(
        "/one"
    )


def test_closing_active_tab_falls_back_to_most_recent_open_tab():
    first = _FakePage("https://example.com/one", "One")
    second = _FakePage("https://example.com/two", "Two")
    controller = _controller_with_pages(first, second)

    second.close()

    assert controller.call("probe", lambda page: {"url": page.url}) == {
        "url": "https://example.com/one"
    }


def test_switch_tab_validates_index():
    controller = _controller_with_pages(_FakePage("https://example.com", "Example"))

    assert "integer" in controller.switch_tab(True)["error"]
    assert controller.switch_tab(2) == {
        "error": "tab index 2 is out of range",
        "tab_count": 1,
    }


def test_browser_tab_tools_are_exposed_with_selection_schema():
    tools = {tool.__name__: tool for tool in make_browser_automation_tools()}

    assert "browser_list_tabs" in tools
    assert "browser_switch_tab" in tools
    schema = tools["browser_switch_tab"].__coworker_schema__["function"]["parameters"]
    assert schema["properties"]["index"]["type"] == "integer"
    assert schema["required"] == ["index"]
