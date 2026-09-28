"""Tests for web search — provider abstraction, the tool, and config resolution.

No network: a FakeProvider is injected; third-party key handling and the REST config path
are exercised without hitting DuckDuckGo/Tavily/Brave/fastCRW.
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
    FastCRWProvider,
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
    assert isinstance(build_provider("tavily", "tvly-x"), TavilyProvider)
    assert isinstance(build_provider("brave", "brv-x"), BraveProvider)


def test_build_provider_fastcrw_requires_key():
    with pytest.raises(ValueError):
        build_provider("fastcrw")  # no key
    assert isinstance(build_provider("fastcrw", "crw_live_x"), FastCRWProvider)
    # Membership, not equality: provider_names() is list(_PROVIDERS), so asserting the
    # whole list would break the moment anyone adds or reorders a provider.
    assert "fastcrw" in provider_names()


def test_provider_names_match_their_class_names():
    from coworker.web.providers import _PROVIDERS

    # resolve_provider derives the env var from the configured name while the tool
    # reports cls.name; if the two ever diverge the key resolves but the report lies.
    assert all(key == cls.name for key, cls in _PROVIDERS.items())


class _Resp:
    """Minimal httpx.Response stand-in: `json()` plus the `status_code` we fall back on."""

    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value")  # non-JSON body
        return self._payload


def _patch_post(monkeypatch, resp, captured=None):
    import httpx

    def fake_post(url, headers=None, json=None, timeout=None):
        if captured is not None:
            captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return resp

    monkeypatch.setattr(httpx, "post", fake_post)


def test_fastcrw_maps_flat_data_array(monkeypatch):
    captured = {}
    _patch_post(
        monkeypatch,
        _Resp(
            {
                "success": True,
                # /v1/search returns `data` as a flat array of results.
                "data": [
                    {"title": "t0", "url": "https://x/0", "snippet": "s0"},
                    {"title": "t1", "url": "https://x/1", "description": "d1"},
                    {"title": "t2", "url": "https://x/2"},  # no snippet at all
                ],
            }
        ),
        captured,
    )
    results = FastCRWProvider("crw_live_x").search("openworker docs", max_results=3)

    assert captured["url"] == "https://api.fastcrw.com/v1/search"
    assert captured["headers"]["Authorization"] == "Bearer crw_live_x"
    # Exact equality: an accidental `scrapeOptions` would scrape every hit.
    assert captured["json"] == {"query": "openworker docs", "limit": 3}
    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("t0", "https://x/0", "s0"),
        ("t1", "https://x/1", "d1"),
        ("t2", "https://x/2", ""),
    ]


def test_fastcrw_limit_stays_within_the_api_maximum(monkeypatch):
    captured = {}
    _patch_post(monkeypatch, _Resp({"success": True, "data": []}), captured)
    make_web_search_tool(provider=FastCRWProvider("crw_live_x"))(
        query="q", max_results=99
    )
    assert captured["json"]["limit"] == 10  # tool clamp, well under fastCRW's max of 20


def test_fastcrw_bad_key_surfaces_through_the_tool(monkeypatch):
    _patch_post(
        monkeypatch,
        _Resp({"success": False, "error": "Invalid or missing API key"}, status=401),
    )
    out = make_web_search_tool(provider=FastCRWProvider("bad"))(query="q")
    assert out["provider"] == "fastcrw"
    assert "Invalid or missing API key" in out["error"]


def test_fastcrw_reports_failure_sent_with_a_200(monkeypatch):
    # fastCRW can answer 200 with success:false, so status alone is not the signal.
    _patch_post(monkeypatch, _Resp({"success": False, "error": "search_disabled"}))
    out = make_web_search_tool(provider=FastCRWProvider("k"))(query="q")
    assert "search_disabled" in out["error"]


def test_fastcrw_non_json_body_reports_the_status(monkeypatch):
    _patch_post(monkeypatch, _Resp(None, status=502))  # HTML from a gateway
    out = make_web_search_tool(provider=FastCRWProvider("k"))(query="q")
    assert "502" in out["error"]


def test_fastcrw_rejects_a_response_shape_it_does_not_understand(monkeypatch):
    # `data` is a required array. Returning [] for these would be the same silent
    # "no results for that query" lie that checking `success` exists to prevent.
    for body in ({"success": True}, {"success": True, "data": {}}, {"success": "yes"}):
        _patch_post(monkeypatch, _Resp(body))
        out = make_web_search_tool(provider=FastCRWProvider("k"))(query="q")
        assert "error" in out and "results" not in out, out


def test_fastcrw_empty_result_set_is_not_an_error(monkeypatch):
    _patch_post(monkeypatch, _Resp({"success": True, "data": []}))
    out = make_web_search_tool(provider=FastCRWProvider("k"))(query="q")
    assert out == {"provider": "fastcrw", "results": []}


def test_fastcrw_skips_unusable_rows_but_keeps_the_good_ones(monkeypatch):
    # JSON null is a present key, so a .get() default would let None through.
    _patch_post(
        monkeypatch,
        _Resp(
            {
                "success": True,
                "data": [
                    None,
                    "junk",
                    {},  # no url
                    {"title": None, "url": "https://x/1", "description": None},
                ],
            }
        ),
    )
    assert [(r.title, r.url, r.snippet) for r in FastCRWProvider("k").search("q")] == [
        ("", "https://x/1", "")
    ]


def test_fastcrw_rejects_rows_it_cannot_use_at_all(monkeypatch):
    # A non-empty array that yields nothing usable is a shape change, not "no hits" —
    # reporting it as zero results would be the same silent lie as a swallowed 401.
    _patch_post(monkeypatch, _Resp({"success": True, "data": [None, "junk", {}]}))
    out = make_web_search_tool(provider=FastCRWProvider("k"))(query="q")
    assert "error" in out and "results" not in out, out


def test_fastcrw_non_dict_error_body_still_reports_the_status(monkeypatch):
    # A CDN in front of the API can answer with a bare JSON list or string.
    for body in (["Service Unavailable"], "maintenance", 0):
        _patch_post(monkeypatch, _Resp(body, status=503))
        out = make_web_search_tool(provider=FastCRWProvider("k"))(query="q")
        assert "503" in out["error"], out
        assert "has no attribute" not in out["error"], out


def test_fastcrw_key_from_env(tmp_path, monkeypatch):
    from coworker.web import resolve_provider

    # The env var name is derived from the provider name, so this is what pins
    # `fastcrw` (lowercase, no dash) as the only spelling that works.
    monkeypatch.setenv("FASTCRW_API_KEY", "crw_live_env")
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("web_search:default", {"provider": "fastcrw"})
    p = resolve_provider(secrets)
    assert p.name == "fastcrw" and p.api_key == "crw_live_env"


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
    # Registering the provider is all it takes for the REST surface to accept it.
    assert "fastcrw" in client.get("/v1/web-search").json()["providers"]
    assert (
        client.post(
            "/v1/web-search", json={"provider": "fastcrw", "api_key": "crw_live_xyz"}
        ).json()["ok"]
        is True
    )
    assert "crw_live_xyz" not in client.get("/v1/web-search").text


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
