# GCP deployment implementation and trial ledger

Date: October 30, 2026. Branch: `feat/ows-gcp-deployment`, based on the consolidated
`openworker-harness-sdk` at `6e1bc173`.

The user authorized **US$100 total** for the GCP experiment and an isolated project
and budget setup. This allowance is cumulative for the experiment, not replenished
by another attempt or by a calendar-month boundary. Keep account-specific receipts
in the ignored private state; do not publish credentials, billing-account IDs or
raw provider logs. Existing projects and unrelated workloads are outside this trial.

## Current boundary

The native `ows deploy gcp plan` command exists and is qualified for offline
planning. GCP identity/bootstrap export, cloud preflight and infrastructure
apply/status/destroy are **not implemented**. Existing AWS qualification does not
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
The next implementation milestone is **G1**, then lifecycle/cleanup gates before
paid infrastructure.

## References

- [Cloud deployment specification](OWS-DEPLOYMENT-SPEC.md)
- [Cloud identity design](OWS-CLOUD-IDENTITY-DESIGN.md#gcp-plan-implemented-identityinstaller-design)
- [Google Infrastructure Manager](https://docs.cloud.google.com/infrastructure-manager/docs/overview)
- [Google Cloud budget behavior](https://docs.cloud.google.com/billing/docs/how-to/budgets)
- [Budget periods, credits and threshold rules](https://docs.cloud.google.com/billing/docs/reference/budget/rest/v1/billingAccounts.budgets)
