"""Web search providers — a keyless default + pluggable third-party services.

`duckduckgo` works with no API key (our "starting version of our own"). `tavily`, `brave` and
`serpdive` give better results but need a key (configured via the SecretStore / env). All
providers return a uniform `list[SearchResult]`; the heavy client libs are lazy-imported.

`serpdive` fills each result's snippet with the extracted text of the page rather than a SERP
description, and its default `krill` model is free and unlimited (fair use) — no credit counter,
nothing that runs out at the end of the month.
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


class SerpdiveProvider(WebSearchProvider):
    """One call returns the ranked results AND the extracted text of each page.

    `snippet` therefore carries page content, not a SERP description — the same mapping
    `TavilyProvider` already makes. The default model is `krill`, which is free and unlimited
    (fair use): configuring this provider costs nothing and keeps working, with no monthly
    allowance to run out. `SERPDIVE_MODEL` selects the paid `mako` or `moby` for deeper
    retrieval — opt-in, never automatic.
    """

    name = "serpdive"
    requires_key = True

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        resp = httpx.post(
            "https://api.serpdive.com/v1/search",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "query": query,
                # krill: free and unlimited. Anything else is billed, so it is opt-in only.
                "model": os.environ.get("SERPDIVE_MODEL", "krill"),
                # A cap, never a minimum: the engine returns what it judges relevant, up to
                # this many. The API accepts 1-10 and clamps anything outside that range.
                "max_results": max_results,
            },
            timeout=_TIMEOUT,
        )
        data = resp.json()
        return [
            SearchResult(
                title=r.get("title") or "",  # the API sends null when a page has no title
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
    "tavily": TavilyProvider,
    "brave": BraveProvider,
    "serpdive": SerpdiveProvider,
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
