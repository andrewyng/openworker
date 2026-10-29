# Optional harness telemetry

Content-free traces, metrics and structured lifecycle logs using OpenTelemetry.
Disabled by default. No global subscriber/provider, default vendor destination,
durable spool, change to approvals, or change to agent recovery. This is a first
observability slice, not the complete server-spec S4 acceptance gate.

Optional [local diagnostic recording](../observability/README.md) now composes
with this instrumentation in the server reference. It has a separate bounded
writer and SQLite/PostgreSQL storage; enabling recording does not change exporter
content settings. Recording is opt-in, covers existing execution segments, and
does not yet provide complete auxiliary-call usage or cross-resume turn tracing.
The queue remains best effort; committed records survive restart, but are not
audit or billing evidence.

## Server reference

Add this optional field to the existing server-reference JSON configuration:

```json
"telemetry": {"endpoint": "http://127.0.0.1:4318"}
```

The API/worker binary configures its own components once. The endpoint is an
origin, not `/v1/traces`; export uses `/v1/traces`, `/v1/metrics`, `/v1/logs`.
HTTPS is required except literal loopback addresses or localhost. Paths, query
strings, URL credentials and fragments reject. Invalid configuration fails
startup; an unreachable collector does **not** fail startup or agent execution.
Authenticated `/readyz` includes local telemetry counters without making
collector availability a readiness dependency. Shutdown emits at most one
content-free diagnostic summary when records dropped or flushing failed.

The reference transport has verified TLS, no redirects, no ambient proxy, no
inherited OTEL HTTP headers, no automatic resource detection, no exporter retries,
a 3-second HTTP deadline and a 64-KiB collector response-body cap. Collector
authentication/custom CA configuration is not exposed in this first reference;
use a local collector/sidecar or app-owned export components. Do not place secrets
in the endpoint. No API/model/tool credentials are forwarded to the collector.

## Embedded/runtime integration

The core `openworker-telemetry` crate has no OpenTelemetry SDK/exporter dependency
unless `otel`/`otlp` is enabled. Existing Runtime constructors, stdio and Harness
SDK defaults remain off. The high-level Harness SDK has no new configuration API.
Trusted Rust hosts can use `Runtime::open_instrumented`, which takes the existing
constructor arguments plus a `Telemetry` handle. PostgreSQL hosts also set
`Config.telemetry` so worker slots/storage share that handle.

```rust,ignore
let (telemetry, controller) = openworker_telemetry::Telemetry::start(sink, 2048)?;
db_config.telemetry = telemetry.clone();
// Pass telemetry to Runtime::open_instrumented(..., telemetry).
// After stopping agent work, from a blocking thread:
let flushed = controller.shutdown(std::time::Duration::from_secs(5));
```

`Sink` receives fixed-shape completed records on a dedicated thread. The `otel`
feature supplies `otel::OtelSink::new(Arc<dyn SpanProcessor>, SdkLoggerProvider,
SdkMeterProvider)` for app-owned OpenTelemetry components. Completed spans go to
the processor's `on_end`; this is not a global tracing-subscriber integration or
an application TracerProvider's sampler/on_start hook. Keep an owner for shared
components: the sink flushes but does not explicitly shut down app providers.
Custom sinks/providers own any extra resource metadata or content they add.

The `otlp` feature supplies `otlp::Config::start(role)`. Construct it on a blocking
thread when embedding in Tokio (the reference does this). Official components
are pinned at **0.33.1**. Rust traces/exporters are beta; metrics/logs are stable
per the official status documentation. We use versioned `openworker.*` attributes
and metrics, not an assertion of conformance to evolving GenAI conventions.
[Research and sources](https://opentelemetry.io/docs/specs/semconv/gen-ai/).

## Meaning of the signals

| Operation | Meaning |
|---|---|
| `api.request` / `command.admission` | HTTP handler/response preparation and durable command admission, not SSE stream lifetime or applied approval. |
| `runtime.command` | Local protocol command, with no method payload exported. |
| `agent.execute` | One active execution segment. Ends when parked; a resumed segment has a new trace. |
| `model.attempt` | Main agent model attempt, including preparation/checkpoint overhead; each retry is separate. |
| `tool.execute` | Actual admitted harness/MCP/internal tool execution. Denial is not a tool execution. Missing result is unknown, not success. |
| `policy.review` | Authorization/reviewer path. Includes waiting/denied outcomes. |
| `environment.operation` | Tool/environment preparation. Not full provider lifecycle coverage. |
| `storage.call` / `worker.activation` / `worker.lease` | Bounded store operation, claimed worker slot lifetime, and lease renewal. |
| `session.recovery_scan` / `session.recovery` | Startup scan versus actual committed per-session PostgreSQL recovery transitions. |
| `approval.*` / `question.*` | Newly committed request/response transitions, not replayed events. |
| `turn.cancellation` / `tool.unknown_outcome` | Observed cancelled completion or uncertain effects; no rollback claim. |

Real parent/child relationships are maintained with task-local context within an
execution segment. New Tokio jobs and database actors do not implicitly inherit
context. Cross-process activity correlates through SHA-256 session/action hashes
on spans/logs, not a fabricated shared trace ID. Durable distributed trace links
are deferred. Hashes are pseudonymous identifiers, **not anonymization**.

No prompts, answers, messages, images, tool arguments/results/names, URLs, file
paths, SQL, raw errors, private reasoning, headers or arbitrary baggage are
accepted into instrumentation records. Only fixed operations/outcomes, identity
hashes, generated trace/span IDs, durations, attempt numbers and numeric usage
are available. Error status text is a constant, never an exception string.

Metrics use only bounded operation/outcome/direction dimensions:
Resource identity also contains the fixed service/role and a generated process
instance UUID, keeping different workers' cumulative counters separate, plus an
explicit non-sensitive deployment identifier (default `default` for old JSON).

- `openworker.operation.count` and `openworker.operation.duration` (seconds).
  Transition events have counts, not duration samples.
- `openworker.model.tokens`: reported input/output tokens for completed main
  model attempts. Missing usage stays absent. Not a billing ledger.
- `openworker.model.retry.count`: observed main model attempts numbered >1.
- `openworker.model.first_output.duration`: first nonempty visible delta from
  attempt start. Not provider-internal token generation time.
- `openworker.telemetry.dropped`: cumulative local enqueue/drain drops.
- `openworker.telemetry.export_failures`: failed export/flush batches, not an
  exact HTTP request count. Both gauges are snapshots on subsequent exports;
  `Telemetry::snapshot`/`Controller::snapshot` remain available during an outage.
- `openworker.operation.last_error_timestamp_seconds`: latest model/tool error
  completion time per process/operation; catches a first error without requiring
  an earlier zero counter sample. Still best-effort, not an audit record.
- Optional fixed numeric server/worker gauges cover queue depth, active turns,
  current pending interactions, queue age, potential stalls, recent unknown
  transitions, sampler/database health, worker slots and freshness timestamps.
  `Sink::observe` is optional for older custom sinks. `observe_many` accepts or
  drops an entire bounded snapshot atomically. No trace/log is manufactured.
  See the [operations runbook](../../deploy/reference/OPERATIONS.md) for exact
  meanings, configuration, replica deduplication and missing-data handling.

## Bounds and failure behavior

The execution-side queue is at most 2,048 fixed-shape messages plus an in-flight
batch of at most 128. A message is one record or up to 14 fixed numeric gauges;
an observation-only batch creates no spans/logs. Enqueue never waits on a collector. The official trace/log
processors also have explicit 2,048-entry caps and 128-record batch limits; a
single dispatcher flushes each batch before submitting another. Metric dimensions
are fixed and bounded. No per-session in-memory span registry is retained.

Full/closed queues drop new records and increment local counters. Export failures
lose telemetry rather than retry agent work. Metrics/logs/traces are best-effort,
not exactly-once and not durable audit. Drops can leave incomplete trace trees.
Sampling is not implemented; all admitted records are sampled.

Shutdown fences producers, drains within a deadline and waits at most five
seconds **total**. It returns false if incomplete and does not wait for every
queued record individually. An in-flight export batch may finish afterward;
custom app sinks that ignore deadlines cannot be forcibly killed. No new batch
starts after the drain deadline. Keep this distinction when hosting in-process.

## Qualification

```sh
# harness/
cargo test --locked -p openworker-telemetry --all-features
cargo test --locked -p openworker-runtime --test telemetry # Linux/Windows
cargo run --locked -p openworker-telemetry --features otlp --example collector -- http://127.0.0.1:4318
```

`collector.yaml` is a disposable local collector configuration; bind any Docker
published port to host loopback. Verified image:
`otel/opentelemetry-collector-contrib@sha256:e7c92c715f28ff142f3bcaccd4fc5603cf4c71276ef09954a38eb4038500a5a5`
(0.147.0). Debug export exposes all received attributes, so this fixture must
never receive private application payloads from other clients.

Set `OW_TEST_OTLP_ENDPOINT` on the existing server-reference replica test to
enable real API/worker telemetry while it exercises PostgreSQL and OpenShell.
Wire tests decode all three protobuf signals and check parent IDs/privacy;
fault tests cover full queues, blocked sinks, refused/slow collectors and bounded
shutdown. A real runtime test covers human allow/deny/resume and actual native
tool execution without exporting content.

Remaining: durable cross-worker propagation/sampling, active-session/queue-depth
gauges, durable approval-wait durations, per-model error classification, separate
reviewer/compaction token accounting, full environment lifecycle coverage,
collector auth/CA configuration and production dashboards/alerts. No Windows
VM requalification or cloud/multi-AZ observability acceptance is claimed here.
