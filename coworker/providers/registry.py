"""Model-provider registry — descriptors + a factory, mirroring the connector
(`connectors/descriptors.py`) and web-search (`web/providers.py`) patterns.

A `ProviderDescriptor` declares a provider's UI config `fields` (rendered dynamically by the
GUI, same `to_dict()` shape connectors use) and a `build(profile, secrets)` factory that returns
a `ProviderClient`. The `ProviderRouter` selects a descriptor by the `provider:` prefix of a
model string and builds (and caches) its client from the matching SecretStore profile.

Today: `openai` (the default — native models via the Responses API; an optional custom
endpoint for vLLM and any OpenAI-compliant gateway keeps the Chat Completions path),
`azure` (the user's own Azure OpenAI / Foundry resource through its `/openai/v1` API,
key-authenticated; model ids are deployment names), `anthropic` (native Messages API via
`AnthropicProvider`), `gemini` (native Google GenAI API via `GeminiProvider`), `bedrock`
(models in the user's own AWS account — Claude natively, everything else via Converse),
`vertex` (the user's own GCP project — Gemini and Claude natively, open-weight via the
MaaS endpoint), `novita` (OpenAI-compatible Novita Cloud), and `ollama` (local,
OpenAI-compatible `/v1`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .anthropic_provider import AnthropicProvider
from .base import ProviderClient
from .bedrock_provider import BedrockProvider
from .gemini_provider import GeminiProvider
from .local_server import LOCAL_SERVERS
from .openai_provider import OpenAIProvider
from .openai_responses import OpenAIResponsesProvider
from .vertex_provider import VertexProvider

DEFAULT_OLLAMA_URL = "http://localhost:11434"
# The index deliberately lives in the SecretStore too: it names profiles which contain
# credentials, and keeping the two together makes a future secure-store backend a drop-in.
CUSTOM_PROVIDERS_PROFILE = "providers:custom"


@dataclass(frozen=True)
class ProviderField:
    """One config input for a provider, rendered by the GUI (mirrors connectors' `Field`)."""

    key: str
    label: str
    secret: bool = False
    required: bool = True
    help: str = ""
    placeholder: str = ""
    # Pre-filled (still editable) form value — e.g. an OpenAI-compatible vendor's official
    # endpoint, so the user only has to paste a key. Distinct from `placeholder` (grey hint).
    default: str = ""
    # Non-empty → the field renders as a segmented choice control instead of a text input;
    # each option is {"value", "label"} plus optional UI extras: "tag" (a tiny badge like
    # "Easiest"), "desc" (one-liner atop the method's panel), and "command" (a copyable
    # terminal command shown in the panel, e.g. the gcloud ADC login). The chosen value is
    # stored like any other field value.
    choices: tuple = ()
    # {"other_field_key": "value"} → the field only renders while that other field holds
    # that value. Drives auth-method switching (Bedrock) without a per-provider form.
    show_when: Optional[dict] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "secret": self.secret,
            "required": self.required,
            "help": self.help,
            "placeholder": self.placeholder,
            "default": self.default,
            "choices": [dict(c) for c in self.choices],
            "show_when": self.show_when,
        }


@dataclass(frozen=True)
class ProviderDescriptor:
    """A model provider: its UI fields + a factory that builds its `ProviderClient`."""

    name: str
    title: str
    needs_key: bool
    fields: list[ProviderField]
    build: Callable[[dict[str, Any], Any], ProviderClient] = field(repr=False)
    recommended_model: Optional[str] = (
        None  # pre-filled in the UI; auto-added on configure
    )
    env_key: Optional[str] = (
        None  # env var that can supply the API key (e.g. ANTHROPIC_API_KEY)
    )
    # One-line note under the provider title (e.g. "Connects through X's OpenAI-compatible API").
    blurb: str = ""
    # "oauth" → no key form at all: the provider is configured by a browser sign-in
    # (tokens in its `provider:<name>` profile) and the GUI renders connect/sign-out
    # instead of fields. None → the usual key/field form.
    auth: Optional[str] = None
    # How the GUI groups it: "local" (a server on the user's own hardware), "subscription"
    # (a plan signed into), or "api_key" (everything else).
    kind: str = "api_key"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "needs_key": self.needs_key,
            "fields": [f.to_dict() for f in self.fields],
            "recommended_model": self.recommended_model,
            "blurb": self.blurb,
            "auth": self.auth,
            "kind": self.kind,
        }


def _normalize_ollama_url(url: Optional[str]) -> str:
    """Accept `http://host:11434` or `.../v1` and return an OpenAI-compatible base URL.

    Ollama serves its OpenAI-compatible API under `/v1`; the native API lives at the root, so we
    always target `<root>/v1`.
    """
    base = (url or DEFAULT_OLLAMA_URL).strip().rstrip("/")
    if not base:
        base = DEFAULT_OLLAMA_URL
    if not base.endswith("/v1"):
        base = base + "/v1"
    return base


def _build_openai(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Key resolution stays in resolve_api_key (explicit → env → SecretStore), so we just
    # hand over the SecretStore. Stock OpenAI (no custom endpoint) speaks the Responses
    # API — the only wire with reasoning + tools on GPT-5.6+. A custom endpoint (vLLM,
    # any OpenAI-compliant gateway) keeps Chat Completions, which is what compat servers
    # implement. Azure OpenAI has its own card below (`azure`); an Azure `/openai/v1`
    # URL pasted here keeps working for users who set it up before that card existed.
    base_url = ((profile or {}).get("base_url") or "").strip() or None
    if base_url:
        return OpenAIProvider(secrets=secrets, base_url=base_url)
    return OpenAIResponsesProvider(secrets=secrets)


AZURE_OPENAI_ENV_KEY = "AZURE_OPENAI_API_KEY"
_AZURE_V1_SUFFIX = "/openai/v1"


def normalize_azure_endpoint(endpoint: Optional[str]) -> str:
    """The `/openai/v1` base URL for an Azure OpenAI or Foundry resource, from whatever the
    user pasted: the bare resource endpoint (`https://<resource>.openai.azure.com`, also
    `…services.ai.azure.com` and sovereign-cloud hosts), one already ending in `/openai`
    or `/openai/v1`, or a full request URL copied from the portal (its path and
    `?api-version=` query are dropped — the v1 API takes neither). Empty in ⇒ empty out.

    Base URL per learn.microsoft.com/azure/ai-foundry/openai/api-version-lifecycle
    (read 2026-10-26): append `/openai/v1` to the resource endpoint; no api-version.
    """
    raw = (endpoint or "").strip().split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    lowered = raw.lower()
    if lowered.endswith(_AZURE_V1_SUFFIX):
        return raw
    if lowered.endswith("/openai"):
        return raw + "/v1"
    # A deployments URL (`…/openai/deployments/<name>/chat/completions`) or any other
    # path under /openai: keep the origin and the /openai segment, drop the rest.
    marker = lowered.find("/openai/")
    if marker != -1:
        return raw[:marker] + _AZURE_V1_SUFFIX
    return raw + _AZURE_V1_SUFFIX


def _build_azure(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    """Azure OpenAI through the v1 API: the stock OpenAI SDK pointed at
    `<endpoint>/openai/v1`. The key comes from THIS provider's profile or
    AZURE_OPENAI_API_KEY only — never the OpenAI key or SecretStore fallback, so a
    configured OpenAI key is never sent to an Azure host (same guard as the compat
    vendors). Chat Completions, as the OpenAI custom-endpoint path always used for Azure."""
    p = profile or {}
    base_url = normalize_azure_endpoint(p.get("endpoint"))
    if not base_url:
        raise RuntimeError(
            "No Azure OpenAI endpoint configured — add it in Settings ▸ Models."
        )
    api_key = (p.get("api_key") or "").strip() or os.environ.get(
        AZURE_OPENAI_ENV_KEY, ""
    ).strip()
    if not api_key:
        raise RuntimeError(
            "No Azure OpenAI API key configured — add it in Settings ▸ Models."
        )
    return OpenAIProvider(api_key=api_key, base_url=base_url)


def _build_codex(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Credentials come from the OAuth token set in the provider's own profile,
    # resolved (and refreshed) at call time by the token store — never a key.
    from .codex_provider import CodexProvider

    return CodexProvider(secrets=secrets)


def _build_anthropic(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Key resolution stays in AnthropicProvider/resolve_api_key (explicit → env → SecretStore),
    # deferred to first call so the provider can be built before a key exists.
    # thinking_budget: hidden profile override — absent/invalid → the default (ON),
    # explicit 0 → off (see DEFAULT_THINKING_BUDGET).
    from .anthropic_provider import DEFAULT_THINKING_BUDGET

    api_key = ((profile or {}).get("api_key") or "").strip() or None
    try:
        thinking_budget = int(str((profile or {}).get("thinking_budget") or "").strip())
    except ValueError:
        thinking_budget = DEFAULT_THINKING_BUDGET
    return AnthropicProvider(
        api_key=api_key, secrets=secrets, thinking_budget=thinking_budget
    )


def _build_gemini(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Same deferred-key contract as anthropic (GeminiProvider/resolve_api_key).
    api_key = ((profile or {}).get("api_key") or "").strip() or None
    return GeminiProvider(api_key=api_key, secrets=secrets)


def _build_bedrock(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Credentials resolve inside boto3/AnthropicBedrock at call time: explicit keys →
    # named profile → ambient chain (env / ~/.aws default / instance role).
    p = profile or {}

    def get(key: str) -> Optional[str]:
        return (p.get(key) or "").strip() or None

    return BedrockProvider(
        region=get("region"),
        auth_method=get("auth_method"),
        bedrock_api_key=get("bedrock_api_key"),
        profile_name=get("aws_profile"),
        access_key_id=get("aws_access_key_id"),
        secret_access_key=get("aws_secret_access_key"),
        session_token=get("aws_session_token"),
    )


def _build_vertex(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    p = profile or {}

    def get(key: str) -> Optional[str]:
        return (p.get(key) or "").strip() or None

    return VertexProvider(
        project=get("project"),
        location=get("location"),
        auth_method=get("auth_method"),
        service_account_json=get("service_account_json"),
        api_key=get("vertex_api_key"),
    )


def _build_ollama(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Ollama's OpenAI-compatible endpoint ignores the key but the SDK requires a non-empty
    # string, so we pass a placeholder. `base_url` comes from the stored profile (or the default).
    # Chat calls are rewritten onto the native API so we can set num_ctx — the /v1 handler
    # cannot, and its 4,096-token default drops the Cowork prompt (see ollama_context.py).
    from .ollama_context import ollama_http_client

    base_url = _normalize_ollama_url((profile or {}).get("base_url"))
    return OpenAIProvider(api_key="ollama", base_url=base_url, http_client=ollama_http_client())


def _local_server(name: str):
    """Builder for the OpenAI-compatible local servers (providers/local_server.py: llama.cpp,
    vLLM, LM Studio, Jan, Docker Model Runner, mlx-lm): the user's own server,
    reached through its `/v1`, with an optional key. Without a key the server takes any
    value, so a placeholder goes out."""
    from .local_server import PLACEHOLDER_KEY, remember_server, thinking_as_template_kwargs, v1_base

    class _LocalServerProvider(OpenAIProvider):
        # The thinking switch, in the form these servers read.
        def complete(self, **kwargs: Any):
            return super().complete(**thinking_as_template_kwargs(kwargs))

        def stream(self, **kwargs: Any):
            return super().stream(**thinking_as_template_kwargs(kwargs))

    def build(profile: dict[str, Any], secrets: Any) -> ProviderClient:
        key = ((profile or {}).get("api_key") or "").strip() or PLACEHOLDER_KEY
        remember_server(name, (profile or {}).get("base_url"), key if key != PLACEHOLDER_KEY else None)
        return _LocalServerProvider(api_key=key, base_url=v1_base(name, (profile or {}).get("base_url")))

    return build


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _build_openrouter_account(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    """The `openrouter-account` provider: the key OpenRouter issued at sign-in
    (providers/openrouter_auth.py), always against OpenRouter's own endpoint — a
    browser credential is never sent to a custom gateway."""
    api_key = ((profile or {}).get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("OpenRouter account not signed in — sign in under Models & Keys.")
    return OpenAIProvider(api_key=api_key, base_url=OPENROUTER_BASE_URL)


def _openai_compat(vendor: str, default_base_url: str, env_key: Optional[str] = None):
    """Builder factory for vendors reached through their OpenAI-compatible API (Z AI, DeepSeek,
    Kimi, MiniMax, Qwen, xAI, Mistral, Novita, Upstage). The key is resolved from the vendor's OWN
    profile (or its env var) — deliberately NOT from the OpenAI env/SecretStore fallback, so a
    configured OpenAI key is never silently sent to a different vendor's endpoint. Missing key ⇒
    fail fast with a vendor-named error (these are only built on demand, when one of their models
    is selected).
    """

    def build(profile: dict[str, Any], secrets: Any) -> ProviderClient:
        base_url = ((profile or {}).get("base_url") or "").strip() or default_base_url
        api_key = ((profile or {}).get("api_key") or "").strip() or (
            os.environ.get(env_key, "").strip() if env_key else ""
        )
        if not api_key:
            raise RuntimeError(
                f"No {vendor} API key configured — add it in Settings ▸ Models."
            )
        return OpenAIProvider(api_key=api_key, base_url=base_url)

    return build


def _openai_responses_compat(
    vendor: str,
    default_base_url: str,
    env_key: Optional[str] = None,
    *,
    reasoning_summary: bool = True,
):
    """Builder factory for vendors that explicitly implement the OpenAI Responses API.

    Credentials stay isolated to the vendor's own profile/environment variable, matching the
    Chat Completions compat path above. In particular, an OpenAI key is never sent to Ark.
    """

    def build(profile: dict[str, Any], secrets: Any) -> ProviderClient:
        base_url = ((profile or {}).get("base_url") or "").strip() or default_base_url
        api_key = ((profile or {}).get("api_key") or "").strip() or (
            os.environ.get(env_key, "").strip() if env_key else ""
        )
        if not api_key:
            raise RuntimeError(
                f"No {vendor} API key configured — add it in Settings ▸ Models."
            )
        return OpenAIResponsesProvider(
            api_key=api_key,
            base_url=base_url,
            reasoning_summary=reasoning_summary,
        )

    return build


def _compat(
    name: str,
    title: str,
    *,
    base_url: str,
    recommended_model: str,
    env_key: str,
    endpoint_help: str = "",
) -> ProviderDescriptor:
    """Descriptor for an OpenAI-compatible vendor: key + a prefilled, editable endpoint."""
    vendor = title.split(" (")[0]
    return ProviderDescriptor(
        name=name,
        title=title,
        needs_key=True,
        fields=[
            ProviderField(
                "api_key",
                f"{vendor} API key",
                secret=True,
            ),
            ProviderField(
                "base_url",
                "Endpoint",
                required=False,
                default=base_url,
                placeholder=base_url,
                help=endpoint_help
                or f"Prefilled with {vendor}'s official endpoint; edit only for a regional or proxy variant.",
            ),
        ],
        build=_openai_compat(vendor, base_url, env_key),
        recommended_model=recommended_model,
        env_key=env_key,
        blurb=f"Uses {vendor}'s OpenAI-compatible API — the endpoint is prefilled, just add your key.",
    )


def _responses_compat(
    name: str,
    title: str,
    *,
    base_url: str,
    recommended_model: str,
    env_key: str,
    endpoint_help: str = "",
    reasoning_summary: bool = True,
) -> ProviderDescriptor:
    """Descriptor for a vendor exposing the OpenAI Responses API."""
    return ProviderDescriptor(
        name=name,
        title=title,
        needs_key=True,
        fields=[
            ProviderField(
                "api_key",
                f"{title} API key",
                secret=True,
            ),
            ProviderField(
                "base_url",
                "Endpoint",
                required=False,
                default=base_url,
                placeholder=base_url,
                help=endpoint_help
                or f"Prefilled with {title}'s official Responses endpoint.",
            ),
        ],
        build=_openai_responses_compat(
            title,
            base_url,
            env_key,
            reasoning_summary=reasoning_summary,
        ),
        recommended_model=recommended_model,
        env_key=env_key,
        blurb=f"Uses {title}'s OpenAI-compatible Responses API — the endpoint is prefilled, just add your key.",
    )


def _custom_provider_descriptor(name: str, title: str) -> ProviderDescriptor:
    """Descriptor synthesized for a user-defined OpenAI-compatible endpoint.

    Unlike the built-ins there is no catalog or capability claim here. The endpoint and
    bearer key are required; model ids are supplied separately by the user.
    """
    return ProviderDescriptor(
        name=name,
        title=title,
        needs_key=True,
        fields=[
            ProviderField("api_key", f"{title} API key", secret=True),
            ProviderField(
                "base_url",
                "Endpoint",
                required=True,
                placeholder="https://…/v1",
                help="An HTTPS OpenAI-compatible API base URL ending in /v1. HTTP is allowed only for local gateways.",
            ),
        ],
        build=_openai_compat(title, ""),
        blurb="Uses the standard OpenAI Chat Completions API. Add model IDs manually below.",
    )


def _disabled_custom_provider_descriptor(name: str, title: str) -> ProviderDescriptor:
    """Keep a removed route recognizable so old sessions fail closed, never reroute."""

    def build(profile: dict[str, Any], secrets: Any) -> ProviderClient:
        raise RuntimeError(
            f"{title} was removed from Settings ▸ Models. Configure that provider again "
            "before continuing this session."
        )

    return ProviderDescriptor(
        name=name,
        title=title,
        needs_key=True,
        fields=[],
        build=build,
        blurb="Removed custom provider.",
    )


DESCRIPTORS: list[ProviderDescriptor] = [
    ProviderDescriptor(
        name="openai",
        title="OpenAI",
        needs_key=True,
        fields=[
            ProviderField(
                "api_key",
                "OpenAI API key",
                secret=True,
                placeholder="sk-…",
            ),
            ProviderField(
                "base_url",
                "Custom endpoint (optional)",
                secret=False,
                required=False,
                placeholder="https://…/v1",
                help="For vLLM or any OpenAI-compliant server. Leave blank for api.openai.com. "
                "Azure OpenAI has its own card.",
            ),
        ],
        build=_build_openai,
        recommended_model="gpt-5.6-sol",
        env_key="OPENAI_API_KEY",
    ),
    # Azure OpenAI as its own card (issue #457): before it, Azure meant pasting an
    # `/openai/v1` URL into the OpenAI card's custom endpoint, with the OpenAI key field
    # standing in for the Azure key. Entra ID sign-in is a second auth mode to follow (#507).
    ProviderDescriptor(
        name="azure",
        title="Azure OpenAI",
        needs_key=True,
        fields=[
            ProviderField(
                "endpoint",
                "Endpoint",
                secret=False,
                placeholder="https://<resource>.openai.azure.com",
                help="Your resource's endpoint, from Keys and Endpoint in the Azure portal "
                "or the Foundry portal. /openai/v1 is added automatically; Foundry "
                "(…services.ai.azure.com) and sovereign-cloud hosts work too.",
            ),
            ProviderField(
                "api_key",
                "Azure OpenAI API key",
                secret=True,
            ),
        ],
        build=_build_azure,
        # No catalogue: on Azure the model id is the user's own deployment name.
        recommended_model=None,
        env_key=AZURE_OPENAI_ENV_KEY,
        blurb="Your own Azure OpenAI or Foundry resource, through its v1 API — model ids "
        "are your deployment names.",
    ),

    ProviderDescriptor(
        name="openai-codex",
        kind="subscription",
        title="ChatGPT subscription",
        needs_key=False,
        fields=[],
        build=_build_codex,
        recommended_model="gpt-5.6-sol",
        blurb="Sign in with your ChatGPT plan and run OpenAI models through your "
        "subscription — no API key. Tokens stay on this machine.",
        auth="oauth",
    ),
    ProviderDescriptor(
        name="anthropic",
        title="Claude (Anthropic)",
        needs_key=True,
        fields=[
            ProviderField(
                "api_key",
                "Anthropic API key",
                secret=True,
                placeholder="sk-ant-…",
            ),
            # No thinking_budget field (owner call 2026-07-23): extended thinking is
            # on by default; the profile key stays a hidden override (0 = off).
        ],
        build=_build_anthropic,
        recommended_model="claude-fable-5",
        env_key="ANTHROPIC_API_KEY",
    ),
    ProviderDescriptor(
        name="gemini",
        title="Gemini (Google)",
        needs_key=True,
        fields=[
            ProviderField(
                "api_key",
                "Gemini API key",
                secret=True,
                placeholder="AIza…",
            ),
        ],
        build=_build_gemini,
        recommended_model="gemini-3.6-flash",
        env_key="GEMINI_API_KEY",
    ),
    ProviderDescriptor(
        name="bedrock",
        title="AWS Bedrock",
        needs_key=True,
        fields=[
            ProviderField(
                "region",
                "AWS region",
                secret=False,
                placeholder="us-east-1",
                help="The region your Bedrock model access is enabled in.",
            ),
            # One auth method at a time (owner call 2026-07-26): AWS users are advanced —
            # a direct choice beats a pile of "(optional)" fields with hidden precedence.
            ProviderField(
                "auth_method",
                "Connect with",
                secret=False,
                required=False,  # the default stands in; builder tolerates absence
                default="api_key",
                choices=(
                    {
                        "value": "api_key",
                        "label": "Bedrock API key",
                        "tag": "Easiest",
                        "desc": "A single key generated on the Bedrock console — no AWS CLI or IAM setup needed.",
                    },
                    {
                        "value": "profile",
                        "label": "AWS profile",
                        "desc": "Uses a named profile from ~/.aws — works with `aws configure` and `aws sso login`.",
                    },
                    {
                        "value": "iam",
                        "label": "IAM keys",
                        "desc": "An IAM access key pair. For temporary STS credentials, include the session token.",
                    },
                ),
            ),
            ProviderField(
                "bedrock_api_key",
                "Bedrock API key",
                secret=True,
                required=False,
                placeholder="ABSK…",
                show_when={"auth_method": "api_key"},
            ),
            ProviderField(
                "aws_profile",
                "AWS profile",
                secret=False,
                required=False,
                placeholder="default",
                show_when={"auth_method": "profile"},
                help="Leave blank to use your default AWS credentials (env vars or ~/.aws).",
            ),
            ProviderField(
                "aws_access_key_id",
                "Access key ID",
                secret=False,
                required=False,
                placeholder="AKIA…",
                show_when={"auth_method": "iam"},
            ),
            ProviderField(
                "aws_secret_access_key",
                "Secret access key",
                secret=True,
                required=False,
                show_when={"auth_method": "iam"},
            ),
            ProviderField(
                "aws_session_token",
                "Session token (STS only, optional)",
                secret=True,
                required=False,
                show_when={"auth_method": "iam"},
            ),
        ],
        build=_build_bedrock,
        recommended_model="claude/anthropic.claude-sonnet-4-6-v1:0",
        blurb="Runs models inside your own AWS account. Claude uses Anthropic's native "
        "Bedrock path; every other model goes through the Converse API.",
    ),
    ProviderDescriptor(
        name="vertex",
        title="Vertex AI (Google Cloud)",
        needs_key=True,
        fields=[
            ProviderField(
                "project",
                "GCP project ID",
                secret=False,
                placeholder="my-project-123",
            ),
            ProviderField(
                "location",
                "Location",
                secret=False,
                placeholder="global",
                help="Use `global` for the newest Gemini and Claude models. Some models "
                "are regional — Model Garden lists each (Claude also: us-east5 / "
                "europe-west1; Qwen3 Coder: us-south1).",
            ),
            ProviderField(
                "auth_method",
                "Connect with",
                secret=False,
                required=False,  # the default stands in; builder tolerates absence
                default="adc",
                choices=(
                    {
                        "value": "adc",
                        "label": "Google Cloud login",
                        "tag": "Recommended",
                        "desc": "Uses your machine's Google Cloud identity (Application "
                        "Default Credentials). Nothing to paste — sign in once in a terminal:",
                        "command": "gcloud auth application-default login",
                    },
                    {
                        "value": "service_account",
                        "label": "Service account",
                        "desc": "A service-account key — the usual path on shared or headless machines.",
                    },
                    {
                        "value": "api_key",
                        "label": "API key",
                        "desc": "A long-lived key from the Google Cloud console's API Keys page. "
                        "Reaches Gemini models only — Claude and open-weight need Google "
                        "Cloud login or a service account.",
                    },
                ),
            ),
            ProviderField(
                "service_account_json",
                "Service-account JSON",
                secret=True,
                required=False,
                show_when={"auth_method": "service_account"},
                help="Paste the JSON key, or a path to the file.",
            ),
            ProviderField(
                "vertex_api_key",
                "Vertex API key",
                secret=True,
                required=False,
                placeholder="AQ.…",
                show_when={"auth_method": "api_key"},
            ),
        ],
        build=_build_vertex,
        recommended_model="gemini/gemini-3.6-flash",
        blurb="Runs models inside your own Google Cloud project. Gemini and Claude use "
        "their native APIs; open-weight models go through the Vertex MaaS endpoint.",
    ),
    # Ark has two intentionally separate provider identities. BytePlus pay-as-you-go and
    # Volcengine Agent Plan use different regions, endpoints, credentials, and model catalogs;
    # combining them would let one provider profile route a model to the wrong service.
    _responses_compat(
        "ark",
        "BytePlus Ark",
        base_url="https://ark.ap-southeast.bytepluses.com/api/v3",
        recommended_model="dola-seed-evolving-latest-version",
        env_key="ARK_API_KEY",
        endpoint_help="BytePlus Ark's Asia Pacific endpoint. This provider is separate from Volcengine Ark Agent Plan.",
        reasoning_summary=False,
    ),
    _responses_compat(
        "ark-agent-plan-cn",
        "Volcengine Ark Agent Plan",
        base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
        recommended_model="doubao-seed-evolving",
        env_key="ARK_AGENT_PLAN_CN_API_KEY",
        endpoint_help="Volcengine Ark Agent Plan's China (Beijing) endpoint. It requires an Agent Plan API key.",
    ),
    # OpenAI-compatible vendors, listed as first-class providers so users don't need to know the
    # "point the OpenAI slot at a different endpoint" trick (owner call, 2026-07-04). Each keeps
    # its own key profile; the endpoint is prefilled and editable (regional variants in `help`).
    _compat(
        "zai",
        "Z AI (GLM)",
        base_url="https://api.z.ai/api/paas/v4",
        recommended_model="glm-5.2",
        env_key="ZAI_API_KEY",
        endpoint_help="Prefilled with Z AI's international endpoint. China mainland: https://open.bigmodel.cn/api/paas/v4",
    ),
    _compat(
        "deepseek",
        "DeepSeek",
        base_url="https://api.deepseek.com",
        recommended_model="deepseek-v4-flash",
        env_key="DEEPSEEK_API_KEY",
    ),
    _compat(
        "kimi",
        "Kimi (Moonshot AI)",
        base_url="https://api.moonshot.ai/v1",
        recommended_model="kimi-k2.6",
        env_key="MOONSHOT_API_KEY",
        endpoint_help="Prefilled with Moonshot's international endpoint. China mainland: https://api.moonshot.cn/v1",
    ),
    _compat(
        "minimax",
        "MiniMax",
        base_url="https://api.minimax.io/v1",
        recommended_model="MiniMax-M2.5",
        env_key="MINIMAX_API_KEY",
    ),
    _compat(
        "qwen",
        "Qwen (Alibaba)",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        recommended_model="qwen3-max",
        env_key="DASHSCOPE_API_KEY",
        endpoint_help=(
            "Prefilled with the international endpoint. If your key was issued on the "
            "China mainland portal (qwencloud.com), switch to "
            "https://dashscope.aliyuncs.com/compatible-mode/v1 — keys only work against "
            "the endpoint in the region where they were created."
        ),
    ),
    _compat(
        "xai",
        "xAI (Grok)",
        base_url="https://api.x.ai/v1",
        recommended_model="grok-4.3",
        env_key="XAI_API_KEY",
    ),
    _compat(
        "mistral",
        "Mistral",
        base_url="https://api.mistral.ai/v1",
        recommended_model="mistral-large-latest",
        env_key="MISTRAL_API_KEY",
    ),
    _compat(
        "meta",
        "Meta (Muse Spark)",
        base_url="https://api.meta.ai/v1",
        recommended_model="muse-spark-1.1",
        env_key="META_API_KEY",
        endpoint_help="Prefilled with the Meta Model API endpoint (public preview, US-only as of 2026-07).",
    ),
    _compat(
        "sambanova",
        "SambaNova",
        base_url="https://api.sambanova.ai/v1",
        recommended_model="Meta-Llama-3.3-70B-Instruct",
        env_key="SAMBANOVA_API_KEY",
        endpoint_help="Prefilled with SambaNova's official OpenAI-compatible endpoint.",
    ),
    _compat(
        "atlascloud",
        "Atlas Cloud",
        base_url="https://api.atlascloud.ai/v1",
        recommended_model="zai-org/glm-5.2",
        env_key="ATLASCLOUD_API_KEY",
    ),
    _compat(
        "novita",
        "Novita AI",
        base_url="https://api.novita.ai/openai/v1",
        recommended_model="zai-org/glm-5.2",
        env_key="NOVITA_API_KEY",
        endpoint_help="Prefilled with Novita's OpenAI-compatible endpoint; keep it unless you use a regional or proxy variant.",
    ),
    _compat(
        "upstage",
        "Upstage (Solar)",
        base_url="https://api.upstage.ai/v1",
        recommended_model="solar-pro4",
        env_key="UPSTAGE_API_KEY",
    ),
    # Resellers: many labs' models behind one key, using THEIR model namespaces (the curated
    # ids + display labels live in providers/matrix.py). Groq was deferred on 2026-07-04 to
    # bound how much needed verifying at once; it landed 2026-10-25 with verified rows.
    _compat(
        "together",
        "Together AI",
        base_url="https://api.together.xyz/v1",
        recommended_model="zai-org/GLM-5.2",
        env_key="TOGETHER_API_KEY",
    ),
    _compat(
        "fireworks",
        "Fireworks AI",
        base_url="https://api.fireworks.ai/inference/v1",
        recommended_model="accounts/fireworks/models/glm-5p2",
        env_key="FIREWORKS_API_KEY",
    ),
    _compat(
        "openrouter",
        "OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        recommended_model="z-ai/glm-5.2",
        env_key="OPENROUTER_API_KEY",
    ),
    ProviderDescriptor(
        name="openrouter-account",
        kind="subscription",
        title="OpenRouter account",
        needs_key=False,
        fields=[],
        build=_build_openrouter_account,
        recommended_model="z-ai/glm-5.2",
        blurb="Sign in with your OpenRouter account and use its credits — nothing to "
        "paste. The key OpenRouter issues stays on this machine.",
        auth="oauth",
    ),
    _compat(
        "nebius",
        "Nebius Token Factory",
        base_url="https://api.tokenfactory.nebius.com/v1",
        recommended_model="zai-org/GLM-5.2",
        env_key="NEBIUS_API_KEY",
    ),
    _compat(
        "orcarouter",
        "OrcaRouter",
        base_url="https://api.orcarouter.ai/v1",
        recommended_model="anthropic/claude-sonnet-5",
        env_key="ORCAROUTER_API_KEY",
    ),
    # NVIDIA NIM: NVIDIA's own Nemotron line plus other labs' models, all under NVIDIA's
    # `vendor/model` namespace (hence a routed id like `nvidia:nvidia/nemotron-…`). Same
    # reseller shape as Together/Fireworks; the endpoint also fronts self-hosted NIM
    # containers, which is why the base_url help calls that out.
    _compat(
        "nvidia",
        "NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        recommended_model="nvidia/nemotron-3-super-120b-a12b",
        env_key="NVIDIA_API_KEY",
        endpoint_help="Prefilled with NVIDIA's hosted NIM endpoint. Point it at a self-hosted NIM container (e.g. http://localhost:8000/v1) to use your own deployment.",
    ),
    _compat(
        "groq",
        "Groq",
        base_url="https://api.groq.com/openai/v1",
        recommended_model="llama-3.3-70b-versatile",
        env_key="GROQ_API_KEY",
    ),
    # Four more OpenAI-compatible vendors (2026-10-26); endpoints, key pages and the
    # recommended ids read from each vendor's docs that day (sources in matrix.py).
    _compat(
        "cerebras",
        "Cerebras",
        base_url="https://api.cerebras.ai/v1",
        recommended_model="gpt-oss-120b",
        env_key="CEREBRAS_API_KEY",
    ),
    # Hugging Face routes each repo id to one of its partner providers; model ids are
    # the Hub repo ids, optionally with a `:provider` suffix to pin one.
    _compat(
        "huggingface",
        "Hugging Face (Inference Providers)",
        base_url="https://router.huggingface.co/v1",
        recommended_model="zai-org/GLM-5.2",
        env_key="HF_TOKEN",
        endpoint_help="Prefilled with the Inference Providers router. Model ids are Hugging Face repo ids, e.g. zai-org/GLM-5.2.",
    ),
    _compat(
        "cohere",
        "Cohere",
        base_url="https://api.cohere.ai/compatibility/v1",
        recommended_model="command-a-plus-05-2026",
        env_key="COHERE_API_KEY",
        endpoint_help="Prefilled with Cohere's Compatibility API, which speaks the OpenAI chat format.",
    ),
    _compat(
        "siliconflow",
        "SiliconFlow",
        base_url="https://api.siliconflow.com/v1",
        recommended_model="zai-org/GLM-5.2",
        env_key="SILICONFLOW_API_KEY",
        endpoint_help="Prefilled with SiliconFlow's international endpoint. China mainland: https://api.siliconflow.cn/v1 (keys from cloud.siliconflow.cn).",
    ),
    ProviderDescriptor(
        name="ollama",
        title="Ollama",
        needs_key=False,
        fields=[
            ProviderField(
                "base_url",
                "Ollama server URL",
                secret=False,
                required=False,
                placeholder=DEFAULT_OLLAMA_URL,
                help="Where `ollama serve` is listening. The OpenAI-compatible /v1 path is added automatically.",
            ),
        ],
        build=_build_ollama,
        # Reliable native tool-calling + strong coding quality (verified). Pull with
        # `ollama pull qwen3-coder:30b`.
        recommended_model="qwen3-coder:30b",
        kind="local",
    ),
    ProviderDescriptor(
        name="llamacpp",
        title="llama.cpp",
        needs_key=False,
        fields=[
            ProviderField(
                "base_url",
                "Server address",
                secret=False,
                required=False,
                placeholder="http://localhost:8080",
                help="Where `llama-server` is listening. The /v1 path is added automatically.",
            ),
            ProviderField(
                "api_key",
                "API key (only if the server was started with one)",
                secret=True,
                required=False,
            ),
        ],
        build=_local_server("llamacpp"),
        blurb="A llama-server you started yourself, on this computer or another one.",
        kind="local",
    ),
    ProviderDescriptor(
        name="vllm",
        title="vLLM",
        needs_key=False,
        fields=[
            ProviderField(
                "base_url",
                "Server address",
                secret=False,
                required=False,
                placeholder="http://localhost:8000",
                help="Where `vllm serve` is listening. The /v1 path is added automatically.",
            ),
            ProviderField(
                "api_key",
                "API key (only if the server was started with one)",
                secret=True,
                required=False,
            ),
        ],
        build=_local_server("vllm"),
        blurb="A vLLM server, usually on a Linux machine with an NVIDIA GPU.",
        kind="local",
    ),
    ProviderDescriptor(
        name="lmstudio",
        title="LM Studio",
        needs_key=False,
        fields=[
            ProviderField(
                "base_url",
                "Server address",
                secret=False,
                required=False,
                placeholder="http://localhost:1234",
                help="Where LM Studio's local server is listening (the Developer tab, or "
                "`lms server start`). The /v1 path is added automatically.",
            ),
            ProviderField(
                "api_key",
                "API key (only if the server was started with one)",
                secret=True,
                required=False,
            ),
        ],
        build=_local_server("lmstudio"),
        # The same verified tool-calling pick as Ollama's, under LM Studio's catalog id
        # (lmstudio.ai/models/qwen/qwen3-coder-30b, read 2026-10-25) — download it in-app.
        recommended_model="qwen/qwen3-coder-30b",
        blurb="The LM Studio app's local server, with whatever models you downloaded there.",
        kind="local",
    ),
    # Three more OpenAI-compatible local servers (2026-10-26). Defaults read from each
    # tool's own docs: docs.jan.ai/desktop/api-server (127.0.0.1:1337, /v1 prefix),
    # docs.docker.com/ai/model-runner/api-reference (localhost:12434/engines/v1),
    # mlx-lm's server.py (127.0.0.1:8080). No recommended model: each serves whatever the
    # user loaded, and the picker lists that.
    ProviderDescriptor(
        name="jan",
        title="Jan",
        needs_key=False,
        fields=[
            ProviderField(
                "base_url",
                "Server address",
                secret=False,
                required=False,
                placeholder="http://127.0.0.1:1337",
                help="Where Jan's local API server is listening (Settings ▸ Local API Server ▸ "
                "Start Server). The /v1 path is added automatically.",
            ),
            ProviderField(
                "api_key",
                "API key (only if you set one in Jan's server settings)",
                secret=True,
                required=False,
            ),
        ],
        build=_local_server("jan"),
        blurb="The Jan app's local API server, with the models you downloaded there.",
        kind="local",
    ),
    ProviderDescriptor(
        name="docker",
        title="Docker Model Runner",
        needs_key=False,
        fields=[
            ProviderField(
                "base_url",
                "Server address",
                secret=False,
                required=False,
                placeholder="http://localhost:12434/engines",
                help="Docker Model Runner's TCP address (enable it in Docker Desktop ▸ Settings ▸ "
                "AI, or `docker desktop enable model-runner --tcp 12434`). The /engines/v1 path "
                "is added automatically.",
            ),
            ProviderField(
                "api_key",
                "API key (only if the runner was started with one)",
                secret=True,
                required=False,
            ),
        ],
        build=_local_server("docker"),
        blurb="Models pulled with `docker model pull`, served by Docker Desktop.",
        kind="local",
    ),
    ProviderDescriptor(
        name="mlx",
        title="mlx-lm",
        needs_key=False,
        fields=[
            ProviderField(
                "base_url",
                "Server address",
                secret=False,
                required=False,
                placeholder="http://127.0.0.1:8080",
                help="Where `mlx_lm.server` is listening. It defaults to port 8080, the same as "
                "llama.cpp — if you run both, start one on another port "
                "(`mlx_lm.server --port 8081`). The /v1 path is added automatically.",
            ),
            ProviderField(
                "api_key",
                "API key (only if the server was started with one)",
                secret=True,
                required=False,
            ),
        ],
        build=_local_server("mlx"),
        blurb="An mlx-lm server on Apple silicon (`pip install mlx-lm`, then `mlx_lm.server --model <repo>`).",
        kind="local",
    ),
]

_BY_NAME = {d.name: d for d in DESCRIPTORS}


def _custom_provider_titles(secrets: Any = None) -> dict[str, str]:
    """Return the validated custom-provider index without exposing credentials."""
    if secrets is None:
        return {}
    raw = secrets.get(CUSTOM_PROVIDERS_PROFILE) or {}
    providers = raw.get("providers") if isinstance(raw, dict) else None
    if not isinstance(providers, dict):
        return {}
    return {
        name: title
        for name, title in providers.items()
        if isinstance(name, str)
        and isinstance(title, str)
        and name not in _BY_NAME
        and title.strip()
    }


def _removed_custom_provider_titles(secrets: Any = None) -> dict[str, str]:
    """Routes kept as tombstones after deletion to protect existing sessions."""
    if secrets is None:
        return {}
    raw = secrets.get(CUSTOM_PROVIDERS_PROFILE) or {}
    removed = raw.get("removed") if isinstance(raw, dict) else None
    if not isinstance(removed, dict):
        return {}
    return {
        name: title
        for name, title in removed.items()
        if isinstance(name, str)
        and isinstance(title, str)
        and name not in _BY_NAME
        and title.strip()
    }


def is_custom_provider(name: str, secrets: Any = None) -> bool:
    return name in _custom_provider_titles(secrets)


def is_removed_custom_provider(name: str, secrets: Any = None) -> bool:
    return name in _removed_custom_provider_titles(secrets)


def provider_descriptors(secrets: Any = None) -> list[ProviderDescriptor]:
    custom = [
        _custom_provider_descriptor(name, title)
        for name, title in _custom_provider_titles(secrets).items()
    ]
    return [*DESCRIPTORS, *custom]


def provider_names(secrets: Any = None) -> list[str]:
    return [d.name for d in provider_descriptors(secrets)]


def get_descriptor(name: str, secrets: Any = None) -> Optional[ProviderDescriptor]:
    builtin = _BY_NAME.get(name)
    if builtin is not None:
        return builtin
    title = _custom_provider_titles(secrets).get(name)
    if title:
        return _custom_provider_descriptor(name, title)
    title = _removed_custom_provider_titles(secrets).get(name)
    return _disabled_custom_provider_descriptor(name, title) if title else None


def build_provider_client(
    name: str, profile: dict[str, Any], secrets: Any
) -> ProviderClient:
    """Build a `ProviderClient` for `name` from its stored profile. Unknown → OpenAI default."""
    descriptor = get_descriptor(name, secrets) or _BY_NAME["openai"]
    return descriptor.build(profile or {}, secrets)


def descriptor_configured(d: ProviderDescriptor, profile: dict[str, Any]) -> bool:
    """Whether a provider is usable with the given stored profile. Single-key providers:
    a stored or env key. Multi-field cloud providers (no `api_key` field, e.g. Bedrock):
    every required field present — their actual credentials may be ambient (~/.aws, ADC).
    """
    if d.auth == "oauth":
        # Signed in = a stored token set, or the key a sign-in issued (OpenRouter).
        profile = profile or {}
        return bool(profile.get("tokens") or profile.get("api_key"))
    if not d.needs_key:
        return True  # keyless (Ollama) — usable out of the box
    profile = profile or {}
    if any(f.key == "api_key" for f in d.fields):
        has_key = bool(profile.get("api_key")) or bool(
            d.env_key and os.environ.get(d.env_key)
        )
        # A key alone is not enough where the card also requires a non-secret field
        # (Azure's endpoint): an env key with nowhere to send it is not configured.
        return has_key and all(
            profile.get(f.key) for f in d.fields if f.required and not f.secret
        )
    return all(profile.get(f.key) for f in d.fields if f.required)


def detect_provider(api_key: str) -> Optional[str]:
    """Best-effort provider guess from an API key's shape, for the onboarding auto-detect.
    Returns a known provider name or None. Mirrors the GUI's client-side detection so both agree.
    """
    key = (api_key or "").strip()
    if not key:
        return None
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith("sk-orca-"):
        return "orcarouter"
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("AIza"):
        return "gemini"
    if key.startswith("nvapi-"):
        return "nvidia"
    if key.startswith(("sk-", "sk_")):
        return "openai"
    return None


def _verify_bedrock(fields: dict[str, Any], timeout: float) -> dict[str, Any]:
    """One cheap read-only Bedrock call (list models) with the same explicit → profile →
    ambient credential resolution the provider itself uses."""
    from .bedrock_provider import _session_kwargs

    def get(key: str) -> Optional[str]:
        return (fields.get(key) or "").strip() or None

    # Exactly one auth method is exercised — the one the form has selected. Per-method
    # required fields are checked here so the Test button says what's missing. Do this
    # before importing boto3 so the test suite (and users) get a helpful missing-field
    # message even when the optional `bedrock` extra is not installed.
    method = get("auth_method") or "api_key"
    if method == "api_key" and not (
        get("bedrock_api_key") or os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
    ):
        return {"ok": False, "error": "Enter a Bedrock API key to test."}
    if method == "iam" and not (
        get("aws_access_key_id") and get("aws_secret_access_key")
    ):
        return {"ok": False, "error": "Enter an access key ID and secret access key."}
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        return {
            "ok": False,
            "error": "boto3 is not installed — `pip install 'openworker[bedrock]'`.",
        }
    try:
        if method == "api_key":
            # The key rides the env var (boto3's only bearer channel); bearer then wins
            # over any ambient SigV4 credentials for Bedrock calls.
            if get("bedrock_api_key"):
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = get("bedrock_api_key")
            session_kwargs: dict[str, Any] = {}
        elif method == "profile":
            # Blank profile → the default credential chain (env vars / ~/.aws / role).
            session_kwargs = _session_kwargs(get("aws_profile"), None, None, None)
        else:  # iam
            session_kwargs = _session_kwargs(
                None,
                get("aws_access_key_id"),
                get("aws_secret_access_key"),
                get("aws_session_token"),
            )
        session = boto3.session.Session(**session_kwargs)
        client = session.client(
            "bedrock",
            region_name=get("region"),
            config=Config(connect_timeout=timeout, read_timeout=timeout),
        )
        client.list_foundation_models()
    except Exception as exc:
        kind = exc.__class__.__name__
        if kind == "NoCredentialsError":
            return {
                "ok": False,
                "error": "No AWS credentials found — enter keys or a profile, or run "
                "`aws configure` / `aws sso login` first.",
            }
        if kind == "ProfileNotFound":
            return {"ok": False, "error": f"{exc}"}
        # botocore raises MODELED subclasses of ClientError (class name
        # "AccessDeniedException", not "ClientError"), so detect by the response shape,
        # never the class name — a name check sent every modeled error to the generic
        # fallback and hid the specific message (owner report 2026-08-17).
        code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code", "")
        if code:
            if code in ("UnrecognizedClientException", "InvalidSignatureException"):
                return {"ok": False, "error": "AWS rejected the credentials."}
            if code in ("AccessDeniedException", "AccessDenied"):
                return {
                    "ok": False,
                    "error": (
                        "Credentials work but lack Bedrock access "
                        "(bedrock:ListFoundationModels) — check the key's policy, its "
                        "expiry (short-term keys last up to 12h), and that the region "
                        "matches where the key was created."
                    ),
                }
            if code == "ExpiredTokenException":
                return {"ok": False, "error": "The credentials have expired — generate a new key."}
            return {"ok": False, "error": f"AWS Bedrock returned {code}."}
        return {
            "ok": False,
            "error": f"Couldn't reach AWS Bedrock ({_describe_transport_error(exc)})."
        }
    return {"ok": True}


# Verify probe: countTokens on a stable Gemini model — free (no generation), works with
# plain ADC (the model list/GET endpoints 403/404 under user credentials — checked live
# 2026-07-26), and exercises project + location + API enablement in one call.
_VERTEX_PROBE_MODEL = "gemini-2.5-flash"
_VERTEX_PROBE_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


def _verify_vertex(fields: dict[str, Any], timeout: float) -> dict[str, Any]:
    """One cheap call (countTokens) through the SELECTED auth method: ADC /
    service-account bearer, or the express API key header."""
    import httpx

    from .vertex_provider import load_credentials

    project = (fields.get("project") or "").strip()
    location = (fields.get("location") or "").strip()
    method = (fields.get("auth_method") or "").strip() or (
        "service_account" if (fields.get("service_account_json") or "").strip() else "adc"
    )
    if method == "api_key":
        key = (fields.get("vertex_api_key") or "").strip()
        if not key:
            return {"ok": False, "error": "Enter a Vertex API key to test."}
        try:
            # Express mode is global — no region host, no project in the path.
            resp = httpx.post(
                "https://aiplatform.googleapis.com/v1/publishers/google/models/"
                f"{_VERTEX_PROBE_MODEL}:countTokens",
                headers={"x-goog-api-key": key},
                json=_VERTEX_PROBE_BODY,
                timeout=timeout,
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Couldn't reach Vertex AI ({_describe_transport_error(exc)}).",
            }
        if resp.status_code < 300:
            return {"ok": True}
        if resp.status_code in (401, 403):
            return {"ok": False, "error": "Google rejected the API key."}
        return {"ok": False, "error": f"Vertex AI returned HTTP {resp.status_code}."}
    if method == "service_account" and not (fields.get("service_account_json") or "").strip():
        return {"ok": False, "error": "Paste a service-account JSON to test."}
    try:
        creds = None
        if method == "service_account":
            creds = load_credentials(fields.get("service_account_json"))
        if creds is None:
            import google.auth

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        from google.auth.transport.requests import Request

        creds.refresh(Request())
    except Exception as exc:
        kind = exc.__class__.__name__
        if kind == "DefaultCredentialsError":
            return {
                "ok": False,
                "error": "No Google Cloud credentials found — paste a service-account "
                "JSON, or run `gcloud auth application-default login` first.",
            }
        if kind in ("RefreshError", "MalformedError", "JSONDecodeError", "ValueError"):
            return {"ok": False, "error": "Google rejected the credentials."}
        return {"ok": False, "error": f"Couldn't load Google credentials ({kind})."}
    from .vertex_provider import _regional_host

    try:
        resp = httpx.post(
            f"https://{_regional_host(location)}/v1/projects/{project}"
            f"/locations/{location}/publishers/google/models/"
            f"{_VERTEX_PROBE_MODEL}:countTokens",
            headers={"Authorization": f"Bearer {creds.token}"},
            json=_VERTEX_PROBE_BODY,
            timeout=timeout,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Couldn't reach Vertex AI ({_describe_transport_error(exc)}).",
        }
    if resp.status_code < 300:
        return {"ok": True}
    if resp.status_code in (401, 403):
        return {
            "ok": False,
            "error": "Credentials work but lack Vertex AI access in this project.",
        }
    if resp.status_code == 404:
        return {"ok": False, "error": "Project or location not found on Vertex AI."}
    return {"ok": False, "error": f"Vertex AI returned HTTP {resp.status_code}."}


def _describe_transport_error(exc: Exception) -> str:
    """Turn a verify probe's transport failure into one actionable line.

    The bare class name ("ConnectError") cost a real user round-trips of
    diagnosis while the exception carried the answer all along — TLS
    verification, DNS, refusal and timeout each read differently (#505).
    """
    msg = " ".join(str(exc).split()).rstrip(".")[:200]
    lowered = msg.lower()
    for signature, hint in (
        (
            "certificate_verify_failed",
            "TLS certificate verification failed; the app's CA bundle may be missing or stale",
        ),
        ("ssl:", "TLS handshake failed"),
        ("gaierror", "DNS could not resolve the endpoint"),
        ("nodename", "DNS could not resolve the endpoint"),
        ("name or service", "DNS could not resolve the endpoint"),
        ("getaddrinfo", "DNS could not resolve the endpoint"),
        ("refused", "the endpoint refused the connection"),
        ("timed out", "the connection timed out"),
    ):
        if signature in lowered:
            if not msg:
                return f"{exc.__class__.__name__} — {hint}"
            return f"{exc.__class__.__name__}: {msg} — looks like {hint}"
    if not msg:
        return exc.__class__.__name__
    return f"{exc.__class__.__name__}: {msg}"


def verify_provider_key(
    name: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    fields: Optional[dict[str, Any]] = None,
    timeout: float = 10.0,
    descriptor: Optional[ProviderDescriptor] = None,
) -> dict[str, Any]:
    """Validate a provider's credentials with one cheap call — usually list models.

    Ark's Responses-compatible data plane does not document a `/models` probe, so its Test button
    sends a non-persisted one-token Responses request instead. Callers pass the key directly so a
    user can Test before saving. Never raises; returns {ok, error?}. Multi-field cloud providers
    (Bedrock, Vertex) take their whole form via `fields`; everyone else uses api_key/base_url.
    """
    import httpx

    d = descriptor or _BY_NAME.get(name) or _BY_NAME["openai"]
    key = (api_key or "").strip()
    if d.auth == "oauth":
        # OAuth providers verify from their stored tokens (needs the SecretStore),
        # which only the manager holds — see SessionManager.verify_provider.
        return {"ok": False, "error": f"{d.title} verifies via its sign-in, not a key."}
    if name == "bedrock":
        return _verify_bedrock(fields or {}, timeout)
    if name == "vertex":
        return _verify_vertex(fields or {}, timeout)
    try:
        if name == "anthropic":
            resp = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                timeout=timeout,
            )
        elif name == "gemini":
            resp = httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": key},
                timeout=timeout,
            )
        elif name == "azure":
            # The Azure v1 API's own model list, at the user's resource — never
            # api.openai.com. The key rides as a bearer token, exactly as the OpenAI SDK
            # sends it at run time (the v1 spec takes the key in `authorization` as
            # well as `api-key`), so a passing Test proves the path the sessions use.
            endpoint = normalize_azure_endpoint(
                (fields or {}).get("endpoint") or base_url
            )
            if not endpoint:
                return {"ok": False, "error": "Enter the Azure OpenAI endpoint to test."}
            resp = httpx.get(
                endpoint + "/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=timeout,
            )
            if resp.status_code == 404:
                return {
                    "ok": False,
                    "error": "Reached the host, but no Azure OpenAI v1 API at this "
                    "endpoint — check the resource endpoint.",
                }
        elif name == "ollama":
            base = _normalize_ollama_url(base_url)
            resp = httpx.get(base.rstrip("/") + "/models", timeout=timeout)
        elif name in LOCAL_SERVERS:
            from .local_server import v1_base

            headers = {"Authorization": f"Bearer {key}"} if key else {}
            resp = httpx.get(v1_base(name, base_url) + "/models", headers=headers, timeout=timeout)
        elif name == "nvidia":
            # NIM's /v1/models is PUBLIC: it answers 200 for any key, including an empty
            # one, so the usual list-models probe can't tell a good key from a typo. Auth
            # is enforced only on inference — and only once the model id routes (a bad
            # model 404s before the key is looked at) — so we spend a single token on the
            # recommended model instead. A self-hosted NIM (any other endpoint) serves one
            # model of its own choosing, so the recommended id would 404 there; its
            # /v1/models is behind the key, so the plain listing is the right probe.
            default_base = next(
                (f.default for f in d.fields if f.key == "base_url" and f.default), ""
            )
            base = (base_url or "").strip().rstrip("/") or default_base.rstrip("/")
            if base != default_base.rstrip("/"):
                resp = httpx.get(
                    base + "/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=timeout,
                )
            else:
                resp = httpx.post(
                    base + "/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": d.recommended_model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1,
                    },
                    timeout=timeout,
                )
        elif name in ("ark", "ark-agent-plan-cn"):
            default_base = next(
                (f.default for f in d.fields if f.key == "base_url" and f.default), ""
            )
            base = (base_url or "").strip().rstrip("/") or default_base.rstrip("/")
            resp = httpx.post(
                base + "/responses",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": d.recommended_model,
                    "input": "Reply with OK.",
                    "max_output_tokens": 1,
                    "store": False,
                },
                timeout=timeout,
            )
        else:  # openai + any OpenAI-compatible endpoint (Azure, OpenRouter, vendors, vLLM…)
            default_base = next(
                (f.default for f in d.fields if f.key == "base_url" and f.default), ""
            )
            base = (
                (base_url or "").strip().rstrip("/")
                or default_base.rstrip("/")
                or "https://api.openai.com/v1"
            )
            resp = httpx.get(
                base + "/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=timeout,
            )
            # Reachability of /models isn't enough: a base URL that omits the /v1 path
            # segment (e.g. LM Studio served at http://127.0.0.1:1234) can still answer
            # /models while every completion 404s — the session then hangs silently on
            # an endpoint that "tested" fine. Probe the real completions route too and
            # fail fast with a path-specific message instead (issue #431).
            if resp.status_code < 300:
                route = httpx.get(
                    base + "/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=timeout,
                )
                if route.status_code == 404:
                    return {
                        "ok": False,
                        "error": "Server reachable, but the completions route 404s — the "
                        "endpoint is usually missing a /v1 path segment (e.g. "
                        "http://127.0.0.1:1234/v1).",
                    }
    except Exception as exc:  # DNS/connection/timeout — never let it bubble to a 500
        return {
            "ok": False,
            "error": f"Couldn't reach {d.title} ({_describe_transport_error(exc)}).",
        }

    if resp.status_code < 300:
        return {"ok": True}
    if resp.status_code in (404, 405) and d.name not in _BY_NAME:
        # Chat Completions compatibility does not require the optional model-list
        # endpoint. The gateway was reached, so do not call a usable custom route
        # invalid merely because it cannot provide a catalog.
        return {
            "ok": True,
            "warning": "Endpoint reached, but it does not expose /models. Add a model ID and try a chat to finish checking it.",
        }
    if resp.status_code in (401, 403):
        if name == "ollama":
            return {"ok": False, "error": "Server rejected the request."}
        return {"ok": False, "error": "Invalid API key."}
    if resp.status_code == 404 and name == "ollama":
        return {
            "ok": False,
            "error": "Reached the server, but no OpenAI-compatible /v1 API there.",
        }
    return {"ok": False, "error": f"{d.title} returned HTTP {resp.status_code}."}
