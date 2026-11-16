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

This deployment path is experimental. A production deployment requires validation
of live GCP permissions, OpenShell compatibility and independent cleanup in your
project.

Start with the [service CLI](../../../harness/service-cli/README.md) and
[deployment options](deployment.md).
