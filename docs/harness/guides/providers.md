# Models and credentials

A provider registration names a connection and its API format. An agent selects a
provider plus model ID as its default; session creation can override that choice.
The session saves its resolved model. Unavailable models fail explicitly.

| Service | API | Typical configuration |
|---|---|---|
| OpenAI | Responses | `name: "OpenAI", api: "responses"` |
| Anthropic | Native Messages | `name: "Anthropic", api: "anthropic"` |
| OpenRouter or a compatible proxy | Chat Completions | Custom `baseUrl` and credentials |
| Ollama / vLLM | Chat Completions | Local base URL and an installed model ID |

Keep credentials out of agent instructions, tool arguments and coworker bundles.
Register a provider at startup or after your settings UI obtains a key. Use an
environment-variable reference for server deployments; for desktop UI input,
pass `credentials: { apiKey, storage: "memory" }` and use your application's secret
store for persistence. Only choose SDK secure persistence on a supported platform.

A custom proxy can supply short-lived bearer tokens through the provider-auth
callback interface and proactively rotate them. Authentication refresh does not
create another session or silently change the selected model.

See the [desktop example](desktop.md), [TypeScript SDK](../../../harness/typescript-sdk/README.md)
and [guardrails guide](guardrails.md).
A compatible endpoint does not guarantee equivalent tool or image behavior;
qualify the specific model/configuration used by your application.

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
