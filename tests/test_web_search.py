"""Tests for web search — provider abstraction, the tool, and config resolution.

No network: a FakeProvider is injected; third-party key handling and the REST config path
are exercised without hitting DuckDuckGo/Tavily/Brave.
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
    TavilyProvider,
    WebSearchProvider,
    YouProvider,
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
    assert isinstance(build_provider("tavily", "tvly-x"), TavilyProvider)
    assert isinstance(build_provider("brave", "brv-x"), BraveProvider)


def test_build_provider_you_is_keyless_with_optional_key(monkeypatch):
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    assert "you" in provider_names()
    keyless = build_provider("you")  # no key needed
    assert isinstance(keyless, YouProvider) and keyless.api_key is None
    assert build_provider("you", "ydc-x").api_key == "ydc-x"  # key is passed through
    monkeypatch.setenv("YDC_API_KEY", "from-env")
    assert build_provider("you").api_key == "from-env"


def _fake_you_get(captured, payload):
    """Stand-in for httpx.get that records the request and replays a canned response."""

    class Resp:
        def json(self):
            return payload

    def get(url, **kw):
        captured.update(url=url, **kw)
        return Resp()

    return get


def test_you_provider_keyless_request_and_parsing(monkeypatch):
    import httpx

    monkeypatch.delenv("YDC_API_KEY", raising=False)
    got = {}
    monkeypatch.setattr(
        httpx,
        "get",
        _fake_you_get(
            got,
            {
                "results": {
                    "web": [
                        {"title": "w", "url": "https://w", "description": "wd"},
                        {"title": "s", "url": "https://s", "snippets": ["a", "b"]},
                    ],
                    "news": [{"title": "n", "url": "https://n", "description": "nd"}],
                }
            },
        ),
    )

    out = YouProvider().search("openworker", max_results=3)
    assert got["url"] == YouProvider._KEYLESS_URL  # free tier, no key
    assert "X-API-Key" not in got["headers"]
    assert got["headers"]["User-Agent"] == "youdotcom-integration/andrewyng-openworker"
    assert got["params"] == {"query": "openworker", "count": 3}
    assert [(r.title, r.url, r.snippet) for r in out] == [
        ("w", "https://w", "wd"),
        ("s", "https://s", "a b"),  # falls back to snippets when description is absent
        ("n", "https://n", "nd"),  # web first, news fills the remainder
    ]


def test_you_provider_with_key_uses_search_api(monkeypatch):
    import httpx

    got = {}
    monkeypatch.setattr(httpx, "get", _fake_you_get(got, {"results": {}}))

    assert YouProvider("ydc-123").search("q", max_results=2) == []
    assert got["url"] == YouProvider._KEYED_URL
    assert got["headers"]["X-API-Key"] == "ydc-123"


def test_you_provider_truncates_to_max_results(monkeypatch):
    import httpx

    rows = [{"title": f"r{i}", "url": f"https://x/{i}"} for i in range(10)]
    monkeypatch.setattr(
        httpx, "get", _fake_you_get({}, {"results": {"web": rows, "news": rows}})
    )
    assert len(YouProvider().search("q", max_results=4)) == 4


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
