# Persistent local Langfuse and live harness qualification

This is a retained local development installation, separate from the disposable
[synthetic export fixture](EXPORT-QUALIFICATION.md). It runs the pinned Langfuse
web/worker, PostgreSQL, Redis, ClickHouse and MinIO images on an existing **kind**
cluster. Four PVCs preserve Langfuse data across pod replacement. The harness
keeps its own PostgreSQL recording connection; Langfuse's ClickHouse is not a
harness storage adapter.

## The verified installation

On the qualification Mac, from the repository root:

```sh
# Data services remain in Kubernetes; this supervises loopback access only.
node deploy/reference/observability-local-views.mjs .ow-service-observability-live

# Read-only status; does not provision or submit an agent.
node deploy/reference/langfuse-kubernetes-local.mjs status \
  .ow-service-observability-live/langfuse

# Reconcile the journaled, completed UI run. This never reruns the agent/tools.
node deploy/reference/qualify-live-observability.mjs .ow-service-observability-live
```

Only run one view supervisor at a time. It starts the local management UI and
port-forwards for the service API and Langfuse, and reconnects those forwards
after pod replacement. Ctrl-C stops those processes without removing workloads,
credentials, PVCs or the completed agent's sandbox.

* Management: <http://127.0.0.1:58650/#observability>. Select the completed run
  `t_d63713b94609450b9222d7f488f3ce86` using the searchable run picker.
* [Langfuse trace](http://127.0.0.1:59600/project/openworker-local/traces/86ba88d0f039172690283117373d726b).
  Its usage breakdown shows **10,560 input + 222 output = 10,782 tokens**.
* Service/session: `ows-c95161815cda` /
  `s_81467909b63247ac992f8c5d0abd6a8e`.
* Langfuse namespace: `ow-langfuse-observe20261030`; service namespace:
  `ow-service-m4-observe20261030`; kind context: `kind-ow-server-20261011`.

Credentials are deliberately absent from this document. The generated Langfuse
email/password are in `.ow-service-observability-live/langfuse/private/credentials.json`.
The service's existing administrator token is in `service/input/admin-token`
under the same state root, with its **original one-hour expiry**, not a renewed
demo lifetime. The management UI does not extend service authorization. After
expiry an authorized administrator must supply another valid scoped credential;
stopping/restarting the view supervisor does not renew one. Never copy private
state or raw pod logs into commits or handoffs.

For reconciliation after an authorized credential rotation, set
`OW_SERVICE_TOKEN_FILE` to the replacement credential file. An observer scoped to
this session is sufficient; the checker only reads the run/timeline/summary.

## Start another local Langfuse installation

Prerequisites: Node 20+, kubectl, a dedicated local kind context and available
PVC provisioning. Cache/import the immutable vendor images from
`export-qualification-images.json` into that cluster first. Budget several GiB
for those images and about 6–8 GiB of Docker memory for the combined demo and
harness. These local limits are not production sizing guidance.

Create a private configuration file:

```json
{
  "kubeconfig": "/absolute/path/local-kind.kubeconfig",
  "context": "kind-my-local-cluster",
  "namespace": "ow-langfuse-mytest",
  "port": 59600
}
```

```sh
# Choose a fresh gitignored state directory; init refuses existing state.
node deploy/reference/langfuse-kubernetes-local.mjs init \
  .ow-service-my-langfuse /private/local-langfuse.json
node deploy/reference/langfuse-kubernetes-local.mjs up .ow-service-my-langfuse
```

`up` validates namespace ownership, reuses generated credentials and PVCs, applies
the installation and waits for readiness. It does not reset an account, replace
data, install OpenShell, or configure an agent/model. Cluster deletion or removing
the PVCs still destroys this local storage; PVC persistence is not a backup.
There is no automatic destroy command.

## Harness wiring

The actual live test used a freshly built Linux service and matching ToolRunner,
the existing OpenShell gateway, a worker-mounted Anthropic key, ask-mode tool
approvals in the management UI, and PostgreSQL recording. Export uses the existing
`langfuse_v4` OTLP dialect with metadata capture only. All 22 run-scoped source
records were matched by span ID, trace ID, parent and pseudonymous session ID to
Langfuse; its additional storage/lifecycle records carry no duplicated model usage.

For Helm, `secrets.telemetry` names an existing Kubernetes Secret whose
`authorization` file contains the exporter Authorization header value. It mounts
read-only into harness roles/bootstrap, not the UI or TLS sidecar. The entrypoint
exports it as `OW_TELEMETRY_AUTH`; public configuration references that name:

```json
{
  "telemetry": {
    "endpoint": "https://your-langfuse.example/api/public/otel",
    "deployment": "your-deployment",
    "signals": ["traces"],
    "acknowledgement": "langfuse_v4",
    "headers": [
      {"name": "Authorization", "value_env": "OW_TELEMETRY_AUTH"},
      {"name": "x-langfuse-ingestion-version", "value": "4"}
    ],
    "content": {"capture": "metadata"}
  }
}
```

The local cluster uses a fixed loopback nginx relay in each harness pod to reach
the namespaced Langfuse HTTP service. The relay receives no model/exporter secret
volume, and has a fixed upstream. This is an explicit local-development bridge,
not a recommendation to expose production telemetry over plaintext. Use direct
verified HTTPS for a production collector/Langfuse endpoint. The manually added
local relay must be reapplied if replacing the demo's Helm release.

## What this test found

* The catalog worker's slot database configuration lost its instance-owned
  instrumentation handle. The fix carries `store.telemetry()` into those
  connections. A real PostgreSQL/OpenShell catalog-worker regression now requires
  model, tool and execution records, rather than accepting monitor storage spans
  alone as evidence.
* The tiny synthetic fixture's ClickHouse pool and memory caps were insufficient
  for full UI queries. The retained installation bounds query parallelism/memory
  separately, increases the global thread pool, and checks HTTP `/ping` readiness.
  MinIO also needs realistic CPU/memory and readiness deadlines.
* Earlier runs retained their export gaps during that outage. They were not
  backfilled or automatically replayed. A fresh, explicitly submitted read-only
  turn supplied the final complete end-to-end evidence.

The final run, exact usage and all matched source spans survived replacement of
ClickHouse, PostgreSQL, Redis and MinIO pods with the same PVCs. Model/tool payloads
remain excluded. See the [ledger](../../docs/harness/OBSERVABILITY-PROGRESS.md) and
[sanitized evidence](../../docs/harness/evidence/observability-live-20261030/qualification.json).
