# Harness observability

Status: implementation specification, 29 October 2026. Companion desktop design:
`rohitprasad15/ocw-context`, branch `docs/observability-spec`, commit `bd4dfa0`.
The desktop spec remains the product reference; this document defines the Rust
storage/service work. No Python engine changes are part of this implementation.

## Purpose

Explain where an agent's time and model tokens go, both for one turn and grouped
across runs. The management UI should start with summaries and allow drill-down
into the underlying timeline. It must work without an external observability
vendor. OpenTelemetry and Langfuse receive exports of the same measurements.

The run selector is a search field with recent results and server-side,
authorization-filtered search, bounded pages and cancellation of stale searches.
It must not download all runs into a dropdown. The current UI design is a mock;
its numbers are not measurements of the deployed service.

## Records and counting

Record completed model requests, tool executions, authorization checks,
approval/question waits, compaction and helper-agent work. A record has a schema
version, deployment identity, step identity, parent context, session/turn/call
correlation, start/end times, duration, outcome, and optional usage. Missing usage
or timing is unknown, not zero. Retried requests are separate attempts.

* Tokens belong to provider requests. Input includes cached input; output includes
  reasoning when the provider's definition does. Cache read/write and reasoning
  breakdowns are subsets, never extra tokens added to totals. Unsupported counts
  stay absent. Main work, reviewer, summary and helper are separate purposes.
* Tools have call counts, active duration and output size. Do not invent a token
  cost per tool; subsequent model processing cannot be attributed exactly.
* First-content latency includes transport, provider queueing and preparation;
  it is not provider-internal inference time. Tool-only responses need a defined
  first-output event; no visible text means the old metric may be unavailable.
* Wall-clock turn duration, accumulated request/tool duration, and exclusive phase
  duration are distinct measures. Parallel or parent/child spans must not be added
  twice. Resumption keeps the logical turn but creates distinct execution segments.
* Approval waiting is an interval between durable request and resolution facts.
  Restart and disconnected UI time remain part of that interval. An unanswered
  approval is open, not a completed zero-duration wait.
* Rollups count completed request usage once by stable observation identity. They
  never sum both a parent total and its child usage. Unknown tool outcome remains
  unknown; losing a recording never authorizes tool replay.

## Storage decisions

Use one recording/query contract with SQLite and PostgreSQL adapters. The local
SDK can use SQLite; the service uses PostgreSQL. Model execution must not depend
on a particular adapter. No ClickHouse or additional required database service.

Operationally the default is the execution PostgreSQL database plus a dedicated
observability schema. **Execution and observability always use separate
connections and independently configurable targets, credentials, TLS roots and
timeouts.** Omitting the observability database override reuses the execution
target configuration, never its live connection or transaction. Pointing the
override at another PostgreSQL database/server requires no recording format change.
Use separate execution/recording DB users when practical; no credentials belong
in bundles, prompts, command-line connection strings or recorded steps.

Raw records are insert-only until retention removes them. A repeated identical
step is idempotent; a changed record with the same identity is a conflict. There
is no foreign key or cross-database transaction linking a recording to execution
state. The query layer must authorize the canonical session in the execution
store before reading recordings. An absent recording is not proof of no action.
Do not add public recording endpoints until those authority checks exist.

Grouped summaries/rollups live in the recording database and can be rebuilt from
raw records. Large outputs remain in artifact storage, with references that retain
their original authorization. Do not copy transcript chunks into recordings.
Infrastructure metrics can continue to go to the collector; do not store every
periodic sample or streamed token in PostgreSQL.

Default raw-record retention is 30 days, configurable (1–365). Pruning is bounded,
indexed and limited to recording tables. Audit, sessions, transcripts and artifacts
have independent retention; trace pruning must not delete them. Product rollup
retention will be configured separately when rollups ship.

## Performance, durability and batching

Agent execution only tries a bounded, nonblocking enqueue. A dedicated recording
writer owns its database connection. Collection does not make a database request
on the model/tool execution thread. Export has a separate queue and connection;
a broken vendor cannot prevent locally accepted records from being written.

For M1, write one immutable record per database transaction; no explicit flush
timer or multi-record SQL batch. Existing telemetry already coalesces export
messages. This is deliberately a simple starting point, with a bounded queue,
record-size limits, SQL lock/statement timeouts and bounded shutdown. Publish
accepted, persisted, duplicate, conflict, dropped and failed-write counters.
Recording failure does not change an agent/tool result or service readiness.

Committed records survive process restart. **The queue is not a durable outbox:**
a crash, saturation or unavailable database can lose observations. Expose those
gaps and counters; this is not a billing ledger or compliance evidence. Recovery
of recordings must never execute agent work. Never blindly retry an uncertain
tool action. Observation inserts themselves have stable identity, allowing a
caller to inspect/re-submit the same record safely.

Measure writer latency/throughput locally. If needed, add transactions of up to
128 records or a 200 ms flush deadline, with fixed memory and shutdown bounds.
Do not add a new broker or durable outbox as an unmeasured optimization. Later
usage requiring lossless accounting needs execution-side durable usage facts and
an outbox; it must not be claimed by this best-effort recorder.

## Privacy and integration

Store metadata only in M1. Allowlisted model/tool identifiers are bounded; no
prompts, arguments, results, reviewer reasons, environment values, URLs, paths,
headers, credentials or private reasoning are accepted into the recording schema.
Exporter capture policy is independent: enabling local metadata must not turn on
metadata/content export. Existing content capture remains explicit and off by
default. Audit remains the security authority; recordings share correlation IDs,
not audit's retention or guarantees.

M1 consumes the existing instance-owned instrumentation. Its current session/action
hashes are pseudonymous, not anonymous. It preserves current trace/span/parent IDs
and records execution-segment facts. It does **not** claim complete logical-turn
continuity or capture of reviewer/summary usage. M2 adds explicit turn/request
correlation, missing model purposes, full usage and distributed resume links before
the UI displays those totals as complete. Keep fields/fixtures aligned with the
desktop spec rather than copying desktop-only remote-machine assumptions into the
service. No mandatory OTel dependency for local recording.

## Service configuration

Recording is opt-in; absent `observability` preserves existing behavior. Example,
same target database, independent connection and schema:

```json
"observability": {
  "schema": "ow_observability",
  "queue_capacity": 1024,
  "retention_days": 30
}
```

Separate target, credentials and TLS configuration:

```json
"observability": {
  "schema": "ow_observability",
  "database": {
    "database_env": "OW_OBSERVABILITY_DATABASE_URL",
    "database_ca": "/run/secrets/observability-ca.pem",
    "database_loopback_plaintext": false
  },
  "queue_capacity": 1024,
  "retention_days": 30
}
```

`ows-server-reference --config ... migrate` explicitly prepares both configured
schemas. Normal API/worker startup checks the recording schema, never implicitly
migrates it. An explicitly configured but unreachable recorder is reported as a
startup configuration error; an outage after successful startup is fail-open for
agent execution and visible through recorder health counters. Do not silently
fall back from the recording database to execution storage. Same-server settings
are convenience, not physical isolation or additional capacity.

## Acceptance and sequence

See [implementation plan](OBSERVABILITY-PLAN.md) and
[progress ledger](OBSERVABILITY-PROGRESS.md). Local qualification uses disposable
SQLite files and exact-owned Docker PostgreSQL fixtures, including two distinct
databases and an observability outage while execution SQL stays available. No AWS
resources or paid model calls are required.
