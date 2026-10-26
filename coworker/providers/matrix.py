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

Resellers: Together + Fireworks + OpenRouter + Nebius + OrcaRouter + NVIDIA NIM
+ Groq (landed 2026-10-25, after the deferral of 2026-07-04).
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
    # ChatGPT-subscription catalog (the `openai-codex` OAuth provider). Curated to the
    # ids the subscription backend actually serves; vision per the vendor's model docs,
    # PDF unverified over this backend → local fallback via pdf_support.py.
    # 5.6 tiers (Sol flagship / Terra balanced / Luna fast) serve over the subscription
    # backend by plan — Sol is rate-limited on Plus, full on Pro.
    "openai-codex:gpt-5.6-sol": ModelEntry(
        "GPT-5.6 Sol · ChatGPT plan",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
        400_000,
    ),
    "openai-codex:gpt-5.6-terra": ModelEntry(
        "GPT-5.6 Terra · ChatGPT plan",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
        400_000,
    ),
    "openai-codex:gpt-5.6-luna": ModelEntry(
        "GPT-5.6 Luna · ChatGPT plan",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
        400_000,
    ),
    "openai-codex:gpt-5.2-codex": ModelEntry(
        "GPT-5.2 Codex · ChatGPT plan",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
        400_000,
    ),
    "openai-codex:gpt-5.2": ModelEntry(
        "GPT-5.2 · ChatGPT plan",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
        400_000,
    ),
    "openai-codex:gpt-5.1-codex": ModelEntry(
        "GPT-5.1 Codex · ChatGPT plan",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
        400_000,
    ),
    "openai-codex:gpt-5.1-codex-mini": ModelEntry(
        "GPT-5.1 Codex Mini · ChatGPT plan", _AGENTIC, 400_000
    ),
    # Claude 5 generation (platform.claude.com/docs/en/models/overview, read 2026-09-08):
    # 1M context is the default and only size; 128K max output; adaptive thinking.
    # Fable 5.1 (2026-09-01) is GA; its Mythos 5.1 sibling is approved-orgs-only, so it
    # stays out of a picker meant for the public. Missing rows fell back to the 128k
    # compaction guess and compacted at 102,400 tokens (OPE-170).
    "anthropic:claude-fable-5-1": ModelEntry(
        "Claude Fable 5.1 · Anthropic", _AGENTIC_VISION, 1_000_000
    ),
    "anthropic:claude-opus-5": ModelEntry(
        "Claude Opus 5 · Anthropic", _AGENTIC_VISION, 1_000_000
    ),
    "anthropic:claude-sonnet-5": ModelEntry(
        "Claude Sonnet 5 · Anthropic", _AGENTIC_VISION, 1_000_000
    ),
    # Fable 5 (2026-06-09) stays listed as the registry's recommended model.
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
    # Claude 4.5/4.6: kept for comparability with published evaluations of those
    # models, so OpenWorker can be measured against them on the same model.
    "anthropic:claude-opus-4-6": ModelEntry(
        "Claude Opus 4.6 · Anthropic", _AGENTIC_VISION, 200_000
    ),
    "anthropic:claude-opus-4-5": ModelEntry(
        "Claude Opus 4.5 · Anthropic", _AGENTIC_VISION, 200_000
    ),
    "anthropic:claude-sonnet-4-5": ModelEntry(
        "Claude Sonnet 4.5 · Anthropic", _AGENTIC_VISION, 200_000
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
    # Ark Responses API providers (verified 2026-08-14). BytePlus pay-as-you-go and
    # Volcengine Agent Plan intentionally use separate provider prefixes because their
    # endpoints, credentials, regions, and model catalogs are not interchangeable.
    "ark:dola-seed-evolving-latest-version": ModelEntry(
        "Dola Seed Evolving · BytePlus Ark", context_window=256_000
    ),
    "ark:dola-seed-2-1-turbo-260628": ModelEntry(
        "Dola Seed 2.1 Turbo · BytePlus Ark", context_window=256_000
    ),
    "ark-agent-plan-cn:doubao-seed-evolving": ModelEntry(
        "Doubao Seed Evolving · Volcengine Agent Plan", context_window=256_000
    ),
    "ark-agent-plan-cn:doubao-seed-2.1-turbo": ModelEntry(
        "Doubao Seed 2.1 Turbo · Volcengine Agent Plan", context_window=256_000
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
    # GLM-5.2 shipped with a 1M window (docs.z.ai, read 2026-09-08: "1M" context,
    # 128K output); the 128,000 here was stale and compacted it at 102,400 (OPE-170).
    "zai:glm-5.2": ModelEntry("GLM-5.2 · Z AI", _AGENTIC, 1_000_000),
    # DeepSeek's native API exposes the full 1M context for both V4 variants
    # (verified against the official model table 2026-08-10).
    "deepseek:deepseek-v4-flash": ModelEntry(
        "DeepSeek V4 Flash · DeepSeek", _AGENTIC, 1_000_000
    ),
    "deepseek:deepseek-v4-pro": ModelEntry(
        "DeepSeek V4 Pro · DeepSeek", _AGENTIC, 1_000_000
    ),
    "kimi:kimi-k2.6": ModelEntry("Kimi K2.6 · Moonshot", _AGENTIC, 256_000),
    "minimax:MiniMax-M2.5": ModelEntry("MiniMax M2.5 · MiniMax"),
    "qwen:qwen3-max": ModelEntry("Qwen3 Max · Alibaba", _AGENTIC, 256_000),
    # Qwen3.8 Max: multimodal (text/image/video/document) per Alibaba's model card, unlike
    # the text-only Qwen3 Max above — needs its own entry so the qwen prefix heuristic in
    # capabilities.py (vision=False for the whole family) doesn't strip images client-side
    # (owner report 2026-08-02). PDF unverified over the compat surface, same caveat as
    # Muse Spark, so it falls back via pdf_support.py too. Model Studio lists the GA id
    # `qwen3.8-max` with a 1,000,000 context window (read 2026-10-17); the earlier
    # `qwen3.8-max-preview` id is no longer listed.
    "qwen:qwen3.8-max": ModelEntry(
        "Qwen3.8 Max · Alibaba",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
        1_000_000,
    ),
    "xai:grok-4.3": ModelEntry("Grok 4.3 · xAI", _AGENTIC, 256_000),
    "mistral:mistral-large-latest": ModelEntry(
        "Mistral Large · Mistral", _AGENTIC, 128_000
    ),
    # Upstage serves its Solar line under aliases that track the newest version
    # (console.upstage.ai/docs/models/solar-pro-4 and /solar-mini-4, read 2026-10-25: "512K
    # context length", 128K output, tool calling listed). solar-open2 stopped serving on
    # 2026-08-12 and solar-pro3 retires 2026-10-30 (docs/models/history).
    "upstage:solar-pro4": ModelEntry("Solar Pro 4 · Upstage", _AGENTIC, 512_000),
    "upstage:solar-mini4": ModelEntry("Solar Mini 4 · Upstage", _AGENTIC, 512_000),
    # -- SambaNova ---------------------------------------------------------------
    # SambaCloud's OpenAI-compatible API; ids and windows are the `context_length` values
    # api.sambanova.ai/v1/models reports (keyless, read 2026-10-25). The two 128k rows are
    # the production tier on docs.sambanova.ai/docs/en/models/sambacloud-models; MiniMax
    # M3.1 Flash is what the catalogue serves today in place of the M2.7/M3 previews.
    "sambanova:Meta-Llama-3.3-70B-Instruct": ModelEntry(
        "Llama 3.3 70B · SambaNova", _AGENTIC, 131_072
    ),
    "sambanova:DeepSeek-V3.1": ModelEntry(
        "DeepSeek V3.1 · SambaNova", _AGENTIC, 131_072
    ),
    "sambanova:MiniMax-M3.1-Flash": ModelEntry(
        "MiniMax M3.1 Flash · SambaNova", _AGENTIC, 1_048_576
    ),
    # Atlas Cloud fronts open-weight models under each lab's namespace. Id and window
    # from api.atlascloud.ai/v1/models (keyless, read 2026-10-25: `context_length`
    # 1,048,576, `supported_features` lists tools); the earlier openai/gpt-4.1-mini row is
    # not in its catalogue any more.
    "atlascloud:zai-org/glm-5.2": ModelEntry(
        "GLM-5.2 · via Atlas Cloud", _AGENTIC, 1_048_576
    ),
    # Novita fronts open-weight models under each lab's namespace. Id and window from
    # api.novita.ai/openai/v1/models (keyless, read 2026-10-25: `context_size` 1,048,576,
    # `features` lists function-calling).
    "novita:zai-org/glm-5.2": ModelEntry("GLM-5.2 · via Novita", _AGENTIC, 1_048_576),
    # -- resellers (their model namespaces, verbatim) -----------------------------
    "together:thinkingmachines/Inkling": ModelEntry("Inkling · via Together"),
    # Together windows below are the `context_length` values its /v1/models catalog
    # reported on 2026-09-08 (exact, per model; GLM-5.2 really is 1,048,575 there).
    "together:zai-org/GLM-5.2": ModelEntry(
        "GLM-5.2 · via Together", _AGENTIC, 1_048_575
    ),
    # Kimi K3 on Together (landed late July 2026): 1M window, native vision; PDFs
    # unverified over the compat surface (falls back via pdf_support.py, like Muse Spark).
    "together:moonshotai/Kimi-K3": ModelEntry(
        "Kimi K3 · via Together",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
        1_048_576,
    ),
    "together:moonshotai/Kimi-K2.7-Code": ModelEntry(
        "Kimi K2.7 Code · via Together", _AGENTIC, 262_144
    ),
    "together:moonshotai/Kimi-K2.6": ModelEntry(
        "Kimi K2.6 · via Together", _AGENTIC, 262_144
    ),
    "together:deepseek-ai/DeepSeek-V4-Pro": ModelEntry(
        # Together's serverless deployment exposes 512K, not the model-level 1M.
        "DeepSeek V4 Pro · via Together", _AGENTIC, 512_000
    ),
    "together:meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8": ModelEntry(
        "Llama 4 Maverick · via Together", _AGENTIC, 1_000_000
    ),
    # fireworks.ai/models/fireworks/glm-5p2 lists "1040k tokens" (read 2026-09-08).
    "fireworks:accounts/fireworks/models/glm-5p2": ModelEntry(
        "GLM-5.2 · via Fireworks", _AGENTIC, 1_040_000
    ),
    "fireworks:accounts/fireworks/models/kimi-k2p6": ModelEntry(
        "Kimi K2.6 · via Fireworks", _AGENTIC, 256_000
    ),
    # fireworks.ai/models/fireworks/deepseek-v4-pro lists "1040k tokens" (read 2026-10-17),
    # a little under the model-level 1M.
    "fireworks:accounts/fireworks/models/deepseek-v4-pro": ModelEntry(
        "DeepSeek V4 Pro · via Fireworks", _AGENTIC, 1_040_000
    ),
    "fireworks:accounts/fireworks/models/llama4-maverick-instruct-basic": ModelEntry(
        "Llama 4 Maverick · via Fireworks", _AGENTIC, 1_000_000
    ),
    # OpenRouter slugs are lowercase `<lab>/<model>` (checked against their catalog
    # 2026-07-25); same labs as above, one key for all of them. Kimi K2.6 and Llama 4
    # Maverick declare image input (architecture.input_modalities, 2026-08-09); PDFs
    # unverified over the compat surface, like Kimi K3 below.
    # openrouter.ai/z-ai/glm-5.2: 1,048,576 context, 163,840 completion (read 2026-09-08).
    "openrouter:z-ai/glm-5.2": ModelEntry(
        "GLM-5.2 · via OpenRouter", _AGENTIC, 1_048_576
    ),
    # Kimi K3 via OpenRouter (openrouter.ai/api/v1/models, read 2026-09-09: 1,048,576
    # context; served by Together among others — OpenRouter's `provider` routing can pin
    # it). Same capabilities as the Together row.
    "openrouter:moonshotai/kimi-k3": ModelEntry(
        "Kimi K3 · via OpenRouter",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
        1_048_576,
    ),
    "openrouter:moonshotai/kimi-k2.6": ModelEntry(
        "Kimi K2.6 · via OpenRouter",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
        256_000,
    ),
    "openrouter:deepseek/deepseek-v4-pro": ModelEntry(
        "DeepSeek V4 Pro · via OpenRouter", _AGENTIC, 1_048_576
    ),
    "openrouter:meta-llama/llama-4-maverick": ModelEntry(
        "Llama 4 Maverick · via OpenRouter",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
        1_000_000,
    ),
    # NVIDIA Nemotron via OpenRouter (openrouter.ai/api/v1/models/<id>/endpoints, read
    # 2026-10-01). Paid endpoints only; the `:free` variants are left out on purpose (they
    # log and may train on inputs, and are capped at 1,000 requests/day). Text-only.
    # Ultra is sized for BaseTen, the host a pinned run uses (202,800); DeepInfra lists
    # 262,144 and Venice 256,000, but the matrix holds the pinned host's window so
    # compaction triggers inside the real one (OPE-215).
    "openrouter:nvidia/nemotron-3-ultra-550b-a55b": ModelEntry(
        "Nemotron 3 Ultra · via OpenRouter", _AGENTIC, 202_800
    ),
    "openrouter:nvidia/nemotron-3.5-lightning": ModelEntry(
        "Nemotron 3.5 Lightning · via OpenRouter", _AGENTIC, 262_144
    ),
    # GLM 5.3 via OpenRouter (openrouter.ai/api/v1/models/z-ai/glm-5.3/endpoints, read
    # 2026-10-01: 39 endpoints, all tool-capable). Sized for Z.AI, the host a pinned run
    # uses (1,048,576 ctx, fp8, 131,072 max output). Without this row the model would fall
    # back to DEFAULT_CONTEXT_WINDOW (128,000) and compact at 102,400, far inside what the
    # endpoint actually serves (OPE-215).
    "openrouter:z-ai/glm-5.3": ModelEntry(
        "GLM 5.3 · via OpenRouter", _AGENTIC, 1_048_576
    ),
    # Stealth/cloaked alpha (catalog-checked 2026-08-24: 1,048,576 ctx, tool calling).
    # These are temporary lab previews — expect the slug to vanish when the lab ships
    # the real model; keep it until OpenRouter retires it.
    "openrouter:stealth/ox-alpha": ModelEntry(
        "Ox Alpha · via OpenRouter", _AGENTIC, 1_048_576
    ),
    # OrcaRouter: the same vendor namespaces, one key for all of them. Ids, windows and
    # tool support from api.orcarouter.ai/v1/models (keyless, read 2026-10-25:
    # `context_length` 1,000,000 and 1,048,576; both list `tools`; Sonnet 5 declares
    # image input, PDFs unverified over the compat surface).
    "orcarouter:anthropic/claude-sonnet-5": ModelEntry(
        "Claude Sonnet 5 · via OrcaRouter",
        ModelCapabilities(
            tools=True, vision=True, parallel_tool_calls=True, streaming=True
        ),
        1_000_000,
    ),
    "orcarouter:deepseek/deepseek-v4-pro": ModelEntry(
        "DeepSeek V4 Pro · via OrcaRouter", _AGENTIC, 1_048_576
    ),
    # NVIDIA NIM — NVIDIA's own `vendor/model` namespace, so its models repeat the word in
    # the routed id. Ids from integrate.api.nvidia.com/v1/models (keyless, read 2026-10-25;
    # the earlier llama-3.3-nemotron-super-49b, z-ai/glm-5.2 and deepseek-v4-pro ids are
    # no longer listed). Windows from the build.nvidia.com model pages the same day, which
    # list "1M" context for all three. Text-only; tool calling as on the OpenRouter and
    # Bedrock rows for the same models.
    "nvidia:nvidia/nemotron-3-super-120b-a12b": ModelEntry(
        "Nemotron 3 Super · via NVIDIA", _AGENTIC, 1_000_000
    ),
    "nvidia:nvidia/nemotron-3-ultra-550b-a55b": ModelEntry(
        "Nemotron 3 Ultra · via NVIDIA", _AGENTIC, 1_000_000
    ),
    "nvidia:nvidia/nemotron-3.5-lightning-30b-a3b": ModelEntry(
        "Nemotron 3.5 Lightning · via NVIDIA", _AGENTIC, 1_000_000
    ),
    # -- cloud accounts (models running in the user's own AWS/GCP) ----------------
    # Bedrock ids carry a family segment (claude/ → native Anthropic path, other/ →
    # Converse) plus AWS's own `-v<n>:<m>` version suffix. Some regions require the
    # `us.`/`eu.` cross-region inference-profile prefix — custom add-model accepts those.
    # Claude 5 generation: ids from the AWS model cards (docs.aws.amazon.com/bedrock/
    # latest/userguide/model-card-anthropic-claude-{fable-5,opus-5,sonnet-5}.html, read
    # 2026-10-25). AWS publishes these without a `-v<n>:<m>` suffix; 1M context, image
    # input, tool use — same as the native `anthropic:` rows above.
    "bedrock:claude/anthropic.claude-fable-5": ModelEntry(
        "Claude Fable 5 · AWS Bedrock", _AGENTIC_VISION, 1_000_000
    ),
    "bedrock:claude/anthropic.claude-opus-5": ModelEntry(
        "Claude Opus 5 · AWS Bedrock", _AGENTIC_VISION, 1_000_000
    ),
    "bedrock:claude/anthropic.claude-sonnet-5": ModelEntry(
        "Claude Sonnet 5 · AWS Bedrock", _AGENTIC_VISION, 1_000_000
    ),
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
    # Nebius Token Factory keeps each lab's own namespace, like Together. Ids and windows
    # from its public catalogue (tokenfactory.nebius.com/api/public/models_info, read
    # 2026-10-25: `max_model_len` 1,048,576 for both; function_calling listed).
    "nebius:zai-org/GLM-5.2": ModelEntry("GLM-5.2 · via Nebius", _AGENTIC, 1_048_576),
    "nebius:deepseek-ai/DeepSeek-V4-Pro": ModelEntry(
        "DeepSeek V4 Pro · via Nebius", _AGENTIC, 1_048_576
    ),
    # -- Groq (LPU inference) ---------------------------------------------------
    # Groq keeps its own short ids. Rows and windows from console.groq.com/docs/models
    # (production table, read 2026-10-25: 131,072 context each; the earlier Maverick,
    # R1-distill and QwQ ids are no longer listed). console.groq.com/docs/tool-use (same
    # day): all three take tools; only Llama 3.3 does parallel tool use.
    "groq:llama-3.3-70b-versatile": ModelEntry(
        "Llama 3.3 70B Versatile · via Groq", _AGENTIC, 131_072
    ),
    "groq:openai/gpt-oss-120b": ModelEntry(
        "GPT-OSS 120B · via Groq",
        ModelCapabilities(
            tools=True, vision=False, parallel_tool_calls=False, streaming=True
        ),
        131_072,
    ),
    "groq:openai/gpt-oss-20b": ModelEntry(
        "GPT-OSS 20B · via Groq",
        ModelCapabilities(
            tools=True, vision=False, parallel_tool_calls=False, streaming=True
        ),
        131_072,
    ),
}


# The signed-in OpenRouter provider serves the same catalog as the key-based one.
MATRIX.update(
    {
        "openrouter-account:" + mid.split(":", 1)[1]: entry
        for mid, entry in list(MATRIX.items())
        if mid.startswith("openrouter:")
    }
)


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
