# First-class agent teams: implementation plan and ledger

Base: `57c43e92f518`; branch: `feat/agent-teams-inbox-20261030`.
Target for verified milestones: `openworker-harness-sdk`.
Contract: [one-page decisions](AGENT-TEAMS-DECISIONS.md).

Follow-up accepted design: [session-defined types, team management and sandbox
placement](TEAM-TYPES-AND-SANDBOXES.md), tracked in the
[new implementation ledger](TEAM-TYPES-AND-SANDBOXES-PLAN.md). It distinguishes
today's trusted outside harness from the future RFC 0021 in-sandbox agent loop.

## Milestones

1. **Shared coordination contract.** Define agent types versus instances, stable
   spawn identity/idempotency, configurable team capacity, communication policy,
   mention resolution, ordered history pagination and busy/idle/paused delivery
   decisions. Keep these rules in Rust core, independently of database and SDK.
   Test the agreed behaviors, including a 21-member roster, recipient snapshots,
   both communication modes disabled, identity conflicts and paging boundaries.
2. **Durable board and inbox adapters.** SQLite and PostgreSQL implement one
   transactional domain contract: author-bound append, recipient fan-out, message
   deduplication, pending batches and durable continuation receipts. Add tests
   against both real stores, concurrent writers, restart, rollback and ownership
   fencing. No replacement in-memory queue may be the durability boundary.
3. **Runtime tools and waiting.** Expose spawn/send/post/read/wait for registered
   agent types. Busy recipients get inbox batches at their next model request;
   idle recipients wake; stopped/paused ones remain parked. Preserve provider
   tool-result ordering and attribution. Persist waits/timeouts, release server
   slots, deduplicate wakes and resume through the existing scheduler. Keep old
   work-item API behavior explicit rather than silently mixing the two contracts.
4. **First-class SDKs and environments.** Rust and TypeScript create/resume team
   sessions with registered types, default board-only communication and one shared
   OpenShell environment. Child sessions pin their own model/tool configuration.
   Record parent/child relationships, enforce total and per-agent budgets, stream
   an attributed activity tree and keep storage/transport choices independent.
5. **Reference team and qualification.** Lead spawns builder then verifier; extend
   to multiple instances and disjoint directories/ports. Run deterministic tests,
   real SQLite/PostgreSQL tests, local OpenShell and browser/telemetry checks. Verify
   unknown outcomes without replay, a one-slot worker pool, inbox delivery during
   tools, cancellation, restart and owned-environment cleanup. Finish with a live
   coding team before the fresh GCP deployment trial.

## Implementation boundaries

- The board's new mention-only contract has no implicit assignment/status/completion
  notifications. Explicit `wait_agent` observes child lifecycle independently.
- No threads, GitHub/AWS subscriptions, model-selected arbitrary sandbox creation
  or per-agent communication overrides in the first version.
- Existing team tools currently assume fixed session membership and task assignment.
  Do not expose the new model-facing tools before their execution paths exist.
- Keep failure/recovery receipt semantics from the resilience suite. No automatic
  retry of an uncertain child launch or external effect.
- Use owned local test fixtures. No cloud provisioning or paid model calls for
  contract/storage milestones. Keep raw fixtures and credentials gitignored.

## Progress

- Recorded the accepted decisions before implementation. Read existing shared team
  domain, SQLite/PostgreSQL adapters, SDK wrapper and worker scheduler. Existing
  board delivery uses queued turns, and membership is capped at 16. These do not
  yet provide named multi-instance spawning or next-call inbox injection.
- M1 started: establish the shared contract first; persistence, scheduler and SDK
  exposure remain separately qualified milestones. The one-page tool examples are
  a design contract, not a claim that the current SDK already exposes them.

### M1 — shared coordination contract complete

- Added `runtime::team_coordination`, with no new dependencies or runtime tool
  exposure. It provides named instance reservations, pinned session references,
  byte-identical spawn deduplication/conflicts, configurable capacity (default 32),
  channel policy, exact mention routing and frozen broadcast recipients.
- Added bounded, exclusive before/after history normalization from at most 51
  fetched rows; adapters do not need to load full history. Delivery decisions
  distinguish busy, idle, waiting, starting, paused and stopped agents.
- Literal mentions can be escaped or placed in backtick code literals; email
  addresses do not trigger delivery. Unknown explicit targets reject clearly.
- **12 new contract tests pass.** A 21-member case covers ten builders, ten
  verifiers and a lead. Other cases cover duplicate/colliding session identities,
  snapshot roundtrip, all four communication modes, post-time recipient sets,
  capacity and pagination. Snapshot roundtrip is not a database durability test.
- Local targeted regression passes **105 Rust tests**: 72 runtime unit, 18 core,
  three existing SQLite team transaction tests and 12 new contract tests. Strict
  all-target runtime/PostgreSQL Clippy with test-support passes; formatting passes.
- Existing legacy team tools and work-item scheduling are unchanged. M2 remains:
  real SQLite/PostgreSQL append/fan-out transactions, inbox batches and continuation
  receipts. No cloud resources or model credentials were needed for M1.

### Full implementation batch — authorized

Complete M2–M5 without stopping at each milestone. GCP remains deferred.

- Implementing a shared transactional domain used by both adapters. The roster is
  bounded metadata; messages are append-only rows, indexed by team and sequence.
  Inbox receipts belong to the recipient session and commit with canonical input.
- Runtime tools are separate from legacy work-item tools. New teams opt in through
  explicit APIs; disabled channels are absent from schemas and rejected by storage.
- In progress: adapter tests, host reservation reconciliation, durable waits,
  SDK surfaces, PostgreSQL worker scheduling, shared OpenShell qualification.

### M2–M4 implementation and initial qualification

- Added append-only board/direct messages and a shared transactional domain to
  SQLite and PostgreSQL. Recipient cursors commit with canonical transcript input;
  overflow stays queued. Application command IDs deduplicate writes; changed
  retries reject. Tool results and coordination effects commit together.
- Added model tools `team_spawn`, `team_wait_agent`, `team_post_board`,
  `team_read_board`, and policy-gated `team_send_message`. Roster reservations pin
  canonical child session IDs. Inbox input preserves sender attribution and cannot
  authorize an action. Idle instances wake, cancelled/failed instances stay parked.
- Added Rust `Harness::new_team`/`AgentTeam` and TypeScript `newTeam`/`AgentTeam`,
  with persisted references, history, commands, and streamed instance attribution.
  The remote service exposes the same domain through approved release mappings and
  one original shared environment. Legacy work-item teams retain their own API.
- Added cumulative team model/output budgets alongside each agent's turn limits.
  Failed/unknown usage retains its reservation. A bounded final summary is reserved
  inside the team token allowance, with a deterministic fallback when unavailable.
- Initial results: six SQLite storage tests, the real PostgreSQL shared contract,
  embedded and managed Rust team scenarios, and a TypeScript managed streaming/
  restart scenario pass. The TypeScript stream caught a nested store-lock deadlock
  in new attribution code; fixed by reading journal and membership before iteration.
- A real PostgreSQL **one-slot worker pool** test passes: lead parks/releases its
  lease, child runs, lead reacquires ownership and continues with the child's message.
- Still qualifying: approved service-child provisioning, local Kubernetes/OpenShell,
  authorization/fencing/concurrent ingress, final regression/lint checks and live
  team run. An isolated namespace and owned images are prepared; no cloud resources.

### Qualification checkpoint

- Real PostgreSQL contract/concurrency/authorization tests and existing auth/ingress/
  legacy-team regressions pass: **15 tests**. Read-only and unrelated-session
  credentials cannot send/read beyond their grants; stale owners cannot acknowledge
  inboxes. A separate one-slot fixture now uses a private controller directory for
  each runtime incarnation to avoid cross-incarnation fixture state contention.
- Core storage coverage now includes author-scoped command IDs and public-snapshot
  privacy. Seven SQLite tests pass; public snapshots exclude private receipts and
  initial tasks. Default team budgets now aggregate working calls across children.
- Linux SDK regression pass: **20 executed tests**, covering named teams, earlier
  agents/work-item teams, callback crash recovery, dynamic agents, desktop APIs and
  human responses. PostgreSQL variants in that VM batch were explicitly skipped;
  database contracts run separately against the isolated local database.
- The initial real Kubernetes/OpenShell run built and independently verified HTML
  in one environment with one worker slot. Its test script used `close()` instead
  of the service SDK's `disconnect()` during cleanup; corrected. Reconciled the
  saved team/session identities, closed the original sessions and deleted only the
  original environment. No external tool actions were replayed. A fresh final
  installation will qualify the corrected script and final binaries.
- Added the team QuickStart, grouped test-case guide and CI entry points. Final
  qualification continues before merging; GCP has not been started.

### Final concurrency and cancellation fixes

- Inbox wakes now join the normal PostgreSQL submission FIFO, never dispatching
  ahead of queued user work. Added a real-store regression and a scheduling test
  for a never-started idle lead receiving an explicit mention.
- Journal-only progress and roster receipts preserve canonical metadata revisions.
  Actual metadata writes still invalidate stale snapshots; commit-time ownership
  fencing and serialized event cursors are unchanged. This fixes a reproduced
  PostgreSQL managed-SDK stale-save failure without retrying tool effects.
- Wait wakeups persist a `resuming_agents` checkpoint. Buffered notices are inserted
  only after every output in a parallel tool batch; regression checks both actual
  Anthropic and Chat Completions payload conversion after restart.
- Durable team stop reconciles all active/parked local SDK members, including an
  idle lead. Server reconciliation also cancels unstarted queued submissions and
  uses the existing cancellation control path for active members.
- Verified: six PostgreSQL team contracts plus the one-slot worker test; ten
  existing PostgreSQL store/fencing tests and authorization/ingress/legacy-team
  regressions; both PostgreSQL Rust process modes; TypeScript SQLite/PostgreSQL
  streaming/restart; 18 TypeScript transport/config tests; Linux SDK four team
  tests (PostgreSQL variants run separately on macOS against the real database).
- Real OpenShell file build, independent verification, reconnect and parked-child
  cancellation pass. Corrected two qualification-script issues (stale client
  handle after reconnect, asynchronous sandbox pod removal); a complete fresh
  team run and bounded live-model follow-up remain before final merge.

### Live qualification and bounded-stop correction

- The first live Anthropic team completed its builder and verifier, but the lead
  hit the test's 30-call aggregate ceiling and failed rather than summarizing.
  Preserved that failed evidence. Fixed the runtime to honor the persisted team
  stop reason, refund requests never admitted to the provider, and reduce a
  session's tentative output reservation when the remaining team cap is smaller.
- Added a repeatable SDK regression in both process modes: one work call, one
  tool-free summary, a 1,024-token ceiling, and no second board effect. It passes
  on macOS and Linux. A deployed equivalent is included in `deploy/teams/cluster.mjs`.
- Runner revisions are pinned in installation/execution identities. An old team's
  queued summary did not run after changing the runner binary. Stopped that team
  and cancelled the unstarted submission; used its matching controller revision
  to finish its original sandbox deletion. No external tool effects were replayed.
  Drain old installations or retain their matching runner/controller when upgrading.
- A **fresh live `claude-sonnet-5` team on the fixed service and matching runner**
  passes: lead six model calls, builder four, verifier four. Both children shared
  the original environment, posted mention evidence, and completed independently.
  Per-member model/tool/turn summaries are recorded. The original environment was
  deleted. The captured complete HTML passes browser increment/reset and JS-error
  checks. Private evidence: `.ow-service-agent-teams-20261031/live-final/`.

### M2–M5 — complete and qualified, 31 October 2026

All four remaining deliverables are complete: durable board/inbox adapters, runtime
coordination/waits, first-class Rust/TypeScript/service APIs, and end-to-end team
qualification. Verified changes are ready for `openworker-harness-sdk`; no GCP
trial or cloud provisioning was started in this batch.

Final checks:

- **113 core/runtime tests** pass (72 unit, 18 core, eight SQLite team storage,
  three legacy SQLite team storage, 12 coordination rules).
- **29 real PostgreSQL tests** pass: six new contracts, one one-slot worker test,
  ten store/revision tests, eight authorization, three ingress and one legacy team
  contract. No mock database substitutes for these checks.
- Rust SDK named-team tests pass on macOS and Linux: five executed per platform,
  including embedded/managed flows, dynamic child questions, team stop and bounded
  aggregate summary. The two PostgreSQL process-mode scenarios pass separately
  against the local database. Earlier Linux SDK regression matrix also passes.
- TypeScript: **18 config/transport tests**, **eight service transport tests**,
  and both real-host SQLite/PostgreSQL team streaming/restart scenarios pass.
  Server reference unit/boundary tests: **10 pass**. Strict all-target Clippy,
  TypeScript compilation, Rust formatting and diff checks pass. CI includes the
  named-team contracts and supported process/storage matrices.
- Final pinned Linux service and ToolRunner images pass the deployed **one-slot
  Kubernetes/OpenShell suite**, including file build, independent verification,
  mention delivery, lease transfer, reconnect, parked-child stop, no duplicate
  effects, per-member model/tool accounting and a forced aggregate-budget summary
  with no second tool effect. Evidence: private `cluster-budget-qualified/report.json`.
- Fresh live-model team and browser qualification pass, as recorded above. Failed
  exploratory evidence remains separate; no failed trial is relabeled as passed.
- Owned environments are deleted and unrelated sandbox pod identities preserved.
  Dedicated Linux VM returned to stopped state. The owned qualification namespace,
  PVC and labeled PostgreSQL test container/volume are removed. Private evidence
  remains gitignored; unrelated OpenShell and Langfuse workloads are untouched.

V1 boundaries: one shared execution environment; no model-created sandboxes, board
threads or external subscriptions. Local named SDK teams require an explicit off
profile or an already-bound shared environment; use the service API for OpenShell
teams. Shared generation limits count working requests and the final summary;
auxiliary compaction/reviewer requests retain their existing per-turn limits.
These limits are not a combined bill for all model purposes.

Reviewer entry points: [QuickStart](AGENT-TEAMS-QUICKSTART.md),
[grouped test cases](AGENT-TEAMS-TEST-CASES.md),
[wire contract](AGENT-RUN-PROTOCOL.md#named-agent-teams), and the
[accepted decisions](AGENT-TEAMS-DECISIONS.md). GCP trial is the next separate task.
