# Diagnostic recording storage

M1 foundation for [harness observability](../../docs/harness/OBSERVABILITY.md).
Metadata-only immutable steps, SQLite/PostgreSQL adapters and a bounded independent
writer. Does not depend on an OpenTelemetry exporter. Committed rows survive
restart; the queue is not a durable outbox, billing ledger or audit record.

## Local host

The recording database is separate from the execution database, even for SQLite.
Create its directory inside the application's private state directory. Unix
database files are created with mode 0600; symlink database files are rejected.

```rust,no_run
use openworker_observability::{sqlite::Sqlite, Recorder, RecordingSink};
use openworker_telemetry::{Capture, ContentPolicy, Operation, Outcome, Telemetry};
use std::{path::Path, time::Duration};

# fn main() -> anyhow::Result<()> {
let store = Sqlite::open(Path::new("observability.sqlite"))?;
let (recorder, recording) = Recorder::start(Box::new(store), "my_app".into(), 1024, 30)?;
let statistics = recorder.statistics();
let sink = RecordingSink::new(recorder, "my_app".into(), "embedded".into(), None, Capture::Off)?;
let (telemetry, dispatch) = Telemetry::start_with_content(
    sink, 2048, ContentPolicy { capture: Capture::Metadata, ..Default::default() },
)?;
// A trusted host passes `telemetry` to Runtime::open_instrumented(...).
let mut span = telemetry.span(Operation::Model, Some("session-id"), Some("request-id"));
span.metadata("gen_ai.request.model", "example-model");
span.usage(100, 20);
span.finish(Outcome::Ok);
// Stop runtime work first, then drain dispatch before the recording writer.
let _ = dispatch.shutdown(Duration::from_secs(5));
let _ = recording.shutdown(Duration::from_secs(5));
assert_eq!(statistics.snapshot().persisted, 1);
# Ok(())
# }
```

This is a trusted low-level runtime integration, not a new high-level Rust or
TypeScript Harness SDK option. The SDK's default remains unchanged. No prompts,
tool arguments/results, reviewer reasons or reasoning text enter the stored type;
even an explicitly content-enabled exporter cannot add content fields to it.

## Service

Add `observability` to the server-reference JSON configuration; see the
[same-target and separate-target examples](../../docs/harness/OBSERVABILITY.md#service-configuration).
Run the existing server's `--config <path> migrate` command explicitly. PostgreSQL
defaults to verified TLS; plaintext is accepted only for explicit loopback tests.
Separate-target credentials and roots do not inherit from the execution database.
Normal startup only checks the schema and opens the independent writer connection.
The PostgreSQL adapter is synchronous: use a blocking thread for migration,
opening and trusted reads. The service does this explicitly; the dedicated writer
owns all subsequent insert/prune calls.

Each process's authenticated `/readyz` includes `recording` counters: accepted,
persisted, duplicates, conflicts, dropped, rejected, abandoned, failed writes,
pending, pruning failures and last successful write time. An outage after startup
does not fail agent readiness. A failed write can mean an unconfirmed commit;
inspect the stable step ID rather than assume absence or replay agent work. There
is no automatic connection replacement or write retry in M1; after a broken
connection, restart the process to establish a fresh writer. A lock timeout leaves
the connection reusable. Queries are trusted host APIs, not public HTTP endpoints.
Counters are process-local diagnostics, not durable accounting. Local PostgreSQL
qualification uses explicit loopback plaintext; the new recording connector's
verified-TLS path has not yet been qualified against a real TLS database.

Default retention is 30 days. Once per minute the writer attempts at most 1,000
expired deletes for its own deployment only. Pruning never touches execution,
audit or artifact tables. Backlogs may require multiple passes. History is bounded
by deployment/session/cursor and a maximum page of 1,000 rows.

Runtime instrumentation supplies linked execution segments of one logical turn,
main/reviewer/compaction requests, provider-reported cache/reasoning subsets,
tool durations/result sizes, policy and lifecycle facts, and durable human-wait
intervals. Session/turn/action IDs are hashed; model/tool names are allowlisted
metadata. Optional typed call contexts let hosts bind additional model purposes;
they do not introduce a helper/title feature. New payloads use v2 while v1 remains
readable in the unchanged indexed SQL schema. No historical values are inferred.
Rollups, authorized search and management UI integration remain later milestones.
Never display an incomplete total as complete. See the
[M2 accounting contract](../../docs/harness/OBSERVABILITY.md#m2-accounting-contract).

## Local tests

```sh
cargo test --locked --manifest-path harness/Cargo.toml -p openworker-observability --all-features
node deploy/reference/qualify-observability.mjs
```

For native Linux runtime/restart and real PostgreSQL worker-replacement tests:

```sh
docker build -f deploy/reference/Dockerfile.observability \
  -t openworker-observability-qualification:m2 .
OW_OBSERVABILITY_LINUX_IMAGE="$(docker image inspect --format '{{.Id}}' openworker-observability-qualification:m2)" \
  node deploy/reference/qualify-observability.mjs
```

This pinned test image disables LTO to shorten qualification linking. It contains
test executables only and must not be deployed as a service. The helper accepts
an exact local image ID, never pulls it, and removes its labeled Linux/PostgreSQL
fixtures and generated password files. Build caches and the test image remain
available for reruns; unrelated containers are never changed.

The second command requires Docker and the cached pinned PostgreSQL image; it
creates a labeled loopback-only container and two databases, tests adapter parity
and actual service migration, then forces recording-table contention while the
real execution adapter stays available. It reports a small single-connection
per-record write probe, not a deployment capacity guarantee. It removes its exact
container and generated password. Other containers, clouds and model keys are
untouched. Retention, privacy, duplicate/conflict, queue pressure, bounded shutdown
and stalled-exporter isolation are covered separately. The fixture integration
tests are explicitly ignored outside that runner.

## Authorized queries (M3)

The reference service exposes run search, per-turn timeline/summary and bounded
multi-run summaries. See the [query guide](../../docs/harness/OBSERVABILITY-QUERIES.md).
Search uses execution-store authorization before pagination. Recording reads
recheck canonical permission after a separate read-only SQL transaction; a
recording outage never becomes an authorization fallback.

Trusted embedded hosts can call `Store::timeline` with a deployment and explicit
session/turn-hash scopes, then `summary::build`. Both SQLite and PostgreSQL satisfy
that bounded contract. This low-level interface has no authorization authority;
an application must authorize the canonical sessions itself. Projection v1 is
rebuilt from at most 4,096 raw rows and reports incomplete coverage explicitly.
There is no persistent summary cache or mandatory additional database.
