"""Web search providers — a keyless default + pluggable third-party services.

`duckduckgo` works with no API key (our "starting version of our own"). `tavily`, `brave`,
and `fastcrw` give better results but need a key (configured via the SecretStore / env). All
providers return a uniform `list[SearchResult]`; the heavy client libs are lazy-imported.
"""

from __future__ import annotations

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


class FastCRWProvider(WebSearchProvider):
    """fastCRW web search (https://docs.fastcrw.com) via the hosted /v1 API."""

    name = "fastcrw"
    requires_key = True

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        resp = httpx.post(
            "https://api.fastcrw.com/v1/search",
            headers={"Authorization": f"Bearer {self.api_key}"},
            # No `scrapeOptions`: it would scrape every hit for page content that
            # SearchResult discards. `limit` maxes out at 20.
            json={"query": query, "limit": max_results},
            timeout=_TIMEOUT,
        )
        try:
            data = resp.json()
        except ValueError:  # a gateway's HTML error page, not a fastCRW envelope
            data = {}
        if not isinstance(data, dict) or data.get("success") is not True:
            # fastCRW reports a bad key, quota and disabled search as
            # {"success": false, "error": ...}, and can do so with a 200. Raise so the
            # tool surfaces the message rather than the empty list a .get() chain would
            # produce, which reads to the user as "no results for that query".
            err = data if isinstance(data, dict) else {}
            raise RuntimeError(err.get("error") or f"HTTP {resp.status_code}")
        rows = data.get("data")
        if not isinstance(rows, list):
            # `data` is a required array. Anything else is a response we don't understand,
            # and treating it as "no results" would be the same silent lie as above.
            raise RuntimeError(f"unexpected response shape (HTTP {resp.status_code})")
        results = [
            SearchResult(
                # `or ""` rather than a .get() default: JSON null is a present key.
                title=r.get("title") or "",
                url=r.get("url") or "",
                # `snippet` is fastCRW's alias of `description`; either may be sent.
                snippet=r.get("description") or r.get("snippet") or "",
            )
            for r in rows
            if isinstance(r, dict) and r.get("url")  # a hit with no URL is not a hit
        ]
        if rows and not results:
            raise RuntimeError(f"unexpected result shape (HTTP {resp.status_code})")
        return results


_PROVIDERS = {
    "duckduckgo": DuckDuckGoProvider,
    "tavily": TavilyProvider,
    "brave": BraveProvider,
    "fastcrw": FastCRWProvider,
}


def build_provider(name: str, api_key: Optional[str] = None) -> WebSearchProvider:
    cls = _PROVIDERS.get(name, DuckDuckGoProvider)
    if cls.requires_key:
        if not api_key:
            raise ValueError(f"web search provider '{name}' needs an API key")
        return cls(api_key)  # type: ignore[call-arg]
    return cls()  # type: ignore[call-arg]


def provider_names() -> list[str]:
    return list(_PROVIDERS)
