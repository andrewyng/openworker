# Azure service deployment: sequence and progress

Branch: `feat/azure-service-lifecycle-20261031`; base: `f13de9b9`.
Authorization: implementation and local deterministic qualification only. No Azure
login, IAM/resource changes, paid infrastructure, subscription or budget is authorized.
The parent thread is discussing team sandbox delegation separately; this branch
does not change team behavior.

## Architecture

Use Azure-specific Bicep/ARM control for infrastructure, with the existing shared
OpenShell/service Helm installation and management API. Keep `RuntimeProfile`,
session storage, observability, service tokens, agent releases, ToolRunner and the
unknown-outcome/no-automatic-replay contract identical across clouds.

Separate authorities:

- Customer bootstrap administrator: creates reviewed, exact-scope role grants.
- Setup principal: an existing Entra user or service principal, explicitly supplied
  by object ID. Interactive users log in normally; CI/compute supplies federation
  or managed-identity login. An Azure CLI user does not automatically impersonate
  a newly created managed identity.
- Resolved resource executor: ordinary deployment needs explicit Azure resource
  actions. ARM deployment-metadata access alone cannot create AKS or a database.
- Workload principal: AKS OIDC issuer + Kubernetes service-account subject +
  audience bound to a dedicated managed identity; Blob data access scoped to the
  intended container. Keep installer rights and agent credentials out of pods.
- Independent cleanup principal: operates outside AKS, survives cluster/node loss,
  and cleans only journaled owned IDs. Resource-group names/tags are not authority.

The first bootstrap accepts an existing dedicated customer-owned resource group.
It does not create, adopt or assume deletion ownership of that group. A later
explicit group-creation operation can remove that prerequisite after ownership,
recovery and cleanup are qualified.

## Milestones and implementation order

1. **A1: identity export and read-only bootstrap preflight.** Extend `ows deploy
   azure` without cloud mutations. Export deterministic role/assignment IDs, exact
   recipient/scope/action JSON, a separately customer-run script, and review text.
   Check public Azure endpoints, tenant/subscription, existing RG, exact role and
   assignment. Observe provider registrations but never enable them. Preserve
   `applyReady:false`; do not claim effective IAM or deploy readiness.
2. **A2: resolved Bicep/ARM graph and permission export.** Pin supported AKS/node
   architecture, network/VNet/subnet, PostgreSQL, ACR, artifact storage and secret
   references. Derive resource-specific executor actions from actual operations.
   Verify the chosen AKS node pools meet OpenShell's Linux/runtime requirements;
   a generic AKS cluster does not establish sandbox compatibility by itself.
   Plan API management access, quota/cost and policy/lock checks. Scope role
   assignments to explicit resources/principals; no general RBAC write privilege.
3. **A3: lifecycle journal and independent expiry cleanup.** Persist deployment,
   operation and exact resource IDs before effects. Distinguish accepted, unknown,
   observed-complete and failed states. Observe an uncertain original operation;
   do not resubmit it. Cleanup must survive loss of AKS and the invoking laptop,
   and avoid deleting a replacement or unrelated RG. Test partial provisioning,
   response loss, interrupted teardown and stale ownership.
4. **A4: installation wiring and local qualification.** Connect shared OpenShell
   and service Helm flows, workload federation, PostgreSQL/artifact credentials,
   images, networking, service bootstrap and UI. Namespace permissions alone cannot
   install cluster-wide OpenShell components. Keep artifact credentials separate
   from setup credentials. Validate with local fixtures and recovery cases.
   The current artifact implementations are filesystem/S3; Azure Blob needs a
   native adapter or an explicitly qualified compatibility layer. A role grant
   or Helm values alone does not provide that backend.
5. **A5: separately authorized live Azure trial.** Obtain tenant/subscription,
   approved login/bootstrap identity, region, network path and total experiment
   budget. Qualify actual IAM, prices/quotas, independent expiry and clean teardown,
   then submit real agent/team runs. Use the same acceptance suite as GCP.

## A1 contract

`ows deploy azure bootstrap --plan PLAN --resource-group RG --setup-object-id UUID
--setup-principal-type service-principal|user --out NEW_BUNDLE` is offline. Its
custom role allows only ARM deployment metadata and required scope/RBAC reads.
It cannot provision AKS/resources, write role assignments, export keys or grant
itself additional rights. The chosen principal must already exist.

The exported `azure-bootstrap.sh --apply-identities NEW_PRIVATE_JOURNAL` is an
explicit customer-run mutation, never invoked by `ows`. It verifies the selected
public cloud/tenant/subscription/RG, refuses existing generated role/assignment IDs,
and writes private receipts. It never creates secrets, switches subscriptions,
enables providers, generates an Entra application, or automatically retries a
mutation. Azure CLI/SDK transport behavior is distinct from script-level retries.

`ows deploy azure preflight --plan BOOTSTRAP --out NEW_REPORT` issues allowlisted,
bounded, timed read commands only. Reports contain sanitized checks, not raw
stderr, tokens, account emails or provider payloads. Exact grant verification is
not proof of all effective permissions, deny assignments, Azure Policy, quota,
spend, a working federation login, or infrastructure installation readiness.
Optional provider-registration observations do not widen the bootstrap guarantee.

## Progress ledger

- Inspected existing AWS/GCP deployment adapters and Azure plan-only scaffolding.
  No Azure lifecycle or identity/preflight execution existed at the base commit.
- Consulted Microsoft primary documentation for custom roles, scoped assignments,
  management API shapes and AKS Workload ID. Azure CLI uses create-or-update for
  custom-role REST operations; fixed IDs, explicit existing-ID refusal and private
  journals make uncertain results inspectable. This is not a transactional cloud
  lock or proof that no concurrent administrator can change a role.
- Implementing A1 export and read-only observation with fake-`az` integration tests.
  Test results and remaining qualification gates will be recorded before commit.

### A1 locally qualified — 31 October 2026

- Added native `ows deploy azure bootstrap` and `preflight` dispatch. Optional
  Azure identity metadata is validated and included in regenerated plan manifests;
  existing AWS/GCP plans keep their original encoding when the field is absent.
- Export is offline and creates private immutable review bundles. Fixed role and
  assignment IDs bind one existing recipient to one customer-owned RG. The setup
  role has 11 explicit metadata/read actions, no wildcard/resource-creation/data/
  role-assignment-write permissions. No principal or credential is created.
- Read-only preflight validates public Azure cloud endpoints, tenant/subscription,
  existing RG, exact custom-role actions/scope and exact unconditional assignment.
  It observes eight provider registrations without enabling them. Reports distinguish
  exact-grant checks from effective IAM, caller-object proof and deploy readiness;
  `applyReady` always remains false. Calls/output/deadlines are bounded and raw
  stdout/stderr errors are not copied into reports.
- **81 checks pass:** 49 CLI unit/local-HTTP tests, eight Azure integration tests,
  seven shared AWS/GCP/Azure deployment regressions and 17 GCP integration tests.
  Fixtures use isolated fake executables and local HTTP only. Initial unrestricted
  test invocation could not create loopback fixture listeners; rerunning with local
  listener permission passed. A bootstrap shell quoting error caught by the positive
  script test was corrected before this qualification.
- Formatting and diff checks pass. Normal scoped Clippy completes with six
  **pre-existing** warnings in unchanged `deployment_cluster_platform.rs`,
  `deployment_local.rs` and `main.rs`; strict `-D warnings` fails on those. The
  all-target check additionally finds a pre-existing suspicious-open-options lint
  in unchanged `tests/local_installer.rs`. New Azure source/tests emit no Clippy
  diagnostics. These unrelated repairs are not included or hidden by allow flags.
- No real Azure CLI, login, token, IAM or resource operation was executed. No cloud
  credentials or generated fixtures are committed. Shared target cache was reused;
  no second large build cache, cluster or container was created. Disk remains ~55 GiB.

Next: A2 resolved graph/permissions, then lifecycle/independent cleanup. Real Azure
IAM, federation, AKS/OpenShell node compatibility, Blob integration, prices/quotas,
installation and cloud teardown remain unqualified. A1 is not deploy-ready access.

## Primary references

- [Azure custom roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles)
- [CLI role assignments](https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-cli)
- [Role definitions API](https://learn.microsoft.com/en-us/rest/api/authorization/role-definitions/get?view=rest-authorization-2022-04-01)
- [Resource-management actions](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/management-and-governance#microsoftresources)
- [AKS Workload ID](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview)
- [Azure CLI role implementation](https://github.com/Azure/azure-cli/blob/dev/src/azure-cli/azure/cli/command_modules/role/custom.py)

### A2 design and sequence — 31 October 2026

Use pinned native ARM JSON, avoiding an implicit Bicep installer or compiler
runtime. Export four distinct stages: foundation, administrator-only bindings,
AKS, and administrator-only OIDC federation. The ordinary executor cannot write
Azure RBAC. Explicit identity `assign/action` grants and the existing Key Vault's
`deploy/action` grant are separate prerequisites, not ambient subscription roles.

The workload group and independent control group must already exist and be
dedicated. Both remain customer owned. Control is outside AKS; its pinned runner
image must also survive deletion of the workload ACR. The graph fixes two bounded
AMD64 Ubuntu nodes, private management API/PostgreSQL/Blob, authenticated public
ACR Basic, and one version-pinned existing Key Vault password reference. Network
ranges are explicit fixed reference values; live deployment must reject customer
route overlap. No key, password, SAS or model credential is embedded in bundles.

Resource creation and deletion will use fixed deployment request identities,
persist intent before effects, and read the original operation after response
loss. A matching name/tag is insufficient ownership. Resource incarnation fields
are recorded and checked before cleanup; missing provider receipts fail closed.
Never delete the customer resource group or guessed AKS `MC_*` groups. AKS's
provider owns its generated infrastructure lifecycle.

### A2 locally qualified — 31 October 2026

- Added `ows deploy azure infrastructure` offline input validation and deterministic
  ARM export, including exact resource inventory, private access, narrow executor
  action list, separate administrator bindings and exact-subject federation.
- Ten Azure CLI integration tests pass (eight A1 plus graph integrity/safety tests).
  The tests assert private endpoints, disabled account keys/local AKS accounts,
  bounded nodes, pinned secret references, no wildcard/RBAC-write executor grants,
  distinct cleanup scope, malformed-input rejection and zero cloud invocations.
- ARM JSON shapes use pinned Microsoft schemas. No Azure-side template validation,
  quota/version availability, price, networking or OpenShell-on-AKS qualification
  is claimed. The immutable bundle continues reporting `applyReady:false`.

### A3 implementation — durable control and independent expiry

Native bounded ARM/Blob HTTP uses trusted Azure CLI short-lived access tokens in
memory, or the Container Apps managed-identity endpoint for its exact client ID.
TLS/endpoints are pinned, redirects/environment proxies/retries are disabled,
responses are bounded, token-command output is bounded/timed and raw credential
or Azure error bodies never enter reports. Fixed request UUIDs and exact ARM
operation URLs/correlation receipts are journaled. A Blob ETag CAS persists intent
before each effect; a competing writer cannot pass an old checkpoint.

`lifecycle prepare` and `cleanup package` run offline. A separately reviewed
bootstrap administrator creates control resources/grants from the exports.
`cleanup initialize` is create-only, then the independent scheduled job provides
a heartbeat. `lifecycle apply`, `reconcile`, `status` and `destroy` use the shared
control Blob. Creation requires an exact bundle-digest acknowledgement, an
explicit experimental flag, unexpired plan and recent independent heartbeat.
The two ARM stages keep administrator-only RBAC/federation outside the executor.

An uncertain PUT stays unknown even when its deployment is absent. Reconciliation
binds the original nonce/digest/correlation and records immutable resource creation
receipts. Cleanup compares those receipts before each exact-ID deletion. A lost
DELETE is observed after restart; it is never automatically reissued. Unknown
creates, incomplete ownership and replacements pause teardown for human review.

The external scheduled job advances at most one workload delete each tick, after
creation becomes terminal. It then records completion and deletes its own exact
job once. The control account/blob, identity, consumption environment and reviewed
roles remain for customer audit/removal; this retention is explicit and may have
small storage costs. No customer RG or guessed AKS node RG is ever deleted. Azure
lacks a universal conditional DELETE across resource providers: a concurrently
privileged administrator can race read/delete, so dedicated IDs must not be
modified externally during teardown. No atomic replacement immunity is claimed.

The live path remains experimental and unqualified: exact IAM/deny policies,
provider API validation, scheduling, managed-identity endpoints, private routes,
quota/cost, AKS/OpenShell and whole-subscription cleanup need the authorized live
trial. The immutable plan reports `applyReady:false`; heartbeat verification is
not an effective-permissions or production-readiness certificate.

### A3 locally qualified — 31 October 2026

- Ten new native unit/loopback tests pass: lost create/delete after provider effect,
  restart reconciliation, CAS conflict preventing mutation, ownership replacement,
  expired/missing-guard creation refusal, unknown-create 404 remaining parked,
  independent expiry plus self-stop, control graph permissions, native HTTP
  create-only/ETag headers, response loss with exactly one transport attempt and
  hostile redirect refusal. Initial loopback bind was sandbox-blocked; rerun with
  local listener permission passed, with no cloud access.
- Eleven Azure CLI tests pass, including private offline state/control packaging,
  tamper detection and refused unconfirmed remote actions without invoking `az`.
- Local qualification proves protocol/state-machine behavior; it does not establish
  actual Azure IAM/API/provider success or automatic cleanup under real outages.

### A4 implementation — shared installer and native artifacts

- Added the optional native Rust Azure Blob artifact backend and server config.
  It uses explicit AKS workload identity, fixed HTTPS Azure authority/storage
  endpoints and no ambient shared-key/SAS/metadata fallback. Immutable object
  writes preserve hash/size/cancellation/scope rules and refuse automatic replay.
- Chart projects a rotating exact-audience token only into API/worker harness
  containers and attaches only their dedicated service account. TLS sidecars,
  controller, platform and bootstrap receive no artifact token. Filesystem/S3
  behavior remains unchanged. Azure is optional in the artifacts crate; an
  isolated no-default-features build passes. Added the crate's explicit Tokio
  macros feature, fixing a pre-existing reliance on unified dependencies for
  `select!` in isolated builds.
- Added Azure installation prepare/verify/close and a binding in the existing
  shared Helm/OpenShell installer. Private kubeconfig is rebuilt from original
  ARM endpoint/CA using Entra Azure CLI login, without copying credentials.
  Values bind original ACR, deployment, container and artifact identity. Provisioning
  checks expiry, external cleanup and original immutable cluster ownership.
- Database keys/TLS/model secrets remain private-file/stdin inputs. The URL must
  use original PostgreSQL host/database/user and verify-full TLS. This milestone
  deliberately leaves fetching the version-pinned Key Vault password to the
  customer's approved installer, avoiding a general secret-read grant.
- Installation mutation intent is persisted in independent control. Manual cloud
  deletion requires original shared-installer teardown plus live matching cluster
  UID and owned namespace absence. Expiry remains the explicitly agreed hard
  workload lifetime. All existing Helm unknown-operation/UID/chart hash safeguards
  and pinned OpenShell 0.1.3 authenticated gateway checks are reused.
- The actual live gates remain: effective IAM/Entra membership, regional version/
  SKU/quota/cost, control scheduling and retention removal, AKS private routes/CA,
  CSI, OIDC webhook admission, Blob data permissions/DNS, image pushes, Agent Sandbox,
  network enforcement, OpenShell kernel/runtime compatibility and real agent runs.
  None is inferred from a fixture, role file or successful Helm render.

## Qualification map

| Group | What the fixtures check | What this establishes |
|---|---|---|
| Immutable export | Scope/recipient IDs, pins, bounded topology, private settings, manifest hashes, malformed input and no CLI invocation | Review artifacts are deterministic and fail closed locally |
| Identity observation | Exact role/assignment, wrong cloud/tenant/principal/scope, deadlines/output and redaction | Read-only bootstrap checks do not overstate readiness or mutate IAM |
| Durable effects | Intent before HTTP, ETag contention, committed-effect/lost-response, restart, 404 uncertainty and correlation/nonce receipts | Connection failures do not replay an unknown infrastructure action |
| Independent expiry | Controller/control outside AKS, fixed schedule/image/identity, stale heartbeat, exact resource incarnations, one-shot deletion and self-stop | Cleanup logic remains independent and refuses replacement/ambiguous ownership in fixtures |
| Installation | Original private endpoint/CA, Entra plugin, exact registry/artifact identity, database TLS, CSI checks and shared installer regressions | Azure follows the existing installer boundary without weakening kind/EKS behavior |
| Blob data plane | Immutable chunks/manifests, hashes/scopes/limits/cancellation, redirects, committed-effect/503 and token rotation | Native Azure adapter preserves the artifact-store contract |
| Chart roles | Real Helm rendering of projections, service accounts, rejected overrides, token-free sidecar/controller/platform/bootstrap, unchanged S3/filesystem | Rendered configuration keeps workload credentials in the intended containers |

Tests use in-memory provider models, isolated fake executables and loopback HTTP.
They intentionally do not demonstrate real Azure control/data planes or kernel
sandboxing. The live trial must separately validate provisioning/teardown, the
original and replacement cases against actual APIs, private network enforcement,
external expiry during AKS/laptop loss, and real long-running agent/team behavior.

### Additional primary references

- [Pinned AKS ARM schema](https://learn.microsoft.com/en-us/azure/templates/microsoft.containerservice/2024-10-01/managedclusters)
- [Pinned PostgreSQL ARM schema](https://learn.microsoft.com/en-us/azure/templates/microsoft.dbforpostgresql/2024-08-01/flexibleservers)
- [Pinned Container Apps job schema](https://learn.microsoft.com/en-us/azure/templates/microsoft.app/2024-03-01/jobs)
- [Container Apps managed identity endpoint](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity)
- [ARM deployment correlation/history](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deployment-history)
- [Blob conditional writes](https://learn.microsoft.com/en-us/rest/api/storageservices/put-blob)
- [Entra kubelogin](https://learn.microsoft.com/en-us/azure/aks/kubelogin-authentication)
- [AKS application IDs](https://azure.github.io/kubelogin/concepts/aks.html)
- [Azure Disk CSI](https://learn.microsoft.com/en-us/azure/aks/create-volume-azure-disk)
- [Workload identity service-account annotations](https://azure.github.io/azure-workload-identity/docs/topics/service-account-labels-and-annotations.html)

### A4 locally qualified — 31 October 2026

- **133 unique automated checks pass:** 64 CLI unit/loopback checks, 12 Azure CLI
  fixtures, seven shared deployment checks, 17 GCP regressions, 12 shared installer
  regressions, 11 artifact checks, nine real Helm-render checks and one server
  Azure configuration check. Two pre-existing artifact entrypoints remain opt-in:
  the child probe (invoked by its synthetic-endpoint parent) and isolated live S3.
- The external job now captures its own immutable provider creation timestamp on
  its first authenticated heartbeat and refuses same-name replacements/config
  drift before provisioning or self-stop. That replacement assertion was rerun
  after the full matrix. Job command uses the image's actual absolute binary path.
- Offline negative tests also reject nested output mutation and bad installation
  configuration before obtaining credentials. Shared cloud platform opt-in covers
  Azure, while ordinary kind/EKS serialized inputs and safeguards remain intact.
- Azure artifact **strict library Clippy passes**. CLI Clippy completes with only
  the six documented baseline warnings on existing expressions; no new Azure
  diagnostic or suppression. Formatting and diff checks pass. No all-target strict
  CLI success is claimed because the earlier baseline lints remain.
- Used one temporary task-owned debug=0/incremental-off target while the parent's
  primary build cache was busy. One build job, 6 GiB enforced cap, final size ~1.6 GiB;
  removed that exact marked target after verification. No global cache prune or
  cluster/container change. The Docker `azure-cleanup` target is exported and
  statically reviewed; no Azure image build/push is claimed.
- No real Azure/GCP/AWS account, token, IAM or resource operation occurred in this
  batch; no model keys were read/printed. No cloud credential or generated private
  control/journal is committed. [Sanitized evidence](evidence/azure-offline-20261031.json).

A1–A4 are implemented with offline qualification. **A5 live Azure remains unqualified
and requires separate account/budget authorization.** Read the service reference's
explicit control retention, dedicated/exclusive resource scope and non-transactional
ARM create/delete caveats before any trial. This branch was committed locally;
canonical merge/push remains with the parent reviewer.
