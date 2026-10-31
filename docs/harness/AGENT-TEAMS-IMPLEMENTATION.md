# First-class agent teams: implementation plan and ledger

Base: `57c43e92f518`; branch: `feat/agent-teams-inbox-20261030`.
Target for verified milestones: `openworker-harness-sdk`.
Contract: [one-page decisions](AGENT-TEAMS-DECISIONS.md).

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
