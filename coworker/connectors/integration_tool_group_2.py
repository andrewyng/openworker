"""Bounded first-party integration tool builder partition."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any, Callable, Optional

from ..secrets import SecretStore
from . import integration_tools as _it


def add_tools(
    secrets: SecretStore,
    roots: Optional[list[Any]],
    tools: list[Callable[..., Any]],
) -> None:
        def gmail_search_messages(
            query: str, max_results: int = 10, account: str = ""
        ) -> dict[str, Any]:
            email, profile, err = _it._gmail_profile(secrets, account)
            if err:
                return err
            token = profile["access_token"]
            result = _it._request(
                "GET",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers=_it._google_headers(token),
                params={"q": query, "maxResults": max(1, min(int(max_results or 10), 20))},
            )
            filters = _it._gmail_filters(secrets)
            if result.get("ok") and filters:
                # Enforce "Never show agents" HERE, silently: matching hits are
                # omitted (no tombstone); the count rides the `_display` sidecar for
                # the user's tool card + audit — never the agent-visible content.
                data = dict(result.get("data") or {})
                label_map = _it._gmail_label_map(token) if filters["labels"] else {}
                kept, hidden = [], 0
                for m in data.get("messages") or []:
                    meta = _it._request(
                        "GET",
                        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m.get('id')}",
                        headers=_it._google_headers(token),
                        params={"format": "metadata", "metadataHeaders": "From"},
                    )
                    detail = meta.get("data") if meta.get("ok") else None
                    # Fail-open on a metadata miss: ids alone reveal nothing, and
                    # gmail_get_message re-enforces before any content flows.
                    if isinstance(detail, dict) and _it._gmail_is_hidden(
                        detail, filters, label_map
                    ):
                        hidden += 1
                    else:
                        kept.append(m)
                if hidden:
                    data["messages"] = kept
                    if isinstance(data.get("resultSizeEstimate"), int):
                        data["resultSizeEstimate"] = max(
                            0, data["resultSizeEstimate"] - hidden
                        )
                    result = {
                        "ok": True,
                        "data": data,
                        "_display": {"hidden_by_filters": hidden, "connector": "gmail"},
                    }
            if result.get("ok"):
                result["account"] = email
            return result

        gmail_search_messages.__name__ = "gmail_search_messages"
        tools.append(
            _it._attach(
                gmail_search_messages,
                _it._schema(
                    "gmail_search_messages",
                    "Search Gmail messages using Gmail query syntax.",
                    {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "account": _it._ACCOUNT_PROP,
                    },
                    ["query"],
                ),
                caps=["gmail", "read"],
            )
        )

        def gmail_get_message(message_id: str, account: str = "") -> dict[str, Any]:
            email, profile, err = _it._gmail_profile(secrets, account)
            if err:
                return err
            token = profile["access_token"]
            result = _it._request(
                "GET",
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                headers=_it._google_headers(token),
                params={"format": "full"},
            )
            filters = _it._gmail_filters(secrets)
            if result.get("ok") and filters:
                data = result.get("data") or {}
                label_map = _it._gmail_label_map(token) if filters["labels"] else {}
                if isinstance(data, dict) and _it._gmail_is_hidden(data, filters, label_map):
                    # Indistinguishable from a real miss — the agent must not be able
                    # to tell "filtered" from "gone" (a tombstone invites probing).
                    return {
                        "error": "HTTP 404",
                        "details": {"error": {"code": 404, "message": "Not Found"}},
                        "_display": {"hidden_by_filters": 1, "connector": "gmail"},
                    }
            if result.get("ok"):
                result["account"] = email
            return result

        gmail_get_message.__name__ = "gmail_get_message"
        tools.append(
            _it._attach(
                gmail_get_message,
                _it._schema(
                    "gmail_get_message",
                    "Read a Gmail message by ID.",
                    {"message_id": {"type": "string"}, "account": _it._ACCOUNT_PROP},
                    ["message_id"],
                ),
                caps=["gmail", "read"],
            )
        )

        def gmail_send_email(
            to: str, subject: str, body: str, cc: str = "", account: str = ""
        ) -> dict[str, Any]:
            email, profile, err = _it._gmail_profile(secrets, account)
            if err:
                return err
            msg = EmailMessage()
            msg["To"], msg["Subject"] = to, subject
            if cc:
                msg["Cc"] = cc
            msg.set_content(body)
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
            result = _it._request(
                "POST",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers=_it._google_headers(profile["access_token"]),
                json={"raw": raw},
            )
            if result.get("ok"):
                result["account"] = email
            return result

        gmail_send_email.__name__ = "gmail_send_email"
        tools.append(
            _it._attach(
                gmail_send_email,
                _it._schema(
                    "gmail_send_email",
                    "Send an email through Gmail. Requires user approval; the "
                    "`account` argument names the sending mailbox on the approval card.",
                    {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "cc": {"type": "string"},
                        "account": _it._ACCOUNT_PROP,
                    },
                    ["to", "subject", "body"],
                ),
                approval=True,
                caps=["gmail", "write"],
            )
        )

        _CAL_ACCOUNT_PROP = {
            "type": "string",
            "description": "Google account email to use; omit for the default account.",
        }

        def _gcal_result(email: str, result: dict[str, Any]) -> dict[str, Any]:
            # Name the account on every success so approvals/transcripts say whose
            # calendar was touched (same contract as the gmail tools).
            if result.get("ok"):
                result["account"] = email
            return result

        def gcal_list_events(
            calendar_id: str = "primary",
            time_min: str = "",
            time_max: str = "",
            max_results: int = 10,
            account: str = "",
        ) -> dict[str, Any]:
            email, profile, err = _it._gcal_profile(secrets, account)
            if err:
                return err
            params: dict[str, Any] = {
                "singleEvents": True,
                "orderBy": "startTime",
                "maxResults": max(1, min(int(max_results or 10), 20)),
            }
            if time_min:
                params["timeMin"] = time_min
            if time_max:
                params["timeMax"] = time_max
            return _gcal_result(
                email,
                _it._request(
                    "GET",
                    f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                    headers=_it._google_headers(profile["access_token"]),
                    params=params,
                ),
            )

        gcal_list_events.__name__ = "gcal_list_events"
        tools.append(
            _it._attach(
                gcal_list_events,
                _it._schema(
                    "gcal_list_events",
                    "List Google Calendar events. time_min/time_max should be RFC3339 timestamps when provided.",
                    {
                        "calendar_id": {"type": "string"},
                        "time_min": {"type": "string"},
                        "time_max": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "account": _CAL_ACCOUNT_PROP,
                    },
                    [],
                ),
                caps=["calendar", "read"],
            )
        )

        def gcal_free_busy(
            time_min: str,
            time_max: str,
            calendars: str = "primary",
            timezone: str = "UTC",
            account: str = "",
        ) -> dict[str, Any]:
            email, profile, err = _it._gcal_profile(secrets, account)
            if err:
                return err
            items = [
                {"id": c.strip()}
                for c in str(calendars or "primary").split(",")
                if c.strip()
            ]
            return _gcal_result(
                email,
                _it._request(
                    "POST",
                    "https://www.googleapis.com/calendar/v3/freeBusy",
                    headers=_it._google_headers(profile["access_token"]),
                    json={
                        "timeMin": time_min,
                        "timeMax": time_max,
                        "timeZone": timezone,
                        "items": items,
                    },
                ),
            )

        gcal_free_busy.__name__ = "gcal_free_busy"
        tools.append(
            _it._attach(
                gcal_free_busy,
                _it._schema(
                    "gcal_free_busy",
                    "Look up busy intervals (availability) for one or more calendars. "
                    "time_min/time_max are RFC3339 timestamps; calendars is a comma-separated list of calendar ids.",
                    {
                        "time_min": {"type": "string"},
                        "time_max": {"type": "string"},
                        "calendars": {"type": "string"},
                        "timezone": {"type": "string"},
                        "account": _CAL_ACCOUNT_PROP,
                    },
                    ["time_min", "time_max"],
                ),
                caps=["calendar", "read"],
            )
        )

        def gcal_create_event(
            summary: str,
            start: str,
            end: str,
            calendar_id: str = "primary",
            timezone: str = "UTC",
            description: str = "",
            account: str = "",
        ) -> dict[str, Any]:
            email, profile, err = _it._gcal_profile(secrets, account)
            if err:
                return err
            payload = {
                "summary": summary,
                "description": description,
                "start": {"dateTime": start, "timeZone": timezone},
                "end": {"dateTime": end, "timeZone": timezone},
            }
            return _gcal_result(
                email,
                _it._request(
                    "POST",
                    f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                    headers=_it._google_headers(profile["access_token"]),
                    json=payload,
                ),
            )

        gcal_create_event.__name__ = "gcal_create_event"
        tools.append(
            _it._attach(
                gcal_create_event,
                _it._schema(
                    "gcal_create_event",
                    "Create a Google Calendar event. Requires user approval.",
                    {
                        "summary": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "calendar_id": {"type": "string"},
                        "timezone": {"type": "string"},
                        "description": {"type": "string"},
                        "account": _CAL_ACCOUNT_PROP,
                    },
                    ["summary", "start", "end"],
                ),
                approval=True,
                caps=["calendar", "write"],
            )
        )

        def gcal_update_event(
            event_id: str,
            calendar_id: str = "primary",
            summary: str = "",
            start: str = "",
            end: str = "",
            timezone: str = "UTC",
            description: str = "",
            account: str = "",
        ) -> dict[str, Any]:
            email, profile, err = _it._gcal_profile(secrets, account)
            if err:
                return err
            # PATCH semantics: only the provided fields change.
            payload: dict[str, Any] = {}
            if summary:
                payload["summary"] = summary
            if description:
                payload["description"] = description
            if start:
                payload["start"] = {"dateTime": start, "timeZone": timezone}
            if end:
                payload["end"] = {"dateTime": end, "timeZone": timezone}
            if not payload:
                return {
                    "error": "nothing to update — pass summary, description, start, or end"
                }
            return _gcal_result(
                email,
                _it._request(
                    "PATCH",
                    f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
                    headers=_it._google_headers(profile["access_token"]),
                    json=payload,
                ),
            )

        gcal_update_event.__name__ = "gcal_update_event"
        tools.append(
            _it._attach(
                gcal_update_event,
                _it._schema(
                    "gcal_update_event",
                    "Update fields of a Google Calendar event (only the provided fields change). Requires user approval.",
                    {
                        "event_id": {"type": "string"},
                        "calendar_id": {"type": "string"},
                        "summary": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "timezone": {"type": "string"},
                        "description": {"type": "string"},
                        "account": _CAL_ACCOUNT_PROP,
                    },
                    ["event_id"],
                ),
                approval=True,
                caps=["calendar", "write"],
            )
        )

        def gcal_delete_event(
            event_id: str, calendar_id: str = "primary", account: str = ""
        ) -> dict[str, Any]:
            email, profile, err = _it._gcal_profile(secrets, account)
            if err:
                return err
            return _gcal_result(
                email,
                _it._request(
                    "DELETE",
                    f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
                    headers=_it._google_headers(profile["access_token"]),
                ),
            )

        gcal_delete_event.__name__ = "gcal_delete_event"
        tools.append(
            _it._attach(
                gcal_delete_event,
                _it._schema(
                    "gcal_delete_event",
                    "Delete a Google Calendar event. Requires user approval.",
                    {
                        "event_id": {"type": "string"},
                        "calendar_id": {"type": "string"},
                        "account": _CAL_ACCOUNT_PROP,
                    },
                    ["event_id"],
                ),
                approval=True,
                caps=["calendar", "write"],
            )
        )
