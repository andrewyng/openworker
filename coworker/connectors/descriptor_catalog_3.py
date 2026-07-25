"""Connector descriptor catalog partition; assembled by descriptors.py."""

from .descriptor_core import (
    ConnectorDescriptor,
    Field,
    _validate_amplitude,
    _validate_apollo,
    _validate_hunter,
    _validate_mixpanel,
    _validate_posthog,
)

DESCRIPTORS: list[ConnectorDescriptor] = [
    ConnectorDescriptor(
        name="posthog",
        title="PostHog",
        icon="◫",
        blurb="Query product analytics: events, funnels, saved insights.",
        auth="api_token",
        two_way=False,
        fields=[
            Field(
                "base_url",
                "PostHog URL",
                required=False,
                help="Leave empty for US cloud; set for EU cloud or self-hosted.",
                placeholder="https://us.posthog.com",
            ),
            Field(
                "api_key",
                "Personal API key",
                secret=True,
                help="Settings → Personal API keys (read access is enough).",
                placeholder="phx_…",
            ),
            Field(
                "project_id",
                "Project ID",
                help="Settings → Project → Project ID. Add more projects as extra accounts.",
            ),
        ],
        instructions=[
            "In PostHog, open Settings → Personal API keys and create a key.",
            "Copy your Project ID from Settings → Project.",
            "One project per account — connect again to add another project.",
        ],
        validate=_validate_posthog,
        brand_color="#f54e00",
        logo="posthog",
        account_field="project_id",
    ),
    ConnectorDescriptor(
        name="mixpanel",
        title="Mixpanel",
        icon="◭",
        blurb="Query Mixpanel events and segmentation.",
        auth="api_token",
        two_way=False,
        fields=[
            Field("username", "Service account username", secret=False),
            Field("secret", "Service account secret", secret=True),
            Field(
                "project_id",
                "Project ID",
                help="Add more projects as extra accounts.",
            ),
        ],
        instructions=[
            "In Mixpanel, open Organization Settings → Service Accounts and create one.",
            "Copy the username, the secret, and your Project ID (Project Settings).",
        ],
        validate=_validate_mixpanel,
        brand_color="#7856ff",
        logo="mixpanel",
        account_field="project_id",
    ),
    ConnectorDescriptor(
        name="amplitude",
        title="Amplitude",
        icon="∿",
        blurb="Query Amplitude charts data: active users, event totals.",
        auth="api_token",
        two_way=False,
        fields=[
            Field(
                "api_key", "API key", secret=True, help="Project Settings → API Keys."
            ),
            Field("secret_key", "Secret key", secret=True),
        ],
        instructions=[
            "In Amplitude, open Settings → Projects → your project → API Keys.",
            "Copy the API key and secret key. One project per account.",
        ],
        validate=_validate_amplitude,
        brand_color="#1e61f0",
        logo="amplitude",
        account_field="@identity",
    ),
    ConnectorDescriptor(
        name="apollo",
        title="Apollo.io",
        icon="☄",
        blurb="Enrich people and companies; search the B2B database.",
        auth="api_token",
        two_way=False,
        fields=[
            Field(
                "api_key", "API key", secret=True, help="Settings → Integrations → API."
            ),
            Field(
                "label",
                "Account label",
                required=False,
                help="Name this account (used if you connect more than one).",
                placeholder="work",
            ),
        ],
        instructions=[
            "In Apollo, open Settings → Integrations → API and create an API key.",
            "Enrichment and search endpoints require a paid Apollo plan.",
        ],
        validate=_validate_apollo,
        brand_color="#fbbf24",
        logo="apollo",
        account_field="@identity",
    ),
    ConnectorDescriptor(
        name="hunter",
        title="Hunter",
        icon="✉",
        blurb="Find and verify professional email addresses by domain.",
        auth="api_token",
        two_way=False,
        fields=[
            Field(
                "api_key", "API key", secret=True, help="hunter.io → API → API keys."
            ),
        ],
        instructions=[
            "In Hunter, open API → API keys and copy your key.",
        ],
        validate=_validate_hunter,
        brand_color="#fa5320",
        logo="hunter",
        account_field="@identity",
    ),
    ConnectorDescriptor(
        name="pagerduty",
        title="PagerDuty",
        icon="◔",
        blurb="See who's on-call and review active incidents before paging.",
        auth="none",
        two_way=False,
        fields=[],
        instructions=[],
        available=False,
        brand_color="#06ac38",
        logo="pagerduty",
    ),
]
