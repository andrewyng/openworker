# Models and credentials

A provider registration names a connection and its API format. An agent selects a
provider plus model ID as its default; session creation can override that choice.
The session saves its resolved model. Unavailable models fail explicitly.

| Service | API | Typical configuration |
|---|---|---|
| OpenAI | Responses | `name: "OpenAI", api: "responses"` |
| Anthropic | Native Messages | `name: "Anthropic", api: "anthropic"` |
| Google Gemini | Google OpenAI-compatible Chat Completions | Google's `/v1beta/openai` base URL and a Gemini API key |
| OpenRouter or a compatible proxy | Chat Completions | Custom `baseUrl` and credentials |
| Ollama / vLLM | Chat Completions | Local base URL and an installed model ID |

Keep credentials out of agent instructions, tool arguments and coworker bundles.
Register a provider at startup or after your settings UI obtains a key. Use an
environment-variable reference for server deployments; for desktop UI input,
pass `credentials: { apiKey, storage: "memory" }` and use your application's secret
store for persistence. Only choose SDK secure persistence on a supported platform.

## Where credentials are read

| Interface | What resolves an environment-key reference? |
| --- | --- |
| TypeScript `setModelProvider` / `modelProviders` | The Node process reads `apiKeyEnv` and sends the value over private stdio to the Rust child. |
| Embedded Rust `Credentials::from_env` | The Rust application process reads its environment. |

`inheritEnv` is unnecessary for the TypeScript provider convenience API. Use it
only for variables that Rust or another child must resolve itself; avoid passing
the whole environment. Neither SDK automatically loads env files. Memory-only
credentials must be supplied when the application starts. Secure provider storage
uses macOS Keychain or Windows DPAPI; on Linux choose memory-only credentials or
server provisioning. Callback functions also need registration on app startup.

A custom proxy can supply short-lived bearer tokens through the provider-auth
callback interface and proactively rotate them. Authentication refresh does not
create another session or silently change the selected model.

See the [desktop example](desktop.md), [TypeScript SDK](../../../harness/typescript-sdk/README.md)
and [guardrails guide](guardrails.md).
A compatible endpoint does not guarantee equivalent tool or image behavior;
qualify the specific model/configuration used by your application.

See [Streaming and retries](streaming.md) for provider reasoning display, explicit
Anthropic thinking settings and structured failure diagnostics.

## Register a cloud or local connection

Choose one connection below, then use its name in the agent's `defaultModel`.
The model ID must be available at that endpoint.

```typescript
// Anthropic: keep the key in your trusted process environment.
await harness.setModelProvider({
  name: 'Anthropic', api: 'anthropic',
  credentials: { apiKeyEnv: 'ANTHROPIC_API_KEY', storage: 'memory' },
});

// Ollama: choose a model already installed on this machine.
await harness.setModelProvider({
  name: 'Ollama', api: 'chat-completions',
  baseUrl: 'http://127.0.0.1:11434/v1',
});
```

Use the [Desktop example](desktop.md#connect-a-model) for OpenAI Responses.
For an authenticated OpenAI-compatible proxy, use `api: 'chat-completions'`, its
`baseUrl` and application-owned credentials. A provider registration does not make
a text-only model understand images or PDFs; see [attachments](attachments.md).

## Streaming token usage

Chat Completions providers request `stream_options: { include_usage: true }` by
default. When the server reports usage, the harness emits `model_usage` with the
provider's token counts. Some compatible servers do not support this request
option; disable it explicitly on that provider:

<!-- sdk-tabs -->
### TypeScript

```typescript
await harness.setModelProvider({
  name: "Local",
  api: "chat-completions",
  baseUrl: "http://127.0.0.1:11434/v1",
  streamUsage: false, // Only if this server rejects stream_options.
});
```

### Rust

```rust,ignore
harness.set_model_provider(
    ModelProvider::chat_completions("Local")
        .base_url("http://127.0.0.1:11434/v1")
        .stream_usage(false),
).await?;
```
<!-- /sdk-tabs -->

The setting persists across restart and credential rotation. Re-registering the
provider without it keeps the existing choice; set it to `true` to re-enable
usage requests. It applies only to Chat Completions. The harness never retries a
request automatically merely to remove this option.

Missing usage is unknown, not zero. Servers may omit it, and an interrupted stream
may never deliver its final accounting frame. Token counts alone also do not
establish a dollar cost; apply the pricing for the chosen provider and model.
The [OpenAI streaming contract](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
describes the optional final usage chunk.

## Anthropic prompt caching

Anthropic Messages requests enable automatic prompt caching by default using
`cache_control: { type: "ephemeral" }`. The provider moves the cache breakpoint
with the growing conversation, covering the reusable tool definitions, system
instructions and history. The default lifetime is five minutes. A request below
the provider's minimum cacheable length can still report zero cache reads.

Disable it for a proxy that rejects this field, or when your application does
not want caching:

```typescript
await harness.setModelProvider({
  name: "Anthropic", api: "anthropic",
  credentials: { apiKeyEnv: "ANTHROPIC_API_KEY", storage: "memory" },
  promptCaching: false,
});
```

```rust,ignore
harness.set_model_provider(
    ModelProvider::anthropic("Anthropic")
        .credentials(Credentials::from_env("ANTHROPIC_API_KEY").memory_only())
        .prompt_caching(false),
).await?;
```

The choice persists across credential rotation and restart. Omission preserves
an existing choice; `true` re-enables caching. The option is valid only for
Anthropic Messages. There is no silent fallback or automatic request replay.
`model_usage` reports cache reads and writes separately; total input tokens
include both. Actual hit rates and cost depend on model, prefix stability,
minimum prompt size and provider pricing. See
[Anthropic's prompt caching guide](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

## Gemini tool conversations

Register Google's compatible endpoint and choose a model available to your
Google account. The SDK manages streamed tool calls and their follow-up requests:

```typescript
await harness.setModelProvider({
  name: "Gemini",
  api: "chat-completions",
  baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
  credentials: { apiKeyEnv: "GEMINI_API_KEY", storage: "memory" },
});
const agent = harness.createAgent({
  name: "assistant",
  instructions: "Use the application's lookup tool to answer the user.",
  tools: ["app.lookup"], // Register this application tool first.
  defaultModel: { provider: "Gemini", name: "gemini-3.8-flash" },
});
```

```rust,ignore
harness.set_model_provider(
    ModelProvider::chat_completions("Gemini")
        .base_url("https://generativelanguage.googleapis.com/v1beta/openai")
        .credentials(Credentials::from_env("GEMINI_API_KEY").memory_only()),
).await?;
```

Google's opaque thought signatures are preserved in durable provider-private
history and replayed unchanged on the corresponding tool call or assistant
message. They survive host restart and credential rotation. They do not appear
in application tool arguments, display history, reasoning text, or telemetry
content—even when full-content telemetry is enabled.

Private replay state is bound to the connection identity, API, endpoint and
model. Sending it to another connection/model is rejected; the harness does not
invent signatures or bypass Google's signature validation. Use a fresh session
when changing providers. A provider-side proxy must preserve Google's
`extra_content.google.thought_signature` fields for this flow to work.

If a guardrail blocks or changes arguments in a signed tool-call batch, the
harness discards that candidate before any tool runs and asks the model for a
fresh compliant response. This uses the existing `blockedOutputOptions.maxRetries`
limit; exhausting it stops the turn. It never pairs modified arguments with an
old signature or keeps denied arguments in conversation history.

The compatible adapter accepts Google's complete tool calls without an `index`,
as well as indexed argument fragments. It never guesses how to combine partial
unindexed calls, and incomplete streams cannot authorize tool execution.
See [Google's compatibility guide](https://ai.google.dev/gemini-api/docs/openai)
and [thought signature requirements](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures).
