# Guardrails, hooks and compliance

Apply application-owned checks to user input, assistant text, tool arguments and
selected tool results. The same engine serves Rust embedded applications, Rust
managed subprocesses, TypeScript managed subprocesses and service workers.
Checks remain active in Ask, auto-approve and bypass modes.

## Start with two rules

<!-- sdk-tabs -->

### TypeScript

```typescript
import { Harness, guard, hook } from "@openworkerai/harness-sdk";

const harness = await Harness.start({
  guardrails: {
    userInput: [guard.creditCard({ action: "block" })],
    modelOutput: [guard.secrets({ action: "redact" })],
    toolResults: [{
      tools: ["coding.read_file"],
      rules: [guard.regex({
        name: "company-secret",
        pattern: /ACME_[A-Za-z0-9]{32}/g,
        action: "redact",
        replacement: "[redacted]",
      })],
    }],
  },
  blockedOutputOptions: { maxRetries: 1 },
  compliance: { retentionDays: 30, maxPendingEvents: 10000 },
});
```

### Rust

```rust,ignore
use openworker_sdk::{agents::Harness, compliance, guardrails::*};

let safety = Config {
    guardrails: Guardrails {
        user_input: vec![Rule::credit_card(Action::Block)],
        model_output: vec![Rule::secrets(Action::redact())],
        tool_results: vec![ToolRules {
            tools: vec!["coding.read_file".into()],
            rules: vec![Rule::regex(
                "company-secret", "ACME_[A-Za-z0-9]{32}", Action::redact(),
            )],
        }],
        ..Default::default()
    },
    blocked_output_options: Some(BlockedOutputOptions { max_retries: 1 }),
    ..Default::default()
};
let harness = Harness::builder()
    .safety(safety)
    .compliance(compliance::Config::default())
    .start().await?;
```

<!-- /sdk-tabs -->

Rule names and versions identify installed policy. Session/turn settings add
checks; they cannot replace an inherited definition or increase its explicit
regeneration allowance. Tool selectors use registered qualified names or unambiguous installed aliases. MCP names resolve through the registered server, so identically named tools on different servers remain distinct. Regex uses
Rust syntax, bounded compilation and UTF-8 match offsets. Credit-card detection
checks candidate length and Luhn; it is not a general financial-data classifier.

## Use a separate model when a pattern is insufficient

Register the privacy provider through the existing model-provider API, then refer
to it independently of the working agent and auto-approver:

```typescript
const spokenPii = guard.model({
  name: "spoken-identifiers",
  model: { provider: "Privacy", name: privacyModelId },
  instructions: "Find SSNs and phone numbers, including numbers written as words. " +
    "Identify the exact matching source text.",
  action: "redact",
});
const session = await agent.newSession({
  guardrails: { userInput: [spokenPii] },
});
```

A detector must identify exact source fragments; the harness validates them before
redaction. Missing credentials, invalid replies and timeouts do not become allows.
Model checks have separate accounting and bounded output. Their provider identity
is pinned; refreshing credentials does not silently switch the selected provider.
Detection quality still depends on the chosen model and rule. Use labelled cases
from your application to evaluate it.

## Hooks and custom replacements

Guardrails transform text first. Blocking hooks inspect the resulting candidate
and return allow/block. They cannot edit a candidate after validation.

```typescript
const session = await agent.newSession({
  hooks: [hook.beforeTool({
    name: "publication-policy",
    tools: ["github.publish_review"],
    run: async ({ arguments: args }) =>
      await appAllowsPublication(args)
        ? { action: "allow" }
        : { action: "block", code: "PUBLICATION_NOT_AUTHORIZED",
            feedbackToModel: "Keep the review as a draft." },
  })],
});

await session.send(message, {
  guardrails: { userInput: [guard.regex({
    name: "ticket-secret",
    pattern: /ACME_[A-Za-z0-9]{32}/g,
    action: async ({ text }) => text.replace(/ACME_[A-Za-z0-9]{32}/g, "[redacted]"),
  })] },
});
```

Rust callbacks use the same decisions:

```rust,ignore
let reference = HandlerRef { name: "publication-policy".into(), version: "1".into() };
harness.register_safety_handler(reference.clone(), handler(|context, cancel| async move {
    if cancel.is_cancelled() { anyhow::bail!("Cancelled"); }
    // Inspect application state; return a stable, content-free reason code.
    Ok(Decision::block("PUBLICATION_NOT_AUTHORIZED"))
})).await?;
let hook = Hook::new("publication-policy", HookPhase::BeforeTool, reference);
```

Available boundaries: `beforeInput`, `beforeModel`, `beforeTool`, `afterTool`,
`beforeOutput`, and observational `onEvent`. Callbacks execute in application code
for both embedded and managed SDKs. Managed callbacks use the private control lane,
not MCP and not the agent's tool catalog. Implement blocking checks as repeatable
checks: cancellation, reconnect and execution revalidation can invoke them again.
Treat callback input as sensitive; do not copy it into diagnostics or reason codes.

## What a block means

| Boundary | Result |
|---|---|
| User input | Reject before the working model, canonical history or submission outbox receives the rejected text. |
| Tool arguments / before-tool hook | No dispatch. The model receives a safe error with `executed: false`. |
| Tool result / after-tool hook | The action has already happened. Record that fact and withhold/redact its result. Never rerun the action to recover a blocked result. |
| Assistant text | Buffer protected text. Reject the candidate and use bounded regeneration from accepted history plus safe feedback. |
| Required check unavailable | Fail closed; active work parks with a safety interruption. Restore the handler/service, then explicitly resume. |

```typescript
await session.resumeSafety();
const result = await session.resume();
```

For a service session, use `session.resumeSafety(runId)` (Rust: `resume_safety`)
or post a durable `resume_safety` control. This retries checks; it does not grant
an approval or weaken policy. Pending cancellation takes precedence.

Rejected model candidates do not enter accepted history, public deltas or full
content exports. The separate evidence record retains the check/version, phase,
reason, duration and outcome. It does not retain the rejected content. Guarded
text is buffered rather than streamed before its verdict. Text checks do not
implicitly authorize images or other non-text artifacts.

## Observe without taking control

```typescript
const harness = await Harness.start({
  hooks: [hook.onEvent({
    name: "activity-view",
    run: async ({ context }) => updateActivityView(context),
  })],
});
```

Observers receive metadata with stable event IDs. Delivery uses a durable outbox,
bounded concurrency/deadlines and retries. A crash between callback delivery and
acknowledgement can repeat an ID; deduplicate external effects. Observer failure
cannot allow, veto or replay agent actions. SIEM delivery has its own checkpoints
and health rather than relying on an application observer.

## Audit policy decisions

Policy decisions can be recorded and exported independently of performance traces.
See [compliance capture and SIEM delivery](compliance.md#configure-capture-and-delivery)
for runnable configuration, retention and access controls.

## Prisma AIRS and other scanners

The Prisma adapter is experimental. It uses the synchronous scan API; validate
your tenant, profile and failure handling before enabling it in production.

```typescript
const harness = await Harness.start({ inheritEnv: ["PRISMA_AIRS_KEY"] });
await harness.registerSecurityAdapter({ name: "airs", version: "1" }, {
  endpoint: "https://service.api.aisecurity.paloaltonetworks.com/v1/scan/sync/request",
  protocol: { kind: "prismaAirs", profile: "my-security-profile" },
  credentialEnv: "PRISMA_AIRS_KEY",
});
const session = await agent.newSession({ hooks: [{
  name: "airs", version: "1", when: "beforeInput", tools: [],
  handler: { name: "airs", version: "1" }, timeoutMs: 10000,
}] });
```

Register through the Rust SDK with `register_security_adapter`; use
`Harness::builder().inherit_env("PRISMA_AIRS_KEY")` for a managed child. A native
application can instead supply a secure-store `Credential` resolver to `Scanner`
and register it as a handler. Only explicitly selected boundary text is sent.
Broad `beforeModel` payloads are rejected by the external scanner adapter so it
cannot implicitly upload system instructions, history or private provider state.

The vendor-independent `selectedContext` protocol sends a versioned object with
`scanId`, `phase`, `text` and optional tool name. A receiver returns `action: allow`
or `action: block`; unknown/inconclusive responses pause enforcement. Endpoint and
credential references are operator-owned. Neither agents nor retrieved content
can change them.

## Service configuration and optional packaging

Standalone services accept operator-owned `safety` and `compliance` configuration.
API admission checks run before durable submission writes; workers enforce model,
tool and output boundaries. API nodes need credentials only for input-check models,
not the working agent. `security_scanners` registers named external adapters on
API and worker processes. Application-native handlers can be supplied through the
server library's registry. Required session policy remains durable across owners.

Cargo features:

| Component | Feature | Default |
|---|---|---|
| Inline guardrail engine / hooks | `guardrails` | On in runtime/SDK/service reference |
| Compliance capture/query | `compliance` | On in runtime/SDK/service reference; capture still opt-in |
| External HTTP security adapters | `security-adapters` | Off |
| Durable HTTP SIEM exporter | `compliance-export` | Off |

```sh
# Minimal desktop host: required unavailable capabilities are rejected explicitly.
cargo build --release -p openworker-sdk --no-default-features

# Host including the optional integrations.
cargo build --release -p openworker-sdk --features security-adapters,compliance-export
```

Runtime switches do not shrink an already compiled binary. A TypeScript app ships
its selected native host build. Existing coding agents and tool-free guides keep
their behavior when no checks are configured. Enterprise policy download/pushdown
is a separate future milestone, not part of this implementation.

## Upgrade an existing installation

Follow [storage upgrades](upgrades.md) for current schema versions, backup and
migration steps. Use matching service/worker builds when changing policy support.

## Service deployment

The reference service accepts operator-owned `safety`, `compliance`,
`security_scanners` and `compliance_sinks` configuration. Install matching checks
and versions on API nodes and workers: APIs inspect user input before enqueueing;
workers revalidate before provider calls and tool execution. Remote REST clients
cannot upload callback functions. Custom Rust handlers are installed by the trusted
service host in `Config::safety_handlers`; HTTP scanners use named registrations
and credential environment references. The local Rust/TypeScript SDKs additionally
support application callbacks and additive session/turn options through their
embedded/private-stdio integration.

Compliance metadata can be pruned by trusted maintenance using
`Store::prune_compliance()` or `PostgresStore::prune_compliance()` (administrator
scope). Schedule it through the application's maintenance job. Query expiry does
not itself delete bytes, and this release does not automatically schedule physical
pruning. Canonical conversation retention remains a separate application decision.
