"""Friendly translation of model access + quota failures.

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

import re
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

_RATE_LIMIT = (
    "rate_limit_reached",
    "rate limit",
    "max rpm",
    "too many requests",
)


def rate_limit_retry_after(exc: Exception) -> Optional[int]:
    """Return a small, safe provider backoff for a transient 429, if present.

    Quota exhaustion also arrives as a 429, but retrying it only creates duplicate
    notices and burns a request. The returned delay is capped to keep an attended
    chat responsive; longer vendor windows remain an explicit Retry for the user.
    """
    text = str(exc).lower()
    if any(marker in text for marker in _NO_QUOTA):
        return None
    if "429" not in text and not any(marker in text for marker in _RATE_LIMIT):
        return None
    if not any(marker in text for marker in _RATE_LIMIT):
        return None
    match = re.search(
        r"(?:try again|retry)(?:\s+(?:in|after))?\s+(\d+)\s*(?:s|sec(?:ond)?s?)?",
        text,
    )
    delay = int(match.group(1)) if match else 1
    return max(1, min(delay, 10))


def friendly_model_error(model: str, exc: Exception) -> Optional[str]:
    """One actionable sentence for "your account can't use this model" failures, or None."""
    text = str(exc).lower()
    if rate_limit_retry_after(exc) is not None:
        return "Rate limit reached."
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
    return None
