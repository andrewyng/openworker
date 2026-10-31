# Azure service deployment reference

Azure support is experimental and locally qualified with deterministic fixtures.
No live Azure account, IAM, AKS, PostgreSQL, Blob, scheduling or OpenShell deployment
has been qualified. These commands describe the implemented flow for a separately
authorized trial; they are not instructions to create resources without review.
AWS/kind behavior remains unchanged.

## Authority and prerequisites

Supply an existing tenant/subscription, region, **dedicated workload resource
group** and **different dedicated control resource group**, approved existing
Entra user/service-principal object ID, exact admin group IDs, an SSH public key,
and one version-pinned existing Key Vault password reference. The groups remain
customer owned. No command assumes subscription Owner, generates a client secret,
impersonates an arbitrary managed identity or installs Azure extensions.

The bootstrap administrator reviews separate exact-scope grants. The normal
executor never receives RBAC write authority. AKS cluster, kubelet, artifacts and
external cleanup each have a distinct managed identity. Blob data roles bind the
exact container; workload federation binds
`system:serviceaccount:ow-service:ows-artifacts` and its OIDC issuer/audience.

The installer needs private AKS/DNS/database access, customer-approved cluster
administrator rights through Entra group membership, `az`, `kubelogin`, `kubectl`
and Helm. The user-credential action is scoped to the one original cluster;
admin kubeconfigs/static client certificates are not used. Namespace-only rights
cannot install OpenShell and Agent Sandbox cluster components. Separate reviewed
ACR push rights are needed for image uploads.

The plan's budget is an authorization and planning limit, not a hard spending cap.
Regional SKUs/version, quotas, actual prices, Azure Policy/locks/deny assignments,
network overlap and managed-identity effective permissions must be checked in the
live trial. The reference bounds the worker topology to two AMD64 Ubuntu nodes;
this does not prove their OpenShell runtime compatibility.

## 1. Review immutable infrastructure exports

```sh
ows deploy azure plan --tenant TENANT_UUID --subscription SUBSCRIPTION_UUID \
  --location westus2 --name demo --ttl-hours 4 --budget 100 --out ./azure-plan
ows deploy azure bootstrap --plan ./azure-plan --resource-group customer-workload \
  --setup-object-id SETUP_OBJECT_UUID --out ./azure-bootstrap
ows deploy azure infrastructure --plan ./azure-bootstrap \
  --config ./azure-inputs.json --out ./azure-graph
ows deploy validate --plan ./azure-graph
```

`azure-inputs.json` names the exact Kubernetes version, bounded node SKU, public
SSH key, admin groups, control resource group, SHA256-pinned independent cleanup
image, and existing `{keyVaultId, secretName, secretVersion}`. The typed CLI rejects
plaintext passwords, an unpinned image, shared workload/control RG, unsupported
node SKUs and ambiguous versions. See `OWS-AZURE-IMPLEMENTATION.md` for the schema
and source modules. The PostgreSQL password becomes a secure ARM parameter with a
Key Vault reference; the vault must support template deployment and grant the
caller the exact vault `deploy/action` prerequisite.

The export contains native ARM JSON with pinned API versions, avoiding an implicit
Bicep compiler download. Foundation resources, administrator-only role bindings,
AKS, and administrator-only federation are separate templates. Exact generated
IDs/API versions/incarnation fields are listed in `azure-inventory.json`. The
executor action list and installer prerequisites are exported for review.

## 2. Install independent control before paid workload creation

```sh
ows deploy azure lifecycle prepare --plan ./azure-graph --out ./azure-state
ows deploy azure cleanup package --state ./azure-state --out ./azure-control
```

A customer administrator applies the reviewed control ARM resources and fixed,
exact-scope grants. This milestone exports those privileged operations; `ows`
does not silently execute role assignments. Resolve the cleanup managed identity's
actual principal ID after creating it. Review `AZURE-CLEANUP.md` and the binding
metadata rather than posting unresolved ARM expressions as REST role payloads.
Use the `azure-cleanup` target in `deploy/service/Dockerfile` to package the native
runner at `/opt/openworker/ows`. The Docker target was statically reviewed here;
no Azure image was built/pushed. The pinned image must contain this matching native
`ows` command and be pullable
from a registry that survives deletion of the workload ACR.

After a separately authorized bootstrap:

```sh
ows deploy azure cleanup initialize --state ./azure-state \
  --allow-experimental-cloud --accept BUNDLE_DIGEST
ows deploy azure cleanup verify --state ./azure-state
```

The five-minute Container Apps job lives outside AKS. Its OAuth-only private Blob
checkpoint uses create-only initialization and ETag compare-and-swap. TLS is
verified; there are no account-key/SAS fallbacks. The checkpoint is publicly
addressable HTTPS but its contents require explicit OAuth data rights. The job
has one parallel invocation and zero platform retries. CLI workload submission
requires its recent heartbeat and exact pinned job/identity/command/schedule.

## 3. Submit and observe the two workload stages

```sh
ows deploy azure lifecycle apply --state ./azure-state --stage foundation \
  --allow-experimental-cloud --accept BUNDLE_DIGEST
ows deploy azure lifecycle reconcile --state ./azure-state \
  --allow-experimental-cloud --accept BUNDLE_DIGEST
# Administrator applies the reviewed exact resource bindings from the immutable bundle.
ows deploy azure lifecycle apply --state ./azure-state --stage cluster \
  --allow-experimental-cloud --accept BUNDLE_DIGEST
# Administrator applies the exact OIDC federation binding after AKS exposes its issuer.
ows deploy azure lifecycle status --state ./azure-state
```

Every mutation has persisted intent and a fixed request UUID before HTTP. Unknown
outcomes are reported back to the caller/agent so it can read status before
retrying; neither CLI nor scheduler replays the original write. Reconciliation
uses the exact original ARM deployment/correlation, nonce/digest and immutable
resource incarnation receipts. A missing original deployment after a lost PUT
stays parked. Resource names/tags cannot authorize adoption or replacement deletion.

ARM PUT is create-or-update: a read-before-create is not a transactional create-only
guarantee. Reserve the generated IDs for this deployment and prohibit concurrent
external writers during both provisioning and teardown. Provider receipts detect
ordinary replacement/recovery cases; they cannot make Azure APIs atomic against
a privileged competing writer.

Foundation includes private PostgreSQL and artifact Blob, private DNS/endpoints,
VNet and identities. AKS has a private management API, local accounts disabled,
Entra administration, OIDC/workload identity and bounded Linux nodes. ACR Basic
uses authenticated public HTTPS, with admin/anonymous credentials disabled.

## 4. Hand off to the shared Helm/OpenShell installer

Build/upload digest-pinned AMD64 service, runner and optional UI images to the
original ACR. Prepare ordinary shared installer config/values using namespace
`ow-service`, release `ows`, a managed database and the exact Azure artifact
configuration. Then:

```sh
ows deploy azure installation prepare --state ./azure-state \
  --config ./cluster-input.json --out ./azure-install
ows deploy azure installation verify --config ./azure-install/cluster-install.json
ows deploy cluster plan --config ./azure-install/cluster-install.json --out ./install-state
# Review returned INSTALL_DIGEST, then use the existing shared commands:
ows deploy cluster platform --state ./install-state --accept-plan INSTALL_DIGEST \
  --allow-experimental-cloud
ows deploy cluster apply --state ./install-state --accept-plan INSTALL_DIGEST --openshell-only
ows deploy cluster secrets --state ./install-state --accept-plan INSTALL_DIGEST \
  --input /private/azure-service-secrets.json
ows deploy cluster apply --state ./install-state --accept-plan INSTALL_DIGEST
ows deploy cluster status --state ./install-state
```

The private handoff constructs a kubeconfig from the original ARM endpoint/CA,
with an Entra `kubelogin --login azurecli` exec plugin. It discards returned user
credentials and never copies a bearer token/admin certificate into the handoff.
Before each provisioning step, it rechecks control/ownership, unexpired lifetime,
private endpoint, original version/network policy and workload identity. It
retains the existing shared namespace UIDs, chart hashes, fixed OpenShell archive,
authenticated gateway, Helm receipts and unknown-operation safeguards.

Supply database URL/CA, API TLS/private key and optional model keys only through
private secret files/stdin. The URL must use the exact original private PostgreSQL
host, `owsadmin`, `postgres` and a single `sslmode=verify-full`. Secrets are never
part of values, arguments, control or deployment bundles. Obtaining the database
secret from Key Vault is customer/installer-owned here. This is explicit rather
than expanding the ordinary executor into a general secret-reading identity.

Artifact config is native Rust `kind:"azure"` with exact account/container,
tenant/client ID and projected token path. Only API/worker harness containers get
that projection; controller, platform, bootstrap and TLS sidecars do not. The
feature is optional in the artifacts crate and absent from the core runtime.
Actual AKS webhook admission, OIDC, Blob data RBAC/private DNS, disk provisioning,
network-deny probes and OpenShell must be demonstrated in the live trial.

## 5. Teardown and uncertain outcomes

```sh
ows deploy cluster destroy --state ./install-state --deployment-id INSTALL_DEPLOYMENT_ID
ows deploy azure installation close --state ./azure-state --installation-state ./install-state
ows deploy azure lifecycle destroy --state ./azure-state \
  --allow-experimental-cloud --accept BUNDLE_DIGEST
```

Manual infrastructure destroy requires the original Kubernetes installation's
quiet/namespace teardown receipt after any installation mutation. The close check
also reads the same cluster UID and verifies owned service/gateway namespaces are
absent, rather than trusting a caller-supplied boolean. Agreed expiry remains a
hard workload lifetime even when sessions are active.

Each cleanup tick advances at most one exact-resource deletion, in dependency
order, after creation is terminal. Unknown DELETE outcomes are observed without
replay. Replacements, missing incarnation receipts, incomplete creates, read
failures and permission errors stop for inspection. The customer workload RG and
guessed AKS node groups are never deleted; AKS's provider owns its node-group
lifecycle. ARM does not provide a universal atomic conditional DELETE: do not let
other privileged administrators replace these dedicated IDs during teardown.

The job records workload completion then deletes its own exact job once. The
control Blob/account, identity, consumption environment and reviewed grants remain
for audit/recovery and explicit customer removal. This retention can have small
storage costs; workload expiry is not a claim of zero residual billable resources.
