# Observability progress ledger

## 29 October 2026 — authorization and baseline

User agreed to the grouped management UI direction and same-database storage,
with independently configurable execution and observability PostgreSQL connections.
Authorized specification, plan, ledger, M1 implementation and local tests. No AWS
provisioning, paid model calls, Python changes or live UI integration in this work.

Baseline branch `feat/openworker-service-m1`, commit `fc331130`. Worktree clean.
Approximately 202 GiB free. Existing instrumentation is opt-in and best effort:
main model and most tool durations, main usage and lifecycle facts exist; full
reviewer/summary usage, timestamped portable events, cross-resume trace continuity
and grouped management queries do not. The SQLite/PostgreSQL execution adapters
remain independent of the proposed recording adapter. Docker needs the normal
local sandbox escalation; no unrelated containers will be changed.

Written before implementation: [specification](OBSERVABILITY.md) and
[ordered plan](OBSERVABILITY-PLAN.md). M1 starts with metadata-only immutable records,
two adapter implementations and a separate bounded writer. Per-record transactions
first; SQL batching is a measured follow-up, not an initial prerequisite.

## M1 — implemented and locally qualified

Added `openworker-observability`: versioned metadata-only steps, stable immutable
insert identity, bounded cursor reads, SQLite and optional PostgreSQL adapters,
scoped retention and an independent bounded writer. PostgreSQL defaults to verified
TLS and supports explicit loopback-only plaintext for tests. SQLite creates private
files and rejects symlink database files. Configuration never borrows an execution
transaction or falls back after a recording target fails.

The reference server accepts optional `observability`, with separate schema,
connection settings, credentials, roots, timeout, queue capacity and retention.
Without a target override it copies execution configuration into a new connection.
Explicit `migrate` prepares both schemas; normal startup checks recording schema.
Authenticated API readiness reports local recording health without making later
recording failures an execution-readiness dependency. Shutdown drains upstream
instrumentation before the independent writer, each with its own five-second bound.
Recovery inspection does not start the recording writer.

Current instrumentation is bridged into recording. Allowlisted identifying
metadata is stored locally; exporter capture policy is independently filtered.
Prompt/result/reviewer content cannot enter the stored type. No public query API
or SDK convenience flag is advertised; trusted low-level hosts can inject it.

Qualification found and corrected two issues before completion:

* The actual server migration command initially opened the new synchronous
  PostgreSQL connection on the Tokio runtime thread. It now uses `spawn_blocking`;
  the real command is exercised by the integration test.
* SQLite's NOFOLLOW option initially rejected macOS's normal `/var` parent symlink.
  Canonicalize only the parent and continue rejecting a symlink database leaf.
  Local reopen, file-mode and unmodified-target checks pass.

All attempted disposable PostgreSQL fixtures were removed with exact label/ID
checks, including unsuccessful test attempts; their generated password files were
removed. No unrelated containers, local Kubernetes resources, cloud resources,
provider credentials or paid model calls were touched. Approximately 199 GiB free.

### Verified

* Nine ordinary recording tests: SQLite reopen/retention isolation, immutable
  duplicate/conflict semantics, bounds/privacy, unsupported schema, symlink/mode,
  PostgreSQL plaintext/schema guards, blocked writer/queue/stop, local persistence
  while vendor export is stalled, and recording/export privacy separation.
* Eight reference-server/configuration checks, plus eleven existing telemetry
  boundary/wire regressions. The loopback wire fixtures initially required sandbox
  escalation to bind local sockets; all pass with that local-test access.
* Two real Docker PostgreSQL suites: adapter parity/reopen/retention and actual
  service migration plus same-target and separate-target connection contention.
  Locking recording tables times out recording writes while the real execution
  adapter continues SQL successfully. An explicitly unavailable target fails
  startup without silently recording into execution storage.
* A 100-record single-connection probe, one transaction per record, measured about
  759 records/second with p95 write latency about 1.51 ms on this local Docker
  fixture. This is not production capacity guidance. Defer batching; retain bounded
  queues, failure counters and per-record transaction simplicity until load data
  justifies a change.
* Offline locked builds, default-feature recording build, Rust formatting,
  JavaScript syntax and diff checks pass.

[Sanitized local evidence](evidence/observability-m1-local-20261029.json).

### Remaining scope

The queue is not a durable outbox; crashes, saturation or outages can lose
observations. A committed step is durable, but this is not lossless billing or
audit. Counters are process-local. Current hashes and active-segment spans do not
yet form a complete logical-turn recording across resumes. Cache/reasoning,
reviewer/compaction/helper accounting, authorized run search/rollups and live UI
integration are M2–M4. Deployment packaging for separate recording credentials is
not exposed yet. The new PostgreSQL connector's verified-TLS path is implemented
but was not qualified against a real TLS database in this local plaintext fixture.
No AWS trial or new public API is claimed by M1. The management mock and Python
desktop implementation remain unchanged.
