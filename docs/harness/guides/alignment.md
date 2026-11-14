# Agent alignment

An action can be technically permitted and still be unrelated to the user's task.
OpenWorker separates **what an agent can reach** from **whether an action is
justified by the user's request**.

For example, a coding agent may be allowed to read a project file. That does not
mean a request to fix a UI bug authorizes uploading the file to an external service.

## Choose an approval mode

| Mode | What happens |
|---|---|
| Ask | Required decisions go to a human; responses and session grants are durable. |
| Auto-approve | A separate reviewer evaluates eligible actions. Uncertainty or reviewer failure pauses for a human. |
| Bypass | Human approval is skipped. Required access policy, sandbox limits and guardrails still apply. |

The default is `approvals: { mode: "always" }`: tools require a human decision
unless a valid session grant covers them. To use a model reviewer, register its
provider first and select it explicitly:

<!-- sdk-tabs -->
### TypeScript

```typescript
const agent = harness.createAgent({
  name: 'support-agent-v1',
  instructions: 'Investigate the customer issue and propose or perform authorized fixes.',
  defaultModel: { provider: 'OpenAI', name: workingModel },
  tools: ['support.lookup', 'support.update_ticket'],
  approvals: { mode: 'auto', reviewerModel: { provider: 'OpenAI', name: reviewModel } },
});
```

### Rust

```rust
use openworker_sdk::{Agent, Approvals, Model};

let agent = harness.create_agent(Agent::new("support-agent-v1")
    .instructions("Investigate the issue and perform only authorized fixes.")
    .default_model(Model::new("OpenAI", working_model))
    .tool("support.lookup").tool("support.update_ticket")
    .approvals(Approvals::auto(Model::new("OpenAI", review_model))))
    .await?;
```
<!-- /sdk-tabs -->

The application registers the two example tools and supplies its model IDs.
Automatic review may allow, deny or ask a human. The UI still needs the
[approval interruption loop](streaming.md#streaming-with-tool-approvals).
Use `mode: "never"` only when your application intentionally bypasses that review;
it does not disable hard permissions or guardrails. See [all approval modes](sessions.md#approvals-and-questions).

Configure the reviewer's model separately from the working agent. Keep task context,
tool effects and source-labelled information available to the review process.
Tool output, memory and messages from another agent are data, not new human consent.

## Keep the layers independent

- **Access policy and OpenShell** constrain resources and execution.
- **Approval/review** decides whether an action is appropriate for the task.
- **Guardrails and hooks** apply required application rules to content and actions.
- **Compliance records** retain the decisions and committed activity for inspection.

Tool categories describe effects; they are not a safety ranking. Calling a tool
“read” or “execute” cannot remove another restriction. “Allow always” is scoped to
the current session and its bindings, and does not disable mandatory checks.
Auto-skipping a question also does not count as approval.

These mechanisms help enforce intent; a model reviewer is not a guarantee of perfect
judgment. Evaluate it against representative tasks and adversarial inputs from your
application. See [guardrails](guardrails.md) and [sessions](sessions.md) for
configuration and interruption handling.
