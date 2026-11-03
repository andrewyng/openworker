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

See [provider APIs and teams](../PROVIDERS-AND-TEAMS.md),
[desktop example](desktop.md) and [guardrail models](../GUARDRAILS-QUICKSTART.md).
A compatible endpoint does not guarantee equivalent tool or image behavior;
qualify the specific model/configuration used by your application.
