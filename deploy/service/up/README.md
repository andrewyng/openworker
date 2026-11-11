# Deploy OpenWorker with one command

This experimental coordinator installs a private service cluster, OpenShell and the OpenWorker service using a reviewed account setup profile. It resumes the same deployment when rerun. Fresh AWS and Azure installs with this coordinator still require live qualification; do not treat local tests as a production certificate.

Once the prerequisites below are installed, copy `aws.yaml` or `azure.yaml` to `deployment.yaml`, set your account/subscription and region, then run:

```sh
owcluster deploy aws up --config deployment.yaml
# or
owcluster deploy azure up --config deployment.yaml
```

The command resolves a supported VM size, writes a cost/permission review, and asks for approval before making cloud changes. Fixed node counts are supported; autoscaling is not. Requests with an uncertain outcome are observed using their original identity rather than blindly resubmitted.

## One-time prerequisites

Install `owcluster`, your cloud CLI, Docker, Helm, kubectl and OpenSSL. Azure also needs kubelogin and, for the management VM, SSH. AWS's optional operator bridge needs the Session Manager plugin. Authenticate with your own cloud identity. The command never creates long-lived cloud access keys.

An administrator prepares a target-bound setup profile at:

```text
~/.config/ows/deployments/aws/<account-id>/<region>.json
~/.config/ows/deployments/azure/<subscription-id>/<region>.json
```

Use `advanced.setupProfile` in YAML to select another file. The administrator reviews the scoped native bootstrap permission exports. AWS needs permission to create the reviewed bootstrap identity stack. Azure needs the reviewed RBAC authority at the workload/control groups and exact vault/secret scopes; its active login must match `setupObjectId`. Normal workload identities never receive these installation privileges. Azure resource groups, the Entra administrator group and the versioned database password in Key Vault already exist. The vault must use RBAC and allow ARM template deployment.

The profile contains **references and version pins**, not passwords:

```json
{
  "schemaVersion": 1,
  "provider": "aws",
  "account": "123456789012",
  "region": "us-west-2",
  "operatorArn": "arn:aws:iam::123456789012:role/approved-installer",
  "infrastructure": {},
  "release": "/absolute/path/to/release/release.json"
}
```

Replace `infrastructure` with the reviewed native AWS input described in [`harness/service-cli/examples/README.md`](../../../harness/service-cli/examples/README.md). It includes zone/version pins and a versioned S3 reference to **this CLI release's packaged cleanup guard**. For Azure, use the existing Azure infrastructure input (Kubernetes version, supported VM size, SSH public key, administrator group IDs, control group, pinned external cleanup image and Key Vault secret reference). Its profile uses `provider: azure`, the subscription as `account`, and `resourceGroup`, `setupObjectId`, `setupPrincipalType` instead of `operatorArn`. Package the matching native cleanup image before running the installer. Existing control assets are retained, not silently taken over or deleted.

This release does not assume public OpenWorker service images or a published Helm chart exist. Prepare a reviewed release manifest with `package.py --help`: it copies a chart, pinned OpenShell archive and values, platform manifest and database CA bundle, then hashes them. Supply immutable harness/runner image digests for Linux ARM64 on AWS or AMD64 on Azure. The supported OpenShell archive is 0.1.7. The CA bundle must contain 1–32 certificates. The installer publishes the selected images into its owned ECR/ACR registry. An optional UI image can be packaged, but browser ingress/UI exposure is not enabled by this initial coordinator.

## Review, budget and resume

```sh
owcluster deploy azure up --config deployment.yaml --plan-only
```

The private `.ows/deployments/<target-and-name-hash>/` directory contains `review.json`, the exact native `infrastructure/` permission/template bundle and the durable journal. Keep it: it holds the original deployment identities. Do not delete or edit it to retry an uncertain cloud operation.

`budget.limitUSD` is the reviewed experiment/planning budget used for cost checks. **It does not create cloud billing alerts and is not a hard cap.** Fixed costs, missing catalog rates and a separate variable-cost allowance are reported. Storage, logs, traffic, model usage and retained control resources can add costs; the allowance is not an upper bound. Persistent reviews use a 730-hour planning horizon; disposable trials use their specified lifetime. Set account billing alerts separately.

For automation, review the generated files, then supply their exact review digest:

```sh
owcluster deploy azure up --config deployment.yaml \
  --accept-plan <planDigest> --accept-bootstrap <planDigest>
```

AWS accepts the same options. Saved approvals apply only to that exact configuration, release and generated cloud graph. Rerunning observes unfinished operations and retains their receipts. Changed configuration requires a separate reviewed deployment/update; it is not applied in place.

## Private management access

The command can use an existing private route. If your account input enables the operator bridge, it creates the native bridge resource and connects automatically: AWS SSM port forwarding or Azure SSH restricted to the configured operator IPv4. Azure's `advanced.azure.sshPrivateKeyFile` selects the matching local key; otherwise SSH uses your existing key/agent. SSH host keys are saved in the private deployment directory. The original Kubernetes hostname and CA remain verified through a loopback proxy.

Add `--keep-access` to keep that route open after readiness checks. In another terminal use `connection.json` for the private API URL, CA, token-file path and port-forward instructions. Ctrl-C closes the local route; subsequent commands can recreate it after reconciling the original SSM session. A changed operator address, unavailable SSM session, occupied local proxy port or missing private route produces an actionable error rather than making the Kubernetes management API public.

## Model credentials and completion

Use `models.openai: {env: OPENAI_API_KEY}` or `models.anthropic: {env: ANTHROPIC_API_KEY}` (a `file` reference also works). Keys are read only when staging Kubernetes Secrets and are never written to the immutable review. The private local credential staging directory contains secrets and must not be committed.

An installation can complete without model credentials. Completion verifies component readiness; it explicitly reports `modelSmokeTest: not_run`. Submit an agent separately to qualify your chosen provider/model and application.

## Persistent deployments and disposable trials

Omitting `trial` disables automatic workload expiry. The older destructive teardown route refuses persistent deployments until a separate retention workflow is approved. It will not delete your database as a side effect of an expired provisioning deadline.

For disposable tests only, set:

```yaml
trial:
  expiresAfterHours: 6
  deleteData: true
```

This enables the existing independent cleanup controller. It retains the control/audit resources documented in the native review. A timeout or failure does not authorize deleting/recreating resources with uncertain outcomes. Read the original receipts and use the native lifecycle reconciliation/cleanup commands for the same deployment.

## Local checks

Run the CLI unit/integration suite with `cargo test --manifest-path harness/Cargo.toml -p openworker-service-cli`. Its cloud operations use fixtures. The cleanup guard and Helm rendering checks run with `node --test deploy/service/guard/guard.test.cjs deploy/service/chart.test.mjs`.

To exercise the actual `up --plan-only` entry point, package pins, approval gates and resume handling with trapped AWS commands:

```sh
python3 deploy/service/up/test-plan.py \
  --ows /absolute/path/to/ows \
  --openshell-archive /absolute/path/to/helm-chart-0.1.7.tgz
```

That check uses the real pinned OpenShell archive, fixture image references and catalog responses. It performs no real cloud calls, does not install a cluster and must not be presented as cloud qualification.

## Management UI and trace storage

Both features are optional and independent. Select them before the first `up`:

```yaml
managementUI:
  enabled: true
  publicOrigin: https://localhost:8444
observability:
  enabled: true
  retentionDays: 14
```

The reviewed release manifest must include a digest-pinned `service-ui` image.
The default private preview uses the API certificate and a loopback-only Kubernetes
port-forward; `connection.json` contains its command, URL and CA file. Trust that
certificate locally, then sign in with a current service token. No token is put in
the browser URL. Keep the cloud's private management route open as described above.
A non-loopback `publicOrigin` requires your own TLS ingress; `up` does not create
public exposure or DNS records.

After installation, use the original **installer** state directory (the
`installation/` child of the `up --state` directory):

```sh
ows management-ui enable --state .ows/my-deployment/installation
ows management-ui status --state .ows/my-deployment/installation
ows management-ui disable --state .ows/my-deployment/installation

ows observability enable --state .ows/my-deployment/installation --retention-days 14
ows observability status --state .ows/my-deployment/installation
ows observability disable --state .ows/my-deployment/installation
```

If the original release did not include a UI image, pass
`--image registry.example.com/ui@sha256:YOUR_VERIFIED_DIGEST` on enable.
`--public-origin` overrides its origin. These commands use the original Kubernetes
credentials, verify cluster/namespace/release identities, and perform a Helm upgrade
of the saved chart. They do not use the service REST token. Features are saved in
`journal.json` with their original Helm operation and revision receipts; the initial
provisioning review and `deployment.yaml` stay immutable. Rerunning `up` does not
reset these saved feature overrides. Do not edit old installer values in place.

Trace storage uses a separate `ow_observability` schema and connection to the
execution PostgreSQL by default. For a separate database, create an existing
Kubernetes Secret containing `database-url` and `ca.crt`, then select it with
`observability.databaseSecret` or `ows observability enable --database-secret NAME`.
Verified database TLS remains required. Enabling initializes the recording schema
before workloads start; feature changes roll the affected pods, so allow active
turns to drain. The first-admin bootstrap is not rerun.

Disabling recording stops new recordings as the rollout completes. It does not
delete traces, databases or credentials. While disabled, the recorder's retention
worker is also stopped; retained data remains until recording resumes or an operator
performs a separate cleanup. Security audit, durable execution state and separately
configured OTLP exporters are unaffected. This is deployment-level control, not a
new per-agent trace policy API.

A lost Helm response never causes an automatic repeat. `status` can reconcile the
original successful revision; an unresolved/pending/failed result blocks another
upgrade or teardown until the operator inspects that original operation.

Local regression checks:

```sh
cargo test --manifest-path harness/Cargo.toml -p openworker-service-cli
node --test deploy/service/chart.test.mjs
OWS_PROXY_TEST=1 node --test deploy/service/proxy.test.mjs
```

If the API uses a certificate signed by a separate CA, provide its public trust
bundle using `managementUI.caSecret` / `managementUI.caKey` in deployment YAML,
or `ows management-ui enable --ca-secret API_CA_SECRET --ca-key ca.crt`.
The Secret must already exist in the service namespace. Without an override,
the CLI trusts the API TLS Secret's `tls.crt`, suitable for the self-signed
certificate produced by `up`. The UI process receives only that public certificate;
the HTTPS proxy sidecar receives the separate TLS key mount.
