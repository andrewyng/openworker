"""Apify connector: cache one dataset locally so search doesn't re-fetch it.

Read only. One connected account is one dataset. Refresh runs through a
scheduled task calling apify_refresh_cache, not a background loop.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Callable, Optional

import aisuite as ai

from ..secrets import SecretStore, state_dir
from .record_cache import FieldMap, RecordCache

_API = "https://api.apify.com/v2"
_PAGE_SIZE = 1000
_DEFAULT_MAX_RECORDS = 5000
_MAX_MAX_RECORDS = 50_000
_DEFAULT_SEARCH_LIMIT = 10
_MAX_SEARCH_LIMIT = 100


def _http(method: str, url: str, **kw: Any) -> dict[str, Any]:
    """Call integration_tools._request, imported here rather than at module level
    to avoid a circular import (integration_tools imports this module) and to keep
    a single monkeypatch seam for tests."""
    from . import integration_tools

    return integration_tools._request(method, url, **kw)


# -- tool metadata plumbing (same shape as the sibling connector modules) -----------
def _meta(name: str, *, approval: bool, capabilities: list[str]):
    return ai.ToolMetadata(
        name=name,
        category="connector",
        risk_level="medium" if approval else "low",
        capabilities=capabilities,
        requires_approval=approval,
    )


def _schema(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _attach(
    fn: Callable[..., Any],
    schema: dict[str, Any],
    *,
    approval: bool,
    caps: list[str],
):
    from .tool_defs import approval_for_tool

    name = schema["function"]["name"]
    # §36: the tool registry's read/write kind wins for registered tools — reads
    # never gate. All three Apify tools are registered "read" in tool_defs.py.
    approval = approval_for_tool(name, default=approval)
    fn.__name__ = name
    fn.__coworker_schema__ = schema
    fn.__aisuite_tool_metadata__ = _meta(name, approval=approval, capabilities=caps)
    fn.__doc__ = schema["function"]["description"]
    return fn


_GEN_ACCOUNT_PROP = {
    "type": "string",
    "description": "Which connected dataset to use (default when empty)",
}


def _field_map(profile: dict[str, Any]) -> FieldMap:
    text_fields = tuple(
        f.strip()
        for f in str(profile.get("text_fields") or "").split(",")
        if f.strip()
    )
    return FieldMap(
        key_field=str(profile.get("key_field") or "url").strip() or "url",
        title_field=str(profile.get("title_field") or "title").strip() or "title",
        url_field=str(profile.get("url_field") or "url").strip() or "url",
        text_fields=text_fields,
    )


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat()


def make_apify_tools(
    secrets: SecretStore, *, cache_path: Optional[str] = None
) -> list[Callable[..., Any]]:
    """Build the Apify tools. `cache_path` is injectable so tests point the cache
    at a tmp_path file; the default resolves lazily inside each tool call (never
    at factory-build time, and never before the "not connected" check), so calling
    these tools against an empty SecretStore — which several existing tests do —
    never creates a stray cache file on disk."""

    def _cache() -> RecordCache:
        return RecordCache(cache_path or str(state_dir() / "connector_cache.db"))

    def _resolve(account: str):
        """(account_id, profile, source_id, err). Runs before any cache access."""
        from .integration_tools import _account_profile

        aid, profile, err = _account_profile(
            secrets, "apify", account, "api_token", "dataset_id"
        )
        if err:
            return "", None, "", err
        return aid, profile, f"apify:{aid}", None

    def apify_refresh_cache(
        max_records: int = _DEFAULT_MAX_RECORDS, account: str = ""
    ) -> dict[str, Any]:
        from .integration_tools import _acct_result, _bearer_headers, _clamp

        aid, profile, source_id, err = _resolve(account)
        if err:
            return err
        # profile["dataset_id"] — NEVER `aid` — is the value the API call needs.
        # `aid` (the account id) is derived via accounts._norm(), which LOWERCASES
        # it; Apify dataset ids are case-sensitive, so the stored, verbatim
        # dataset_id is what must go in the URL.
        dataset_id = profile["dataset_id"]
        want = _clamp(max_records, _DEFAULT_MAX_RECORDS, _MAX_MAX_RECORDS)
        headers = _bearer_headers(profile["api_token"])

        items: list[Any] = []
        offset = 0
        while len(items) < want:
            page = min(_PAGE_SIZE, want - len(items))
            result = _http(
                "GET",
                f"{_API}/datasets/{dataset_id}/items",
                headers=headers,
                params={"clean": "true", "limit": page, "offset": offset},
            )
            if "error" in result:
                return _acct_result(aid, result)
            data = result.get("data")
            if not isinstance(data, list):
                return _acct_result(
                    aid,
                    {
                        "error": "unexpected dataset payload; expected a JSON "
                        "array of items",
                        "details": type(data).__name__,
                    },
                )
            items.extend(data)
            if len(data) < page:
                break  # short page — no more items
            offset += len(data)

        with _cache() as cache:
            stats = cache.upsert(
                source_id,
                "apify",
                items,
                fmap=_field_map(profile),
                label=dataset_id,
            )

        return _acct_result(
            aid,
            {
                "ok": True,
                "source": source_id,
                "dataset_id": dataset_id,
                "fetched": stats["fetched"],
                "new": stats["new"],
                "updated": stats["updated"],
                "unchanged": stats["unchanged"],
                "truncated": stats["truncated"],
                "total_records": stats["total"],
                "fetched_at": _iso(stats["fetched_at"]),
                "search_mode": cache.search_mode,
            },
        )

    apify_refresh_cache.__name__ = "apify_refresh_cache"
    tools = [
        _attach(
            apify_refresh_cache,
            _schema(
                "apify_refresh_cache",
                "Pull the connected Apify dataset into the local cache. Idempotent: "
                "re-running adds no duplicates and reports how many records were "
                "new vs updated. Run this once, then answer questions with "
                "apify_search_cache instead of refreshing again.",
                {
                    "max_records": {"type": "integer"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                [],
            ),
            approval=False,
            caps=["apify", "read"],
        )
    ]

    def apify_search_cache(
        query: str, max_results: int = _DEFAULT_SEARCH_LIMIT, account: str = ""
    ) -> dict[str, Any]:
        from .integration_tools import _acct_result, _clamp
        from .record_cache import has_terms

        aid, profile, source_id, err = _resolve(account)
        if err:
            return err
        if not has_terms(query):
            return {"error": "query has no searchable terms"}

        limit = _clamp(max_results, _DEFAULT_SEARCH_LIMIT, _MAX_SEARCH_LIMIT)
        with _cache() as cache:
            rows, mode = cache.search(source_id, query, limit=limit)
            status = cache.status(source_id)

        if not rows and status["records"] == 0:
            return _acct_result(
                aid,
                {
                    "ok": True,
                    "query": query,
                    "search_mode": mode,
                    "count": 0,
                    "results": [],
                    "hint": "cache is empty — run apify_refresh_cache first",
                },
            )

        return _acct_result(
            aid,
            {
                "ok": True,
                "query": query,
                "search_mode": mode,
                "fetched_at": _iso(status["fetched_at"]),
                "count": len(rows),
                "results": [
                    {
                        "key": r.key,
                        "title": r.title,
                        "url": r.url,
                        "rank": r.rank,
                        "score": r.score,
                        "snippet": r.snippet,
                        "fields": r.data,
                    }
                    for r in rows
                ],
            },
        )

    apify_search_cache.__name__ = "apify_search_cache"
    tools.append(
        _attach(
            apify_search_cache,
            _schema(
                "apify_search_cache",
                "Keyword-search the locally cached Apify dataset records — ranked, "
                "no network, no API quota. If the cache is empty, run "
                "apify_refresh_cache first.",
                {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["query"],
            ),
            approval=False,
            caps=["apify", "read"],
        )
    )

    def apify_cache_status(account: str = "") -> dict[str, Any]:
        from .integration_tools import _acct_result

        aid, profile, source_id, err = _resolve(account)
        if err:
            return err
        with _cache() as cache:
            status = cache.status(source_id)
        return _acct_result(
            aid,
            {
                "ok": True,
                "source": source_id,
                "dataset_id": profile["dataset_id"],
                "records": status["records"],
                "fetched_at": _iso(status["fetched_at"]),
                "last_refresh": status["last_refresh"],
                "search_mode": status["search_mode"],
            },
        )

    apify_cache_status.__name__ = "apify_cache_status"
    tools.append(
        _attach(
            apify_cache_status,
            _schema(
                "apify_cache_status",
                "Report the local Apify cache: how many records are stored, when "
                "it was last refreshed, and which search engine is active. "
                "No network.",
                {"account": _GEN_ACCOUNT_PROP},
                [],
            ),
            approval=False,
            caps=["apify", "read"],
        )
    )

    return tools
