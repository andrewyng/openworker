# Observability implementation plan

29 October 2026. [Specification](OBSERVABILITY.md).
[Progress ledger](OBSERVABILITY-PROGRESS.md).

## M1 — recording/storage foundation

1. Add a versioned, metadata-only recording type and an adapter-neutral store API.
   Validate bounds and IDs, immutable/idempotent writes, cursor-based bounded reads
   and bounded retention deletion. No transcript/content columns.
2. Implement SQLite and PostgreSQL stores with identical contract tests. PostgreSQL
   uses its own configuration, connection, namespace/schema and TLS validation.
3. Add a dedicated bounded writer, counters and bounded stop. Start with one insert
   transaction per record. Test saturation and failures without blocking producers.
4. Bridge current instrumentation into recording independently of the OTLP sink;
   preserve exporter privacy settings and current runtime defaults. Add optional
   service configuration, explicit migration, writer health and graceful shutdown.
5. Qualify locally: roundtrip/reopen, duplicate/conflict, malformed records,
   retention isolation, metadata privacy, SQLite/PostgreSQL parity, separate-target
   routing, and recording outage while execution storage remains usable. Record
   exact limits: segment coverage only, no complete reviewer/summary totals yet.

M1 exit: committed existing instrumentation survives restart in either adapter;
service can choose same-target or separate-target connections; no schema/query or
database failure triggers a tool retry. No management UI is connected yet.

## M2 — complete runtime accounting

Implemented and locally qualified. [Evidence](evidence/observability-m2-local-20261029.json).

1. Stable session/turn/request/step identities and durable timing fields for start,
   first output and completion. Resume/worker change uses links/segments of one turn.
2. One model-call recording path for main, reviewer, compaction and helpers. Cache
   read/write and reasoning remain optional provider-reported subsets.
3. Durable approval/question wait intervals, sandbox facts and tool output sizes.
   Cover internal planning/team/memory tools consistently.
4. Shared deterministic metadata fixtures consumable by desktop/service tests. Reconcile
   request usage once, including retries; test parallel duration accounting and
   unknown outcomes. No claims of completeness from missing historical records.

M2 exit: provider request tests reconcile working/reviewer/summary usage, including
failed streams, retries, refresh and cancellation. Native Linux SQLite restart
and PostgreSQL worker replacement preserve turn identity and measured waits.
Existing permission, uncertain-outcome, privacy/export and storage-isolation
regressions pass. The metadata contract does not create unimplemented helper/title
features, infer unavailable counters or promise lossless billing.

## M3 — authorized queries and rollups

Implemented and locally qualified. [Query guide](OBSERVABILITY-QUERIES.md).
[Evidence](evidence/observability-m3-local-20261029.json).

1. Read-only API checks execution-store principal/namespace/session access first.
2. Search recent runs with bounded cursor pages; filter by agent, time, name/ID.
3. Run timelines and grouped duration/call/usage queries. Version/rebuild rollups;
   distinguish elapsed, accumulated and exclusive durations. Account for coverage.
4. Prove unauthorized reads cannot reach another session/deployment, even when
   recording storage is separate or lagging. Observe query costs before tuning.
5. Expose recording settings and independently mounted database credentials in
   deployment packaging; qualify installer handoff before enabling it by default.

## M4 — management UI

Implemented and locally/visually qualified. [Evidence](evidence/observability-m4-local-20261029.json).
[UI guide](../../harness/service-ui/README.md#observability).

Use the approved mock's grouped summaries, model/tool tables and turn timeline.
Add the searchable run picker and progressive detail. Replace sample data only
when each metric's query and completeness contract are implemented. Keep current
operational charts and display recorder/exporter health without noisy banners.

## M5 — export alignment and qualification

Align typed records with the shared attribute registry and pinned OTel conventions.
Export usage on model spans and preserve context across worker changes; verify
metadata-only/default-off content against a local collector and Langfuse-compatible
fixture. Test outages, shutdown, pruning, queue pressure and local throughput.
Only add SQL micro-batching if measurements justify it. Fresh AWS installation of
the final release candidate remains a separate bounded cloud qualification.
