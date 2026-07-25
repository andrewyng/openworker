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
        def gitlab_search(
            query: str, scope: str = "issues", max_results: int = 10
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "gitlab", "token")
            if err:
                return err
            kind = scope if scope in ("projects", "issues", "merge_requests") else "issues"
            return _it._request(
                "GET",
                f"{_it._gitlab_api(profile)}/search",
                headers={"PRIVATE-TOKEN": profile["token"]},
                params={"scope": kind, "search": query, "per_page": _it._clamp(max_results)},
            )

        gitlab_search.__name__ = "gitlab_search"
        tools.append(
            _it._attach(
                gitlab_search,
                _it._schema(
                    "gitlab_search",
                    "Search GitLab projects, issues, or merge_requests (scope).",
                    {
                        "query": {"type": "string"},
                        "scope": {"type": "string"},
                        "max_results": {"type": "integer"},
                    },
                    ["query"],
                ),
                caps=["gitlab", "read"],
            )
        )

        def gitlab_get_issue(project: str, issue_iid: int) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "gitlab", "token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_it._gitlab_api(profile)}/projects/{quote(project, safe='')}/issues/{issue_iid}",
                headers={"PRIVATE-TOKEN": profile["token"]},
            )

        gitlab_get_issue.__name__ = "gitlab_get_issue"
        tools.append(
            _it._attach(
                gitlab_get_issue,
                _it._schema(
                    "gitlab_get_issue",
                    "Read a GitLab issue. project is an ID or full path like group/repo.",
                    {"project": {"type": "string"}, "issue_iid": {"type": "integer"}},
                    ["project", "issue_iid"],
                ),
                caps=["gitlab", "read"],
            )
        )

        def gitlab_get_merge_request(project: str, mr_iid: int) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "gitlab", "token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_it._gitlab_api(profile)}/projects/{quote(project, safe='')}/merge_requests/{mr_iid}",
                headers={"PRIVATE-TOKEN": profile["token"]},
            )

        gitlab_get_merge_request.__name__ = "gitlab_get_merge_request"
        tools.append(
            _it._attach(
                gitlab_get_merge_request,
                _it._schema(
                    "gitlab_get_merge_request",
                    "Read a GitLab merge request. project is an ID or full path like group/repo.",
                    {"project": {"type": "string"}, "mr_iid": {"type": "integer"}},
                    ["project", "mr_iid"],
                ),
                caps=["gitlab", "read"],
            )
        )

        def gitlab_create_issue(
            project: str, title: str, description: str = ""
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "gitlab", "token")
            if err:
                return err
            return _it._request(
                "POST",
                f"{_it._gitlab_api(profile)}/projects/{quote(project, safe='')}/issues",
                headers={"PRIVATE-TOKEN": profile["token"]},
                json={"title": title, "description": description},
            )

        gitlab_create_issue.__name__ = "gitlab_create_issue"
        tools.append(
            _it._attach(
                gitlab_create_issue,
                _it._schema(
                    "gitlab_create_issue",
                    "Create a GitLab issue. Requires user approval.",
                    {
                        "project": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    ["project", "title"],
                ),
                approval=True,
                caps=["gitlab", "write"],
            )
        )

        def discord_list_channels(guild_id: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "discord", "bot_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"https://discord.com/api/v10/guilds/{guild_id}/channels",
                headers={"Authorization": f"Bot {profile['bot_token']}"},
            )

        discord_list_channels.__name__ = "discord_list_channels"
        tools.append(
            _it._attach(
                discord_list_channels,
                _it._schema(
                    "discord_list_channels",
                    "List channels in a Discord server (guild).",
                    {"guild_id": {"type": "string"}},
                    ["guild_id"],
                ),
                caps=["discord", "read"],
            )
        )

        def discord_read_messages(channel_id: str, max_results: int = 10) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "discord", "bot_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers={"Authorization": f"Bot {profile['bot_token']}"},
                params={"limit": _it._clamp(max_results, ceiling=50)},
            )

        discord_read_messages.__name__ = "discord_read_messages"
        tools.append(
            _it._attach(
                discord_read_messages,
                _it._schema(
                    "discord_read_messages",
                    "Read recent messages from a Discord channel.",
                    {"channel_id": {"type": "string"}, "max_results": {"type": "integer"}},
                    ["channel_id"],
                ),
                caps=["discord", "read"],
            )
        )

        def discord_send_message(channel_id: str, content: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "discord", "bot_token")
            if err:
                return err
            return _it._request(
                "POST",
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers={"Authorization": f"Bot {profile['bot_token']}"},
                json={"content": content[:2000]},
            )

        discord_send_message.__name__ = "discord_send_message"
        tools.append(
            _it._attach(
                discord_send_message,
                _it._schema(
                    "discord_send_message",
                    "Send a message to a Discord channel. Requires user approval.",
                    {"channel_id": {"type": "string"}, "content": {"type": "string"}},
                    ["channel_id", "content"],
                ),
                approval=True,
                caps=["discord", "write"],
            )
        )

        def stripe_search_customers(query: str, max_results: int = 10) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "stripe", "api_key")
            if err:
                return err
            return _it._request(
                "GET",
                "https://api.stripe.com/v1/customers/search",
                headers=_it._bearer_headers(profile["api_key"]),
                params={"query": query, "limit": _it._clamp(max_results)},
            )

        stripe_search_customers.__name__ = "stripe_search_customers"
        tools.append(
            _it._attach(
                stripe_search_customers,
                _it._schema(
                    "stripe_search_customers",
                    "Search Stripe customers. Query uses Stripe search syntax, e.g. email:'jane@example.com' or name~'Jane'.",
                    {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                    ["query"],
                ),
                caps=["stripe", "read"],
            )
        )

        def stripe_list_charges(
            customer_id: str = "", max_results: int = 10
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "stripe", "api_key")
            if err:
                return err
            params: dict[str, Any] = {"limit": _it._clamp(max_results)}
            if customer_id:
                params["customer"] = customer_id
            return _it._request(
                "GET",
                "https://api.stripe.com/v1/charges",
                headers=_it._bearer_headers(profile["api_key"]),
                params=params,
            )

        stripe_list_charges.__name__ = "stripe_list_charges"
        tools.append(
            _it._attach(
                stripe_list_charges,
                _it._schema(
                    "stripe_list_charges",
                    "List Stripe charges, optionally for one customer.",
                    {"customer_id": {"type": "string"}, "max_results": {"type": "integer"}},
                    [],
                ),
                caps=["stripe", "read"],
            )
        )

        def stripe_list_invoices(
            customer_id: str = "", max_results: int = 10
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "stripe", "api_key")
            if err:
                return err
            params: dict[str, Any] = {"limit": _it._clamp(max_results)}
            if customer_id:
                params["customer"] = customer_id
            return _it._request(
                "GET",
                "https://api.stripe.com/v1/invoices",
                headers=_it._bearer_headers(profile["api_key"]),
                params=params,
            )

        stripe_list_invoices.__name__ = "stripe_list_invoices"
        tools.append(
            _it._attach(
                stripe_list_invoices,
                _it._schema(
                    "stripe_list_invoices",
                    "List Stripe invoices, optionally for one customer.",
                    {"customer_id": {"type": "string"}, "max_results": {"type": "integer"}},
                    [],
                ),
                caps=["stripe", "read"],
            )
        )

        def asana_list_workspaces() -> dict[str, Any]:
            profile, err = _it._profile(secrets, "asana", "token")
            if err:
                return err
            return _it._request(
                "GET",
                "https://app.asana.com/api/1.0/workspaces",
                headers=_it._bearer_headers(profile["token"]),
            )

        asana_list_workspaces.__name__ = "asana_list_workspaces"
        tools.append(
            _it._attach(
                asana_list_workspaces,
                _it._schema(
                    "asana_list_workspaces",
                    "List Asana workspaces (GIDs are needed to search tasks).",
                    {},
                    [],
                ),
                caps=["asana", "read"],
            )
        )

        def asana_search_tasks(
            workspace_gid: str, query: str, max_results: int = 10
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "asana", "token")
            if err:
                return err
            return _it._request(
                "GET",
                f"https://app.asana.com/api/1.0/workspaces/{workspace_gid}/typeahead",
                headers=_it._bearer_headers(profile["token"]),
                params={
                    "resource_type": "task",
                    "query": query,
                    "count": _it._clamp(max_results),
                },
            )

        asana_search_tasks.__name__ = "asana_search_tasks"
        tools.append(
            _it._attach(
                asana_search_tasks,
                _it._schema(
                    "asana_search_tasks",
                    "Search Asana tasks by name in a workspace. Get workspace_gid from asana_list_workspaces.",
                    {
                        "workspace_gid": {"type": "string"},
                        "query": {"type": "string"},
                        "max_results": {"type": "integer"},
                    },
                    ["workspace_gid", "query"],
                ),
                caps=["asana", "read"],
            )
        )

        def asana_get_task(task_gid: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "asana", "token")
            if err:
                return err
            return _it._request(
                "GET",
                f"https://app.asana.com/api/1.0/tasks/{task_gid}",
                headers=_it._bearer_headers(profile["token"]),
            )

        asana_get_task.__name__ = "asana_get_task"
        tools.append(
            _it._attach(
                asana_get_task,
                _it._schema(
                    "asana_get_task",
                    "Read an Asana task.",
                    {"task_gid": {"type": "string"}},
                    ["task_gid"],
                ),
                caps=["asana", "read"],
            )
        )

        def asana_create_task(
            project_gid: str, name: str, notes: str = ""
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "asana", "token")
            if err:
                return err
            return _it._request(
                "POST",
                "https://app.asana.com/api/1.0/tasks",
                headers=_it._bearer_headers(profile["token"]),
                json={"data": {"name": name, "notes": notes, "projects": [project_gid]}},
            )

        asana_create_task.__name__ = "asana_create_task"
        tools.append(
            _it._attach(
                asana_create_task,
                _it._schema(
                    "asana_create_task",
                    "Create an Asana task in a project. Requires user approval.",
                    {
                        "project_gid": {"type": "string"},
                        "name": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    ["project_gid", "name"],
                ),
                approval=True,
                caps=["asana", "write"],
            )
        )

        _PORTAL_PROP = {
            "type": "string",
            "description": "Portal (hub id or name) to use; omit for the default portal.",
        }
        _HS_KINDS = ("contacts", "companies", "deals", "tickets")
