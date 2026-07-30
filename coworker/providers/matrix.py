"""The curated model matrix — the only models we actively suggest, label, and vouch for.

Keyed by the FULL routed id, exactly as the ProviderRouter receives it — including reseller
"ugly names" like ``together:zai-org/GLM-5.2`` (bare ids route to the OpenAI default). Each
entry carries the UI display label and the model's capabilities, making this the single
source of truth the capability probe and the GUI's pickers read from.

Deliberately SMALL (owner call, 2026-07-04): current-generation, agent-capable (tool-calling)
models only. It is not user-editable — users can still add any custom model string, which
falls back to the conservative heuristics in ``capabilities.py`` at their own risk of
degraded results. Ids verified against vendor/reseller catalogs on 2026-07-04; refresh the
reseller rows when catalogs rotate (they rename on every model generation).

Context windows (``context_window``, tokens) feed the GUI's context-fill meter. Entries
where the vendor spec wasn't re-checked stay ``None`` — the meter simply hides rather than
showing a made-up denominator. Values entered 2026-07-28 from vendor docs; verify alongside
the id refresh.

Resellers: Together + Fireworks + OpenRouter. TODO: add Groq entries here AND its
descriptor in ``registry.py`` once the current provider surface is tested — deliberately
deferred to bound how much needs verifying at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .base import ModelCapabilities

_AGENTIC = ModelCapabilities(
    tools=True, vision=False, parallel_tool_calls=True, streaming=True
)
# The native three (OpenAI, Anthropic, Gemini) all take PDFs directly; every
# OpenAI-compatible vendor and reseller in the matrix does not (their chat APIs have
# no inline file part — checked 2026-07-17), so those fall back via pdf_support.py.
_AGENTIC_VISION = ModelCapabilities(
    tools=True, vision=True, pdf=True, parallel_tool_calls=True, streaming=True
)


@dataclass(frozen=True)
class ModelEntry:
    label: str  # UI display name, e.g. "GLM-5.2 · via Together"
    caps: ModelCapabilities = _AGENTIC
    # Max context length in tokens (prompt side), for the GUI's context-fill meter.
    # None = not verified against the vendor spec yet; the meter hides.
    context_window: Optional[int] = None


MATRIX: dict[str, ModelEntry] = {
    # -- first-party ------------------------------------------------------------
    # GPT-5.6 (2026-07-09): number = generation, Sol/Terra/Luna = capability tiers.
    # Bare "gpt-5.6" aliases to Sol server-side; we list the explicit tier ids only.
    # Rolling out — accounts without access get a friendly error (providers/errors.py).
    "gpt-5.6-sol": ModelEntry("GPT-5.6 Sol · OpenAI", _AGENTIC_VISION, 400_000),
    "gpt-5.6-terra": ModelEntry("GPT-5.6 Terra · OpenAI", _AGENTIC_VISION, 400_000),
    "gpt-5.6-luna": ModelEntry("GPT-5.6 Luna · OpenAI", _AGENTIC_VISION, 400_000),
    "gpt-5.5": ModelEntry("GPT-5.5 · OpenAI", _AGENTIC_VISION, 400_000),
    # Fable 5 (2026-06-09) is GA; its Mythos 5 sibling is approved-orgs-only, so it
    # stays out of a picker meant for the public.
    "anthropic:claude-fable-5": ModelEntry(
        "Claude Fable 5 · Anthropic", _AGENTIC_VISION, 1_000_000
    ),
    "anthropic:claude-opus-4-8": ModelEntry(
        "Claude Opus 4.8 · Anthropic", _AGENTIC_VISION, 200_000
    ),
    "anthropic:claude-sonnet-4-6": ModelEntry(
        "Claude Sonnet 4.6 · Anthropic", _AGENTIC_VISION, 200_000
    ),
    "anthropic:claude-haiku-4-5": ModelEntry(
        "Claude Haiku 4.5 · Anthropic", _AGENTIC_VISION, 200_000
    ),
    # Gemini 3 (thought signatures required in tool loops — carried via the `_gemini`
    # message sidecar, see gemini_provider.py; ids from the vendor catalog 2026-07-22).
    "gemini:gemini-3.1-pro-preview": ModelEntry(
        "Gemini 3.1 Pro · Google", _AGENTIC_VISION, 1_048_576
    ),
    "gemini:gemini-3.6-flash": ModelEntry(
        "Gemini 3.6 Flash · Google", _AGENTIC_VISION, 1_048_576
    ),
    "gemini:gemini-2.5-pro": ModelEntry(
        "Gemini 2.5 Pro · Google", _AGENTIC_VISION, 1_048_576
    ),
    "gemini:gemini-2.5-flash": ModelEntry(
        "Gemini 2.5 Flash · Google", _AGENTIC_VISION, 1_048_576
    ),
    # -- direct OpenAI-compatible vendors ----------------------------------------
    # Muse Spark (Meta Model API, public preview 2026-07-09): multimodal + tools via
    # their OpenAI-compat surface. Vision yes; PDFs unverified over compat — falls
    # back via pdf_support.py like the other compat vendors.
    "meta:muse-spark-1.1": ModelEntry(
        "Muse Spark 1.1 · Meta",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
    ),
    # Compat-vendor windows re-verified 2026-07-29/30 against vendor docs (and
    # cross-checked on Alibaba Bailian model catalog where the same ids appear):
    #   Z.AI GLM-5.2 → 1M (docs.z.ai/guides/llm/glm-5.2)
    #   DeepSeek V4 → 1M (api-docs.deepseek.com pricing)
    #   Kimi K2.6 / K2.7 Code → 256k / 262_144 (platform.kimi.ai)
    #   MiniMax M3 → 1M; M2.5 → 204_800 (platform.minimax.io text-generation)
    #   Qwen 3.7 → 1M (help.aliyun.com model catalog / text-generation)
    "zai:glm-5.2": ModelEntry("GLM-5.2 · Z AI", _AGENTIC, 1_000_000),
    "deepseek:deepseek-v4-flash": ModelEntry(
        "DeepSeek V4 Flash · DeepSeek", _AGENTIC, 1_000_000
    ),
    "deepseek:deepseek-v4-pro": ModelEntry(
        "DeepSeek V4 Pro · DeepSeek", _AGENTIC, 1_000_000
    ),
    "kimi:kimi-k2.7-code": ModelEntry("Kimi K2.7 Code · Moonshot", _AGENTIC, 256_000),
    "kimi:kimi-k2.6": ModelEntry("Kimi K2.6 · Moonshot", _AGENTIC, 256_000),
    "minimax:MiniMax-M3": ModelEntry("MiniMax M3 · MiniMax", _AGENTIC, 1_000_000),
    "minimax:MiniMax-M2.5": ModelEntry("MiniMax M2.5 · MiniMax", _AGENTIC, 204_800),
    # Qwen / Alibaba Model Studio (verified 2026-07-30 against
    # help.aliyun.com/zh|en/model-studio/text-generation-model). Rolling ids
    # only — dated snapshots stay as user-typed custom strings. Includes Token
    # Plan–only qwen3.8-max-preview. qwen-long has no function calling (doc), so
    # caps.tools=False; still metered for long-doc sessions.
    "qwen:qwen3.8-max-preview": ModelEntry(
        "Qwen3.8 Max Preview · Alibaba", _AGENTIC, 1_000_000
    ),
    "qwen:qwen3.7-max": ModelEntry("Qwen3.7 Max · Alibaba", _AGENTIC, 1_000_000),
    "qwen:qwen3.7-plus": ModelEntry("Qwen3.7 Plus · Alibaba", _AGENTIC, 1_000_000),
    "qwen:qwen3.7-flash": ModelEntry("Qwen3.7 Flash · Alibaba", _AGENTIC, 1_000_000),
    "qwen:qwen3.6-max-preview": ModelEntry(
        "Qwen3.6 Max Preview · Alibaba", _AGENTIC, 256_000
    ),
    "qwen:qwen3.6-plus": ModelEntry("Qwen3.6 Plus · Alibaba", _AGENTIC, 1_000_000),
    "qwen:qwen3.6-flash": ModelEntry("Qwen3.6 Flash · Alibaba", _AGENTIC, 1_000_000),
    "qwen:qwen3.5-plus": ModelEntry("Qwen3.5 Plus · Alibaba", _AGENTIC, 1_000_000),
    "qwen:qwen3.5-flash": ModelEntry("Qwen3.5 Flash · Alibaba", _AGENTIC, 1_000_000),
    "qwen:qwen3.5-397b-a17b": ModelEntry(
        "Qwen3.5 397B · Alibaba", _AGENTIC, 256_000
    ),
    "qwen:qwen3.5-122b-a10b": ModelEntry(
        "Qwen3.5 122B · Alibaba", _AGENTIC, 256_000
    ),
    "qwen:qwen3.5-35b-a3b": ModelEntry("Qwen3.5 35B · Alibaba", _AGENTIC, 256_000),
    "qwen:qwen3.5-27b": ModelEntry("Qwen3.5 27B · Alibaba", _AGENTIC, 256_000),
    "qwen:qwen3-max": ModelEntry("Qwen3 Max · Alibaba", _AGENTIC, 256_000),
    "qwen:qwen3-max-preview": ModelEntry(
        "Qwen3 Max Preview · Alibaba", _AGENTIC, 256_000
    ),
    "qwen:qwen3-235b-a22b": ModelEntry("Qwen3 235B · Alibaba", _AGENTIC, 256_000),
    "qwen:qwen3-235b-a22b-thinking-2507": ModelEntry(
        "Qwen3 235B Thinking · Alibaba", _AGENTIC, 256_000
    ),
    "qwen:qwen3-235b-a22b-instruct-2507": ModelEntry(
        "Qwen3 235B Instruct · Alibaba", _AGENTIC, 256_000
    ),
    "qwen:qwen3-next-80b-a3b-thinking": ModelEntry(
        "Qwen3 Next 80B Thinking · Alibaba", _AGENTIC, 256_000
    ),
    "qwen:qwen3-next-80b-a3b-instruct": ModelEntry(
        "Qwen3 Next 80B Instruct · Alibaba", _AGENTIC, 256_000
    ),
    "qwen:qwen3-32b": ModelEntry("Qwen3 32B · Alibaba", _AGENTIC, 256_000),
    "qwen:qwen3-30b-a3b": ModelEntry("Qwen3 30B · Alibaba", _AGENTIC, 256_000),
    "qwen:qwen3-30b-a3b-thinking-2507": ModelEntry(
        "Qwen3 30B Thinking · Alibaba", _AGENTIC, 256_000
    ),
    "qwen:qwen3-30b-a3b-instruct-2507": ModelEntry(
        "Qwen3 30B Instruct · Alibaba", _AGENTIC, 256_000
    ),
    "qwen:qwen3-14b": ModelEntry("Qwen3 14B · Alibaba", _AGENTIC, 256_000),
    "qwen:qwen3-8b": ModelEntry("Qwen3 8B · Alibaba", _AGENTIC, 256_000),
    "qwen:qwen3-4b": ModelEntry("Qwen3 4B · Alibaba", _AGENTIC, 256_000),
    "qwen:qwen3-1.7b": ModelEntry("Qwen3 1.7B · Alibaba", _AGENTIC, 256_000),
    "qwen:qwen3-0.6b": ModelEntry("Qwen3 0.6B · Alibaba", _AGENTIC, 256_000),
    "qwen:qwen3-coder-plus": ModelEntry(
        "Qwen3 Coder Plus · Alibaba", _AGENTIC, 1_000_000
    ),
    "qwen:qwen3-coder-flash": ModelEntry(
        "Qwen3 Coder Flash · Alibaba", _AGENTIC, 1_000_000
    ),
    "qwen:qwen3-coder-next": ModelEntry(
        "Qwen3 Coder Next · Alibaba", _AGENTIC, 256_000
    ),
    "qwen:qwen3-coder-480b-a35b-instruct": ModelEntry(
        "Qwen3 Coder 480B · Alibaba", _AGENTIC, 256_000
    ),
    "qwen:qwen3-coder-30b-a3b-instruct": ModelEntry(
        "Qwen3 Coder 30B · Alibaba", _AGENTIC, 256_000
    ),
    "qwen:qwen-plus": ModelEntry("Qwen-Plus · Alibaba", _AGENTIC, 1_000_000),
    "qwen:qwen-flash": ModelEntry("Qwen-Flash · Alibaba", _AGENTIC, 1_000_000),
    "qwen:qwen-turbo": ModelEntry("Qwen-Turbo · Alibaba", _AGENTIC, 1_000_000),
    "qwen:qwen-max": ModelEntry("Qwen-Max · Alibaba", _AGENTIC, 128_000),
    "qwen:qwq-plus": ModelEntry("QwQ-Plus · Alibaba", _AGENTIC, 128_000),
    "qwen:qwen-long": ModelEntry(
        "Qwen-Long · Alibaba",
        ModelCapabilities(
            tools=False, vision=False, parallel_tool_calls=False, streaming=True
        ),
        10_000_000,
    ),
    "qwen:qwen-long-latest": ModelEntry(
        "Qwen-Long Latest · Alibaba",
        ModelCapabilities(
            tools=False, vision=False, parallel_tool_calls=False, streaming=True
        ),
        10_000_000,
    ),
    "xai:grok-4.3": ModelEntry("Grok 4.3 · xAI", _AGENTIC, 256_000),
    "mistral:mistral-large-latest": ModelEntry(
        "Mistral Large · Mistral", _AGENTIC, 128_000
    ),
    # -- resellers (their model namespaces, verbatim) -----------------------------
    "together:thinkingmachines/Inkling": ModelEntry("Inkling · via Together"),
    "together:zai-org/GLM-5.2": ModelEntry("GLM-5.2 · via Together", _AGENTIC, 1_000_000),
    "together:moonshotai/Kimi-K2.7-Code": ModelEntry(
        "Kimi K2.7 Code · via Together", _AGENTIC, 256_000
    ),
    "together:moonshotai/Kimi-K2.6": ModelEntry(
        "Kimi K2.6 · via Together", _AGENTIC, 256_000
    ),
    "together:deepseek-ai/DeepSeek-V4-Pro": ModelEntry(
        "DeepSeek V4 Pro · via Together", _AGENTIC, 1_000_000
    ),
    "together:meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8": ModelEntry(
        "Llama 4 Maverick · via Together", _AGENTIC, 1_000_000
    ),
    "fireworks:accounts/fireworks/models/glm-5p2": ModelEntry(
        "GLM-5.2 · via Fireworks", _AGENTIC, 1_000_000
    ),
    "fireworks:accounts/fireworks/models/kimi-k2p6": ModelEntry(
        "Kimi K2.6 · via Fireworks", _AGENTIC, 256_000
    ),
    "fireworks:accounts/fireworks/models/deepseek-v4-pro": ModelEntry(
        "DeepSeek V4 Pro · via Fireworks", _AGENTIC, 1_000_000
    ),
    "fireworks:accounts/fireworks/models/llama4-maverick-instruct-basic": ModelEntry(
        "Llama 4 Maverick · via Fireworks", _AGENTIC, 1_000_000
    ),
    # OpenRouter slugs are lowercase `<lab>/<model>` (checked against their catalog
    # 2026-07-25); same labs as above, one key for all of them.
    "openrouter:z-ai/glm-5.2": ModelEntry("GLM-5.2 · via OpenRouter", _AGENTIC, 1_000_000),
    "openrouter:moonshotai/kimi-k2.6": ModelEntry(
        "Kimi K2.6 · via OpenRouter", _AGENTIC, 256_000
    ),
    "openrouter:deepseek/deepseek-v4-pro": ModelEntry(
        "DeepSeek V4 Pro · via OpenRouter", _AGENTIC, 1_000_000
    ),
    "openrouter:meta-llama/llama-4-maverick": ModelEntry(
        "Llama 4 Maverick · via OpenRouter", _AGENTIC, 1_000_000
    ),
    # -- cloud accounts (models running in the user's own AWS/GCP) ----------------
    # Bedrock ids carry a family segment (claude/ → native Anthropic path, other/ →
    # Converse) plus AWS's own `-v<n>:<m>` version suffix. Some regions require the
    # `us.`/`eu.` cross-region inference-profile prefix — custom add-model accepts those.
    "bedrock:claude/anthropic.claude-sonnet-4-6-v1:0": ModelEntry(
        "Claude Sonnet 4.6 · AWS Bedrock", _AGENTIC_VISION, 200_000
    ),
    "bedrock:claude/anthropic.claude-haiku-4-5-v1:0": ModelEntry(
        "Claude Haiku 4.5 · AWS Bedrock", _AGENTIC_VISION, 200_000
    ),
    "bedrock:other/amazon.nova-2-pro-v1:0": ModelEntry(
        "Nova 2 Pro · AWS Bedrock", _AGENTIC, 300_000
    ),
    "bedrock:other/meta.llama4-maverick-17b-instruct-v1:0": ModelEntry(
        "Llama 4 Maverick · AWS Bedrock", _AGENTIC, 1_000_000
    ),
    "bedrock:other/mistral.mistral-large-3-v1:0": ModelEntry(
        "Mistral Large 3 · AWS Bedrock", _AGENTIC, 128_000
    ),
    # Live-verified on Converse 2026-07-26 (complete/stream/tool round trip); asked for
    # two tool calls it emits them one at a time, so parallel stays off.
    "bedrock:other/nvidia.nemotron-super-3-120b": ModelEntry(
        "Nemotron Super 3 120B · AWS Bedrock",
        ModelCapabilities(
            tools=True, vision=False, parallel_tool_calls=False, streaming=True
        ),
    ),
    # Vertex ids carry a family segment too (gemini/ and claude/ → native paths,
    # openweight/ → the MaaS OpenAI-compat endpoint, keeping the publisher segment).
    "vertex:gemini/gemini-3.1-pro-preview": ModelEntry(
        "Gemini 3.1 Pro · Vertex AI", _AGENTIC_VISION, 1_048_576
    ),
    "vertex:gemini/gemini-3.6-flash": ModelEntry(
        "Gemini 3.6 Flash · Vertex AI", _AGENTIC_VISION, 1_048_576
    ),
    "vertex:claude/claude-sonnet-4-6": ModelEntry(
        "Claude Sonnet 4.6 · Vertex AI", _AGENTIC_VISION, 200_000
    ),
    "vertex:claude/claude-haiku-4-5": ModelEntry(
        "Claude Haiku 4.5 · Vertex AI", _AGENTIC_VISION, 200_000
    ),
    "vertex:openweight/meta/llama-4-maverick-17b-128e-instruct-maas": ModelEntry(
        "Llama 4 Maverick · Vertex AI", _AGENTIC, 1_000_000
    ),
    "vertex:openweight/qwen/qwen3-coder-480b-a35b-instruct-maas": ModelEntry(
        "Qwen3 Coder · Vertex AI", _AGENTIC, 256_000
    ),
}


def entry_for(model: str) -> ModelEntry | None:
    return MATRIX.get(model)


def model_labels() -> dict[str, str]:
    """Full-id → display-label map, shipped to the GUI so every picker shows human names."""
    return {mid: e.label for mid, e in MATRIX.items()}


def model_context_windows() -> dict[str, int]:
    """Full-id → context-window map (verified entries only), for the GUI's fill meter."""
    return {
        mid: e.context_window for mid, e in MATRIX.items() if e.context_window
    }


def models_for_provider(provider: str) -> list[str]:
    """BARE model ids (prefix stripped) the matrix curates for a provider — feeds the
    Settings pane's suggestions and the composer picker so both stay in lockstep with the
    matrix. OpenAI entries are stored without a prefix (bare ids route to the OpenAI
    default), so its list is every un-prefixed id."""
    if provider == "openai":
        return [mid for mid in MATRIX if ":" not in mid]
    prefix = provider + ":"
    return [mid[len(prefix) :] for mid in MATRIX if mid.startswith(prefix)]
