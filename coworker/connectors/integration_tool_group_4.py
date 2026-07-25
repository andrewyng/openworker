"""Bounded first-party integration tool builder partition."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..secrets import SecretStore
from . import integration_tools as _it


def add_tools(
    secrets: SecretStore,
    roots: Optional[list[Any]],
    tools: list[Callable[..., Any]],
) -> None:
        def jira_search_issues(jql: str, max_results: int = 10) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "jira", "base_url", "email", "api_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_it._atlassian_base(profile)}/rest/api/3/search",
                auth=_it._basic_auth(profile["email"], profile["api_token"]),
                params={"jql": jql, "maxResults": max(1, min(int(max_results or 10), 20))},
            )

        jira_search_issues.__name__ = "jira_search_issues"
        tools.append(
            _it._attach(
                jira_search_issues,
                _it._schema(
                    "jira_search_issues",
                    "Search Jira issues using JQL.",
                    {"jql": {"type": "string"}, "max_results": {"type": "integer"}},
                    ["jql"],
                ),
                caps=["jira", "read"],
            )
        )

        def jira_get_issue(issue_key: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "jira", "base_url", "email", "api_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_it._atlassian_base(profile)}/rest/api/3/issue/{issue_key}",
                auth=_it._basic_auth(profile["email"], profile["api_token"]),
            )

        jira_get_issue.__name__ = "jira_get_issue"
        tools.append(
            _it._attach(
                jira_get_issue,
                _it._schema(
                    "jira_get_issue",
                    "Read a Jira issue.",
                    {"issue_key": {"type": "string"}},
                    ["issue_key"],
                ),
                caps=["jira", "read"],
            )
        )

        def jira_create_issue(
            project_key: str, issue_type: str, summary: str, description: str = ""
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "jira", "base_url", "email", "api_token")
            if err:
                return err
            payload = {
                "fields": {
                    "project": {"key": project_key},
                    "issuetype": {"name": issue_type},
                    "summary": summary,
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": description or summary}
                                ],
                            }
                        ],
                    },
                }
            }
            return _it._request(
                "POST",
                f"{_it._atlassian_base(profile)}/rest/api/3/issue",
                auth=_it._basic_auth(profile["email"], profile["api_token"]),
                json=payload,
            )

        jira_create_issue.__name__ = "jira_create_issue"
        tools.append(
            _it._attach(
                jira_create_issue,
                _it._schema(
                    "jira_create_issue",
                    "Create a Jira issue. Requires user approval.",
                    {
                        "project_key": {"type": "string"},
                        "issue_type": {"type": "string"},
                        "summary": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    ["project_key", "issue_type", "summary"],
                ),
                approval=True,
                caps=["jira", "write"],
            )
        )

        def confluence_search(query: str, max_results: int = 10) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "confluence", "base_url", "email", "api_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_it._atlassian_base(profile)}/wiki/rest/api/search",
                auth=_it._basic_auth(profile["email"], profile["api_token"]),
                params={
                    "cql": f'text ~ "{query}"',
                    "limit": max(1, min(int(max_results or 10), 20)),
                },
            )

        confluence_search.__name__ = "confluence_search"
        tools.append(
            _it._attach(
                confluence_search,
                _it._schema(
                    "confluence_search",
                    "Search Confluence pages.",
                    {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                    ["query"],
                ),
                caps=["confluence", "read"],
            )
        )

        def confluence_get_page(page_id: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "confluence", "base_url", "email", "api_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_it._atlassian_base(profile)}/wiki/rest/api/content/{page_id}",
                auth=_it._basic_auth(profile["email"], profile["api_token"]),
                params={"expand": "body.storage,version,space"},
            )

        confluence_get_page.__name__ = "confluence_get_page"
        tools.append(
            _it._attach(
                confluence_get_page,
                _it._schema(
                    "confluence_get_page",
                    "Read a Confluence page.",
                    {"page_id": {"type": "string"}},
                    ["page_id"],
                ),
                caps=["confluence", "read"],
            )
        )

        def confluence_create_page(
            space_key: str, title: str, body: str, parent_id: str = ""
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "confluence", "base_url", "email", "api_token")
            if err:
                return err
            payload: dict[str, Any] = {
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "body": {"storage": {"value": body, "representation": "storage"}},
            }
            if parent_id:
                payload["ancestors"] = [{"id": parent_id}]
            return _it._request(
                "POST",
                f"{_it._atlassian_base(profile)}/wiki/rest/api/content",
                auth=_it._basic_auth(profile["email"], profile["api_token"]),
                json=payload,
            )

        confluence_create_page.__name__ = "confluence_create_page"
        tools.append(
            _it._attach(
                confluence_create_page,
                _it._schema(
                    "confluence_create_page",
                    "Create a Confluence page. Body should be Confluence storage-format HTML. Requires user approval.",
                    {
                        "space_key": {"type": "string"},
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "parent_id": {"type": "string"},
                    },
                    ["space_key", "title", "body"],
                ),
                approval=True,
                caps=["confluence", "write"],
            )
        )

        def zendesk_search(query: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "zendesk", "subdomain", "email", "api_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"https://{profile['subdomain']}.zendesk.com/api/v2/search.json",
                auth=_it._basic_auth(f"{profile['email']}/token", profile["api_token"]),
                params={"query": query},
            )

        zendesk_search.__name__ = "zendesk_search"
        tools.append(
            _it._attach(
                zendesk_search,
                _it._schema(
                    "zendesk_search",
                    "Search Zendesk tickets/users/articles.",
                    {"query": {"type": "string"}},
                    ["query"],
                ),
                caps=["zendesk", "read"],
            )
        )

        def zendesk_get_ticket(ticket_id: int) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "zendesk", "subdomain", "email", "api_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"https://{profile['subdomain']}.zendesk.com/api/v2/tickets/{ticket_id}.json",
                auth=_it._basic_auth(f"{profile['email']}/token", profile["api_token"]),
            )

        zendesk_get_ticket.__name__ = "zendesk_get_ticket"
        tools.append(
            _it._attach(
                zendesk_get_ticket,
                _it._schema(
                    "zendesk_get_ticket",
                    "Read a Zendesk ticket.",
                    {"ticket_id": {"type": "integer"}},
                    ["ticket_id"],
                ),
                caps=["zendesk", "read"],
            )
        )

        def zendesk_create_ticket(
            subject: str, body: str, requester_email: str = ""
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "zendesk", "subdomain", "email", "api_token")
            if err:
                return err
            ticket: dict[str, Any] = {"subject": subject, "comment": {"body": body}}
            if requester_email:
                ticket["requester"] = {"email": requester_email}
            return _it._request(
                "POST",
                f"https://{profile['subdomain']}.zendesk.com/api/v2/tickets.json",
                auth=_it._basic_auth(f"{profile['email']}/token", profile["api_token"]),
                json={"ticket": ticket},
            )

        zendesk_create_ticket.__name__ = "zendesk_create_ticket"
        tools.append(
            _it._attach(
                zendesk_create_ticket,
                _it._schema(
                    "zendesk_create_ticket",
                    "Create a Zendesk ticket. Requires user approval.",
                    {
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "requester_email": {"type": "string"},
                    },
                    ["subject", "body"],
                ),
                approval=True,
                caps=["zendesk", "write"],
            )
        )

        def linear_search_issues(query: str, max_results: int = 10) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "linear", "api_key")
            if err:
                return err
            gql = (
                "query($term: String!, $first: Int!) {"
                " searchIssues(term: $term, first: $first) {"
                " nodes { identifier title url state { name } assignee { name } } } }"
            )
            return _it._linear_gql(
                profile["api_key"], gql, {"term": query, "first": _it._clamp(max_results)}
            )

        linear_search_issues.__name__ = "linear_search_issues"
        tools.append(
            _it._attach(
                linear_search_issues,
                _it._schema(
                    "linear_search_issues",
                    "Search Linear issues by text.",
                    {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                    ["query"],
                ),
                caps=["linear", "read"],
            )
        )

        def linear_get_issue(issue_id: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "linear", "api_key")
            if err:
                return err
            gql = (
                "query($id: String!) { issue(id: $id) {"
                " identifier title description url state { name } assignee { name }"
                " comments { nodes { body user { name } } } } }"
            )
            return _it._linear_gql(profile["api_key"], gql, {"id": issue_id})

        linear_get_issue.__name__ = "linear_get_issue"
        tools.append(
            _it._attach(
                linear_get_issue,
                _it._schema(
                    "linear_get_issue",
                    "Read a Linear issue (with comments) by ID or key like ENG-123.",
                    {"issue_id": {"type": "string"}},
                    ["issue_id"],
                ),
                caps=["linear", "read"],
            )
        )

        def linear_list_teams() -> dict[str, Any]:
            profile, err = _it._profile(secrets, "linear", "api_key")
            if err:
                return err
            return _it._linear_gql(
                profile["api_key"], "{ teams { nodes { id key name } } }", {}
            )

        linear_list_teams.__name__ = "linear_list_teams"
        tools.append(
            _it._attach(
                linear_list_teams,
                _it._schema(
                    "linear_list_teams",
                    "List Linear teams (IDs are needed to create issues).",
                    {},
                    [],
                ),
                caps=["linear", "read"],
            )
        )

        def linear_create_issue(
            team_id: str, title: str, description: str = ""
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "linear", "api_key")
            if err:
                return err
            gql = (
                "mutation($input: IssueCreateInput!) { issueCreate(input: $input) {"
                " success issue { identifier url } } }"
            )
            return _it._linear_gql(
                profile["api_key"],
                gql,
                {"input": {"teamId": team_id, "title": title, "description": description}},
            )

        linear_create_issue.__name__ = "linear_create_issue"
        tools.append(
            _it._attach(
                linear_create_issue,
                _it._schema(
                    "linear_create_issue",
                    "Create a Linear issue. Get team_id from linear_list_teams. Requires user approval.",
                    {
                        "team_id": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    ["team_id", "title"],
                ),
                approval=True,
                caps=["linear", "write"],
            )
        )
