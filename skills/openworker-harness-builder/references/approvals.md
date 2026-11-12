# Tool approvals and streamed interactions

Set approval behavior explicitly when creating the agent. Changing it later
requires a new agent name because persisted definitions are immutable.

| Configuration | Behavior | Use it for |
| --- | --- | --- |
| `{ mode: 'always' }` | Ask a person for tool calls unless an existing valid session grant covers them. This is the default. | Interactive control. |
| `{ mode: 'only', tools: ['files.move_file'] }` | Ask for selected tools; ordinary unlisted tools receive application-policy approval. | Approval for writes, routine reads allowed. |
| `{ mode: 'auto', reviewerModel: { provider: 'OpenAI', name: modelId } }` | A selected model reviews eligible calls: allow executes, deny returns a denial, uncertain requests human approval. | Model-assisted approval. |
| `{ mode: 'never' }` | Bypass the approval review. Hard policy checks still apply. | Trusted unattended operations. |

`auto` is not unconditional approval. It can review read-only tools too. Hard
denials and valid session grants are handled first. Privileged dynamic agent-type
or child-creation operations can have additional approval requirements; `only`
does not automatically exempt them merely because they are absent from its list.
Model review is not a sandbox or a replacement for tool scope checks.

Human responses are `{ decision: 'once' }`, `{ decision: 'deny' }` or
`{ decision: 'session' }`. Use `once` when each mutation needs a separate card.
A session grant covers that tool for the session, including different valid
arguments, and survives restart. It does not override hard security checks.
Structured questions are separate: answer them with `{ answer: '...' }`, or
disable `builtinTools.askUser` when the product does not support questions.

## Stream, show the proposal, respond, continue

Use the complete [streamWithApprovals helper](../assets/local-script/organizer.mjs).
Its core flow is:

```typescript
const display = new AttemptOutput();
let stream = (await session.send(userText)).events();
while (true) {
  for await (const event of stream) {
    display.apply(event);
    renderText(display.text); // Replace, do not append the full projection.
    renderActivity(event);
  }
  const result = await stream.result();
  if (result.status !== 'interrupted') {
    if (result.status === 'completed') renderText(result.finalOutput ?? display.text);
    else { display.interrupt(); renderText(display.text); }
    return result; // Check failed/cancelled as well as completed.
  }
  if (!result.interruptions.length) throw new Error('No pending interaction');
  for (const pending of result.interruptions) {
    if (pending.kind !== 'approval') return result; // Route questions/safety separately.
    // The card must display pending.tool AND pending.arguments.
    const allowed = await showApprovalCard(pending);
    await session.respond(pending, { decision: allowed ? 'once' : 'deny' });
  }
  stream = await session.resumeStreamed(stream.checkpoint());
}
```

The snippet belongs inside an async app function; import `AttemptOutput` from
the SDK. Rendering/card functions are supplied by the app. It uses no new user
message to resume: keep the submission ID and consumed journal cursors. Use
`session.cancel()` for Stop. Closing the observer does not cancel execution and
must not cause automatic approval. For process recovery, persist the session
reference/checkpoint and reconcile pending interactions from durable state.

## What the app can observe today

The typed `auto_approved` summary describes application-policy approval, such as
ordinary unlisted tools under `only`. It is not the model auto-approver's verdict.
To display model decisions, inspect raw `interaction_required` and
`interaction_resolved` events: `event.data.review` contains the safe
`verdict`/`reason` when available; `origin` and nested `call.call_id` identify the
decision source and call. Handle absent/withheld reasons. Never interpret a
missing `auto_approved` summary as a denial or failure to review.

The current denied tool response tells the working agent that execution was
denied and not to retry or evade the decision. It does not currently include the
reviewer's reason. The app can display an available safe journal reason itself;
do not promise that the agent receives it. Application guidance for auto approval
is described in [Runtime options](runtime-options.md).
