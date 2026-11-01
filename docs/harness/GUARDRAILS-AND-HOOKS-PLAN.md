# Guardrails and hooks: implementation sequence and ledger

November 1, 2026. Feature: `feat/guardrails-hooks-spec-20261101`, based on
`openworker-harness-sdk` at `673e5d62`. The
[draft contract](GUARDRAILS-AND-HOOKS.md) precedes implementation. This plan does
not claim the proposed public APIs already exist.

## Milestones

| Milestone | Deliverable | Independent verification |
|---|---|---|
| S1 — core decisions and tool gate | Versioned registry/configuration, required/advisory distinction, pinned bindings, bounded async checks at runtime tool authorization/dispatch, durable findings | Deterministic tests prove no execution on block/error/timeout, exact-action binding, unchanged approval behavior and coverage of native/MCP/host/team paths |
| S2 — model/result/output boundaries | beforeModel and afterTool checks, bounded guarded-output delivery, content-safe event/history/export paths, purpose-aware auxiliary requests | Scripted-provider tests prove no blocked outbound request, no unchecked bytes through any supported public channel, and no rerun/rollback of an already-executed tool |
| S3 — SDKs and process placement | Typed Rust/TypeScript guardrails and observers, owner-bound managed callback transport, registration restoration and capability negotiation | Rust embedded/managed plus TypeScript managed parity; disconnected/stale/late callbacks, saturated queues and callback deadlock tests |
| S4 — service and recovery | Operator-installed worker registry, authorized release/profile references, SQLite/PostgreSQL contract, leases/epochs and restart handling, management findings/OTel | Two-node PostgreSQL fixture kills an owner mid-check and before dispatch; another worker cannot reuse a stale decision or leak held output; reconnect/replay stays gated |
| S5 — evaluation and launch evidence | Versioned adversarial scenarios, reviewer evaluation set, measured errors/latency/cost, SDK recipes and honest public capability matrix | Repeatable deterministic suite plus separately authorized live-model runs; evidence identifies source revision, model/config, platform, samples and limits |

Implement common runtime decisions first. Do not bolt enforcement only onto one
SDK or make capabilities depend on SQLite versus PostgreSQL. Merge independently
verified milestones into `openworker-harness-sdk`; preserve existing partner APIs
unless a breaking change is explicitly agreed.

## Test catalog

### Policy and approval composition

Test hard denial before approval in Ask, auto-approve and bypass; unknown external
classification; required guardrails with session Allow Always; required versus
advisory checks; exact tool/schema/config binding; mandatory block not cleared by
an approval; skipped question not consent. Prove checks add restrictions rather
than becoming another route around access policy.

### Action integrity and failure handling

Test allow/block/inconclusive, missing handlers, invalid responses, exceptions,
timeouts, cancellation, user steering during a check, policy changes, stale owner
epochs, late callbacks and out-of-order replies. Count actual tool dispatches and
provider requests, not only returned statuses. Prove no protected effect happens
after an invalid or stale authorization.

### Content and streaming boundaries

Test blocked user data before provider delivery, auxiliary model calls, malicious
tool results, oversized guarded responses, intermediate text, live deltas,
history/event replay, artifacts, callbacks and full-content exporters. Probe with
unique sentinel strings and inspect all public channels. Prove withheld content
never escapes through a secondary surface. Do not call after-the-fact detection
prevention.

### Durability and observer delivery

Test SQLite/PostgreSQL, Rust embedded/managed, TypeScript callbacks and service
workers. Crash during evaluation, after committed allow, after tool dispatch and
before output release. Prove callbacks rebind before work resumes, stale decisions
reject, tool effects are not replayed, and duplicate observer events have stable
IDs. Observer failures must leave agent execution unaffected.

### Task-alignment evaluation

Use labelled cases including legitimate file edits, ambiguous cleanup, explicitly
forbidden deletion, authorized/unrequested publication, changed destinations,
prompt injection in tools/files, forged approval claims, agent-delegated requests,
board mentions, misleading summaries and skipped questions. Separate policy-rule
expectations from reviewer judgement. Record unsafe allows, unnecessary denials,
human escalations, inconclusive/error rates, latency and auxiliary token usage.

Live model evaluation uses a pinned model/config and repeated trials where
appropriate. Deterministic fixtures remain the regression gate; one good model
response is not evidence of a universal safety guarantee. Publish failures and
sample counts, not only a pass percentage.

## Progress ledger

### November 1 — specification checkpoint

- User agreed to strengthen alignment, safety, guardrails and hooks as a launch
  pillar. Source audit confirms existing separate reviewer, policy/resource
  checks, session grants and telemetry/events; the general public guardrail/hook
  API is still a documented gap.
- Read runtime authorization/dispatch, review-context source labels, session-grant
  fencing, provider sampling/deltas, existing callback transport and SDK builders.
- Created the draft contract, API sketch, enforcement/failure/streaming semantics,
  process-placement model and this evaluation/implementation sequence.
- No runtime/SDK behavior, approval defaults, schemas or cloud resources changed.
  Specification and proposal review precede S1 implementation.
- GCP live qualification remains independently waiting for login refresh. This
  task neither increases its US$100 budget nor authorizes Azure provisioning.

## Source anchors for implementation

- `harness/runtime/src/runtime.rs`: model requests/deltas, tool authorization,
  dispatch checkpoint and durable outputs.
- `harness/runtime/src/permissions.rs` and `runtime_policy_tests.rs`: hard floors,
  reviewer context/verdicts and approval/session-grant regressions.
- `harness/runtime/src/resource_policy.rs`: resource/placement authority.
- `harness/harness-sdk/src/callbacks.rs`, `application_tools.rs` and `cli.rs`:
  private callback delivery and host endpoint.
- `harness/typescript-sdk/src/transport.ts` and `index.ts`: reserved callback
  messages, owner/generation binding, restart and cancellation.
- `harness/store-postgres` and runtime store contracts: durable decisions and
  owner fencing; mirror SQLite behavior without SDK-specific logic.
- `harness/telemetry`: content capture policy and model-purpose accounting.

These are implementation locations, not assertions that all guardrail boundaries
are already instrumented there.
