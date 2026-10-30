# GCP deployment implementation and trial ledger

Date: October 30, 2026. Branch: `feat/ows-gcp-deployment`, based on the consolidated
`openworker-harness-sdk` at `6e1bc173`.
Verified work through `e4f96969` is now consolidated into the public
`openworker-harness-sdk` integration branch for partner builds.

The user authorized **US$100 total** for the GCP experiment and an isolated project
and budget setup. This allowance is cumulative for the experiment, not replenished
by another attempt or by a calendar-month boundary. Keep account-specific receipts
in the ignored private state; do not publish credentials, billing-account IDs or
raw provider logs. Existing projects and unrelated workloads are outside this trial.

## Current boundary

The native `ows deploy gcp plan` command exists and is qualified for offline
planning. Identity-only bootstrap export and read-only **bootstrap** preflight
are now implemented and qualified below. G2a adds a locally schema-validated
infrastructure export and private lifecycle prepare/local-status commands. Remote reconciliation and owned-deployment deletion now have a locally tested
transport/engine and independent runner package. Full infrastructure preflight,
cloud installation and real workload creation remain **gated and unqualified**. Existing AWS qualification does not
qualify those GCP paths. The shared Kubernetes/Helm installer and harness service
can be reused after GCP infrastructure and provider access are verified.

The proposed topology remains GKE Standard, Cloud SQL PostgreSQL, Cloud Storage,
Artifact Registry and NVIDIA OpenShell. Resolve exact versions, node OS/kernel
compatibility, networking and costs before provisioning. Do not silently substitute
GKE Autopilot, expose the cluster management API, disable OpenShell isolation or
fall back to static workload keys to get the trial running.

## Milestones and acceptance order

### G0 — isolated project and budget (complete)

- Verify the connected caller, project inventory and billing authority.
- Create one labeled trial project; attach the selected open billing account.
- Create a project-only **$100 cumulative custom-period** budget starting October
  30, with current-spend thresholds at **50%, 75%, 90% and 100%**. Keep default
  billing-administrator/user notification recipients enabled.
- Count gross costs before credits conservatively. The budget is an alert, not a
  hard cap, and has no monthly reset. Do not change other projects' budgets.
- Read back the amount, currency, project filter, period and thresholds. Save
  provider receipts and ownership labels privately.

### G1 — bootstrap artifacts and read-only preflight

1. Add provider-specific native CLI commands, preserving the existing offline
   boundary and fresh explicit project/location targeting.
2. Generate a reviewable, versioned identity/resource graph for setup, Terraform
   execution, workloads and independent cleanup. Keep bootstrap IAM changes
   explicit and avoid Owner/Editor grants and downloadable service-account keys.
3. Implement bounded read-only checks for identity, billing, required APIs,
   permissions, organization policies, quotas, compatible GKE/OpenShell versions,
   images and the operator's cluster-management access path.
4. Resolve a conservative cost estimate for the bounded topology and TTL, including
   prior attempts and an explicit reserve. Fail before mutation if the remaining
   allowance or teardown prerequisites cannot support it.
5. Test using trap/fake cloud CLIs, including wrong projects, missing permissions,
   disabled APIs, malformed responses and budget overflow. Read-only preflight
   must not enable APIs, grant permissions or create resources.

### G2 — infrastructure and durable lifecycle

- Render pinned Terraform/provider inputs for Google Infrastructure Manager;
  validate the actual resource graph and required permissions locally.
- Add apply/status/destroy reconciliation using persisted provider operation,
  deployment and revision identities. An unknown outcome is reconciled before
  another mutation; a lost response does not authorize a duplicate deployment.
- Keep the cluster management API protected and provide an explicit installer
  access path. Keep Terraform state until teardown is confirmed.
- Qualify interruption/restart and partial-resource cleanup with simulated provider
  operations before the first paid deployment.

### G3 — independent expiry and cleanup gate

- Run cleanup with its own narrowly scoped identity/lifecycle outside the GKE
  cluster it may need to delete. Pin the intended project, deployment and expiry.
- Proposed first attempt: **at most four hours**, with manual early shutdown on
  completion. A new attempt does not reset the cumulative $100 allowance.
- Verify the guard can reconcile Terraform deletion and dependent resources after
  the CLI/Mac or workload cluster is unavailable. Preserve state/receipts while
  cleanup is incomplete; report unknown costs honestly.
- Budget notifications supplement this guard; delayed billing is not an immediate
  stop mechanism. No paid cluster/database launch until cleanup and the fresh
  estimate are ready.

### G4 — shared installation and real agent acceptance

- Publish digest-pinned compatible images, install pinned OpenShell and the shared
  OpenWorker chart, then verify readiness, PostgreSQL TLS and workload artifact
  identity. Model credentials belong only on authorized harness workers.
- Submit a real agent via the SDK/`ows`; verify file/process execution inside
  OpenShell, durable approvals and results through the management API/UI.
- Verify PostgreSQL recording, charts and authenticated OTel/Langfuse export using
  the same run. Export only the configured content level.
- Exercise worker replacement, pending approval/cancellation, deployment upgrade,
  persistent artifacts and expiry cleanup. Check negative identity/access cases.
- Verify resource absence and delayed billing before closing the trial. Record
  remaining provider-specific limits and publish a reproducible getting-started
  guide.

## G0 execution — October 30

Connected as the newly selected user. Inventory found one open billing account,
an existing billing-enabled experiments project and a stale CLI default project
that the new login could not use. All trial commands used explicit project flags;
the global CLI default and existing projects were left unchanged.

Created the new labeled project, linked the selected billing account, enabled its
Cloud Billing Budget API, created the cumulative custom-period $100 budget and
read it back. All four thresholds, project-only scope, USD amount, credit treatment
and enabled default recipients match the intended configuration. No GKE cluster,
VM, Cloud SQL instance, bucket, registry, workload service account, IAM grant or
scheduled cleanup job was created in this phase.

Built the latest `ows` locally with locked offline Cargo dependencies. Generated,
inspected and validated a private GCP plan for `us-central1` with a four-hour
planning TTL. It correctly reports **`applyReady: false`**, unresolved cost and
identity bootstrap, and offline-only validation. This is not evidence of a working
GCP deployment or an installed expiry guard. A stale/expired plan must be replaced
with a fresh reviewed plan before any future apply.

Private state on the qualification host:
`.ow-service-gcp-20261030/{preparation.json,budget-receipt.json,plan/}`. Public
documentation intentionally excludes billing-account and provider receipt IDs.
This checkpoint preceded G1. Its implementation/qualification follows below;
lifecycle/cleanup gates still precede paid infrastructure.

## G1 bootstrap implementation — qualified

Added native identity-only bootstrap export and bounded read-only bootstrap
preflight. Plans preserve the existing immutable manifest contract; generated GCP
identities bind an explicit project number and exact operator. Five separate
accounts receive only the reviewed platform/impersonation bindings; infrastructure
executor, artifact, node and cleanup grants remain unresolved until the resource
graph is implemented. Export never executes the customer-run mutation script.
The script explicitly initializes the Google-managed service identity and creates
five customer-managed accounts; it does not generate keys or grant workload data
access.

Preflight pins project/account/quota-project flags, rejects configured endpoint/implicit impersonation overrides, clears inherited
token/credential-file variables, disables prompts, bounds subprocess deadlines/output and
persists scrubbed results privately. Its read operation allowlist excludes API
enablement, tokens, secrets and IAM mutations. IAM bindings are direct observations,
not proof of effective inherited/conditional permissions. Deployment readiness
remains false while costs, networking, organization policies and cleanup are not
qualified. Local fake-CLI and exported-script tests passed before live use.
The live setup below used separately authorized mutations; preflight itself never
performed an IAM/API mutation.

### Live qualification and recovery

After the user's explicit approval of the exact recipients, roles and project
scope, enabled the seven reviewed bootstrap APIs as a separate setup action and
created the five dedicated accounts. API activation can enable provider-managed
dependencies and service agents; it did not launch a cluster, VM, SQL instance,
bucket, registry, Cloud Build job or scheduled cleanup job.

The first script run stopped when IAM's project-policy backend had not yet indexed
its newly created executor account. The script did not retry mutations or recreate
accounts. Compared **all five live unique IDs** with the private creation receipts,
read the current policies, preserved the two confirmed bindings and applied only
confirmed-missing approved bindings. A second stop exposed the lazily initialized
Google Infrastructure Manager service agent. Initialized that exact approved
identity using the official gcloud beta component, then confirmed the last approved
executor act-as binding. No Google-internal IAM metadata privilege was requested.

Final result: **five customer-managed accounts and six reviewed bindings**. Setup
has project Infra Manager administration and Service Usage consumer; executor has
`config.agent`. The operator can impersonate only setup; setup and the exact Google
service agent can act as only executor. Artifact/cleanup/node accounts have no
project grants. No Owner/Editor, project-wide impersonation or static keys were
introduced by this bootstrap.

The final script now initializes the service identity before customer account
creation. Its final full execution was exercised with a fake cloud; the live grant
set was completed through the explicit reconciliation above, not a second clean
live script run. Final live read-only preflight used the final exported bundle and
creation journal: **bootstrapReady=true, applyReady=false, 15 gcloud invocations,
zero cloud mutations, five receipt-matched UIDs**. Project/account/billing/API and
reviewed direct role/trust checks pass. Cloud SQL API remains an optional reported
infrastructure blocker, not something preflight enables.

The current plan keeps **$25 as an explicit variable-cost reserve** and reports
actual spend/cost estimate unknown. The $100 budget remains cumulative; no paid
infrastructure was launched. API enablement and IAM roles are intentionally retained
for G2, and plan expiry does not revoke them automatically. Private journals/report
paths and exact provider IDs remain under `.ow-service-gcp-20261030/`.

### Validation and remaining gates

- **70 CLI tests pass**, including nine GCP integration groups and the existing
  AWS/deployment/installer suites. Fake scenarios cover scope/caller mismatch,
  disabled/missing APIs/identities, trust/role drift, malformed/truncated/oversized
  responses, deadlines, credential-marker scrubbing, nested output rejection,
  valid expired/future bundles, journal UID replacement and partial-write recovery.
- Export remains offline; scripts run in tests only against fake gcloud. Existing
  plan shapes and integrity/overwrite behavior retain their regression coverage.
- Rust formatting, shell syntax, documentation links and whitespace checks pass.
  Default Clippy completes with **no diagnostics in the new GCP files**. Strict
  `-D warnings` remains blocked by pre-existing telemetry/CLI/test lints (including
  too-many-arguments and redundant-pattern/open-options warnings); those unrelated
  modules were not changed or globally suppressed.
- This milestone qualifies bootstrap/direct observations, not effective inherited
  policy, deny constraints, full resource permissions, a GKE/OpenShell deployment,
  prices, capacity reservation or an independent expiry guard. Those remain G2/G3
  requirements before paid infrastructure.

[Command guide](OWS-GCP-BOOTSTRAP.md) ·
[Sanitized evidence](evidence/gcp-bootstrap-preflight-20261030.json).

## G2 execution plan — October 30

Proceed in three reviewable slices. This work is in the separate `ows` CLI crate;
it does not add code/dependencies to either measured agent binary. The
[release-size baseline](BINARY-FOOTPRINT.md) is recorded before this work.

1. **G2a: immutable infrastructure export and recovery rules.** Export a pinned
   Terraform JSON graph for a private zonal GKE Standard cluster (two fixed AMD64
   Ubuntu nodes), private Cloud SQL, dedicated VPC/NAT, artifacts and image
   registry. Validate explicit versions, bounded disks and non-overlapping private
   address ranges. Describe separate control-state/source storage and independent
   cleanup authority; never delete Terraform state with the workload graph.
   Add/test the lifecycle decision rules for persisted create/delete intents,
   pinned provider operation/creation receipts, revision and ambiguous outcomes. Qualify the generated
   graph with the real pinned provider schema locally, without Terraform apply.
2. **G2b: provider transport and full preflight.** Wire the recovery rules to
   Infra Manager's create-only REST method (not gcloud's create-or-update wrapper),
   durable journals, private generation-pinned source, bounded authenticated
   observations and a resolved permission/cost/access check. No API failure or
   interrupted create automatically becomes permission to retry a mutation.
3. **G3: independent cleanup deployment and qualification.** Install a separately
   owned authenticated scheduler/cleanup runner outside GKE, with exact acknowledged operation,
   creation time, revision and expiry receipts kept in durable control storage. Confirm its health
   before workload apply. Exercise teardown failure/restart and prove absence;
   retain source/state/receipts until destruction is verified. Only then review
   the fresh cost estimate and remaining cumulative allowance for a paid trial.

G2a exports remain non-applicable. A Terraform schema pass is not GCP IAM,
organization-policy, OpenShell compatibility, price or lifecycle qualification.
No new cloud grants or paid resources are required for these local tests.

### G2a implementation and qualification

Implemented the native infrastructure export, immutable provider checksum lock,
twelve-address workload graph and separate cleanup contract. Private GKE/SQL
networking, both Calico enablement controls, fixed node counts, dedicated node
identity (including the transient default pool), Workload Identity, public bucket
access prevention and trial deletion behavior are explicit. VPC, pod, service,
control-plane and SQL ranges must be canonical, private and non-overlapping.
Disk/machine/TTL bounds and exact Terraform/provider pins reject unreviewed inputs.

Implemented private fsynced lifecycle preparation and locked **local-only** status,
binding the immutable graph to a fixed request UUID. Recovery decisions never
choose a replay for a persisted intent/unknown outcome. Names and labels are not
ownership receipts. Google's Deployment API does not expose a UID; acknowledged
operation, creation time and revision evidence must be retained by the future
transport. A missing acknowledgement needs explicit ownership reconciliation,
even if a subsequent read says the name is absent.

Downloaded the official Terraform **1.5.7** ARM64 archive and verified its published
SHA-256. `init -backend=false` installed Google **6.44.0**, signature-verified by
Terraform; the resulting provider checksum lock is included in exports. Real
`terraform validate -json` passed with **zero errors and zero warnings**. The
provider's local IPC socket needed execution outside the desktop filesystem
sandbox; this was local validation, not a Google API or Terraform apply operation.
No Terraform plan/apply, new GCP grant or paid resource was created.

The complete CLI suite passed **75 tests**, including thirteen GCP integration
groups, the recovery-decision unit test and the existing AWS/install regression
suites. Tests cover immutable graph/lock checks, unsafe address/version/capacity
inputs, private journal permissions/reopening, graph/scope drift and missing
ownership receipts. Default Clippy completed without diagnostics in the GCP
files. Formatting, local documentation links and whitespace checks pass.

The checked-in example GKE version is syntactic test input only. Regional version
availability, node/kernel/OpenShell compatibility, actual NetworkPolicy isolation,
effective permissions, private installer access, costs and independent cleanup
remain unqualified. The control source/state bucket and cleanup scheduler/runner
are deliberately outside the workload graph. **At this G2a checkpoint, G2b and G3 remained outstanding**;
`applyReady` was false and cloud mutation commands were not exposed. The local
lifecycle/cleanup implementation and its remaining cloud gates are recorded below.

[Infrastructure/recovery guide](OWS-GCP-INFRASTRUCTURE.md) ·
[Sanitized G2a evidence](evidence/gcp-infrastructure-local-20261030.json).

## G2b/G3 execution — local protocol and packaging qualified

Working branch: `feat/ows-gcp-lifecycle`, from consolidated `f1fd671f`.
Merge verified checkpoints back to `openworker-harness-sdk`.

Implementation order:

1. Add a bounded Google REST transport and typed durable control/checkpoint
   contract. Use exact API hosts, no redirects/retries, ephemeral credentials and
   generation-preconditioned GCS writes. Pin the immutable source ZIP generation,
   digest, deployment scope and executor; keep control objects outside GKE.
2. Implement create/reconcile/delete decision execution with intent persisted
   before a request, acknowledged operation/creation/revision receipts, and
   restart-safe compare-and-swap checkpoints. Unknown responses remain unknown;
   a 404, name or label alone never authorizes a retry/adoption/deletion.
3. Package an independent Cloud Run cleanup job and authenticated Cloud Scheduler
   trigger, including exact image/control digest, expiry, bounded tasks/retries,
   and separate authority requirements. The cleanup runner uses metadata identity
   and the same recovery engine, with no Mac/GKE dependency.
4. Qualify complete lifecycle, concurrent runners, lost replies, provider failures,
   changed ownership/source/revisions, delayed deletion and post-expiry operation
   locally against isolated HTTP/storage fixtures. Validate exported control
   Terraform with the pinned provider. No paid GCP deployment or new grant in
   these tests. The real workload-create gate stays closed while full cost,
   effective IAM/private access and live guard qualification are outstanding.

The $100 cumulative trial allowance is unchanged. Control installation itself
also requires a reviewed cost/permission check before any paid trial.

### Verified result

Implemented fixed-host, bounded Google REST calls with redirects/retries disabled,
short-lived credentials and no token persistence. The shared CLI/Cloud Run engine
persists create/delete/pause intents before effects and uses generation-CAS for
checkpoint writes. Operation discovery after a lost reply requires one matching
original operation plus target/verb/time, source, executor and creation/revision
receipts. Missing/ambiguous outcomes never trigger an automatic mutation replay.

Before deletion, capture discoverable GKE instance, boot/PVC disk, group and template
IDs. Provider deletion alone is insufficient: observe named roots and captured
assets, reject replacement IDs, then persist and confirm the scheduler pause.
Keep source/state/receipts outside the workload. Pause recovery also observes its
original outcome instead of blindly repeating the request. Retained control,
registry, IAM and delayed billing remain a separate decommission responsibility.

The independent package contains a private versioned source/control bucket and
separate image registry, a digest-pinned Rust cleanup job, exact job invoker and
OAuth Scheduler trigger. Source uploads are generation-pinned and checkpoint
updates are fenced. The permission-request artifact explicitly remains
`policyReady=false`; it is not a sufficient IAM policy or an applied grant.

- **95 CLI tests passed**, including nineteen GCP engine/transport unit tests and
  fifteen GCP CLI integration groups, plus existing AWS/installer regressions.
  Nominal fake-gcloud startup allowance increased for loaded CI; explicit per-call
  timeout scenarios remain bounded. The final full suite used two test threads.
- Both independent Terraform graphs passed **Terraform 1.5.7 / Google 6.44.0**
  schema validation with zero errors/warnings. No Terraform plan/apply ran.
- The actual **Linux AMD64** runner image built locally and passed help/invalid-input
  smoke checks with no network, read-only root, all capabilities dropped, no new
  privileges, 32 PIDs, one CPU and 256 MiB RAM. Image size: **142.95 MiB**. No image
  was pushed to a registry. This qualifies packaging, not metadata/IAM behavior.
- Default Clippy reports no diagnostics in GCP files. Formatting, whitespace,
  private-state exclusion and local documentation links pass.
- **No new GCP API mutation, IAM grant or paid infrastructure** in this work. The
  authorized budget remains $100 cumulative; actual billing is not measured here.

### Remaining cloud gates

Workload creation still refuses in the production authorizer. Full effective IAM,
prices/prior spend, quotas/versions, private access and OpenShell compatibility
remain unresolved. Source/control upload and cloud installation reconciliation
need wiring. Live Cloud Run metadata/authentication, expiry/restart and real GKE
label/PVC dependent discovery require a separately qualified cloud drill.
`managedDependentAbsenceQualified` and `allCleanupComplete` remain false so local
fixtures cannot claim an orphan-free account or completed billing cleanup.
The G2b protocol and G3 package are qualified locally; **full G2b preflight and live
G3 qualification are not complete**.

[Lifecycle guide](OWS-GCP-LIFECYCLE.md) ·
[Sanitized evidence](evidence/gcp-lifecycle-cleanup-local-20261030.json).

## References

- [Cloud deployment specification](OWS-DEPLOYMENT-SPEC.md)
- [Cloud identity design](OWS-CLOUD-IDENTITY-DESIGN.md#gcp-plan-implemented-identityinstaller-design)
- [Google Infrastructure Manager](https://docs.cloud.google.com/infrastructure-manager/docs/overview)
- [Google Cloud budget behavior](https://docs.cloud.google.com/billing/docs/how-to/budgets)
- [Budget periods, credits and threshold rules](https://docs.cloud.google.com/billing/docs/reference/budget/rest/v1/billingAccounts.budgets)
