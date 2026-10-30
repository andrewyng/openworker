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

## M4 — started, 29 October 2026

User authorized management UI wiring, local and visual qualification, ledger updates
and push after verification. Baseline `76fdd56a`; clean tree. No cloud provisioning
or paid models. Preserve Activity/Overview operational charts and the browser's
in-memory credential, fixed-upstream and strict route boundaries.

Implementation order:
1. Add exact read-only proxy routes/validated query encoding for M3 search,
   summaries and timelines. Keep arbitrary scope/database selection forbidden.
2. Add Observability with a debounced, cancellable, paginated run combobox,
   selected-page summaries, model/tool groups, and per-turn timeline/drill-down.
   Never substitute sample values, zero for missing usage or unbounded totals.
3. Keep coverage, exporter/recorder health and counting explanations in progressive
   detail. Health is API-instance-only and restricted to administrators because
   process counters are not filtered by session ownership.
4. Test proxy bounds and rendering math; drive the UI in a real browser against
   separate PostgreSQL execution/recording databases. Include search races,
   keyboard/pagination, unknown/empty/partial, outages, revocation and navigation.
   Inspect desktop and narrow layouts visually, then record evidence and push.


### M4 completed — local and visual qualification, 29 October 2026

Added **Observability** beside the existing Activity page, preserving operational
charts. The fixed-upstream proxy now allowlists exact recording reads, including
bounded percent/Unicode search, query-bound run cursors and numeric timeline
bounds. Unknown/duplicate query fields, alternate scopes/databases and encoded
path separators stay rejected. No browser database or exporter credentials.

The run picker searches after 250 ms, cancels old requests and discards stale
responses. It pages 10 results at a time, retaining at most 100. Recent summaries
cover one authorized page of at most 25 runs. Selecting a run aligns its initial
100-row timeline and summary with one high-water cursor; explicit pagination
retains at most 1,000 browser rows. Selecting a tool filters loaded calls; selecting
a timeline bar exposes timing, usage, result size, outcome and record metadata.
There is no new background polling or request fan-out per model/tool chart.

The UI keeps measured zero, absent usage/duration and partial reported totals
separate. It shows accumulated versus exclusive time, concurrent phases, model
first-output averages from known samples, and tool result bytes. Scan/group caps
show a small Partial summary marker and expandable coverage. Read errors remove
old metrics. Administrator-only process health is expandable and explicitly scoped
to the serving API, not a worker/cluster aggregate. Service revocation signs out
and clears rendered metrics; no browser credential storage was added.

Verification:
* Four local PostgreSQL qualification suites passed: storage contracts, separate
  recording-database outage isolation, query parity and the actual two-node Rust
  HTTP authorization matrix. Optional browser QA runs within that HTTP fixture.
* All 14 browser check groups passed, both in a host UI run and the final Linux
  UI container against the real host Rust API and separate PostgreSQL databases.
  Real checks include owned scopes, page/keyboard navigation, timeline pagination,
  tool drill-down, missing/empty recordings, disabled recording, administrator
  health and actual service-token revocation. Browser-only delayed searches,
  busy/transport errors and partial-cap flags are explicitly injected over the
  real read path; these are not claimed as new worker/cloud failures.
* Six Node proxy/security/presentation tests and seven Helm rendering tests pass.
  Host recording/server/OTLP regressions pass 43 tests (10 environment-gated tests
  ignored there; applicable PostgreSQL cases run separately). Workspace packages,
  targets and features compile offline/locked. Formatting, syntax and diff checks
  pass. The read-only, unprivileged Linux UI image builds using its cached pinned
  Node base without network access; packaged asset hashes match source.
* Captured ten deterministic fixture screenshots. Reviewed desktop (1440 px) and
  narrow (390 px) layouts, search/dropdown, timeline, unknown/partial/error states
  and instance health. No page overflow, JavaScript exception or CSP error. Selected
  screenshots are saved with the sanitized evidence, clearly labeled fixture data.

Final UI image: `sha256:1ae1e267507b0b50126949730cc3ea239341633bbec808adb14a42eec037ce12`.
All exact labeled fixture containers, generated PostgreSQL passwords and temporary
UI connection configurations were removed. No unrelated containers, Kubernetes
resources, AWS resources, real model keys or paid models were used/modified. Build
caches/images remain for reruns; about 188 GiB free, no extra space request needed.

[Sanitized M4 evidence](evidence/observability-m4-local-20261029.json).
[UI screenshots](evidence/observability-m4/README.md).

Next is M5: export alignment and collector/Langfuse-compatible qualification.
Recording remains best effort. No live Kubernetes rollout, cloud qualification,
production load/sizing, new live recording TLS server, SSO, Windows/Python desktop
or worker-global health aggregation is claimed by M4.

## M5 — started, 29 October 2026

User authorized export alignment, local collector/Langfuse qualification, ledger
updates and push after verification. Baseline `151e6711`; clean tree. No AWS,
paid models, new Python/desktop tracing API, or hosted vendor credentials.

Order:
1. Pin a supported OTel GenAI attribute revision and add one shared machine-readable
   registry with Rust constants and privacy/type classifications. Keep existing
   OpenWorker operation names, unknown usage and metric cardinality contracts.
   Align cache creation, typed operation/vendor observation hints and pseudonymous
   session/turn correlation without enabling name/content capture by default.
2. Reconcile the same deterministic turn records through local recording and OTLP:
   main/reviewer/compaction attempts, retries, known zero/unknown, cross-worker
   segments/links, tool bytes and human waits. Never roll model usage onto parents.
3. Qualify real local OTLP protobuf ingestion with a pinned collector and an
   authenticated Langfuse-compatible traces-only receiver. Check attributes and
   mapped generation usage, not just HTTP 200. Keep full Langfuse deployment/cloud
   acceptance distinct from protocol/mapping compatibility.
4. Recheck privacy, ambient-header/proxy isolation, redirects/auth/partial failures,
   queue pressure, bounded shutdown, recording/exporter independence and retention.
   Measure a small local throughput fixture before deciding about batching.
5. Update export/setup documentation and sanitized reproducible evidence; push the
   existing feature branch after verification. No automatic cloud rollout.

### M5 checkpoint — wire/collector alignment

Added `harness/telemetry/attributes.json` and checked-in generated Rust constants
(pinned OTel GenAI attribute subset 1.41.0). Derived operation/Langfuse type and
pseudonymous conversation/session correlation remain available with capture off;
model/tool names and payload keys stay separately gated. Cache creation now uses
the pinned standard name. Model usage is emitted only on request-completion spans,
not parents/start markers. Fixed metric dimensions are unchanged.

The OTLP client validates protobuf acknowledgements, counts partial rejection,
malformed/oversized acknowledgement and authentication/redirect failures without
replaying batches, and suppresses receiver-supplied error bodies/text. Registry
bounds disallow arbitrary metadata/content keys and duplicate-attribute growth.
Default-off/private-key laziness, ambient proxy/header/resource isolation, bounded
shutdown and queue-pressure tests pass. Typed unknown timing remains visible in
logs even when an invalid-clock span is withheld.

The pinned official collector has ingested all three signals and reconciled 13
records, four model requests, 110 input/22 output tokens, known zero, unknown usage,
wait timing and resume links with local SQLite recordings. Cumulative metric
snapshots are reconciled per process/dimension rather than added repeatedly. The
fixture now handles empty OTLP log bodies correctly. Docker Desktop requires a
loopback ingress bridge in addition to its internal service network; only the
collector/web ingress joins it. Dependencies stay internal, vendor telemetry/AI
features are disabled and no real credentials are provided.

Actual pinned Langfuse v4 self-host qualification is in progress. Its first web
readiness run exposed a container-interface binding issue; the fixture now sets
`HOSTNAME=0.0.0.0` explicitly. Failed/interrupted fixture resources were removed by
exact ownership labels (including a manually recovered collector from a Node
lifetime/abort-timer exit). Later runs journal ownership and keep deadline timers
alive. No harness execution or tool behavior was changed by fixture corrections.

### M5 checkpoint — actual Langfuse reconciliation

The real pinned Langfuse v4 receiver returns `{}` for an empty request and a
nonstandard ingestion-job JSON receipt for populated batches. This was initially
reported as failed export despite successful ingestion. Added explicit
`telemetry.acknowledgement: "langfuse_v4"` (traces only), with narrow receipt-marker
validation. Standard OTLP remains the default and rejects that envelope. Both
modes reject partial/malformed acknowledgements; receiver job/error metadata is
never logged or retained by the exporter. Tests cover JSON and binary responses,
wrong-signal counts, authorization failure, redirects and no automatic replay.

Full local collector/Langfuse fixture `exports-l` passed: 13 observations, four
generations, correct parent/session identity, no name/content export, known-zero
reviewer and absent compaction usage. Actual Langfuse normalization yielded 50
uncached + 40 cache-read + 10 cache-created input and 15 ordinary + 5 reasoning
output on the successful working request; its total stays 120. Including the
failed 12-token request gives 132, matching source 110 input/22 output. Vendor
cache-creation bucket is `input_cache_creation`; the qualification comparator now
uses the real API field. Source OTLP counters remain inclusive and unchanged.

All fixture resources were cleaned up by exact ownership. A small synthetic
SQLite probe persisted 1,000 records without loss (~6,635 records/second including
drain on this Mac); this is not an agent throughput or overhead claim. No new SQL
batching was justified or added. Native Linux/PostgreSQL final regressions are
running before M5 is marked complete/pushed.

## M5 — complete and locally qualified

Final collector/Langfuse run `exports-m` passes using the final probe binary and
scripts, including explicit v4 receipt handling, error-status mapping, parent
identity, request-only usage and empty unknown-usage preservation. All 13 source
records reconcile; four generations total 110 input/22 output without counting
cache/reasoning twice. Zero exporter losses. Owned containers/anonymous volumes,
network and private generated configuration were removed; captured container logs
are scrubbed of known generated fixture secrets. The ownership journal also
records intended names before creation for interrupted-run recovery.

Final validation:
- **49 host tests pass**, 10 environment-dependent tests intentionally ignored.
- **231 native Linux tests pass**, 13 fixture/environment-dependent tests ignored
  in that phase. Separate native PostgreSQL qualification then runs **14 worker
  recovery cases**, authorized recording HTTP and packaged two-database migrations.
- **Seven real PostgreSQL suites pass**: storage/retention contract, recording outage
  independent of execution, recording queries, authorized HTTP, Linux replacement
  recovery, Linux authorized HTTP and packaged separate-target installation.
- Default/no-exporter feature check, registry-generation check, Rust formatting,
  Node syntax and whitespace checks pass. No new management UI code in M5; M4
  browser/visual evidence remains separate.

The native fixture image was resolved with `docker image inspect` before the final
recovery run. An earlier invocation supplied the OCI configuration digest instead
of Docker's image ID; it stopped before creating a Linux worker, cleaned up the
owned PostgreSQL fixture, and was rerun with the inspected immutable image ID.
Final image `sha256:bf96ad1e3ccf021846d9cb6700bb3731c28cb06c7e8b9aaf437f08a8fa74d3d9`.

[Sanitized evidence](evidence/observability-m5-local-20261029.json) includes exact
image pins, source/probe/log hashes, request/usage reconciliation, database tests,
cleanup and measurement limits. [Export setup/reproduction](../../deploy/reference/EXPORT-QUALIFICATION.md)
and [telemetry configuration](../../harness/telemetry/README.md) document the new
explicit Langfuse dialect and cache-creation wire-key compatibility change.

No cloud resources, paid model calls, Python harness changes or SQL batching.
Recording/export remain best effort, independent of agent authorization/recovery.
Fresh AWS installation of the final release candidate remains a separate gate.

## Persistent local dual-UI qualification — started

User authorized a persistent local Langfuse installation, latest-harness
end-to-end run and visual verification of the same run in management/Langfuse UIs.
Baseline `b3ae6a3f`. Existing local kind cluster and OpenShell are healthy; the
older `ow-helm-eval` service remains untouched. No cloud provisioning.

Plan:
1. Create dedicated namespaces/private state with pinned Langfuse images and PVCs;
   preserve data across pod restarts. Keep UI/API ingress on host loopback.
2. Build/import the current harness and matching ToolRunner; deploy a fresh
   service with local recording plus a fixed local relay forwarding OTLP traces
   to Langfuse. Keep exporter/provider credentials outside public configuration.
3. Publish a small agent, execute real OpenShell file/shell work with a live model,
   and reconcile session/turn IDs, tool effects and provider usage in both stores.
4. Verify management UI and Langfuse UI visually, test persistence, leave both
   available, and save screenshots, access/run instructions and evidence.

The ClickHouse adapter remains a separate design decision. Langfuse's own
ClickHouse dependency does not change the harness recording backend (PostgreSQL).

### Persistent setup checkpoint — real execution exposed a worker wiring gap

Pinned Langfuse v4.72.0 is running in `ow-langfuse-observe20261030` with four
bound PVCs (PostgreSQL, ClickHouse, Redis and MinIO). Current harness/runner run in
`ow-service-m4-observe20261030`; the management UI is a local Node process using
verified TLS to its API pod. Both frontends use loopback-only access.

The real Claude Sonnet 5 task created/read `hello-observability.md` and verified
SHA-256 `2631317abf7b76b91e3dbd34e0b6cd47b3bcdeb370de261c24416530253e4d11`.
Four tool approvals were submitted through the management UI; an independent
read/checksum inside the owned OpenShell sandbox confirmed the effect.

Two concrete issues surfaced:
- MinIO's one-second default readiness check plus a tight CPU/memory setting caused
  its Service to lose endpoints. Explicit GOMAXPROCS=2, 512 MiB/2 CPU limits and
  five-second readiness restored ingestion. The source recorder stayed usable;
  failed telemetry batches are not replayed or hidden.
- Catalog `management::worker_with_options` rebuilt slot database configuration
  without copying `store.telemetry()`. That disabled runtime/model/tool records
  even though the worker monitor recorded storage activity. Carry the existing
  instance-owned handle into the worker's slot connections, matching the legacy
  worker path. The final qualification must assert actual model/tool/turn records
  from a catalog agent, not just synthetic records or direct-worker fixtures.

A separate seven-day all-scope credential request was rejected by automatic
approval review as unnecessary; it was not created. The demo uses the existing
bootstrap credential and its original expiry. No workaround or broader grant.

### Persistent dual-UI qualification — verified

The catalog-worker fix passed **13 server-reference tests** (eight environmental
cases intentionally ignored there), plus the strengthened real PostgreSQL /
OpenShell `service_v2` suite: two API nodes/controllers, bounded catalog worker,
shared coder/verifier and Rust/TypeScript SDKs. That suite now asserts actual
model/tool/execution recording. **Eight Helm tests** pass, including optional
read-only exporter-secret mounting into harness roles/bootstrap only. Rust
formatting, Node syntax, shell syntax and whitespace checks pass.

The first post-fix turn completed but had incomplete vendor export during the
Langfuse outage. ClickHouse's 128-thread global pool starved HTTP/native queries
and async inserts; the real UI also exceeded the synthetic fixture's memory cap.
The retained local setup uses a 1024-thread global ceiling, independently bounded
two-thread queries/300 MB per-query memory, 1.8 GB server memory and a 2560 MiB pod
limit. Readiness uses HTTP `/ping`. These are local demo settings, not production
sizing guarantees. No source recording was rewritten to conceal vendor gaps.

A fresh UI-submitted **read-only** turn completed after recovery:

| Identity / measurement | Verified value |
|---|---|
| Session | `s_81467909b63247ac992f8c5d0abd6a8e` |
| Run | `t_d63713b94609450b9222d7f488f3ce86` |
| Trace | `86ba88d0f039172690283117373d726b` |
| Model | Claude Sonnet 5, three requests |
| Tools | `read_file`, `run_shell`, one call each |
| Usage | 10,560 input + 222 output = **10,782 tokens** |
| Recording/export | All **22 source span IDs** matched in **148** Langfuse observations |

Both tool approvals used **Allow once** through the management UI. The final file
checksum matches the independently inspected OpenShell file. Reconciliation reads
the authorized source API and authenticated vendor API, checking parents, trace,
hashed session, request-only generation accounting and absence of prompt/tool
payloads. Additional vendor storage/lifecycle spans are not counted as model calls.
No lost-outcome tool retry, completed-turn replay or manual trace backfill occurred.

The same final records and usage survived replacement of **all four data-service
pods** (ClickHouse, PostgreSQL, Redis, MinIO), retaining their original PVCs. The
management usage/timeline and Langfuse trace/usage breakdown were visually verified
and captured. Both frontends remain loopback-only; the dedicated namespaces,
completed OpenShell sandbox, private state and generated credentials are retained
intentionally. Unrelated workloads were not removed. Host disk has ~167 GiB free.

New helpers provide retained Langfuse installation/status, supervised UI/API
port-forwards, and read-only live-run reconciliation. The actual local export path
uses a fixed nginx relay, not a collector transformation; M5's independently tested
collector path remains separate. Exporter auth is a private Kubernetes Secret.

[Setup/access and reproduction](../../deploy/reference/LANGFUSE-LOCAL.md) ·
[Evidence and screenshots](evidence/observability-live-20261030/qualification.json).

The bootstrap administrator retains its original expiry. Automatic approval review
also rejected a narrower one-day session-only observer credential because explicit
consent was required to persist new access. That request is pending with the user;
no new service credential was created. This affects later management login, not
the completed run, recorded evidence, Langfuse login or persistent storage.
