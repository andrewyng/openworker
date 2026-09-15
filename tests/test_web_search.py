"""Tests for web search — provider abstraction, the tool, and config resolution.

No network: a FakeProvider is injected; third-party key handling and the REST config path
are exercised without hitting DuckDuckGo/Tavily/Brave/Linkup.
"""

from __future__ import annotations

import pytest

from coworker.secrets import SecretStore
from coworker.web import (
    SearchResult,
    build_provider,
    make_web_search_tool,
    provider_names,
)
from coworker.web.providers import (
    BraveProvider,
    DuckDuckGoProvider,
    LinkupProvider,
    TavilyProvider,
    WebSearchProvider,
)


class FakeProvider(WebSearchProvider):
    name = "fake"

    def __init__(self):
        self.calls = []

    def search(self, query, max_results=5):
        self.calls.append((query, max_results))
        return [
            SearchResult(title=f"r{i}", url=f"https://x/{i}", snippet="s")
            for i in range(max_results)
        ]


def test_tool_returns_results():
    fake = FakeProvider()
    tool = make_web_search_tool(provider=fake)
    out = tool(query="anthropic", max_results=3)
    assert out["provider"] == "fake"
    assert [r["title"] for r in out["results"]] == ["r0", "r1", "r2"]
    assert fake.calls == [("anthropic", 3)]
    # metadata + schema for the registry
    assert tool.__aisuite_tool_metadata__.category == "web"
    assert tool.__coworker_schema__["function"]["name"] == "web_search"


def test_tool_clamps_max_results():
    fake = FakeProvider()
    make_web_search_tool(provider=fake)(query="q", max_results=99)
    assert fake.calls[0][1] == 10  # clamped to 10
    make_web_search_tool(provider=fake)(query="q", max_results=0)
    assert fake.calls[1][1] == 1  # clamped to >=1


def test_tool_reports_search_errors():
    class Boom(WebSearchProvider):
        name = "boom"

        def search(self, query, max_results=5):
            raise RuntimeError("network down")

    out = make_web_search_tool(provider=Boom())(query="q")
    assert "web search failed" in out["error"] and out["provider"] == "boom"


def test_build_provider_default_is_keyless_duckduckgo():
    assert isinstance(build_provider("duckduckgo"), DuckDuckGoProvider)
    assert isinstance(build_provider("unknown-thing"), DuckDuckGoProvider)  # falls back
    assert "duckduckgo" in provider_names()


def test_build_provider_third_party_requires_key():
    with pytest.raises(ValueError):
        build_provider("tavily")  # no key
    with pytest.raises(ValueError):
        build_provider("linkup")  # no key
    assert isinstance(build_provider("tavily", "tvly-x"), TavilyProvider)
    assert isinstance(build_provider("brave", "brv-x"), BraveProvider)
    assert isinstance(build_provider("linkup", "lp-x"), LinkupProvider)
    assert "linkup" in provider_names()


def test_linkup_maps_response_to_search_results(monkeypatch):
    """Linkup names its fields `name`/`content`; check the mapping and the request shape."""
    import httpx

    sent: dict = {}

    def fake_post(url, **kwargs):
        sent["url"] = url
        sent.update(kwargs)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "type": "text",
                        "name": "Result one",
                        "url": "https://example.com/1",
                        "content": "snippet one",
                        "favicon": "https://example.com/favicon.ico",
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    results = LinkupProvider("lp-x").search("anthropic", max_results=3)

    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("Result one", "https://example.com/1", "snippet one")
    ]
    assert sent["url"] == "https://api.linkup.so/v1/search"
    assert sent["headers"]["Authorization"] == "Bearer lp-x"
    assert sent["json"] == {
        "q": "anthropic",
        "depth": "fast",
        "outputType": "searchResults",
        "maxResults": 3,
    }


def test_tool_surfaces_missing_key_error(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("web_search:default", {"provider": "tavily"})  # no api_key
    out = make_web_search_tool(secrets)(
        query="q"
    )  # resolve_provider raises ValueError → error dict
    assert "needs an API key" in out["error"]


def test_resolve_provider_from_secretstore(tmp_path):
    from coworker.web import resolve_provider

    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("web_search:default", {"provider": "tavily", "api_key": "tvly-123"})
    p = resolve_provider(secrets)
    assert p.name == "tavily" and p.api_key == "tvly-123"


def test_web_search_rest(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    client = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))

    assert client.get("/v1/web-search").json()["provider"] == "duckduckgo"
    assert (
        client.post(
            "/v1/web-search", json={"provider": "tavily", "api_key": "sk-secret-xyz"}
        ).json()["ok"]
        is True
    )
    got = client.get("/v1/web-search").json()
    assert got["provider"] == "tavily" and got["has_key"] is True
    assert (
        "sk-secret-xyz" not in client.get("/v1/web-search").text
    )  # key never returned
    assert (
        client.post("/v1/web-search", json={"provider": "nope"}).json()["ok"] is False
    )


def test_engine_registers_web_search(tmp_path):
    from coworker.agent import build_engine
    from coworker.agents import chat_agent

    eng = build_engine(
        agent=chat_agent(),
        provider=_StubProvider(),
        secrets=SecretStore(tmp_path / "s.json"),
    )
    assert "web_search" in eng.registry.names()


class _StubProvider:
    def complete(self, **_kw):
        from coworker.providers import AssistantTurn

        return AssistantTurn()

    def capabilities(self, _model):
        from coworker.providers.base import ModelCapabilities

        return ModelCapabilities()

    def stream(self, **_kw):
        from coworker.providers.base import StreamChunk

        yield StreamChunk(turn=self.complete())
