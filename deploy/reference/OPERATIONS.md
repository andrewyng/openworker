# Operational dashboard and alert runbook

This is the optional, local Grafana reference for the native server harness.
It is not a new product UI, an audit store, an SLA, or a production monitoring
deployment. Desktop, stdio and Harness SDK defaults remain unchanged.

## Enable

New `stack.mjs init` deployments with observability enabled generate both the
dashboard and alert JSON. The two APIs observe the same PostgreSQL schema;
queries take the maximum of fresh replicas, **not their sum**. The two workers
advertise one execution slot each in this reference.

Existing state directories must be upgraded explicitly; **do not reinitialize
them or rotate their secrets**. Build the new server binary. Add these fields to
the existing private `reference.json`, choosing one non-sensitive deployment ID
shared by all API/worker replicas of this database:

```json
"telemetry": {
  "endpoint": "http://127.0.0.1:54318",
  "deployment": "my-local-harness"
},
"operational_metrics": {
  "sample_interval_seconds": 10,
  "stalled_after_seconds": 300,
  "recent_window_seconds": 300
}
```

Use your existing OTLP port, not necessarily 54318. Deployment identifiers are
1–64 ASCII letters/digits/underscores/hyphens. Old telemetry JSON still accepts
an omitted deployment (`default`); it has no new state sampler unless enabled.
Never put customer identity or secrets in deployment names.

```sh
# From repository root; 2 = expected total slots, not number of workers.
node deploy/reference/operations.mjs .ow-stack my-local-harness 2
```

Restart only the owned reference observability service (Compose project/env/file
are the same ones used by `stack.mjs`), or reload Grafana's alerting and dashboard
provisioning using its authenticated admin API. A plain Compose `up` does not
reload changed alert files in an already-running container. Restart API/worker
processes to load the new binary/config in an approved maintenance window. None
of this requires changing an agent's model, permissions or sandbox.

Grafana: the existing localhost Grafana port, `admin`, password in the private
state directory. Choose **OpenWorker · DEPLOYMENT · Operations**. The older
Harness operations dashboard remains available and aggregates all deployments;
the new operational dashboard and all its alerts are deployment-scoped.

The generator's `build`/`write` functions accept explicit expected slots,
freshness, queue age, pending duration, evaluation interval and error window.
Defaults: 2 slots, 45s freshness, 120s queue age, 60s pending, 10s evaluation,
300s error window. Keep freshness longer than sampling/export latency, and the
pending period shorter than the recent-error/uncertain-transition window.

## Signals and actions

| Signal / alert | Meaning and first response |
|---|---|
| Active sessions / queued turns | Current durable state, not total sessions or throughput. Queue age includes follow-ups behind parked work. Check compatible pool capacity before adding workers. |
| Pending approvals / questions | Unanswered current-turn interactions on waiting/recoverable sessions. Terminal/cancelled or old-turn interactions do not count. Answer through the app/API, never from Grafana. |
| Worker capacity / occupied slots | Fresh advertised slots, including startup/drain occupancy. Capacity is not a proof that tools, OpenShell or model connections work. A missing/crashed worker ages out; inspect its supervisor and lease state before restarting. |
| Potentially stalled run | Active turn without a durable event for the configured interval. Waiting/recoverable turns are excluded. A legitimate quiet tool can trigger this; inspect the execution record rather than automatically cancelling or retrying. |
| Database observer unavailable | At least one fresh sampler lost its own bounded connection/query. It is not proof that every API connection failed. Check PG/TLS/query latency, then restart that sampler/API after repair. |
| Telemetry missing / state stale | No fresh sampler heartbeat / successful state snapshot. Queue and approval panels become **Unknown**, not zero. Check the process, DB, collector and network. |
| Recent uncertain effects | Count of completion/recovery transitions containing unknown effects, not number of calls or total unresolved effects. Reconcile the original environment/external system. Alert clearing means a quiet window, **not** that an effect became safe to retry. |
| Model / tool errors | Processes with a recent exported error timestamp. Even the first error is detectable without a counter baseline. Inspect sanitized traces and durable results; a later success does not immediately erase the warning. |
| Telemetry drops / export failures | Rolling increases in local loss counters. Check backpressure/TLS/collector health. Initial samples or resets can be missed; a complete outage is covered by heartbeat freshness instead. Durable audit remains authoritative. |

No alert automatically retries a tool, answers an approval, cancels a session,
scales a pool or restarts a process. Those actions require application/operator
policy, especially when a tool may already have had an external effect.

## Failure and privacy boundaries

The opt-in API observer uses a separate connection, one read-only aggregate SQL
statement per interval and the existing DB deadline. No execution row locks, payloads, IDs or
raw errors go to telemetry. A failed connection is not silently reopened;
heartbeats continue with unhealthy status until restart. A snapshot and its
freshness timestamp are enqueued atomically, so a partially dropped sample
cannot appear fresh. Telemetry remains bounded, nonblocking and best-effort.

Worker timestamps are host-clock based; state snapshots use PostgreSQL time.
Synchronize clocks. Resource UUIDs separate worker incarnations. A crash followed
by a fast restart can briefly count the old and new capacity until the old
heartbeat expires. Set thresholds for your topology; these are reference alerts,
not an availability guarantee or proof of fair scheduling.

Grafana/Prometheus may keep exporting or returning an old gauge. The queries use
the **value of explicit timestamps**, not just sample presence. Derivative work
rules suppress missing values; separate missing/stale rules detect that condition.
Datasource/query errors use Grafana's explicit Error state. If Grafana itself
dies, it cannot notify you: production needs independent monitoring of the
monitoring system. These distinctions follow Grafana's documentation on
[missing series](https://grafana.com/docs/grafana/latest/alerting/guides/missing-data/)
and [No Data / Error handling](https://grafana.com/docs/grafana/latest/alerting/fundamentals/alert-rule-evaluation/nodata-and-error-states/).

This local provisioning replaces the **owned reference Grafana's** notification
policy with an always-muted loopback-only placeholder. It sends no email,
Slack, PagerDuty or external webhook. Do **not** import that policy wholesale
into a customer's Grafana. Import the rules/dashboard into the customer's
authenticated monitoring stack and explicitly configure their contact points,
on-call ownership, retention, TLS, access controls and notification tests.
[Grafana provisioning](https://grafana.com/docs/grafana/latest/alerting/set-up/provision-alerting-resources/file-provisioning/).

## Kubernetes / production sizing

The existing Helm schema accepts the same optional telemetry/deployment and
operational-metrics settings. Keep the collector endpoint HTTPS, allow it in
the existing egress policy, and do not mount model credentials into APIs or
observability. No dependency on, or change to, the OpenShell chart is introduced.
With customer Prometheus, retain `target_info` with `job`, `instance` and
`openworker_deployment` resource labels (or deliberately adapt the queries).

Start with a small slot limit; each active slot owns a runtime and DB connection,
and each instrumented API adds one sampler connection. Parked approvals release
worker slots. Sandbox sharing/isolation remains the higher layer's explicit
choice—not a monitoring decision. Size for measured model latency, tool CPU/RAM,
database latency, queue age and sandbox cost. The earlier eight-session/four-slot
tests are correctness evidence, **not** a throughput recommendation. At larger
retention/row counts, benchmark this aggregate query and its deadline before
increasing sampling frequency or API replicas; this slice does not add rollups
or retention/index migrations.

## Repeat local fault qualification

Use a dedicated local Grafana reference and isolated test PostgreSQL, never an
application database. The script uses an allowlisted new `ow_ops_UUID` schema;
starts production sampler functions and an idle production worker; and supplies
synthetic durable state and synthetic model/tool error records. It makes no
model calls, starts no sandbox, and must never execute tools.

```sh
cargo build --locked --manifest-path harness/Cargo.toml \
  -p openworker-server-reference --example operations_probe
# Supply OW_PG_TEST_URL securely in the environment; loopback URL required.
node deploy/reference/qualify-operations.mjs .ow-stack
```

The drill checks replica deduplication, queue/stall/unknown transitions,
SIGKILL/restart of a real worker, bounded observer query timeouts from a lock on
only its own schema's audit table, first synthetic model/tool errors, missing sampler data, a broken
Grafana datasource and restoration, and payload-free metric labels. It deletes
only its schema, stops owned children, restores the datasource, and pauses its
finished experiment alerts. Evidence is `operations-qualification.json`; the
dashboard remains for inspection but is **not monitoring a live deployment**.
Earlier qualification rules in this dedicated reference are removed by their
fixture deployment identity. Regenerate the normal deployment files before
using the reference for application monitoring. Exporter slow/outage/overflow
behavior and payload privacy also have deterministic Rust tests.

This does not qualify live-provider failures, paging delivery, HA Grafana,
multi-AZ loss, cloud infrastructure, throughput or every customer tool.
