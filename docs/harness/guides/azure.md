# Azure

The Azure reference targets AKS, PostgreSQL and Blob artifact storage, with separate
managed identities for installation, cluster/workload operations and independent
cleanup. OpenShell supplies the sandbox execution layer.

## Setup flow

1. Select the tenant, subscription, region and dedicated workload/control resource groups.
2. Review the exact-scope Entra/managed-identity grants and existing secret references.
3. Export and validate the ARM infrastructure and independent cleanup configuration.
4. Verify private AKS/database access, storage, images and OpenShell prerequisites.
5. Qualify installation, a real agent workload, recovery and cleanup in the subscription.

The workload and cleanup control have separate lifecycles. The installer does not
assume subscription Owner rights, and the normal executor is not granted permission
to change role assignments. Keep customer-owned/shared resources outside the trial's
cleanup scope.

This deployment path is experimental. Validate live Azure permissions, network,
OpenShell compatibility and independent cleanup in your subscription before use.

Start with the [service CLI](../../../harness/service-cli/README.md) and
[deployment options](deployment.md).
