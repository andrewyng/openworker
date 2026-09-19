"""Friendly translation of model access + quota + authentication failures.

The picker now defaults to brand-new flagships (GPT-5.6 Sol, Claude Fable 5), and not every
account can use them: OpenAI is still rolling GPT-5.6 out per-organization, and both vendors
reject calls once quota/credits run out. Those failures arrive as terse SDK exceptions
wrapping JSON error bodies; this maps the well-known shapes to one actionable sentence.
Anything unrecognized returns None and the caller surfaces the raw error unchanged.

Matching is on the error BODY text (error codes/types), not just HTTP status — a 404 also
means "wrong base_url" and a 429 also means "slow down", and neither of those should be
dressed up as an access problem.
"""

from __future__ import annotations

from typing import Optional

# Error-body markers, verbatim from the vendors' error codes/messages:
# OpenAI: {"error": {"code": "model_not_found", "message": "The model `X` does not exist or
#   you do not have access to it."}} (404/403) and {"code": "insufficient_quota"} (429).
# Anthropic: {"type": "not_found_error", "message": "model: X"} (404),
#   {"type": "permission_error"} (403), and "credit balance is too low" (400).
_NO_ACCESS = (
    "model_not_found",
    "does not exist or you do not have access",
    "does not have access to model",
    "permission_error",
    "permission denied",
)
_NO_QUOTA = (
    "insufficient_quota",
    "exceeded your current quota",
    "credit balance is too low",
    "billing hard limit",
)

# Authentication failure markers — 401/403 with an invalid-key code, not an access-to-model
# denial. Qwen/DashScope returns {"code": "InvalidApiKey", "message": "Invalid API-key
# provided."}; the OpenAI SDK surfaces this as "Error code: 401 — ... InvalidApiKey ...".
# Other compat vendors (DeepSeek, Kimi, etc.) use similar "invalid_api_key" phrasing.
_AUTH_FAILURE = (
    "invalid_api_key",
    "invalid api-key",
    "invalid api key",
    "invalidapikey",
    "incorrect api key provided",
    "authentication_error",
    "authenticationerror",
)


def _auth_error_message(model: str, exc: Exception) -> Optional[str]:
    """Provider-specific guidance for API-key authentication failures, or None.

    Qwen/DashScope keys obtained from the China mainland portal (qwencloud.com) only work
    against the China endpoint, while the international portal keys only work against the
    international endpoint — a mismatch produces a 401 InvalidApiKey even though the key
    itself is valid. We detect the Qwen provider from the model prefix and surface that
    specific guidance; other compat vendors get a generic "check your key" message.
    """
    text = str(exc).lower()
    if not any(marker in text for marker in _AUTH_FAILURE):
        return None

    # Qwen models carry a "qwen:" provider prefix in the router; bare model names
    # starting with "qwen" also indicate the DashScope backend.
    provider = model.split(":", 1)[0].lower() if ":" in model else ""
    bare = model.split(":", 1)[-1].lower() if ":" in model else model.lower()

    if provider == "qwen" or bare.startswith("qwen"):
        return (
            f"Qwen rejected your API key for {model}. Keys from the China mainland portal "
            "(qwencloud.com) require the China endpoint "
            "https://dashscope.aliyuncs.com/compatible-mode/v1, while international keys "
            "require https://dashscope-intl.aliyuncs.com/compatible-mode/v1 — check that "
            "your endpoint in Settings ▸ Models matches where your key was issued. "
            "If the endpoint is correct, verify the key is active and has no extra spaces."
        )

    return (
        f"The provider rejected your API key for {model} — check that the key in "
        "Settings ▸ Models is correct, active, and has no leading or trailing spaces."
    )


def friendly_model_error(model: str, exc: Exception) -> Optional[str]:
    """One actionable sentence for "your account can't use this model" failures, or None."""
    text = str(exc).lower()
    no_access = (
        f"Your account doesn't have access to {model} — new models can roll out "
        "gradually or require a plan upgrade. Pick a different model, or check "
        "the provider's console for availability."
    )
    if any(marker in text for marker in _NO_QUOTA):
        return (
            f"Your account is out of quota for {model} — add credits or raise the limit "
            "in the provider's billing console, or pick a different model."
        )
    if any(marker in text for marker in _NO_ACCESS):
        return no_access
    # Anthropic's 404 body is just "model: <id>" under type not_found_error; require both
    # halves so unrelated 404s (bad base_url, deleted resource) keep their raw message.
    if "not_found_error" in text and f"model: {model.split(':')[-1].lower()}" in text:
        return no_access
    # Authentication failures (401 InvalidApiKey) — checked last so a 403 permission_error
    # (which is about model access, not key validity) is caught by _NO_ACCESS above.
    auth_msg = _auth_error_message(model, exc)
    if auth_msg:
        return auth_msg
    return None
