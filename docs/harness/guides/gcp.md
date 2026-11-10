# GCP

The GCP reference targets GKE Standard, Cloud SQL PostgreSQL, Cloud Storage and
Artifact Registry, with NVIDIA OpenShell providing tool sandboxes. The application
uses the same OpenWorker service API and session model as other deployments.

## Validation status

Experimental: bootstrap and read-only preflight have been exercised in a GCP
project, and lifecycle, installation and cleanup logic have local test coverage.
A complete live GKE deployment with OpenShell, an agent run, recovery and
independent cleanup has **not yet been qualified**. The setup flow below describes
what remains to validate, not a completed cloud deployment.

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
