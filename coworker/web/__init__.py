"""Web search — a keyless DuckDuckGo default + configurable third-party providers."""

from __future__ import annotations

from .providers import (
    BraveProvider,
    DuckDuckGoProvider,
    ExaProvider,
    FirecrawlProvider,
    SearchResult,
    SerperProvider,
    TavilyProvider,
    WebSearchProvider,
    YouProvider,
    build_provider,
    provider_names,
)
from .fetch import make_web_fetch_tool
from .tool import (
    fetch_provider,
    fetch_provider_name,
    make_web_search_tool,
    provider_host,
    provider_name,
    resolve_provider,
)

__all__ = [
    "SearchResult",
    "WebSearchProvider",
    "DuckDuckGoProvider",
    "TavilyProvider",
    "BraveProvider",
    "SerperProvider",
    "ExaProvider",
    "FirecrawlProvider",
    "YouProvider",
    "build_provider",
    "provider_names",
    "make_web_search_tool",
    "make_web_fetch_tool",
    "provider_name",
    "provider_host",
    "resolve_provider",
    "fetch_provider",
    "fetch_provider_name",
]
