# Approved text guardrail and hook additions

November 2, 2026. User authorized full implementation. Public option name:
**`blockedOutputOptions: { maxRetries: 1 }`**. Rust uses
`blocked_output_options`. This supersedes the original decision-only and
observer-only restrictions; implementation must qualify all behavior below.

## Rules and scope

- `guard.creditCard({ action: "redact", keepLast: 4 })`: bounded candidates and
  checksum; preserve only the requested suffix.
- `guard.secrets({ action: "redact" })`: useful common key/token/private-key
  patterns; not an exhaustive secret detector.
- `guard.regex({ name, pattern, action, replacement })`: linear-time bounded
  runtime regex; reject unsupported JavaScript RegExp features explicitly.
- `guard.model({ name, model, instructions, action, replacement })`: separately
  configured local/cloud model, with separately attributed usage. Validate exact
  original source fragments/spans and redact in the harness rather than asking
  the model to rewrite text. Detection is probabilistic.
- A custom action receives text, validated matches, evaluation ID and cancellation
  and returns complete replacement text. Logging can deduplicate by evaluation ID.

Attach rules to `guardrails.userInput`, `guardrails.modelOutput`, and
`guardrails.toolResults: [{ tools: ["coding.read_file"], rules }]` on session
creation or turn submission. App/operator-required checks remain inherited;
session/turn additions cannot remove them or raise the operator's retry allowance.
No handler registration from model input, coworker content or tool output.

## Hooks

- `hook.beforeModel`, `hook.beforeTool`, `hook.afterTool`, `hook.beforeOutput`:
  explicit blocking checks with immutable context and allow/block results.
- A block carries a stable reason code and sanitized `feedbackToModel` explaining
  an allowed correction. It cannot echo sensitive text or claim human approval.
- `hook.onEvent`: observational only. Failure emits a bounded diagnostic and does
  not stop execution. Consumers deduplicate external effects by durable event ID.
- Text replacement stays in guardrails, so a late blocking hook cannot edit a
  candidate after validation. Both SDKs expose the same runtime contract.

## Order

Fixed access/schema checks → guardrail detection/sanitization → final required
validation → blocking hooks on the final candidate → approval handling where
applicable → execution/release → observational outcome hooks. No changes after
final checks. Required checks remain active in bypass and after session grants;
approval cannot override a mandatory policy block. Missing/failed required checks
pause safely. Apply all source-label, cancellation and stale-owner protections.

## Blocked outcomes and history

| Item | Behavior |
|---|---|
| User input | Safe application error; do not store rejected text in canonical history, outbox/events or send it to the working model. |
| Tool call | No dispatch; a safe protocol-valid tool error with `executed: false`. |
| Tool result | Preserve that the tool ran; withhold rejected output and return `executed: true, outputBlocked: true`. Never automatically repeat it. |
| Assistant output | Keep draft private; regenerate from last accepted history and safe harness feedback, within retry and turn budgets. |
| Retry still blocked | Structured blocked outcome and a static safe explanation; no unchecked model-generated fallback. |

Buffer screened content before delivery. Metadata may stream, but rejected text
cannot escape via deltas, history, replay, callbacks, artifacts or content-enabled
telemetry. Retain accepted tool facts across regeneration without replaying tools.
Do not continue through a provider state reference that includes a rejected draft.

Canonical history contains accepted/sanitized messages and safe provider-valid
tool exchanges. A separate durable safety journal records check/version, phase,
decision/code, action/evaluation IDs, retry outcome and duration. Keep metadata by
default. Raw blocked-content retention requires explicit protected storage,
access/retention controls and no agent `read_history` access. Public events must
not contain blocked content or low-entropy PII fingerprints.

`session.safetyEvents({ outcome: "blocked" })` returns bounded paginated metadata.
Managed Rust/TypeScript callbacks use owner-bound private stdio. Embedded Rust
can register native handlers. Remote workers use operator-installed providers;
REST clients select authorized definitions, never send executable closures.
Rebind after restart, fence late results and never repeat unknown tool effects.

## Compliance and security integrations

Approved direction, November 2: provide OpenWorker's own compliance activity API
and security integration contract. This is broader than consuming Anthropic's
Compliance API. These capabilities are not implemented or qualified yet.

- Guardrails and blocking hooks enforce policy before protected execution or
  delivery. An installed security-service adapter can send explicitly selected
  context to an organization-approved service such as Prisma AIRS. Invoking the
  scanner is the harness's responsibility, not a tool the model must remember
  to call. Adapter endpoints, credentials and data-sharing settings are trusted
  application/operator configuration, never model-controlled destinations.
- Context includes the current candidate and authorized relevant conversation,
  source labels, trusted actor/application/session/turn/action identities, and
  pinned policy versions. Select original versus sanitized content explicitly.
  Local redaction precedes external inspection by default; sending original
  content requires a separate configured authorization. Reject unsupported or
  oversized inspection input explicitly rather than silently omitting checks.
- Required hooks inspect the final candidate. A transformation invalidating a
  previous decision requires a fresh check before release. Missing, timed-out or
  invalid required security verdicts remain unavailable, not implicit approval.
- Durable activity records connect decisions to actual outcomes: model requests,
  tools, approvals, guardrails, child creation, permission/configuration changes,
  and access to compliance records. Retain external scan/report identifiers for
  correlation without automatically retaining sensitive scan payloads.
- Compliance reads expose authorized, bounded cursor-based activity queries.
  Separate permissions for metadata, content and retention administration. Local
  SDKs query their journal; services expose the equivalent REST contract.
- SIEM export uses a durable outbox/checkpoint and stable event identities, with
  bounded retries and visible delivery health. It is not an `onEvent` callback or
  a best-effort observability queue. Export retry never repeats model/tool work.
  A full backlog has explicit backpressure behavior; required evidence must not
  silently disappear. Retention of metadata and protected content is independent.

## Lightweight packaging and optional operation

Approved direction, November 2: customers may omit optional capabilities to keep
the SDK and shipped runtime small. Distinguish two controls:

| Control | Effect |
|---|---|
| Build-time selection | Exclude unused crates/features and their transitive dependencies from the delivered Rust library/runtime. |
| Runtime configuration | Enable or disable capabilities present in that build, under application/operator authority. |

Keep the minimal execution/runtime contract separate from optional content
detectors and model-backed checks, external security-vendor adapters, expanded
compliance queries/export workers, and observability exporters. Exact Cargo
feature names/defaults will be specified and measured during packaging work;
this document does not claim those build switches already exist. Preserve basic
execution state and unknown-outcome handling needed for correct operation even
when expanded compliance capture/export is absent.

TypeScript configuration cannot remove code from a prebuilt Rust child binary.
A lightweight TypeScript desktop distribution must bundle the corresponding
minimal runtime build as well as omit unused JavaScript integrations. Embedded
Rust and managed-process builds advertise their actual capabilities.

An absent capability is never a successful check. Fail configuration validation
clearly if selected policy needs a component not present in the build. Do not
silently downgrade to an unenforced policy. Once the trusted app/operator enables
mandatory checks or required auditing, session/turn options, model instructions,
approval grants and bypass cannot disable them. Optional installation is distinct
from agent-controlled removal of active policy.

Qualify both minimal and fully enabled builds, and publish stripped binary size
and relevant resource-use deltas. No enterprise service, vendor SDK, export worker
or background policy-fetch task should start merely because the SDK is imported.

## Later: enterprise policy delivery

Record as a separate future milestone; do not implement a policy server or
policy-download client as part of the current guardrails work.

An enterprise may configure its applications/SDK instances to authenticate to a
trusted policy endpoint and obtain versioned policy covering required guardrails,
hook/security integration references, compliance requirements and SIEM export
destinations. Start with authenticated download/refresh; server-push delivery is
a later transport choice, not required for the policy semantics.

Reserve these design requirements for that milestone:

- TLS, explicit enterprise identity and authenticated/versioned policy documents;
  validate authenticity, tenant/audience, validity and rollback protection.
- Declarative documents select installed capabilities and trusted adapter IDs;
  they do not download executable callbacks, vendor credentials or arbitrary
  model-selected URLs. Resolve credentials through approved secret references.
- Enterprise requirements are inherited by application/session/turn settings and
  cannot be weakened there. Unsupported required policy fails visibly.
- Validate policy atomically before activation. Pin policy identity/version to
  each protected decision and define activation boundaries for running sessions;
  do not swap enforcement halfway through an already-authorized action.
- Define last-known-good/offline/expiry and revocation behavior explicitly.
  Network failure must not silently remove mandatory checks. Required expired or
  unverifiable policy blocks affected work unless the verified policy explicitly
  authorizes an offline grace period.
- Record refresh/activation/rejection events and make the applied version
  inspectable. Policy distribution remains a trusted control-plane capability,
  separate from both working-agent traffic and audit export.

[Original contract](GUARDRAILS-AND-HOOKS.md) ·
[Implementation plan and ledger](GUARDRAILS-AND-HOOKS-PLAN.md)
