# Qualify OpenShell and OpenWorker locally

`ows deploy local` installs the pinned OpenShell gateway and the OpenWorker service
chart into dedicated namespaces on an existing kind cluster. It can also use an
existing gateway without taking over its lifecycle. This is the local qualification
path for the service installer. The [experimental AWS lifecycle](OWS-AWS-LIFECYCLE.md)
uses the same installer through `ows deploy cluster`; no cloud provisioning was
performed for local qualification.

The Rust CLI calls Helm and kubectl directly. It needs no Node or Python runtime.
The scripts below are development fixtures and acceptance tests, not runtime
dependencies or a general customer bootstrap installer.

## Prerequisites

- Explicit kubeconfig and `kind-*` context; no default-context mutation.
- Ready Calico, Agent Sandbox CRD plus available controller in
  `agent-sandbox-system`, and a default storage provisioner. These shared components
  are checked, not installed or replaced. A fresh cloud cluster still needs an
  explicit platform-installation phase.
- Helm, kubectl, and digest-pinned Linux service/runner images available to the
  cluster. The qualified service images are Linux ARM64.
- Reviewed chart values plus separately staged PostgreSQL, OpenShell client TLS,
  API TLS, bootstrap and readiness Secrets. No credential values belong in a plan.
- The `deploy local` fixture profile uses a dedicated PostgreSQL deployment named
  `postgres`. For a managed database, use `deploy cluster` with `fixtureDatabase:
  false`; cleanup reads aggregate service state through the trusted worker binary.

## Plan the installation

Build the CLI with `cargo build --manifest-path harness/Cargo.toml -p
openworker-service-cli`. Prepare a JSON config:

```json
{
  "schemaVersion": 1,
  "kubeconfig": "/absolute/path/local.kubeconfig",
  "context": "kind-my-cluster",
  "helm": "/absolute/path/helm",
  "service": {
    "namespace": "ow-service-m4-review",
    "release": "ows",
    "chart": "/absolute/path/deploy/service/chart",
    "valuesFile": "/absolute/path/service-values.json",
    "fixtureDatabase": true
  },
  "openshell": {
    "mode": "existing",
    "namespace": "my-openshell",
    "release": "openshell",
    "fullname": "my-openshell-gateway"
  }
}
```

The service namespace must use the current fixture prefix `ow-service-m4-`. The
service values must pin the image by digest and use the exact pod-reachable gateway
endpoint `https://<fullname>.<namespace>.svc.cluster.local:8080`. Configure the matching
TLS server name and client certificate Secret. A localhost gateway address cannot
be used by the worker pods.

For an installer-owned gateway, change `openshell` to:

```json
{
  "mode": "install",
  "namespace": "ow-local-shell-review",
  "release": "openshell",
  "fullname": "ow-local-gateway-review",
  "archive": "/absolute/path/helm-chart-0.1.3.tgz",
  "valuesFile": "/absolute/path/openshell-values.json"
}
```

Use NVIDIA OpenShell chart **0.1.3**, archive SHA-256
`5a82e27d6cf4089fa16d96c87d9fc0fe62f65fa05341465bc749ba5ab91abb33`
(OCI digest `sha256:44ec11bbf264fde0b65c804c856113a4dd322731a8b3803315724357855a434b`).
The values must set `fullnameOverride`, disable unauthenticated gateway access, and
pin gateway, sandbox runtime, supervisor and default sandbox images by digest.
Use a unique fullname: the upstream chart also creates cluster-scoped RBAC.
This version pin records the tested integration, not a production support guarantee.

```sh
ows deploy local plan --config ./installer-config.json --out ./install-state
ows deploy local apply --state ./install-state --accept-plan <planDigest> --openshell-only
# Stage the service Secrets and private database/certificate fixtures separately.
ows deploy local apply --state ./install-state --accept-plan <planDigest>
ows deploy local status --state ./install-state
```

`--openshell-only` prepares the namespaces and gateway first, so its generated client
TLS material can be handed to OpenWorker privately. Full apply then waits for API,
worker, controller and platform-controller rollouts. Worker readiness performs the
authenticated OpenShell API/version/driver check. A healthy rollout is not a
functional sandbox or network-policy qualification; run those acceptance tests too.

## Resume and remove

Keep the same state directory after an interruption. The journal records intent
before mutations and binds the cluster UID, namespace UIDs, input hashes and Helm
release receipt/revision. A new installer process reconciles those original objects.
It never substitutes `helm upgrade --install` for an uncertain installation.

If Helm's response was lost but the original deployed release is present, rerunning
apply confirms it without installing again. If no release is observed, apply stops:
absence alone does not prove the earlier request had no effect. After independent
inspection of the original release, hooks and cluster-scoped resources, an operator
may explicitly confirm no effect:

```sh
ows deploy local apply --state ./install-state --accept-plan <planDigest> \
  --confirm-no-effect service-release
```

The other permitted reset is `openshell-release`. Completed, pending or failed
releases cannot be reset this way. An original hook-free service release that timed
out can instead use `--reconcile-ready-release service-release`: verify its original
UID/revision/request labels, successful bootstrap Job and ready rollouts without
reinstalling it. OpenShell hook recovery requires operator inspection. Failed Helm installations require inspection;
the installer does not silently delete and reinstall them. The old operation intent
is retained in recovery history. Keep input files unchanged; editing chart/values
requires a newly reviewed plan rather than silently changing an accepted operation.

Close sessions and explicitly delete their execution environments before teardown:

```sh
ows deploy local destroy --state ./install-state --deployment-id <deploymentId>
```

Teardown checks the fixture database, freezes API intake, waits for API pods to exit,
and checks the database again. It refuses active sessions or environments. A race
that introduces work before the second check stops teardown with API intake frozen;
inspect the deployment before restoring intake. This is not a general production
drain implementation for an external database.

Owned Helm releases are removed before namespace deletion, including OpenShell's
cluster RBAC. Namespace deletion uses a UID precondition. Owned OpenShell gateways
must have no remaining sandboxes. Existing gateways are never upgraded or removed.
Unknown uninstall outcomes stop for inspection rather than repeating the action.

## Development acceptance helpers

`prepare-local-install.mjs` uses this workstation's cached `.ow-service-build/`
images/configuration and Helm path. It is an explicit qualification helper, not a
portable installer. It generates public reviewed values first, then stages private
fixtures with a separate command. Adjust those cached tool/image inputs when
qualifying another development machine.

```sh
node deploy/service/prepare-local-install.mjs prepare .ow-service-local-test install
ows deploy local plan --config .ow-service-local-test/installer-config.json \
  --out .ow-service-local-test/state
ows deploy local apply --state .ow-service-local-test/state \
  --accept-plan <planDigest> --openshell-only
node deploy/service/prepare-local-install.mjs fixtures .ow-service-local-test
ows deploy local apply --state .ow-service-local-test/state --accept-plan <planDigest>
node deploy/service/qualify-local-install.mjs .ow-service-local-test /absolute/path/ows
node deploy/service/probe-local-network.mjs .ow-service-local-test
ows deploy local destroy --state .ow-service-local-test/state --deployment-id <deploymentId>
```

Use a fresh directory and `existing` for the second mode. The acceptance test uses
a deterministic model, but real PostgreSQL, worker/controller processes, authenticated
OpenShell provisioning and sandbox file/shell effects. It also checks installer
restart, active-work teardown refusal and explicit sandbox deletion. The network
probe checks baseline allow, ingress/egress deny, then restored allow; failed probe
resources remain in the owned test namespace for diagnosis.

## Package the independent expiry guard

```sh
ows deploy guard package --out ./guard-package
node --test deploy/service/guard/guard.test.cjs
```

Packaging is offline and deterministic: `cleanup.zip` contains `index.js`, with a
source/archive SHA-256 manifest. It does not upload or invoke anything. The Lambda
targets Node.js 22 and its runtime-provided AWS SDK v3 clients; exact runtime/SDK
compatibility still needs live qualification before AWS apply can be enabled.

The guard validates deployment/account/region/function identity and fixed expiry.
Its independent DynamoDB control table stores the original stack ARN and deletion
intents using conditional writes. Its IAM grant permits one deployment key only.
Health probes are read-only. Before expiry it may bind the observed stack identity,
but performs no destructive action.

At expiry, it inventories the owned artifact bucket completely within explicit
bounds before removing deployment-prefix objects/uploads, then requests deletion
of the bound stack with its stable request token and exact executor role. Foreign
objects, bucket versioning, partial cleanup, replacement stacks or ambiguous
ownership stop the process. Lost stack/volume deletion responses are not blindly
retried. Tagged available volumes can be removed after the original stack is absent;
the guard and bootstrap remain until teardown is independently verified.

These are disposable-experiment deletion semantics, not a hard spending cap or a
backup policy. New schema-3 infrastructure bundles include the guard control table
and updated scoped policies. Regenerate and review prior development schema-3
bundles; schema-1/2 identity-only exports keep their original shape.

Local evidence: [28 October qualification](evidence/local-installer-20261028.json).
Cloud lifecycle reconciliation, fresh-cluster platform bootstrap, private management
API connectivity, artifact upload and live cloud guard/identity checks remain separate
work. No AWS provisioning was performed in this qualification.
