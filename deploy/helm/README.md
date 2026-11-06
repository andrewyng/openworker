# OpenWorker + OpenShell: local Kubernetes reference

Two harness API pods, two worker pods, verified TLS, PostgreSQL and S3 artifacts,
with real tools in a separately installed OpenShell gateway/sandbox. The model
is deterministic: this qualifies deployment and recovery, not AI quality.
No AWS account, provider key, Python runtime in the application image, Docker
socket mount, privileged harness pod or public endpoint.

The reusable chart is `openworker/`. It does **not** install PostgreSQL, object
storage, OpenShell, a CNI, a certificate authority or an identity provider. It
connects to those independently managed resources. `local.mjs` supplies isolated
development backing fixtures; those fixtures are not a production database chart.

## Prerequisites and compatibility

- Dedicated kind Kubernetes cluster and explicit kubeconfig/context, Docker,
  Node 20+, OpenSSL, kubectl and Helm. Never point this qualification at customer
  state. The driver refuses non-kind/unexpected contexts and unrelated namespaces.
- An enforcing CNI and Agent Sandbox CRDs/controller installed by the cluster
  administrator. Follow `../local/README.md` and run its network policy probes.
  We tested Kubernetes 1.36.4, Calico 3.33.0, Agent Sandbox 1.0.6, Helm 3.22.0 and
  kind 0.33.0 on a Linux ARM64 node inside Docker Desktop.
- Independent upstream OpenShell 0.1.3 release. OCI chart digest
  `sha256:44ec11bbf264fde0b65c804c856113a4dd322731a8b3803315724357855a434b`;
  downloaded archive SHA-256
  `5a82e27d6cf4089fa16d96c87d9fc0fe62f65fa05341465bc749ba5ab91abb33`.
  The local driver checks the archive before installation. Its upstream path is
  experimental; do not infer customer-production readiness from a local pass.

OpenShell runs with `allowUnauthenticatedUsers=false`, verified TLS and its
certificate-based caller identity. The live test created an environment using
the client certificate and verified that the same gRPC API rejects a caller
without it. API pods receive **only the gateway CA**, not its private client key;
workers/environment provisioning receive the client credential. This supersedes
the older port-forward-only development-principal setup for this new release;
the older reference gateway is not modified. Customer OIDC, certificate rotation
and trust-domain/tenant design remain separate work.
The current harness adapter uses certificate authentication; an OIDC-only gateway
needs additional client integration. Do not enable unauthenticated access to work
around that mismatch.

## Build and import images

For planned worker replacement, see [draining](../reference/DRAINING.md).
The chart defaults to 300s drain + 45s cleanup + 15s margin and rejects a shorter
termination grace. Keep compatible old pools for version-pinned sessions; a
rolling Deployment alone does not migrate those identities or sandbox storage.

From the repository root, a normal source-built image uses:

```sh
docker build --target runtime -f deploy/containers/Dockerfile -t openworker-harness:local .
```

The Dockerfile builds both native binaries with the locked Cargo dependencies;
its final image contains the server, matching tool runner and example coworker.
It excludes local state/credentials from the build context. Replace the bundled
example with your reviewed coworker/tool distribution for a real application.
Base images are pinned; apt package updates mean rebuilds are not byte-identical.
Production image signing, SBOMs and registry publishing are not automated here.

For a local prebuilt-image workflow, place matching stripped Linux binaries
in `.ow-helm/build/{openworker,openworker-server-reference}`:

```sh
docker build --target prebuilt -f deploy/containers/Dockerfile -t openworker-harness:local .
docker build -f deploy/local/runner.Dockerfile -t openworker-runner:local .ow-helm/build
node deploy/helm/load-images.mjs /path/to/kind YOUR_KIND_CLUSTER openworker-harness:local openworker-runner:local
```

Ensure the runner image contains
the **same Linux executable** as the harness image; the harness pins its identity.
The loader imports the selected CPU platform and returns manifest-digest image
references. It does not confuse Docker configuration IDs with manifest digests,
push to a registry or prune images. Dependencies can be pulled normally by kind.

For the source-built route, extract `/opt/openworker/openworker` from that image
into a new temporary runner build context, then build `../local/runner.Dockerfile`.
Do not substitute an unrelated locally compiled binary. Publish/load both images
and use their exact digests in configuration.

## First installation

Create a non-secret input JSON outside Git (replace every placeholder):

```json
{
  "kubeconfig": "/private/server.kubeconfig",
  "context": "kind-YOUR_KIND_CLUSTER",
  "helm": "/absolute/path/to/helm",
  "image": "docker.io/library/openworker-harness@sha256:YOUR_MANIFEST_DIGEST",
  "runnerImage": "docker.io/library/openworker-runner@sha256:YOUR_MANIFEST_DIGEST"
}
```

```sh
node deploy/helm/local.mjs init /private/input.json
node deploy/helm/local.mjs openshell
node deploy/helm/local.mjs fixtures
node deploy/helm/local.mjs install
node deploy/helm/local.mjs environment
node deploy/helm/qualify.mjs
```

The fixed development namespaces are `ow-helm-shell` and `ow-helm-eval`; releases
are `ow-shell` and `ow`. Use a separate dedicated kind cluster for another copy.
Shared cluster prerequisites are reused, never replaced. The driver records an
ownership annotation and refuses another installation's namespaces. `init`
refuses an existing private directory: it never replaces database passwords or
revoked keys. State is retained under gitignored `.ow-helm/` (0700), with private
credentials/config files. Local certificates have a **30-day** lifetime; use a
deliberate renewal procedure, not `init`, when they expire.

`install` creates the chart with zero replicas, runs explicit one-off migration,
administrator bootstrap and minimal observer/probe-key Jobs, then enables API/
worker replicas. It is first-time setup, not the normal upgrade command. Failed
Jobs have no automatic retry: inspect their state/logs before intervention.
Bootstrap uses the existing registry; no restart un-revokes credentials.
`environment` saves the same durable creation receipt on an exact retry; it must
never create a replacement sandbox to hide an uncertain provisioning result.

The development PostgreSQL and SeaweedFS instances use retained PVCs. PostgreSQL
requires TLS for remote connections. S3 is exposed only through a TLS proxy;
its raw API/admin ports are blocked by NetworkPolicy. Its CA is explicitly
configured using the new optional S3 `ca_file`; certificate verification is never
disabled. The test creates objects from actual sandbox file-tool results.

## Using the chart with existing infrastructure

Provide reviewed non-secret `config` values matching the reference server and
existing Secret names. The chart fails on mutable image references unless the
explicit local-only escape is enabled, plaintext database/OpenShell configuration,
invalid capacity or missing required Secrets. It does not grant K8s API access.

| Input | Contents / mount |
|---|---|
| `secrets.database` | `url` → `OW_SERVER_DATABASE_URL`; verified database TLS. |
| `secrets.databaseCA` | `ca.crt` → `/database-ca/ca.crt`. |
| `secrets.openshell` | Gateway `ca.crt`, `tls.crt`, `tls.key`; API projection gets only CA. |
| `secrets.apiTLS` | `tls.crt`, `tls.key` for API sidecar; include Service DNS in certificate. |
| `secrets.probe` | `curl.conf` containing an observer bearer header, never an administrator key. |
| `secrets.objectStore` | Optional S3 credential environment variables; API only. For cloud, prefer workload identity after qualification. |
| `secrets.objectStoreCA` | Optional private object-store `ca.crt`; API only. |
| `secrets.model` | Optional model credential environment variables; workers only. |

The API still binds **127.0.0.1:8080 inside its pod**. A non-root TLS sidecar exposes
8443, and its ClusterIP Service exposes 443. Requests still need an API bearer;
TLS does not replace authorization. SSE/large body buffering is disabled and
timeouts are bounded. Same-pod containers are one trust boundary. No Ingress,
LoadBalancer, external DNS or public exposure is created by this chart.

`config` belongs to the operator; never put secret values inside it or Helm values
(Helm retains release manifests). Config/credential subPath mounts require an
explicit pod rollout after rotation; there is no silent live reload. Controller
local `/state` is ephemeral; PostgreSQL, S3 and the sandbox PVC hold durable data.
Do not select filesystem artifact storage for this multi-replica chart.

API readiness checks the authenticated API/database path. Liveness checks only
process existence: database outages remove readiness rather than forcing API
restart loops. Workers fail-stop on unusable DB connections and Kubernetes
restarts them; Running/Ready is not proof that a model/tool dependency is healthy.
After a poisoned API database connection, explicitly roll out the API pods once
the database is healthy; this chart does not add an automatic reconnect supervisor.
Termination grace is 360s by default (300s drain + 45s cleanup + margin);
started external effects are never blindly replayed.

NetworkPolicy allows DNS and selected internal service ports. Add explicit
`network.serviceNamespaces`/`extraEgress` for your database, gateway, object store,
model and telemetry destinations, and `clientNamespaces` for application callers.
Do not assume a namespace allowlist provides tenant isolation. Model/internet
egress is **not enabled** by the local fixture. OpenTelemetry is optional via an
explicit HTTPS `config.telemetry.endpoint` and matching egress; no collector or
dashboard is installed implicitly by the chart. The Compose console remains
separate; optional Task Desk is configured through `taskDesk` values. See the
[Deployment options](../../docs/harness/guides/deployment.md) for the
current integration and AWS deployment path.

## Inspect, upgrade and stop

All kubectl/Helm operations must include the explicit kubeconfig/context.
To inspect the HTTPS API locally:

```sh
kubectl --kubeconfig /private/server.kubeconfig -n ow-helm-eval port-forward service/ow-openworker 9443:443 --address 127.0.0.1
# Another terminal; never put the bearer itself in arguments:
NODE_EXTRA_CA_CERTS=.ow-helm/private/api.ca.crt OW_API_URLS=https://localhost:9443 OW_API_TOKEN_FILE=.ow-helm/private/admin node harness/server-reference/client.mjs --session SESSION_ID
```

The administrative file is for this trusted local operator only. Issue a scoped
operator for an application and never send the administrator key to browser code.

Use `node deploy/helm/local.mjs upgrade` for chart changes, not `install`.
No automatic DB migration, downgrade or provider switch accompanies upgrades.
Review version compatibility and take coordinated backups before real upgrades.
To pause this local instance, first scale its API/worker Deployments to zero and
wait for termination, then scale its PostgreSQL/objects fixtures to zero. Resume
the fixtures and wait for readiness before `upgrade`. Keep OpenShell/sandbox
running if you want to inspect its workspace. Never delete a namespace/PVC or
uninstall the gateway as a shortcut for restart; that can lose data or orphan
active sandboxes. `helm uninstall ow` removes harness resources only, not the
separately provisioned Secrets/PVCs/databases/OpenShell.

## Verification and limits

For version changes, use [side-by-side pools and the upgrade runbook](../reference/UPGRADES.md).
Worker readiness now checks the database and authenticated OpenShell gateway;
it does not make different execution identities interchangeable.

```sh
HELM=/absolute/path/to/helm node --test deploy/helm/chart.test.mjs
node deploy/helm/qualify.mjs
```

Qualification asserts TLS and role denials, unauthenticated gateway rejection,
real file tools, pending-approval identity through worker/API replacement,
contiguous SSE, TLS S3 upload/download, out-of-project kernel denial, absent
Docker/Kubernetes credentials in the workload, shipped Node client HTTPS resume,
and results/revocation after replacing both APIs. It leaves demo sessions and
artifacts; the latest report is private `.ow-helm/qualification.json`.

This is one local ARM64 node, not multi-AZ HA, EKS/AKS, Windows, a load test or a
backup/restore drill. Database/object-store fixtures intentionally remain simple.
Production follow-ups include managed storage/IAM, automated TLS renewal, a
secrets manager, ingress/user identity, image release/signing, tenant quotas,
backup/restore, availability/upgrade testing and resource/cost sizing. Preserve
the separate upstream OpenShell lifecycle in the future AWS installer.
