# OpenWorker Service Helm chart

Installs the authenticated API/TLS ingress, OpenShell provisioning controller,
worker Deployment and a separate Kubernetes capacity controller. PostgreSQL and
NVIDIA OpenShell are explicit external dependencies. The chart does not create a
cloud account, cluster, node pool, database, registry or public load balancer.

Use the same chart on a local cluster, EKS or GKE; cloud qualification remains a
separate step. Infrastructure operators supply DNS/TLS, image distribution,
PostgreSQL, OpenShell reachability and secrets. No AWS/GCP SDK is required by the
capacity reconciler. Do not interpret local ARM64 qualification as cloud or x86-64
qualification.

## Required inputs

- `image`: immutable service image digest, with its matching ToolRunner image in
  `config.openshell.image`. Both must be available to the target cluster.
- `config`: non-secret server configuration (see local package). Use
  `runner: /opt/openworker/openworker`, `bundle: /opt/openworker/bundle`,
  `database_env: OW_PG_URL`, `database_ca: /run/service/pg-ca.crt`,
  `api_token_file: /bootstrap/admin-token`, `state_root: /state`.
- Existing Secrets referenced by `secrets.*` (names in `values.yaml`):

| Secret | Keys | Mounted into |
|---|---|---|
| database | `database-url`, `pg-ca.crt` | API, workers, controllers, bootstrap |
| openshell | `ca.crt`, `tls.crt`, `tls.key` | Worker and provisioning controller only |
| apiTLS | `tls.crt`, `tls.key` | TLS sidecar only |
| bootstrap | `admin-token`, `probe`, `probe.json` | Private bootstrap Job only |
| probe | `curl.conf` with a read-only probe bearer header | API readiness only |
| model (optional) | `openai` and/or `anthropic`, raw key values | Worker only |

Do not put token/key values into Helm values or Terraform state. Provision the
Secrets using an infrastructure-authorized installer, external secret integration,
or cloud secret-store binding. Model credentials currently load at worker startup;
rotation requires a controlled worker rollout. The REST configuration endpoint does
not accept plaintext provider credentials or arbitrary provider endpoints.

The private bootstrap Job creates the schema and initial admin token with a
one-hour default lifetime, then installs the explicit probe identity. `probe.json`
is a credential spec; give it an expiry and rotate its Secret/credential before
then. Long-lived installations must own probe and TLS renewal. The local installer
uses a 24-hour probe credential and 30-day TLS certificate for qualification.
After initialization, set `bootstrapEnabled: false` and remove the bootstrap
Secret/Job once administrators are enrolled. Bootstrap retries never reopen auth
or extend the original credential's lifetime. Infrastructure-admin recovery is a
private command; there is no HTTP reset route.

## Replica ownership

The default `manual` worker pool starts at Kubernetes' one-replica default. Scale
through `ows pool scale` using an expected revision. Helm omits `spec.replicas`, so
upgrades do not reset live manual capacity. The controller records the deployment
UID on first registration and refuses to adopt a same-name replacement after that.

- `manual`: service controller reconciles desired replicas within `maxReplicas`.
- `autoscaled`: an operator-supplied HPA owns replicas; REST scaling rejects changes.
- `gitops`: Helm/GitOps supplies `worker.replicas`; REST scaling rejects changes.

Changing ownership, target identity or ceilings requires an explicit infrastructure
change and registration/reconciliation plan; live REST calls cannot rewrite them.
One chart currently provides one `default` worker pool. Multiple independent
services/namespaces are supported through separate releases; this is not a claim
of arbitrary multi-pool or multi-tenant management.

`worker.slots` is a rollout-time setting, not a live per-session knob.
`terminationGracePeriodSeconds` must exceed `worker.drainSeconds + 60` (45 seconds
for runtime cleanup and 15 seconds of margin). Workers stop intake on SIGTERM,
finish or park active work, and interrupt on the explicit deadline/second signal.
Scale-to-zero preserves sessions in PostgreSQL and their OpenShell environments.
The API reports desired, ready and draining capacity separately. Pending pods do
not cause the service to create cloud nodes or expand a spending ceiling.

## Privilege boundary

Only the platform controller gets a projected Kubernetes service-account token.
Its namespace Role can read its named worker Deployment, read/patch that
Deployment's `scale` resource and list pods. It cannot create pods, read Secrets,
change RBAC or submit arbitrary manifests. API/worker/provisioning pods have no
Kubernetes API token. Use a dedicated namespace because pod listing is namespace
scoped. Deployment/pod labels bind capacity observations to the service and pool.

API and worker state directories are ephemeral: authoritative session state is in
PostgreSQL; workspace files are in the registered OpenShell environment. Do not
mount a shared host home directory. Add ingress/egress NetworkPolicies for your
CNI and approved database, gateway, model and Kubernetes API destinations; this
chart does not claim network-policy enforcement on every CNI.

The service is ClusterIP only. Expose its HTTPS endpoint through your chosen
load balancer/ingress without bypassing bearer authentication or TLS verification.
The UI, when added, will use this REST endpoint; it needs no direct database or
Kubernetes credentials.

## Local qualification

`deploy/service/kubernetes-local.mjs` creates a dedicated `ow-service-m4-*`
namespace on an explicitly named kind context. It uses a PVC-backed PostgreSQL
fixture and the existing OpenShell gateway. It never changes the default kubecontext
or touches other namespaces. See the M4 quickstart for the reproducible test flow.

## Upgrade boundary

The M4 chart supports rolling worker/API restarts of a compatible M4 installation.
Before upgrading an earlier M3 service database, stop old API writers, run the new
private migration command, and start M4 roles together. Mixed-generation M3/M4
admission/configuration behavior is not qualified. Do not assume a successful
same-version pod rollout proves cross-version database compatibility.

The managed chart disables legacy reference API compatibility. All application
traffic uses `/v1`; a caller cannot sidestep release activation through the older
configured-agent endpoints. Recovery-only mode blocks both provisioning and
Kubernetes capacity controllers before they access dependencies.

## Optional management UI

Set `managementUI.enabled`, a digest-pinned UI `image`, HTTPS `publicOrigin` and
HTTPS API `serviceUrl`. Add `caSecret` containing `ca.crt` when the API uses a private
CA. The chart creates a separate `<release>-ui` Service/Deployment without database,
model, gateway or Kubernetes credentials. Supply a TLS ingress that preserves Host
and Origin and disables stream buffering. The UI runs one replica; rollouts require
browser sign-in again but do not interrupt harness sessions.

Build/run details and qualification limits are in
[the management UI guide](../../../harness/service-ui/README.md).

### Optional agent recordings

Recording remains disabled unless `config.observability` is present. For the same
PostgreSQL target with an independent connection and schema:

```yaml
config:
  observability:
    schema: ow_observability
    queue_capacity: 1024
    retention_days: 30
```

To choose a separate PostgreSQL database/server/credential, additionally set
`secrets.observability: your-recording-database-secret`. That Secret contains
`database-url` and `ca.crt`. The chart supplies `database_env:
OW_OBSERVABILITY_URL` and `/run/observability/ca.crt`; the entrypoint reads the
mounted credential without placing it in the pod environment specification or
command arguments. Optional `config.observability.database.timeout_ms` controls
its bounded statement/connection timeout. Verified TLS is required by this chart.

The mount is independent of execution/model/bootstrap credentials and is supplied
to harness roles and the migration/bootstrap job. It is never mounted into the
management UI or TLS proxy. Bootstrap explicitly prepares both schemas before
normal startup; separate target failures do not silently select execution storage.
The APIs in [the query guide](../../../docs/harness/OBSERVABILITY-QUERIES.md) use
current service-token/session scope. Live management UI charts remain separate.

### Optional exporter authentication

Set `secrets.telemetry` to an existing Secret with key `authorization`, containing
the exporter Authorization header value. Harness roles and bootstrap mount it
read-only at `/run/telemetry/authorization`; the entrypoint supplies
`OW_TELEMETRY_AUTH`. Reference that environment name through a telemetry header's
`value_env`, keeping the credential outside public configuration. The UI and TLS
sidecar do not receive this mount. Nothing is mounted when the value is empty.

Use verified HTTPS for production exports. See the
[local Langfuse guide](../../reference/LANGFUSE-LOCAL.md) for a complete telemetry
configuration example and the explicitly local relay used during qualification.
