"""OpenCode Zen / Go contract — verified against upstream API documentation.

This module records every detail of the OpenCode API contract so tests and provider
code can reference a shared source of truth rather than repeating literals.

Verified against the public OpenCode Zen and Go documentation on 2026-07-31:

- https://opencode.ai/docs/zen/
- https://opencode.ai/docs/go/

OpenCode exposes two independent OpenAI-compatible endpoints:

- Zen (``https://opencode.ai/zen/v1/``) — the hosted credit-tier aggregator.
- Go  (``https://opencode.ai/zen/go/v1/``) — the flat-rate subscription tier.

PR #110 treats them as two independent providers (``opencode_zen`` / ``opencode_go``):
each stores its own api_key on its own ``provider:<name>`` SecretStore profile, routes
through its own endpoint, and only *shares* the ``OPENCODE_API_KEY`` environment-var
fallback. There is deliberately NO canonical ``provider:opencode`` shared profile and
no shared configured state.

Known model caveats (conservative by design):

- Capabilities: tools, parallel tool calls and streaming are verified on the shared
  OpenAI-compatible endpoint; vision and PDF input are NOT verified; reasoning is
  supported via DeepSeek-style ``reasoning_content``.
- Zen Free models are time-limited (availability checked 2026-07-31), free of charge,
  and Zen-ONLY (no Go equivalent).
- This first PR exposes only the documented OpenAI-compatible ``/chat/completions``
  compatibility slice. ``/responses``, Anthropic-compatible ``/messages``, and
  Gemini-specific endpoints are intentionally not implemented or selectable here.
"""

# -- base URLs ------------------------------------------------------------------
# Note the trailing slashes: these MUST match the registry's prefilled `base_url`
# field defaults exactly (registry.py `_compat` entries for opencode_zen/opencode_go).
OPCODE_GO_ENDPOINT = "https://opencode.ai/zen/go/v1/"
OPCODE_ZEN_ENDPOINT = "https://opencode.ai/zen/v1/"

# -- auth -----------------------------------------------------------------------
# Header: Authorization: Bearer <key>
# Key source: https://opencode.ai/auth
# Env var: OPENCODE_API_KEY — shared fallback for BOTH independent providers
# (each provider's own stored profile key still wins over this env var).
SHARED_ENV_KEY = "OPENCODE_API_KEY"

# -- verification ---------------------------------------------------------------
# GET <endpoint>/models with Bearer auth returns 200 on success, 401/403 on bad key.
VERIFY_PATH = "/models"

from dataclasses import dataclass

# -- model-to-transport mapping -------------------------------------------------
# Model rosters are auto-derived from the matrix (coworker.providers.matrix.MATRIX),
# which is the canonical source of truth. The helpers below extract the catalog
# rather than hardcoding snapshots that would drift.

TRANSPORT_OPENAI = "openai"  # OpenAI SDK chat.completions
TRANSPORT_ANTHROPIC = "anthropic"  # Anthropic SDK messages (reserved for future use)


@dataclass(frozen=True)
class OpenCodeModel:
    model_id: str
    label: str
    tier: str
    free: bool = False
    transport: str = TRANSPORT_OPENAI
    profile: str = "chat.completions"
    data_retention_notice: str | None = None
    recommendation_priority: int | None = None


def _build_catalog():
    from coworker.providers.matrix import MATRIX

    entries = []
    for mid, mat_entry in MATRIX.items():
        if mid.startswith("opencode_zen:"):
            bare = mid.split(":", 1)[1]
            is_free = any(
                token in bare.lower() for token in ("-free",)
            )
            entries.append(OpenCodeModel(
                model_id=bare,
                label=mat_entry.label,
                tier="zen",
                free=is_free,
                recommendation_priority=None,
            ))
        elif mid.startswith("opencode_go:"):
            bare = mid.split(":", 1)[1]
            entries.append(OpenCodeModel(
                model_id=bare,
                label=mat_entry.label,
                tier="go",
                recommendation_priority=0 if bare == "kimi-k3" else None,
            ))
    return tuple(entries)


OPEN_CODE_CATALOG = _build_catalog()
OPEN_CODE_RECOMMENDED = {
    f"opencode_{tier}": next(
        (x.model_id for x in OPEN_CODE_CATALOG if x.tier == tier and x.recommendation_priority == 0),
        None,
    )
    for tier in ("zen", "go")
}
OPEN_CODE_MODELS = {
    tier: frozenset(x.model_id for x in OPEN_CODE_CATALOG if x.tier == tier)
    for tier in ("zen", "go")
}
COMMON_CHAT_COMPLETIONS_MODELS = frozenset(
    x.model_id for x in OPEN_CODE_CATALOG
    if not x.free and x.tier == "zen"
    and any(y.model_id == x.model_id and y.tier == "go" for y in OPEN_CODE_CATALOG)
)
ZEN_MODELS = frozenset(x.model_id for x in OPEN_CODE_CATALOG if x.tier == "zen" and not x.free)
GO_CHAT_COMPLETIONS_MODELS = frozenset(x.model_id for x in OPEN_CODE_CATALOG if x.tier == "go")
GO_MODEL_TRANSPORT = {x.model_id: x.transport for x in OPEN_CODE_CATALOG if x.tier == "go"}
ZEN_FREE_MODELS = {x.model_id: x.transport for x in OPEN_CODE_CATALOG if x.tier == "zen" and x.free}
ZEN_CHAT_COMPLETIONS_MODELS = OPEN_CODE_MODELS["zen"]

UNSUPPORTED_TRANSPORTS = {
    "responses": "Deferred: OpenCode Zen/Go /responses models are not part of this first PR.",
    "messages": "Deferred: Anthropic-compatible /messages models are not part of this first PR.",
    "gemini": "Deferred: Gemini model-specific endpoints are not part of this first PR.",
}

# -- capabilities (conservative) ------------------------------------------------
# Tools: verified
# Parallel tool calls: verified
# Streaming: verified
# Vision: not verified on shared endpoint
# PDF: not verified on shared endpoint
# Reasoning: supported (DeepSeek-style reasoning_content)

# -- streaming event format -----------------------------------------------------
# OpenAI-compat: SSE with standard delta choices (all exposed first-PR models use this transport).
# Anthropic-compat SSE is reserved for future use.
