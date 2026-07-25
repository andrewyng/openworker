"""Bounded first-party integration tool builder partition."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from ..secrets import SecretStore
from . import integration_tools as _it


def add_tools(
    secrets: SecretStore,
    roots: Optional[list[Any]],
    tools: list[Callable[..., Any]],
) -> None:
        # -- notion (managed OAuth or integration token, multi-workspace) --

        def _notion_headers(profile: dict[str, Any]) -> dict[str, str]:
            return {
                "Authorization": f"Bearer {profile['access_token']}",
                "Notion-Version": "2022-06-28",
            }

        def _notion_blocks_text(blocks: list[dict]) -> str:
            """Flatten block children to readable lines (rich_text plain_text)."""
            lines = []
            for b in blocks:
                content = b.get(b.get("type", ""), {})
                texts = content.get("rich_text") or content.get("title") or []
                line = "".join(
                    t.get("plain_text", "") for t in texts if isinstance(t, dict)
                )
                if line:
                    lines.append(line)
            return "\n".join(lines)

        def notion_search(
            query: str, max_results: int = 10, account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(secrets, "notion", account, "access_token")
            if err:
                return err
            result = _it._request(
                "POST",
                "https://api.notion.com/v1/search",
                headers=_notion_headers(profile),
                json={"query": query, "page_size": _it._clamp(max_results, ceiling=100)},
            )
            return _it._acct_result(aid, result)

        notion_search.__name__ = "notion_search"
        tools.append(
            _it._attach(
                notion_search,
                _it._schema(
                    "notion_search",
                    "Search Notion pages and databases the integration can see.",
                    {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["query"],
                ),
                caps=["notion", "read"],
            )
        )

        def notion_read_page(page_id: str, account: str = "") -> dict[str, Any]:
            aid, profile, err = _it._account_profile(secrets, "notion", account, "access_token")
            if err:
                return err
            page = _it._request(
                "GET",
                f"https://api.notion.com/v1/pages/{page_id}",
                headers=_notion_headers(profile),
            )
            if "error" in page:
                return _it._acct_result(aid, page)
            blocks = _it._request(
                "GET",
                f"https://api.notion.com/v1/blocks/{page_id}/children",
                headers=_notion_headers(profile),
                params={"page_size": 100},
            )
            text = (
                _notion_blocks_text((blocks.get("data") or {}).get("results") or [])
                if "error" not in blocks
                else ""
            )
            return _it._acct_result(
                aid,
                {
                    "ok": True,
                    "properties": (page.get("data") or {}).get("properties"),
                    "url": (page.get("data") or {}).get("url"),
                    "text": text,
                },
            )

        notion_read_page.__name__ = "notion_read_page"
        tools.append(
            _it._attach(
                notion_read_page,
                _it._schema(
                    "notion_read_page",
                    "Read a Notion page: properties plus its content flattened to text.",
                    {"page_id": {"type": "string"}, "account": _it._GEN_ACCOUNT_PROP},
                    ["page_id"],
                ),
                caps=["notion", "read"],
            )
        )

        def notion_query_database(
            database_id: str,
            filter_json: str = "",
            max_results: int = 10,
            account: str = "",
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(secrets, "notion", account, "access_token")
            if err:
                return err
            body: dict[str, Any] = {"page_size": _it._clamp(max_results, ceiling=100)}
            if filter_json:
                try:
                    body["filter"] = json.loads(filter_json)
                except ValueError:
                    return {"error": "filter_json must be a Notion filter object (JSON)"}
            result = _it._request(
                "POST",
                f"https://api.notion.com/v1/databases/{database_id}/query",
                headers=_notion_headers(profile),
                json=body,
            )
            return _it._acct_result(aid, result)

        notion_query_database.__name__ = "notion_query_database"
        tools.append(
            _it._attach(
                notion_query_database,
                _it._schema(
                    "notion_query_database",
                    "Query a Notion database, optionally with a Notion filter object.",
                    {
                        "database_id": {"type": "string"},
                        "filter_json": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["database_id"],
                ),
                caps=["notion", "read"],
            )
        )

        def notion_create_page(
            parent_page_id: str, title: str, content: str = "", account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(secrets, "notion", account, "access_token")
            if err:
                return err
            children = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"text": {"content": line}}]},
                }
                for line in content.splitlines()
                if line.strip()
            ]
            result = _it._request(
                "POST",
                "https://api.notion.com/v1/pages",
                headers=_notion_headers(profile),
                json={
                    "parent": {"page_id": parent_page_id},
                    "properties": {"title": {"title": [{"text": {"content": title}}]}},
                    "children": children,
                },
            )
            return _it._acct_result(aid, result)

        notion_create_page.__name__ = "notion_create_page"
        tools.append(
            _it._attach(
                notion_create_page,
                _it._schema(
                    "notion_create_page",
                    "Create a Notion page under a parent page (plain-text paragraphs).",
                    {
                        "parent_page_id": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["parent_page_id", "title"],
                ),
                approval=True,
                caps=["notion", "write"],
            )
        )

        # -- attio (managed OAuth or API key, multi-workspace) --

        def attio_list_objects(account: str = "") -> dict[str, Any]:
            aid, profile, err = _it._account_profile(secrets, "attio", account, "access_token")
            if err:
                return err
            result = _it._request(
                "GET",
                "https://api.attio.com/v2/objects",
                headers=_it._bearer_headers(profile["access_token"]),
            )
            return _it._acct_result(aid, result)

        attio_list_objects.__name__ = "attio_list_objects"
        tools.append(
            _it._attach(
                attio_list_objects,
                _it._schema(
                    "attio_list_objects",
                    "List Attio object types (companies, people, deals, custom).",
                    {"account": _it._GEN_ACCOUNT_PROP},
                    [],
                ),
                caps=["attio", "read"],
            )
        )

        def attio_query_records(
            object_type: str,
            filter_json: str = "",
            max_results: int = 10,
            account: str = "",
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(secrets, "attio", account, "access_token")
            if err:
                return err
            body: dict[str, Any] = {"limit": _it._clamp(max_results, ceiling=100)}
            if filter_json:
                try:
                    body["filter"] = json.loads(filter_json)
                except ValueError:
                    return {"error": "filter_json must be an Attio filter object (JSON)"}
            result = _it._request(
                "POST",
                f"https://api.attio.com/v2/objects/{object_type}/records/query",
                headers=_it._bearer_headers(profile["access_token"]),
                json=body,
            )
            return _it._acct_result(aid, result)

        attio_query_records.__name__ = "attio_query_records"
        tools.append(
            _it._attach(
                attio_query_records,
                _it._schema(
                    "attio_query_records",
                    "List/filter records of an Attio object (e.g. companies, people); "
                    "filter_json is an Attio filter object.",
                    {
                        "object_type": {"type": "string"},
                        "filter_json": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["object_type"],
                ),
                caps=["attio", "read"],
            )
        )

        def attio_get_record(
            object_type: str, record_id: str, account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(secrets, "attio", account, "access_token")
            if err:
                return err
            result = _it._request(
                "GET",
                f"https://api.attio.com/v2/objects/{object_type}/records/{record_id}",
                headers=_it._bearer_headers(profile["access_token"]),
            )
            return _it._acct_result(aid, result)

        attio_get_record.__name__ = "attio_get_record"
        tools.append(
            _it._attach(
                attio_get_record,
                _it._schema(
                    "attio_get_record",
                    "Read one Attio record by object type and record id.",
                    {
                        "object_type": {"type": "string"},
                        "record_id": {"type": "string"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["object_type", "record_id"],
                ),
                caps=["attio", "read"],
            )
        )

        def attio_create_note(
            parent_object: str,
            parent_record_id: str,
            title: str,
            content: str,
            account: str = "",
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(secrets, "attio", account, "access_token")
            if err:
                return err
            result = _it._request(
                "POST",
                "https://api.attio.com/v2/notes",
                headers=_it._bearer_headers(profile["access_token"]),
                json={
                    "data": {
                        "parent_object": parent_object,
                        "parent_record_id": parent_record_id,
                        "title": title,
                        "format": "plaintext",
                        "content": content,
                    }
                },
            )
            return _it._acct_result(aid, result)

        attio_create_note.__name__ = "attio_create_note"
        tools.append(
            _it._attach(
                attio_create_note,
                _it._schema(
                    "attio_create_note",
                    "Log a note on an Attio record (e.g. a company or person).",
                    {
                        "parent_object": {"type": "string"},
                        "parent_record_id": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["parent_object", "parent_record_id", "title", "content"],
                ),
                approval=True,
                caps=["attio", "write"],
            )
        )

        # -- product analytics: posthog / mixpanel / amplitude (manual keys, multi-account) --

        def _posthog_base(profile: dict[str, Any]) -> str:
            return str(profile.get("base_url") or "https://us.posthog.com").rstrip("/")

        def posthog_query(hogql: str, account: str = "") -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "posthog", account, "api_key", "project_id"
            )
            if err:
                return err
            result = _it._request(
                "POST",
                f"{_posthog_base(profile)}/api/projects/{profile['project_id']}/query",
                headers=_it._bearer_headers(profile["api_key"]),
                json={"query": {"kind": "HogQLQuery", "query": hogql}},
            )
            return _it._acct_result(aid, result)

        posthog_query.__name__ = "posthog_query"
        tools.append(
            _it._attach(
                posthog_query,
                _it._schema(
                    "posthog_query",
                    "Run a HogQL (SQL-like) query against PostHog analytics, e.g. "
                    "SELECT event, count() FROM events WHERE timestamp > now() - "
                    "INTERVAL 7 DAY GROUP BY event.",
                    {"hogql": {"type": "string"}, "account": _it._GEN_ACCOUNT_PROP},
                    ["hogql"],
                ),
                caps=["posthog", "read"],
            )
        )

        def posthog_list_insights(
            query: str = "", max_results: int = 10, account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "posthog", account, "api_key", "project_id"
            )
            if err:
                return err
            params: dict[str, Any] = {"limit": _it._clamp(max_results)}
            if query:
                params["search"] = query
            result = _it._request(
                "GET",
                f"{_posthog_base(profile)}/api/projects/{profile['project_id']}/insights",
                headers=_it._bearer_headers(profile["api_key"]),
                params=params,
            )
            return _it._acct_result(aid, result)

        posthog_list_insights.__name__ = "posthog_list_insights"
        tools.append(
            _it._attach(
                posthog_list_insights,
                _it._schema(
                    "posthog_list_insights",
                    "List saved PostHog insights (dashboards' building blocks).",
                    {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    [],
                ),
                caps=["posthog", "read"],
            )
        )

        def mixpanel_segmentation(
            event: str,
            from_date: str,
            to_date: str,
            unit: str = "day",
            where: str = "",
            account: str = "",
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "mixpanel", account, "username", "secret", "project_id"
            )
            if err:
                return err
            params = {
                "project_id": profile["project_id"],
                "event": event,
                "from_date": from_date,
                "to_date": to_date,
                "unit": (
                    unit if unit in ("minute", "hour", "day", "week", "month") else "day"
                ),
            }
            if where:
                params["where"] = where
            result = _it._request(
                "GET",
                "https://mixpanel.com/api/query/segmentation",
                params=params,
                auth=(profile["username"], profile["secret"]),
            )
            return _it._acct_result(aid, result)

        mixpanel_segmentation.__name__ = "mixpanel_segmentation"
        tools.append(
            _it._attach(
                mixpanel_segmentation,
                _it._schema(
                    "mixpanel_segmentation",
                    "Mixpanel event counts over a date range (YYYY-MM-DD), optionally "
                    'filtered by a `where` expression like properties["plan"]=="pro".',
                    {
                        "event": {"type": "string"},
                        "from_date": {"type": "string"},
                        "to_date": {"type": "string"},
                        "unit": {"type": "string"},
                        "where": {"type": "string"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["event", "from_date", "to_date"],
                ),
                caps=["mixpanel", "read"],
            )
        )

        def mixpanel_top_events(max_results: int = 10, account: str = "") -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "mixpanel", account, "username", "secret", "project_id"
            )
            if err:
                return err
            result = _it._request(
                "GET",
                "https://mixpanel.com/api/query/events/top",
                params={
                    "project_id": profile["project_id"],
                    "type": "general",
                    "limit": _it._clamp(max_results, ceiling=100),
                },
                auth=(profile["username"], profile["secret"]),
            )
            return _it._acct_result(aid, result)

        mixpanel_top_events.__name__ = "mixpanel_top_events"
        tools.append(
            _it._attach(
                mixpanel_top_events,
                _it._schema(
                    "mixpanel_top_events",
                    "Today's top Mixpanel events by volume.",
                    {"max_results": {"type": "integer"}, "account": _it._GEN_ACCOUNT_PROP},
                    [],
                ),
                caps=["mixpanel", "read"],
            )
        )

        def amplitude_active_users(
            start: str, end: str, metric: str = "active", account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "amplitude", account, "api_key", "secret_key"
            )
            if err:
                return err
            result = _it._request(
                "GET",
                "https://amplitude.com/api/2/users",
                params={
                    "m": metric if metric in ("active", "new") else "active",
                    "start": start.replace("-", ""),
                    "end": end.replace("-", ""),
                    "i": 1,
                },
                auth=(profile["api_key"], profile["secret_key"]),
            )
            return _it._acct_result(aid, result)

        amplitude_active_users.__name__ = "amplitude_active_users"
        tools.append(
            _it._attach(
                amplitude_active_users,
                _it._schema(
                    "amplitude_active_users",
                    "Amplitude daily active or new users between two dates (YYYYMMDD "
                    "or YYYY-MM-DD).",
                    {
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "metric": {"type": "string", "description": "active | new"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["start", "end"],
                ),
                caps=["amplitude", "read"],
            )
        )

        def amplitude_event_totals(
            event_type: str, start: str, end: str, account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "amplitude", account, "api_key", "secret_key"
            )
            if err:
                return err
            result = _it._request(
                "GET",
                "https://amplitude.com/api/2/events/segmentation",
                params={
                    "e": json.dumps({"event_type": event_type}),
                    "start": start.replace("-", ""),
                    "end": end.replace("-", ""),
                    "m": "totals",
                },
                auth=(profile["api_key"], profile["secret_key"]),
            )
            return _it._acct_result(aid, result)

        amplitude_event_totals.__name__ = "amplitude_event_totals"
        tools.append(
            _it._attach(
                amplitude_event_totals,
                _it._schema(
                    "amplitude_event_totals",
                    "Daily totals for one Amplitude event between two dates.",
                    {
                        "event_type": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["event_type", "start", "end"],
                ),
                caps=["amplitude", "read"],
            )
        )
