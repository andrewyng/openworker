"""Bounded first-party integration tool builder partition."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional
from urllib.parse import quote

from ..secrets import SecretStore
from . import integration_tools as _it


def _dropbox_path(path: str) -> str:
    normalized = path.strip()
    if normalized and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def add_tools(
    secrets: SecretStore,
    roots: Optional[list[Any]],
    tools: list[Callable[..., Any]],
) -> None:
        def dropbox_search(query: str, max_results: int = 10) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "dropbox", "access_token")
            if err:
                return err
            return _it._request(
                "POST",
                "https://api.dropboxapi.com/2/files/search_v2",
                headers=_it._bearer_headers(profile["access_token"]),
                json={"query": query, "options": {"max_results": _it._clamp(max_results)}},
            )

        dropbox_search.__name__ = "dropbox_search"
        tools.append(
            _it._attach(
                dropbox_search,
                _it._schema(
                    "dropbox_search",
                    "Search Dropbox files and folders by name/content.",
                    {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                    ["query"],
                ),
                caps=["dropbox", "read"],
            )
        )

        def dropbox_list_folder(path: str = "") -> dict[str, Any]:
            profile, err = _it._profile(secrets, "dropbox", "access_token")
            if err:
                return err
            return _it._request(
                "POST",
                "https://api.dropboxapi.com/2/files/list_folder",
                headers=_it._bearer_headers(profile["access_token"]),
                json={"path": _dropbox_path(path)},
            )

        dropbox_list_folder.__name__ = "dropbox_list_folder"
        tools.append(
            _it._attach(
                dropbox_list_folder,
                _it._schema(
                    "dropbox_list_folder",
                    "List a Dropbox folder. Empty path is the root.",
                    {"path": {"type": "string"}},
                    [],
                ),
                caps=["dropbox", "read"],
            )
        )

        def dropbox_read_file(path: str, max_chars: int = 20000) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "dropbox", "access_token")
            if err:
                return err
            out = _it._request(
                "POST",
                "https://content.dropboxapi.com/2/files/download",
                headers={
                    "Authorization": f"Bearer {profile['access_token']}",
                    "Dropbox-API-Arg": json.dumps({"path": _dropbox_path(path)}),
                },
            )
            if "error" in out:
                return out
            text = out["data"] if isinstance(out["data"], str) else str(out["data"])
            cap = max(1, min(int(max_chars or 20000), 100000))
            return {"path": path, "text": text[:cap], "truncated": len(text) > cap}

        dropbox_read_file.__name__ = "dropbox_read_file"
        tools.append(
            _it._attach(
                dropbox_read_file,
                _it._schema(
                    "dropbox_read_file",
                    "Read a text file from Dropbox by path.",
                    {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
                    ["path"],
                ),
                caps=["dropbox", "read"],
            )
        )

        def box_search(query: str, max_results: int = 10) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "box", "access_token")
            if err:
                return err
            return _it._request(
                "GET",
                "https://api.box.com/2.0/search",
                headers=_it._bearer_headers(profile["access_token"]),
                params={"query": query, "limit": _it._clamp(max_results)},
            )

        box_search.__name__ = "box_search"
        tools.append(
            _it._attach(
                box_search,
                _it._schema(
                    "box_search",
                    "Search Box files and folders.",
                    {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                    ["query"],
                ),
                caps=["box", "read"],
            )
        )

        def box_list_folder(folder_id: str = "0") -> dict[str, Any]:
            profile, err = _it._profile(secrets, "box", "access_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"https://api.box.com/2.0/folders/{folder_id}/items",
                headers=_it._bearer_headers(profile["access_token"]),
            )

        box_list_folder.__name__ = "box_list_folder"
        tools.append(
            _it._attach(
                box_list_folder,
                _it._schema(
                    "box_list_folder",
                    "List items in a Box folder. Folder '0' is the root.",
                    {"folder_id": {"type": "string"}},
                    [],
                ),
                caps=["box", "read"],
            )
        )

        def box_read_file(file_id: str, max_chars: int = 20000) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "box", "access_token")
            if err:
                return err
            out = _it._request(
                "GET",
                f"https://api.box.com/2.0/files/{file_id}/content",
                headers=_it._bearer_headers(profile["access_token"]),
            )
            if "error" in out:
                return out
            text = out["data"] if isinstance(out["data"], str) else str(out["data"])
            cap = max(1, min(int(max_chars or 20000), 100000))
            return {"file_id": file_id, "text": text[:cap], "truncated": len(text) > cap}

        box_read_file.__name__ = "box_read_file"
        tools.append(
            _it._attach(
                box_read_file,
                _it._schema(
                    "box_read_file",
                    "Read a text file from Box by file ID.",
                    {"file_id": {"type": "string"}, "max_chars": {"type": "integer"}},
                    ["file_id"],
                ),
                caps=["box", "read"],
            )
        )

        def quickbooks_query(query: str, max_results: int = 10) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "quickbooks", "access_token", "realm_id")
            if err:
                return err
            q = query.strip()
            if "maxresults" not in q.lower():
                q = f"{q} MAXRESULTS {_it._clamp(max_results, ceiling=100)}"
            return _it._request(
                "GET",
                f"{_it._qbo_base(profile)}/query",
                headers=_it._bearer_headers(profile["access_token"]),
                params={"query": q},
            )

        quickbooks_query.__name__ = "quickbooks_query"
        tools.append(
            _it._attach(
                quickbooks_query,
                _it._schema(
                    "quickbooks_query",
                    "Run a QuickBooks Online query, e.g. \"SELECT * FROM Invoice WHERE TotalAmt > '100'\". "
                    "Entities include Customer, Invoice, Bill, Payment, Account, Vendor.",
                    {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                    ["query"],
                ),
                caps=["quickbooks", "read"],
            )
        )

        def quickbooks_list_customers(max_results: int = 10) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "quickbooks", "access_token", "realm_id")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_it._qbo_base(profile)}/query",
                headers=_it._bearer_headers(profile["access_token"]),
                params={
                    "query": f"SELECT * FROM Customer MAXRESULTS {_it._clamp(max_results)}"
                },
            )

        quickbooks_list_customers.__name__ = "quickbooks_list_customers"
        tools.append(
            _it._attach(
                quickbooks_list_customers,
                _it._schema(
                    "quickbooks_list_customers",
                    "List QuickBooks customers.",
                    {"max_results": {"type": "integer"}},
                    [],
                ),
                caps=["quickbooks", "read"],
            )
        )

        def quickbooks_list_invoices(max_results: int = 10) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "quickbooks", "access_token", "realm_id")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_it._qbo_base(profile)}/query",
                headers=_it._bearer_headers(profile["access_token"]),
                params={
                    "query": "SELECT * FROM Invoice ORDERBY TxnDate DESC "
                    f"MAXRESULTS {_it._clamp(max_results)}"
                },
            )

        quickbooks_list_invoices.__name__ = "quickbooks_list_invoices"
        tools.append(
            _it._attach(
                quickbooks_list_invoices,
                _it._schema(
                    "quickbooks_list_invoices",
                    "List recent QuickBooks invoices.",
                    {"max_results": {"type": "integer"}},
                    [],
                ),
                caps=["quickbooks", "read"],
            )
        )

        def quickbooks_get_report(
            report: str, start_date: str = "", end_date: str = ""
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "quickbooks", "access_token", "realm_id")
            if err:
                return err
            params: dict[str, Any] = {}
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            return _it._request(
                "GET",
                f"{_it._qbo_base(profile)}/reports/{quote(report, safe='')}",
                headers=_it._bearer_headers(profile["access_token"]),
                params=params or None,
            )

        quickbooks_get_report.__name__ = "quickbooks_get_report"
        tools.append(
            _it._attach(
                quickbooks_get_report,
                _it._schema(
                    "quickbooks_get_report",
                    "Run a QuickBooks report such as ProfitAndLoss, BalanceSheet, CashFlow, "
                    "AgedReceivables. Dates are YYYY-MM-DD.",
                    {
                        "report": {"type": "string"},
                        "start_date": {"type": "string"},
                        "end_date": {"type": "string"},
                    },
                    ["report"],
                ),
                caps=["quickbooks", "read"],
            )
        )

        def whatsapp_send_message(to: str, text: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "whatsapp", "access_token", "phone_number_id")
            if err:
                return err
            return _it._request(
                "POST",
                f"https://graph.facebook.com/v21.0/{profile['phone_number_id']}/messages",
                headers=_it._bearer_headers(profile["access_token"]),
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": text[:4096]},
                },
            )

        whatsapp_send_message.__name__ = "whatsapp_send_message"
        tools.append(
            _it._attach(
                whatsapp_send_message,
                _it._schema(
                    "whatsapp_send_message",
                    "Send a WhatsApp text message. Only delivered if the recipient messaged "
                    "this number within the last 24 hours; otherwise use "
                    "whatsapp_send_template. Requires user approval.",
                    {"to": {"type": "string"}, "text": {"type": "string"}},
                    ["to", "text"],
                ),
                approval=True,
                caps=["whatsapp", "write"],
            )
        )

        def whatsapp_send_template(
            to: str, template_name: str, language_code: str = "en_US"
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "whatsapp", "access_token", "phone_number_id")
            if err:
                return err
            return _it._request(
                "POST",
                f"https://graph.facebook.com/v21.0/{profile['phone_number_id']}/messages",
                headers=_it._bearer_headers(profile["access_token"]),
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "template",
                    "template": {
                        "name": template_name,
                        "language": {"code": language_code},
                    },
                },
            )

        whatsapp_send_template.__name__ = "whatsapp_send_template"
        tools.append(
            _it._attach(
                whatsapp_send_template,
                _it._schema(
                    "whatsapp_send_template",
                    "Send a pre-approved WhatsApp template message (works outside the "
                    "24-hour service window). Requires user approval.",
                    {
                        "to": {"type": "string"},
                        "template_name": {"type": "string"},
                        "language_code": {"type": "string"},
                    },
                    ["to", "template_name"],
                ),
                approval=True,
                caps=["whatsapp", "write"],
            )
        )
