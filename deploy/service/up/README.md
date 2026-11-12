# Deploy OpenWorker from one YAML

Use one `staging-deploy.yaml` or `prod-deploy.yaml` for deployment choices and
advanced account/infrastructure overrides. There is no required setup-profile
file. Generated release manifests, plans, credentials and recovery journals are
separate installer artifacts, not additional configuration you must hand-author.

Complete [Azure, AWS and local examples](../examples/README.md) show the supported
schema. The service installer remains experimental until the current release
passes a fresh cloud install, agent/recovery and cleanup qualification.

```sh
ows deploy azure up --config staging-deploy.yaml --plan-only
ows deploy aws up --config prod-deploy.yaml --plan-only
```

Remove `--plan-only` to review/approve provisioning. `--accept-plan DIGEST` and
`--accept-bootstrap DIGEST` provide separate noninteractive approvals of the exact
saved review. The command never treats an uncertain result as permission to
recreate resources. Rerun with the original state to reconcile unfinished work.

## Application choices

```yaml
apiVersion: ows/v1alpha1
name: my-agents
cloud:
  provider: azure
  subscriptionId: "11111111-2222-4333-8444-555555555555"
  region: westus2
nodes:
  count: 2
  instanceType: Standard_D4as_v7
managementUI:
  enabled: true
  port: 8444
observability:
  enabled: true
  retentionDays: 14
models:
  anthropic: {env: ANTHROPIC_API_KEY}
advanced:
  # Inline the provider/identity overrides shown in the complete example.
  release: /absolute/path/to/generated-release/release.json
  infrastructure: {}
  azure:
    resourceGroup: rg-my-agents
    setupObjectId: "22222222-3333-4444-8555-666666666666"
    setupPrincipalType: User
```

The snippet omits infrastructure detail for readability; copy a complete example.
Fixed node count or a CPU/memory size range is supported; autoscaling is roadmap.
`models` supplies env/file credential references, currently OpenAI and Anthropic.
It is not a general provider-routing configuration API. The installer stages keys
privately after approval. Completion reports `modelSmokeTest: not_run`; deploy and
submit an agent separately through the service SDK.

## Advanced inputs and prerequisites

`advanced.infrastructure` embeds the reviewed provider inputs previously kept in
`setup.json`. AWS uses `advanced.aws.operatorArn`; Azure uses
`advanced.azure.resourceGroup`, `setupObjectId` and `setupPrincipalType`.
`advanced.release` selects a generated, checksum-verified release manifest with
pinned service/runner/UI images and chart/CA/OpenShell artifacts. Relative paths
in deployment YAML resolve against that YAML's directory. Generated release
manifest paths are absolute. Legacy explicit `advanced.setupProfile` import is
compatibility-only; new files do not use it or a hidden default account profile.

Install `ows`, the cloud CLI, Docker, Helm, kubectl and OpenSSL. Azure also needs
kubelogin and SSH for its optional management VM; AWS's SSM bridge needs the
Session Manager plugin. Sign in using your existing cloud identity. The installer
requires the reviewed provisioning/RBAC scopes, not merely a successful login.

Current Azure prerequisites still include existing workload/control resource
groups, an Entra administrator group, versioned database password in an RBAC Key
Vault with ARM deployment access, and an independent pinned cleanup image.
AWS requires the reviewed installer identity and a versioned S3 cleanup package.
The native approval/bootstrap flow handles its supported grants; it does not
magically create all account prerequisites. Default-pin discovery, account setup
wizard and publicly distributed release artifacts remain separate launch work.

Package the matching release using `package.py --help`: Linux ARM64 for AWS,
AMD64 for Azure, including `service-ui` when enabling the UI. New cloud deployments
require the reviewed OpenShell0.1.7 or0.1.8 archive. The current version does not assume
public service images or a published Helm chart are already available.

## Cost estimates, not spending caps

There is no required `budget` field in new deployment YAML. Check an optional
estimated-cost ceiling in plan mode:

```sh
ows deploy azure up --config staging-deploy.yaml --plan-only \
  --max-estimated-cost-usd 100
```

The CLI queries provider retail catalogs for the selected resources and saves a
separate `cost-estimate.json` with timestamp, sources, quantities, missing prices,
planned hours and ceiling result. The estimate covers modeled fixed infrastructure
plus an explicit allowance; model usage, variable traffic/storage/requests, taxes,
discounts and unmodeled resources are not a bill prediction or hard cap. A ceiling
check fails if modeled prices are incomplete or exceed the ceiling. Without a
ceiling, unknown prices remain visible rather than becoming zero or a passing
budget claim. The option requires `--plan-only`.

Quotes expire after30minutes. Rerun plan mode to refresh the quote for the same
frozen resource intent; its review digest and original operations do not change.
Before a new deployment is admitted, expired or failed quote checks require a
fresh plan review. Already dispatched deployments retain their original approval
for recovery; price expiry never causes replacement/recreation. Estimates are not
continuously enforced during execution. Cloud billing alerts are separate.
Earlier experimental YAML/native CLI budgeting is compatibility behavior only.

## Optional trial expiry

Without `trial`, the workload does not automatically expire. For disposable data:

```yaml
trial:
  expiresAfterHours: 6
  deleteData: true
```

This enables independent expiry cleanup and explicitly permits workload data
deletion. It is not a guaranteed cost cap; failures and retained control/audit
resources can still incur costs. Persistent destructive teardown remains refused
pending a separately reviewed data-retention workflow.

## UI, trace storage and private access

`managementUI.port` is the operator's loopback HTTPS preview port. The CLI derives
`https://localhost:PORT`; internal browser origin/CSRF checks remain strict. It
emits port-forward commands and CA information, not tokens in URLs. Use a current
service token to sign in. This does not create public ingress, DNS or a certificate
for a company domain. Existing ingress users can retain the legacy explicit origin;
`ows management-ui enable --ingress-url https://manage.example.com` selects it.
Do not combine an explicit ingress origin and preview port.

Use `--keep-access` to retain AWS SSM/Azure SSH private management connectivity.
The original Kubernetes endpoint/hostname/CA stay verified through the loopback
route. `connection.json` records API and enabled UI connection instructions.

UI and recording are independent and disabled unless configured. Recording uses
its own connection/schema on execution PostgreSQL by default; an existing Secret
selected by `observability.databaseSecret` may provide `database-url` and `ca.crt`
for a separate database. That Secret must be staged in the owned namespace before
service installation. Enabling initializes its schema before startup.

```sh
ows management-ui enable --state ./deployment-state/installation --port 8444
ows management-ui disable --state ./deployment-state/installation
ows management-ui status --state ./deployment-state/installation
ows observability enable --state ./deployment-state/installation --retention-days 14
ows observability disable --state ./deployment-state/installation
ows observability status --state ./deployment-state/installation
```

The commands save feature overrides in the installer journal, retain the original
review and verify cluster/namespace/Helm identities. Unknown upgrades are observed,
never replayed. Disabling recording retains stored data; its retention worker is
also stopped until recording resumes. Execution state, security audit and independent
OTLP exporters remain separate. A UI rollout requires sign-in again.

For a separately signed API certificate, the UI can select a public CA Secret/key
through `caSecret`/`caKey` or `--ca-secret`/`--ca-key`. Without an override it trusts
the self-signed API certificate generated by the installer. The UI process gets
only public trust material; its TLS sidecar gets the separate private-key mount.
