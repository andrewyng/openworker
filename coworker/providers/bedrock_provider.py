"""AWS Bedrock provider — Claude via AWS IAM credentials.

Uses the `anthropic` SDK's built-in `AnthropicBedrock` client, which handles all AWS
credential resolution (profiles, SSO, env vars, IAM roles) through the standard boto3
credential chain. The Messages API surface is identical to direct Anthropic, so this
subclass only swaps the client construction — all message/tool conversion, streaming,
and thinking logic is inherited from `AnthropicProvider`.

Auth modes (mutually exclusive, first wins):
1. Explicit access key + secret key (+ optional session token for temporary creds),
   pasted in Settings.
2. AWS profile name (reads from ~/.aws/credentials or ~/.aws/config, including SSO).
3. Default credential chain (env vars, instance role, ECS task role, etc.).
"""

from __future__ import annotations

from typing import Any, Optional

from .anthropic_provider import AnthropicProvider


# Lazy import to avoid pulling boto3/botocore at module load time.
AnthropicBedrock: Any = None


def _load_anthropic_bedrock():
    global AnthropicBedrock
    if AnthropicBedrock is None:
        from anthropic import AnthropicBedrock as _AB

        AnthropicBedrock = _AB
    return AnthropicBedrock


class BedrockProvider(AnthropicProvider):
    """Anthropic Claude via AWS Bedrock — IAM credentials, no API key."""

    def __init__(
        self,
        *,
        aws_profile: Optional[str] = None,
        aws_region: str = "us-east-1",
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        aws_session_token: Optional[str] = None,
        secrets: Any = None,
        thinking_budget: Optional[int] = None,
    ):
        # Initialize the parent without an API key — Bedrock uses IAM auth.
        # Pass a placeholder so the parent doesn't complain at build time (key
        # resolution is deferred to _ensure_client, which we override).
        super().__init__(
            api_key="bedrock-iam-auth",
            secrets=secrets,
            thinking_budget=thinking_budget,
        )
        self._aws_profile = aws_profile
        self._aws_region = aws_region
        self._aws_access_key_id = aws_access_key_id
        self._aws_secret_access_key = aws_secret_access_key
        self._aws_session_token = aws_session_token
        # Reset the client so our _ensure_client builds the Bedrock variant.
        self._client = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            self._build_client()
        return self._client

    def _build_client(self) -> None:
        AB = _load_anthropic_bedrock()

        kwargs: dict[str, Any] = {"aws_region": self._aws_region}

        # Auth modes are mutually exclusive (first wins). Explicit credentials take
        # precedence; when they're set we do NOT also pass a profile, or the SDK's
        # precedence between the two would be undefined.
        if self._aws_access_key_id and self._aws_secret_access_key:
            # SDK uses aws_access_key / aws_secret_key (not the boto3-style names).
            kwargs["aws_access_key"] = self._aws_access_key_id
            kwargs["aws_secret_key"] = self._aws_secret_access_key
            # Temporary credentials (SSO / assumed roles, key id starting ASIA…)
            # require the session token; long-lived keys leave it None.
            kwargs["aws_session_token"] = self._aws_session_token
        else:
            # Resolve credentials via a fresh boto3 session every time so we always
            # pick up rotated/refreshed tokens from disk. AnthropicBedrock's own
            # aws_profile path can cache stale credentials within the same process.
            import boto3

            session_kwargs: dict[str, Any] = {}
            if self._aws_profile:
                session_kwargs["profile_name"] = self._aws_profile
            session = boto3.Session(**session_kwargs)
            creds = session.get_credentials()
            if creds:
                frozen = creds.get_frozen_credentials()
                kwargs["aws_access_key"] = frozen.access_key
                kwargs["aws_secret_key"] = frozen.secret_key
                if frozen.token:
                    kwargs["aws_session_token"] = frozen.token
            else:
                # No credentials found — let the SDK try (will likely fail with a
                # clear error about missing credentials).
                kwargs["aws_profile"] = self._aws_profile

        self._client = AB(**kwargs)

    def _is_expired_error(self, exc: Exception) -> bool:
        """Check if an exception indicates expired AWS credentials."""
        msg = str(exc).lower()
        return any(s in msg for s in ("expired", "security token", "403"))

    def complete(self, *, model, messages, tools=None, **settings):
        try:
            return super().complete(model=model, messages=messages, tools=tools, **settings)
        except Exception as exc:
            if self._is_expired_error(exc):
                # Rebuild client to pick up refreshed credentials from disk.
                self._client = None
                self._build_client()
                try:
                    return super().complete(
                        model=model, messages=messages, tools=tools, **settings
                    )
                except Exception:
                    self._client = None
                    raise RuntimeError(
                        "AWS credentials have expired. Please re-authenticate "
                        "in your terminal and click Retry."
                    ) from None
            raise

    def stream(self, *, model, messages, tools=None, **settings):
        try:
            yield from super().stream(model=model, messages=messages, tools=tools, **settings)
        except Exception as exc:
            if self._is_expired_error(exc):
                self._client = None
                self._build_client()
                try:
                    yield from super().stream(
                        model=model, messages=messages, tools=tools, **settings
                    )
                except Exception:
                    self._client = None
                    raise RuntimeError(
                        "AWS credentials have expired. Please re-authenticate "
                        "in your terminal and click Retry."
                    ) from None
            raise
