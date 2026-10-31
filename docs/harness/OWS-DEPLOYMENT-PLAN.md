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

GCP follow-up (October 30): the user approved a separate **$100 total** experiment.
Isolated project/budget setup and the implementation sequence are recorded in the
[GCP trial plan and ledger](OWS-GCP-TRIAL-PLAN.md). Identity-only bootstrap export
and read-only bootstrap preflight are now implemented and qualified. GCP
production infrastructure creation remains gated; bootstrap readiness
does not qualify a paid deployment. Local lifecycle recovery and independent
cleanup packaging now pass qualification in the [lifecycle guide](OWS-GCP-LIFECYCLE.md).
Source/control installation, effective IAM and live cleanup still need qualification. See the [GCP command guide](OWS-GCP-BOOTSTRAP.md).
G2a adds a pinned Terraform export and local lifecycle prepare/status commands;
see the [infrastructure guide](OWS-GCP-INFRASTRUCTURE.md) for the remaining gates.

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

### D2b complete as an offline export — 28 October 2026

Added `--infrastructure-config` with a strict, versioned input schema and explicit
experiment-data-deletion acknowledgement. With an operator ARN, this generates the
execution role rather than importing one. Existing identity-only exports and schema
1/2 bundles retain their original artifact contents/digests.

Generated 21-artifact review bundle:
- Bootstrap template: seven roles and five scoped execution managed policies.
- Infrastructure template: 36 resources covering a dedicated VPC, single NAT, private
  worker subnets, private EKS endpoint, pinned node/add-on versions, private TLS RDS,
  generated named DB secret, artifact S3 bucket and three ECR repositories.
- Independent guard template: four resources referencing an immutable S3 code version.
- Per-domain execution policies, workload/setup/cleanup policies, resource permission
  coverage, wildcard explanations, guard qualification contract and pinned inputs.

The infrastructure executor has no IAM identity/policy mutation permissions.
Bootstrap owns platform roles; PassRole is restricted to exact roles/services.
Cleanup can delete the owned infra stack and scoped artifacts/volumes, not provision
resources or alter bootstrap/guard identities. EC2 creation and subsequent mutation
use request/ownership tags. Tag deletion requires a nonempty key list excluding the
ownership tag. EKS CreateCluster's required Resource * is explicitly documented;
IAM does not enforce the experiment's cost or node-count ceiling.

Validation completed:
- 19 Rust CLI tests pass (7 unit, 7 deployment, 5 infrastructure tests).
- New tests cover dependency/reference graphs, private endpoints/subnets, explicit
  destructive retention, exact identity references, policy sizes and authority
  boundaries, missing/unpinned/unsafe inputs, every artifact's integrity, no cloud
  invocations, and generated-role bootstrap using a fake AWS executable only.
- `cfn-lint 1.58.0` passes all three templates for us-west-2. Only W3010 is waived:
  availability zones are deliberately pinned per target account/region. The linter
  caught an invalid S3 permission action during development; it was removed.
- Manual plan/inspect/validate/status smoke succeeded; applyReady remains false.
  Historical D1 and D2a bundle digests remain unchanged on inspection.
- Formatting and diff checks pass. No runtime dependency was added. cfn-lint was
  downloaded to a /tmp tool cache; no AWS credentials/API/provisioning were used.

Final local review bundle:
`.ow-service-deploy-d2b-verified-8lh3e0ko/plan/` (ignored, synthetic target/fixtures).
Guide: [AWS infrastructure export](OWS-AWS-INFRASTRUCTURE-EXPORT.md).

Next is D3 read-only preflight: identity, service-linked roles, version/image support,
quota, prices and guard artifact digest. D4 still owns guard packaging/behavior and
cloud lifecycle reconciliation. D5 still owns connectivity to the cluster management API (private access),
image upload, exact secret handoff and OpenShell/service Helm qualification. Live
apply remains disabled. The example versions/artifact identifiers are test fixtures,
not a supported-version catalog or an uploaded/qualified guard. A generated template
is not an installed guard, a cloud IAM qualification, or a hard $100 spending cap.

### D3 implemented and read-only AWS qualified — 28 October 2026

Added `ows deploy aws preflight --plan DIR --profile NAME --out NEW_DIR` plus an
optional post-bootstrap setup-role assumption check. Report integrity binds to the
reviewed deployment/bundle digest and fixed expiry. Plan/inspect/validate/status
remain offline. Preflight uses an explicit read-only API allowlist, no shell,
bounded subprocess output/time/pagination and fixed sanitized errors. Credentials
and raw provider output are not persisted; optional STS credential buffers are
cleared. Runtime AWS CLI credentials are handled by the customer's configured login.

Checks cover identity, service-linked roles, zone/type offerings, standard-support
Kubernetes, current recommended ARM64 AMI/node release, add-on compatibility, RDS
version/orderability, conservative quota headroom, published fixed rates plus reserve,
pinned guard artifact bytes and Access Analyzer validation of all eight policy docs.
Quota usage includes unused active and future capacity reservations. Stale pins
produce reviewable alternatives, never silent substitutions. Guard bytes use exact
owner/version/range, size limits and SHA-256; the scratch file is removed. A passing
report expires within 15 minutes and never enables apply.

25 tests pass: 7 unit + 7 deployment + 5 infrastructure + 6 preflight integration.
Preflight tests include successful optional temporary-role identity verification,
wrong identities/denied calls, malformed/oversized output, secret redaction, timeouts,
RDS/EC2 pagination and repeated-token rejection, quota exhaustion/unused/future
reservations, unsupported versions/architecture, ambiguous/missing/zero/NaN prices,
budget overflow retaining the estimate, owner-pinned guard requests and region/size/
digest errors, policy
security warnings and immutable bundle/output boundaries. A fake AWS executable
asserts every requested operation belongs to the read-only contract. No dependency
or Python runtime was added; Python is used only for the test fake executable.

Also exercised the actual command against ocw-demos with a temporary STS session
restricted to metadata, policy validation and the fixture artifact. This was an
explicit read-only qualification: no role/policy/cluster/resource was created or
modified. The initial run correctly blocked stale fixture pins. The real Price List
catalog exposed unused-reservation and regional-NAT alternatives plus EKS's `Hours`
unit label; selectors/unit normalization were corrected and regression-covered.

The final live read-only run used observed compatible pins in a fresh local plan:
all identity/prerequisite/version/zone/quota/pricing checks passed, and Access Analyzer
returned zero findings for all eight generated policy documents. 45 AWS CLI
invocations. Fixed 24-hour estimate $7.45, plus $25 usage reserve = $32.45 against
$100 authorization, not a hard cap. The only required blocked check was the expected
unpublished fixture guard package. Setup-role assumption was not requested live
because no identity bootstrap was authorized/performed; its behavior is tested locally.

Sanitized evidence: [read-only AWS result](evidence/aws-readonly-preflight-20261028.json).
Full private reports remain under ignored `.ow-service-d3-live-*/`; final report is
`.ow-service-d3-live-U2ZZZU/report/preflight.json`. No credential values were saved.
Guide: [AWS preflight](OWS-AWS-PREFLIGHT.md). Formatting and diff checks pass.

Next: D4 guard packaging/behavior and durable apply/status/destroy reconciliation,
starting with local/fake-provider failure tests. No AWS provisioning is authorized.
D5 still owns image/secret/Helm wiring and qualification of the cluster management API (private access). A valid
policy document is not proof of effective IAM permissions or creation/rollback
qualification, and a verified guard hash is not proof of safe cleanup behavior.

### Local installer and guard behavior qualified — 28 October 2026

Completed the local D4/D5 subset authorized for this task. Added native `ows deploy
local plan/apply/status/destroy` and offline `ows deploy guard package`. Local apply
supports an installer-owned pinned OpenShell release or a read-only existing-gateway
dependency. Cluster/namespace UIDs, reviewed input hashes, intent-before-effect
journaling, OS locks, original Helm receipts and explicit no-effect recovery protect
resumption. Unknown mutation outcomes are not blindly replayed. Safe fixture teardown
refuses active work, freezes API intake, removes Helm releases before namespaces, and
uses namespace UID preconditions. Existing gateways are never upgraded or removed.

The independent expiry guard now has a deterministic ZIP packager and injected
provider tests. Its own scoped DynamoDB CAS record stores the original infra stack
ARN, stable delete token and volume deletion intents across invocations. Fixed expiry,
ownership, complete bounded inventories, foreign/versioned buckets, partial cleanup,
replacement stacks and unknown outcomes are checked before proceeding. The guard
does not remove itself or its bootstrap. Health is read-only; pre-expiry binding of
stack identity is permitted, destructive cleanup is not.

32 Rust CLI tests and nine guard tests pass. Independently verified deterministic
ZIP/CRC/source/hash; all three current CloudFormation templates pass cfn-lint 1.58.0
with only intentional W3010 waived. Exact table/key IAM scope and dependency edges
are regression-covered. Prior development schema-3 bundles must be regenerated and
reviewed for the new guard table/policies; schema-1/2 identity-only shapes remain stable.

Real kind qualification passed for both gateway modes, including authenticated worker
readiness, scoped publication/activation, service submission, OpenShell sandbox file
write and shell read, installer process restart without revision changes, teardown
refusal during active work, session close and explicit environment deletion. A real
Calico probe passed ingress/egress deny then restored allow. Test namespaces, PVCs
and owned gateway cluster RBAC are removed. The baseline gateway revision/receipt,
all baseline namespace UIDs and three suspended sandbox UIDs are unchanged.

Reused cached Linux ARM64 images and the existing kind cluster; about 202 GiB free.
The model was deterministic; tools and execution environment were real. The native
CLI requires Helm/kubectl, with no new Rust dependency or Python/Node runtime. Node/
Python are development fixture/test helpers only.

Details: [local plan and progress](OWS-LOCAL-INSTALLATION-PLAN.md),
[guide](OWS-LOCAL-INSTALLATION.md), [sanitized evidence](evidence/local-installer-20261028.json).

Remaining before an AWS experiment: cloud apply/status/destroy reconciliation and
interruption tests; cloud platform installation for CRD/controller/CNI/storage;
private cluster management API connectivity; image/guard upload and exact secret
handoff; real Lambda/runtime/IAM/cleanup qualification and fresh preflight. Local
success does not complete those cloud milestones. No AWS resources or identities
were created/changed/uploaded in this task. Live cloud apply/destroy stay disabled.

### D4/D5 local execution and installation qualification — 28 October 2026

The [cloud lifecycle ledger](OWS-CLOUD-EXECUTION-PLAN.md) records the implemented
experimental coordinator, ECR publisher, shared Kubernetes platform/service
installer, private Secret handoff and teardown recovery. [Operator guide](OWS-AWS-LIFECYCLE.md).
Fake AWS and real kind/OpenShell/PostgreSQL checks pass; ordinary cloud entry points
remain closed and the lifecycle path requires explicit experimental opt-in.
No AWS calls, identity changes, image uploads or provisioning occurred in this task.
D6 remains the separately authorized AWS trial, including actual IAM, network,
volume, Pod Identity, guard cleanup and billing qualification.


### Azure A1 — identity export and bootstrap preflight (31 October)

The [Azure sequence/ledger](OWS-AZURE-IMPLEMENTATION.md) and
[command guide](OWS-AZURE-BOOTSTRAP.md) define the provider-specific follow-up.
Azure A1 exports an exact existing-principal, customer-RG-scoped ARM-metadata role
and assignment, and verifies those grant records read-only. It does not create
an identity, provide a login/federation flow, or confer resource-creation authority.
AKS, workload identities, Bicep resources, execution/cleanup and live qualification
remain later milestones. No real Azure CLI/account/IAM/resource changes were made.


### Azure A2–A4 — offline graph, lifecycle and installation

Azure now has staged ARM graph/permission exports, durable ETag-CAS lifecycle
control, independent Container Apps expiry packaging, exact resource ownership
receipts, and a binding into the shared Helm/OpenShell installer. A native optional
Azure Blob artifact backend and role-specific workload identity projections are
included. Existing kind/EKS/GCP regressions pass; the detailed qualification matrix
and remaining gates are in the [Azure ledger](OWS-AZURE-IMPLEMENTATION.md).

The [Azure service reference](OWS-AZURE-SERVICE.md) describes customer-reviewed
bootstrap grants, experimental operations, private AKS access, secret handoff and
control-resource retention. No real Azure CLI login/account/IAM/resource operation
has been performed. Live readiness is deliberately not inferred from offline
exports or fixtures; an authorized, budgeted Azure trial remains A5.
