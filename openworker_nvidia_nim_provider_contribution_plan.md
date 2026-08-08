# OpenWorker — NVIDIA NIM Provider Contribution Plan

**Target contribution:** Add NVIDIA NIM as a first-class model provider to OpenWorker.

**Repository reviewed:** `andrewyng/openworker` repository dump supplied with this task.

**Status:** Implementation-ready design. This document intentionally does not modify the repository.

---

## 1. Executive recommendation

Add NVIDIA NIM as an **OpenAI-compatible first-class provider**, not as a new low-level SDK provider.

This is the cleanest fit for OpenWorker's existing architecture because:

- OpenWorker already has a reusable `OpenAIProvider` for OpenAI-compatible Chat Completions APIs.
- Provider configuration is descriptor-driven through `ProviderDescriptor` + `ProviderField`.
- `ProviderRouter` routes by a `provider:model` prefix and caches the provider client.
- Provider verification already uses a cheap `GET /v1/models` request.
- The GUI renders provider forms dynamically from backend descriptors.
- The model matrix is the single source of truth for curated model labels/capabilities.

NVIDIA's current hosted NIM API is OpenAI-compatible and uses:

- Base URL: `https://integrate.api.nvidia.com/v1`
- Models endpoint: `GET /v1/models`
- Chat endpoint: `POST /v1/chat/completions`
- API key environment variable: `NVIDIA_API_KEY`

NVIDIA currently advertises free serverless/free endpoints for development on Build.NVIDIA.com. "Free" should be described as a development/free endpoint, not as unlimited production inference.

### Recommended first model

`nvidia/nemotron-3-nano-30b-a3b`

Why:

- NVIDIA currently lists it as a free endpoint.
- NVIDIA describes it as supporting coding, reasoning, instruction following, and tool calling.
- It is substantially smaller than the 120B/550B Nemotron variants.
- It has a 262K context specification on the current model page.
- NVIDIA provides an OpenAI-compatible Python example using the same hosted NIM base URL.

For the curated matrix, the routed model ID would therefore be:

`nvidia:nvidia/nemotron-3-nano-30b-a3b`

The duplicated `nvidia:` + `nvidia/` is deliberate: the first part is OpenWorker's provider namespace; the second is NVIDIA's actual model ID.

---

# 2. What I found in the repository

The repository is already structured around a provider abstraction.

Relevant layout:

```text
coworker/
└── providers/
    ├── __init__.py
    ├── anthropic_provider.py
    ├── base.py
    ├── bedrock_provider.py
    ├── capabilities.py
    ├── errors.py
    ├── gemini_provider.py
    ├── matrix.py
    ├── openai_provider.py
    ├── openai_responses.py
    ├── registry.py
    ├── router.py
    └── vertex_provider.py

surfaces/gui/src/providers/
├── logos.ts
├── ProviderSetup.test.tsx
└── ProviderSetup.tsx

tests/
├── test_provider_router.py
├── test_provider_verify.py
├── test_providers.py
└── ...
```

The repository README describes OpenWorker as provider-agnostic and says users bring their own model/API key. The backend is Python and the desktop UI is React/Tauri.

The provider registry currently supports native providers plus OpenAI-compatible vendors. The registry explicitly uses descriptors containing UI fields and a client factory, while `ProviderRouter` selects a provider using the `provider:` prefix.

That means NVIDIA NIM should reuse this existing abstraction rather than introduce a parallel implementation.

---

# 3. Existing architecture to reuse

## 3.1 ProviderDescriptor

`coworker/providers/registry.py` defines:

```python
@dataclass(frozen=True)
class ProviderDescriptor:
    name: str
    title: str
    needs_key: bool
    fields: list[ProviderField]
    build: Callable[[dict[str, Any], Any], ProviderClient]
    recommended_model: Optional[str] = None
    env_key: Optional[str] = None
    blurb: str = ""
```

The registry also exposes:

```python
provider_descriptors()
provider_names()
get_descriptor()
build_provider_client()
descriptor_configured()
verify_provider_key()
```

This is exactly what NVIDIA needs.

---

## 3.2 Existing OpenAI-compatible provider factory

The repository already has a generic compatibility builder:

```python
def _openai_compat(vendor: str, default_base_url: str, env_key: Optional[str] = None):
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
```

This is the core reason a new `nvidia_provider.py` is unnecessary.

The builder deliberately prevents an unrelated `OPENAI_API_KEY` from being sent to another vendor's endpoint.

---

# 4. Exact backend implementation

## File 1 — `coworker/providers/registry.py`

Add NVIDIA to the OpenAI-compatible descriptor list.

Recommended implementation:

```python
_compat(
    "nvidia",
    "NVIDIA NIM",
    base_url="https://integrate.api.nvidia.com/v1",
    recommended_model="nvidia/nemotron-3-nano-30b-a3b",
    env_key="NVIDIA_API_KEY",
    endpoint_help=(
        "Prefilled with NVIDIA's hosted NIM endpoint. "
        "You can replace it with a self-hosted NIM or compatible proxy."
    ),
),
```

Place it with the other direct OpenAI-compatible providers.

### Why this is sufficient

The `_compat()` helper automatically creates:

- `api_key` field
- editable `base_url` field
- `NVIDIA_API_KEY` environment fallback
- OpenAI-compatible `OpenAIProvider`
- recommended model metadata
- provider configuration UI
- provider routing

No separate NVIDIA SDK dependency is required.

---

# 5. Provider routing

No `router.py` implementation change should be necessary.

Current routing behavior is:

```text
model string
     |
     v
nvidia:nvidia/nemotron-3-nano-30b-a3b
     |
     +---- provider = "nvidia"
     |
     +---- bare model = "nvidia/nemotron-3-nano-30b-a3b"
     |
     v
ProviderRouter
     |
     v
NVIDIA ProviderDescriptor
     |
     v
OpenAIProvider
     |
     v
https://integrate.api.nvidia.com/v1
```

The router already strips the provider prefix before passing the model to the underlying SDK.

This follows the same pattern already tested for:

```text
zai:glm-5.2
deepseek:deepseek-v4-flash
kimi:kimi-k2.6
qwen:qwen3-max
xai:grok-4.3
mistral:mistral-large-latest
```

---

# 6. Provider verification

The existing `verify_provider_key()` function already treats providers other than Anthropic, Gemini, Ollama, Bedrock, and Vertex as OpenAI-compatible.

It sends:

```http
GET {base_url}/models
Authorization: Bearer <api-key>
```

Therefore NVIDIA automatically becomes:

```http
GET https://integrate.api.nvidia.com/v1/models
Authorization: Bearer nvapi-...
```

No special verification branch is required.

Expected behavior:

| HTTP result | OpenWorker behavior |
|---|---|
| 2xx | Provider verified |
| 401/403 | `Invalid API key.` |
| network/timeout | `Couldn't reach NVIDIA NIM (...)` |
| other error | `NVIDIA NIM returned HTTP ...` |

Important: keep verification read-only. Do not send a chat completion merely to validate the key.

---

# 7. Curated model matrix

## File 2 — `coworker/providers/matrix.py`

Add at least one NVIDIA model after the direct OpenAI-compatible providers.

Recommended first entry:

```python
"nvidia:nvidia/nemotron-3-nano-30b-a3b": ModelEntry(
    "Nemotron 3 Nano 30B · NVIDIA NIM",
    ModelCapabilities(
        tools=True,
        vision=False,
        parallel_tool_calls=False,
        streaming=True,
    ),
    262_144,
),
```

### Important capability rule

Do **not** claim `parallel_tool_calls=True` until the model has been tested through the actual hosted endpoint with OpenWorker's tool schemas.

The repository explicitly treats the curated matrix as a verified capability source. Conservative capability flags are better than optimistic ones.

If live testing proves parallel tool calls work reliably, change it to:

```python
ModelCapabilities(
    tools=True,
    vision=False,
    parallel_tool_calls=True,
    streaming=True,
)
```

### Optional second/third curated models

After the first model is stable, consider:

```text
nvidia:nvidia/nemotron-3-super-120b-a12b
nvidia:nvidia/nemotron-3-ultra-550b-a55b
```

Both are currently advertised by NVIDIA as free endpoints and are positioned for agentic reasoning/tool use.

Do not add a large list just because NVIDIA exposes many models. The repository intentionally keeps the curated matrix small and only lists models that have been verified for agent use.

---

# 8. Why not create `nvidia_provider.py`?

Do NOT initially create:

```text
coworker/providers/nvidia_provider.py
```

That would duplicate the existing compatibility abstraction.

The NVIDIA hosted endpoint is explicitly OpenAI-compatible, so the correct dependency graph is:

```text
NVIDIA NIM
   |
   | OpenAI-compatible API
   v
OpenAIProvider
   ^
   |
_openai_compat()
   ^
   |
ProviderDescriptor("nvidia")
```

A dedicated provider file should only be introduced later if NVIDIA requires behavior that cannot be represented by the existing `OpenAIProvider`, such as a provider-specific wire protocol or persistent response state that the current abstraction cannot handle.

---

# 9. GUI changes

## File 3 — `surfaces/gui/src/providers/ProviderSetup.tsx`

Add NVIDIA's API-key help entry:

```ts
nvidia: {
  url: "https://build.nvidia.com/settings/api-keys",
  label: "build.nvidia.com",
},
```

The provider form itself does not need custom NVIDIA fields because the descriptor supplies:

```text
NVIDIA API key
Endpoint
```

The endpoint should be prefilled with:

```text
https://integrate.api.nvidia.com/v1
```

The user should still be able to edit it for:

- self-hosted NIM
- private proxy
- enterprise gateway
- regional/internal endpoint

This follows the existing design for OpenAI-compatible providers.

---

# 10. Provider logo

## File 4 — `surfaces/gui/src/providers/logos.ts`

The repository keeps provider logos as imported SVG assets.

Add:

```ts
import nvidia from "./logos/nvidia.svg";
```

Then:

```ts
export const PROVIDER_LOGOS: Record<string, string> = {
  ...
  nvidia,
};
```

And add `"nvidia"` to `PROVIDER_ORDER`.

### Asset

Create:

```text
surfaces/gui/src/providers/logos/nvidia.svg
```

Use a properly licensed/vendor-approved NVIDIA mark that is compatible with the repository's existing bundled-icon policy.

Do not download a random logo from an unlicensed icon website.

---

# 11. README update

## File 5 — `README.md`

The current "Bring your own model" provider list should mention NVIDIA NIM.

Suggested wording:

```markdown
OpenAI · Anthropic · Google Gemini · NVIDIA NIM · Inkling (Thinking Machines) ·
GLM (Z.ai) · DeepSeek · Kimi (Moonshot) · Qwen · MiniMax · Mistral · Grok (xAI)
```

Do not describe NVIDIA as "unlimited free AI."

Use wording such as:

```markdown
NVIDIA NIM provides hosted OpenAI-compatible model endpoints, including
free development endpoints for selected models.
```

This is more accurate and avoids promising production-level free access.

---

# 12. Tests — backend

## File 6 — `tests/test_providers.py`

Add tests for the descriptor:

```python
def test_nvidia_descriptor():
    from coworker.providers.registry import get_descriptor

    d = get_descriptor("nvidia")

    assert d is not None
    assert d.title == "NVIDIA NIM"
    assert d.needs_key is True
    assert d.env_key == "NVIDIA_API_KEY"
    assert d.recommended_model == "nvidia/nemotron-3-nano-30b-a3b"

    base = next(f for f in d.fields if f.key == "base_url")
    assert base.default == "https://integrate.api.nvidia.com/v1"
    assert base.required is False
```

---

# 13. Tests — builder

Verify that the NVIDIA provider uses its own API key:

```python
def test_nvidia_builder_defaults_and_profile_override(monkeypatch):
    from coworker.providers.registry import build_provider_client

    provider = build_provider_client(
        "nvidia",
        {"api_key": "nvapi-test"},
        None,
    )

    assert provider._api_key == "nvapi-test"
    assert provider._base_url == "https://integrate.api.nvidia.com/v1"

    provider2 = build_provider_client(
        "nvidia",
        {
            "api_key": "nvapi-test",
            "base_url": "http://localhost:8000/v1",
        },
        None,
    )

    assert provider2._base_url == "http://localhost:8000/v1"
```

---

# 14. Tests — environment key

Add:

```python
def test_nvidia_builder_env_key_fallback(monkeypatch):
    from coworker.providers.registry import build_provider_client

    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-env")

    provider = build_provider_client("nvidia", {}, None)

    assert provider._api_key == "nvapi-env"
    assert provider._base_url == "https://integrate.api.nvidia.com/v1"
```

---

# 15. Security regression test

The existing provider architecture has an important security invariant:

> A configured `OPENAI_API_KEY` must never be silently sent to another vendor.

Add the same test for NVIDIA:

```python
def test_nvidia_does_not_fall_back_to_openai_key(monkeypatch):
    import pytest

    from coworker.providers.registry import build_provider_client

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-real")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="NVIDIA NIM"):
        build_provider_client("nvidia", {}, None)
```

This is especially important because NVIDIA uses the same OpenAI-compatible client.

---

# 16. Tests — router

Add NVIDIA to the existing routing test:

```python
def test_nvidia_model_routes_and_strips_prefix(monkeypatch):
    state = _patch_build(monkeypatch)

    router = ProviderRouter(secrets=None)

    turn = router.complete(
        model="nvidia:nvidia/nemotron-3-nano-30b-a3b",
        messages=[],
    )

    assert turn.text == "nvidia"

    assert state["latest"]["nvidia"].models == [
        "nvidia/nemotron-3-nano-30b-a3b"
    ]
```

This verifies both halves:

```text
OpenWorker provider ID
        ↓
nvidia

Actual NVIDIA model ID
        ↓
nvidia/nemotron-3-nano-30b-a3b
```

---

# 17. Tests — capability matrix

Add:

```python
def test_nvidia_curated_model_capabilities():
    model = "nvidia:nvidia/nemotron-3-nano-30b-a3b"

    caps = capabilities_for(model)

    assert caps.tools is True
    assert caps.streaming is True
    assert caps.vision is False
```

Only assert `parallel_tool_calls` after a real tool-call verification.

---

# 18. Tests — matrix/provider lockstep

The existing repository has an invariant that a descriptor's recommended model must appear in its suggested models.

Therefore:

```python
def test_nvidia_recommended_model_is_curated():
    from coworker.providers.matrix import models_for_provider
    from coworker.providers.registry import get_descriptor

    d = get_descriptor("nvidia")

    assert d is not None
    assert d.recommended_model in models_for_provider("nvidia")
```

This prevents the common failure where the provider is configured successfully but its recommended model never appears in the GUI.

---

# 19. Tests — provider verification

Mock the existing `httpx.get` verification call.

Expected request:

```text
GET https://integrate.api.nvidia.com/v1/models
Authorization: Bearer nvapi-test
```

Test:

```python
def test_verify_nvidia_key(monkeypatch):
    import httpx

    from coworker.providers import verify_provider_key

    class Response:
        status_code = 200

    def fake_get(url, **kwargs):
        assert url == "https://integrate.api.nvidia.com/v1/models"
        assert kwargs["headers"]["Authorization"] == "Bearer nvapi-test"
        return Response()

    monkeypatch.setattr(httpx, "get", fake_get)

    assert verify_provider_key(
        "nvidia",
        api_key="nvapi-test",
    ) == {"ok": True}
```

Also add 401/403 and network-error cases if the existing provider verification tests cover them.

---

# 20. GUI tests

## File 7 — `surfaces/gui/src/providers/ProviderSetup.test.tsx`

Add a minimal NVIDIA provider fixture and assert:

- provider title is visible
- API key field exists
- endpoint field is prefilled
- endpoint remains editable
- no special auth-method controls appear
- NVIDIA help link is available

The current GUI is intentionally descriptor-driven, so avoid hard-coding NVIDIA-specific form logic.

---

# 21. GUI E2E tests

## File 8 — `surfaces/gui/e2e/provider-keys.spec.ts`

The existing provider-key flow already covers:

```text
open Settings
→ Models
→ select provider
→ enter key
→ Test
→ save
→ return to provider gallery
→ show Connected state
```

Add NVIDIA to the seeded mock provider data and create a small regression test.

Important:

The mock API fixture must know about the new provider.

The repository's E2E infrastructure routes `/v1/**` requests through mocked fixtures, so simply changing the backend descriptor is not enough for hermetic GUI tests.

Update the provider fixture used by:

```text
surfaces/gui/e2e/fixtures.ts
```

or whichever provider fixture currently supplies the Z AI/OpenAI/Anthropic states.

---

# 22. Live API test

After implementation, manually verify the real NVIDIA endpoint.

Do not commit a real API key.

Example environment setup:

```bash
export NVIDIA_API_KEY="nvapi-..."
```

Then:

```bash
python -c '
from openai import OpenAI
import os

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=os.environ["NVIDIA_API_KEY"],
)

models = client.models.list()

for model in models.data[:10]:
    print(model.id)
'
```

The purpose of this test is to confirm:

1. DNS/network connectivity
2. key validity
3. `/v1/models`
4. actual model ID
5. hosted model availability

---

# 23. Real tool-calling verification

This is the most important live test.

Do not mark NVIDIA as fully agent-capable merely because the model page says "tool calling."

OpenWorker needs to verify the complete loop:

```text
User
 ↓
OpenWorker
 ↓
NVIDIA NIM
 ↓
tool_call
 ↓
OpenWorker tool execution
 ↓
tool result
 ↓
NVIDIA NIM
 ↓
final answer
```

Use a tiny harmless tool such as:

```json
{
  "type": "function",
  "function": {
    "name": "get_test_value",
    "description": "Return a fixed test value.",
    "parameters": {
      "type": "object",
      "properties": {},
      "required": []
    }
  }
}
```

Expected:

```text
assistant → tool call
OpenWorker → executes tool
tool → returns value
OpenWorker → sends tool result
NVIDIA → produces final answer
```

Only after this succeeds should the model be considered safe for the curated agent matrix.

---

# 24. Streaming verification

NVIDIA documents streaming through the OpenAI-compatible API.

Test:

```text
stream=True
```

and verify OpenWorker receives:

```text
text_delta
```

chunks and produces the final `AssistantTurn`.

The existing `OpenAIProvider.stream()` implementation already handles OpenAI Chat Completions streaming, so no NVIDIA-specific stream parser should be needed unless the real response format exposes an incompatibility.

---

# 25. Reasoning-content verification

NVIDIA models can expose reasoning content through OpenAI-compatible response fields.

The repository's OpenAI provider already has handling for reasoning-style content, including `reasoning_content`.

Verify that:

```text
reasoning_content
```

does not accidentally appear inside the normal final answer text.

Expected separation:

```text
reasoning_content → AssistantTurn.reasoning
content           → AssistantTurn.text
```

This is particularly important for Nemotron models.

---

# 26. Do not hard-code NVIDIA-only request parameters initially

NVIDIA examples may show model-specific parameters such as:

```python
extra_body={
    "reasoning_budget": 16384,
}
```

or:

```python
extra_body={
    "chat_template_kwargs": {
        "enable_thinking": True,
    }
}
```

Do not automatically inject these globally into OpenWorker.

Reason:

- different NIM models can expose different parameters;
- OpenWorker supports many providers;
- the curated provider layer should not silently alter every request;
- a model-specific option belongs in a capability/model-settings abstraction if the repository later chooses to expose it.

First make standard:

```text
messages
tools
stream
temperature
max_tokens
```

work reliably.

Then consider an explicit model-settings mechanism for NVIDIA reasoning controls.

---

# 27. Self-hosted NIM support comes almost for free

One of the strongest reasons to use the existing `_compat()` abstraction is that the endpoint is editable.

Hosted:

```text
https://integrate.api.nvidia.com/v1
```

Self-hosted example:

```text
http://localhost:8000/v1
```

The same provider can therefore support:

```text
Hosted NVIDIA NIM
        OR
Self-hosted NVIDIA NIM
        OR
NVIDIA-compatible proxy
```

without introducing additional provider names.

This is a major architectural advantage.

---

# 28. Suggested user experience

Provider gallery:

```text
┌─────────────────────────────────────────┐
│ NVIDIA NIM                              │
│ OpenAI-compatible AI models             │
│                                         │
│ [ NVIDIA logo ]                         │
│                                         │
│ Free development endpoints available    │
└─────────────────────────────────────────┘
```

Provider form:

```text
NVIDIA NIM

API key
┌─────────────────────────────────────────┐
│ nvapi-••••••••••••••••                  │
└─────────────────────────────────────────┘

Endpoint
┌─────────────────────────────────────────┐
│ https://integrate.api.nvidia.com/v1     │
└─────────────────────────────────────────┘

Get a key: build.nvidia.com

[ Test & Save ]
```

After success:

```text
NVIDIA NIM
✓ Connected

Included models
• Nemotron 3 Nano 30B
```

---

# 29. Model selection UX

After configuration, the model should appear in the normal model picker as:

```text
Nemotron 3 Nano 30B · NVIDIA NIM
```

while the internal model string remains:

```text
nvidia:nvidia/nemotron-3-nano-30b-a3b
```

This matches the repository's separation between:

- routed model ID
- curated display label

---

# 30. Files expected to change

### Required

```text
coworker/providers/registry.py
coworker/providers/matrix.py
surfaces/gui/src/providers/ProviderSetup.tsx
surfaces/gui/src/providers/logos.ts
surfaces/gui/src/providers/logos/nvidia.svg
README.md
tests/test_providers.py
tests/test_provider_router.py
tests/test_provider_verify.py
```

### Likely required for hermetic GUI tests

```text
surfaces/gui/e2e/fixtures.ts
surfaces/gui/e2e/provider-keys.spec.ts
surfaces/gui/src/providers/ProviderSetup.test.tsx
```

### NOT required initially

```text
coworker/providers/nvidia_provider.py
pyproject.toml dependency for NVIDIA SDK
new router implementation
new provider API endpoint
new SecretStore implementation
new authentication system
```

---

# 31. Dependency impact

Do not add an NVIDIA Python SDK.

The repository already depends on:

```toml
"openai>=1.0"
```

and uses:

```text
OpenAIProvider
```

for OpenAI-compatible services.

Therefore:

```text
NVIDIA NIM support
        ↓
existing openai package
        ↓
no new Python dependency
```

This keeps the PR small and reduces maintenance.

---

# 32. Potential issue: NVIDIA model namespace

NVIDIA model IDs commonly look like:

```text
nvidia/nemotron-3-nano-30b-a3b
```

OpenWorker provider routing looks like:

```text
provider:model
```

Therefore the internal ID becomes:

```text
nvidia:nvidia/nemotron-3-nano-30b-a3b
```

This is valid because the router splits only on the first colon.

Do NOT change the router to split on `/`.

Do NOT rename NVIDIA model IDs to remove the `nvidia/` namespace.

The actual model string must reach NVIDIA unchanged after OpenWorker strips the provider prefix.

---

# 33. Potential issue: `GET /models` versus `/v1/models`

The provider descriptor base URL must include:

```text
/v1
```

Therefore verification correctly becomes:

```text
https://integrate.api.nvidia.com/v1/models
```

Do not configure:

```text
https://integrate.api.nvidia.com
```

unless the existing provider builder/verification normalization is intentionally changed.

The current compatibility-provider pattern expects the configured base URL to be the OpenAI API root.

---

# 34. Potential issue: "free" wording

The PR should NOT claim:

> NVIDIA gives unlimited free AI.

Better:

> NVIDIA NIM provides free development endpoints for selected models, subject to NVIDIA's current availability, limits, and terms.

The NVIDIA Build site currently labels selected models as "Free Endpoint" and the NVIDIA API site describes free serverless APIs for development.

This distinction matters for a public open-source project because provider pricing/limits can change.

---

# 35. Potential issue: rate limits

Hosted free endpoints are not suitable as an assumed production SLA.

Tests should therefore avoid:

- huge prompts
- large output limits
- repeated stress loops
- parallel API hammering

Use small deterministic prompts.

For live tests, keep the number of calls low.

---

# 36. Potential issue: API-key leakage

Never:

- commit `NVIDIA_API_KEY`
- place it in Playwright fixtures
- place it in screenshots
- log request headers
- add it to README examples
- include it in issue reports

Use:

```bash
export NVIDIA_API_KEY="..."
```

locally and redact it from all PR artifacts.

---

# 37. Test strategy

## Layer 1 — unit tests

No network:

```text
descriptor
builder
env fallback
routing
model matrix
capabilities
```

## Layer 2 — verification tests

Mock `httpx.get`:

```text
GET /v1/models
200
401
403
network failure
```

## Layer 3 — GUI tests

Mock backend:

```text
provider appears
provider form opens
endpoint is prefilled
Test & Save works
provider becomes Connected
model appears
```

## Layer 4 — real NVIDIA smoke test

One or two API calls:

```text
list models
simple completion
```

## Layer 5 — real agent test

One complete:

```text
tool call → tool execution → tool result → final answer
```

## Layer 6 — real streaming test

Verify streaming deltas.

---

# 38. Suggested PR implementation order

### Commit 1 — Provider backend

```text
registry.py
matrix.py
backend tests
```

Goal:

```text
nvidia:nvidia/nemotron-3-nano-30b-a3b
```

can route to NVIDIA.

### Commit 2 — GUI

```text
ProviderSetup.tsx
logos.ts
nvidia.svg
GUI tests
E2E fixture
```

Goal:

NVIDIA appears as a normal provider in Settings → Models.

### Commit 3 — Documentation

```text
README.md
```

Goal:

document NVIDIA NIM without making unsupported pricing claims.

### Commit 4 — Verification hardening

Only if needed after live testing:

```text
streaming fixes
reasoning parsing
tool-call compatibility
capability corrections
```

Keeping the commits separated makes review much easier.

---

# 39. Acceptance criteria

The contribution is complete only when all are true.

### Provider discovery

- [ ] `nvidia` appears in `provider_names()`.
- [ ] `/v1/providers` contains NVIDIA NIM.
- [ ] GUI provider gallery displays NVIDIA.

### Configuration

- [ ] API key field is present.
- [ ] Endpoint defaults to `https://integrate.api.nvidia.com/v1`.
- [ ] Endpoint is editable.
- [ ] `NVIDIA_API_KEY` works as an environment fallback.
- [ ] NVIDIA does not use `OPENAI_API_KEY` as fallback.

### Verification

- [ ] `/v1/models` verification succeeds with a valid key.
- [ ] 401/403 is reported as invalid key.
- [ ] network errors are handled without a server crash.

### Routing

- [ ] `nvidia:nvidia/nemotron-3-nano-30b-a3b` routes to NVIDIA.
- [ ] underlying SDK receives `nvidia/nemotron-3-nano-30b-a3b`.

### Model matrix

- [ ] model has a human-readable label.
- [ ] capabilities are conservatively correct.
- [ ] recommended model is in the provider's curated list.

### Runtime

- [ ] normal completion works.
- [ ] streaming works.
- [ ] tool calling works.
- [ ] tool result round trip works.
- [ ] reasoning content does not corrupt final text.

### GUI

- [ ] provider can be tested and saved.
- [ ] provider shows connected state.
- [ ] model appears in picker.
- [ ] endpoint can be edited.
- [ ] E2E mock knows the provider.

### Documentation

- [ ] README lists NVIDIA NIM.
- [ ] no API key is committed.
- [ ] no unsupported "unlimited free" claim exists.

---

# 40. Recommended final architecture

```text
                    OpenWorker
                         |
                         v
                +----------------+
                | ProviderRouter |
                +----------------+
                         |
                 model = nvidia:...
                         |
                         v
             +----------------------+
             | ProviderDescriptor    |
             | name = "nvidia"       |
             +----------------------+
                         |
                         v
                  _openai_compat()
                         |
                         v
                  OpenAIProvider
                         |
                         v
        +--------------------------------+
        | NVIDIA NIM hosted API          |
        | https://integrate.api.nvidia.com/v1 |
        +--------------------------------+
                         |
                         v
        +--------------------------------+
        | nvidia/nemotron-3-nano-30b-a3b |
        +--------------------------------+
```

For self-hosting:

```text
OpenWorker
    |
    v
NVIDIA provider
    |
    v
http://localhost:8000/v1
    |
    v
NVIDIA NIM container
    |
    v
GPU
```

---

# 41. PR description draft

## Title

`feat: add NVIDIA NIM as an OpenAI-compatible provider`

## Summary

Add NVIDIA NIM as a first-class OpenAI-compatible model provider.

The integration reuses OpenWorker's existing compatibility provider abstraction, so no NVIDIA SDK dependency or provider-specific transport is required.

### Included

- NVIDIA NIM provider descriptor
- NVIDIA API-key configuration
- editable hosted/self-hosted endpoint
- `NVIDIA_API_KEY` environment fallback
- curated Nemotron model entry
- provider routing tests
- provider verification tests
- GUI provider entry and logo
- GUI/E2E provider configuration coverage
- README documentation

### Architecture

NVIDIA NIM exposes an OpenAI-compatible API, so the implementation routes:

```text
nvidia:nvidia/nemotron-3-nano-30b-a3b
```

through the existing `OpenAIProvider` using:

```text
https://integrate.api.nvidia.com/v1
```

### Validation

Validated:

- provider discovery
- credential verification
- provider routing
- model selection
- streaming
- tool calling
- tool-result round trip
- GUI configuration flow

No NVIDIA API key is committed.

---

# 42. Bottom line

The correct contribution is **small**.

Do not build a new NVIDIA SDK adapter.

OpenWorker already has almost everything required:

```text
ProviderDescriptor
        +
_openai_compat()
        +
OpenAIProvider
        +
ProviderRouter
        +
verify_provider_key()
        +
model matrix
        +
dynamic GUI provider forms
```

The contribution is primarily:

```text
registry.py       → add "nvidia"
matrix.py         → curate Nemotron model(s)
ProviderSetup.tsx → add NVIDIA key-help link
logos.ts           → add NVIDIA branding
tests              → prove routing/verification/tool use
README.md          → document the provider
```

That is the architecture most likely to be accepted because it follows the repository's existing provider design instead of introducing a special case.

---

## External verification used for this plan

NVIDIA's current documentation confirms that NIM exposes OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoints.

NVIDIA's current Build pages show selected NIM models with free endpoints and provide the hosted endpoint:

```text
https://integrate.api.nvidia.com/v1
```

The current Nemotron 3 Nano page advertises a free endpoint and describes the model as supporting coding, reasoning, instruction following, and tool calling.

The exact availability of free endpoints, models, quotas, and limits should be rechecked immediately before merging because NVIDIA can change its hosted model catalog.

