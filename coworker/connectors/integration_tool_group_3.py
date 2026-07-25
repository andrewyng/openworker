"""Bounded first-party integration tool builder partition."""

from __future__ import annotations

import datetime as _dt
from typing import Any, Callable, Optional
from urllib.parse import quote

from ..secrets import SecretStore
from . import integration_tools as _it


def add_tools(
    secrets: SecretStore,
    roots: Optional[list[Any]],
    tools: list[Callable[..., Any]],
) -> None:
        def outlook_search_messages(
            query: str = "", max_results: int = 10, account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "outlook", account, "access_token"
            )
            if err:
                return err
            params = {"$top": max(1, min(int(max_results or 10), 20))}
            if query:
                params["$search"] = f'"{query}"'
            return _it._acct_result(
                aid,
                _it._request(
                    "GET",
                    "https://graph.microsoft.com/v1.0/me/messages",
                    headers=_it._graph_headers(profile["access_token"]),
                    params=params,
                ),
            )

        outlook_search_messages.__name__ = "outlook_search_messages"
        tools.append(
            _it._attach(
                outlook_search_messages,
                _it._schema(
                    "outlook_search_messages",
                    "Search or list Outlook messages through Microsoft Graph.",
                    {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    [],
                ),
                caps=["outlook", "read"],
            )
        )

        def outlook_send_mail(
            to: str, subject: str, body: str, account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "outlook", account, "access_token"
            )
            if err:
                return err
            payload = {
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                }
            }
            return _it._acct_result(
                aid,
                _it._request(
                    "POST",
                    "https://graph.microsoft.com/v1.0/me/sendMail",
                    headers=_it._graph_headers(profile["access_token"]),
                    json=payload,
                ),
            )

        outlook_send_mail.__name__ = "outlook_send_mail"
        tools.append(
            _it._attach(
                outlook_send_mail,
                _it._schema(
                    "outlook_send_mail",
                    "Send mail through Outlook/Microsoft Graph. Requires user approval.",
                    {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["to", "subject", "body"],
                ),
                approval=True,
                caps=["outlook", "write"],
            )
        )

        def outlook_list_events(
            start: str = "", end: str = "", max_results: int = 10, account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "outlook", account, "access_token"
            )
            if err:
                return err
            # calendarView expands recurrences and takes a window; /me/events does
            # neither, so a bare call used to return arbitrary (often past) events.
            # Default window: now → +7 days.
            now = _dt.datetime.now(_dt.timezone.utc)
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            return _it._acct_result(
                aid,
                _it._request(
                    "GET",
                    "https://graph.microsoft.com/v1.0/me/calendarView",
                    headers=_it._graph_headers(profile["access_token"]),
                    params={
                        "startDateTime": start or now.strftime(fmt),
                        "endDateTime": end or (now + _dt.timedelta(days=7)).strftime(fmt),
                        "$orderby": "start/dateTime",
                        "$top": max(1, min(int(max_results or 10), 50)),
                    },
                ),
            )

        outlook_list_events.__name__ = "outlook_list_events"
        tools.append(
            _it._attach(
                outlook_list_events,
                _it._schema(
                    "outlook_list_events",
                    "List upcoming Outlook calendar events (recurrences expanded, ordered "
                    "by start). start/end are ISO timestamps; default window is the next "
                    "7 days.",
                    {
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    [],
                ),
                caps=["outlook", "read"],
            )
        )

        def outlook_create_event(
            subject: str,
            start: str,
            end: str,
            timezone: str = "UTC",
            body: str = "",
            attendees: str = "",
            location: str = "",
            teams_meeting: bool = False,
            account: str = "",
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "outlook", account, "access_token"
            )
            if err:
                return err
            payload: dict[str, Any] = {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "start": {"dateTime": start, "timeZone": timezone},
                "end": {"dateTime": end, "timeZone": timezone},
            }
            if attendees:
                payload["attendees"] = [
                    {"emailAddress": {"address": a.strip()}, "type": "required"}
                    for a in attendees.split(",")
                    if a.strip()
                ]
            if location:
                payload["location"] = {"displayName": location}
            if teams_meeting:
                payload["isOnlineMeeting"] = True
                payload["onlineMeetingProvider"] = "teamsForBusiness"
            return _it._acct_result(
                aid,
                _it._request(
                    "POST",
                    "https://graph.microsoft.com/v1.0/me/events",
                    headers=_it._graph_headers(profile["access_token"]),
                    json=payload,
                ),
            )

        outlook_create_event.__name__ = "outlook_create_event"
        tools.append(
            _it._attach(
                outlook_create_event,
                _it._schema(
                    "outlook_create_event",
                    "Create an Outlook calendar event; invites go to attendees "
                    "(comma-separated emails). teams_meeting adds a Teams link. "
                    "Requires user approval.",
                    {
                        "subject": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "timezone": {"type": "string"},
                        "body": {"type": "string"},
                        "attendees": {"type": "string"},
                        "location": {"type": "string"},
                        "teams_meeting": {"type": "boolean"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["subject", "start", "end"],
                ),
                approval=True,
                caps=["outlook", "write"],
            )
        )

        def outlook_update_event(
            event_id: str,
            subject: str = "",
            start: str = "",
            end: str = "",
            timezone: str = "UTC",
            body: str = "",
            location: str = "",
            account: str = "",
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "outlook", account, "access_token"
            )
            if err:
                return err
            # PATCH semantics: only the provided fields change.
            payload: dict[str, Any] = {}
            if subject:
                payload["subject"] = subject
            if body:
                payload["body"] = {"contentType": "Text", "content": body}
            if start:
                payload["start"] = {"dateTime": start, "timeZone": timezone}
            if end:
                payload["end"] = {"dateTime": end, "timeZone": timezone}
            if location:
                payload["location"] = {"displayName": location}
            return _it._acct_result(
                aid,
                _it._request(
                    "PATCH",
                    f"https://graph.microsoft.com/v1.0/me/events/{quote(event_id)}",
                    headers=_it._graph_headers(profile["access_token"]),
                    json=payload,
                ),
            )

        outlook_update_event.__name__ = "outlook_update_event"
        tools.append(
            _it._attach(
                outlook_update_event,
                _it._schema(
                    "outlook_update_event",
                    "Change fields of an existing Outlook calendar event (only the "
                    "provided fields change). Requires user approval.",
                    {
                        "event_id": {"type": "string"},
                        "subject": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "timezone": {"type": "string"},
                        "body": {"type": "string"},
                        "location": {"type": "string"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["event_id"],
                ),
                approval=True,
                caps=["outlook", "write"],
            )
        )

        def outlook_delete_event(event_id: str, account: str = "") -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "outlook", account, "access_token"
            )
            if err:
                return err
            return _it._acct_result(
                aid,
                _it._request(
                    "DELETE",
                    f"https://graph.microsoft.com/v1.0/me/events/{quote(event_id)}",
                    headers=_it._graph_headers(profile["access_token"]),
                ),
            )

        outlook_delete_event.__name__ = "outlook_delete_event"
        tools.append(
            _it._attach(
                outlook_delete_event,
                _it._schema(
                    "outlook_delete_event",
                    "Delete (cancel) an Outlook calendar event. Requires user approval.",
                    {"event_id": {"type": "string"}, "account": _it._GEN_ACCOUNT_PROP},
                    ["event_id"],
                ),
                approval=True,
                caps=["outlook", "write"],
            )
        )

        def outlook_respond_event(
            event_id: str, response: str, comment: str = "", account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "outlook", account, "access_token"
            )
            if err:
                return err
            actions = {
                "accept": "accept",
                "decline": "decline",
                "tentative": "tentativelyAccept",
            }
            action = actions.get((response or "").strip().lower())
            if not action:
                return {"error": "response must be one of: accept, decline, tentative"}
            return _it._acct_result(
                aid,
                _it._request(
                    "POST",
                    f"https://graph.microsoft.com/v1.0/me/events/{quote(event_id)}/{action}",
                    headers=_it._graph_headers(profile["access_token"]),
                    json={"comment": comment, "sendResponse": True},
                ),
            )

        outlook_respond_event.__name__ = "outlook_respond_event"
        tools.append(
            _it._attach(
                outlook_respond_event,
                _it._schema(
                    "outlook_respond_event",
                    "Respond to an Outlook meeting invite: accept, decline, or "
                    "tentative. The organizer is notified. Requires user approval.",
                    {
                        "event_id": {"type": "string"},
                        "response": {"type": "string"},
                        "comment": {"type": "string"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["event_id", "response"],
                ),
                approval=True,
                caps=["outlook", "write"],
            )
        )
