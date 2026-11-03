"""`web_fetch` through the search service (Settings ▸ Customization).

No network: httpx is stubbed. Covers the three providers' request/response shapes
(Exa /contents, Firecrawl /v2/scrape, Tavily /extract), when `fetch_provider` picks one,
the address guard running before the provider is asked, the fallback to a local fetch
with a notice, and the approval card's `fetch_provider` extra.
"""

from __future__ import annotations

import httpx
import pytest

from coworker.secrets import SecretStore
from coworker.web import fetch_provider, fetch_provider_name
from coworker.web.fetch import make_web_fetch_tool
from coworker.web.providers import (
    _PROVIDERS,
    BraveProvider,
    ExaProvider,
    FirecrawlProvider,
    TavilyProvider,
)


def _post_stub(monkeypatch, status, payload, captured):
    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return httpx.Response(status, request=httpx.Request("POST", url), json=payload)

    monkeypatch.setattr(httpx, "post", fake_post)


def test_only_exa_firecrawl_and_tavily_fetch_pages():
    assert {n for n, c in _PROVIDERS.items() if c.supports_fetch} == {"exa", "firecrawl", "tavily"}
    with pytest.raises(NotImplementedError):
        BraveProvider("k").fetch_page("https://example.com")


def test_exa_fetch_page_request_and_response(monkeypatch):
    got = {}
    _post_stub(monkeypatch, 200, {
        "results": [{"title": "T", "url": "https://example.com", "text": "page text"}],
        "statuses": [{"id": "https://example.com", "status": "success", "source": "crawled"}],
    }, got)
    assert ExaProvider("exa-k").fetch_page("https://example.com") == "page text"
    assert got["url"] == "https://api.exa.ai/contents"
    assert got["headers"]["x-api-key"] == "exa-k"
    assert got["json"] == {"urls": ["https://example.com"], "text": True}

    _post_stub(monkeypatch, 200, {
        "results": [],
        "statuses": [{"id": "x", "status": "error", "error": {"tag": "CRAWL_NOT_FOUND", "httpStatusCode": 404}}],
    }, {})
    with pytest.raises(RuntimeError, match="CRAWL_NOT_FOUND \\(HTTP 404\\)"):
        ExaProvider("exa-k").fetch_page("https://example.com/missing")


def test_firecrawl_fetch_page_request_and_response(monkeypatch):
    got = {}
    _post_stub(monkeypatch, 200, {"success": True, "data": {"markdown": "# Hi", "metadata": {"title": "Hi"}}}, got)
    assert FirecrawlProvider("fc-k").fetch_page("https://example.com") == "# Hi"
    assert got["url"] == "https://api.firecrawl.dev/v2/scrape"
    assert got["headers"]["Authorization"] == "Bearer fc-k"
    assert got["json"] == {"url": "https://example.com", "formats": ["markdown"]}

    _post_stub(monkeypatch, 200, {"success": False, "error": "blocked by robots"}, {})
    with pytest.raises(RuntimeError, match="blocked by robots"):
        FirecrawlProvider("fc-k").fetch_page("https://example.com")


def test_tavily_fetch_page_request_and_response(monkeypatch):
    got = {}
    _post_stub(monkeypatch, 200, {
        "results": [{"url": "https://example.com", "raw_content": "body"}],
        "failed_results": [],
    }, got)
    assert TavilyProvider("tv-k").fetch_page("https://example.com") == "body"
    assert got["url"] == "https://api.tavily.com/extract"
    assert got["headers"]["Authorization"] == "Bearer tv-k"
    assert got["json"] == {"urls": ["https://example.com"]}

    _post_stub(monkeypatch, 200, {"results": [], "failed_results": [{"url": "x", "error": "timeout"}]}, {})
    with pytest.raises(RuntimeError, match="timeout"):
        TavilyProvider("tv-k").fetch_page("https://example.com")

    _post_stub(monkeypatch, 401, {}, {})
    with pytest.raises(httpx.HTTPStatusError):
        TavilyProvider("tv-k").fetch_page("https://example.com")


def _secrets(tmp_path, monkeypatch, **profile):
    for name in _PROVIDERS:
        monkeypatch.delenv(f"{name.upper()}_API_KEY", raising=False)
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("web_search:default", profile)
    return secrets


def test_fetch_provider_needs_the_switch_support_and_a_key(tmp_path, monkeypatch):
    # Off: local fetch, whatever the provider.
    s = _secrets(tmp_path, monkeypatch, provider="exa", keys={"exa": "k"})
    assert fetch_provider(s) is None and fetch_provider_name(s) == ""
    # On with a provider that offers it and has a key.
    s = _secrets(tmp_path, monkeypatch, provider="exa", keys={"exa": "k"}, fetch_via_provider=True)
    assert isinstance(fetch_provider(s), ExaProvider) and fetch_provider_name(s) == "exa"
    # On, but the active provider does not fetch pages.
    s = _secrets(tmp_path, monkeypatch, provider="brave", keys={"brave": "k", "exa": "k"}, fetch_via_provider=True)
    assert fetch_provider(s) is None
    s = _secrets(tmp_path, monkeypatch, provider="duckduckgo", fetch_via_provider=True)
    assert fetch_provider(s) is None
    # On, a fetching provider, but no key anywhere: a local fetch rather than an error.
    s = _secrets(tmp_path, monkeypatch, provider="tavily", fetch_via_provider=True)
    assert fetch_provider(s) is None
    monkeypatch.setenv("TAVILY_API_KEY", "env-k")
    assert fetch_provider(s).api_key == "env-k"


def test_web_fetch_goes_through_the_provider_after_the_address_check(tmp_path, monkeypatch):
    import coworker.web.fetch as fetch_mod

    s = _secrets(tmp_path, monkeypatch, provider="firecrawl", keys={"firecrawl": "k"}, fetch_via_provider=True)
    got = {}
    _post_stub(monkeypatch, 200, {"success": True, "data": {"markdown": "x" * 50}}, got)
    checked = []
    monkeypatch.setattr(fetch_mod, "check_url", lambda url: checked.append(url) or None)

    def no_local(*_a, **_k):
        raise AssertionError("the page must not be fetched locally")

    monkeypatch.setattr(fetch_mod, "get_checked", no_local)
    out = make_web_fetch_tool(s)("https://example.com/doc", max_chars=10)
    assert checked == ["https://example.com/doc"]
    assert got["json"]["url"] == "https://example.com/doc"
    assert out == {
        "url": "https://example.com/doc",
        "content_type": "text/markdown",
        "fetched_via": "firecrawl",
        "truncated": True,
        "text": "x" * 10,
    }


def test_web_fetch_refuses_blocked_addresses_before_asking_the_provider(tmp_path, monkeypatch):
    s = _secrets(tmp_path, monkeypatch, provider="exa", keys={"exa": "k"}, fetch_via_provider=True)

    def no_post(*_a, **_k):
        raise AssertionError("the provider must not see a blocked address")

    monkeypatch.setattr(httpx, "post", no_post)
    out = make_web_fetch_tool(s)("http://127.0.0.1:11434/api/tags")
    assert "loopback" in out["error"]
    out = make_web_fetch_tool(s)("http://169.254.169.254/latest/meta-data/")
    assert "link-local" in out["error"]


def test_web_fetch_falls_back_to_a_local_fetch_with_a_notice(tmp_path, monkeypatch):
    import coworker.web.fetch as fetch_mod

    s = _secrets(tmp_path, monkeypatch, provider="tavily", keys={"tavily": "k"}, fetch_via_provider=True)
    _post_stub(monkeypatch, 500, {}, {})
    monkeypatch.setattr(fetch_mod, "check_url", lambda url: None)

    def local(client, url):
        resp = httpx.Response(
            200, request=httpx.Request("GET", url), text="<html><body><p>local</p></body></html>",
            headers={"content-type": "text/html"},
        )
        resp.extensions["logical_url"] = url
        return resp

    monkeypatch.setattr(fetch_mod, "get_checked", local)
    out = make_web_fetch_tool(s)("https://example.com/")
    assert out["text"] == "local" and out["url"] == "https://example.com/"
    assert "fetched_via" not in out
    assert out["notice"].startswith("Tavily could not fetch the page (")
    assert out["notice"].endswith("); fetched from this machine instead.")
    assert "k" * 2 not in out["notice"]


def test_web_fetch_without_the_switch_is_unchanged(tmp_path, monkeypatch):
    import coworker.web.fetch as fetch_mod

    s = _secrets(tmp_path, monkeypatch, provider="exa", keys={"exa": "k"})
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no provider call")))

    def local(client, url):
        resp = httpx.Response(200, request=httpx.Request("GET", url), text="plain", headers={"content-type": "text/plain"})
        resp.extensions["logical_url"] = url
        return resp

    monkeypatch.setattr(fetch_mod, "get_checked", local)
    out = make_web_fetch_tool(s)("https://example.com/")
    assert out == {"url": "https://example.com/", "content_type": "text/plain", "truncated": False, "text": "plain"}


def test_web_fetch_approval_card_names_the_fetching_service(tmp_path, monkeypatch):
    from coworker.agent import build_engine
    from coworker.agents import chat_agent

    class _Stub:
        def complete(self, **_kw):
            from coworker.providers import AssistantTurn

            return AssistantTurn()

        def capabilities(self, _model):
            from coworker.providers.base import ModelCapabilities

            return ModelCapabilities()

        def stream(self, **_kw):
            from coworker.providers.base import StreamChunk

            yield StreamChunk(turn=self.complete())

    s = _secrets(tmp_path, monkeypatch, provider="exa", keys={"exa": "k"})
    eng = build_engine(agent=chat_agent(), provider=_Stub(), secrets=s)
    assert "web_fetch" in eng.registry.names()
    assert eng.approval_extras("web_fetch", {"url": "https://x"}) == {}
    assert eng.approval_extras("web_search", {}) == {"search_provider": "exa"}
    s.put("web_search:default", {"provider": "exa", "keys": {"exa": "k"}, "fetch_via_provider": True})
    # Resolved at card time, so the Settings change shows through without a new session.
    assert eng.approval_extras("web_fetch", {"url": "https://x"}) == {"fetch_provider": "exa"}
