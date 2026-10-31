# Team types and sandbox placement: plan and progress ledger

Branch: `feat/team-types-sandboxes-20261031`; base: `f13de9b9`.
Target after qualification: `openworker-harness-sdk`.
Accepted design: [types, membership and two sandbox stories](TEAM-TYPES-AND-SANDBOXES.md).
Previous completed work: [named teams ledger](AGENT-TEAMS-IMPLEMENTATION.md).

## Milestones and implementation order

1. **Delegation/type contracts.** Typed, bounded definitions and named selections;
   lead-session scope, immutable revision/digest, collision checks, per-request
   subset validation, shared-environment policy compatibility and explicit limits.
   Test invalid/duplicate input, capability escalation and scope/digest confusion.
   This foundation alone does not expose a new runnable tool.
2. **Durable approved types.** SQLite/PostgreSQL definition and approval receipts,
   pending/approved/denied transitions, safe redefinition/restart and per-lead
   catalog. Wire `team_define_type` through the real approval path in Ask,
   Auto-approve and Bypass. Compile constrained definitions without mutating the
   global application registry. Qualify SDK storage/process parity and approval-card
   data first; the distributed release-backed service adapter follows in M3.
3. **Sandbox-aware spawning.** Extend idempotent spawn with a pinned type revision
   and team/member/new placement. Persist provisioning intent, reuse the trusted
   gateway adapter, enforce selected grants/resources and attach original provider
   IDs. Provider capabilities must explicitly cover each requested restriction and
   storage mode; unsupported requests reject, never fall back or broaden access.
   Add title/background, explicit fresh context, authorized per-child directory
   and managed Git-worktree selection separately from sandbox placement. Pin
   resolved spawn metadata and permitted model overrides. Reject unsupported
   inherited snapshots explicitly until their provenance/filtering is qualified.
4. **Member lifecycle.** `team_manage` list/retire, graceful/cancel modes, admission
   and inbox/wake gates, cancellation reconciliation, capacity and owned-environment
   release. Preserve history, stable identities and unknown-result receipts.
   Distinguish cancel/interrupt-current-turn, follow-up work and final retirement;
   list/state and durable wake/admission gates must agree across adapters.
5. **Parallel and mixed-placement qualification.** Real SQLite/PostgreSQL, SDK
   process/storage matrix, two concurrent teams with multiple builders/verifiers,
   shared/independent/mixed environments, crash/approval/cancel races and telemetry.
   Repeat on owned local Kubernetes/OpenShell with bounded live-model work, then
   merge only verified functionality. Record storage capabilities and limits.

The future in-sandbox OpenWorker loop and RFC 0021 MCP integration have their own
follow-up milestone after a qualified upstream version exists. They are not part
of the current implementation or an automatic alternative provisioning path.

## Validation gates

- The lead cannot bypass its app-authorized delegation ceiling, including in Bypass.
- Approval binds a concrete type revision; another lead or changed definition cannot
  reuse it. Pending approval never creates a child or sandbox.
- Child instructions cannot replace trusted approvers or permission policy.
- Equal named grants are resolved/identity-checked by the trusted adapter; names
  alone are not proof of access or provider enforcement.
- Different hard file/network/credential scopes require separate enforcement
  contexts; a shared directory or different tool list is not a sandbox boundary.
- A lost create/cancel/delete response is reconciled against original identities.
  No automatic replay, replacement sandbox, provider switch or external-effect retry.
- New/retiring work observes finite capacity and resource limits; cleanup cannot
  delete an environment another member owns or still uses.
- Existing shared-team and legacy work-item behavior remain covered. No new tool
  is advertised as implemented before its real dispatch path is qualified.

## Progress

### 31 October 2026 — child creation comparison accepted

- Compared screenshot fields and development collaboration tools to the actual
  type/ID/message spawn contract. Accepted title/background, explicit fresh
  context, authorized directory/worktree selection, pinned metadata and constrained
  model overrides. Working directories/worktrees are not security boundaries.
- Recorded parent/initiator provenance as trusted harness metadata, never a
  model-supplied authorization assertion. Clarified current-turn cancellation,
  follow-up and retirement semantics. These additions extend M3/M4 above.
- Resuming M2 durable definitions/approval wiring. No unqualified new capabilities
  are advertised as shipped by the documentation changes.

### 31 October 2026 — decisions and baseline recorded

- Recorded the accepted two stories and RFC head/restrictions. Current work uses
  the trusted outside harness and existing gateway API; the future lead/child
  loops inside OpenShell use its sandbox-local MCP endpoint.
- Documented current Rust team domain, both storage adapters, SDK/service paths,
  shared environment, scoped-registration gap, approval and retirement gaps.
- Confirmed Python's worker discovery/staffing/connector-grant precedents. Its
  fixed initial roster and approval behavior are not copied as new Rust policy.
- Created an isolated feature branch. Azure remains in its own worktree/branch;
  no cloud provisioning is authorized by this team implementation.
- Starting milestone 1. Later runtime/approval/provisioning capabilities are not
  exposed by this documentation or the initial policy contract.

### M1 — policy/type foundation complete locally

- Added runtime `team_definition.rs`: bounded definitions, named file/network/
  credential selections, permitted placements/resources/models and host-owned
  delegation ceilings. No new dependencies or runtime tool registration.
- Added immutable binding material for lead-session identity, exact instructions,
  tools/settings, revision and resolved policy digest. Revalidation checks both
  the current ceiling and original binding. This is not an approval receipt or
  proof that a provider has enforced the settings; trusted adapters must do both.
- Shared-sandbox compatibility requires trusted resolved-policy fingerprints;
  matching profile names or different tool lists do not prove isolation.
- **12 new contract tests pass**. The targeted regression batch passes **122 tests**:
  72 runtime unit, 18 core, eight SQLite team storage, 12 existing coordination
  tests and the 12 new definition tests. Strict all-target Clippy for runtime,
  PostgreSQL store, SDK and reference server passes with test-support features.
  Formatting, diff checks and Markdown link/code-fence checks pass.
- A parsing regression caught extra fields being ignored for a unit-style shared
  placement variant. A strict struct-style variant now rejects them; the negative
  cases cover policy/image/key smuggling and approval/launcher overrides.
- No model-facing definition tool, approval card, new environment or retirement
  behavior is exposed yet. M2 remains the real storage/approval/runtime wiring.
  Serialization roundtrips here are not database restart qualification.

## M1 test groups and purpose

Source: [definition contract tests](../../harness/runtime/tests/team_definition.rs).

| Group | What it checks | What this establishes |
| --- | --- | --- |
| Delegation limits | Excess tools, files, network, credential references, CPU/memory, profiles and models reject; omitted new-sandbox grants are empty. | A requested definition cannot silently enlarge the supplied ceiling. |
| Input boundaries | Unknown approval modes, reviewer settings, launchers, raw policies/images/keys and invalid placement data reject; text/count bounds hold. | Model-authored JSON cannot smuggle host-owned configuration through this contract. |
| Identity and reuse | Pre-built/reserved names reject; scope, policy, revision and prompt/tool/model changes alter or invalidate bindings. | Approval wiring can bind an exact reusable definition without cross-lead or changed-definition confusion. |
| Sharing semantics | Only equal valid resolved fingerprints are compatible; labels and empty strings reject. | The adapter must establish actual enforcement identity before attaching a child to shared access. |

Run locally without a model, database server or Kubernetes:

```sh
cargo test --offline --locked --manifest-path harness/Cargo.toml \
  -p openworker-runtime --test team_definition
```

Next: durable pending/approved/denied definitions and canonical approval handling,
then constrained compilation and scoped catalogs across storage/process modes.

## M2a — scoped definitions and SDK runtime wiring

- Added a shared durable catalog inside the existing team transaction document:
  exact pending/approved/denied/abandoned revision records, bounded history and
  parent/policy bindings. Both SQLite and PostgreSQL use the same implementation.
- Added opt-in app-owned `TeamOptions.delegation` (Rust) / `delegation` (TypeScript).
  The model receives `team_define_type` only after that ceiling is configured.
  Ask decisions bind one definition; uncertain reviewers pause; Bypass validates
  the same ceiling. Whole-tool Always grants and `interrupt_on` exclusions cannot
  approve new definitions. No custom launcher, reviewer or approval mode is accepted.
- Approved definitions compile into private immutable SDK runtimes. Children pin
  their revision; later edits cannot replace their instructions. Private types are
  excluded from the global catalog and cannot start independent app sessions.
  Startup restores approved private bundles before SQLite coverage/PostgreSQL
  schema ownership checks. Model connections/reviewers remain in the trusted host.
- Fixed two integration regressions exposed by the new tests: run aggregation now
  routes through the child's pinned runtime, and dynamic names are validated at
  dispatch rather than changing the lead's pinned tool-schema enum after a wait.
- Verified the four-way real SDK matrix: Rust embedded/managed × SQLite/PostgreSQL,
  including real file reads, approval and host restart/reuse. TypeScript managed
  stdio on SQLite/PostgreSQL also passes, including cross-lead isolation. Expanded
  revision/cancel contracts and uncertain-reviewer/human-denial cases also pass.
- Final gate: **73 successful checks** (34 runtime contracts, seven real PostgreSQL
  contracts, 12 Rust SDK integration cases, two TypeScript real-process/database
  integrations and 18 TypeScript configuration/transport regressions). Strict
  all-target Clippy passes for runtime, PostgreSQL store, SDK and reference server;
  formatting, diff checks and documentation links/code fences pass. SDK integration
  tests run sequentially to avoid saturating the small loopback database/process
  fixture. The four-way SDK matrix was also verified before the final regression.
- The temporary PostgreSQL container is owned by this qualification and is removed
  after verification; unrelated local applications/clusters remain untouched.
- Adapter limit: only existing team placement is advertised. SDK configuration
  rejects new/member profiles until provisioning is wired. The distributed service
  adapter, workspace/worktree fields, lifecycle tools and OpenShell mixed-placement
  qualification remain required M3–M5 work; no live cloud resources were created.

### Reviewer provenance follow-up

- Found and corrected a pre-existing transport/source mismatch while preparing
  the briefing work: the initial delegated task uses the provider's `user` role
  but must not be labelled `direct_user` evidence. The reviewer now receives it
  separately as `collaborator_inputs` with `human_authorization: false`.
- Expanded the policy regression to include an agent falsely claiming human
  approval. The delegated content is available as task context but absent from
  actual human intent; existing human answers, skipped questions and size limits
  retain their behavior. All 12 policy tests and strict runtime Clippy pass.
- A child without genuine human intent available to its reviewer must pause on
  uncertainty. Future bounded parent-intent transfer needs trusted provenance;
  an inherited briefing or model-written approval claim cannot substitute for it.

## Scoped-type test groups

| Group | What we test | Why |
| --- | --- | --- |
| Durable authorization | Pending, exact approval, denial, blanket-grant rejection, stale policy/turn, cancellation abandonment and revision reuse on real SQLite/PostgreSQL. | A saved proposal is not permission; retries and restarts cannot approve different content. |
| Runtime identity | Private compilation, immutable child pins, stable schema across waits, startup reconstruction and global-catalog isolation. | One lead cannot borrow another's types, and an edited role cannot alter existing children. |
| SDK conformance | Rust embedded/managed and TypeScript managed, each using SQLite/PostgreSQL; child reads a real file and approved roles survive host restart. | Storage/process/language choices use the same runtime and permission behavior. |
| Existing behavior | Named-team coordination/storage suites and strict all-target Clippy. | The opt-in extension preserves the existing team/inbox contract. |

Sources: `harness/tests/team_type_catalog_contract.rs`,
`harness/runtime/tests/team_type_catalog.rs`,
`harness/store-postgres/tests/team_type_catalog.rs`,
`harness/harness-sdk/tests/team_types.rs` and
`harness/typescript-sdk/test/team-types.test.mjs`.

Database tests require an isolated `OW_PG_TEST_URL`; they never adopt a production
schema. TypeScript integration additionally uses `OW_TS_TEST_HOST` pointing at the
development-only `sdk-test-host` built with `test-support`. No model key is needed
for these deterministic real runtime/store/process checks. Live-model/OpenShell
qualification remains a separate gate rather than a claim made by these fixtures.

## M3a — child title, briefing and explicit fresh context

- Added bounded display title, separate project background and typed fresh-context
  selection to the existing spawn request. Context inheritance/snapshots, raw cwd,
  initiation assertions and unknown extra fields reject before allocation.
- The title becomes the child session name; stable instance/type IDs do not change.
  The briefing/task are separately encoded collaborator data beneath the durable
  delegation source label, with combined input-size bounds. They do not replace the
  registered system prompt, grant permissions or copy another conversation.
- Rust embedded/managed and remote clients expose `spawn_with_options`; TypeScript
  managed/service clients accept an optional fifth options argument. The common
  SQLite/PostgreSQL domain persists the same request and checks all fields on retry.
  The existing PostgreSQL service provisioner also applies the stored display name.
- Kept optional spawn/type-definition fields explicitly non-strict in provider
  requests, matching the existing MCP binding strategy. OpenAI Responses requires
  all properties to be required in strict schemas; local JSON-schema/typed-policy
  validation remains mandatory. Added a provider-schema regression; reference:
  [strict function calling](https://developers.openai.com/api/docs/guides/function-calling#strict-mode).
- Fast tests pass for default/explicit fresh equivalence, unsupported inheritance,
  durable identity/retry conflicts, typed briefing separation, size/control bounds
  and provider schema compatibility. Titles use the existing session-name bound
  (120 bytes), preventing a valid-looking reservation from failing later in the
  provisioner. All five SDK cases pass, including the four database/process modes;
  both TypeScript database/process integrations pass with structured briefings.
- M3a gate: **69 successful checks** across 28 runtime contract cases, eight real
  PostgreSQL contracts, five Rust SDK integrations, two TypeScript real-process
  integrations and 26 TypeScript service/configuration/transport regressions.
  Strict all-target Clippy, formatting, diff checks and document link/fence checks
  pass. Owned temporary PostgreSQL fixtures are removed after qualification.
- Remaining M3 work: workspace/worktree preparation receipts, delegated model and
  environment selection, distributed release-backed private compilation and real
  OpenShell provisioning/attachment. Retirement and mixed-placement resilience
  remain M4/M5. These are not advertised as implemented by the new options.

## M3b — durable workspace preparation and SDK child working locations

- Added typed team/directory/worktree selections and Rust/TypeScript child options.
  Qualified local SDK adapters opt in; unqualified service adapters reject non-team
  locations before reservation. Directory/worktree selections are working locations,
  never sandbox boundaries. No new model-accessible filesystem provisioning tool.
- Persist one original preparation intent in the shared SQLite/PostgreSQL team
  transaction before filesystem mutation. Recovery can inspect only that operation.
  Saved receipts pin the actual directory and Git metadata identities. A child cannot
  become ready without its acknowledged receipt, and changed request retries reject.
- Added bounded internal `workspace-stdio` framing for future ToolRunner dispatch.
  Managed worktrees use fixed Git executables, disabled hooks/environment helpers,
  checked metadata pointers and local commit references; no fetch/install/auto-replay.
  Repository helper/include config rejects. Missing or replaced workspaces close the
  reservation with an explicit possibly-unknown outcome, without starting a child task
  or poisoning the host. Files/worktrees are retained for explicit user reconciliation.
- Verification: **75 successful checks**: 34 runtime/SQLite contracts, nine real
  PostgreSQL contracts, four Rust SDK database/process cases, two TypeScript managed
  process/database cases and 26 TypeScript configuration/service/transport regressions.
  Real child file reads prove directory/worktree routing; host restart preserves the
  same child IDs and files. Strict all-target Clippy passes for runtime/store/SDK/server,
  with test support enabled. Formatting and diff checks pass.
- Tests initially exposed the tool-process sandbox restriction (rerun with authorized
  local subprocess/loopback access) and macOS `/var` canonical path assumptions in the
  fixtures; corrected fixture assertions and verified the actual production paths.
- Remaining: constrained spawn model selection; service release-backed private types;
  remote directory/worktree preparation; team/member/new environment enforcement and
  provisioning; member lifecycle; mixed-placement/OpenShell resilience. Git inspection
  tools still refuse metadata outside their working directory; a future trusted
  project-scope selector is required to support their linked-worktree inspection.
  No new sandbox/provider/cloud qualification is implied by this local SDK milestone.

### Workspace test groups and purpose

| Group | Checks | Why |
| --- | --- | --- |
| Filesystem identity | Existing directories, replaced directory/Git identities, symlinks and external Git pointers. | A saved location cannot silently adopt another object or leave its authorized project. |
| One-shot preparation | Real worktree creation, response-loss inspection, missing evidence, conflicting retries and durable dispatch across database reopen. | Restart and transport uncertainty do not duplicate or relocate filesystem effects. |
| Admission and failure | Receipt required before ready, non-lead rejection, failed preparation closes only that reservation, unsupported adapters reject selection. | Invalid preparation cannot start a child or stop unrelated host work. |
| SDK/store conformance | Rust embedded/managed × SQLite/PostgreSQL; TypeScript managed × both databases; actual reads and host restart. | The selected location works through real runtime/store/process paths, not only JSON serialization. |

Sources: `team_workspace.rs`, `agent_team_storage.rs`, shared
`agent_team_contract.rs`, SDK `team_workspaces.rs` and TypeScript `team-types.test.mjs`.

## M4a — membership gates and existing-environment retirement

- Added lead `team_manage` with bounded, cursor-based roster pages and pinned
  `finish_current` / `cancel` retirement. Rust and TypeScript SDKs expose members
  and retire for managed/embedded hosts and remote service clients. Lists include
  stable type/instance/session, resolved working/model/environment metadata and
  retirement status; prompts, background and secret values remain excluded.
- Added durable retiring state, timestamps and metadata-only pending-cleanup reasons.
  Retirement immediately blocks new turns, direct messages, mentions and inbox
  wakes. Existing turn continuations and their wait notices still work. Current-turn
  cancellation remains separate from permanent retirement and explicit follow-up.
- Active capacity includes reserved/ready/retiring instances. It is released only
  after owning runtime closure; 4,096 retained identities separately bound history.
  SQLite acceptance and PostgreSQL submission/promotion use the canonical team gate.
  PostgreSQL admission shares the team row lock with retirement. Queue reconciliation
  cancels unstarted work, and workers can claim idle retiring members for cleanup.
- SDK host and service worker paths reconcile through normal cancellation/closure,
  retaining original identities and unknown outcomes. A never-provisioned reservation
  is closed without creating a session. Shared files/environments are retained.
- Qualification found and fixed a genuine graceful-retirement continuation bug:
  a worker resumed a parked member and immediately released that runtime, interrupting
  the resumed turn. It now keeps ownership while the continuation runs. Transient
  row-lock skipping is tested across controller passes; it is not treated as proof
  that queued work is absent. An already-stopped team's next-turn read remains empty.
- Rust embedded/managed × SQLite/PostgreSQL passes both retirement modes, including
  parked work, admission blocking, capacity release, stable IDs and host restart.
  TypeScript scoped-type/workspace/retirement/restart passes on both databases.
  The one-slot PostgreSQL worker suite covers graceful and cancellation retirement,
  queued follow-up suppression, parent resumption and the existing slot-release case.
- Preserved final child events in each run's durable replay set before task admission.
  A slow/reconnecting consumer still receives completion and closure after the child
  leaves the live roster. Graceful retirement keeps current human-answer routing and
  saved PostgreSQL API control priority. Cancellation fences work, not state reads or
  cleanup; an explicit regression prevents that control-path deadlock. Wait notices
  and roster pages preserve interrupted/possibly-unknown outcomes after closure.
- M4a gate: **110 successful targeted checks** across 50 runtime/SQLite contracts,
  15 real PostgreSQL contract/worker checks, 17 Rust SDK checks, two TypeScript
  process/database integrations and 26 TypeScript configuration/service/transport
  regressions. The four Rust process/database retirement tests each exercise both
  modes (eight scenarios); additional tests cover human answers, external controls
  and delayed event replay. Strict all-target Clippy passes for runtime/store/SDK/server
  with test support; formatting, diff and documentation checks pass.
- The existing local kind/OpenShell/Langfuse fixtures were inspected read-only and
  remain healthy. This slice was not deployed to OpenShell and is not mixed-placement
  qualification. Approximately 44 GiB remains free. Only task-owned local database/
  executable fixtures were created; no cloud resources, IAM or paid model calls.
- Remaining M4 work is owned exclusive-environment release after M3 provisioning;
  new/member sandbox selection, service-scoped type compilation, provider enforcement,
  model override selection and mixed-placement/OpenShell qualification remain open.

### Lifecycle test groups and purpose

| Group | Checks | Why |
| --- | --- | --- |
| Admission and identity | Retiring capacity, blocked messages/turns/wakes, no reactivation/recycling, bounded pages and immutable command receipts. | Retirement cannot silently admit more work or free capacity before settlement. |
| Storage and authorization | Identical SQLite/PostgreSQL rules, admission/promotion fencing, observer lists, denied mutations and unrelated scopes. | Any API node sees the same durable gate without borrowing worker ownership. |
| Worker control | One slot, parked current turns, graceful completion, cancellation, queued suppression, never-created reservations and cleanup settlement. | Parents cannot starve children, and cleanup cannot restart a retired member. |
| SDK/restart | Both modes through embedded/managed Rust and both databases; TypeScript subprocess parity and persisted retirement. | Process/storage choices preserve lifecycle semantics and original IDs. |

## M3c — constrained working-model overrides

- Added an optional child working-model choice to typed Rust/TypeScript spawn
  options and model-facing `team_spawn`. It requires an exact app-delegated pair;
  unset uses the registered type default. Provider/model union schemas are backed
  by exact-pair dispatch validation, preventing cross-pair permission confusion.
- The choice is an immutable spawn/idempotency input and resolves through the
  existing session preparation path. No second model loop, provider client or
  credential store. Approver selection remains separate. Credentials/endpoints
  never become child arguments or bundle content.
- Child admission errors now close only their unstarted reservation, preserving
  the original ID and an explicit failure. Missing selected providers cannot cause
  a fallback or poison unrelated host sessions. Already-created children still
  require normal cancellation/lifecycle reconciliation.
- Four real Rust SDK cases cover embedded/managed × SQLite/PostgreSQL: approved
  type default, delegated override, rejected choice, persisted resolved selection,
  restart, then removal of the provider before another child. The last child never
  becomes a session or starts a task; the host still responds. Reviewer state is
  unchanged. All model responses here are explicitly scripted; no LLM was called.
- TypeScript integration covers both the model tool's wire choice and friendly
  application `{ provider, name }` options, alongside real reads and restart on
  both databases. Shared contracts check exact pairs, immutable retries, no
  allocation for rejected input, unavailable-admission receipts and no-delegation
  rejection. The distributed service adapter remains gated, not silently broadened.
- M3c gate: **96 successful targeted checks**: 51 runtime/SQLite contracts,
  13 real PostgreSQL contracts, four Rust SDK process/database integrations and
  28 TypeScript checks (including both real process/database cases). Strict all-target
  Clippy passes for runtime/store/SDK/server; formatting, diff and documentation
  checks pass. No cloud operations, new provider dependencies or paid model calls.
- Remaining M3 work is the
  release-backed private-type compiler, remote workspace preparation, delegated
  team/member/new environments, provider enforcement and mixed-placement/OpenShell
  qualification, followed by owned exclusive-environment retirement/cleanup.

### Model-selection test groups and purpose

| Group | Checks | Why |
| --- | --- | --- |
| Delegation | Exact pairs, cross-pair rejection, unknown choices and no-delegation rejection before allocation. | Naming an available connection does not grant permission to use it. |
| Identity | Choice is part of immutable retries; default and override are saved/resolved and survive restart. | Reconnecting or reusing an instance cannot silently change its model. |
| Failure | Provider removed before admission closes only that reservation; no session/task/fallback is created. | Missing configuration cannot run a different model or stop unrelated host work. |
| Runtime/SDK parity | Four Rust process/store modes and both TypeScript databases, real tools, separate reviewer state. | Language and storage choices reuse the same enforcement and execution path. |

## Parallel Azure integration — offline gate

- Integrated the separately authorized Azure branch with the current canonical
  workspace/lifecycle/model milestones without conflicts. The isolated combined
  branch passed 153 local checks; final parent integration reran 33 targeted checks
  and strict runtime/store/SDK/server Clippy. The model slice's 96-check gate was
  independently completed before integration. No live cloud claim is made.
- [Review and exact scope](OWS-AZURE-MERGE-REVIEW.md), including durable unknown
  self-stop receipts and identity pinned before first cleanup heartbeat, and
  [sanitized evidence](evidence/azure-combined-20261031.json). Azure remains
  explicitly experimental until a separately authorized live trial.
