# Streaming, retries and provider diagnostics

An agent turn can make several model calls, and a failed call can be retried. Build
the UI around attempt IDs rather than concatenating every text delta into one
permanent message. A successful model attempt is still provisional until the
turn completes; it can request tools or be followed by another model call.

```typescript
import { AttemptOutput } from "@openworkerai/harness-sdk";

const display = new AttemptOutput(); // Create one projection for this run.
const run = await session.send("Explain the trade-offs", {
  output: { reasoning: true, bufferAttempts: false },
});
for await (const item of run.events()) {
  display.apply(item);
  // Render as escaped text; never inject model text as HTML.
  answerElement.textContent = display.text;
  thinkingElement.textContent = display.reasoning;
}
const result = await run.result();
if (result.status !== "completed") display.interrupt();
// Save result.finalOutput as the final answer, not the accumulated stream.
```

The helper removes failed candidate text and reasoning as soon as the attempt
fails. Replayed event IDs are ignored. It limits retained attempts and characters
for display; these limits do not truncate the durable transcript. After cancelling
a run, call `interrupt()` to discard any remaining provisional output. To avoid
showing failed candidates at all, set `bufferAttempts: true`: the runtime releases
display deltas only after that provider attempt completes successfully. This
trades incremental output for a quieter UI during retries.

Rust exposes the same options through `TurnOptions.output` / `OutputOptions` and
typed `RunEvent::AttemptStarted`, `AttemptFinished`, `TextDelta`, `ReasoningDelta`,
`ModelActivity` and `ProviderError`. Keep separate buffers per
`(session_id, turn_id, attempt_id)` and discard a failed or interrupted buffer.
TypeScript summaries use `attemptId`; the raw journal retains `attempt_id`.

| Event | Meaning |
| --- | --- |
| `model_attempt_started` | A provider attempt began. |
| `assistant_delta` | Candidate answer text for that attempt. |
| `reasoning_delta` | Opt-in provider-designated display reasoning, separate from the answer. |
| `model_activity` | The request is still pending; includes elapsed milliseconds, at most once every five seconds. |
| `model_attempt_finished` | That attempt completed or failed. |
| `provider_error` | Safe structured diagnostics for a failed attempt. |

An activity event is a liveness signal, not a claim that the model has completed
a particular task. It does not extend the request timeout or add provider calls.

## Enable reasoning display

Reasoning display is off by default. `output.reasoning: true` exposes only the
provider's supported display text. It never decodes encrypted reasoning, exposes
signatures or reveals redacted thinking. Display is bounded to 64 KiB per attempt;
`reasoning_display_limited` records when that display bound is reached.

- **OpenAI Responses:** requests `reasoning.summary: "auto"` and forwards
  `response.reasoning_summary_text.delta`. Use a model that supports summaries.
- **Anthropic:** forwards `thinking_delta`. Explicitly configure thinking
  generation for the selected model; display alone does not enable generation.
- **Chat Completions:** forwards `reasoning_content` or `reasoning` text when the
  selected endpoint/model supplies it. The harness does not guess a vendor's
  generation settings or extract hidden reasoning from answer text.

For Anthropic, choose a mode supported by your exact model:

```typescript
await harness.setModelProvider({
  name: "Claude", api: "anthropic",
  credentials: { apiKeyEnv: "ANTHROPIC_API_KEY" },
  thinking: { type: "adaptive" },
  // For a model using an explicit thinking budget instead:
  // thinking: { type: "enabled", budgetTokens: 2048 },
});
```

Rust uses `ModelProvider::anthropic("Claude").thinking(AnthropicThinking::Adaptive)`
or `AnthropicThinking::Enabled { budget_tokens: 2048 }`. An enabled budget must
be at least 1,024 tokens and smaller than the current call's output limit. These
settings apply to main agent calls, not short connection probes, compactors or
reviewers. Omitted settings preserve an existing choice; explicitly use
`{ type: "disabled" }` to disable thinking generation. Provider configuration
persists across restart and credential rotation.

Output guardrails and blocking output/tool hooks suppress candidate reasoning as
well as candidate answer text, including for agents without tools. Display
opt-in does not bypass inspection or allow withheld content into telemetry.

See the provider contracts for [OpenAI summaries](https://developers.openai.com/api/docs/guides/reasoning#reasoning-summaries),
[Anthropic thinking streams](https://platform.claude.com/docs/en/build-with-claude/streaming#thinking-delta)
and [Together reasoning fields](https://docs.together.ai/docs/inference/chat/reasoning).

## Diagnose provider failures

Failed run results expose `providerError` in TypeScript or `provider_error` in
Rust. The diagnostic contains a category, retryability, and available HTTP status,
API, request ID and fixed parser/allowlisted upstream code. Probe failures expose
the same data through `HarnessError.context.providerError` or Rust
`Error::Provider`. An `invalid_tool_call_index` code, for example, identifies a
malformed streamed tool-call index without logging the tool arguments.

```typescript
const result = await run.result();
if (result.providerError) {
  const { category, httpStatus, requestId, code } = result.providerError;
  console.error({ category, httpStatus, requestId, code });
}
```

Upstream response bodies, prompts, keys and arbitrary error messages are not
included in these diagnostics. Use the request ID with the provider when more
detail is needed. A retryable provider error does not authorize an application to
replay tool actions: retain the SDK's run reference and reconcile unknown outcomes.
