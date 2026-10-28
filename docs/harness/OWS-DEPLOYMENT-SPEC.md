# ows deployment commands

Status: design and incremental implementation. No cloud provisioning is authorized
for this work. The first deliverable is the offline planning/validation contract;
cloud executors follow as separate milestones. AWS identity-only bootstrap export
and offline GCP/Azure plans are now implemented; see the
[cloud identity design](OWS-CLOUD-IDENTITY-DESIGN.md) for scope and prerequisites.

## Product flow

A customer keeps their existing cloud login. OpenWorker generates a reviewable plan
and identity/bootstrap artifacts. The customer performs the one-time authorized
bootstrap; `ows` then uses temporary deployment credentials to coordinate the rest.
No cloud administrator key is embedded in a pod, agent, model context or plan.

Target interface:

```sh
ows deploy aws plan --account 123456789012 --region us-west-2 \
  --name my-service --budget 100 --out ./my-service-plan
ows deploy inspect --plan ./my-service-plan
ows deploy validate --plan ./my-service-plan

# AWS plans can optionally export identity-only bootstrap (see linked design).
# Complete service provisioning remains a later milestone:
ows deploy apply --plan ./my-service-plan --accept-plan PLAN_DIGEST
ows deploy status --plan ./my-service-plan
ows deploy destroy --plan ./my-service-plan --deployment-id EXACT_ID
```

`plan` is local/offline in the first release of the planner. It never reads AWS
credentials, invokes cloud CLIs, creates change sets, creates IAM identities or
claims pricing/quotas were checked. Future online `preflight` is explicitly read-only
and separate. `apply` is the only ordinary command that creates service resources;
bootstrap grants authority separately. `destroy` is an explicit bounded mutation.
All `ows` cloud mutations remain disabled until their adapter is qualified.
The optional exported AWS script is a separate, explicit customer-run IAM bootstrap;
its generation is offline and does not authorize running it.

## Common interface, cloud-specific implementation

| Provider | Infrastructure adapter | Deployment/workload identity | Application installation |
|---|---|---|---|
| AWS | CloudFormation | STS setup role; EKS Pod Identity for workloads | Helm |
| GCP | Infrastructure Manager running Terraform | Federated setup service account; Workload Identity | Helm |
| Azure | Bicep/ARM deployments | Federated deployment identity; managed workload identities | Helm |

These choices do not require developers to learn three interfaces. Each adapter
implements plan rendering, read-only preflight, bootstrap-artifact generation,
reconciliation, apply, status and destroy. Provider-specific input/output objects
remain explicit; do not flatten every cloud permission or remote resource ID into
an AWS-shaped abstraction. A future existing-cluster adapter can skip infrastructure
creation and use the same application phase.

GCP Infrastructure Manager uses Terraform; Google's older Deployment Manager is not
the target. Azure Bicep compiles to ARM templates. AWS stays CloudFormation because
that is the existing qualified experimental reference. No need to introduce
Terraform on AWS merely for a uniform implementation language.
Sources: [GCP Infrastructure Manager](https://docs.cloud.google.com/infrastructure-manager/docs/overview),
[Azure Bicep](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep).

## Plans and local state

A versioned, typed JSON plan records:
- A unique deployment ID, human name, provider, account/project/subscription and region.
- Creation and fixed experiment-expiry times; rerunning a command never renews expiry.
- Resource ownership names/tags, planned topology, capacity ceilings, image digest
  requirements, network exposure and dependency requirements.
- Budget authorization and estimate provenance. Budget is not a spending cap.
  An offline plan has **no verified estimate**. Never turn unknown prices into zero.
- Provider identity/permission requirements and exact generated artifact hashes.

Plans contain no credentials, model keys, DB passwords, kubeconfigs, or private keys.
Use cloud secret references and the customer's existing login. Keep generated cloud
state private as a defense in depth. The first planner refuses existing output paths
and writes restrictive files; it does not overwrite another deployment.

A bundle digest detects accidental edits and pins what the operator approved. It is
not a signature or a defense against an attacker who can rewrite the entire bundle.
Validation also checks semantic consistency, scope, expiry, expected artifacts and
content hashes. Apply must require the reviewed digest plus fresh cloud preflight.
Read-only inspect remains available after expiry for audit/cleanup.

A separate deployment ledger records provider request IDs and immutable remote IDs
before progressing through phases. CloudFormation stack ARN, cluster UID, Helm
release/namespace, database identity and owned bucket/prefix must be retained.
Existing resources are never adopted solely because their names match.

## Two identity categories

1. **Setup authority:** temporarily creates/updates the approved infrastructure and
   installs the applications. Trust is restricted to an explicit caller identity;
   cross-account operation uses an external ID where appropriate. IAM PassRole is
   limited to generated/approved roles and intended services. IAM policies cannot
   silently grant AdministratorAccess, change unrelated roles, or escalate their
   own trust. Bootstrap must be run by someone already entitled to grant authority.
2. **Workload authority:** only components needing a cloud API receive it. For example,
   the artifact API gets S3 access limited to its bucket/prefix. UI and sandbox agent
   pods do not inherit the setup role. Separate workload roles are allowed when
   component privileges differ; “two categories” does not require a shared runtime role.

Generated output eventually includes `bootstrap.sh`, trust documents, complete setup
and workload policies, infrastructure templates and Helm configuration. D1 emits
**permission requirements**, not a grantable setup policy or a deceptively runnable
bootstrap script. Full policies must be derived from actual adapter operations and
verified in D2/D3; exact resources, creation-time tagging and unavoidable wildcard
permissions must be explained in the review summary.

The customer can execute bootstrap locally or through their CI. `ows` verifies the
account and role after assumption and uses an expiry-bounded session; no credential
values are printed. An AWS IAM role and EKS/Kubernetes RBAC are different grants;
the adapter must configure both. OpenShell cluster-wide CRDs/controllers/storage/CNI
setup is an explicit platform phase, not hidden inside namespace-only permissions.

OpenWorker's first service administrator is a separate service credential, created
by the private bootstrap Job and retrieved through the installer's existing cloud/
Kubernetes authority. It never becomes a cloud administrator. Rotate/revoke the
bootstrap credential after enrollment.

## AWS initial deployment scope

Start with a fresh, isolated experiment: EKS, bounded ARM64 capacity, private
PostgreSQL, image registry, optional artifact storage, OpenShell and the current
service/UI Helm chart. No reuse of another application's database or workspace.
Node capacity and harness worker replica counts are separate settings.

The initial offline plan proposes two nodes and at most two worker replicas, private
UI/API access, no automatic node autoscaler, and a 24-hour expiry. These are planning
bounds, not resources created or tested by the planner. RuntimeProfile sandbox
capacity also consumes node capacity; do not equate slots to free sandboxes.

Before apply, resolve/pin supported versions, images, AMIs, storage/network policy,
region availability, quotas, DNS/TLS, secrets, database role and cost estimates.
No public application ingress or broad control-plane CIDR is selected implicitly.
The previous `deploy/aws/` scripts use an account-scoped, fixed experiment name and
an older application chart; they are reference material, not commands to invoke
blindly from the new CLI. Reuse reviewed pieces with parameterized ownership.

## Execution phases and uncertainty

Phases: planned → preflight verified → identities ready → guard ready → infrastructure
ready → platform ready → service ready → qualified. Failed/unknown/cleanup-required
are explicit states; a local ledger cannot substitute for provider observations.

- Install and verify an independent cloud-side expiry/cleanup mechanism before
  starting billable experiment resources. Budgets/alerts are not hard enforcement.
- Persist intent and idempotency/request identity before each mutation. After a lost
  response, inspect the same provider operation/resource; never allocate a fresh
  deployment ID and retry the action blindly.
- Plan expiry prevents new provisioning, not inspection or teardown.
- Tool-action uncertainty retains the existing harness rule: report unknown outcome
  to the model, which can check status/read before deciding whether to retry.
- Apply/status/destroy can resume after a CLI restart. Reconcile observations before
  changing state. Serialize commands for the same deployment with an exclusive lock.
- Never patch/delete objects outside ownership scope; stop on UID/tag/identity mismatch.
- Destroy drains service work, closes sessions and explicitly deletes owned execution
  environments before deleting dependencies. Preserve requested backups and refuse
  teardown if ownership or external-action outcomes require operator review.

## Export and enterprise use

Expose generated templates, policies, values and commands so customers can run them
through their own systems. `ows` coordinates by default; exporting does not remove
ownership checks or permit claiming a deployment succeeded without evidence.
Backend-specific CLIs are adapters, not shell strings assembled from untrusted input.

## Verification gates

Offline tests must run with cloud credentials absent and a trap `aws` executable:
zero invocations during plan/inspect/validate/status and rejected apply/destroy.
Test invalid scopes, malformed input, overwrite/symlink refusal, hash changes,
extra/missing artifacts, expiry, cross-cloud dispatch, and persistent plan identity.
Then simulate cloud responses for mutation loss, retries/reconciliation, wrong
accounts, mismatched resource ownership, partial installs and cleanup failures.
Only a separately authorized cloud run can qualify real IAM behavior, prices,
quotas, cluster provisioning, guard execution and actual billing bounds.

## Schema 2 and current provider plans

New plans use schema 2 and include an unresolved `resource-graph.json`. Schema 1
AWS bundles remain inspectable with their original artifact set and digest.
GCP plans take `--project` and regional `--location`; Azure takes `--tenant`,
`--subscription` and `--location`. Each uses the common name/budget/TTL/output options.
Provider scope validation is syntactic; it does not assert cloud availability.

AWS may receive paired `--operator-arn` and `--execution-role-arn` options to export
identity-only bootstrap. The execution role is customer-provided, not auto-generated
or policy-audited. These extra artifacts are integrity-checked along with the plan.
No identity artifacts are emitted for GCP/Azure yet. Their identity/resource designs
and remaining qualification gates are in [cloud identities](OWS-CLOUD-IDENTITY-DESIGN.md).

## Schema 3: generated AWS infrastructure and identities

`ows deploy aws plan --operator-arn ARN --infrastructure-config FILE ...` now exports
bootstrap, infrastructure and guard templates plus resource-mapped execution policies.
It generates the CloudFormation execution role instead of requiring an imported one;
`--execution-role-arn` therefore conflicts with this option. The explicit input
pins zones, private network range, Kubernetes/node/add-on/PostgreSQL versions and a
versioned guard artifact. It requires an acknowledgement of experiment data deletion.

The output is still offline: no cloud identity, availability, quota, price or guard
behavior is verified. Runtime images, private connectivity, image uploading, secret
handoff and Helm platform/service installation are later phases. See the
[infrastructure export guide](OWS-AWS-INFRASTRUCTURE-EXPORT.md) for exact files and
permission boundaries. Schema 1/2 rendering remains unchanged. Live apply/destroy
remain disabled; a generated guard template does not constitute an installed or
qualified expiry guard.

## Implemented AWS preflight

`ows deploy aws preflight --plan DIR --profile NAME --out NEW_DIR` is the explicit
read-only path. Offline commands still invoke no cloud CLI. Preflight verifies
schema-3 bundle integrity/expiry before observing the pinned AWS target and operator.
It emits a private, bounded-lifetime report tied to the original bundle digest.

Checks cover service-linked role existence, zones/type offering, current standard-
support Kubernetes, recommended node release, ARM64 add-on pins, PostgreSQL options,
conservative quota headroom, seven published fixed-rate components plus a variable
reserve, exact guard object version/bytes and Access Analyzer policy validation.
Unknown/denied/partial/ambiguous observations block rather than claim success. Pin
mismatches include reviewable alternatives without silently changing the plan.

The optional post-bootstrap `--check-setup-role` uses STS with an identity-only session
policy and verifies the exact assumed target. No credential value is persisted.
Pagination, process output and operation/global deadlines are bounded; raw provider
errors are not logged. The full contract and limitations are in the
[preflight guide](OWS-AWS-PREFLIGHT.md). `checksPassed` is observational evidence;
`applyReady` remains false. It is not a reservation, signature, billing cap or proof
of effective provisioning/cleanup permissions.
