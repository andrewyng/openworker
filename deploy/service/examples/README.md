# Deployment reference files

Use one authored YAML per environment: `staging-deploy.yaml`, `prod-deploy.yaml`,
or any filename. Account/infrastructure settings live under `advanced`; no separate
setup-profile or local values/installer JSON is required. The CLI generates its
own private plan, values, credentials and recovery files. Never discard those to
retry an uncertain operation.

| Mode | Reference | Current status |
| --- | --- | --- |
| Azure | [azure/deployment.yaml](azure/deployment.yaml) | Experimental coordinator; fresh combined cloud qualification pending |
| AWS | [aws/deployment.yaml](aws/deployment.yaml) | Experimental coordinator; fresh combined cloud qualification pending |
| local-k8s | [local-k8s/deployment.yaml](local-k8s/deployment.yaml) | Existing local kind cluster; reuse journaled installer |
| local-docker | Existing `deploy/service/stack.mjs` / Compose workflow | One-YAML CLI orchestration is roadmap; command refuses effects |
| remote-k8s | Existing cluster + explicit kubeconfig/network route required | Roadmap until implemented and tested on a real remote cluster |

Reference identities, images, versions and paths are placeholders. Replace them.
Examples do not authorize provisioning, provide real image digests or promise a
spending cap. They omit `trial`: no automatic expiry by default. For disposable
data only, optionally add `trial: {expiresAfterHours: 6, deleteData: true}`.

## Azure and AWS

```sh
ows deploy azure up --config azure/deployment.yaml --plan-only \
  --max-estimated-cost-usd 100
ows deploy aws up --config aws/deployment.yaml --plan-only
```

A cost ceiling is optional, is plan-only, and checks an estimate rather than actual
billing. It fails when modeled prices are unknown. Review the price timestamp,
assumptions and excluded variable costs. Omit `--plan-only` after reviewing the
provisioning/grant bundle. Noninteractive use requires its exact `--accept-plan`
and `--accept-bootstrap` digests. Add `--keep-access` for the private management
route and use the generated `connection.json` to open the UI.

Replace `advanced.infrastructure` pins and identity/resource references in the same
YAML. Azure needs its actual subscription, installer object ID (not app/client ID),
existing workload/control groups, Entra administrator group, SSH public key,
operator IPv4, AKS/Ubuntu versions and versioned Key Vault password reference.
The documentation IPv4 and image digests are fake. The Key Vault uses RBAC and ARM
deployment access. Workload/control prerequisites and the cleanup image must exist.
The private key stays local; select `advanced.azure.sshPrivateKeyFile` if needed.

AWS needs its actual account/profile, installer ARN, region/availability zones,
reviewed versions/add-ons, versioned S3 cleanup package/checksum and regional ARM64
AMI for the optional SSM bridge. Replace the fake AMI and cleanup object reference.
Use the matching reviewed identity scopes and current regional preflight.

Set `advanced.release` to a generated release manifest from `up/package.py`:
ARM64 for AWS, AMD64 for Azure, including a UI image if enabled. Release manifests
are generated artifacts, not another hand-authored environment config. Versions
and package distribution remain subject to qualification; a CLI login alone is
not permission to provision. Export model keys using the named environment
variables or use file references. No secret values belong in deployment YAML.

[Installer details](../up/README.md) explain current prerequisites, pricing scope,
trial data deletion, private access and mode limitations.

## Existing local Kubernetes

Both local and remote Kubernetes require an already running cluster, a working
kubeconfig/context, permissions and network access. Current `local-k8s` is qualified
only for explicit `kind-*` contexts and dedicated `ow-service-m4-*` namespaces. It
does not create Kubernetes from SSH machines or install a database.

Edit the one local YAML: kubeconfig/context/namespace, service/runner/UI image
digests, chart path, model/provider references, existing OpenShell coordinates and
an existing PostgreSQL URL reference + public CA file. The database connection
verifies TLS; the credentials and model keys are read privately only after plan
approval. Kubernetes must be able to reach PostgreSQL and pull/use those images.
`scripted` is a local fixture option, not a substitute for live-provider qualification.

```sh
ows deploy local-k8s plan --config local-k8s/deployment.yaml --state .ows/local
ows deploy local-k8s up --config local-k8s/deployment.yaml --state .ows/local \
  --accept-plan PLAN_DIGEST
ows deploy local-k8s status --state .ows/local
ows management-ui status --state .ows/local/installation
```

Use the actual digest printed by plan. The shared installer checks cluster and
namespace identities, OpenShell compatibility/storage/network prerequisites and
original Helm receipts. `advanced.openshell.mode: existing` reuses the named
installation; `install` accepts a reviewed pinned archive and inline `values`
that the CLI materializes internally. It never silently upgrades customer-managed
OpenShell. The existing installer enforces its pinned archive/image requirements.
No new sandbox/model run is implied by successful installation readiness.

The CLI generates the private API certificate and maps `managementUI.port` to the
loopback preview. Keep and trust that CA; do not disable TLS verification.
Set UI/recording preferences in YAML or use the independent feature commands.
Native lower-level `ows deploy local` / `deploy cluster` remain compatibility
interfaces for existing journals; new examples use `local-k8s`.

The new `local-docker` and `remote-k8s` command names are discoverable but report
`phase: roadmap`, return failure, and create no resources. They must not be used
as proof of successful deployment or qualification. Docker's existing separate
scripts remain available pending a complete one-file CLI adapter.
