# Harness observability

Status: M1–M5 recording, accounting, authorized queries, management UI and export
alignment implemented and locally qualified, 29 October 2026.
[M5 qualification evidence](evidence/observability-m5-local-20261029.json).
[Query API guide](OBSERVABILITY-QUERIES.md).
Companion desktop design:
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
It does not download all runs into a dropdown. The management UI now reads live
authorized service data; the separate design mock remains sample data.

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

## M2 accounting contract

The shared model request boundary now records the working agent, separate
auto-approver and compaction model. Each HTTP request has its own span, including
the second HTTP request after a rejected access token is refreshed. Runtime
retries retain their retry ordinal and distinct action identities; authentication
refresh attempts share the runtime attempt identity but have distinct spans.
Provider usage snapshots replace the latest values; they are never accumulated
as additional calls. Only `model.attempt` owns tokens. `model.requested` is an
immutable, zero-duration open fact whose parent identifies the request span.
A missing completion remains unknown after process loss; it cannot be interpreted
as zero tokens or a successful response.

Input/output, cache read/write and reasoning counts are individually optional.
Anthropic input includes ordinary input plus cache reads and cache creation;
cache breakdowns are subsets. Responses and Chat Completions retain reported
cache/reasoning subsets. Impossible breakdowns are omitted, never added to the
total. A truncated, failed or cancelled request retains usage already reported.
It still cannot authorize an incomplete tool call. Reported zero remains zero;
unreported usage remains absent. Token totals are not dollar-cost estimates.

First output means accepted nonempty text, tool identity/arguments, or useful
content in a completed response. Keepalives, created events, usage-only updates
and private thinking do not start that timer. The timer starts at the request
boundary, after credential acquisition; it includes client/transport overhead,
not just model inference. There is no additional streaming/polling mechanism.

The logical trace is a versioned digest of session and turn identities. Each
active execution segment has a fresh span and links to the previous segment,
whose context and first execution time are saved in canonical turn metadata
before execution starts. These canonical writes use the existing ownership fence;
the independent recording writer never acquires execution authority. A worker
replacement can retain identity without pretending that the stopped process kept
running. Request/tool children share the turn identity. Approvals and questions
carry durable request/resolution timestamps; explicit cancellation ends the wait
without inventing a human answer. Shutdown leaves a parked wait open. Clock skew
produces unknown duration; the raw endpoints remain recorded and an invalid
negative-duration span is not sent to OTLP. Older interactions without timestamps
cannot yield measured historical waits.

Tool executions include internal planning, team/message-board, memory and
scheduling operations. Returned bytes measure the serialized result returned by
the tool, not created workspace files or a fabricated per-tool token cost.
Sandbox provider, enforcement and the tool-policy digest are recorded when
applicable. These facts describe execution; a tool label still cannot weaken
access policy. Internal metadata operations are marked `session-metadata-only`.

New recordings write format v2 and read v1; the indexed SQL schema remains v1
because the payload is versioned JSON. Existing session JSON uses optional new
fields. No historical usage or segment linkage is backfilled. The shared fixture
is `harness/tests/contracts/observability.json`. It is consumable by desktop/service
tests; this qualification exercises Rust, not the Python desktop engine.

There is no built-in Rust title generator or inline helper-model feature to
instrument today. Team agents run their own ordinary sessions; `helper`, `title`
and `probe` are reserved purposes for hosts using the same explicit call-context
seam. Unbound provider connection probes do not belong to a session's totals.
This remains best-effort diagnostics: dropped records, absent usage and orphaned
open facts remain visible as incomplete coverage in the authorized query/rollup
APIs and management UI.

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

## M3 read API and projections

A run is a canonical session/turn submission, including queued work. Run search
reads execution metadata and applies session authorization inside SQL before
pagination. It never searches prompts or recording payloads. Search and result
availability therefore remain meaningful when observability is disabled or down.

Read endpoints (also available under the reference API without `/v1`):

* `GET /v1/observability/runs`: `q`, `agent`, `from_ms`, `to_ms`, `limit`
  (1–100, default 25), opaque `after`. Literal name/session/turn/submission search;
  newest submissions first. Cursor binds query/service/credential context, while
  authorization is always re-evaluated independently.
* `GET /v1/sessions/{sid}/observability/{turn}/timeline`: `after` recording cursor,
  optional `through` high-water cursor, `limit` (1–200, default 100). Immutable,
  metadata-only recorded steps. No caller-controlled deployment or session hash.
* `GET /v1/observability/summary`: the same search filters, at most 25 selected
  runs with one shared 4,096-step budget; selection truncation and missing
  recordings remain explicit. This is not an unbounded deployment-wide total.
* `GET /v1/sessions/{sid}/observability/{turn}/summary`: optional `through` cursor.
  Projection v1 rebuilt from at most 4,096 raw steps, with explicit truncation and
  coverage. No persistent cache or stale authorization decision is involved.

Canonical run access is checked before any recording connection is opened and
again before returning recording data. Independent recording reads run on blocking
threads behind four API-instance permits; bounded statements, rows and response
shape prevent an unbounded scan. Recording outage returns a recording-specific
503, never an execution-store fallback. These endpoints launch no agent work.

Per-request PostgreSQL reads are repeatable within one read-only transaction.
A high-water cursor bounds later pages but does not freeze commits or retention
between HTTP requests. Summary values are diagnostics over available records:
unknown usage, pending/orphaned requests, missing segments, truncation and clock
anomalies remain explicit. A successful scan is not proof of complete billing.
A versioned fresh projection can be rebuilt after late writes/pruning; materialized
rollups can be added later if query measurements justify their invalidation cost.

The full response/counting contract and examples are in the
[query guide](OBSERVABILITY-QUERIES.md). Phase partitions cover observed intervals,
not an inferred complete process history. SQL indexes are added only by explicit
migration; materialized summary caching remains deferred pending query measurements.


## M4 management UI

The separate UI process proxies exact authorized recording read routes; it has no
recording database or exporter credentials. **Observability** starts with a bounded
recent run-page summary. The debounced searchable picker pages through matching
names/IDs, discards stale responses and supports keyboard selection. Selected
runs expose grouped usage, first-output timing, tool duration/result bytes, phase
partitions and a watermarked timeline with step details. Activity/Overview retain
their existing operational charts. Refresh is explicit; no new polling loop.

Missing counters show unknown, known zero remains zero, and scan/group truncation
has a visible partial-summary marker with expandable coverage. Reader errors clear
previous metrics; disabled recording does not disable canonical run search. The
browser retains at most 100 search matches and 1,000 timeline records; summaries
use the API's separate 4,096-step/25-run bounds.

An administrator-only `/v1/observability/health` read exposes serving-API process
recorder/exporter counters in an expandable UI section. These are not aggregated
worker/cluster health or a database-readiness promise. No secrets or endpoint
configuration are returned. UI tokens remain server-memory-only and responses
remain non-cacheable.

[UI operation/qualification](../../harness/service-ui/README.md#observability).
[M4 evidence](evidence/observability-m4-local-20261029.json).

## Export alignment (M5)

The shared [attribute registry](../../harness/telemetry/attributes.json) versions
keys, types, capture classes and legal metric dimensions. Generated Rust constants
are checked in, so consumers need no Python runtime. Selected GenAI attributes
are pinned to 1.41.0; OpenWorker operation names and recording JSON remain stable.
The OTLP cache-creation key changes from the old nonstandard `cache_write` spelling
to `gen_ai.usage.cache_creation.input_tokens`. Local `cache_write` stays unchanged.

Exports derive generation/event/tool/agent hints from the operation even with
content capture off. Only completed provider requests own usage. Hash-based
conversation/session correlation and linked turn segments survive worker changes;
private names/text do not become necessary to obtain useful vendor traces. Metric
dimensions remain fixed enums, never model/tool names or identity hashes.

Authentication is explicit, via secret environment references; arbitrary ambient
OTEL headers/resources/proxies are ignored. Standard OTLP acknowledgements are
strictly validated, including partial rejection. Langfuse v4's nonstandard queue
receipt uses an explicit traces-only `acknowledgement: "langfuse_v4"` mode. Neither
path copies receiver error text or replays failed exports/tool work. A receipt is
not a durable vendor-read guarantee: local qualification also fetches observations
and reconciles normalized cache/reasoning buckets, known zero and unknown usage.

See [configuration and limits](../../harness/telemetry/README.md) and the
[pinned collector/Langfuse fixture](../../deploy/reference/EXPORT-QUALIFICATION.md).
Recording remains the independent diagnostic source for management queries;
external export does not add a mandatory database/vendor or change authorization.
