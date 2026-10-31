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
