"""Web search — a keyless DuckDuckGo default + configurable third-party providers."""

from __future__ import annotations

from .providers import (
    BraveProvider,
    DuckDuckGoProvider,
    SearchResult,
    TavilyProvider,
    WebSearchProvider,
    build_provider,
    env_key_name,
    provider_names,
    provider_requires_key,
)
from .fetch import make_web_fetch_tool
from .tool import (
    PROFILE as WEB_SEARCH_PROFILE,
    key_source,
    make_web_search_tool,
    resolve_api_key,
    resolve_provider,
    stored_keys,
)

__all__ = [
    "SearchResult",
    "WebSearchProvider",
    "DuckDuckGoProvider",
    "TavilyProvider",
    "BraveProvider",
    "build_provider",
    "provider_names",
    "provider_requires_key",
    "env_key_name",
    "WEB_SEARCH_PROFILE",
    "stored_keys",
    "resolve_api_key",
    "key_source",
    "make_web_search_tool",
    "make_web_fetch_tool",
    "resolve_provider",
]
