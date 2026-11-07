# Persistent local Langfuse

Use this for a persistent local development installation. The disposable
export fixture is available through `qualify-exports.mjs`. This setup runs pinned Langfuse
web/worker, PostgreSQL, Redis, ClickHouse and MinIO images on an existing **kind**
cluster. Four PVCs preserve Langfuse data across pod replacement. The harness
keeps its own PostgreSQL recording connection; Langfuse's ClickHouse is not a
harness storage adapter.

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
