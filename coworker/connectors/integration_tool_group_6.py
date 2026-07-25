"""Bounded first-party integration tool builder partition."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from ..secrets import SecretStore
from . import integration_tools as _it

_PORTAL_PROP = {
    "type": "string",
    "description": "Optional HubSpot portal/profile name",
}
_HS_KINDS = ("contacts", "companies", "deals", "tickets")


def add_tools(
    secrets: SecretStore,
    roots: Optional[list[Any]],
    tools: list[Callable[..., Any]],
) -> None:
        def hubspot_search(
            query: str = "",
            object_type: str = "contacts",
            max_results: int = 10,
            properties: str = "",
            filters: str = "",
            portal: str = "",
        ) -> dict[str, Any]:
            name, token, err = _it._hubspot_profile(secrets, portal)
            if err:
                return err
            kind = object_type if object_type in _HS_KINDS else "contacts"
            # The search API only returns HubSpot's default properties unless asked,
            # and free-text `query` never matches custom properties — so property
            # filters are the only way to select on them (e.g. an "org_type" field).
            body: dict[str, Any] = {"limit": _it._clamp(max_results, ceiling=100)}
            if query:
                body["query"] = query
            if properties:
                body["properties"] = [p.strip() for p in properties.split(",") if p.strip()]
            if filters:
                try:
                    parsed = json.loads(filters)
                except ValueError:
                    return {"error": "filters must be a JSON array of filter objects"}
                if not isinstance(parsed, list) or not all(
                    isinstance(f, dict) and f.get("property") and f.get("operator")
                    for f in parsed
                ):
                    return {"error": "each filter needs at least 'property' and 'operator'"}
                body["filterGroups"] = [{"filters": parsed}]
            if not query and not filters:
                return {"error": "provide a query, filters, or both"}
            result = _it._request(
                "POST",
                f"https://api.hubapi.com/crm/v3/objects/{kind}/search",
                headers=_it._bearer_headers(token),
                json=body,
            )
            return _it._hubspot_result(secrets, name, result)

        hubspot_search.__name__ = "hubspot_search"
        tools.append(
            _it._attach(
                hubspot_search,
                _it._schema(
                    "hubspot_search",
                    "Search HubSpot CRM contacts, companies, deals, or tickets (object_type). "
                    "Custom properties are only returned if named in `properties`, and only "
                    "matchable via `filters` (free-text query searches default fields only).",
                    {
                        "query": {"type": "string", "description": "Free-text search"},
                        "object_type": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "properties": {
                            "type": "string",
                            "description": "Comma-separated property names to return "
                            "(include custom properties here)",
                        },
                        "filters": {
                            "type": "string",
                            "description": 'JSON array of {"property", "operator", "value"} '
                            "objects, ANDed together. Operators: EQ, NEQ, LT, LTE, GT, GTE, "
                            "CONTAINS_TOKEN, HAS_PROPERTY, NOT_HAS_PROPERTY, IN",
                        },
                        "portal": _PORTAL_PROP,
                    },
                    [],
                ),
                caps=["hubspot", "read"],
            )
        )

        def hubspot_get_object(
            object_type: str,
            object_id: str,
            properties: str = "",
            associations: str = "",
            portal: str = "",
        ) -> dict[str, Any]:
            name, token, err = _it._hubspot_profile(secrets, portal)
            if err:
                return err
            kind = object_type if object_type in _HS_KINDS else "contacts"
            params: dict[str, Any] = {}
            if properties:
                params["properties"] = properties  # API takes the comma string as-is
            if associations:
                params["associations"] = associations
            result = _it._request(
                "GET",
                f"https://api.hubapi.com/crm/v3/objects/{kind}/{object_id}",
                headers=_it._bearer_headers(token),
                params=params or None,
            )
            return _it._hubspot_result(secrets, name, result)

        hubspot_get_object.__name__ = "hubspot_get_object"
        tools.append(
            _it._attach(
                hubspot_get_object,
                _it._schema(
                    "hubspot_get_object",
                    "Read a HubSpot CRM record by ID. Custom properties are only "
                    "returned if named in `properties`; pass `associations` to also get "
                    "linked record ids.",
                    {
                        "object_type": {"type": "string"},
                        "object_id": {"type": "string"},
                        "properties": {
                            "type": "string",
                            "description": "Comma-separated property names to return",
                        },
                        "associations": {
                            "type": "string",
                            "description": "Comma-separated object types to return "
                            "associated ids for (e.g. companies,contacts)",
                        },
                        "portal": _PORTAL_PROP,
                    },
                    ["object_type", "object_id"],
                ),
                caps=["hubspot", "read"],
            )
        )

        def hubspot_create_contact(
            email: str, first_name: str = "", last_name: str = "", portal: str = ""
        ) -> dict[str, Any]:
            name, token, err = _it._hubspot_profile(secrets, portal)
            if err:
                return err
            props = {"email": email}
            if first_name:
                props["firstname"] = first_name
            if last_name:
                props["lastname"] = last_name
            result = _it._request(
                "POST",
                "https://api.hubapi.com/crm/v3/objects/contacts",
                headers=_it._bearer_headers(token),
                json={"properties": props},
            )
            return _it._hubspot_result(secrets, name, result)

        hubspot_create_contact.__name__ = "hubspot_create_contact"
        tools.append(
            _it._attach(
                hubspot_create_contact,
                _it._schema(
                    "hubspot_create_contact",
                    "Create a HubSpot contact. Requires user approval; the `portal` "
                    "argument names the portal on the approval card.",
                    {
                        "email": {"type": "string"},
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                        "portal": _PORTAL_PROP,
                    },
                    ["email"],
                ),
                approval=True,
                caps=["hubspot", "write"],
            )
        )

        def hubspot_update_object(
            object_type: str, object_id: str, properties: dict, portal: str = ""
        ) -> dict[str, Any]:
            name, token, err = _it._hubspot_profile(secrets, portal)
            if err:
                return err
            kind = object_type if object_type in _HS_KINDS else "contacts"
            if not isinstance(properties, dict) or not properties:
                return {"error": "properties must be a non-empty object"}
            result = _it._request(
                "PATCH",
                f"https://api.hubapi.com/crm/v3/objects/{kind}/{object_id}",
                headers=_it._bearer_headers(token),
                json={"properties": properties},
            )
            return _it._hubspot_result(secrets, name, result)

        hubspot_update_object.__name__ = "hubspot_update_object"
        tools.append(
            _it._attach(
                hubspot_update_object,
                _it._schema(
                    "hubspot_update_object",
                    "Update properties on a HubSpot CRM record (no deletes exist). "
                    "Requires user approval.",
                    {
                        "object_type": {"type": "string"},
                        "object_id": {"type": "string"},
                        "properties": {"type": "object"},
                        "portal": _PORTAL_PROP,
                    },
                    ["object_type", "object_id", "properties"],
                ),
                approval=True,
                caps=["hubspot", "write"],
            )
        )

        def hubspot_log_note(
            object_type: str, object_id: str, note: str, portal: str = ""
        ) -> dict[str, Any]:
            name, token, err = _it._hubspot_profile(secrets, portal)
            if err:
                return err
            kind = object_type if object_type in _HS_KINDS else "contacts"
            # Note engagement associated to the record (association type ids are
            # HubSpot-defined per object; v4 default associations handle the rest).
            result = _it._request(
                "POST",
                "https://api.hubapi.com/crm/v3/objects/notes",
                headers=_it._bearer_headers(token),
                json={
                    "properties": {
                        "hs_note_body": note,
                        "hs_timestamp": _it._now_ms(),
                    },
                    "associations": [
                        {
                            "to": {"id": object_id},
                            "types": [
                                {
                                    "associationCategory": "HUBSPOT_DEFINED",
                                    "associationTypeId": _it._HS_NOTE_ASSOC[kind],
                                }
                            ],
                        }
                    ],
                },
            )
            return _it._hubspot_result(secrets, name, result)

        hubspot_log_note.__name__ = "hubspot_log_note"
        tools.append(
            _it._attach(
                hubspot_log_note,
                _it._schema(
                    "hubspot_log_note",
                    "Log a note on a HubSpot record's timeline. Requires user approval.",
                    {
                        "object_type": {"type": "string"},
                        "object_id": {"type": "string"},
                        "note": {"type": "string"},
                        "portal": _PORTAL_PROP,
                    },
                    ["object_type", "object_id", "note"],
                ),
                approval=True,
                caps=["hubspot", "write"],
            )
        )

        def hubspot_create_task(
            title: str, due: str = "", notes: str = "", portal: str = ""
        ) -> dict[str, Any]:
            name, token, err = _it._hubspot_profile(secrets, portal)
            if err:
                return err
            props: dict[str, Any] = {
                "hs_task_subject": title,
                "hs_task_status": "NOT_STARTED",
                "hs_timestamp": due or _it._now_ms(),
            }
            if notes:
                props["hs_task_body"] = notes
            result = _it._request(
                "POST",
                "https://api.hubapi.com/crm/v3/objects/tasks",
                headers=_it._bearer_headers(token),
                json={"properties": props},
            )
            return _it._hubspot_result(secrets, name, result)

        hubspot_create_task.__name__ = "hubspot_create_task"
        tools.append(
            _it._attach(
                hubspot_create_task,
                _it._schema(
                    "hubspot_create_task",
                    "Create a HubSpot task (due = epoch ms or ISO date). Requires user approval.",
                    {
                        "title": {"type": "string"},
                        "due": {"type": "string"},
                        "notes": {"type": "string"},
                        "portal": _PORTAL_PROP,
                    },
                    ["title"],
                ),
                approval=True,
                caps=["hubspot", "write"],
            )
        )

        def _dropbox_path(path: str) -> str:
            path = (path or "").strip()
            if path and not path.startswith("/"):
                path = "/" + path
            return path
