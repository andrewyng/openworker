# Alignment, guardrails and hooks

Draft specification, November 1, 2026. **The APIs below are proposed, not shipped.**
The [implementation plan](GUARDRAILS-AND-HOOKS-PLAN.md) identifies the sequence and
qualification gates. Existing resource policy and approvals remain authoritative.

## Purpose

Help applications keep agent actions within user intent and operator-defined
boundaries, with inspectable decisions. An accessible file is not automatically
authorized for upload. An approved tool is not automatically appropriate for
every task. A model's explanation is not evidence that its action is authorized.

| Responsibility | Question | Existing foundation / addition |
|---|---|---|
| Task alignment | Is this action justified by the user's request? | Separate action reviewer; allow/deny/unsure; uncertainty returns to a human |
| Access policy | What can this instance reach? | App/operator resource ceilings, scoped tools and OpenShell execution policy |
| Guardrails | Does this request, action or content violate application rules? | Add registered checks at explicit runtime boundaries |
| Hooks | How does the application observe what happens? | Add a typed convenience API over versioned lifecycle events |

Guardrails can be deterministic or call a trusted policy service/model. They are
additional checks, not proof of general alignment or complete prompt-injection
protection. Host callbacks are trusted application code outside the tool sandbox;
the harness cannot constrain a malicious application running as the same OS user.

## Preserve the decisions already agreed

- Tool categories describe effects; they are not an ordered danger scale and do
  not grant filesystem/network authority. Unknown external tools remain External
  unless an operator supplies a trusted classification.
- Hard access restrictions accumulate. An approval, a guardrail allow verdict,
  a different category or a child-agent prompt cannot lift another restriction.
- Ask, auto-approve and bypass control approval handling. Bypass skips consent
  review; it does not switch off mandatory operator policy or OS isolation.
  Conversely, the harness must not silently turn a request for bypass into
  mandatory human approval under another name.
- Auto-approver uncertainty/failure pauses for a human. Headless execution can
  retain the interaction for an API/CLI response. Auto-skip questions affects only
  agent questions and never creates user consent.
- Allow Always means the current durable session, bound to the original tool,
  schema and policy identities. It does not skip required per-action guardrails.
- Collaborator requests, board posts, tool output, memory and generated summaries
  remain source-labelled data, never human permission.
- Unknown tool outcomes are returned to the model for inspection/status checks;
  hooks, recovery and policy changes must not automatically repeat their effects.

## Two extension contracts

### Guardrail: a blocking decision

Register a stable qualified ID, explicit version, phase, context requirements,
bounded timeout and handler. The registry is owned by the trusted application or
service deployment. Sessions pin definition/configuration digests; bundles can
reference installed IDs but cannot install handlers or remove required checks.

The initial handler returns one of:

| Result | Meaning |
|---|---|
| `allow` | This check passed. All remaining policy, approval and checks still apply. |
| `block(code, message)` | The checked operation is refused. It is not a request for broader permission. |
| `inconclusive(code)` | Required check could not establish an answer; pause before the protected boundary. |

Timeouts, handler crashes, lost callback replies, malformed/oversized results and
missing pinned handlers become a typed `guardrail_unavailable` pause, never allow.
Do not disguise these as an unknown tool effect when dispatch never occurred.
Unlike an approval interaction, a mandatory guardrail block cannot be cleared by
Allow Once or Allow Always. Trusted application/operator remediation is separate.

Required checks run in every approval mode, including bypass. Optional advisory
checks only emit findings; they do not secretly veto execution. Application
configuration must distinguish these roles explicitly. No handler can change
tool arguments, messages, grants, policy, model selection or approval state in v1.
Content redaction/transformation requires a later explicit contract.

Blocking a tool action normally returns a bounded policy error to the model with
`executed=false`, so it can propose an allowed alternative within the existing
turn limits. A withheld completed tool result reports `executed=true` plus a safe
policy error, never the rejected content. Blocking a provider request or output
delivery ends that protected step with a typed policy-blocked status for the app;
do not generate an unchecked explanatory model response to bypass the block.
Required-check unavailability is a distinct waiting reason in session state,
separate from approval or an agent question. Re-evaluate after trusted remediation
using fresh context/bindings; an approval answer cannot stand in for a check reply.

### Hook: an observation

An observer consumes immutable typed lifecycle events. Hook failure emits a
bounded diagnostic but does not fail or approve the agent action. Hooks do not
return decisions and have no veto authority. An application that needs to prevent
an effect must use a guardrail or existing policy/approval interface.

Use stable event IDs and replay cursors. Delivery can repeat after reconnect or
restart; applications must deduplicate externally visible side effects. The
runtime's durable journal remains authoritative. Bound queues and deadlines so a
slow observer cannot retain a worker slot or cause unbounded memory growth.

Start with session/turn lifecycle, model attempts, tool lifecycle, approval/review,
guardrail findings, recovery and completion/error events. Reuse existing events
where possible instead of introducing a second competing event history.

## Runtime boundaries and ordering

```text
User message / durable inbox
              |
        beforeModel check -------- block/unavailable --> no provider request
              |
        model proposes action
              |
      fixed access/schema checks
              |
       beforeTool guardrails ------ block/unavailable --> no tool dispatch
              |
       approval handling ---------- Ask / reviewer / bypass / session grant
              |
   revalidate context, policy, lease and exact checked action
              |
        dispatch tool once
              |
        afterTool check ----------- block/unavailable --> stop propagation
              |                       (tool effects already happened)
       next model request
              |
        beforeOutput check -------- block/unavailable --> withhold content
              |
    release checked output to UI/events/exporters
```

| Phase | Scope and guarantee |
|---|---|
| `beforeModel` | Check the actual destination/model and outbound content before a provider request, including relevant reviewer, compaction and limit-summary calls. Never check only the main agent while auxiliary calls leak the same data. |
| `beforeTool` | Check the exact qualified tool, arguments, placement and resource facts before any effect, including host callbacks, MCP tools and native/internal team mutations. |
| `afterTool` | Check a completed result before it enters another model request or a public tool-result stream. Preserve `executed=true` and effect/result identity if the result is withheld. No rollback claim or automatic retry. |
| `beforeOutput` | Check application-visible assistant text, including intermediate messages. Cover live delivery and reconnect/replay, not only the final convenience return value. |

Checks that apply to an event run sequentially in stable configured order. Stop at
the first non-allow result; do not log later checks as passed if they were skipped.
All gates execute inside the runtime, not only in the friendly SDK wrapper.

Before dispatch, compare the checked action, source-labelled user-intent revision,
resource/profile bindings, guardrail configuration, approval binding and ownership
epoch. Changed inputs invalidate the decision and return to the appropriate
gate. Cancellation or a stale worker cannot release output or dispatch a tool.

Guardrail-internal model requests require an explicit purpose and budget. Reject
recursive checking configurations at registration; never allow an unbounded
guardrail-calls-model-calls-guardrail loop. Reviewer/guardrail models are separately
configured from the working model. Application-owned remote checks have explicit
endpoints/credentials and data scopes, not credentials from agent arguments.

## Streaming and content handling

With no configured output guardrail, preserve current streaming behavior. With
a required output guardrail, hold the protected content until the complete
checked unit passes. Start with bounded complete-message buffering; do not claim
real-time token delivery for guarded messages. Status, time and usage metadata
may continue to stream. Oversized buffers stop safely; never silently truncate
the content and pretend it was fully checked.

Apply the same withholding to tool outputs protected by an afterTool check.
Unchecked content must not escape through event replay, partial deltas, history
APIs, observer callbacks, error messages, artifact URLs or content-enabled OTel
export. Keep any required private recovery material in access-controlled storage
with explicit retention; public streams carry metadata and safe findings until
release. This must be designed before enabling output-screening claims.

Guards receive only their declared context under application-owned content policy.
Do not include provider keys, credential bytes or unrelated sessions. Treat tool
arguments, files, retrieved content and collaborator text as untrusted data even
when included in a check. Short user-facing reasons and telemetry must avoid
echoing sensitive input or arbitrary handler exception text.

## Proposed SDK shape

Illustrative TypeScript; names are subject to review:

```typescript
const publishPolicy = guardrail({
  id: "company.publication", version: "1", phase: "beforeTool",
  timeoutMs: 10_000,
  check: async (context) => {
    return publicationAllowed(context)
      ? { decision: "allow" }
      : { decision: "block", code: "PUBLICATION_NOT_ALLOWED",
          message: "This destination is outside the publication policy." };
  },
});

const harness = await Harness.start({
  guardrails: { required: [publishPolicy] },
  hooks: { onEvent: (event) => showActivity(event) },
});
```

`publicationAllowed` and `showActivity` are application code. A Rust builder
exposes the same phases and decision types, with async handlers; its callbacks
remain ordinary Rust code. The shared wire/runtime contract owns the semantics.
Agent-specific checks add to application-required checks. Sessions and children
cannot remove inherited mandatory requirements or substitute another version.

| Placement | Handler location |
|---|---|
| Rust embedded | Trusted host registry in the application process |
| Rust/TypeScript managed child | Bounded callbacks on the existing owner-bound private stdio control channel; persist decisions in the runtime |
| Remote service | Operator-installed registry/providers on the workers; client selects authorized IDs/configuration, never sends executable closures over REST |

Callback registration is process-generation-bound. After restart, required
callbacks must re-register with identical definitions before affected work can
continue. Reserve callback control capacity so a check's reply cannot deadlock
behind the request waiting for it. Remote policy-service adapters are explicit
integrations, not automatic access to arbitrary URLs supplied by agents.

## Durability, cost and audit

Record each evaluation ID, phase, check ID/version/config digest, session, turn,
action/attempt, input binding, outcome/code, duration and original owner epoch.
Metadata is the default; record no prompt, arguments, output or secret bytes in a
decision log by default. LLM-backed checks have separately attributed token/cost
usage and bounded budgets. Do not count an observer delivery as model/tool work.

A committed allow can be reused only for the exact unchanged protected action and
its applicable context. An incomplete check can be evaluated again only under its
declared side-effect-free contract and current ownership; a completed tool cannot
be re-executed to regenerate a missing hook or check response. Unknown check
effects are distinct from unknown tool effects. Native callbacks/checkers must be
documented as pure decisions, not a second tool execution mechanism.

Emit versioned guardrail lifecycle events and OTel fields with phase/outcome,
duration and opaque check identity; keep session IDs out of unbounded metric
labels. Show a compact management-UI finding with progressive details. A guardrail
finding is distinct from reviewer denial, human approval and OS-enforced denial.

## Initial scope and review points

Start with decision-only checks and observers. No model-authored policy, arbitrary
inline executable remote configuration, content rewriting, automatic remediation,
cross-tenant checks or exactly-once observer-side effects.

The consequential proposed defaults are: required checks survive bypass and
session grants; technical failures pause safely; mandatory blocks are not human
approval prompts; output screening buffers delivery. These should remain explicit
in API review and documentation rather than being hidden implementation details.

Existing references: [app policy](APP-POLICY-CONTRACT.md),
[session grants](SESSION-GRANTS.md), [team decisions](AGENT-TEAMS-DECISIONS.md),
[SDK protocol](AGENT-RUN-PROTOCOL.md), and
[qualification plan](GUARDRAILS-AND-HOOKS-PLAN.md).
