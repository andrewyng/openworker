# Deployment CLI implementation plan and ledger

27 October 2026. Branch: `feat/openworker-service-m1`.
Spec: [deployment command contract](OWS-DEPLOYMENT-SPEC.md).
Authorization: implement and test locally; **do not provision AWS resources**.

## Milestones

- D1: Native `ows deploy` command tree; typed provider plan; offline AWS planning;
  private, non-overwriting output bundles; inspect/validate/local status; digest,
  scope and expiry checks; fail-closed unimplemented cloud commands. No AWS calls.
- D2: AWS adapter resource graph and complete IAM/bootstrap generation. Derive
  permissions from actual CloudFormation/EKS/ECR/RDS/S3/secret operations. Customer-
  executed bootstrap script verifies account/caller; policies and templates reviewed
  independently. No placeholder policy advertised as sufficient for deployment.
- D3: Read-only cloud preflight, cost/quotas/version/image resolution, temporary role
  assumption and cloud identity verification. Mock AWS CLI tests first.
- D4: Durable apply/status/destroy state machine, provider-operation reconciliation,
  independent expiry guard and guarded cleanup. Simulated interruption tests.
- D5: Connect shared OpenShell/service/UI Helm installation, namespace permissions,
  secret bootstrap and qualification. Local Kubernetes plus simulated AWS outputs.
- D6: Separately authorized AWS experiment; actual IAM/cost/guard checks. Then define
  GCP/Azure adapter implementations against the same conformance tests.

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
