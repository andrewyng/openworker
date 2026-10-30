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

### Remaining scope at M1 completion

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

## M2 — started, 29 October 2026

User authorized complete model-call accounting/turn continuity, local tests and
push after verification. Baseline `213d8ea7`; clean worktree. No new cloud use or
paid model calls. M1's separate storage connection, bounded writer, privacy and
best-effort queue guarantees remain in force.

Implementation order:

1. Extend typed instrumentation/recording with logical turn correlation, request
   purpose, optional cache/reasoning usage and explicit segment links. Retain old
   records and legacy usage fields; unknown values remain absent.
2. Move model accounting to the shared request boundary. Main, reviewer and
   compaction use it; actual HTTP attempts (including auth refresh) are distinct.
   Recognize first usable output for tool-only responses, and preserve reported
   usage even on incomplete/error responses. Do not count usage update frames twice.
3. Persist the previous execution segment in turn metadata. Resumes use a stable
   logical-turn trace with distinct segment identities and links, not a fabricated
   continuously running span. Record durable interaction request/resolution times
   and completed wait intervals, including after restart. Clock anomalies stay
   unknown. No changes to approvals, grants or uncertain-tool replay rules.
4. Instrument currently bypassed internal tools; record returned result byte counts
   and sandbox/enforcement facts. Add deterministic fixtures for request purposes,
   partial usage, failures/retries, private-content exclusion and pause/restart.
5. Qualify native local tests plus actual Linux runtime/PostgreSQL recovery where
   required. There is no built-in inline helper-model entry point in the current
   Rust engine; team workers execute their own sessions through the same model
   path. Do not manufacture an unimplemented helper or title feature for a metric.

M2 is not lossless billing: provider usage may be unavailable and M1 queues can
lose observations. Open request facts/coverage markers must expose missing ends.
Search, rollups and management UI wiring remain M3/M4.

### M2 implementation/checkpoint

Shared request accounting is implemented for the working model, auto-approver and
compaction. Provider decoders report optional cumulative usage and meaningful first
output without authorizing partial completions. Physical authentication refresh
requests are distinct spans. Runtime retry coverage checks that usage is counted
once per attempt, including the failed request. Cancellation retains known usage.

Canonical turn metadata now carries the last segment context and first execution
time. Interaction request/resolution timestamps support measured waits after
restart; explicit cancellation closes a wait without supplying a human answer.
Internal planning/team/memory/scheduling tools now emit execution observations.
Recording v2 adds turn hashes, links and tool facts while reading v1 payloads.

Host checks passed for all three provider APIs, main/reviewer/summary requests,
token refresh, partial/truncated streams, cancellation, runtime retries, recording
validation/reopen and OTLP usage/link export. Native Linux runtime and PostgreSQL
worker-replacement qualification is in progress; no cloud resources or paid model
calls. Detailed completion evidence will follow after those checks pass.

## M2 — implemented and locally qualified, 29 October 2026

Completed the shared request boundary, provider-reported optional usage, immutable
open-request facts, logical-turn correlation, linked execution segments, durable
human waits, internal tool observations and returned-result sizes. New recordings
write v2 and still read v1; SQL layout remains v1. Recording/export privacy,
separate database connections and best-effort queue guarantees remain unchanged.

The Linux approval/restart test caught a real checkpoint bug: `metadata()` omits
canonical history and cannot be passed to `save()`. Enabled segment checkpoints
now load the complete session before saving under the existing ownership fence.
A dedicated regression proves that a failed event transaction preserves both the
history and the prior observation metadata. Disabled instrumentation uses only
the lightweight metadata read and creates no new checkpoint write.

Workspace-wide compilation also found a provider-probe example missing the new
measurement variants and a dormant recovery-fixture initializer missing its
existing optional auth-expiry field. Both fixtures are aligned; no production
authorization behavior changed. The test-image packaging now selects the exact
Cargo compiler-artifact executable, avoiding an ambiguous cache glob.

### Final verification

* **204 native Linux runtime/recording/telemetry tests pass**, including the real
  SQLite approval/restart flow, stable turn IDs and segment links, question
  answer/cancellation, internal plan/memory/scheduling observations, metadata
  privacy, all three SSE APIs and provider failure/retry regressions. Two entries
  are intentionally ignored in that invocation: the PostgreSQL storage test runs
  in the separate real-database fixture; the preview child entry point is invoked
  by its parent integration test.
* **14 real PostgreSQL worker tests pass** on the exact final image, covering
  parked approval handoff, durable wait timestamps, segment continuity, FIFO,
  cancellation, process death, database loss, renewal, drain and no side-effect
  replay. Two opt-in cases are explicitly filtered: the retained-baseline
  mixed-version test and load matrix. No new load-sizing or downgrade claim.
* Two real PostgreSQL storage/service suites pass with independent execution and
  recording databases: v1/v2 parity, immutable duplicate/conflict handling,
  retention/reopen, actual migration and recording contention/outage isolation.
* Host checks pass: 33 recording/server/OTLP tests plus 59 runtime unit tests and
  five real-loopback model-accounting tests. Platform-overlapping tests are not
  counted as additional unique coverage. Cancellation preserves known usage;
  unsupported counters stay absent; first output excludes keepalives/thinking;
  token refresh creates two request spans without doubling a usage snapshot.
* All workspace packages, features and targets compile offline/locked. Rust
  formatting, JavaScript syntax and diff checks pass. The pinned Linux fixture
  uses release optimization with LTO disabled to shorten test linking.

Both successful PostgreSQL fixture runs removed their exact labeled containers
and generated password/environment files. No unrelated containers, Kubernetes
resources, AWS resources, provider credentials or paid models were used/modified.
Build caches and the qualification image remain available for reruns. Roughly
194 GiB remained free during qualification; no additional disk request needed.

[Sanitized M2 evidence and source hashes](evidence/observability-m2-local-20261029.json).

### Remaining scope after M2

Best-effort recording is still not lossless billing or audit. Historical records
are not backfilled. Built-in inline helpers/title generation do not exist in this
Rust engine; reserved purpose labels are an explicit host seam, not new features.
Separate recording TLS was not newly qualified against a real TLS database, and
no Windows/Python-desktop qualification is claimed here.

Next is M3: execution-authorized run search, bounded timeline queries and grouped
rollups, preserving unknown/incomplete coverage and distinguishing elapsed from
accumulated/exclusive duration. Live management UI wiring remains M4. SQL batching
remains deferred until measurements justify it.

## M3 — started, 29 October 2026

User authorized execution-authorized run search, timelines and grouped summaries,
local qualification and push after verification. Baseline `3e61bd4d`; clean tree.
No AWS provisioning, paid model calls, Python changes or live UI wiring.

Implementation order and query contract:

1. Execution-store run directory: a run is one canonical session/turn submission.
   Search names/IDs literally, filter agent/time, recent-first keyset pages with
   bounded limits. Apply current role/owned/explicit-session scopes in SQL before
   LIMIT; return metadata only, never prompts or transcript snippets.
2. Adapter-neutral bounded recording pages, session/turn filters and a high-water
   cursor. Read queries use independent read-only PostgreSQL transactions. HTTP
   authorizes the canonical run before opening recording storage and rechecks
   current authorization before returning data. No cross-database authorization
   trust, cached grants or user-selectable deployment/schema.
3. Versioned rebuildable projection over at most 4,096 recorded steps. Timeline
   pages default to 100 (max 200). Summary is a fresh bounded projection of raw
   immutable records, not an unbounded materialized cache. Explicitly report scan
   truncation, absent usage, unmatched open requests and missing segment coverage.
   Retention/late commits mean a high-water cursor is not a repeatable snapshot
   across HTTP requests; never advertise lossless completeness.
4. Group model usage by model/purpose and tools by name; retain known/unknown
   sample counts. Accumulated duration and interval union differ. Partition the
   observed window into exclusive model/tool/policy/wait/other/parallel time;
   policy and active-segment spans are containers, so nested reviewer time is
   not added again. Unobserved gaps are not model or tool work. Wall elapsed and
   observed partitions remain separate; missing evidence stays unknown.
5. Add read-only REST endpoints and bounded reader concurrency, then opt-in Helm
   recording configuration plus independently mounted database credentials/CA.
   Test storage parity, overlapping durations, retries, incomplete coverage,
   authorization-before-recording, scopes/revocation, pagination, separate DB
   outage, packaging defaults and installation handoff locally. Keep caches and
   SQL batching deferred until measured need; management UI remains M4.

### M3 implementation/checkpoint

Added the execution-owned run directory with literal metadata search, scoped SQL
pagination, agent/time filters and query-bound cursors. Recording adapters now
support bounded, validated session/turn reads, a high-water cursor and optional
query indexes. A fresh projection v1 reconciles reported usage, model/tool groups,
interval unions and exclusive phase partitions; it exposes unknowns, pending/orphan
requests and truncated coverage instead of inventing totals.

HTTP reads authorize before opening recording storage and recheck after it.
Responses are non-cacheable. Four independent read-only jobs per API preserve
permits through client cancellation. Grouped reads cover an explicitly bounded
page of at most 25 authorized runs with one shared 4,096-step budget, not an
unbounded deployment-wide claim. Helm opts into recording and can mount a separate
credential/CA without exposing either to the UI or proxy.

Host qualification passed SQLite/PostgreSQL query parity and the two-node HTTP
matrix: owned/explicit scopes, literal search, equal-time pagination, cursor
binding, absent/disabled/blocked recording, bounded reader concurrency and
revocation during a blocked read. Fixture setup was corrected to seed sessions
before issuing session-scoped credentials, reload revisions after checkpoints,
and keep synchronous PostgreSQL fixture connections outside Tokio runtime
threads. Production read connections already use blocking jobs.

Native Linux, packaged entrypoint migration and final regression/evidence checks
completed successfully; see the qualification results below.


### M3 completed — local qualification, 29 October 2026

* Added four read endpoints: authorized run search, per-run timeline, per-run
  summary and grouped summaries of a selected run page. Authorization and run
  existence come from execution storage before recording access, and permissions
  are rechecked after the recording read. No recording credential grants session
  access. Responses are non-cacheable, including error responses.
* Projection v1 distinguishes accumulated work, interval unions, exclusive phases
  and canonical wall elapsed. Proven descendant reviewer intervals are removed
  from their policy container; unrelated overlapping policy work remains parallel.
  Missing/cyclic parent relationships are flagged. Known zero, unknown counters,
  pending human waits, overflow, truncation and missing records remain distinct.
* All seven qualification suites passed against two independent local PostgreSQL
  databases: storage contracts, recording outage isolation, PostgreSQL query parity,
  two-node authorized HTTP reads, Linux worker recovery, Linux authorized HTTP
  reads and the actual packaged entrypoint's two-database migration. The worker
  suite passed 14 tests; its opt-in load matrix and retained-baseline mixed-version
  test were excluded explicitly. Fault-injection diagnostics are expected.
* The Linux release fixture passed 225 tests (13 environment-gated tests ignored
  there; applicable database cases ran separately). Host recording/server/OTLP
  regressions passed 43 tests (10 environment-gated cases ignored there). Eight
  deterministic query/projection tests passed, covering golden overlap arithmetic,
  bounds, group limits, corrupt-row isolation, unknowns and parent hierarchy.
  Seven Helm rendering tests passed. All workspace features/targets compile
  offline/locked; Rust formatting, JavaScript/shell syntax and diff checks pass.
* The small Linux serial HTTP probe used 20 requests scanning four records each:
  p50 7.208 ms / p95 8.401 ms, including a fresh recording connection per request.
  This checks the read path, not production capacity or sizing. Keep batching and
  materialized caches deferred until representative measurements justify them.
* Helm recording remains opt-in. The packaged installer routed execution and
  recording schemas to their independently mounted database URLs. Separate TLS
  credential/CA wiring was rendered, but a live recording TLS server or Kubernetes
  rollout was not newly qualified in this milestone.

All owned qualification containers and generated password/connection/config files
were removed; unrelated containers and Kubernetes resources were untouched. No AWS
resources, provider keys or paid model calls were used. Build caches and the pinned
qualification image remain for reproducible reruns.

[Sanitized M3 evidence and source hashes](evidence/observability-m3-local-20261029.json).
[Read API guide](OBSERVABILITY-QUERIES.md).

Next is M4: wire the searchable run picker, grouped summaries and timelines into
the management UI using these APIs. Recording remains best effort, not lossless
billing/audit. Historical records are not backfilled; high-water cursors do not
freeze late commits or retention across HTTP requests. Windows, Python desktop,
cloud sizing and live recording TLS remain outside this qualification.
