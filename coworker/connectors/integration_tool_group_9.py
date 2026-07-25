"""Bounded first-party integration tool builder partition."""

from __future__ import annotations

from typing import Any, Callable, Optional
from urllib.parse import quote

from ..secrets import SecretStore
from . import integration_tools as _it


def add_tools(
    secrets: SecretStore,
    roots: Optional[list[Any]],
    tools: list[Callable[..., Any]],
) -> None:
        # -- prospecting/enrichment: apollo / hunter (manual keys, multi-account) --

        def _apollo_headers(profile: dict[str, Any]) -> dict[str, str]:
            return {"X-Api-Key": profile["api_key"], "Content-Type": "application/json"}

        def apollo_enrich_person(
            email: str = "", name: str = "", company_domain: str = "", account: str = ""
        ) -> dict[str, Any]:
            if not email and not name:
                return {"error": "provide an email, a name, or both"}
            aid, profile, err = _it._account_profile(secrets, "apollo", account, "api_key")
            if err:
                return err
            body: dict[str, Any] = {}
            if email:
                body["email"] = email
            if name:
                body["name"] = name
            if company_domain:
                body["domain"] = company_domain
            result = _it._request(
                "POST",
                "https://api.apollo.io/api/v1/people/match",
                headers=_apollo_headers(profile),
                json=body,
            )
            return _it._acct_result(aid, result)

        apollo_enrich_person.__name__ = "apollo_enrich_person"
        tools.append(
            _it._attach(
                apollo_enrich_person,
                _it._schema(
                    "apollo_enrich_person",
                    "Enrich a person from Apollo: title, company, LinkedIn, location "
                    "— by email and/or name (+ optional company domain).",
                    {
                        "email": {"type": "string"},
                        "name": {"type": "string"},
                        "company_domain": {"type": "string"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    [],
                ),
                caps=["apollo", "read"],
            )
        )

        def apollo_enrich_company(domain: str, account: str = "") -> dict[str, Any]:
            aid, profile, err = _it._account_profile(secrets, "apollo", account, "api_key")
            if err:
                return err
            result = _it._request(
                "GET",
                "https://api.apollo.io/api/v1/organizations/enrich",
                headers=_apollo_headers(profile),
                params={"domain": domain},
            )
            return _it._acct_result(aid, result)

        apollo_enrich_company.__name__ = "apollo_enrich_company"
        tools.append(
            _it._attach(
                apollo_enrich_company,
                _it._schema(
                    "apollo_enrich_company",
                    "Enrich a company from Apollo by domain: size, industry, funding, "
                    "tech stack.",
                    {"domain": {"type": "string"}, "account": _it._GEN_ACCOUNT_PROP},
                    ["domain"],
                ),
                caps=["apollo", "read"],
            )
        )

        def apollo_search_people(
            query: str, max_results: int = 10, account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(secrets, "apollo", account, "api_key")
            if err:
                return err
            result = _it._request(
                "POST",
                "https://api.apollo.io/api/v1/mixed_people/search",
                headers=_apollo_headers(profile),
                json={"q_keywords": query, "page": 1, "per_page": _it._clamp(max_results)},
            )
            return _it._acct_result(aid, result)

        apollo_search_people.__name__ = "apollo_search_people"
        tools.append(
            _it._attach(
                apollo_search_people,
                _it._schema(
                    "apollo_search_people",
                    "Keyword-search people in Apollo's B2B database (e.g. 'VP "
                    "engineering fintech Berlin').",
                    {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["query"],
                ),
                caps=["apollo", "read"],
            )
        )

        def _hunter_get(
            profile: dict[str, Any], path: str, params: dict[str, Any]
        ) -> dict[str, Any]:
            return _it._request(
                "GET",
                f"https://api.hunter.io/v2/{path}",
                params={**params, "api_key": profile["api_key"]},
            )

        def hunter_domain_search(
            domain: str, max_results: int = 10, account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(secrets, "hunter", account, "api_key")
            if err:
                return err
            result = _hunter_get(
                profile, "domain-search", {"domain": domain, "limit": _it._clamp(max_results)}
            )
            return _it._acct_result(aid, result)

        hunter_domain_search.__name__ = "hunter_domain_search"
        tools.append(
            _it._attach(
                hunter_domain_search,
                _it._schema(
                    "hunter_domain_search",
                    "Find published email addresses for a company domain (Hunter).",
                    {
                        "domain": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["domain"],
                ),
                caps=["hunter", "read"],
            )
        )

        def hunter_find_email(
            domain: str, first_name: str, last_name: str, account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(secrets, "hunter", account, "api_key")
            if err:
                return err
            result = _hunter_get(
                profile,
                "email-finder",
                {"domain": domain, "first_name": first_name, "last_name": last_name},
            )
            return _it._acct_result(aid, result)

        hunter_find_email.__name__ = "hunter_find_email"
        tools.append(
            _it._attach(
                hunter_find_email,
                _it._schema(
                    "hunter_find_email",
                    "Find a person's most likely email address from their name and "
                    "company domain (Hunter).",
                    {
                        "domain": {"type": "string"},
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["domain", "first_name", "last_name"],
                ),
                caps=["hunter", "read"],
            )
        )

        def hunter_verify_email(email: str, account: str = "") -> dict[str, Any]:
            aid, profile, err = _it._account_profile(secrets, "hunter", account, "api_key")
            if err:
                return err
            return _it._acct_result(
                aid, _hunter_get(profile, "email-verifier", {"email": email})
            )

        hunter_verify_email.__name__ = "hunter_verify_email"
        tools.append(
            _it._attach(
                hunter_verify_email,
                _it._schema(
                    "hunter_verify_email",
                    "Check whether an email address is deliverable (Hunter).",
                    {"email": {"type": "string"}, "account": _it._GEN_ACCOUNT_PROP},
                    ["email"],
                ),
                caps=["hunter", "read"],
            )
        )

        # --- ClickUp ------------------------------------------------------------

        _CLICKUP = "https://api.clickup.com/api/v2"

        def clickup_list_teams() -> dict[str, Any]:
            profile, err = _it._profile(secrets, "clickup", "api_token")
            if err:
                return err
            return _it._request(
                "GET", f"{_CLICKUP}/team", headers={"Authorization": profile["api_token"]}
            )

        clickup_list_teams.__name__ = "clickup_list_teams"
        tools.append(
            _it._attach(
                clickup_list_teams,
                _it._schema(
                    "clickup_list_teams",
                    "List ClickUp workspaces (team ids are needed to browse spaces).",
                    {},
                    [],
                ),
                caps=["clickup", "read"],
            )
        )

        def clickup_list_spaces(team_id: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "clickup", "api_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_CLICKUP}/team/{quote(team_id)}/space",
                headers={"Authorization": profile["api_token"]},
            )

        clickup_list_spaces.__name__ = "clickup_list_spaces"
        tools.append(
            _it._attach(
                clickup_list_spaces,
                _it._schema(
                    "clickup_list_spaces",
                    "List spaces in a ClickUp workspace.",
                    {"team_id": {"type": "string"}},
                    ["team_id"],
                ),
                caps=["clickup", "read"],
            )
        )

        def clickup_list_lists(space_id: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "clickup", "api_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_CLICKUP}/space/{quote(space_id)}/list",
                headers={"Authorization": profile["api_token"]},
            )

        clickup_list_lists.__name__ = "clickup_list_lists"
        tools.append(
            _it._attach(
                clickup_list_lists,
                _it._schema(
                    "clickup_list_lists",
                    "List folderless lists in a ClickUp space (list ids hold the tasks).",
                    {"space_id": {"type": "string"}},
                    ["space_id"],
                ),
                caps=["clickup", "read"],
            )
        )

        def clickup_list_tasks(
            list_id: str, include_closed: bool = False, max_results: int = 10
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "clickup", "api_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_CLICKUP}/list/{quote(list_id)}/task",
                headers={"Authorization": profile["api_token"]},
                params={
                    "include_closed": str(bool(include_closed)).lower(),
                    "page": 0,
                },
            )

        clickup_list_tasks.__name__ = "clickup_list_tasks"
        tools.append(
            _it._attach(
                clickup_list_tasks,
                _it._schema(
                    "clickup_list_tasks",
                    "List tasks in a ClickUp list.",
                    {
                        "list_id": {"type": "string"},
                        "include_closed": {"type": "boolean"},
                        "max_results": {"type": "integer"},
                    },
                    ["list_id"],
                ),
                caps=["clickup", "read"],
            )
        )

        def clickup_get_task(task_id: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "clickup", "api_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_CLICKUP}/task/{quote(task_id)}",
                headers={"Authorization": profile["api_token"]},
                params={"include_subtasks": "true"},
            )

        clickup_get_task.__name__ = "clickup_get_task"
        tools.append(
            _it._attach(
                clickup_get_task,
                _it._schema(
                    "clickup_get_task",
                    "Read a ClickUp task (with subtasks) by id.",
                    {"task_id": {"type": "string"}},
                    ["task_id"],
                ),
                caps=["clickup", "read"],
            )
        )

        def clickup_create_task(
            list_id: str, name: str, description: str = ""
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "clickup", "api_token")
            if err:
                return err
            return _it._request(
                "POST",
                f"{_CLICKUP}/list/{quote(list_id)}/task",
                headers={"Authorization": profile["api_token"]},
                json={"name": name, "description": description},
            )

        clickup_create_task.__name__ = "clickup_create_task"
        tools.append(
            _it._attach(
                clickup_create_task,
                _it._schema(
                    "clickup_create_task",
                    "Create a ClickUp task in a list. Requires user approval.",
                    {
                        "list_id": {"type": "string"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    ["list_id", "name"],
                ),
                approval=True,
                caps=["clickup", "write"],
            )
        )

        def clickup_update_task(
            task_id: str, name: str = "", description: str = "", status: str = ""
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "clickup", "api_token")
            if err:
                return err
            body: dict[str, Any] = {}
            if name:
                body["name"] = name
            if description:
                body["description"] = description
            if status:
                body["status"] = status
            if not body:
                return {"error": "nothing to update: pass name, description, or status"}
            return _it._request(
                "PUT",
                f"{_CLICKUP}/task/{quote(task_id)}",
                headers={"Authorization": profile["api_token"]},
                json=body,
            )

        clickup_update_task.__name__ = "clickup_update_task"
        tools.append(
            _it._attach(
                clickup_update_task,
                _it._schema(
                    "clickup_update_task",
                    "Update a ClickUp task's name, description, or status. Requires user approval.",
                    {
                        "task_id": {"type": "string"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "status": {"type": "string"},
                    },
                    ["task_id"],
                ),
                approval=True,
                caps=["clickup", "write"],
            )
        )

        def clickup_add_comment(task_id: str, text: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "clickup", "api_token")
            if err:
                return err
            return _it._request(
                "POST",
                f"{_CLICKUP}/task/{quote(task_id)}/comment",
                headers={"Authorization": profile["api_token"]},
                json={"comment_text": text},
            )

        clickup_add_comment.__name__ = "clickup_add_comment"
        tools.append(
            _it._attach(
                clickup_add_comment,
                _it._schema(
                    "clickup_add_comment",
                    "Comment on a ClickUp task. Requires user approval.",
                    {"task_id": {"type": "string"}, "text": {"type": "string"}},
                    ["task_id", "text"],
                ),
                approval=True,
                caps=["clickup", "write"],
            )
        )

        # --- Close --------------------------------------------------------------

        _CLOSE = "https://api.close.com/api/v1"

        def _close_auth(profile: dict[str, Any]) -> tuple[str, str]:
            # HTTP basic: API key as username, blank password.
            return (str(profile.get("api_key", "")), "")

        def close_search_leads(query: str, max_results: int = 10) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "close", "api_key")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_CLOSE}/lead/",
                auth=_close_auth(profile),
                params={"query": query, "_limit": _it._clamp(max_results)},
            )

        close_search_leads.__name__ = "close_search_leads"
        tools.append(
            _it._attach(
                close_search_leads,
                _it._schema(
                    "close_search_leads",
                    'Search Close leads (supports Close\'s search syntax, e.g. "status:potential acme").',
                    {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                    ["query"],
                ),
                caps=["close", "read"],
            )
        )

        def close_get_lead(lead_id: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "close", "api_key")
            if err:
                return err
            return _it._request(
                "GET", f"{_CLOSE}/lead/{quote(lead_id)}/", auth=_close_auth(profile)
            )

        close_get_lead.__name__ = "close_get_lead"
        tools.append(
            _it._attach(
                close_get_lead,
                _it._schema(
                    "close_get_lead",
                    "Read a Close lead (contacts, opportunities, addresses) by id.",
                    {"lead_id": {"type": "string"}},
                    ["lead_id"],
                ),
                caps=["close", "read"],
            )
        )

        def close_list_opportunities(
            lead_id: str = "", max_results: int = 10
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "close", "api_key")
            if err:
                return err
            params: dict[str, Any] = {"_limit": _it._clamp(max_results)}
            if lead_id:
                params["lead_id"] = lead_id
            return _it._request(
                "GET", f"{_CLOSE}/opportunity/", auth=_close_auth(profile), params=params
            )

        close_list_opportunities.__name__ = "close_list_opportunities"
        tools.append(
            _it._attach(
                close_list_opportunities,
                _it._schema(
                    "close_list_opportunities",
                    "List Close opportunities, optionally for one lead.",
                    {"lead_id": {"type": "string"}, "max_results": {"type": "integer"}},
                    [],
                ),
                caps=["close", "read"],
            )
        )

        def close_create_lead(
            name: str, contact_name: str = "", contact_email: str = ""
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "close", "api_key")
            if err:
                return err
            body: dict[str, Any] = {"name": name}
            if contact_name or contact_email:
                contact: dict[str, Any] = {"name": contact_name}
                if contact_email:
                    contact["emails"] = [{"email": contact_email}]
                body["contacts"] = [contact]
            return _it._request("POST", f"{_CLOSE}/lead/", auth=_close_auth(profile), json=body)

        close_create_lead.__name__ = "close_create_lead"
        tools.append(
            _it._attach(
                close_create_lead,
                _it._schema(
                    "close_create_lead",
                    "Create a Close lead (company), optionally with one contact. Requires user approval.",
                    {
                        "name": {"type": "string"},
                        "contact_name": {"type": "string"},
                        "contact_email": {"type": "string"},
                    },
                    ["name"],
                ),
                approval=True,
                caps=["close", "write"],
            )
        )

        def close_update_opportunity(
            opportunity_id: str, status_id: str = "", note: str = ""
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "close", "api_key")
            if err:
                return err
            body: dict[str, Any] = {}
            if status_id:
                body["status_id"] = status_id
            if note:
                body["note"] = note
            if not body:
                return {"error": "nothing to update: pass status_id or note"}
            return _it._request(
                "PUT",
                f"{_CLOSE}/opportunity/{quote(opportunity_id)}/",
                auth=_close_auth(profile),
                json=body,
            )

        close_update_opportunity.__name__ = "close_update_opportunity"
        tools.append(
            _it._attach(
                close_update_opportunity,
                _it._schema(
                    "close_update_opportunity",
                    "Update a Close opportunity's status or note. Requires user approval.",
                    {
                        "opportunity_id": {"type": "string"},
                        "status_id": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    ["opportunity_id"],
                ),
                approval=True,
                caps=["close", "write"],
            )
        )

        def close_log_note(lead_id: str, note: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "close", "api_key")
            if err:
                return err
            return _it._request(
                "POST",
                f"{_CLOSE}/activity/note/",
                auth=_close_auth(profile),
                json={"lead_id": lead_id, "note": note},
            )

        close_log_note.__name__ = "close_log_note"
        tools.append(
            _it._attach(
                close_log_note,
                _it._schema(
                    "close_log_note",
                    "Log a note on a Close lead's timeline. Requires user approval.",
                    {"lead_id": {"type": "string"}, "note": {"type": "string"}},
                    ["lead_id", "note"],
                ),
                approval=True,
                caps=["close", "write"],
            )
        )
