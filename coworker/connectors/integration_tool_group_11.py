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
        # --- Docusign -----------------------------------------------------------

        def _docusign_ctx(
            profile: dict[str, Any],
        ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, str]]]:
            """Return {token, base} — discovering and caching account_id + base_uri
            from the OAuth userinfo endpoint on first use."""
            token = str(profile.get("access_token", ""))
            account_id = profile.get("account_id")
            base_uri = profile.get("base_uri")
            if not (account_id and base_uri):
                info = _it._request(
                    "GET",
                    "https://account.docusign.com/oauth/userinfo",
                    headers=_it._bearer_headers(token),
                )
                if not info.get("ok"):
                    return None, {
                        "error": "docusign account discovery failed",
                        "details": str(info.get("details") or info.get("error")),
                    }
                accounts = (info.get("data") or {}).get("accounts") or []
                chosen = next(
                    (a for a in accounts if a.get("is_default")),
                    accounts[0] if accounts else None,
                )
                if not chosen:
                    return None, {"error": "docusign token has no accounts"}
                account_id = chosen.get("account_id")
                base_uri = chosen.get("base_uri")
                secrets.put(
                    "docusign:default",
                    {**profile, "account_id": account_id, "base_uri": base_uri},
                )
            return {
                "token": token,
                "base": f"{str(base_uri).rstrip('/')}/restapi/v2.1/accounts/{account_id}",
            }, None

        def docusign_list_envelopes(
            status: str = "", since_days: int = 30
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "docusign", "access_token")
            if err:
                return err
            ctx, err = _docusign_ctx(profile)
            if err:
                return err
            from datetime import datetime, timedelta, timezone

            params: dict[str, Any] = {
                "from_date": (
                    datetime.now(timezone.utc) - timedelta(days=max(1, int(since_days)))
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            }
            if status:
                params["status"] = status
            return _it._request(
                "GET",
                f"{ctx['base']}/envelopes",
                headers=_it._bearer_headers(ctx["token"]),
                params=params,
            )

        docusign_list_envelopes.__name__ = "docusign_list_envelopes"
        tools.append(
            _it._attach(
                docusign_list_envelopes,
                _it._schema(
                    "docusign_list_envelopes",
                    "List recent Docusign envelopes, optionally by status (sent/delivered/completed/declined/voided).",
                    {"status": {"type": "string"}, "since_days": {"type": "integer"}},
                    [],
                ),
                caps=["docusign", "read"],
            )
        )

        def docusign_get_envelope(envelope_id: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "docusign", "access_token")
            if err:
                return err
            ctx, err = _docusign_ctx(profile)
            if err:
                return err
            return _it._request(
                "GET",
                f"{ctx['base']}/envelopes/{quote(envelope_id)}",
                headers=_it._bearer_headers(ctx["token"]),
                params={"include": "recipients"},
            )

        docusign_get_envelope.__name__ = "docusign_get_envelope"
        tools.append(
            _it._attach(
                docusign_get_envelope,
                _it._schema(
                    "docusign_get_envelope",
                    "Read a Docusign envelope's status and per-signer progress.",
                    {"envelope_id": {"type": "string"}},
                    ["envelope_id"],
                ),
                caps=["docusign", "read"],
            )
        )

        def docusign_list_templates(max_results: int = 10) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "docusign", "access_token")
            if err:
                return err
            ctx, err = _docusign_ctx(profile)
            if err:
                return err
            return _it._request(
                "GET",
                f"{ctx['base']}/templates",
                headers=_it._bearer_headers(ctx["token"]),
                params={"count": _it._clamp(max_results)},
            )

        docusign_list_templates.__name__ = "docusign_list_templates"
        tools.append(
            _it._attach(
                docusign_list_templates,
                _it._schema(
                    "docusign_list_templates",
                    "List Docusign templates (template ids are needed to send).",
                    {"max_results": {"type": "integer"}},
                    [],
                ),
                caps=["docusign", "read"],
            )
        )

        def docusign_send_from_template(
            template_id: str,
            recipient_email: str,
            recipient_name: str,
            role_name: str = "Signer",
            subject: str = "",
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "docusign", "access_token")
            if err:
                return err
            ctx, err = _docusign_ctx(profile)
            if err:
                return err
            body: dict[str, Any] = {
                "templateId": template_id,
                "templateRoles": [
                    {
                        "email": recipient_email,
                        "name": recipient_name,
                        "roleName": role_name,
                    }
                ],
                "status": "sent",
            }
            if subject:
                body["emailSubject"] = subject
            return _it._request(
                "POST",
                f"{ctx['base']}/envelopes",
                headers=_it._bearer_headers(ctx["token"]),
                json=body,
            )

        docusign_send_from_template.__name__ = "docusign_send_from_template"
        tools.append(
            _it._attach(
                docusign_send_from_template,
                _it._schema(
                    "docusign_send_from_template",
                    "Send a Docusign template to one signer for signature. Requires user approval.",
                    {
                        "template_id": {"type": "string"},
                        "recipient_email": {"type": "string"},
                        "recipient_name": {"type": "string"},
                        "role_name": {"type": "string"},
                        "subject": {"type": "string"},
                    },
                    ["template_id", "recipient_email", "recipient_name"],
                ),
                approval=True,
                caps=["docusign", "write"],
            )
        )

        # --- Canva --------------------------------------------------------------

        _CANVA = "https://api.canva.com/rest/v1"

        def canva_list_designs(query: str = "", max_results: int = 10) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "canva", "access_token")
            if err:
                return err
            params: dict[str, Any] = {"limit": _it._clamp(max_results)}
            if query:
                params["query"] = query
            return _it._request(
                "GET",
                f"{_CANVA}/designs",
                headers=_it._bearer_headers(profile["access_token"]),
                params=params,
            )

        canva_list_designs.__name__ = "canva_list_designs"
        tools.append(
            _it._attach(
                canva_list_designs,
                _it._schema(
                    "canva_list_designs",
                    "List (or text-search) Canva designs.",
                    {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                    [],
                ),
                caps=["canva", "read"],
            )
        )

        def canva_get_design(design_id: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "canva", "access_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_CANVA}/designs/{quote(design_id)}",
                headers=_it._bearer_headers(profile["access_token"]),
            )

        canva_get_design.__name__ = "canva_get_design"
        tools.append(
            _it._attach(
                canva_get_design,
                _it._schema(
                    "canva_get_design",
                    "Read a Canva design's metadata (title, pages, urls).",
                    {"design_id": {"type": "string"}},
                    ["design_id"],
                ),
                caps=["canva", "read"],
            )
        )

        def canva_export_design(design_id: str, format: str = "pdf") -> dict[str, Any]:
            profile, err = _it._profile(secrets, "canva", "access_token")
            if err:
                return err
            return _it._request(
                "POST",
                f"{_CANVA}/exports",
                headers=_it._bearer_headers(profile["access_token"]),
                json={"design_id": design_id, "format": {"type": format}},
            )

        canva_export_design.__name__ = "canva_export_design"
        tools.append(
            _it._attach(
                canva_export_design,
                _it._schema(
                    "canva_export_design",
                    "Start rendering a Canva design to pdf/png/jpg; returns an export job to poll.",
                    {"design_id": {"type": "string"}, "format": {"type": "string"}},
                    ["design_id"],
                ),
                caps=["canva", "read"],
            )
        )

        def canva_get_export(export_id: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "canva", "access_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_CANVA}/exports/{quote(export_id)}",
                headers=_it._bearer_headers(profile["access_token"]),
            )

        canva_get_export.__name__ = "canva_get_export"
        tools.append(
            _it._attach(
                canva_get_export,
                _it._schema(
                    "canva_get_export",
                    "Check a Canva export job; returns download URLs when finished.",
                    {"export_id": {"type": "string"}},
                    ["export_id"],
                ),
                caps=["canva", "read"],
            )
        )
