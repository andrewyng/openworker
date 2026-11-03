"""Web search providers — a keyless default + pluggable third-party services.

`duckduckgo` works with no API key (our "starting version of our own"). `you` is also usable
keyless and takes an optional key to lift the free-tier limits. `tavily`, `brave`, `serper`,
`exa` and `firecrawl` give better results but need a key (configured via the SecretStore /
env). All providers return a uniform `list[SearchResult]`; the heavy client libs are
lazy-imported.
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
    # What Settings shows for this provider, and where a person gets a key (empty when the
    # provider is keyless).
    display_name: str = ""
    key_url: str = ""
    # Whether the provider takes a key: "none" (keyless), "optional" (works keyless, uses a
    # key when one is configured) or "required". `requires_key` / `accepts_key` say the same
    # thing for the callers that check one flag.
    key_mode: str = "none"
    requires_key: bool = False
    # Keyless provider that still uses a key when one is configured (better quota/features).
    # `build_provider` passes the key through instead of dropping it.
    accepts_key: bool = False
    # Whether the provider can also return a page's text (`fetch_page`), so `web_fetch` can
    # go through it instead of fetching from this machine (Settings ▸ Customization).
    supports_fetch: bool = False
    # Per-call HTTP timeout. Settings' "Test and save" shortens it for its one check.
    timeout: float = _TIMEOUT

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]: ...

    def fetch_page(self, url: str) -> str:
        """The readable text of `url`, fetched by the provider. Only for providers with
        `supports_fetch`; the rest raise."""
        raise NotImplementedError(f"{self.name} does not fetch pages")


class DuckDuckGoProvider(WebSearchProvider):
    """Keyless default via the `ddgs` library."""

    name = "duckduckgo"
    display_name = "DuckDuckGo"
    key_mode = "none"
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

    Without a key it hits the agents endpoint, so it works in a fresh install like
    `duckduckgo` does. You.com does not document that endpoint and throttles it per IP
    (about 100 queries a day, per third-party docs), so results may stop without notice.
    With a key it hits the Search API, which lifts the quota. The key comes from the
    SecretStore or the environment (`YOU_API_KEY`, or `YDC_API_KEY` — the name You.com's
    own docs and SDKs use), resolved by `coworker.web.tool`, not here. Both endpoints
    return the same `{"results": {"web": [...], "news": [...]}}` shape.
    """

    name = "you"
    display_name = "You.com"
    key_url = "https://you.com/platform"
    key_mode = "optional"
    requires_key = False
    accepts_key = True

    _KEYLESS_URL = "https://api.you.com/v1/agents/search"
    _KEYED_URL = "https://ydc-index.io/v1/search"
    # The free tier has no key, so the User-Agent is how You.com attributes this traffic.
    _USER_AGENT = "youdotcom-integration/andrewyng-openworker"

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or None

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        headers = {"Accept": "application/json", "User-Agent": self._USER_AGENT}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        resp = httpx.get(
            self._KEYED_URL if self.api_key else self._KEYLESS_URL,
            headers=headers,
            params={"query": query, "count": max_results},
            timeout=self.timeout,
        )
        resp.raise_for_status()
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
    display_name = "Tavily"
    key_url = "https://tavily.com"
    key_mode = "required"
    requires_key = True
    supports_fetch = True

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        resp = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": self.api_key, "query": query, "max_results": max_results},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
            )
            for r in data.get("results", [])
        ]

    def fetch_page(self, url: str) -> str:
        # https://docs.tavily.com/documentation/api-reference/endpoint/extract — POST
        # {urls}, bearer auth; the text is results[].raw_content, failures land in
        # failed_results[] even on a 200.
        import httpx

        resp = httpx.post(
            "https://api.tavily.com/extract",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"urls": [url]},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        for r in data.get("results") or []:
            if r.get("raw_content"):
                return str(r["raw_content"])
        failed = data.get("failed_results") or []
        detail = failed[0].get("error", "") if failed else "no content returned"
        raise RuntimeError(f"Tavily could not extract the page: {detail}")


class BraveProvider(WebSearchProvider):
    name = "brave"
    display_name = "Brave Search"
    key_url = "https://brave.com/search/api"
    key_mode = "required"
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
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
            )
            for r in (data.get("web", {}) or {}).get("results", [])
        ]


class SerperProvider(WebSearchProvider):
    """Google search results via serper.dev — $0.001/search, 2500 free searches.

    POSTs to the Serper API and maps organic results into uniform SearchResult
    items. Requires a key from https://serper.dev (free tier includes 2500 searches
    with no time limit).
    """

    name = "serper"
    display_name = "Serper"
    key_url = "https://serper.dev"
    key_mode = "required"
    requires_key = True

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        resp = httpx.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": self.api_key},
            json={"q": query, "num": max_results},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("link", ""),
                snippet=r.get("snippet", ""),
            )
            for r in data.get("organic", [])
        ]


class ExaProvider(WebSearchProvider):
    name = "exa"
    display_name = "Exa"
    key_url = "https://exa.ai"
    key_mode = "required"
    requires_key = True
    supports_fetch = True

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        resp = httpx.post(
            "https://api.exa.ai/search",
            headers={
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
                "x-exa-integration": "andrewyng/openworker-integration",
            },
            json={
                "query": query,
                "type": "auto",
                "numResults": max_results,
                "contents": {"highlights": True},
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(
                title=r.get("title") or "",
                url=r.get("url") or "",
                snippet=" ".join(r.get("highlights") or []),
            )
            for r in data.get("results", [])
        ]

    def fetch_page(self, url: str) -> str:
        # https://exa.ai/docs/reference/get-contents — POST {urls, text: true}; the text is
        # results[].text, and statuses[] says per URL whether the crawl succeeded.
        import httpx

        resp = httpx.post(
            "https://api.exa.ai/contents",
            headers={
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
                "x-exa-integration": "andrewyng/openworker-integration",
            },
            json={"urls": [url], "text": True},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        for r in data.get("results") or []:
            if r.get("text"):
                return str(r["text"])
        detail = "no content returned"
        for st in data.get("statuses") or []:
            if st.get("status") == "error":
                err = st.get("error") or {}
                detail = f"{err.get('tag', 'error')} (HTTP {err.get('httpStatusCode', '?')})"
                break
        raise RuntimeError(f"Exa could not fetch the page: {detail}")


class FirecrawlProvider(WebSearchProvider):
    """Firecrawl web search (https://docs.firecrawl.dev) via the v2 REST API."""

    name = "firecrawl"
    display_name = "Firecrawl"
    key_url = "https://firecrawl.dev"
    key_mode = "required"
    requires_key = True
    supports_fetch = True

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        import httpx

        resp = httpx.post(
            "https://api.firecrawl.dev/v2/search",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"query": query, "limit": max_results},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
            )
            for r in (data.get("data", {}) or {}).get("web", [])
        ]

    def fetch_page(self, url: str) -> str:
        # https://docs.firecrawl.dev/api-reference/endpoint/scrape — POST /v2/scrape
        # {url, formats: ["markdown"]}, bearer auth; the text is data.markdown.
        import httpx

        resp = httpx.post(
            "https://api.firecrawl.dev/v2/scrape",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"url": url, "formats": ["markdown"]},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        text = (data.get("data") or {}).get("markdown") if isinstance(data, dict) else None
        if not text:
            detail = data.get("error") if isinstance(data, dict) else None
            raise RuntimeError(
                f"Firecrawl could not scrape the page: {detail or 'no content returned'}"
            )
        return str(text)


_PROVIDERS = {
    "duckduckgo": DuckDuckGoProvider,
    "you": YouProvider,
    "tavily": TavilyProvider,
    "brave": BraveProvider,
    "serper": SerperProvider,
    "exa": ExaProvider,
    "firecrawl": FirecrawlProvider,
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


def provider_class(name: str) -> Optional[type[WebSearchProvider]]:
    return _PROVIDERS.get(name)


_VERIFY_TIMEOUT = 10.0


def verify_key(name: str, api_key: str) -> Optional[str]:
    """Run one search with `api_key` ("Test and save"). None when it worked, else the
    provider's error text — with the key scrubbed, should a library echo it."""
    try:
        provider = build_provider(name, api_key)
        provider.timeout = _VERIFY_TIMEOUT
        provider.search("openworker", max_results=1)
    except Exception as exc:  # noqa: BLE001 - the text goes to the person as-is
        text = str(exc) or exc.__class__.__name__
        return text.replace(api_key, "•••") if api_key else text
    return None
