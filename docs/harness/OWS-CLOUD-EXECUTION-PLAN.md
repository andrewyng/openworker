# Cloud lifecycle recovery and installation wiring

28 October 2026. Branch `feat/openworker-service-m1`. Authorization: implement and
qualify locally; no AWS provisioning, identity changes, uploads or deployment.

## Implementation order

1. Durable cloud lifecycle engine: a private locked state directory outside the
   immutable bundle, intent-before-effect, stable CloudFormation request tokens,
   original stack ARN receipts, event-token/template/ownership checks, bounded
   observations and explicit no-effect recovery. No adoption by name. Status is
   observational. Expiry blocks new provisioning but permits observation/cleanup.
2. AWS command adapter: explicit profile and exact caller/setup identity; single
   attempt per mutation, bounded subprocesses, no shell or raw provider errors.
   Verify the pinned guard's deployed code/configuration, schedule and synchronous
   read-only health response before creating the infrastructure stack. Do not
   confuse HTTP success with successful Lambda execution.
3. Installation handoff: bind EKS identity/private endpoint/CA and original stack
   outputs; digest-pinned images, upstream platform artifacts, gateway installation
   or reuse, credentials via private files/Secrets, and service/UI Helm installation.
   Reuse existing installer receipts/recovery rules rather than `upgrade --install`.
   Never store provider/database/model credentials in the public plan or journal.
4. Teardown: require session/environment cleanup and an installation teardown receipt
   before removing infrastructure; preserve the independent guard/bootstrap until
   owned resources and volumes are observed absent. Uncertain external effects need
   inspection, not automatic replay. Do not turn missing permissions into absence.
5. Qualify fake-provider success/failure/restart/replacement/expiry/credential cases,
   installation handoff locally on kind, preserve baseline resources, update guides
   and evidence and commit. Keep the ordinary cloud entry point closed until all
   locally testable gates pass; a future live trial needs explicit opt-in and fresh
   preflight. Local tests do not certify AWS IAM, billing, VPC, EBS or Lambda cleanup.

## Recovery rules

CloudFormation create/delete tokens appear in stack events. A matching name or tag
alone is insufficient evidence of the original request. A lost create response can
be reconciled only against the original intent, template, token and immutable ARN.
An absent stack after a possibly dispatched request never automatically authorizes
another create. Failed/rollback/delete-failed stacks require operator attention.
Installer intent must likewise precede namespace, Secret, Job and Helm mutations.

References: [CreateStack](https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_CreateStack.html),
[DescribeStackEvents](https://docs.aws.amazon.com/AWSCloudFormation/latest/APIReference/API_DescribeStackEvents.html),
[Lambda invoke](https://docs.aws.amazon.com/cli/latest/reference/lambda/invoke.html).

## Progress

- Reviewed the current local installer, guard, preflight, generated IAM/templates
  and older AWS reference. The older account-scoped scripts will not be invoked.
- Implementation starting. The existing cluster/caches will be reused. No AWS
  calls or mutations are needed for this work.
- Recovery engine/experimental AWS adapter compile. `ows deploy aws lifecycle`
  prepares separate locked state, applies one bounded transition, observes original
  stack receipts, and reconciles deletion. Explicit opt-in, exact setup-role profile,
  full fresh preflight and bundled guard digest/health/config/schedule/concurrency
  gates precede new infrastructure. Ordinary offline cloud commands remain closed.
- Nine fake-cloud integration tests pass: lost create/delete replies, no blind replay,
  explicit no-effect confirmation, template/identity/event drift, failed stacks,
  Lambda FunctionError, disabled/changed guard, read-only status, preflight/locks,
  nonempty artifact bucket, private endpoint/CA/repository handoff and required
  installation teardown receipt. Tests have no cloud credentials/network.
- Shared `deploy cluster` installer and native private Secret staging implemented;
  managed-database quiescence uses a new trusted-host aggregate command. Its real
  PostgreSQL regression passes, including denial to session/admin API credentials.
  Local Docker test container: `ows-cloud-pg-local-20261028`, loopback port 58467,
  tmpfs data. Remove when qualification finishes.
- Linux service rebuilt offline with cached Rust target in 1m37s, packaged as
  `openworker-service:cloud-local`, local manifest/index digest
  `sha256:516b6d17623ffc2583e98fe1961206f4303d634f920231066da1570fbab7591b`.
  Loaded into existing kind. Real shared-installer qualification underway at
  `.ow-service-cloud-qualification/managed`; namespace `ow-service-m4-reuse2b361ccf`,
  service identity `ows-2b361ccfd787`, deployment
  `ows-local-d625895ce35a47c4ae1da1d54853feef`, accepted digest
  `80c04005c57ee6d5b399f86d0c1fcf0d062fe2deff812d60e89e5714c625a2a4`.
- Intermediate checkpoint (superseded by completion below): pin/create/reconcile platform artifact + strict CNI
  transition tests, ECR publisher tests (new module), shared Secrets interruption
  tests, real managed-mode install/sandbox/cleanup; operator guides, scoped IAM/
  CloudFormation checks, evidence and commit. Guard artifact pre-upload is a
  separately reviewed prerequisite; this work must not upload it or call real AWS.

## Completion — local qualification

Implemented the experimental `ows deploy aws lifecycle` coordinator and shared
`ows deploy cluster` installer. [Operator workflow](OWS-AWS-LIFECYCLE.md).

- Locked private lifecycle state, durable request tokens and original ARN/event/
  template ownership checks; status is observational. New creates require fresh
  preflight, explicit opt-in and verified independent guard health. Uncertain
  create/delete/push/CNI operations are reconciled rather than replayed.
- Linux ARM64 image publication uses immutable owned ECR repositories, exact local
  content identities and private transient Docker auth. No actual publication ran.
- Handoff verifies original EKS identity, private endpoint/CA, repository and S3
  scope. Offline upstream platform export pins Agent Sandbox 1.0.6, encrypted
  ownership-tagged EBS storage, and explicit trusted-component policies. Policies
  precede the durable strict-CNI transition; untrusted sandbox policies stay separate.
- Private create-only Secret staging, generated bootstrap/readiness tokens, managed
  DB identity/TLS handoff, artifact service-account binding and enabled-UI readiness.
  The setup role no longer inherits the legacy stack-update grant. It cannot read
  model credentials; image writes are confined to three repositories.
- Teardown gates on trusted PostgreSQL aggregates and freezes intake. Installation
  intent is recorded under the cloud lock before any possible Kubernetes mutation;
  only then is a verified installation teardown receipt required. A read-only
  handoff or unstarted plan cannot strand empty infrastructure. A platform/gateway-
  only interruption can be cleaned without a worker/database that never existed.
- Helm now persists request provenance in release labels as well as its description:
  a timeout replaces the latter. Explicit readiness reconciliation checks the
  original hook-free service release, bootstrap success and rollouts, without a
  second installation. Hook-bearing failures remain operator recovery.

The first local attempt failed before any service/bootstrap container started:
kind had the image content but lacked the requested named digest alias in its CRI
cache. Verified no session schema/effects, removed that exact owned failed release/
namespace, corrected the alias, and ran a fresh installation. The old failed journal
was retained; it was not reset or adopted.

The fresh shared-installer test (`fixtureDatabase: false`) passed real local
OpenShell write/read, scoped profile/agent publication, restart/release reconciliation,
active-work teardown refusal, session close and explicit environment deletion.
Its PostgreSQL was a disposable in-cluster fixture exercising the managed-installer
code path, not AWS RDS. The service image was built offline from cached Linux sources.
The final native CLI also compiles on Linux. No live model key was needed.

Test installation `ow-service-m4-reuseca898e2f` was removed through the installer.
The temporary loopback/tmpfs PostgreSQL test container was removed. All nine baseline
namespace UIDs, three sandbox UIDs and the existing OpenShell release UID/revision
are unchanged. Current-task copied gateway TLS/private fixture credentials were
removed after evidence capture. Cached binaries/images were reused; no new cluster.

Final checks passed: 51 Rust CLI tests, 9 guard tests, 5 chart tests, one real
PostgreSQL authorization/aggregate test, offline Linux ARM64 compilation, all three
CloudFormation templates linted (only intentional W3010 suppressed), formatting
and diff checks. Artifact pins and details are recorded in
[evidence/cloud-lifecycle-20261028.json](evidence/cloud-lifecycle-20261028.json).
Tests cover stale preflight during resumption, failed/replaced stacks, uncertain
effects, guard failures, ownership drift, private Secret interruption, CNI update
interruption, partial installation cleanup and least-privilege artifact identity.
The cloud fakes run without credentials or network access. Generated templates are
linted offline; guard and chart tests remain passing.

## Local milestone handoff: explicitly authorized AWS trial

No real AWS API calls, IAM changes, provisioned resources or uploads occurred in
this task. Keep the $100 experiment limit and review a fresh estimate/expiry.
Resolve a versioned guard-code upload and operator-to-management-API route, then
qualify actual bootstrap/IAM handlers and rollback, ECR, strict CNI allow/deny
(including sandbox bootstrap traffic), EBS tagging/reclaim, RDS TLS, Pod Identity/S3
scope, Lambda scheduling/expiry cleanup and final resource/billing absence.
The CLI deliberately reports `allCloudResourcesAbsent: false` after stack deletion:
guard/bootstrap removal and final orphan/cost checks remain separate operator steps.
The guard and budget are not a hard spending cap. GCP/Azure execution remains future work.

## AWS qualification follow-up — 29 October 2026 UTC

The separately authorized trial now passes fresh private EKS/RDS/OpenShell
installation without manual Secret, Job or Helm repairs; live agent file/shell
effects, browser traces, exact event replay, and actual scheduled expiry including
seeded S3 object/upload and orphan EBS removal. Native expiry reconciliation succeeds
without issuing another delete, rejects before expiry or while cleanup is pending,
and supports observation after the cluster API is gone.

See [clean rerun evidence](evidence/aws-clean-expiry-20261029.json),
[AWS lifecycle](OWS-AWS-LIFECYCLE.md) and [trial ledger](OWS-AWS-TRIAL-PLAN.md).
The original local-only statement above is historical. Qualification remains bounded
to Oregon/Linux ARM64. The subsequent [artifact qualification](evidence/aws-artifact-storage-20261029.json)
verifies S3 workload transfer through Pod Identity, scoped denials, interrupted
upload recovery and retrieval after API/sandbox replacement. It also fixes managed
/v1 artifact routes and the TLS proxy upload limit; the proxy fix was tested through
a reviewed Helm upgrade, not a fresh final-chart AWS installation. Final delayed
billing attribution and GCP/Azure execution remain open.
