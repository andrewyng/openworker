# Guardrails and hooks: implementation sequence and ledger

Started November 1, authorized for full implementation November 2, 2026.
Implementation branch: `feat/guardrails-hooks-20261102`, merging the original
specification and canonical `openworker-harness-sdk` at `9dc33312` (including the
DLAI tool-free fix). The [approved additions](GUARDRAILS-TEXT-AND-HOOKS-API.md)
extend the original [contract](GUARDRAILS-AND-HOOKS.md). Public APIs remain under
implementation until their milestones are qualified.

## Milestones

| Milestone | Deliverable | Independent verification |
|---|---|---|
| M1 — core engine and optional packaging | Bounded built-in/regex/model checks, redaction/custom actions, immutable hooks and capability reporting; optional components | Detector and failure tests; minimal/full compile matrix; explicit unsupported-capability rejection |
| M2 — runtime enforcement | All input/model/tool/output boundaries, buffered guarded streaming, bounded regeneration and safe history/export | Sentinel leak tests, actual dispatch counts, no replay after completed effects, approval/bypass invariants |
| M3 — Rust and TypeScript APIs | Harness/session/turn configuration, embedded and private-stdio callbacks, restart binding | Embedded/managed parity, cancellation/timeouts, stale replies, tool-free regression suite |
| M4 — compliance journal and API | Durable actor/policy/decision/outcome evidence, scoped paginated queries and retention | SQLite/PostgreSQL parity, administrative authorization, worker recovery and audit durability |
| M5 — external security integration | Common adapter and mock-qualified Prisma AIRS adapter, selected context and secret references | Local mock allows/blocks/errors/timeouts/malformed replies; scan correlation and egress constraints. Live Prisma qualification explicitly excluded |
| M6 — SIEM delivery | Durable export backlog, stable IDs/checkpoints, retries, backpressure and health | Receiver outage/restart/duplicates/full backlog; export never replays tools or provider work |
| M7 — full qualification and guides | SDK/provider/storage/recovery matrix, compact management findings, QuickStart/test catalog and size measurements | Local end-to-end tests and minimal/full size/resource evidence; honest qualified-capability matrix |

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

### November 2 — implementation authorized

- User approved built-in/regex/model checks for user input, model text and selected
  tool results; custom replacement callbacks; blocking/observational hooks;
  ordering; safe feedback; bounded regeneration; separate safety introspection.
- Final option name is `blockedOutputOptions`, not `blockedOutput`.
- Rebased the work by merging canonical tool-free-agent fix `9dc33312`; preserve
  that partner regression matrix throughout this implementation.
- Host disk has about 23 GiB free. Reuse the task-owned tool-free test caches;
  avoid duplicate toolchain/container downloads. No other agent's artifacts are
  removed. No GCP/Azure or paid model calls in this checkpoint.
- Starting S1 core rules and decision/transform tests. Later milestones remain
  authorized; do not mark them complete merely because the core types compile.

### November 2 — core checks and compliance research checkpoint

- Added the shared bounded detector/transform engine and typed hook registry.
  Ten targeted runtime tests pass locally: wire names/options, invalid regex and
  bounds, overlapping Unicode spans, Luhn/masking, common/custom secrets,
  callback replacement and final-content recheck, missing/error/panic/timeout/
  cancelled callbacks, exact model fragments, immutable blocking hooks,
  observer isolation, inheritance and exact tool selectors. Runtime enforcement,
  SDK wiring and service qualification are still outstanding; this is not a
  shipped guardrails feature.
- User requested a discussion of Anthropic's Compliance API before further
  implementation. Paused runtime changes for official-source research; no
  Anthropic admin keys requested, accounts enabled or remote data retrieved.
- [Compliance API](https://platform.claude.com/docs/en/manage-claude/compliance-api)
  is an after-the-fact activity/content retrieval API. Enterprise app content and
  Console activity coverage differ. The
  [coverage FAQ](https://platform.claude.com/docs/en/manage-claude/compliance-faq)
  explicitly excludes prompt/response content from ordinary API-key workloads.
  OpenWorker's direct model calls and local/native/MCP effects therefore require
  their own evidence; calling Claude does not export our session transcript into
  Anthropic's Compliance API.
- [Setup](https://platform.claude.com/docs/en/manage-claude/compliance-api-access)
  uses separate scoped compliance/admin keys, not inference keys. An optional
  Anthropic collector belongs outside the agent loop with operator-owned secrets.
  It can correlate provider-side account activity with our evidence in a SIEM;
  it is not a prerequisite for the guardrails feature.
- [Inference hooks](https://platform.claude.com/docs/en/manage-claude/inference-hooks)
  are a separate inline allow/deny feature for Claude Enterprise governed apps.
  Current documentation excludes Claude Platform API organizations, Bedrock and
  Google Cloud, and does not support rewriting/redaction. Keep OpenWorker's
  provider-independent inline guardrails and hook API.
- Compliance design recommendations pending discussion: preserve authoritative
  decision records separately from lossy observability queues; correlate trusted
  actor/session/turn/call/provider request IDs and pinned rule versions; expose
  authorized cursor-based evidence export and audit the readers/exporters too;
  configure metadata/content retention independently; add protected retention and
  legal-hold workflows only under an explicit contract. Database append-only
  conventions alone are not tamper-proof evidence against a privileged database
  administrator. Immutable archives/signatures would be separate work.

### November 2 — compliance scope, optional packaging and future policy delivery

- User clarified that OpenWorker must provide its own compliance activity API,
  structured context for external security services, and reliable SIEM export.
  Accepted the proposed separation of inline enforcement, durable evidence and
  export. Updated the approved API document with this contract and reconciled
  the older decision-only/observer-only prose in the original specification.
- Make optional components selectable at build time as well as runtime so that
  a disabled integration does not unnecessarily enlarge a minimal SDK/runtime.
  TypeScript managed distributions need an appropriately minimal native binary;
  JS configuration alone does not shrink it. Measure size/resource deltas and
  test minimal/full builds before advertising packaging capabilities.
- Installation options belong to the trusted developer/operator. Required active
  policy cannot be disabled by sessions, turns, agents or bypass. Reject builds
  that cannot satisfy required policy, rather than silently skipping checks.
- Record authenticated enterprise policy download/refresh as a future, separate
  milestone. Policy may select required checks, compliance and SIEM settings;
  future design must cover identity/authenticity/versioning, installed capability
  validation, atomic activation, offline/expiry behavior and auditability.
  No policy server/client, external service connection or runtime implementation
  was added for this discussion checkpoint.

### November 2 — seven milestones approved

- User approved M1–M7 together, with no per-milestone approval pauses.
- Prisma AIRS credentials are unavailable. Build and test against a local mock;
  live vendor qualification is explicitly outside these seven milestones and is
  not a blocker for completion. Label adapter evidence as mock qualification.
- Docker cache cleanup reclaimed 14.13 GB; available host space is now about
  37 GiB. Preserve existing Docker volumes/running environments and check space
  between significant builds. Reuse task-owned Cargo caches.
- Enterprise policy download remains deferred. Continue implementation now.

### M1 checkpoint — optional engine qualified

- Extracted `openworker-guardrails` with an optional `engine` feature. Runtime
  and SDK expose `guardrails` features; storage/auxiliary crates no longer force
  the runtime's default features into an SDK minimal build.
- Eleven engine tests and one no-engine capability-rejection test pass. Fixed an
  edge case where a callback returned unchanged detected text: final validation
  now runs after every replacement, including a no-op replacement.
- `cargo check -p openworker-sdk` passes both with defaults and with
  `--no-default-features`; guardrail crate strict Clippy passes. These are engine
  and packaging checks, not proof that runtime boundaries already enforce checks.
- Reused existing Cargo target cache; no new Docker images or paid services.
  M2 runtime integration begins next. Binary-size deltas remain an M7 gate.

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
