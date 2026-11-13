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
be at least 1,024 tokens. The configured value is a ceiling: the harness clamps
it below the current call's output limit. If the remaining allowance cannot fit
the minimum thinking budget plus an answer token, the request omits that manual
thinking configuration. Final turn-limit summaries also omit the agent's thinking
configuration so the small summary reserve is not rejected by a larger manual
budget. Prior thinking/signature blocks are still replayed unchanged.

These settings apply to working-agent calls, not connection probes, compactors,
reviewers or final turn-limit summaries. Omitting a request setting uses the
provider/model default; it does not guarantee thinking is disabled on models where
thinking is always on. Output limits remain hard ceilings. Omitted settings during
provider registration preserve an existing choice. Use `{ type: "disabled" }`
only for models that support it; some adaptive-only models reject that mode.
Provider configuration persists across restart and credential rotation. See
[Anthropic's model-specific thinking rules](https://platform.claude.com/docs/en/build-with-claude/thinking-troubleshooting).

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

## Streaming with tool approvals

Use one durable submission for the turn, including time spent waiting for approval.
The UI shows the actual proposed tool and arguments; never invent an approval.

### Stream, approve and continue the same turn

This helper supplies the full loop. The app provides escaped-text rendering,
activity UI and an approval card showing both `pending.tool` and
`pending.arguments`.

```typescript
import { AttemptOutput, type Session, type RunEvent, type Interruption }
  from '@openworkerai/harness-sdk';

type Approval = Extract<Interruption, { kind: 'approval' }>;
export async function streamWithApprovals(session: Session, text: string, ui: {
  renderText(text: string): void;
  renderActivity(event: RunEvent): void;
  approve(proposal: Approval): Promise<boolean>;
}) {
  const display = new AttemptOutput();
  let stream = (await session.send(text)).events();
  while (true) {
    for await (const event of stream) {
      display.apply(event);
      ui.renderText(display.text); // Replace; do not append the whole projection.
      ui.renderActivity(event);
    }
    const result = await stream.result();
    if (result.status !== 'interrupted') {
      if (result.status === 'completed') ui.renderText(result.finalOutput ?? display.text);
      else { display.interrupt(); ui.renderText(display.text); }
      return result;
    }
    if (!result.interruptions.length) throw new Error('No pending interaction');
    for (const pending of result.interruptions) {
      if (pending.kind !== 'approval') return result; // Route questions/safety separately.
      const allowed = await ui.approve(pending);
      await session.respond(pending, { decision: allowed ? 'once' : 'deny' });
    }
    stream = await session.resumeStreamed(stream.checkpoint());
  }
}
```

Do not resend the user message to continue after approval: retain the submission
and consumed cursors. Use `session.cancel()` for Stop. Closing an observer does not
cancel execution or justify automatic approval. Persist the session reference
and stream checkpoint for crash recovery, then reconcile pending interactions.
For a non-streaming flow, `session.runWith(input, handler)` invokes a real app
handler for interruptions; `{ defer: true }` leaves the decision pending.

## Show approval decisions

A streamed event can contain `approvalDecision`, independently of its summary:

```typescript
for await (const item of run.events()) {
  const decision = item.approvalDecision;
  if (decision) {
    console.log(decision.tool, decision.verdict, decision.reason ?? 'No explanation available');
    // Scope interactionId/callId with item.sessionId and item.event.turn_id.
  }
}
```

`verdict` is `allow`, `deny` or `unsure`. `origin` distinguishes an LLM
`reviewer` (the auto-approver), a human, a session grant, hard policy, bypass or
application policy. `unsure` asks for human approval and does not execute the
call. These are persisted decisions; observing or replaying them never approves
another action or reruns a review. Rust exposes `RunEvent::ApprovalDecision`
and `ApprovalVerdict`; existing `AutoApproved` events remain available and can
carry the same decision metadata.

Reasons are optional, limited to 512 characters, and omitted when content
protection withholds them or obvious credential markers are detected. Configure
content guardrails for your application's sensitive data; the bounding filter is
not a general secret detector. Display explanations as untrusted text. A denied
tool response includes a safe explanation when available, plus the instruction
not to retry or evade the decision. Human and hard-policy denials use generic
explanations, without copying private policy errors. Older journals may lack the
metadata needed to identify a human decision or call; the SDK does not guess it.
