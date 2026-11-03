# GCP

The GCP reference targets GKE Standard, Cloud SQL PostgreSQL, Cloud Storage and
Artifact Registry, with NVIDIA OpenShell providing tool sandboxes. The application
uses the same OpenWorker service API and session model as other deployments.

## Setup flow

1. Select a project, region, billing/budget boundary and deployment lifetime.
2. Review the separate setup, executor, workload and cleanup identities.
3. Run bootstrap/preflight checks and review the generated infrastructure plan.
4. Establish independent cleanup before creating the paid workload.
5. Qualify the cluster, private installer route, OpenShell, agent execution and cleanup.

Use scoped service-account impersonation and workload identity instead of copying
administrator credentials into pods. Validate the chosen node OS/kernel and
networking with OpenShell; do not assume that a different GKE mode is interchangeable.

**Qualification:** bootstrap, infrastructure/lifecycle logic and installation
wiring have local qualification. A complete live GKE/OpenShell workload and
independent cleanup trial is still outstanding. Treat this deployment path as
experimental until that evidence is recorded.

See [GCP bootstrap](../OWS-GCP-BOOTSTRAP.md),
[infrastructure](../OWS-GCP-INFRASTRUCTURE.md),
[installation](../OWS-GCP-INSTALLATION.md),
[lifecycle](../OWS-GCP-LIFECYCLE.md) and [trial status](../OWS-GCP-TRIAL-PLAN.md).
