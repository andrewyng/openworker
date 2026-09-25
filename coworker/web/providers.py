"""Web search providers — a keyless default + pluggable third-party services.

`duckduckgo` works with no API key (our "starting version of our own"). `you` is also usable
keyless and takes an optional key to lift the free-tier limits. `tavily` and `brave` give
better results but need a key (configured via the SecretStore / env). All providers return a
uniform `list[SearchResult]`; the heavy client libs are lazy-imported.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

_TIMEOUT = 20.0


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


class WebSearchProvider(ABC):
    name: str = "base"
    requires_key: bool = False
    # Keyless provider that still uses a key when one is configured (better quota/features).
    # `build_provider` passes the key through instead of dropping it.
    accepts_key: bool = False

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]: ...


class DuckDuckGoProvider(WebSearchProvider):
    """Keyless default via the `ddgs` library."""

    name = "duckduckgo"
    requires_key = False

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        from ddgs import DDGS

        rows = DDGS().text(query, max_results=max_results) or []
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("href", "") or r.get("url", ""),
                snippet=r.get("body", "") or r.get("snippet", ""),
            )
            for r in rows
        ]


class YouProvider(WebSearchProvider):
    """You.com — keyless out of the box, better with a key.

    Without a key it hits the free agents endpoint (100 searches/day, rate-limited per IP),
    so it works in a fresh install like `duckduckgo` does. With a key it hits the Search API
    (`YOU_API_KEY`, or `YDC_API_KEY` — the name You.com's own docs and SDKs use), which lifts
    the quota. Both return the same `{"results": {"web": [...], "news": [...]}}` shape.
    """

    name = "you"
    requires_key = False
    accepts_key = True

    _KEYLESS_URL = "https://api.you.com/v1/agents/search"
    _KEYED_URL = "https://ydc-index.io/v1/search"
    # The free tier has no key, so the User-Agent is how You.com attributes this traffic.
    _USER_AGENT = "youdotcom-integration/andrewyng-openworker"

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or os.environ.get("YDC_API_KEY") or None

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        headers = {"Accept": "application/json", "User-Agent": self._USER_AGENT}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        resp = httpx.get(
            self._KEYED_URL if self.api_key else self._KEYLESS_URL,
            headers=headers,
            params={"query": query, "count": max_results},
            timeout=_TIMEOUT,
        )
        data = resp.json()
        results = data.get("results", {}) or {}
        # `count` applies per section; web first, news only fills any remainder.
        rows = (results.get("web") or []) + (results.get("news") or [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", "") or " ".join(r.get("snippets") or []),
            )
            for r in rows[:max_results]
        ]


class TavilyProvider(WebSearchProvider):
    name = "tavily"
    requires_key = True

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        resp = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": self.api_key, "query": query, "max_results": max_results},
            timeout=_TIMEOUT,
        )
        data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
            )
            for r in data.get("results", [])
        ]


class BraveProvider(WebSearchProvider):
    name = "brave"
    requires_key = True

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "X-Subscription-Token": self.api_key,
                "Accept": "application/json",
            },
            params={"q": query, "count": max_results},
            timeout=_TIMEOUT,
        )
        data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
            )
            for r in (data.get("web", {}) or {}).get("results", [])
        ]


_PROVIDERS = {
    "duckduckgo": DuckDuckGoProvider,
    "you": YouProvider,
    "tavily": TavilyProvider,
    "brave": BraveProvider,
}


def build_provider(name: str, api_key: Optional[str] = None) -> WebSearchProvider:
    cls = _PROVIDERS.get(name, DuckDuckGoProvider)
    if cls.requires_key:
        if not api_key:
            raise ValueError(f"web search provider '{name}' needs an API key")
        return cls(api_key)  # type: ignore[call-arg]
    if cls.accepts_key:
        return cls(api_key)  # type: ignore[call-arg]
    return cls()  # type: ignore[call-arg]


def provider_names() -> list[str]:
    return list(_PROVIDERS)
