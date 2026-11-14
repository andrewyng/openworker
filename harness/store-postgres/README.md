# PostgreSQL persistence adapter (development)

Optional `StateStore` for the existing Rust runtime. SQLite remains the default;
select PostgreSQL explicitly in your SDK or server configuration.
This is a storage/recovery foundation, **not a deployable multi-node server**.

## Hosting

Configure the connection outside coworker bundles, using your application's
secret resolver. `Config` deliberately has no `Debug` implementation. Do not log
connection strings, raw SQL diagnostics or session content.

```rust,no_run
use openworker_store_postgres::{Config, PostgresStore};

# fn main() -> anyhow::Result<()> {
let config = Config::new(
    std::env::var("APP_DATABASE_URL")?,
    "ow_harness".to_owned(),
);
// Explicit deployment step, with a schema-owner credential:
PostgresStore::migrate(&config)?;
// At worker startup, normally with a separate least-privileged DML credential:
let store = PostgresStore::open(config)?;
// For an existing session, claim it before Runtime::open_with_store(...):
// store.claim(session_id)?;
// Inject Some(Box::new(store.clone())) into that runtime constructor.
# Ok(())
# }
```

TLS with certificate and hostname verification is mandatory by default; an
additional PEM CA can be configured via `Tls::Verified`. `LoopbackPlaintext` is
an explicit evaluation-only option, restricted to one loopback TCP host and no
host-address override. It is not a cloud deployment option. The local Docker
fixture uses trust authentication only inside its isolated test setup.

Migrations are explicit, advisory-locked, transactional and version checked.
They refuse an unknown schema version or nonempty unversioned schema. Use a
dedicated `ow_`-prefixed schema. Do not grant the runtime role schema ownership,
DDL, administrative backend-termination rights or access to other applications.
There is no SQLite import, automatic reset or upgrade of an existing production
installation. Current development schema is **v5**, including creation receipts, environment bindings and attachments.
The earlier v1–v4 test schemas are explicitly rejected; use a fresh disposable schema
for evaluation. No deployed data is migrated or reset.

## Durable turn submission from API nodes

Use `submit_turn(TurnSubmission { session_id, submission_id, text })` on an API
node's independent store connection. No session lease is required for ingress.
The app must first authenticate the caller and authorize that session. No HTTP
server or new tenant authorization system is supplied here.

Acceptance atomically persists the exact text, a preallocated turn ID, FIFO
sequence, `queued` receipt, event and metadata audit. No model/tool runs during
acceptance. The same session/request ID and exact text returns the same IDs and
current status; different text returns `SubmissionError::Conflict` with stable
code `idempotency_conflict`. IDs are scoped to a session. Nothing is trimmed or
silently truncated. Text is limited to the existing 32,000-byte runtime limit,
submission IDs to 100 bytes and session IDs to 80 bytes; blank/NUL inputs reject.
Receipt `owner_epoch` records initial promotion, not the current worker's identity;
never use it as a routing or authorization hint after takeover.

`submission(session_id, submission_id)` reads a durable receipt without execution
ownership. After response loss, reconnect and inspect this record before deciding
whether another request is needed. Retrying the same *submission ID and exact
payload* deduplicates; using a new ID is a new task. Never retry uncertain tool
effects based merely on a transport failure.
Receipts must be retained with their session. Missing receipt rows are detected
against the durable sequence counter and fail as `stored_data_incomplete`, not
as permission to execute a duplicate. There is no partial command-retention API;
do not prune these rows independently as a way to reclaim space.

`Config.max_queued_turns` defaults to 100 and may be lowered to 1–100. It counts
waiting turns, not the active turn. Apply consistent configuration across API
nodes. Duplicates remain readable even if capacity is full or the session later
closes. New work for a closed/provider-blocked session is rejected. Closing a
session with queued turns is rejected; queued-task cancellation is not built yet.

The owning worker calls `next_turn(session_id)` to **peek**, then supplies that
request to the same injected runtime's `dispatch("turn/start", ...)`. Peek never
removes or reserves work. `StateStore.accept` atomically checks live ownership,
FIFO order and session availability, promotes that receipt to `started`, and
commits the canonical user message/turn/event. Existing direct `turn/start` calls
cannot jump ahead of the queue. A still-cleaning-up runtime job leaves new work
unaccepted for the next poll rather than committing a turn it cannot spawn.

`started` means durably admitted to the runtime, **not proof that a model or tool
was invoked**. Terminal receipt status commits with the turn's final state or
recovery checkpoint. A crash after promotion yields the existing interrupted/
approval recovery behavior, not automatic queue reinsertion. A completed duplicate
never runs again. Inspect the receipt/session; do not infer execution from a 202.

API ingress and worker events share the session row lock and event cursor. The
queue sequence, operational cursor and `storage_revision` have different jobs:
the latter protects canonical worker state only. Queue-only acceptance does not
invalidate an in-flight worker snapshot or overwrite history. Receipt status can
change without altering its stable IDs/sequence. Ingress audit epoch is the
observed owner epoch for correlation, not execution authority granted to an API.

The native worker fixture demonstrates polling and renewal, but neither is a
production scheduler. Cross-process event notification/SSE forwarding is still
to implement; durable records, not wake-up hints, are authoritative. Approval
responses and cancellation use the separate control path below. A direct response
cannot override an already accepted durable decision for the same interaction.

## Out-of-band approval, question and cancellation controls

The trusted API calls `submit_control(openworker_runtime::control::Command)`.
Include session ID, stable request ID, exact turn ID and an opaque authenticated
actor ID. `Action::Response` carries interaction ID, exact decision/answer and
saved bundle/schema/tool-policy/resource-policy digests from that interaction.
`Action::Cancel` targets the current turn. The higher layer authenticates callers,
authorizes session access and supplies actor attribution; pins are not identity
credentials, and actor IDs must not be copied blindly from untrusted input.

Receipt `pending` means received, not an applied approval. Intake validates saved
identity, current target, decision values and question options. The exact request
ID/payload deduplicates even after completion. Changed payload/actor rejects;
another ID for the same target returns `control_target_conflict`. Reuse the
original stable ID. There is a 128-pending-control bound per session. Retain
receipts with the session; missing control rows fail closed. Answers remain in
operational records; metadata audit stores actor and identifiers, not answers.

New controls for closed sessions return `session_closed`; exact receipt retries
still work. Closing with admitted pending controls returns `controls_pending`
without committing the close. Hosts must settle them first. If a control arrives
between `next_turn` and turn admission, admission also returns `controls_pending`:
return to the priority control path, retaining the original queued task ID.

Owner hosts call `next_control(session_id)` before `next_turn`, then
`runtime.apply_control(session_id, request_id)`. The runtime loads the persisted
command; it does not accept replacement decision content. Cancellation is selected
first. Responses defer while a job is still checkpointing or capacity is full.
The runtime rechecks its current bundle/identity, hard path rules and existing
grant logic. Response/grant, receipt application, state, event and audit commit
atomically. Stale targets/identities become durably rejected with bounded reasons.
Database loss stays uncertain, not a fabricated rejection. `control_receipt`
reconciles response loss without ownership or replay.

Allow once, allow session, deny, question and compaction answers use the same
runtime path as local decisions. A pending control blocks admission of later
turns. Cancellation intake also fences subsequent model/tool admission in the
database. It cannot undo work admitted earlier: receipt `applied` means delivered
(or already terminal), not proof of rollback. For PostgreSQL, a started tool
without a durable result yields a paired `tool_outcome_unknown` and an
`interrupted` turn on cancellation, even if local cleanup succeeds. Never
automatically replay it. SQLite keeps its existing local behavior.

This is the library control boundary; the worker service and optional HTTP
reference described below build on it. Current-turn cancellation is supported;
cancelling a not-yet-started queued submission is not. The worker service handles
durable decisions across restart without automatically continuing an interrupted
model turn or replaying an uncertain tool effect. SQLite's
default server-control methods report unsupported. Actor attribution is stored
with controls/events/audit; it is not a new tenant authorization layer.

## Execution ownership and recovery

### Non-owner session ingress and progress

`Runtime::prepare_session` validates the existing session settings and resolves
model/package/resource identities without storing or starting work. Pass that
fresh candidate and `SessionCreation {request_id, actor, parameters}` to
`create_session`; session, idempotency receipt, event and audit commit atomically.
API-created sessions are immediately claimable, with no long-lived API ownership.
Check `creation` first on a retry so an existing receipt remains readable even
if the originally selected bundle/model configuration is no longer installed.
Exact actor/payload reuse returns the original ID; conflicts reject. Missing
retained creation receipts fail closed rather than producing a replacement.

`read_snapshot` returns a bounded UI snapshot and cursor in one transaction,
without claiming execution. `read_events` replays the existing event format
(`type`, `event_id`, session/turn IDs, data) in bounded pages. `ready` checks this
adapter's connection, not an unrelated new connection. API callers must authorize
the principal/session; these are trusted storage APIs, not public tenant auth.
See `../server-reference/README.md` for the runnable REST/SSE adapter and client.

### Remote environments (server opt-in)

`begin_environment` records an exact, idempotent provisioning intent; only its
fresh creator may issue create. Persist the verified result with
`complete_environment`. Ambiguous delivery becomes `Unknown`, not permission to
create again. Reconciliation reads the original named object and checks its
identity. Application-owned environments use explicit `bind_existing` instead.
Credentials and endpoint TLS files stay in the trusted provider configuration,
not in sessions or model/tool arguments.

Configure `Runtime::configure_environments` before sharing/dispatching the runtime.
Server session creation then requires `environment_id`, with `/sandbox` as its
workspace. The stored binding pins provider/profile/image, remote object ID,
workspace scope and accepted policy. There is no host-filesystem canonicalization
or local Docker replacement fallback. Initial OpenShell 0.1.3/Kubernetes profile
supports the image-bundled `$OPENWORKER` MCP runner only, with bounded resources,
non-root/hard Landlock, no network grants, host mounts, credential providers or
native host tools. Wider remote profiles require explicit additional work.

Sharing is explicit (`exclusive`/`shared`). Shared sessions share kernel authority
and files; they are NOT mutually isolated security principals. The database
serializes environment deletion against live session binding and pending/active
attachments. Session close/cancellation releases only its connection, never the
environment. Application-owned environments cannot be deleted by the provider.
Harness deletion requires explicit intent, zero live bindings/attachments, remote
identity inspection and confirmed disappearance before the deleted receipt.
OpenShell delete has no expected-UID precondition: operators must disallow
out-of-band delete/recreate races. Do not claim that race is fenced by PostgreSQL.
Stopped/unavailable objects require operator reconciliation rather than replacement.
Deletion has a separate bounded 90-second RPC deadline (gateway teardown can
outlast ordinary reads). After an uncertain result, `Provider::confirm_deleted`
performs read-only reconciliation; only confirmed absence allows completion of
the durable deleting receipt. It never resends delete and rejects replacement IDs.

Attachment launch UUIDs are persisted before exec; gateway deduplication is not
an infinite guarantee (successful receipts have 24-hour retention). No automatic
replay of an unknown launch/tool request. Failed/cancelled streams are poisoned;
later explicit calls may open a new connection after recording the old outcome
unknown and rechecking the exact tool catalog. Recovered old-epoch attachments
are logically released with `outcome_unknown`, not asserted dead. Real OpenShell
testing showed a delayed effect can continue after stream loss.

Run the opt-in real gateway integration separately from database-only tests:
`cargo test -p openworker-store-postgres --features openshell-test --test openshell_environment -- --ignored --nocapture`.
It requires `OW_PG_TEST_URL`, `OW_PROBE_ENDPOINT`, `OW_PROBE_CERTS`, immutable
`OW_PROBE_IMAGE`, and a Linux ELF `OW_PROBE_RUNNER` for identity (not host execution).
Optional `OW_PROBE_TLS_NAME` supplies the verified TLS name behind a private proxy.
Optional `OW_PROBE_CLI`, `OW_PROBE_CONFIG_HOME`, `OW_PROBE_GATEWAY` add actual
stop/start/PVC persistence qualification. This CLI is test-only, not a harness
dependency. The test deletes its successful disposable environment; failed runs
print exact IDs for explicit cleanup. No live model or provider credentials used.

Each `open` creates a fresh owner incarnation. Clones share that incarnation,
one connection and a bounded database-thread queue. New sessions are owned by
their creator; existing ones require explicit `claim`. A live lease cannot be
stolen. `renew` extends only a still-valid lease; `release` makes it claimable.
Dropping a connection does not release leases: they expire using database time.

Hosts using the raw adapter must arrange renewal before lease expiry and stop
scheduling on failure. `worker::Worker` now supplies a bounded worker service:
one store incarnation/runtime per occupied slot, atomic compatible-session claims
using `FOR UPDATE SKIP LOCKED`, independent heartbeat, controls before turns,
and shutdown/cleanup before explicit release. Parked approvals release the slot;
an incoming answer makes the session runnable again. Matching includes coworker,
execution digest, backend, scripted/live mode, fallback model and remote
provider/deployment/profile. A trusted factory supplies the matching runtime and
configured model connections. Default capacity is four, configurable 1–32;
lease must be at least five database operation timeouts.

The production worker requires bound remote environments. Unbound/local workers
are enabled only by the `test-support` feature for deterministic process tests.
No localhost execution fallback occurs if a remote provider is unavailable.
Cancellation-token shutdown stops claims and drains bounded cleanup. A database,
lease or runtime failure stops that worker service; it does not silently reconnect
and replay. Deployments should terminate/restart failed workers. Incomplete
cleanup retains the lease until expiry rather than asserting effects stopped.
No automatic continuation of an interrupted turn: unknown calls are paired once,
and a subsequent explicit turn can inspect the original environment. Approvals
with valid durable responses resume through the existing permission path.

A worker must not voluntarily release a session with active tool work.
Ownership checks fence durable commits, not an external operation already
admitted before lease loss. Epochs plus expected revisions reject stale writes;
there is no exactly-once external-effect guarantee.

Runtime startup recovery sees only this store's explicitly claimed sessions.
It preserves pending approval IDs and responses, interrupts model attempts, and
pairs a durably started tool call lacking a result with a synthetic
`tool_outcome_unknown` result. It never repeats that tool call. The result tells
the agent to inspect the original environment before deciding what to do next.
Automatic agent continuation remains deferred; interrupted work requires
an explicit next turn. Planned/unstarted calls retain existing runtime recovery
semantics.

`inspect` and `audit` are trusted application diagnostics and do not acquire
execution ownership. They are **not** tenant authorization APIs. The higher layer
must authenticate callers and authorize session access. Ordinary `StateStore`
reads and mutations require a valid owned session.

## Storage and failure semantics

- Canonical session metadata/history, submission identity, events and metadata
  audit commit transactionally. History is append-only. Event cursors are
  contiguous per session, not globally ordered across all sessions.
- `Session.storage_revision` is an internal optimistic-concurrency token. Reload
  after a successful canonical-state write before editing again; do not manufacture a revision.
  This source-level development API addition remains zero and omitted in SQLite
  session serialization. No SQLite schema change is needed.
- Streaming deltas update attempts/events without rewriting canonical history.
  Existing display-history filtering and bounded pages are preserved.
- Audit rows contain IDs, event kind, ownership epoch and database timestamp,
  not raw arguments or message content. Operational state still contains full
  conversation/tool data needed for continuation. This is not a promise of
  content-free storage or the completed S4 audit/retention contract.
- SQL/connection failure poisons the actor; there are no transparent mutation
  retries. A timeout can mean an unknown commit outcome. Reconnect, inspect
  durable IDs and reconcile before deciding whether a new operation is safe.
- Caller waits and SQL/lock waits are bounded. The existing synchronous trait
  uses a dedicated database thread and `block_in_place` on multi-thread Tokio.
  Current-thread Tokio callers receive an error. This remains an interim
  synchronous bridge, not a fully asynchronous high-throughput database pool.
  An underlying stuck I/O thread can outlive a timed-out caller; don't use
  unbounded reconnect loops as a substitute for a circuit breaker.

## Tests

Tests need an explicitly supplied **disposable** PostgreSQL database. They create
unique schemas, install fault triggers and terminate only their own connections.
Do not point them at an application or production database.

```sh
OW_PG_TEST_URL=postgresql://postgres@127.0.0.1:5432/postgres \
  cargo test --locked -p openworker-store-postgres --features test-support \
  -- --ignored --test-threads=1
```

Database tests cover multi-API receipt races, changed-payload conflicts, queue
capacity/FIFO, lost receipts, cross-session isolation, atomic promotion/terminal
state and SQLite/domain conformance, transactional rollback, stale
revisions, concurrent claims, explicit recovery scope, approval preservation,
unknown tool results, deleted data, bounded lock waits, connection loss, schema
rejection and TLS/executor fail-closed behavior. The Linux-only worker test kills
three real native-process incarnations around an approval and a filesystem side
effect, then verifies explicit inspection without replay. A second Linux process
test submits through two independent API connections while the worker is dead,
then observes its replacement consume FIFO through the real approval/model/tool
path. Its scripted model
needs no API key. `pg-worker-fixture` is feature-gated test code: never ship it.

### Bounded concurrent session acceptance

The Linux concurrency suite uses the production worker service with two real
processes and two slots per process. The fixture tool appends and fsyncs an effect
record (session, turn, call and process IDs) before waiting at a test-controlled
completion barrier. It does not overwrite prior evidence or pretend a started
effect did not happen. No model credentials are needed.

| Scenario | Checked invariant |
|---|---|
| Eight sessions, four occupied slots | Four initial effects across more than two lease periods; overflow waits. |
| Same submission through two API-store connections | One durable turn ID per submission; follow-ups retain per-session order. |
| Kill a two-slot worker or terminate one owned DB connection | Its service stops; another worker survives; uncertain effects are not replayed. |
| Cancel an active session through a different connection | Control settles without interrupting its sibling session. |
| Six pending approvals with only four slots | All six park; approvals do not monopolize execution capacity. |
| Session grant, Allow Once, denial and cancellation | Grant stays in one session; another session asks again; denied work does not execute. |
| Twelve concurrent producers and a four-item queue | Exactly four admitted, eight QueueFull; exact retries remain available and another session can submit. |

```sh
OW_PG_TEST_URL=postgresql://postgres@127.0.0.1:5432/postgres \
OW_CONCURRENCY_REPORT_DIR=/tmp/ow-concurrency-private \
  cargo test --locked -p openworker-store-postgres --features test-support \
  --test worker_recovery concurrency:: -- --ignored --nocapture --test-threads=1
```

`--test-threads=1` separates independent fault scenarios; each scenario still
contains concurrent workers, slots and submitters. Optional private JSON evidence
contains the actual effect identities and cursor counts. The three concurrency
scenarios remove their own uniquely generated schemas after success and stop only
their owned child processes. Failures may retain the schema for inspection; never
prune all test/application schemas. Other existing tests may also retain schemas.

This proves bounded logical correctness, not a throughput SLA, scheduler fairness,
OS filesystem isolation, tenant security or a general exactly-once guarantee.
The native probe uses explicitly test-only unsandboxed workspaces. The separate
`openworker-server-reference --features openshell-test --test replicas` qualification
checks real HTTP/SSE clients, API/worker replacement and OpenShell tool execution.
Run that on fresh owned sandbox resources rather than a live application's workspace.

## Production deployment

Monitor database memory, connections, latency and retained-data growth. Choose
worker slots from your own workload measurements. Applications own tenant quotas,
ingress authorization, database high availability and upgrade/cutover procedures.

The Rust SDK supports SQLite or PostgreSQL; see
[sessions and teams](../../docs/harness/guides/sessions.md),
[versions and compatibility](../../docs/harness/guides/releases.md) and
[deployment options](../../docs/harness/guides/deployment.md).
