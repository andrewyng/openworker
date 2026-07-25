"""Connector descriptors — data that drives the guided setup wizard.

Adding a connector is (mostly) data, not UI code: a descriptor declares its auth method,
the fields the user pastes, step-by-step instructions, and a `validate` that confirms the
token by a real API call (and returns the bot identity to show back). Designed so a managed
one-click OAuth (`auth="oauth"`) can slot in later for the cloud product without changing the
data model — only the connect action differs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Field:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    help: str = ""
    placeholder: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "secret": self.secret,
            "required": self.required,
            "help": self.help,
            "placeholder": self.placeholder,
        }


@dataclass
class ValidationResult:
    ok: bool
    identity: Optional[str] = (
        None  # e.g. "@mybot" — shown back to the user, never a secret
    )
    error: Optional[str] = None


@dataclass
class ConnectorDescriptor:
    name: str
    title: str
    icon: str
    blurb: str
    auth: str  # "bot_token" | "socket_app" | "oauth" | "token" | "api_token" | "none"
    two_way: bool
    fields: list[Field]
    instructions: list[str]
    available: bool = True  # False → shown as "soon"
    # Chat-platform capability, narrower than two_way: sessions can SUBSCRIBE to this
    # connector's channels (Sources ▸ Channels, listening-sessions block). GitHub is
    # two_way via the relay (inbound mentions) but has no channel semantics.
    channels: bool = False
    validate: Optional[Callable[[dict], ValidationResult]] = None
    # Registry metadata (UI-Refresh §1): the connector's brand color (hex; fallback gray) and a
    # stable logo id (e.g. "slack") the frontend maps to a bundled SVG. Empty logo → UI fallback.
    brand_color: str = "#6b7280"
    logo: str = ""
    # Extra search terms for the catalog typeahead — capability words the title
    # doesn't carry (e.g. "calendar" must surface Outlook, not just Google Calendar).
    aliases: tuple = ()
    # Vendor-hosted MCP server URL → this connector is MCP-BACKED: one-click connect
    # runs the local MCP OAuth flow (DCR, tokens on this Mac — no broker), and the
    # tool surface is the PINNED subset in tool_defs (names `mcp__<name>__<tool>`),
    # never the vendor's full catalog (drift can only shrink capability, not grow it).
    # A connector may carry BOTH mcp_url and manual fields (jira): the profile's
    # mode decides which tool set is live.
    mcp_url: str = ""
    # Experimental connectors are hidden unless the user enables them in settings, require an
    # explicit risk acknowledgment to connect, and ship in a separate package
    # (connectors/experimental/) that release builds exclude entirely.
    experimental: bool = False
    risk_notice: str = ""
    # One-click managed OAuth via OpenWorker Cloud (requires cloud sign-in).
    # Manual token paste ALWAYS remains available — signed out or in — managed
    # is an extra path, never a replacement (local-only open-source flow is
    # sacred).
    managed: bool = False
    # One-click temporarily unavailable (e.g. Google pending CASA verification):
    # the GUI shows a disabled button with a "Coming soon" badge, the server
    # refuses begin_managed_connect, and the manual path is unaffected.
    managed_paused: bool = False
    # Multi-account (accounts.py generic layer): the creds field that names an
    # account (e.g. "project_id"), or "@identity" = the validator's identity
    # string. Non-empty → profiles live at `<name>:account:<id>` and the
    # `:default` profile is pointer-only. Empty → single-profile connector.
    account_field: str = ""


# -- validators (sync httpx, one-shot) -----------------------------------------
def _validate_telegram(creds: dict) -> ValidationResult:
    import httpx

    token = creds.get("bot_token", "")
    try:
        data = httpx.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=15
        ).json()
    except Exception as exc:
        return ValidationResult(False, error=str(exc))
    if data.get("ok"):
        return ValidationResult(
            True, identity="@" + str(data["result"].get("username", "bot"))
        )
    return ValidationResult(False, error=data.get("description") or "invalid bot token")


def _validate_email(creds: dict) -> ValidationResult:
    from .email_tools import validate_email_account

    ok, identity, error = validate_email_account(creds)
    return ValidationResult(ok, identity=identity or None, error=error or None)


def _validate_slack(creds: dict) -> ValidationResult:
    import httpx

    token = creds.get("bot_token", "")
    try:
        data = httpx.post(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        ).json()
    except Exception as exc:
        return ValidationResult(False, error=str(exc))
    if data.get("ok"):
        return ValidationResult(
            True, identity=f"{data.get('team', '?')} / {data.get('user', 'bot')}"
        )
    return ValidationResult(False, error=data.get("error") or "invalid bot token")


def _validate_whoami(
    method: str,
    url: str,
    *,
    headers: dict,
    identity: Callable[[dict], str],
    json: Optional[dict] = None,
) -> ValidationResult:
    """Shared one-shot whoami check: 2xx + extractable identity, else a failure."""
    import httpx

    try:
        resp = httpx.request(method, url, headers=headers, json=json, timeout=15)
        data = resp.json()
    except Exception as exc:
        return ValidationResult(False, error=str(exc))
    if resp.status_code >= 400:
        detail = (
            (data.get("message") or data.get("error") or data.get("error_summary"))
            if isinstance(data, dict)
            else None
        )
        return ValidationResult(False, error=str(detail or f"HTTP {resp.status_code}"))
    try:
        return ValidationResult(True, identity=str(identity(data)))
    except Exception:
        return ValidationResult(False, error="unexpected response from API")


def _validate_notion(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        "https://api.notion.com/v1/users/me",
        headers={
            "Authorization": f"Bearer {creds.get('access_token', '')}",
            "Notion-Version": "2022-06-28",
        },
        identity=lambda d: (d.get("bot") or {}).get("workspace_name") or d["name"],
    )


def _validate_attio(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        "https://api.attio.com/v2/self",
        headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
        identity=lambda d: d.get("workspace_name") or d["workspace_id"],
    )


def _validate_posthog(creds: dict) -> ValidationResult:
    base = str(creds.get("base_url") or "https://us.posthog.com").rstrip("/")
    return _validate_whoami(
        "GET",
        f"{base}/api/users/@me/",
        headers={"Authorization": f"Bearer {creds.get('api_key', '')}"},
        identity=lambda d: d["email"],
    )


def _validate_mixpanel(creds: dict) -> ValidationResult:
    import base64 as _b64

    pair = f"{creds.get('username', '')}:{creds.get('secret', '')}"
    return _validate_whoami(
        "GET",
        "https://mixpanel.com/api/app/me",
        headers={"Authorization": "Basic " + _b64.b64encode(pair.encode()).decode()},
        identity=lambda d, u=creds.get("username", ""): u,
    )


def _validate_amplitude(creds: dict) -> ValidationResult:
    import base64 as _b64

    pair = f"{creds.get('api_key', '')}:{creds.get('secret_key', '')}"
    return _validate_whoami(
        "GET",
        "https://amplitude.com/api/2/annotations",
        headers={"Authorization": "Basic " + _b64.b64encode(pair.encode()).decode()},
        # No user identity on this API — name the account by the key's tail so
        # two projects stay tellable-apart in the accounts list.
        identity=lambda d, k=str(creds.get("api_key", "")): f"key …{k[-6:]}",
    )


def _validate_apollo(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        "https://api.apollo.io/api/v1/auth/health",
        headers={"X-Api-Key": creds.get("api_key", "")},
        identity=lambda d: str(creds.get("label") or "").strip() or "default",
    )


def _validate_hunter(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        f"https://api.hunter.io/v2/account?api_key={creds.get('api_key', '')}",
        headers={},
        identity=lambda d: d["data"]["email"],
    )


def _validate_linear(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "POST",
        "https://api.linear.app/graphql",
        headers={
            "Authorization": creds.get("api_key", ""),
            "Content-Type": "application/json",
        },
        json={"query": "{ viewer { name } }"},
        identity=lambda d: d["data"]["viewer"]["name"],
    )


def _validate_gitlab(creds: dict) -> ValidationResult:
    base = str(creds.get("base_url") or "https://gitlab.com").rstrip("/")
    return _validate_whoami(
        "GET",
        f"{base}/api/v4/user",
        headers={"PRIVATE-TOKEN": creds.get("token", "")},
        identity=lambda d: "@" + d["username"],
    )


def _validate_discord(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": f"Bot {creds.get('bot_token', '')}"},
        identity=lambda d: d["username"],
    )


def _validate_asana(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        "https://app.asana.com/api/1.0/users/me",
        headers={"Authorization": f"Bearer {creds.get('token', '')}"},
        identity=lambda d: d["data"]["name"],
    )


def _validate_hubspot(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        "https://api.hubapi.com/account-info/v3/details",
        headers={"Authorization": f"Bearer {creds.get('token', '')}"},
        identity=lambda d: f"portal {d['portalId']}",
    )


def _validate_dropbox(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "POST",
        "https://api.dropboxapi.com/2/users/get_current_account",
        headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
        identity=lambda d: d["email"],
    )


def _quickbooks_host(creds: dict) -> str:
    env = str(creds.get("environment", "")).lower()
    return (
        "sandbox-quickbooks.api.intuit.com"
        if env.startswith("sand")
        else "quickbooks.api.intuit.com"
    )


def _validate_quickbooks(creds: dict) -> ValidationResult:
    realm = creds.get("realm_id", "")
    return _validate_whoami(
        "GET",
        f"https://{_quickbooks_host(creds)}/v3/company/{realm}/companyinfo/{realm}",
        headers={
            "Authorization": f"Bearer {creds.get('access_token', '')}",
            "Accept": "application/json",
        },
        identity=lambda d: d["CompanyInfo"]["CompanyName"],
    )


def _validate_box(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        "https://api.box.com/2.0/users/me",
        headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
        identity=lambda d: d["login"],
    )


def _validate_whatsapp(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        f"https://graph.facebook.com/v21.0/{creds.get('phone_number_id', '')}",
        headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
        identity=lambda d: d["display_phone_number"],
    )


def _validate_clickup(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        "https://api.clickup.com/api/v2/user",
        headers={"Authorization": creds.get("api_token", "")},
        identity=lambda d: d["user"]["username"],
    )


def _validate_close(creds: dict) -> ValidationResult:
    import base64 as _b64

    # Close authenticates with HTTP basic auth: the API key is the username, blank password.
    pair = f"{creds.get('api_key', '')}:"
    return _validate_whoami(
        "GET",
        "https://api.close.com/api/v1/me/",
        headers={"Authorization": "Basic " + _b64.b64encode(pair.encode()).decode()},
        identity=lambda d: d["email"],
    )


def _validate_figma(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        "https://api.figma.com/v1/me",
        headers={"X-Figma-Token": creds.get("access_token", "")},
        identity=lambda d: d["email"],
    )


def _validate_google_drive(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        "https://www.googleapis.com/drive/v3/about?fields=user",
        headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
        identity=lambda d: d["user"]["emailAddress"],
    )


def _validate_docusign(creds: dict) -> ValidationResult:
    # userinfo also carries accounts[] (account_id + base_uri); the tool layer
    # re-fetches and caches those on first use, so validation only needs identity.
    return _validate_whoami(
        "GET",
        "https://account.docusign.com/oauth/userinfo",
        headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
        identity=lambda d: d["email"],
    )


def _validate_canva(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        "https://api.canva.com/rest/v1/users/me/profile",
        headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
        identity=lambda d: d["profile"]["display_name"],
    )


def _validate_outlook(creds: dict) -> ValidationResult:
    return _validate_whoami(
        "GET",
        "https://graph.microsoft.com/v1.0/me",
        headers={"Authorization": f"Bearer {creds.get('access_token', '')}"},
        identity=lambda d: d.get("mail") or d["userPrincipalName"],
    )


_ALLOWED_FIELD = Field(
    key="allowed_users",
    label="Allowed user IDs",
    required=False,
    help="Comma-separated IDs allowed to message the bot. Leave empty, then DM the bot and use Capture.",
    placeholder="123456789",
)
