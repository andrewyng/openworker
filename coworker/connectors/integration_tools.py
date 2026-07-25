"""Cowork-only connector tools for first-party integrations.

These tools are intentionally local-first: credentials are read from the SecretStore at
execution time and never enter prompts. OAuth-managed setup can later replace the manual
access-token fields without changing the tool surface.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Callable, Optional

import aisuite as ai

from ..secrets import SecretStore
from .browser_automation import make_browser_automation_tools
from .email_tools import make_email_tools
from .tool_defs import approval_for_tool, connector_for_tool


def _meta(
    name: str, *, approval: bool = False, capabilities: Optional[list[str]] = None
):
    return ai.ToolMetadata(
        name=name,
        category="connector",
        risk_level="medium" if approval else "low",
        capabilities=capabilities or ["integration"],
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
    approval: bool = True,
    caps: Optional[list[str]] = None,
):
    name = schema["function"]["name"]
    # §36: the tool registry's read/write kind overrides the call-site flag for
    # registered tools — connector READS never gate. The explicit arg only governs
    # tools without a registry entry.
    approval = approval_for_tool(name, default=approval)
    fn.__coworker_schema__ = schema
    fn.__aisuite_tool_metadata__ = _meta(name, approval=approval, capabilities=caps)
    fn.__doc__ = schema["function"]["description"]
    return fn


def _profile(
    secrets: SecretStore, name: str, *keys: str
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, str]]]:
    profile = secrets.get(f"{name}:default") or {}
    if profile.get("managed"):
        # Managed-OAuth profiles renew through the cloud broker just before
        # expiry; manual token profiles are never touched (no-op inside).
        from ..cloud import ensure_fresh_connector_token
        from ..config import load_config

        ensure_fresh_connector_token(secrets, load_config(), name)
        profile = secrets.get(f"{name}:default") or {}
    missing = [k for k in keys if not profile.get(k)]
    if missing:
        return None, {"error": f"{name} is not connected; missing {', '.join(missing)}"}
    return profile, None


def _account_profile(
    secrets: SecretStore, connector: str, account: str = "", *keys: str
) -> tuple[str, Optional[dict[str, Any]], Optional[dict[str, str]]]:
    """(account_id, profile, err) for an account-patterned connector (generic
    accounts.py layer): requested — or default — account, managed tokens
    refreshed in place. The gmail/gcal/hubspot bespoke helpers predate this."""
    from . import accounts as _accounts

    account_id, key, profile = _accounts.resolve(secrets, connector, account)
    if profile is None:
        hint = (
            f"no {connector} account matching {account!r}"
            if account
            else f"{connector} is not connected"
        )
        return "", None, {"error": hint}
    if profile.get("managed"):
        from ..cloud import ensure_fresh_connector_token
        from ..config import load_config

        ensure_fresh_connector_token(secrets, load_config(), connector, profile_key=key)
        profile = secrets.get(key) or profile
    missing = [k for k in keys if not profile.get(k)]
    if missing:
        return (
            account_id,
            None,
            {"error": f"{connector} is not connected; missing {', '.join(missing)}"},
        )
    return account_id, profile, None


def _acct_result(account_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Stamp which account served a tool call — approvals and transcripts must
    name the account once more than one is connected."""
    if isinstance(result, dict) and account_id:
        return {"account": account_id, **result}
    return result


_GEN_ACCOUNT_PROP = {
    "type": "string",
    "description": "Which connected account to use (default account when empty)",
}

_ACCOUNT_PROP = {
    "type": "string",
    "description": "Mailbox email to use; omit for the default account.",
}


def _gmail_profile(
    secrets: SecretStore, account: str = ""
) -> tuple[str, Optional[dict[str, Any]], Optional[dict[str, str]]]:
    """(email, profile, err) for the requested — or default — mailbox, with the
    managed token refreshed in place. Multi-account: `gmail:account:<email>`."""
    from . import gmail_accounts

    email, key, profile = gmail_accounts.resolve(secrets, account)
    if profile is None:
        hint = (
            f"no gmail account matching {account!r}"
            if account
            else "gmail is not connected"
        )
        return "", None, {"error": hint}
    if profile.get("managed"):
        from ..cloud import ensure_fresh_connector_token
        from ..config import load_config

        ensure_fresh_connector_token(secrets, load_config(), "gmail", profile_key=key)
        profile = secrets.get(key) or profile
    if not profile.get("access_token"):
        return "", None, {"error": f"gmail account {email} has no usable token"}
    return email, profile, None


def _gcal_profile(
    secrets: SecretStore, account: str = ""
) -> tuple[str, Optional[dict[str, Any]], Optional[dict[str, str]]]:
    """(email, profile, err) for the requested — or default — Google account,
    with the managed token refreshed in place. Multi-account:
    `google_calendar:account:<email>`."""
    from . import gcal_accounts

    email, key, profile = gcal_accounts.resolve(secrets, account)
    if profile is None:
        hint = (
            f"no google calendar account matching {account!r}"
            if account
            else "google calendar is not connected"
        )
        return "", None, {"error": hint}
    if profile.get("managed"):
        from ..cloud import ensure_fresh_connector_token
        from ..config import load_config

        ensure_fresh_connector_token(
            secrets, load_config(), "google_calendar", profile_key=key
        )
        profile = secrets.get(key) or profile
    if not profile.get("access_token"):
        return (
            "",
            None,
            {"error": f"google calendar account {email} has no usable token"},
        )
    return email, profile, None


# HubSpot-defined association type ids: note → object (v4 default associations).
_HS_NOTE_ASSOC = {"contacts": 202, "companies": 190, "deals": 214, "tickets": 228}


def _now_ms() -> int:
    from time import time

    return int(time() * 1000)


def _hubspot_profile(
    secrets: SecretStore, portal: str = ""
) -> tuple[str, str, Optional[dict[str, str]]]:
    """(portal name, bearer token, err) for the requested — or default — portal,
    with a managed token refreshed in place. Multi-portal: `hubspot:portal:<id>`."""
    from . import hubspot_portals

    hub_id, key, profile = hubspot_portals.resolve(secrets, portal)
    if profile is None:
        hint = (
            f"no hubspot portal matching {portal!r}"
            if portal
            else "hubspot is not connected"
        )
        return "", "", {"error": hint}
    if profile.get("managed"):
        from ..cloud import ensure_fresh_connector_token
        from ..config import load_config

        ensure_fresh_connector_token(secrets, load_config(), "hubspot", profile_key=key)
        profile = secrets.get(key) or profile
    # Manual private-app profiles carry `token`; managed OAuth carries
    # `access_token` (which is what the broker refresh rotates).
    token = profile.get("token") or profile.get("access_token") or ""
    if not token:
        return "", "", {"error": f"hubspot portal {hub_id} has no usable token"}
    name = str(profile.get("account") or f"portal {hub_id}")
    return name, token, None


def _hubspot_result(secrets: SecretStore, portal_name: str, result: dict) -> dict:
    """Post-process a CRM read: strip denylisted fields (model-facing policy)
    and name the portal so transcripts/approvals say where data came from.
    Stripped-value counts ride `_display` → audit; agents see nothing."""
    from . import hubspot_portals

    if not result.get("ok"):
        return result
    hidden = hubspot_portals.get_hidden_fields(secrets)
    data, removed = hubspot_portals.strip_hidden(result.get("data"), hidden)
    out = {**result, "data": data, "portal": portal_name}
    if removed:
        out["_display"] = {"hidden_fields": removed, "connector": "hubspot"}
    return out


# --- "Never show agents" enforcement (desktop tool layer, silent to agents) ----


def _gmail_filters(secrets: SecretStore) -> Optional[dict[str, list[str]]]:
    from . import gmail_accounts

    f = gmail_accounts.get_filters(secrets)
    return f if (f["senders"] or f["labels"]) else None


def _gmail_from_address(message: dict[str, Any]) -> str:
    from email.utils import parseaddr

    for h in (message.get("payload") or {}).get("headers") or []:
        if str(h.get("name", "")).lower() == "from":
            return parseaddr(str(h.get("value") or ""))[1]
    return ""


def _gmail_label_map(token: str) -> dict[str, str]:
    """Label id → name for the mailbox (names are what the user filters on)."""
    resp = _request(
        "GET",
        "https://gmail.googleapis.com/gmail/v1/users/me/labels",
        headers=_google_headers(token),
    )
    if not resp.get("ok"):
        return {}
    labels = (resp.get("data") or {}).get("labels") or []
    return {str(l.get("id") or ""): str(l.get("name") or "") for l in labels}


def _gmail_is_hidden(
    message: dict[str, Any],
    filters: dict[str, list[str]],
    label_map: dict[str, str],
) -> bool:
    from .gmail_accounts import sender_matches

    if filters["senders"] and sender_matches(
        _gmail_from_address(message), filters["senders"]
    ):
        return True
    if filters["labels"]:
        wanted = {name.lower() for name in filters["labels"]}
        for lid in message.get("labelIds") or []:
            if (
                label_map.get(str(lid), "").lower() in wanted
                or str(lid).lower() in wanted
            ):
                return True
    return False


def _request(
    method: str, url: str, *, headers=None, params=None, json=None, auth=None
) -> dict[str, Any]:
    try:
        import httpx

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.request(
                method, url, headers=headers, params=params, json=json, auth=auth
            )
            ctype = resp.headers.get("content-type", "")
            data: Any = resp.json() if "json" in ctype.lower() else resp.text
            if resp.status_code >= 400:
                return {"error": f"HTTP {resp.status_code}", "details": data}
            return {"ok": True, "data": data}
    except Exception as exc:
        return {"error": str(exc)}


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parser.parts))


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_base() -> str:
    import os

    return os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def _github_auth(
    secrets: SecretStore, install: str = "", *, force: bool = False
) -> tuple[Optional[dict[str, str]], Optional[dict[str, str]]]:
    """(headers, err). A manual PAT (`github:default.token`) wins, untouched;
    a managed relay profile mints a short-lived installation token instead —
    memory-cached, never stored (github-relay-spec §4). `install` picks the
    installation by account login (pass the repo owner) or id; unknown values
    fall back to the default installation."""
    profile = secrets.get("github:default") or {}
    if profile.get("token"):
        return _github_headers(profile["token"]), None
    if profile.get("mode") == "relay":
        from ..cloud import github_installation_token
        from ..config import load_config
        from . import github_installs

        installation_id, _prof = github_installs.resolve(secrets, install)
        if not installation_id and install:
            installation_id, _prof = github_installs.resolve(secrets, "")
        if not installation_id:
            return None, {"error": "github is not connected; no App installation"}
        token = github_installation_token(
            secrets, load_config(), installation_id, force=force
        )
        if not token:
            return None, {
                "error": "github installation token unavailable "
                "(sign in to OpenWorker Cloud and retry)"
            }
        return _github_headers(token), None
    return None, {"error": "github is not connected; missing token"}


def _github_git_auth_args(secrets: SecretStore, owner: str) -> list[str]:
    """Per-invocation git auth: the token rides an HTTP header on the command
    line only — it must NEVER land in .git/config or a credential store (the
    no-token-at-rest rule; github-relay-spec §4). Empty for the tokenless case
    (public repos clone fine without auth)."""
    import base64

    headers, err = _github_auth(secrets, owner)
    if err:
        return ["-c", "credential.helper="]
    token = headers["Authorization"].split(" ", 1)[1]
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return [
        "-c",
        f"http.extraHeader=AUTHORIZATION: basic {basic}",
        "-c",
        "credential.helper=",
    ]


def _run_git(
    args: list[str], *, cwd: Any = None, timeout: int = 600
) -> tuple[str, str]:
    """(stdout, error). Never raises; the error string is capped and carries no
    auth material (git never echoes header values)."""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return "", "git is not installed"
    except subprocess.TimeoutExpired:
        return "", "git timed out"
    if proc.returncode != 0:
        return "", (proc.stderr or proc.stdout).strip()[-500:]
    return proc.stdout.strip(), ""


def _github_git_base() -> str:
    import os

    return os.environ.get("GITHUB_GIT_URL", "https://github.com").rstrip("/")


def _github_call(
    secrets: SecretStore, method: str, path: str, *, install: str = "", **kw: Any
) -> dict[str, Any]:
    """A GitHub API call that works on either auth path. A 401 on the managed
    path re-mints once (the cached installation token may have just expired)."""
    headers, err = _github_auth(secrets, install)
    if err:
        return err
    out = _request(method, _github_base() + path, headers=headers, **kw)
    managed = not (secrets.get("github:default") or {}).get("token")
    if managed and out.get("error") == "HTTP 401":
        headers, err = _github_auth(secrets, install, force=True)
        if err:
            return out
        out = _request(method, _github_base() + path, headers=headers, **kw)
    return out


def _google_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _graph_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _basic_auth(email: str, token: str) -> tuple[str, str]:
    return (email, token)


def _atlassian_base(profile: dict[str, Any]) -> str:
    return str(profile.get("base_url", "")).rstrip("/")


def _bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _gitlab_api(profile: dict[str, Any]) -> str:
    base = str(profile.get("base_url") or "https://gitlab.com").rstrip("/")
    return f"{base}/api/v4"


def _linear_gql(api_key: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    return _request(
        "POST",
        "https://api.linear.app/graphql",
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        json={"query": query, "variables": variables},
    )


def _clamp(n: Any, default: int = 10, ceiling: int = 20) -> int:
    return max(1, min(int(n or default), ceiling))


def _qbo_base(profile: dict[str, Any]) -> str:
    env = str(profile.get("environment", "")).lower()
    host = (
        "sandbox-quickbooks.api.intuit.com"
        if env.startswith("sand")
        else "quickbooks.api.intuit.com"
    )
    return f"https://{host}/v3/company/{profile['realm_id']}"

def make_integration_tools(
    secrets: SecretStore,
    *,
    enabled_connectors: Optional[set[str]] = None,
    enabled_tools: Optional[set[str]] = None,
    roots: Optional[list[Any]] = None,
) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = make_browser_automation_tools()
    tools.extend(make_email_tools(secrets, roots=roots))

    from .integration_tool_group_1 import add_tools as group_1
    from .integration_tool_group_2 import add_tools as group_2
    from .integration_tool_group_3 import add_tools as group_3
    from .integration_tool_group_4 import add_tools as group_4
    from .integration_tool_group_5 import add_tools as group_5
    from .integration_tool_group_6 import add_tools as group_6
    from .integration_tool_group_7 import add_tools as group_7
    from .integration_tool_group_8 import add_tools as group_8
    from .integration_tool_group_9 import add_tools as group_9
    from .integration_tool_group_10 import add_tools as group_10
    from .integration_tool_group_11 import add_tools as group_11

    builders = (group_1, group_2, group_3, group_4, group_5, group_6, group_7, group_8, group_9, group_10, group_11,)
    for build in builders:
        build(secrets, roots, tools)

    if enabled_connectors is not None:
        tools = [
            t for t in tools if connector_for_tool(t.__name__) in enabled_connectors
        ]
    if enabled_tools is not None:
        tools = [t for t in tools if t.__name__ in enabled_tools]
    return tools
