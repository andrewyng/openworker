# Agent teams: test cases and purpose

The suite checks one coordination implementation across storage and transport
choices. Deterministic models make concurrency and failure cases repeatable; real
PostgreSQL workers and a Kubernetes/OpenShell run test deployment behavior. The
[ledger](AGENT-TEAMS-IMPLEMENTATION.md) records which qualifications passed.

## 1. Identity and communication rules

**Purpose:** distinguish agent definitions from live instances, preserve identity
across retries, and make notification behavior predictable.

Source: `harness/runtime/tests/team_coordination.rs`.

- Ten builder instances, ten verifier instances and one lead have distinct IDs.
- Identical spawn retries return the original session; changing type/task rejects.
- Capacity includes the lead; closed identities cannot be reused as new agents.
- Exact mentions notify, repeated mentions deduplicate, `@all` freezes recipients
  at posting time and excludes the sender. Email/code/escaped mentions do not notify.
- Unknown targets and invalid/reserved names reject clearly.
- Board/direct channels can be enabled independently, equally for lead and workers.
- Before/after history uses exclusive anchors, chronological results and bounded
  page sizes; invalid anchors/order/oversized reads reject.

**Result:** models and applications can reason about instance identity, notification
scope and pagination without relying on database-specific behavior.

## 2. Transactional storage and privacy

**Purpose:** prove messages, results and delivery receipts survive faults without
loss, duplication, cross-session disclosure or stale-owner mutation.

Sources: `harness/tests/agent_team_contract.rs`, runtime `agent_team_storage.rs`,
and PostgreSQL `agent_team_contract.rs`.

- The same contract runs on real SQLite and PostgreSQL.
- Passive board posts do not wake recipients; direct messages stay off the board.
- Inbox batches are bounded at 20 and retain overflow for the next model call.
- Restart preserves queued messages, consumed cursors and waits.
- Injected SQLite event-write failure rolls back board rows, fan-out, history and
  inbox acknowledgements. Retrying after rollback consumes the original message once.
- Internal tool effect/result commits once; a stale second execution cannot append
  another board message or tool result.
- Application command IDs deduplicate per author. Conflicting payloads reject;
  another author's same ID does not impersonate the original sender.
- Public snapshots omit private command receipts and initial-task payloads.
- Concurrent PostgreSQL writers preserve all messages and one ordered sequence.
- Journal-only progress does not invalidate an unchanged metadata snapshot. Actual
  canonical edits still reject stale saves; event cursors remain serialized.
- Non-owners cannot acknowledge inbox input; an old owner cannot mutate after a
  new claim. Observers cannot send; unrelated session scopes cannot read the team.

**Result:** storage is the durability and authorization boundary, rather than an
in-memory delivery queue or a client's retry assumptions.

## 3. Waiting, wakeups and resource limits

**Purpose:** avoid deadlocks and runaway work while preserving a useful continuation.

Sources: runtime storage tests and PostgreSQL `agent_team_worker.rs`.

- Busy agents receive inbox input before their next model call; idle ones wake.
- Approval-paused, cancelled and failed sessions are not automatically restarted.
- Waits resume on an explicit message, selected child completion or timeout.
- A persisted wake survives another restart. Parallel tool results all precede
  inbox/wait notices; both Anthropic and Chat Completions request converters accept
  the resulting canonical history.
- Inbox wakes join the existing submission FIFO; queued user input keeps its place.
  An explicit mention can schedule an idle lead before its first user turn.
- A real **one-slot** PostgreSQL worker pool parks the lead, releases ownership,
  runs the child, then reacquires the lead under another claim. The child's proof
  appears in the resumed input; waiting consumes no worker slot.
- Team model/output reservations aggregate across instances. Known successful usage
  settles once; failed/unknown usage retains its reservation.
- Default and requested limits preserve a bounded summary allowance inside the
  overall output budget. Per-agent limits remain independent.
- Exhausting the shared allowance returns a short completed limit summary, rather
  than failing the lead. A smaller team output cap reduces the session reservation;
  a denied request is not charged as an actual provider attempt. A two-process-mode
  regression uses a 1,024-token team ceiling and refuses the second action.
- Run completion includes reserved/unstarted children and pending inbox messages.
- Team stop cancels queued but unstarted work, cancels parked children through the
  normal control path, and rejects new submissions or members. The embedded and
  managed SDK modes both reconcile the durable stop for all members.

**Result:** children can make progress with limited server capacity, and restarts
or delayed messages cannot silently bypass limits or resurrect stopped work.

## 4. SDK and process boundaries

**Purpose:** verify the public APIs compose the real Rust runtime rather than a
parallel SDK implementation.

Sources: Rust SDK `agent_team.rs`; TypeScript `test/agent-team.test.mjs`.

- Embedded and managed-subprocess Rust clients run lead → builder → verifier.
- Multiple instances of one type remain independently addressable.
- Journal streams attribute events to the instance, type and team.
- A newly spawned child's question can be answered through the lead handle.
- Persisted references reattach the original team and history after restart.
- TypeScript exercises managed stdio streaming, result retrieval, command
  deduplication, disabled-channel rejection and restart.
- Existing agent, desktop callback, approval and legacy work-item team regressions
  remain covered. New named teams do not change legacy assignment/review semantics.

**Result:** applications can change process placement or storage without reimplementing
coordination, and an evolving roster does not break streaming or human responses.

## 5. Kubernetes, OpenShell and live model qualification

Source: `deploy/teams/cluster.mjs`, against an explicitly owned local fixture.

- TLS-authenticated SDK calls deploy activated releases and a shared RuntimeProfile.
- The service runs with one worker slot. The lead spawns builder and verifier in
  sequence, with both children attached to the original environment ID.
- Builder executes a real shell command to create an HTML page; verifier independently
  reads/checks it and writes verification evidence.
- Mention messages reach lead continuations; lease transfers demonstrate parked
  waits. Recorded tools execute once, with no unknown-result replay.
- Reconnecting reads the original sessions and team, without starting replacement
  agents or environments.
- Durable stop/cancellation, telemetry attribution and live-model follow-up are
  recorded in the ledger with the final qualification evidence.
- The real shared-sandbox test stops a parked child and checks model/tool summaries
  for each member. Cleanup waits for asynchronous pod deletion after the provider's
  environment-deleted receipt, preserving unrelated sandbox identities.
- Cleanup closes owned sessions and deletes only the original test environment;
  unrelated sandbox pod identities must remain unchanged.

**Result:** the reference service integrates catalog authorization, scheduler,
ToolRunner and OpenShell behavior under deployment conditions. Scripted tests do
not substitute for a separately recorded live-model result.

## Running the suite

```sh
cargo test --offline --locked --manifest-path harness/Cargo.toml \
  -p openworker-runtime --test team_coordination --test agent_team_storage

# OW_PG_TEST_URL must identify a disposable PostgreSQL database.
cargo test --offline --locked --manifest-path harness/Cargo.toml \
  -p openworker-store-postgres --features test-support \
  --test agent_team_contract --test agent_team_worker -- --ignored

cargo test --offline --locked --manifest-path harness/Cargo.toml \
  -p openworker-sdk --features test-support --test agent_team

npm run build --prefix harness/typescript-sdk
OW_TS_TEST_HOST="$PWD/harness/target/debug/sdk-test-host" \
  node --test harness/typescript-sdk/test/agent-team.test.mjs

# Fresh owned local installation; no cloud deployment or external tool replay.
NODE_EXTRA_CA_CERTS="$STATE/input/api-ca.crt" \
  node deploy/teams/cluster.mjs "$STATE" "$EXACT_SERVICE_ID" "$NEW_IGNORED_OUTPUT"
```

Private logs, credentials and generated artifacts stay in gitignored qualification
state. Test summaries describe verified behavior; they do not claim that a process
crash can undo an already-admitted external effect.
