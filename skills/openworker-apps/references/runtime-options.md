# Provider, session and streaming options

These options require SDK and native runtime 0.1.0-beta.2 or later. Upgrade both;
the published SDK installs its exact matching platform runtime automatically.
If the app sets an explicit executable, update that binary too. Preserve existing
agent identities unless intentionally changing their immutable definitions.

## Providers and usage

Anthropic prompt caching is enabled by default. Set `promptCaching: false` on
provider registration to opt out. A short prompt may not reach the provider's
cache threshold; zero cache hits alone do not indicate a broken cache.

Chat Completions requests streaming usage by default. Set `streamUsage: false`
only for endpoints that reject `stream_options.include_usage`. Missing usage is
unknown, not zero cost. Use the run's `model_usage` events for reported tokens;
calculate monetary cost only when the selected model's pricing is known.

Use Google's OpenAI-compatible endpoint for Gemini; this is not a native Gemini
adapter. Signed tool context survives ordinary durable-session resume. Never
strip signatures from the internal history or reconstruct it from UI text.
Use the exact model ID available to the user's provider account.

```typescript
import { Harness } from '@openworkerai/harness-sdk';

export async function configureProviders(harness: Harness) {
  await harness.setModelProvider({
    name: 'Claude', api: 'anthropic',
    credentials: { apiKeyEnv: 'ANTHROPIC_API_KEY', storage: 'memory' },
    promptCaching: true,
    // Add thinking only when supported by the chosen model:
    // thinking: { type: 'adaptive' },
    // or thinking: { type: 'enabled', budgetTokens: 2048 },
  });
  await harness.setModelProvider({
    name: 'Gemini', api: 'chat-completions',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    credentials: { apiKeyEnv: 'GEMINI_API_KEY', storage: 'memory' },
  });
  await harness.setModelProvider({
    name: 'Together', api: 'chat-completions',
    baseUrl: 'https://api.together.ai/v1',
    credentials: { apiKeyEnv: 'TOGETHER_API_KEY', storage: 'memory' },
  });
}
```

Register only providers the app uses. Provider credentials remain in the trusted
Node process. Reasoning display is opt-in and separate from model generation:
Anthropic needs an explicit supported `thinking` mode; OpenAI Responses requests
available reasoning summaries; compatible providers may return `reasoning_content`.
Do not promise that every model emits reasoning. Never expose private signatures
or redacted thinking. Safety checks can withhold candidate output.

## Turn preferences, migration and retry-safe display

The following helper demonstrates the options together; choose only what the
product needs. The UI callback replaces escaped text, rather than appending every
delta. A failed model attempt is discarded by `AttemptOutput`. For no provisional
text at all, use `bufferAttempts: true` (buffers each attempt, not the entire turn).

```typescript
import { AttemptOutput, Harness } from '@openworkerai/harness-sdk';

export async function runGuide(
  harness: Harness,
  provider: string,
  model: string,
  userText: string,
  preferences: string,
  render: (answer: string, reasoning: string) => void,
) {
  const agent = harness.createAgent({
    name: 'guide', instructions: 'Explain the supplied material clearly.',
    defaultModel: { provider, name: model },
    tools: [], planning: false, memory: false, scheduling: false,
    builtinTools: { askUser: false, readHistory: false },
  });
  await agent.ready();
  // Import only when migrating. For ordinary recovery, use resumeSession.
  const session = await agent.newSession({ initialHistory: [
    { type: 'user', text: 'I am learning about databases.' },
    { type: 'assistant', text: 'We can start with indexes.' },
  ] });
  const display = new AttemptOutput();
  const run = await session.send(userText, {
    // Application-owned instructions, not untrusted user/document text.
    instructionAddendum: preferences,
    limits: {
      maxModelCalls: 8, maxOutputTokens: 60000,
      maxOutputTokensPerCall: 24000, summaryTokens: 512,
    },
    output: { reasoning: true, bufferAttempts: false },
  });
  for await (const event of run.events()) {
    display.apply(event);
    render(display.text, display.reasoning);
  }
  const result = await run.result();
  if (result.status !== 'completed') {
    display.interrupt();
    render(display.text, display.reasoning);
    // Handle pending approvals/questions when enabling tools. Log only safe
    // structured diagnostics, not raw upstream bodies, prompts or credentials.
    if (result.providerError) {
      const { category, httpStatus, requestId, code } = result.providerError;
      console.error({ category, httpStatus, requestId, code });
    }
  } else {
    render(result.finalOutput ?? '', display.reasoning);
  }
  return { result, reference: session.reference() };
}
```

Create one session per conversation in a real app and reuse it for later turns.
The helper creates a new one only to illustrate migration. `instructionAddendum`
is stored with the current turn, applies across retries and expires on the next
turn. Before-model hooks inspect it; user-input guardrails do not treat it as user
text. When supplying `submissionId`, retries must keep the same content/options.

The per-call ceiling defaults to 8,192 tokens; the model must support any higher
requested ceiling. The remaining total turn allowance can lower it. Increasing
one ceiling does not increase the other. Limits can also be agent defaults.

`initialHistory` accepts user/assistant text and completed `tool_call` / paired
`tool_result` entries, never provider-private or system messages. Imported tools
do not execute. Sanitize imported content: import does not retroactively run input
or tool-result guardrails. Resume the original session when signed model context
must be preserved. Imports are bounded (256 entries, 512,000 serialized bytes).

A tool-free agent advertises no implicit tools. For an agent with application
tools, `builtinTools` can independently disable `askUser` and `readHistory`.
Changing these immutable settings requires a new agent identity/session.

For UI activity, `model_activity` reports elapsed waiting time at most once every
five seconds; it is not task progress. Match tool callback UI with
`(sessionId, turnId, callId)`, not event arrival order. Do not retry side effects
because a stream disconnects; resume the durable run and reconcile its outcome.
