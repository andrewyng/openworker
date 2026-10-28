# Service administration and Kubernetes deployment

M4 adds expiring credentials, deployment roles, revisioned service defaults and
Kubernetes worker-pool management. Use the [Helm chart](../../deploy/service/chart/README.md)
for deployment inputs and privilege boundaries. Check the
[ledger](SERVICE-PROGRESS.md) for qualification status.

## Bootstrap and credentials

The private installer/Job initializes the first administrator with a one-hour
credential. Retrieve its file through your existing host/Kubernetes authority,
then connect with `ows login --token-file ...`. Bootstrap is never public HTTP;
retrying it does not renew, reset or re-enable a credential.

Create an explicit credential spec, for example `runner.json`:

```json
{
  "principal": "my-product",
  "role": "operator",
  "scope": {"kind": "owned"},
  "expires_at": 1794000000
}
```

Replace `expires_at` with your intended **future Unix timestamp**, at most one
year ahead. Use shorter lifetimes and rotation for application credentials.

```sh
ows credentials issue --spec runner.json --out /private/my-product.token --idempotency-key product-key-1
ows credentials list
ows credentials revoke CREDENTIAL_ID
ows audit --after 0
```

The CLI generates the random token into a private file **before** sending the
issuance request. A retry reuses that file and the same spec/key; a lost response
cannot silently create an unusable token. Responses and audit records contain
metadata, never the token. PostgreSQL stores a hash. Do not change the spec or
replace the file while retrying an uncertain issuance.

Expiry uses database time and is checked at each authorization transaction,
including delayed admission and stream reauthorization. Expiry/revocation blocks
new authority; it does not undo a tool action already admitted. Cancel explicitly
when needed. Rotation means issuing another credential, switching the client, then
revoking the old one. SDK credential callbacks can supply the current token.

| Role | Intended authority |
|---|---|
| `observer` | Read authorized resources |
| `operator` | Run and control agents within its scope and grants |
| `publisher` | Publish immutable releases; cannot activate or run them |
| `release_administrator` | Activate releases and configure RuntimeProfiles; no tool approvals |
| `service_administrator` | Credentials, service configuration and capacity; no tool approvals |
| `administrator` | Legacy compatibility role combining management and run/control authority |

Administrative roles require `scope: {"kind":"all"}`. Do not give the compatibility
administrator token to product backends. RuntimeProfile/release principal allowlists
still gate which agents an operator may submit. Approval mode and sandbox access
remain independent of these service roles.

If all administrative credentials expire, an infrastructure administrator can run
`openworker-server-reference --config PRIVATE_CONFIG auth-recover --request-id UNIQUE_ID`
from the private administrative environment. Its configured `api_token_file` must
contain a newly generated token. This creates an audited, expiring
`service_administrator`, retaining all old credentials and audit history. There is
no remote reset shortcut. Human login currently uses explicit service credentials;
browser OIDC/PKCE/device login is not advertised as implemented.

## Configuration

```sh
ows configuration get
ows configuration set --file configuration.json --expected-revision 1 --idempotency-key defaults-v2
```

Use the returned `configuration` object as the file. Supported fields:
`admissionsPaused`, `maxPendingOperations`, `maxEnvironments`,
`maxEnvironmentsPerPrincipal`, `maxQueuedTurns` and `compaction`.
Compaction uses the runtime's existing fields, including `context_window`,
`threshold_pct`, `cap_tokens` and `summary_max_tokens`.

Updates require the expected revision. Repeating the same key/body returns its
original receipt; a conflicting revision/body fails. Bounds cannot exceed the
installed service ceilings. Unknown configuration fields are rejected.

Compaction defaults and the configuration revision are captured with each new
admission and exposed in its session's service metadata. Later edits affect new
sessions, not running conversations. Pausing admissions blocks new tasks but
preserves admitted work and allows reads, approvals, answers and cancellation.
Provider endpoints, model secrets and live worker-slot changes are not accepted
through this endpoint; those remain installation configuration and controlled
rollouts in this milestone.

## Worker capacity

```sh
ows pool list
ows pool scale default --replicas 2 --expected-revision 1 --idempotency-key scale-two
ows pool list
```

A scale response is **accepted desired state**, not confirmation that pods are
ready. Read the pool's `observed` state for `pending`, `applied`, `draining` or
`blocked` or `unknown`, ready replicas and active pods. Stale observations become
`unknown` after controller silence; `checkedAt` records the observation time. If scheduling lacks capacity, it stays
pending. The service does not create cloud nodes or change spending limits.

Manual scaling is rejected for pools owned by GitOps or an autoscaler. The private
controller binds to the original worker Deployment UID and refuses a same-name
replacement. It patches only that Deployment's scale subresource with a resource
version check. After a network error it re-reads state, not blindly repeats a patch.
Tool calls retain the stronger unknown-outcome rule: no automatic action replay.

Scaling to zero requests Kubernetes termination. Service workers stop intake,
finish or park active work and release leases; deadline expiry is an explicit
interruption, not a successful tool result. New work remains queued until capacity
returns. PostgreSQL sessions and OpenShell workspaces outlive worker pods.

## Local Kubernetes qualification

Build Linux service/runner images as described in the local package, then load
both into kind using `deploy/helm/load-images.mjs`. That command prints the
content-addressed references to use below. Prepare a private installer file:

```json
{
  "kubeconfig": "/private/local.kubeconfig",
  "context": "kind-your-cluster",
  "namespace": "ow-service-m4-example",
  "connectionFile": "/private/openshell-connection.json",
  "image": "openworker-service:local",
  "imageRef": "registry/service@sha256:YOUR_VERIFIED_DIGEST",
  "runnerImage": "registry/runner@sha256:YOUR_VERIFIED_DIGEST"
}
```

The OpenShell connection must use the gateway's pod-reachable DNS/endpoint, not
host loopback. The matching runner binary is still an explicit input. For the
qualification below use `scripted: true`; it uses real Kubernetes/OpenShell effects
with deterministic model responses.

```sh
node deploy/service/kubernetes-local.mjs init .ow-service-m4 /private/install.json
node deploy/service/kubernetes-local.mjs install .ow-service-m4
# Start the explicit loopback kubectl port-forward printed by the installer.
node deploy/service/qualify-kubernetes.mjs .ow-service-m4 harness/target/debug/ows
```

The installer creates only its labeled namespace, PVC-backed PostgreSQL fixture,
private Secrets and Helm release. It does not create or modify the OpenShell
cluster. Qualification exercises permissions, expiry, settings, scale-to-zero,
queued execution, active-tool drain, controller/API rollouts and explicit sandbox
cleanup. Keep failed-test evidence for inspection rather than rerunning unknown
side effects. Delete remote environments before deleting this namespace/database.

For AWS/GCP qualification, provide existing clusters and infrastructure-authorized
credentials later. The deployment contract remains the same: image digests,
PostgreSQL TLS, gateway mTLS, service TLS and scoped Kubernetes RBAC. Registry,
network/storage classes, external load balancer and node capacity are operator
inputs. Cross-cloud, multi-architecture, identity-provider and network-policy
qualification are separate evidence, not inferred from a successful local test.

The managed service profile exposes `/v1` and authenticated health checks. Legacy
reference endpoints and direct configured-agent admission are disabled by default;
only activated releases can run. The old M1 reference profile can explicitly set
`service.legacy_compatibility: true`, but the managed Helm chart rejects that mode.
This keeps older entry points from bypassing release and credential policy.

To remove the local fixture after closing sessions and deleting environments:

```sh
node deploy/service/kubernetes-local.mjs destroy .ow-service-m4 EXACT_DEPLOYMENT_ID
```

It validates namespace ownership, stops ingress, checks again for concurrent work,
and then deletes only that namespace/PVC. OpenShell and other namespaces remain.
Private local files are retained for deliberate disposal. Use a new state directory
for a new deployment identity after destruction.
