"""Tests for web search — provider abstraction, the tool, and config resolution.

No network: a FakeProvider is injected; third-party key handling and the REST config path
are exercised without hitting DuckDuckGo/Tavily/Brave/Serper/Exa/Firecrawl.
"""

from __future__ import annotations

import pytest

from coworker.sandbox.network_profiles import SEARCH_APIS
from coworker.secrets import SecretStore
from coworker.web import (
    SearchResult,
    build_provider,
    make_web_search_tool,
    provider_names,
)
from coworker.web.providers import (
    _PROVIDERS,
    BraveProvider,
    DuckDuckGoProvider,
    ExaProvider,
    FirecrawlProvider,
    SerperProvider,
    TavilyProvider,
    WebSearchProvider,
    YouProvider,
)
from coworker.web.tool import PROVIDER_HOSTS


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
        build_provider("serper")  # no key
    assert isinstance(build_provider("tavily", "tvly-x"), TavilyProvider)
    assert isinstance(build_provider("brave", "brv-x"), BraveProvider)
    assert isinstance(build_provider("serper", "srp-x"), SerperProvider)
    assert "serper" in provider_names()
    with pytest.raises(ValueError):
        build_provider("exa")  # no key
    assert isinstance(build_provider("exa", "exa-x"), ExaProvider)
    with pytest.raises(ValueError):
        build_provider("firecrawl")  # no key
    assert isinstance(build_provider("firecrawl", "fc-x"), FirecrawlProvider)


def test_serper_sends_correct_request_and_maps_organic_results(monkeypatch):
    """Verify the Serper API request shape and response mapping without a network call."""
    import httpx

    sent = {}

    class _Resp:
        @staticmethod
        def raise_for_status():
            pass

        @staticmethod
        def json():
            return {
                "organic": [
                    {
                        "title": "Example Title",
                        "link": "https://example.com",
                        "snippet": "An example snippet.",
                    }
                ]
            }

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)

    results = build_provider("serper", "srp-test-key").search("anthropic", max_results=3)

    assert sent["url"] == "https://google.serper.dev/search"
    assert sent["headers"]["X-API-KEY"] == "srp-test-key"
    assert sent["json"]["q"] == "anthropic"
    assert sent["json"]["num"] == 3
    assert len(results) == 1
    assert results[0].title == "Example Title"
    assert results[0].url == "https://example.com"
    assert results[0].snippet == "An example snippet."


def test_exa_provider_parses_highlights(monkeypatch):
    import httpx

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "results": [
                    {
                        "title": "Exa",
                        "url": "https://exa.ai",
                        "highlights": ["neural search", "for AI"],
                    },
                    {"title": None, "url": "https://exa.ai/docs"},
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    results = ExaProvider("exa-x").search("what is exa", max_results=2)

    assert captured["url"] == "https://api.exa.ai/search"
    assert captured["headers"]["x-api-key"] == "exa-x"
    assert captured["headers"]["x-exa-integration"] == "andrewyng/openworker-integration"
    assert captured["json"]["numResults"] == 2
    assert captured["json"]["contents"] == {"highlights": True}
    assert [r.to_dict() for r in results] == [
        {
            "title": "Exa",
            "url": "https://exa.ai",
            "snippet": "neural search for AI",
        },
        {"title": "", "url": "https://exa.ai/docs", "snippet": ""},
    ]


def test_firecrawl_maps_v2_search_response(monkeypatch):
    import httpx

    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "success": True,
                "data": {
                    "web": [
                        {"title": "t0", "url": "https://x/0", "description": "d0"},
                        {"title": "t1", "url": "https://x/1", "description": "d1"},
                    ]
                },
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    results = FirecrawlProvider("fc-key").search("claude api", max_results=2)

    assert captured["url"] == "https://api.firecrawl.dev/v2/search"
    assert captured["headers"]["Authorization"] == "Bearer fc-key"
    assert captured["json"] == {"query": "claude api", "limit": 2}
    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("t0", "https://x/0", "d0"),
        ("t1", "https://x/1", "d1"),
    ]


def test_keyed_providers_raise_on_http_errors(monkeypatch):
    """A 401/429/5xx surfaces as an error instead of an empty result list."""
    import httpx

    def denied(url, **kwargs):
        return httpx.Response(401, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", denied)
    monkeypatch.setattr(httpx, "get", denied)
    for provider in (
        TavilyProvider("k"),
        BraveProvider("k"),
        SerperProvider("k"),
        ExaProvider("k"),
        FirecrawlProvider("k"),
        YouProvider(),
        YouProvider("k"),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            provider.search("q")


def test_every_provider_has_a_name_and_a_key_page():
    for name, cls in _PROVIDERS.items():
        assert cls.name == name and cls.display_name, name
        assert cls.key_mode in ("none", "optional", "required"), name
        assert bool(cls.key_url) == (cls.key_mode != "none"), name  # keyless: no key page
        # The one-flag views agree with the tri-state.
        assert cls.requires_key == (cls.key_mode == "required"), name
        assert cls.accepts_key == (cls.key_mode == "optional"), name
    assert DuckDuckGoProvider.key_mode == "none"
    assert YouProvider.key_mode == "optional"
    assert TavilyProvider.key_mode == "required"


def test_every_provider_is_known_to_the_site_wall():
    """`web_search` has no url argument: the sandbox's allowed-sites wall checks the
    provider's host (PROVIDER_HOSTS), and the Access page offers it under Search."""
    for name in provider_names():
        hosts = PROVIDER_HOSTS[name]
        for host in [hosts] if isinstance(hosts, str) else hosts:
            assert host in SEARCH_APIS, (name, host)


def test_build_provider_you_is_keyless_with_optional_key(monkeypatch):
    monkeypatch.setenv("YDC_API_KEY", "ignored")  # providers never read the environment
    assert "you" in provider_names()
    keyless = build_provider("you")  # no key needed
    assert isinstance(keyless, YouProvider) and keyless.api_key is None
    assert build_provider("you", "ydc-x").api_key == "ydc-x"  # key is passed through


def _you_env(monkeypatch, **values):
    for var in ("YOU_API_KEY", "YDC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    for var, value in values.items():
        monkeypatch.setenv(var, value)


def test_resolve_provider_reads_the_you_key_from_either_env_name(tmp_path, monkeypatch):
    from coworker.web import resolve_provider

    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("web_search:default", {"provider": "you"})
    _you_env(monkeypatch)
    assert resolve_provider(secrets).api_key is None
    _you_env(monkeypatch, YDC_API_KEY="from-ydc")  # the name You.com's own docs use
    assert resolve_provider(secrets).api_key == "from-ydc"
    _you_env(monkeypatch, YOU_API_KEY="from-you", YDC_API_KEY="from-ydc")
    assert resolve_provider(secrets).api_key == "from-you"  # the uniform name wins
    secrets.put("web_search:default", {"provider": "you", "api_key": "from-store"})
    assert resolve_provider(secrets).api_key == "from-store"  # the store wins over both


def test_provider_host_follows_the_you_key(tmp_path, monkeypatch):
    """You.com talks to one host keyless and another with a key; the site wall checks the
    one the next search will use."""
    from coworker.web import provider_host

    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("web_search:default", {"provider": "you"})
    _you_env(monkeypatch)
    assert provider_host(secrets) == "api.you.com"
    _you_env(monkeypatch, YDC_API_KEY="k")
    assert provider_host(secrets) == "ydc-index.io"
    _you_env(monkeypatch)
    secrets.put("web_search:default", {"provider": "you", "api_key": "k"})
    assert provider_host(secrets) == "ydc-index.io"
    secrets.put("web_search:default", {"provider": "brave", "api_key": "k"})
    assert provider_host(secrets) == "api.search.brave.com"  # single-host providers as before


def _fake_you_get(captured, payload):
    """Stand-in for httpx.get that records the request and replays a canned response."""

    class Resp:
        def raise_for_status(self):
            pass

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


def _rest_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    for name in provider_names():
        monkeypatch.delenv(f"{name.upper()}_API_KEY", raising=False)
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    manager = SessionManager(data_dir=tmp_path / "data")
    return TestClient(create_app(manager)), manager


def test_web_search_rest_lists_providers_in_registry_order(tmp_path, monkeypatch):
    client, _ = _rest_client(tmp_path, monkeypatch)
    got = client.get("/v1/web-search").json()
    assert got["provider"] == "duckduckgo" and got["fetch_via_provider"] is False
    names = [p["name"] for p in got["providers"]]
    assert names == provider_names() and names[0] == "duckduckgo"
    by = {p["name"]: p for p in got["providers"]}
    assert by["duckduckgo"] == {
        "name": "duckduckgo", "display_name": "DuckDuckGo", "key_mode": "none", "key_url": "",
        "has_key": False, "key_source": None, "key_env": "", "supports_fetch": False,
    }
    assert by["tavily"]["key_mode"] == "required" and by["tavily"]["supports_fetch"] is True
    assert {n for n, p in by.items() if p["supports_fetch"]} == {"exa", "firecrawl", "tavily"}
    assert by["you"]["key_mode"] == "optional" and by["you"]["key_url"]


def test_web_search_rest_key_from_environment(tmp_path, monkeypatch):
    client, _ = _rest_client(tmp_path, monkeypatch)
    monkeypatch.setenv("BRAVE_API_KEY", "brv-env")
    row = next(p for p in client.get("/v1/web-search").json()["providers"] if p["name"] == "brave")
    assert row["has_key"] and row["key_source"] == "env" and row["key_env"] == "BRAVE_API_KEY"
    assert "brv-env" not in client.get("/v1/web-search").text
    # With a key in the environment the provider can be chosen.
    assert client.post("/v1/web-search", json={"provider": "brave"}).json()["provider"] == "brave"


def test_web_search_rest_refuses_a_keyless_required_provider(tmp_path, monkeypatch):
    client, _ = _rest_client(tmp_path, monkeypatch)
    resp = client.post("/v1/web-search", json={"provider": "tavily"})
    assert resp.status_code == 400
    assert resp.json()["ok"] is False and "Tavily needs an API key" in resp.json()["error"]
    assert client.get("/v1/web-search").json()["provider"] == "duckduckgo"
    assert client.post("/v1/web-search", json={"provider": "nope"}).status_code == 400
    assert client.post("/v1/web-search", json={}).status_code == 400
    # You.com works keyless, so it can be chosen without a key.
    assert client.post("/v1/web-search", json={"provider": "you"}).json()["provider"] == "you"


def test_web_search_rest_test_and_save_key(tmp_path, monkeypatch):
    """PUT runs one search with the key before storing it; a failure returns the provider's
    error and stores nothing. Each provider keeps its own key."""
    from coworker.web import providers as providers_mod

    searched = []

    class Verify(TavilyProvider):
        def search(self, query, max_results=5):
            searched.append((self.name, self.api_key, query, max_results, self.timeout))
            if self.api_key == "bad":
                raise RuntimeError("401 Unauthorized for key bad")
            return []

    monkeypatch.setitem(providers_mod._PROVIDERS, "tavily", Verify)
    client, manager = _rest_client(tmp_path, monkeypatch)

    resp = client.put("/v1/web-search/keys/tavily", json={"api_key": "bad"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "401 Unauthorized for key •••"  # never the key
    assert client.get("/v1/web-search").json()["providers"][2]["has_key"] is False

    resp = client.put("/v1/web-search/keys/tavily", json={"api_key": "tvly-good"})
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert searched[-1] == ("tavily", "tvly-good", "openworker", 1, providers_mod._VERIFY_TIMEOUT)
    row = next(p for p in resp.json()["providers"] if p["name"] == "tavily")
    assert row["has_key"] and row["key_source"] == "store"
    assert "tvly-good" not in resp.text
    # The key is stored per provider; the active provider is unchanged until chosen.
    assert manager.secrets.get("web_search:default")["keys"] == {"tavily": "tvly-good"}
    assert resp.json()["provider"] == "duckduckgo"
    assert client.post("/v1/web-search", json={"provider": "tavily"}).json()["provider"] == "tavily"

    # Empty, keyless and unknown providers are refused.
    assert client.put("/v1/web-search/keys/tavily", json={"api_key": " "}).status_code == 400
    assert client.put("/v1/web-search/keys/duckduckgo", json={"api_key": "x"}).status_code == 400
    assert client.put("/v1/web-search/keys/nope", json={"api_key": "x"}).status_code == 400


def test_web_search_rest_switching_keeps_other_keys(tmp_path, monkeypatch):
    from coworker.web import providers as providers_mod

    class Ok(WebSearchProvider):
        name = "stub"
        display_name = "Stub"
        key_mode = "required"
        requires_key = True

        def __init__(self, api_key):
            self.api_key = api_key

        def search(self, query, max_results=5):
            return []

    monkeypatch.setitem(providers_mod._PROVIDERS, "tavily", type("T", (Ok,), {"name": "tavily"}))
    monkeypatch.setitem(providers_mod._PROVIDERS, "exa", type("E", (Ok,), {"name": "exa"}))
    client, manager = _rest_client(tmp_path, monkeypatch)
    assert client.put("/v1/web-search/keys/tavily", json={"api_key": "t-1"}).json()["ok"]
    assert client.put("/v1/web-search/keys/exa", json={"api_key": "e-1"}).json()["ok"]
    assert client.post("/v1/web-search", json={"provider": "tavily"}).json()["ok"]
    assert client.post("/v1/web-search", json={"provider": "exa"}).json()["ok"]
    assert client.post("/v1/web-search", json={"provider": "tavily"}).json()["ok"]
    assert manager.secrets.get("web_search:default")["keys"] == {"tavily": "t-1", "exa": "e-1"}

    # DELETE forgets one key; the other stays. Removing the active provider's only key
    # falls back to the keyless default instead of leaving every search failing.
    got = client.delete("/v1/web-search/keys/exa").json()
    assert got["ok"] and got["provider"] == "tavily"
    assert manager.secrets.get("web_search:default")["keys"] == {"tavily": "t-1"}
    got = client.delete("/v1/web-search/keys/tavily").json()
    assert got["provider"] == "duckduckgo"
    assert manager.secrets.get("web_search:default")["keys"] == {}
    assert client.delete("/v1/web-search/keys/nope").status_code == 400


def test_web_search_rest_fetch_via_provider(tmp_path, monkeypatch):
    client, manager = _rest_client(tmp_path, monkeypatch)
    got = client.post("/v1/web-search/fetch-via-provider", json={"enabled": True}).json()
    assert got["ok"] and got["fetch_via_provider"] is True
    assert manager.secrets.get("web_search:default")["fetch_via_provider"] is True
    assert client.get("/v1/web-search").json()["fetch_via_provider"] is True
    got = client.post("/v1/web-search/fetch-via-provider", json={"enabled": False}).json()
    assert got["fetch_via_provider"] is False


def test_legacy_profile_is_read_as_a_per_provider_key(tmp_path, monkeypatch):
    """The pre-Customization `{provider, api_key}` profile: resolution still finds the key,
    and the first write through the manager lands the new shape."""
    from coworker.web import provider_host, resolve_provider
    from coworker.web.tool import load_profile

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("web_search:default", {"provider": "tavily", "api_key": "tvly-old"})
    assert load_profile(secrets) == {
        "provider": "tavily", "keys": {"tavily": "tvly-old"}, "fetch_via_provider": False,
    }
    assert resolve_provider(secrets).api_key == "tvly-old"
    assert provider_host(secrets) == "api.tavily.com"

    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.secrets = secrets
    got = manager.get_web_search()
    assert got["provider"] == "tavily"
    assert next(p for p in got["providers"] if p["name"] == "tavily")["key_source"] == "store"
    assert secrets.get("web_search:default") == {
        "provider": "tavily", "keys": {"tavily": "tvly-old"}, "fetch_via_provider": False,
    }


def test_resolve_provider_uses_the_active_providers_own_key(tmp_path, monkeypatch):
    """Keys are per provider: the active one's stored key, then its environment variable,
    never another provider's key."""
    from coworker.web import resolve_provider

    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("web_search:default", {"provider": "exa", "keys": {"tavily": "t-1", "exa": "e-1"}})
    assert resolve_provider(secrets).api_key == "e-1"
    secrets.put("web_search:default", {"provider": "tavily", "keys": {"tavily": "t-1", "exa": "e-1"}})
    assert resolve_provider(secrets).api_key == "t-1"
    secrets.put("web_search:default", {"provider": "exa", "keys": {"tavily": "t-1"}})
    with pytest.raises(ValueError):
        resolve_provider(secrets)  # tavily's key is not exa's
    monkeypatch.setenv("EXA_API_KEY", "e-env")
    assert resolve_provider(secrets).api_key == "e-env"


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
