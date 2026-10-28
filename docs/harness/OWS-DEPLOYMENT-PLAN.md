# Deployment CLI implementation plan and ledger

27 October 2026. Branch: `feat/openworker-service-m1`.
Spec: [deployment command contract](OWS-DEPLOYMENT-SPEC.md).
Authorization: implement and test locally; **do not provision AWS resources**.

## Milestones

- D1: Native `ows deploy` command tree; typed provider plan; offline AWS planning;
  private, non-overwriting output bundles; inspect/validate/local status; digest,
  scope and expiry checks; fail-closed unimplemented cloud commands. No AWS calls.
- D2a: Offline GCP/Azure plans and cloud identity design; AWS identity-only bootstrap
  export for a customer-provided CloudFormation execution role. Local policy/script
  tests. No cloud mutations.
- D2b: Complete AWS infrastructure template graph and automatic execution-policy
  generation from actual CloudFormation/EKS/ECR/RDS/S3/secret/guard operations.
  Review creation-time and wildcard grants. No placeholder policy advertised as
  sufficient for full deployment; D2a does not complete D2b.
- D3: Read-only cloud preflight, cost/quotas/version/image resolution, temporary role
  assumption and cloud identity verification. Mock AWS CLI tests first.
- D4: Durable apply/status/destroy state machine, provider-operation reconciliation,
  independent expiry guard and guarded cleanup. Simulated interruption tests.
- D5: Connect shared OpenShell/service/UI Helm installation, namespace permissions,
  secret bootstrap and qualification. Local Kubernetes plus simulated AWS outputs.
- D6: Separately authorized AWS experiment; actual IAM/cost/guard checks. Then qualify
  GCP/Azure bootstrap/execution adapters against the same lifecycle contract.

## D1 order

1. Record design/command semantics and unsupported boundaries.
2. Add deploy dispatch before existing service authentication/settings loading.
3. Implement offline planning and validated artifact bundles without subprocesses.
4. Verify CLI behavior with a trap cloud executable and no credentials.
5. Exercise a sample plan locally and update this ledger with exact results.

## Progress

- Spec written. AWS uses CloudFormation; later GCP uses Infrastructure Manager /
  Terraform and Azure uses Bicep/ARM. All share Helm for application installation.
- Existing service CLI and older account-scoped AWS reference reviewed. The new
  command path must not invoke the old fixed-name experimental launch scripts.
- D1 implementation started. No cloud calls or provisioning performed in this task.

### D1 complete locally

Implemented native Rust deployment dispatch before service config/authentication.
AWS offline planning emits a typed, immutable review bundle: plan, human review,
permission requirements (explicitly not IAM policy) and SHA-256 manifest. Separate
provider target types reserve GCP/Azure without accepting AWS-shaped target inputs
for those clouds. Inspect, offline validation and local status work; unimplemented
apply/destroy/provider paths fail closed without cloud calls.

Tests passed: 7 unit tests (including 3 existing CLI/settings checks) and 3 new
binary integration tests. Coverage includes malformed scope, false budget/cost
claims, unsupported topology, expiry, manifest/content tampering, symlinks, private
POSIX file modes, output overwrite refusal, distinct IDs, and missing service
configuration. A PATH containing trap aws/helm/kubectl/gcloud/az/terraform/docker
executables confirmed no invocations; test subprocess environments had no cloud
credentials. Build/test used offline Cargo dependencies. Formatting/diff checks pass.

Exercised plan/inspect/validate/status for account 504150922218, us-west-2, $100
at local ignored `.ow-service-deploy-preview/`. The plan reports `planned`,
`cloudObservation: not_checked`, `estimate: null`, `hardCap: false`, and
`applyReady: false`. No AWS calls, IAM changes or provisioning in this work.

Remaining next: D2 complete AWS permission/bootstrap artifacts and resource graph,
then D3 read-only preflight and D4 cloud execution/reconciliation. The implemented
planner must not be described as a deployable AWS installer. The sample plan has
an expiry and is not authorization to launch resources later without review.

### D2a complete locally — 28 October 2026

Added [cloud identity design](OWS-CLOUD-IDENTITY-DESIGN.md) covering AWS,
GCP Infrastructure Manager/Terraform and Azure Bicep/ARM, with setup/executor/workload
separation, private-network prerequisites, operation reconciliation and cleanup.

Native GCP/Azure offline plan commands now validate provider-specific target fields,
emit unresolved dependency/resource graphs and support the same inspect/validate/
local-status contract. New schema-2 plans preserve schema-1 AWS inspection. GCP and
Azure propose AMD64 nodes; AWS retains ARM64. Machine/image availability remains
unverified; no runnable GCP/Azure infrastructure or bootstrap is claimed.

AWS paired operator/execution-role arguments export a CloudFormation identity-only
template, setup/workload policies, review and explicit customer-run bootstrap script.
The supplied execution role is NOT generated or policy-audited. Script checks bundle,
expiry, account, caller and existing execution-role ARN, creates only two named roles,
uses a stable request token, disables mutation retries, and refuses to update/adopt
an existing stack. The role policies limit stack names, PassRole and S3 object prefix;
Pod Identity trust pins cluster/namespace/service account. Post-expiry teardown stays
possible. Bootstrap itself grants no EKS Kubernetes access or image/secret authority.

Verification: 7 unit tests and 7 binary integration tests pass. Tests cover all three
providers with trap cloud executables and no cloud credentials; invalid cloud scopes,
identity paths, root/wildcards/cross-account identities, paired arguments, tampering
of each exported artifact, dependency ordering, policy constraints, script syntax,
wrong account/caller/execution role, duplicate stack, failed wait, lost create response,
exact IAM user and assumed-role callers. Script execution uses a fake AWS binary only.
No AWS/GCP/Azure API, IAM mutation or provisioning occurred. No new dependencies.

D2b remains next: resolved AWS infrastructure, complete generated execution permissions
and qualified bootstrap/platform scope, then read-only preflight and cloud lifecycle
implementation. Live deployment remains disabled on every provider. The identity-only
export is an optional bring-your-own-role path, not the finished one-command installer.

Manual offline smoke: generated AWS (10 artifacts), GCP (5) and Azure (5) samples
under ignored `.ow-service-deploy-d2-wbxy9jqe/`; inspected, validated and read local
status for each. Every result has `applyReady: false`. The original D1 sample remains
inspectable with its unchanged bundle digest. Formatting and diff checks pass.
